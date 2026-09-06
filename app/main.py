import asyncio
import csv
import io
import json
import logging
import hashlib
from urllib.parse import urlsplit
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from . import database as db
from . import activity
from . import bidder_master as bidder_db
from .config import BASE_DIR
from .crawler import CrawlEngine, canonicalize_url
from .security import validate_public_url
from .runtime import single_instance
from .models import OshaStatus, ResearchField, ScanOptions, SourceCreate, SourceUpdate
from .bidder_schema import BIDDER_COLUMNS, bidder_row, parse_bidder_csv

TASKS: dict[int, asyncio.Task] = {}
SCHEDULER_TASK: asyncio.Task | None = None
SCAN_LOCK = asyncio.Lock()


async def launch_scan(source_id: int, force_full: bool = False) -> dict:
    async with SCAN_LOCK:
        return await _launch_scan(source_id, force_full)


async def _launch_scan(source_id: int, force_full: bool = False) -> dict:
    source = await db.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    running = await db.running_job_for_source(source_id)
    if running:
        return running
    job_id = await db.create_job(source_id, force_full)
    activity.emit("INFO", "Scan queued", source_id=source_id, job_id=job_id, mode="full" if force_full else "incremental")
    engine = CrawlEngine(source, job_id, force_full=force_full)
    task = asyncio.create_task(engine.run(), name=f"crawl-job-{job_id}")
    TASKS[job_id] = task

    def _done(_task: asyncio.Task):
        TASKS.pop(job_id, None)
        with suppress(asyncio.CancelledError, Exception):
            _task.result()

    task.add_done_callback(_done)
    return await db.get_job(job_id)


async def scheduler_loop():
    while True:
        try:
            sources = await db.list_sources()
            now = datetime.now(timezone.utc)
            for source in sources:
                if not source.get("auto_scan"):
                    continue
                running = await db.running_job_for_source(source["id"])
                if running:
                    continue
                last_scan = source.get("last_scan_at")
                due = last_scan is None
                if last_scan:
                    try:
                        last_dt = datetime.fromisoformat(last_scan)
                        if last_dt.tzinfo is None:
                            last_dt = last_dt.replace(tzinfo=timezone.utc)
                        due = now >= last_dt + timedelta(minutes=int(source["interval_minutes"]))
                    except ValueError:
                        due = True
                if due:
                    await launch_scan(source["id"], force_full=False)
        except Exception:
            logging.getLogger("app.scheduler").exception("Scheduler iteration failed")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global SCHEDULER_TASK
    with single_instance():
        await db.init_db()
        activity.install()
        activity.emit("INFO", "Paralegal Database Tool started. Ready to maintain the master bidder database and collect public-record evidence.", version=activity.APP_VERSION)
        await db.mark_interrupted_jobs()
        SCHEDULER_TASK = asyncio.create_task(scheduler_loop(), name="source-scheduler")
        try:
            yield
        finally:
            SCHEDULER_TASK.cancel()
            with suppress(asyncio.CancelledError):
                await SCHEDULER_TASK
            tasks = list(TASKS.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            activity.emit("INFO", "Workspace stopped. Active scans have been closed safely.")
            await db.list_events(limit=1)



app = FastAPI(title="Paralegal Database Tool", version=activity.APP_VERSION, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])


@app.middleware("http")
async def local_security(request, call_next):
    origin = request.headers.get("origin")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin != str(request.base_url).rstrip("/"):
        return JSONResponse({"detail": "Cross-origin mutation forbidden"}, status_code=403)
    try:
        response = await call_next(request)
    except Exception:
        logging.getLogger("app.api").exception("Request failed: %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Something went wrong. Open Activity console and export diagnostics."}, status_code=500)
    if response.status_code >= 400:
        activity.emit("WARNING" if response.status_code < 500 else "ERROR", "Request could not be completed",
                      method=request.method, path=request.url.path, status=response.status_code)
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


STATIC_DIR = BASE_DIR / "app" / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "version": activity.APP_VERSION, "application": "paralegal-database-tool",
            "workspace": hashlib.sha256(str(db.DB_PATH.resolve()).encode()).hexdigest()[:16]}


@app.get("/api/activity")
async def activity_events(after: int = Query(0, ge=0), limit: int = Query(300, ge=1, le=1000)):
    return await db.list_events(after, limit)


@app.post("/api/activity/snapshot")
async def activity_snapshot():
    counts = await db.stats()
    activity.emit("INFO", "Diagnostic snapshot captured by operator", **counts)
    return {"ok": True, "counts": counts}


@app.get("/api/activity/export")
async def activity_export():
    activity.emit("INFO", "Diagnostic report exported")
    return StreamingResponse(io.BytesIO(await activity.bundle()), media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="paralegal-database-tool-diagnostics.zip"'})


@app.get("/api/stats")
async def get_stats():
    return await db.stats()


@app.get("/api/sources")
async def get_sources():
    return await db.list_sources()


@app.post("/api/sources", status_code=201)
async def post_source(payload: SourceCreate):
    data = payload.model_dump(mode="json")
    data["start_url"] = canonicalize_url(str(payload.start_url))
    try:
        await validate_public_url(data["start_url"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        source = await db.create_source(data)
        activity.emit("INFO", "Source added", source_id=source["id"], url=source["start_url"])
        return source
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise HTTPException(status_code=409, detail="That start URL already exists") from exc
        raise


@app.patch("/api/sources/{source_id}")
async def patch_source(source_id: int, payload: SourceUpdate):
    result = await db.update_source(source_id, payload.model_dump(exclude_unset=True))
    if not result:
        raise HTTPException(status_code=404, detail="Source not found")
    return result


@app.delete("/api/sources/{source_id}", status_code=204)
async def delete_source(source_id: int):
    async with SCAN_LOCK:
        return await _delete_source(source_id)


async def _delete_source(source_id: int):
    running = await db.running_job_for_source(source_id)
    if running:
        raise HTTPException(status_code=409, detail="Stop/wait for the active crawl before deleting this source")
    if not await db.delete_source(source_id):
        raise HTTPException(status_code=404, detail="Source not found")
    activity.emit("WARNING", "Source and its collected records deleted", source_id=source_id)


@app.post("/api/sources/{source_id}/scan")
async def scan_source(source_id: int, options: ScanOptions | None = None):
    options = options or ScanOptions()
    return await launch_scan(source_id, force_full=options.force_full)


@app.get("/api/jobs")
async def jobs(limit: int = Query(default=30, ge=1, le=200)):
    return await db.list_jobs(limit)


@app.get("/api/sources/{source_id}/errors")
async def source_errors(source_id: int):
    return await db.page_errors(source_id)


@app.get("/api/jobs/{job_id}")
async def job(job_id: int):
    result = await db.get_job(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


@app.get("/api/records")
async def records(
    q: str = "",
    source_id: int | None = None,
    field: ResearchField = "all",
    osha_status: OshaStatus | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    return await db.search_records(q=q.strip(), source_id=source_id, limit=limit, offset=offset,
                                   field=field, osha_status=osha_status)


@app.get("/api/bidder-schema")
async def bidder_schema():
    return {"columns": BIDDER_COLUMNS}


@app.get("/api/bidder/status")
async def bidder_status():
    return await bidder_db.status()


@app.post("/api/bidder/import")
async def import_bidder_csv(request: Request):
    filename = (request.headers.get("x-filename") or "bidder-database.csv").strip()[:255]
    data = await request.body()
    try:
        rows, warnings = parse_bidder_csv(data)
        result = await bidder_db.import_rows(filename, rows, warnings)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    activity.emit(
        "INFO", "Bidder database CSV imported",
        filename=filename, rows=result["rows_total"], inserted=result["rows_inserted"],
        updated=result["rows_updated"], unchanged=result["rows_unchanged"],
    )
    return result


@app.get("/api/bidder/master")
async def bidder_master_records(
    q: str = "",
    field: str = "all",
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    try:
        return await bidder_db.search(q=q.strip(), field=field, limit=limit, offset=offset)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/bidder/master/{master_id}/history")
async def bidder_master_history(master_id: int, limit: int = Query(default=200, ge=1, le=1000)):
    return await bidder_db.history(master_id, limit)


@app.post("/api/bidder/compare")
async def compare_bidder_database():
    result = await bidder_db.compare()
    activity.emit("INFO", "Collected records compared with bidder database", **result)
    return result


@app.get("/api/bidder/proposals")
async def bidder_proposals(status: str = "pending", limit: int = Query(default=500, ge=1, le=2000)):
    try:
        return await bidder_db.list_proposals(status_filter=status, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/bidder/proposals/{proposal_id}/apply")
async def apply_bidder_update(proposal_id: int):
    try:
        result = await bidder_db.apply(proposal_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    activity.emit("INFO", "Bidder database update approved", proposal_id=proposal_id, **result)
    return result


@app.post("/api/bidder/proposals/{proposal_id}/dismiss")
async def dismiss_bidder_update(proposal_id: int):
    try:
        result = await bidder_db.dismiss(proposal_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    activity.emit("INFO", "Bidder database update dismissed", proposal_id=proposal_id)
    return result


@app.get("/api/bidder/export")
async def export_bidder_master(format: str = "csv", q: str = "", field: str = "all"):
    fmt = format.lower()
    if fmt not in {"csv", "xlsx", "json"}:
        raise HTTPException(status_code=400, detail="format must be csv, xlsx, or json")
    try:
        rows = await bidder_db.all_rows(q=q.strip(), field=field)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await asyncio.to_thread(build_bidder_master_export, fmt, rows)


@app.get("/api/bidder-records")
async def bidder_records(
    q: str = "",
    source_id: int | None = None,
    field: ResearchField = "all",
    osha_status: OshaStatus | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    result = await db.search_records(q=q.strip(), source_id=source_id, limit=limit, offset=offset,
                                     field=field, osha_status=osha_status)
    return {"total": result["total"], "items": [bidder_row(dict(row)) for row in result["items"]]}


@app.get("/api/records/{record_id}")
async def record_details(record_id: int):
    record = await db.get_record(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Research record not found")
    record["bidder"] = bidder_row(dict(record))
    return record


@app.get("/api/records/{record_id}/history")
async def history(record_id: int):
    return await db.record_history(record_id)


@app.get("/api/export")
async def export_records(format: str = "csv", q: str = "", source_id: int | None = None,
                         field: ResearchField = "all", osha_status: OshaStatus | None = None):
    fmt = format.lower()
    if fmt not in {"csv", "xlsx", "json"}:
        raise HTTPException(status_code=400, detail="format must be csv, xlsx, or json")
    try:
        records = await db.all_records_for_export(q=q.strip(), source_id=source_id, field=field, osha_status=osha_status)
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return await asyncio.to_thread(build_export, fmt, records)


def build_bidder_master_export(fmt, rows):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    clean_rows = [{column: row.get(column, "") for column in BIDDER_COLUMNS} for row in rows]

    if fmt == "json":
        body = json.dumps(clean_rows, ensure_ascii=False, indent=2)
        return StreamingResponse(
            io.BytesIO(body.encode("utf-8")), media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="bidder-database-{stamp}.json"'},
        )

    if fmt == "csv":
        text = io.StringIO()
        writer = csv.DictWriter(text, fieldnames=BIDDER_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(
            {column: spreadsheet_text(row.get(column, "")) for column in BIDDER_COLUMNS}
            for row in clean_rows
        )
        return StreamingResponse(
            io.BytesIO(text.getvalue().encode("utf-8-sig")), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="bidder-database-{stamp}.csv"'},
        )

    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Bidder Database")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(BIDDER_COLUMNS))}{len(clean_rows) + 1}"
    for index in range(1, len(BIDDER_COLUMNS) + 1):
        sheet.column_dimensions[get_column_letter(index)].width = 24
    sheet.append(BIDDER_COLUMNS)
    for row in clean_rows:
        sheet.append([spreadsheet_text(row.get(column, "")) for column in BIDDER_COLUMNS])
    binary = io.BytesIO()
    workbook.save(binary)
    binary.seek(0)
    return StreamingResponse(
        binary,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="bidder-database-{stamp}.xlsx"'},
    )


def build_export(fmt, records):
    columns = BIDDER_COLUMNS
    projected = [bidder_row(dict(row)) for row in records]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    if fmt == "json":
        body = json.dumps([{k: row.get(k, "") for k in columns} for row in projected], ensure_ascii=False, indent=2)
        return StreamingResponse(
            io.BytesIO(body.encode("utf-8")), media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="bidder-database-{stamp}.json"'},
        )

    if fmt == "csv":
        text = io.StringIO()
        writer = csv.DictWriter(text, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({k: spreadsheet_text(row.get(k, "")) for k in columns} for row in projected)
        return StreamingResponse(
            io.BytesIO(text.getvalue().encode("utf-8-sig")), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="bidder-database-{stamp}.csv"'},
        )

    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Bidder Database")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{len(projected) + 1}"
    for index in range(1, len(columns) + 1):
        sheet.column_dimensions[get_column_letter(index)].width = 24
    sheet.append(columns)
    for row in projected:
        sheet.append([spreadsheet_text(row.get(k, "")) for k in columns])
    binary = io.BytesIO()
    workbook.save(binary)
    binary.seek(0)
    return StreamingResponse(
        binary,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="bidder-database-{stamp}.xlsx"'},
    )


def spreadsheet_text(value):
    if not isinstance(value, str):
        return value
    value = ILLEGAL_CHARACTERS_RE.sub("", value)
    if value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
