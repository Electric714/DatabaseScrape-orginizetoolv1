import asyncio
import csv
import io
import json
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook

from . import database as db
from .config import BASE_DIR
from .crawler import CrawlEngine
from .models import ScanOptions, SourceCreate, SourceUpdate

TASKS: dict[int, asyncio.Task] = {}
SCHEDULER_TASK: asyncio.Task | None = None


async def launch_scan(source_id: int, force_full: bool = False) -> dict:
    source = await db.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    running = await db.running_job_for_source(source_id)
    if running:
        return running
    job_id = await db.create_job(source_id, force_full)
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
            pass
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global SCHEDULER_TASK
    await db.init_db()
    await db.mark_interrupted_jobs()
    SCHEDULER_TASK = asyncio.create_task(scheduler_loop(), name="source-scheduler")
    yield
    if SCHEDULER_TASK:
        SCHEDULER_TASK.cancel()
        with suppress(asyncio.CancelledError):
            await SCHEDULER_TASK
    for task in list(TASKS.values()):
        task.cancel()
    if TASKS:
        await asyncio.gather(*TASKS.values(), return_exceptions=True)


app = FastAPI(title="Database Scrape & Organize Tool", version="0.1.0", lifespan=lifespan)
STATIC_DIR = BASE_DIR / "app" / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health():
    return {"ok": True, "version": "0.1.0"}


@app.get("/api/stats")
async def get_stats():
    return await db.stats()


@app.get("/api/sources")
async def get_sources():
    return await db.list_sources()


@app.post("/api/sources", status_code=201)
async def post_source(payload: SourceCreate):
    data = payload.model_dump(mode="json")
    data["start_url"] = str(payload.start_url)
    try:
        return await db.create_source(data)
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
    running = await db.running_job_for_source(source_id)
    if running:
        raise HTTPException(status_code=409, detail="Stop/wait for the active crawl before deleting this source")
    if not await db.delete_source(source_id):
        raise HTTPException(status_code=404, detail="Source not found")


@app.post("/api/sources/{source_id}/scan")
async def scan_source(source_id: int, options: ScanOptions | None = None):
    options = options or ScanOptions()
    return await launch_scan(source_id, force_full=options.force_full)


@app.get("/api/jobs")
async def jobs(limit: int = Query(default=30, ge=1, le=200)):
    return await db.list_jobs(limit)


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
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    return await db.search_records(q=q.strip(), source_id=source_id, limit=limit, offset=offset)


@app.get("/api/records/{record_id}/history")
async def history(record_id: int):
    return await db.record_history(record_id)


@app.get("/api/export")
async def export_records(format: str = "csv", q: str = "", source_id: int | None = None):
    fmt = format.lower()
    if fmt not in {"csv", "xlsx", "json"}:
        raise HTTPException(status_code=400, detail="format must be csv, xlsx, or json")
    records = await db.all_records_for_export(q=q.strip(), source_id=source_id)
    columns = [
        "id", "source_name", "name", "company", "phone", "address", "date", "external_id",
        "source_url", "first_seen", "last_seen", "last_changed", "active",
    ]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    if fmt == "json":
        body = json.dumps([{k: row.get(k) for k in columns} for row in records], ensure_ascii=False, indent=2)
        return StreamingResponse(
            io.BytesIO(body.encode("utf-8")), media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="records-{stamp}.json"'},
        )

    if fmt == "csv":
        text = io.StringIO()
        writer = csv.DictWriter(text, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
        return StreamingResponse(
            io.BytesIO(text.getvalue().encode("utf-8-sig")), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="records-{stamp}.csv"'},
        )

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Records"
    sheet.append(columns)
    for row in records:
        sheet.append([row.get(k) for k in columns])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column_cells in sheet.columns:
        width = min(max(len(str(cell.value or "")) for cell in column_cells) + 2, 60)
        sheet.column_dimensions[column_cells[0].column_letter].width = width
    binary = io.BytesIO()
    workbook.save(binary)
    binary.seek(0)
    return StreamingResponse(
        binary,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="records-{stamp}.xlsx"'},
    )
