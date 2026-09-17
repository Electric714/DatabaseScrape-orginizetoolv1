import asyncio
import csv
import io
import json
import logging
import httpx
import hashlib
from urllib.parse import urlsplit
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from . import database as db
from . import activity
from . import bidder_master as bidder_db
from . import backup as backup_tools
from .config import (
    BASE_DIR,
    dol_api_key_configured,
    save_dol_api_key,
    sam_api_key_configured,
    save_sam_api_key,
)
from .crawler import CrawlEngine, canonicalize_url
from .security import PublicTransport, validate_public_url
from .runtime import single_instance
from .models import OshaStatus, ResearchField, ScanOptions, SourceCreate, SourceUpdate
from .bidder_schema import BIDDER_COLUMNS, bidder_row, parse_bidder_csv
from .osha_adapter import DOL_INSPECTION_ENDPOINT
from .sam_adapter import SAM_EXCLUSIONS_ENDPOINT, _payload as parse_sam_payload
from .bbb_sitemap_adapter import BBB_SITEMAP_INDEX
from .state_adapter import MN_URL
from .state_sources import IL_URL, WI_URL
from .violation_tracker_adapter import VT_URL
from .source_catalog import SOURCE_CATALOG, field_map
from .adapters import adapter_for_url
from .config import get_dol_api_key, get_sam_api_key

TASKS: dict[int, asyncio.Task] = {}
SCHEDULER_TASK: asyncio.Task | None = None
SCAN_LOCK = asyncio.Lock()


class DolApiKeyPayload(BaseModel):
    api_key: str = Field(min_length=10, max_length=512)


class SamApiKeyPayload(BaseModel):
    api_key: str = Field(min_length=10, max_length=512)


OSHA_SOURCE_NAME = "OSHA / DOL Enforcement API"
OSHA_SOURCE_HOSTS = {"www.osha.gov", "apiprod.dol.gov", "api.dol.gov"}
SAM_SOURCE_NAME = "SAM.gov Federal Debarment / Exclusions"
SAM_SOURCE_HOSTS = {"sam.gov", "www.sam.gov", "api.sam.gov", "api-alpha.sam.gov"}
BBB_SOURCE_NAME = "BBB Business Profiles / Complaints"
BBB_SOURCE_HOSTS = {"bbb.org", "www.bbb.org"}


def _is_osha_source(source: dict) -> bool:
    try:
        return (urlsplit(str(source.get("start_url") or "")).hostname or "").lower() in OSHA_SOURCE_HOSTS
    except ValueError:
        return False


async def ensure_builtin_osha_source() -> dict:
    sources = await db.list_sources()
    existing = next((source for source in sources if _is_osha_source(source)), None)
    if existing:
        # Old OSHA HTML-source rows are retained so their evidence/history is not lost.
        # CrawlEngine internally migrates them to the canonical DOL API endpoint.
        desired = {
            "name": OSHA_SOURCE_NAME,
            "render_mode": "http",
            "respect_robots": False,
            "max_depth": 4,
            "concurrency": 4,
            "delay_ms": 150,
        }
        changed = {
            key: value for key, value in desired.items()
            if existing.get(key) != value and not (
                isinstance(value, bool) and bool(existing.get(key)) == value
            )
        }
        if changed:
            return await db.update_source(existing["id"], changed) or existing
        return existing
    return await db.create_source({
        "name": OSHA_SOURCE_NAME,
        "start_url": DOL_INSPECTION_ENDPOINT,
        "auto_scan": False,
        "interval_minutes": 60,
        "max_pages": 5000,
        "max_depth": 4,
        "concurrency": 4,
        "delay_ms": 150,
        "render_mode": "http",
        "respect_robots": False,
    })


def _is_sam_source(source: dict) -> bool:
    try:
        return (urlsplit(str(source.get("start_url") or "")).hostname or "").lower() in SAM_SOURCE_HOSTS
    except ValueError:
        return False


async def ensure_builtin_sam_source() -> dict:
    sources = await db.list_sources()
    existing = next((source for source in sources if _is_sam_source(source)), None)
    desired = {
        "name": SAM_SOURCE_NAME,
        "start_url": SAM_EXCLUSIONS_ENDPOINT,
        "auto_scan": False,
        "interval_minutes": 1440,
        "max_pages": 5000,
        "max_depth": 4,
        "concurrency": 1,
        "delay_ms": 500,
        "render_mode": "http",
        "respect_robots": False,
    }
    if existing:
        changed = {
            key: value for key, value in desired.items()
            if existing.get(key) != value and not (
                isinstance(value, bool) and bool(existing.get(key)) == value
            )
        }
        if changed:
            return await db.update_source(existing["id"], changed) or existing
        return existing
    return await db.create_source(desired)


def _is_bbb_source(source: dict) -> bool:
    try:
        return (urlsplit(str(source.get("start_url") or "")).hostname or "").lower() in BBB_SOURCE_HOSTS
    except ValueError:
        return False


async def ensure_builtin_bbb_source() -> dict:
    sources = await db.list_sources()
    existing = next((source for source in sources if _is_bbb_source(source)), None)
    desired = {
        "name": BBB_SOURCE_NAME,
        "start_url": BBB_SITEMAP_INDEX,
        "auto_scan": False,
        "interval_minutes": 1440,
        "max_pages": 5000,
        "max_depth": 5,
        "concurrency": 1,
        "delay_ms": 500,
        "render_mode": "http",
        "respect_robots": True,
    }
    if existing:
        changed = {
            key: value for key, value in desired.items()
            if existing.get(key) != value and not (
                isinstance(value, bool) and bool(existing.get(key)) == value
            )
        }
        if changed:
            return await db.update_source(existing["id"], changed) or existing
        return existing
    return await db.create_source(desired)


SAM_KEY_VALIDATION_FALLBACK = "https://api.sam.gov/contract-awards/v1/search"


def _normalized_api_key(value: str) -> str:
    key = (value or "").strip()
    if len(key) < 10:
        raise ValueError("API key appears incomplete")
    return key


async def validate_dol_api_key(api_key: str) -> dict:
    """Validate DOL authentication without depending on an OSHA data query."""
    key = _normalized_api_key(api_key)
    # DOL's v4 guide documents /json/metadata as the authentication-aware
    # metadata route. PublicTransport moves X-API-KEY into the documented query
    # parameter only at final network egress so the key never enters app URLs.
    test_url = DOL_INSPECTION_ENDPOINT + "/metadata"
    try:
        async with httpx.AsyncClient(
            transport=PublicTransport(),
            trust_env=False,
            follow_redirects=False,
            timeout=20.0,
        ) as client:
            response = await client.get(
                test_url,
                headers={"X-API-KEY": key, "Accept": "application/json"},
            )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ValueError("Could not reach the DOL Open Data API to test this key") from exc
    if response.status_code in {401, 403}:
        raise ValueError("DOL rejected this API key")
    if response.status_code == 429:
        raise ValueError("DOL API rate limit reached; try the key again after the limit resets")
    if response.status_code == 404 or response.status_code >= 500:
        raise ValueError(
            f"DOL authentication service is temporarily unavailable (HTTP {response.status_code}); "
            "the key was not marked invalid"
        )
    if response.status_code < 200 or response.status_code >= 300:
        raise ValueError(f"DOL API key test could not be completed (HTTP {response.status_code})")
    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise ValueError("DOL accepted the request but returned an unreadable metadata response") from exc
    if not isinstance(payload, (dict, list)):
        raise ValueError("DOL accepted the request but returned an unexpected metadata response")
    return {"validation_service": "dol-metadata", "upstream_available": True}


async def validate_sam_api_key(api_key: str) -> dict:
    """Validate a SAM public API key while distinguishing service failure from bad auth."""
    key = _normalized_api_key(api_key)
    try:
        async with httpx.AsyncClient(
            transport=PublicTransport(),
            trust_env=False,
            follow_redirects=False,
            timeout=20.0,
        ) as client:
            response = await client.get(
                SAM_EXCLUSIONS_ENDPOINT,
                params={
                    "api_key": key,
                    "classification": "Firm",
                    "recordStatus": "Active",
                    "page": 0,
                    "size": 1,
                },
                headers={"Accept": "application/json"},
            )
            if 200 <= response.status_code < 300:
                if response.status_code != 204:
                    parse_sam_payload(response.text)
                return {
                    "validation_service": "sam-exclusions",
                    "exclusions_available": True,
                }
            if response.status_code in {401, 403}:
                raise ValueError("SAM.gov rejected this API key")
            if response.status_code == 429:
                raise ValueError("SAM.gov rate limit reached; try the key again after the limit resets")

            # OpenGSA still documents this exact Exclusions v4 URL, but clean live
            # requests currently can receive HTTP 404. If that happens, verify only
            # the credential against another official SAM public API using the same
            # Public API Key. Do not claim Exclusions itself is healthy.
            if response.status_code == 404 or response.status_code >= 500:
                fallback = await client.get(
                    SAM_KEY_VALIDATION_FALLBACK,
                    params={
                        "api_key": key,
                        "awardeeUniqueEntityId": "000000000000",
                        "limit": 1,
                        "offset": 0,
                    },
                    headers={"Accept": "application/json"},
                )
                if fallback.status_code in {401, 403}:
                    raise ValueError("SAM.gov rejected this API key")
                if fallback.status_code == 429:
                    raise ValueError("SAM.gov rate limit reached; try the key again after the limit resets")
                if fallback.status_code in {200, 204}:
                    return {
                        "validation_service": "sam-contract-awards-fallback",
                        "exclusions_available": False,
                        "warning": (
                            "SAM.gov accepted the API key, but its documented Exclusions endpoint "
                            f"returned HTTP {response.status_code}. The key was saved; Exclusions collection "
                            "may remain unavailable until SAM.gov restores that endpoint."
                        ),
                    }
                raise ValueError(
                    "SAM.gov public APIs are currently unavailable, so this key could not be validated; "
                    "the key was not marked invalid"
                )

            raise ValueError(f"SAM.gov API key test could not be completed (HTTP {response.status_code})")
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ValueError("Could not reach SAM.gov to test this key") from exc


async def launch_scan(source_id: int, force_full: bool = False, master_ids=None) -> dict:
    async with SCAN_LOCK:
        return await _launch_scan(source_id, force_full, master_ids)


def _job_selection(job: dict):
    try:
        return json.loads(job.get("selection_json") or "null")
    except (TypeError, ValueError):
        return None


def _start_job_task(source: dict, job: dict) -> None:
    job_id = int(job["id"])
    engine = CrawlEngine(
        source, job_id, force_full=bool(job.get("force_full")), master_ids=_job_selection(job)
    )
    task = asyncio.create_task(engine.run(), name=f"crawl-job-{job_id}")
    TASKS[job_id] = task

    def _done(_task: asyncio.Task):
        TASKS.pop(job_id, None)
        with suppress(asyncio.CancelledError, Exception):
            _task.result()

    task.add_done_callback(_done)


async def _launch_scan(source_id: int, force_full: bool = False, master_ids=None) -> dict:
    source = await db.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if not getattr(adapter_for_url(source["start_url"]), "query_mode", False):
        raise HTTPException(status_code=422, detail="This POC only runs registered targeted source adapters")
    if master_ids is not None:
        known = {r["_master_id"] for r in await bidder_db.all_rows()}
        if not master_ids or not set(master_ids) <= known:
            raise HTTPException(status_code=422, detail="Select existing master contractors")
    running = await db.running_job_for_source(source_id)
    if running:
        return running
    job_id = await db.create_job(source_id, force_full, selection=master_ids)
    activity.emit("INFO", "Scan queued", source_id=source_id, job_id=job_id, mode="full" if force_full else "incremental")
    job = await db.get_job(job_id)
    _start_job_task(source, job)
    return job


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
        await ensure_builtin_osha_source()
        await ensure_builtin_sam_source()
        await ensure_builtin_bbb_source()
        await ensure_poc_sources()
        activity.install()
        activity.emit("INFO", "Paralegal Database Tool started. Ready to maintain the master bidder database and collect public-record evidence.", version=activity.APP_VERSION)
        interrupted = await db.mark_interrupted_jobs()
        for interrupted_job_id in interrupted:
            recovery = await db.create_recovery_job(interrupted_job_id)
            if not recovery:
                continue
            source = await db.get_source(recovery["source_id"])
            if not source:
                continue
            activity.emit(
                "WARNING", "Interrupted scan queued for a clean recovery attempt",
                source_id=source["id"], job_id=recovery["id"], parent_job_id=interrupted_job_id,
                attempt_no=recovery.get("attempt_no"),
            )
            _start_job_task(source, recovery)
        SCHEDULER_TASK = asyncio.create_task(scheduler_loop(), name="source-scheduler")
        try:
            yield
        finally:
            SCHEDULER_TASK.cancel()
            with suppress(asyncio.CancelledError):
                await SCHEDULER_TASK
            tasks = list(TASKS.values())
            for task in tasks:
                task.cancel("application_shutdown")
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


@app.get("/api/maintenance/integrity")
async def maintenance_integrity(full: bool = False):
    return await db.integrity_check(full=full)


@app.get("/api/maintenance/backup")
async def maintenance_backup():
    archive, manifest = await asyncio.to_thread(backup_tools.create_backup_bytes)
    filename = f"paralegal-research-desk-backup-{manifest['created_at'].replace(':', '').replace('+00:00', 'Z')}.zip"
    activity.emit("INFO", "Operator-safe database backup created", schema_version=manifest["schema_version"])
    return StreamingResponse(
        io.BytesIO(archive), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/integrations/dol")
async def dol_integration_status():
    source = await ensure_builtin_osha_source()
    return {
        "configured": dol_api_key_configured(),
        "source_id": source["id"],
        "source_name": OSHA_SOURCE_NAME,
        "endpoint": DOL_INSPECTION_ENDPOINT,
        "registration_url": "https://dataportal.dol.gov/registration",
    }


@app.post("/api/integrations/dol")
async def configure_dol_integration(payload: DolApiKeyPayload):
    key = _normalized_api_key(payload.api_key)
    try:
        validation = (await validate_dol_api_key(key)) or {}
        save_dol_api_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source = await ensure_builtin_osha_source()
    activity.emit("INFO", "DOL Open Data API key tested and saved", source_id=source["id"])
    return {"configured": True, "validated": True, "source_id": source["id"], **validation}


@app.get("/api/integrations/sam")
async def sam_integration_status():
    source = await ensure_builtin_sam_source()
    return {
        "configured": sam_api_key_configured(),
        "source_id": source["id"],
        "source_name": SAM_SOURCE_NAME,
        "endpoint": SAM_EXCLUSIONS_ENDPOINT,
        "environment": "production",
        "documentation_url": "https://open.gsa.gov/api/exclusions-api/",
        "api_key_url": "https://sam.gov/profile/details",
    }


@app.post("/api/integrations/sam")
async def configure_sam_integration(payload: SamApiKeyPayload):
    key = _normalized_api_key(payload.api_key)
    try:
        validation = (await validate_sam_api_key(key)) or {}
        save_sam_api_key(key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source = await ensure_builtin_sam_source()
    activity.emit("INFO", "SAM.gov production Exclusions API key tested and saved", source_id=source["id"])
    return {"configured": True, "validated": True, "source_id": source["id"], "environment": "production"}


@app.get("/api/sources")
async def get_sources():
    osha = await ensure_builtin_osha_source()
    sam = await ensure_builtin_sam_source()
    bbb = await ensure_builtin_bbb_source()
    await ensure_poc_sources()
    sources = await db.list_sources()
    order = {osha["id"]: 0, sam["id"]: 1, bbb["id"]: 2}
    return sorted(sources, key=lambda source: (order.get(source["id"], 3), source["id"]))


@app.post("/api/sources", status_code=201)
async def post_source(payload: SourceCreate):
    data = payload.model_dump(mode="json")
    data["start_url"] = canonicalize_url(str(payload.start_url))
    hostname = (urlsplit(data["start_url"]).hostname or "").lower()
    if hostname in OSHA_SOURCE_HOSTS:
        raise HTTPException(status_code=409, detail="OSHA is built in. Use the Set API key button on the OSHA card.")
    if hostname in SAM_SOURCE_HOSTS:
        raise HTTPException(status_code=409, detail="SAM.gov Federal Debarment is built in. Use the SAM API key button.")
    if hostname in BBB_SOURCE_HOSTS:
        raise HTTPException(status_code=409, detail="BBB is built in. Use the BBB source card.")
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
    source = await db.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if _is_osha_source(source):
        raise HTTPException(status_code=409, detail="OSHA is a built-in source and cannot be edited here")
    if _is_sam_source(source):
        raise HTTPException(status_code=409, detail="SAM.gov Federal Debarment is a built-in source and cannot be edited here")
    if _is_bbb_source(source):
        raise HTTPException(status_code=409, detail="BBB is a built-in source and cannot be edited here")
    result = await db.update_source(source_id, payload.model_dump(exclude_unset=True))
    if not result:
        raise HTTPException(status_code=404, detail="Source not found")
    return result


@app.delete("/api/sources/{source_id}", status_code=204)
async def delete_source(source_id: int):
    async with SCAN_LOCK:
        return await _delete_source(source_id)


async def _delete_source(source_id: int):
    source = await db.get_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if _is_osha_source(source):
        raise HTTPException(status_code=409, detail="OSHA is built in and cannot be removed")
    if _is_sam_source(source):
        raise HTTPException(status_code=409, detail="SAM.gov Federal Debarment is built in and cannot be removed")
    if _is_bbb_source(source):
        raise HTTPException(status_code=409, detail="BBB is built in and cannot be removed")
    running = await db.running_job_for_source(source_id)
    if running:
        raise HTTPException(status_code=409, detail="Stop/wait for the active crawl before deleting this source")
    if not await db.delete_source(source_id):
        raise HTTPException(status_code=404, detail="Source not found")
    activity.emit("WARNING", "Source and its collected records deleted", source_id=source_id)


@app.post("/api/sources/{source_id}/scan")
async def scan_source(source_id: int, options: ScanOptions | None = None):
    options = options or ScanOptions()
    return await launch_scan(source_id, force_full=options.force_full, master_ids=options.master_ids)


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


@app.get("/api/jobs/{job_id}/diagnostics")
async def job_diagnostics(job_id: int, limit: int = Query(default=1000, ge=1, le=5000)):
    result = await db.get_job(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job": result,
        "diagnostics": await db.job_diagnostics(job_id, limit),
        "frontier": await db.frontier_summary(job_id),
    }


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


async def ensure_poc_sources():
    sources = await db.list_sources()
    for name, url in [
        ("Violation Tracker", VT_URL),
        ("Minnesota OSP debarment", MN_URL),
        ("Illinois public works debarment", IL_URL),
        ("Wisconsin DOT debarment", WI_URL),
    ]:
        if not any(s["start_url"] == url for s in sources):
            await db.create_source(SourceCreate(name=name, start_url=url, concurrency=1, delay_ms=1500, max_depth=8, max_pages=50, render_mode="http", respect_robots=True).model_dump(mode="json"))


@app.get("/api/source-catalog")
async def source_catalog():
    return {"sources": SOURCE_CATALOG, "field_map": field_map()}


@app.post("/api/integrations/{integration}/test")
async def test_saved_key(integration: str):
    if integration not in {"dol", "sam"}:
        raise HTTPException(status_code=404, detail="Unknown integration")
    key = get_dol_api_key() if integration == "dol" else get_sam_api_key()
    if not key:
        raise HTTPException(status_code=422, detail="API key missing")
    try:
        await (validate_dol_api_key(key) if integration == "dol" else validate_sam_api_key(key))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"validated": True, "integration": integration}
