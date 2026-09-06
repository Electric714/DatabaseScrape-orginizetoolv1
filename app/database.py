import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from .config import DB_PATH
from .normalizer import canonical_record, entity_key, record_hash


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _sync_init_db() -> None:
    with closing(connect()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                start_url TEXT NOT NULL UNIQUE,
                auto_scan INTEGER NOT NULL DEFAULT 0,
                interval_minutes INTEGER NOT NULL DEFAULT 60,
                max_pages INTEGER NOT NULL DEFAULT 5000,
                max_depth INTEGER NOT NULL DEFAULT 12,
                concurrency INTEGER NOT NULL DEFAULT 6,
                delay_ms INTEGER NOT NULL DEFAULT 350,
                render_mode TEXT NOT NULL DEFAULT 'auto',
                respect_robots INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_scan_at TEXT
            );

            CREATE TABLE IF NOT EXISTS crawl_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                pages_discovered INTEGER NOT NULL DEFAULT 0,
                pages_processed INTEGER NOT NULL DEFAULT 0,
                records_found INTEGER NOT NULL DEFAULT 0,
                records_new INTEGER NOT NULL DEFAULT 0,
                records_updated INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                force_full INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                url TEXT NOT NULL,
                status_code INTEGER,
                etag TEXT,
                last_modified TEXT,
                content_hash TEXT,
                discovered_links TEXT,
                last_crawled_at TEXT,
                last_error TEXT,
                UNIQUE(source_id, url)
            );

            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                entity_key TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                name TEXT,
                company TEXT,
                phone TEXT,
                address TEXT,
                date TEXT,
                external_id TEXT,
                source_url TEXT NOT NULL,
                extra_json TEXT NOT NULL DEFAULT '{}',
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                last_changed TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(source_id, entity_key)
            );

            CREATE TABLE IF NOT EXISTS record_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
                changed_at TEXT NOT NULL,
                old_json TEXT,
                new_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_records_source ON records(source_id);
            CREATE INDEX IF NOT EXISTS idx_records_name ON records(name);
            CREATE INDEX IF NOT EXISTS idx_records_company ON records(company);
            CREATE INDEX IF NOT EXISTS idx_records_phone ON records(phone);
            CREATE INDEX IF NOT EXISTS idx_records_address ON records(address);
            CREATE INDEX IF NOT EXISTS idx_jobs_source ON crawl_jobs(source_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_records_changed ON records(last_changed DESC, id DESC);
            CREATE TABLE IF NOT EXISTS activity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                level TEXT NOT NULL,
                component TEXT NOT NULL,
                message TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                job_id INTEGER,
                source_id INTEGER
            );
            """
        )
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(pages)")}
        if "rendered" not in columns:
            conn.execute("ALTER TABLE pages ADD COLUMN rendered INTEGER NOT NULL DEFAULT 0")
        if "fetch_mode" not in columns:
            conn.execute("ALTER TABLE pages ADD COLUMN fetch_mode TEXT")
        record_columns = {r["name"] for r in conn.execute("PRAGMA table_info(records)")}
        if "inactive_since" not in record_columns:
            conn.execute("ALTER TABLE records ADD COLUMN inactive_since TEXT")
        for field in ("owner", "location", "osha_details"):
            if field not in record_columns:
                conn.execute(f"ALTER TABLE records ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
        if "osha_status" not in record_columns:
            conn.execute("ALTER TABLE records ADD COLUMN osha_status TEXT NOT NULL DEFAULT 'unknown'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_osha ON records(osha_status)")
        conn.execute("""CREATE TABLE IF NOT EXISTS page_records (
            source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            url TEXT NOT NULL,
            record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
            last_job INTEGER NOT NULL,
            payload_hash TEXT,
            PRIMARY KEY(source_id,url,record_id)
        )""")
        if "payload_hash" not in {r["name"] for r in conn.execute("PRAGMA table_info(page_records)")}:
            conn.execute("ALTER TABLE page_records ADD COLUMN payload_hash TEXT")
            conn.execute("UPDATE pages SET content_hash=NULL,etag=NULL,last_modified=NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_page_records_scan ON page_records(source_id,last_job,record_id)")
        # Re-key once, preserving record IDs and history. Previously merged people
        # cannot be reconstructed from an old snapshot; a full rescan is necessary.
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            for row in conn.execute("SELECT * FROM records").fetchall():
                payload = dict(row)
                payload["extra"] = json.loads(payload["extra_json"] or "{}")
                conn.execute("UPDATE records SET entity_key=?, content_hash=? WHERE id=?",
                             (entity_key(payload), record_hash(payload), row["id"]))
            conn.execute("UPDATE pages SET content_hash=NULL, etag=NULL, last_modified=NULL")
            conn.execute("PRAGMA user_version=2")
        if version < 3:
            # New research fields have neutral defaults. Recompute fingerprints
            # without creating false change history or relabeling old people as
            # owners. Fetch pages again so explicitly labeled fields can be read.
            for row in conn.execute("SELECT * FROM records").fetchall():
                payload = dict(row)
                payload["extra"] = json.loads(payload["extra_json"] or "{}")
                conn.execute("UPDATE records SET content_hash=? WHERE id=?", (record_hash(payload), row["id"]))
            conn.execute("UPDATE pages SET content_hash=NULL, etag=NULL, last_modified=NULL")
            conn.execute("PRAGMA user_version=3")
        conn.commit()


def _sync_mark_interrupted_jobs() -> None:
    with closing(connect()) as conn:
        conn.execute(
            "UPDATE crawl_jobs SET status='interrupted', finished_at=?, message='Application restarted during crawl' WHERE status IN ('queued','running')",
            (utcnow(),),
        )
        conn.commit()


def _sync_create_source(data: dict[str, Any]) -> dict[str, Any]:
    now = utcnow()
    with closing(connect()) as conn:
        cur = conn.execute(
            """INSERT INTO sources
            (name,start_url,auto_scan,interval_minutes,max_pages,max_depth,concurrency,delay_ms,render_mode,respect_robots,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["name"], data["start_url"], int(data["auto_scan"]), data["interval_minutes"], data["max_pages"],
                data["max_depth"], data["concurrency"], data["delay_ms"], data["render_mode"], int(data["respect_robots"]), now,
            ),
        )
        conn.commit()
        source_id = int(cur.lastrowid)
    return _sync_get_source(source_id)


def _sync_get_source(source_id: int) -> dict[str, Any] | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return dict(row) if row else None


def _sync_list_sources() -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """SELECT s.*,
            (SELECT status FROM crawl_jobs j WHERE j.source_id=s.id ORDER BY j.id DESC LIMIT 1) AS last_status,
            (SELECT records_new FROM crawl_jobs j WHERE j.source_id=s.id ORDER BY j.id DESC LIMIT 1) AS last_new,
            (SELECT records_updated FROM crawl_jobs j WHERE j.source_id=s.id ORDER BY j.id DESC LIMIT 1) AS last_updated
            FROM sources s ORDER BY s.id DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def _sync_update_source(source_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    if not data:
        return _sync_get_source(source_id)
    allowed = {"name", "auto_scan", "interval_minutes", "max_pages", "max_depth", "concurrency", "delay_ms", "render_mode", "respect_robots"}
    values = {k: v for k, v in data.items() if k in allowed and v is not None}
    for key in ("auto_scan", "respect_robots"):
        if key in values:
            values[key] = int(values[key])
    if not values:
        return _sync_get_source(source_id)
    clause = ", ".join(f"{k}=?" for k in values)
    with closing(connect()) as conn:
        conn.execute(f"UPDATE sources SET {clause} WHERE id=?", (*values.values(), source_id))
        conn.commit()
    return _sync_get_source(source_id)


def _sync_delete_source(source_id: int) -> bool:
    with closing(connect()) as conn:
        cur = conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
        conn.commit()
        return cur.rowcount > 0


def _sync_mark_source_scanned(source_id: int) -> None:
    with closing(connect()) as conn:
        conn.execute("UPDATE sources SET last_scan_at=? WHERE id=?", (utcnow(), source_id))
        conn.commit()


def _sync_create_job(source_id: int, force_full: bool) -> int:
    with closing(connect()) as conn:
        cur = conn.execute(
            "INSERT INTO crawl_jobs(source_id,status,force_full) VALUES (?, 'queued', ?)",
            (source_id, int(force_full)),
        )
        conn.commit()
        return int(cur.lastrowid)


def _sync_update_job(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    clause = ", ".join(f"{k}=?" for k in fields)
    with closing(connect()) as conn:
        conn.execute(f"UPDATE crawl_jobs SET {clause} WHERE id=?", (*fields.values(), job_id))
        conn.commit()


def _sync_increment_job(job_id: int, **increments: int) -> None:
    if not increments:
        return
    clause = ", ".join(f"{k}={k}+?" for k in increments)
    with closing(connect()) as conn:
        conn.execute(f"UPDATE crawl_jobs SET {clause} WHERE id=?", (*increments.values(), job_id))
        conn.commit()


def _sync_get_job(job_id: int) -> dict[str, Any] | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT j.*, s.name AS source_name FROM crawl_jobs j JOIN sources s ON s.id=j.source_id WHERE j.id=?",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None


def _sync_list_jobs(limit: int = 30) -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT j.*, s.name AS source_name FROM crawl_jobs j JOIN sources s ON s.id=j.source_id ORDER BY j.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def _sync_running_job_for_source(source_id: int) -> dict[str, Any] | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT * FROM crawl_jobs WHERE source_id=? AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
            (source_id,),
        ).fetchone()
        return dict(row) if row else None


def _sync_get_page(source_id: int, url: str) -> dict[str, Any] | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM pages WHERE source_id=? AND url=?", (source_id, url)).fetchone()
        return dict(row) if row else None


def _sync_upsert_page(source_id: int, url: str, **fields: Any) -> None:
    fields["last_crawled_at"] = utcnow()
    with closing(connect()) as conn:
        existing = conn.execute("SELECT id FROM pages WHERE source_id=? AND url=?", (source_id, url)).fetchone()
        if existing:
            clause = ", ".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE pages SET {clause} WHERE source_id=? AND url=?", (*fields.values(), source_id, url))
        else:
            keys = ["source_id", "url", *fields.keys()]
            placeholders = ",".join("?" for _ in keys)
            conn.execute(
                f"INSERT INTO pages({','.join(keys)}) VALUES ({placeholders})",
                (source_id, url, *fields.values()),
            )
        conn.commit()


def _sync_upsert_record(source_id: int, record: dict[str, Any]) -> str:
    now = utcnow()
    normalized = canonical_record(record)
    key = entity_key(normalized)
    digest = record_hash(normalized)
    payload_json = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM records WHERE source_id=? AND entity_key=?", (source_id, key)
        ).fetchone()
        if not existing:
            cur = conn.execute(
                """INSERT INTO records
                (source_id,entity_key,content_hash,name,company,owner,phone,address,location,osha_status,osha_details,date,external_id,source_url,extra_json,first_seen,last_seen,last_changed,active)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (source_id, key, digest, normalized["name"], normalized["company"], normalized["owner"], normalized["phone"], normalized["address"],
                 normalized["location"], normalized["osha_status"], normalized["osha_details"],
                 normalized["date"], normalized["external_id"], normalized["source_url"], json.dumps(normalized["extra"], ensure_ascii=False), now, now, now),
            )
            conn.execute(
                "INSERT INTO record_history(record_id,changed_at,old_json,new_json) VALUES (?,?,NULL,?)",
                (cur.lastrowid, now, payload_json),
            )
            conn.commit()
            return "new"
        if existing["content_hash"] != digest:
            old_payload = json.dumps({
                "name": existing["name"], "company": existing["company"], "owner": existing["owner"], "phone": existing["phone"],
                "address": existing["address"], "date": existing["date"], "external_id": existing["external_id"],
                "location": existing["location"], "osha_status": existing["osha_status"], "osha_details": existing["osha_details"],
                "source_url": existing["source_url"], "extra": json.loads(existing["extra_json"] or "{}"),
            }, ensure_ascii=False, sort_keys=True)
            conn.execute(
                """UPDATE records SET content_hash=?,name=?,company=?,owner=?,phone=?,address=?,location=?,osha_status=?,osha_details=?,date=?,external_id=?,source_url=?,extra_json=?,last_seen=?,last_changed=?,active=1,inactive_since=NULL WHERE id=?""",
                (digest, normalized["name"], normalized["company"], normalized["owner"], normalized["phone"], normalized["address"],
                 normalized["location"], normalized["osha_status"], normalized["osha_details"], normalized["date"],
                 normalized["external_id"], normalized["source_url"], json.dumps(normalized["extra"], ensure_ascii=False), now, now, existing["id"]),
            )
            conn.execute(
                "INSERT INTO record_history(record_id,changed_at,old_json,new_json) VALUES (?,?,?,?)",
                (existing["id"], now, old_payload, payload_json),
            )
            conn.commit()
            return "updated"
        conn.execute("UPDATE records SET last_seen=?, active=1,inactive_since=NULL WHERE id=?", (now, existing["id"]))
        conn.commit()
        return "unchanged"


def _sync_search_records(q: str = "", source_id: int | None = None, limit: int = 100, offset: int = 0,
                         field: str = "all", osha_status: str | None = None) -> dict[str, Any]:
    search_fields = {"all": ("name", "company", "owner", "phone", "address", "location", "date", "source_url", "external_id", "osha_details", "extra_json"),
                     **{name: (name,) for name in ("name", "company", "owner", "address", "location")}}
    if field not in search_fields:
        raise ValueError("Unknown research search field")
    if osha_status is not None and osha_status not in {"unknown", "open", "closed", "none_reported"}:
        raise ValueError("Unknown OSHA status filter")
    clauses = ["1=1"]
    params: list[Any] = []
    if source_id:
        clauses.append("r.source_id=?")
        params.append(source_id)
    if osha_status is not None:
        clauses.append("r.osha_status=?")
        params.append(osha_status)
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append("(" + " OR ".join(f"r.{column} LIKE ? ESCAPE '\\'" for column in search_fields[field]) + ")")
        params.extend([like] * len(search_fields[field]))
    where = " AND ".join(clauses)
    with closing(connect()) as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM records r WHERE {where}", params).fetchone()["c"]
        rows = conn.execute(
            f"""SELECT r.*, s.name AS source_name FROM records r JOIN sources s ON s.id=r.source_id
            WHERE {where} ORDER BY r.last_changed DESC, r.id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        return {"total": total, "items": [dict(r) for r in rows]}


def _sync_all_records_for_export(q: str = "", source_id: int | None = None, field: str = "all",
                                 osha_status: str | None = None) -> list[dict[str, Any]]:
    result = _sync_search_records(q=q, source_id=source_id, limit=250000, offset=0, field=field, osha_status=osha_status)
    if result["total"] > len(result["items"]):
        raise ValueError("Export exceeds 250000 records; narrow the search or source filter")
    return result["items"]


def _sync_get_record(record_id: int) -> dict[str, Any] | None:
    with closing(connect()) as conn:
        row = conn.execute("""SELECT r.*, s.name AS source_name FROM records r
            JOIN sources s ON s.id=r.source_id WHERE r.id=?""", (record_id,)).fetchone()
        return dict(row) if row else None


def _sync_record_history(record_id: int) -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM record_history WHERE record_id=? ORDER BY id DESC", (record_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def _sync_stats() -> dict[str, int]:
    with closing(connect()) as conn:
        source_count = conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"]
        record_count = conn.execute("SELECT COUNT(*) c FROM records").fetchone()["c"]
        changed_24h = conn.execute("SELECT COUNT(*) c FROM records WHERE julianday(last_changed) >= julianday('now','-1 day')").fetchone()["c"]
        running = conn.execute("SELECT COUNT(*) c FROM crawl_jobs WHERE status IN ('queued','running')").fetchone()["c"]
        return {"sources": source_count, "records": record_count, "changed_24h": changed_24h, "running_jobs": running}


def _sync_observe_page_records(source_id: int, url: str, job_id: int, records: list[dict]) -> None:
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM page_records WHERE source_id=? AND url=?", (source_id, url))
        for record in records:
            conn.execute("""INSERT OR REPLACE INTO page_records(source_id,url,record_id,last_job,payload_hash)
                SELECT ?,?,id,?,? FROM records WHERE source_id=? AND entity_key=?""",
                (source_id, url, job_id, record_hash(record), source_id, entity_key(record)))
        conn.commit()


def _sync_touch_page_records(source_id: int, url: str, job_id: int) -> list[dict]:
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE page_records SET last_job=? WHERE source_id=? AND url=?", (job_id, source_id, url))
        conn.execute("""UPDATE records SET last_seen=?,active=1,inactive_since=NULL WHERE id IN
            (SELECT record_id FROM page_records WHERE source_id=? AND url=?)""", (utcnow(), source_id, url))
        observations = conn.execute("""SELECT r.entity_key,p.payload_hash FROM page_records p
            JOIN records r ON r.id=p.record_id WHERE p.source_id=? AND p.url=?""", (source_id, url)).fetchall()
        conn.commit()
        return [dict(row) for row in observations]


def _sync_finish_observations(source_id: int, job_id: int) -> None:
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""UPDATE records SET active=0,inactive_since=? WHERE source_id=? AND active=1 AND id NOT IN
            (SELECT record_id FROM page_records WHERE source_id=? AND last_job=?)""",
            (utcnow(), source_id, source_id, job_id))
        conn.commit()


def _sync_page_errors(source_id: int) -> list[dict]:
    with closing(connect()) as conn:
        rows = conn.execute("SELECT url,last_error,last_crawled_at FROM pages WHERE source_id=? AND last_error IS NOT NULL ORDER BY last_crawled_at DESC LIMIT 200", (source_id,)).fetchall()
        return [dict(row) for row in rows]


def _sync_add_event(level, component, message, details, job_id=None, source_id=None):
    with closing(connect()) as conn:
        cur = conn.execute("""INSERT INTO activity_events(created_at,level,component,message,details_json,job_id,source_id)
            VALUES (?,?,?,?,?,?,?)""", (utcnow(), level, component, message, json.dumps(details), job_id, source_id))
        conn.execute("DELETE FROM activity_events WHERE id <= ?", (cur.lastrowid - 5000,))
        conn.commit()
        return cur.lastrowid


def _sync_list_events(after=0, limit=300):
    with closing(connect()) as conn:
        if after:
            rows = conn.execute("SELECT * FROM activity_events WHERE id>? ORDER BY id LIMIT ?", (after, limit)).fetchall()
        else:
            rows = list(reversed(conn.execute("SELECT * FROM activity_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()))
        items = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            items.append(item)
        newest = conn.execute("SELECT COALESCE(MAX(id),0) FROM activity_events").fetchone()[0]
        return {"items": items, "cursor": items[-1]["id"] if items else after,
                "latest": newest, "retention": 5000, "reset": after > newest}

# SQLite and lock waits run off the event loop. One worker serializes local DB
# operations; write transactions still protect read-modify-write across connections.
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="crawler-db")


def _async_database(function):
    @functools.wraps(function)
    async def call(*args, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(
            _EXECUTOR, functools.partial(function, *args, **kwargs))
    return call


init_db = _async_database(_sync_init_db)
mark_interrupted_jobs = _async_database(_sync_mark_interrupted_jobs)
create_source = _async_database(_sync_create_source)
get_source = _async_database(_sync_get_source)
list_sources = _async_database(_sync_list_sources)
update_source = _async_database(_sync_update_source)
delete_source = _async_database(_sync_delete_source)
mark_source_scanned = _async_database(_sync_mark_source_scanned)
create_job = _async_database(_sync_create_job)
update_job = _async_database(_sync_update_job)
increment_job = _async_database(_sync_increment_job)
get_job = _async_database(_sync_get_job)
list_jobs = _async_database(_sync_list_jobs)
running_job_for_source = _async_database(_sync_running_job_for_source)
get_page = _async_database(_sync_get_page)
upsert_page = _async_database(_sync_upsert_page)
upsert_record = _async_database(_sync_upsert_record)
search_records = _async_database(_sync_search_records)
get_record = _async_database(_sync_get_record)
all_records_for_export = _async_database(_sync_all_records_for_export)
record_history = _async_database(_sync_record_history)
stats = _async_database(_sync_stats)
observe_page_records = _async_database(_sync_observe_page_records)
touch_page_records = _async_database(_sync_touch_page_records)
finish_observations = _async_database(_sync_finish_observations)
page_errors = _async_database(_sync_page_errors)
list_events = _async_database(_sync_list_events)
