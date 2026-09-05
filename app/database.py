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


async def init_db() -> None:
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
            """
        )
        conn.commit()


async def mark_interrupted_jobs() -> None:
    with closing(connect()) as conn:
        conn.execute(
            "UPDATE crawl_jobs SET status='interrupted', finished_at=?, message='Application restarted during crawl' WHERE status IN ('queued','running')",
            (utcnow(),),
        )
        conn.commit()


async def create_source(data: dict[str, Any]) -> dict[str, Any]:
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
    return await get_source(source_id)


async def get_source(source_id: int) -> dict[str, Any] | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        return dict(row) if row else None


async def list_sources() -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """SELECT s.*,
            (SELECT status FROM crawl_jobs j WHERE j.source_id=s.id ORDER BY j.id DESC LIMIT 1) AS last_status,
            (SELECT records_new FROM crawl_jobs j WHERE j.source_id=s.id ORDER BY j.id DESC LIMIT 1) AS last_new,
            (SELECT records_updated FROM crawl_jobs j WHERE j.source_id=s.id ORDER BY j.id DESC LIMIT 1) AS last_updated
            FROM sources s ORDER BY s.id DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


async def update_source(source_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    if not data:
        return await get_source(source_id)
    allowed = {"name", "auto_scan", "interval_minutes", "max_pages", "max_depth", "concurrency", "delay_ms", "render_mode", "respect_robots"}
    values = {k: v for k, v in data.items() if k in allowed and v is not None}
    for key in ("auto_scan", "respect_robots"):
        if key in values:
            values[key] = int(values[key])
    if not values:
        return await get_source(source_id)
    clause = ", ".join(f"{k}=?" for k in values)
    with closing(connect()) as conn:
        conn.execute(f"UPDATE sources SET {clause} WHERE id=?", (*values.values(), source_id))
        conn.commit()
    return await get_source(source_id)


async def delete_source(source_id: int) -> bool:
    with closing(connect()) as conn:
        cur = conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
        conn.commit()
        return cur.rowcount > 0


async def mark_source_scanned(source_id: int) -> None:
    with closing(connect()) as conn:
        conn.execute("UPDATE sources SET last_scan_at=? WHERE id=?", (utcnow(), source_id))
        conn.commit()


async def create_job(source_id: int, force_full: bool) -> int:
    with closing(connect()) as conn:
        cur = conn.execute(
            "INSERT INTO crawl_jobs(source_id,status,force_full) VALUES (?, 'queued', ?)",
            (source_id, int(force_full)),
        )
        conn.commit()
        return int(cur.lastrowid)


async def update_job(job_id: int, **fields: Any) -> None:
    if not fields:
        return
    clause = ", ".join(f"{k}=?" for k in fields)
    with closing(connect()) as conn:
        conn.execute(f"UPDATE crawl_jobs SET {clause} WHERE id=?", (*fields.values(), job_id))
        conn.commit()


async def increment_job(job_id: int, **increments: int) -> None:
    if not increments:
        return
    clause = ", ".join(f"{k}={k}+?" for k in increments)
    with closing(connect()) as conn:
        conn.execute(f"UPDATE crawl_jobs SET {clause} WHERE id=?", (*increments.values(), job_id))
        conn.commit()


async def get_job(job_id: int) -> dict[str, Any] | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT j.*, s.name AS source_name FROM crawl_jobs j JOIN sources s ON s.id=j.source_id WHERE j.id=?",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None


async def list_jobs(limit: int = 30) -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT j.*, s.name AS source_name FROM crawl_jobs j JOIN sources s ON s.id=j.source_id ORDER BY j.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


async def running_job_for_source(source_id: int) -> dict[str, Any] | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT * FROM crawl_jobs WHERE source_id=? AND status IN ('queued','running') ORDER BY id DESC LIMIT 1",
            (source_id,),
        ).fetchone()
        return dict(row) if row else None


async def get_page(source_id: int, url: str) -> dict[str, Any] | None:
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM pages WHERE source_id=? AND url=?", (source_id, url)).fetchone()
        return dict(row) if row else None


async def upsert_page(source_id: int, url: str, **fields: Any) -> None:
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


async def upsert_record(source_id: int, record: dict[str, Any]) -> str:
    now = utcnow()
    normalized = canonical_record(record)
    key = entity_key(normalized)
    digest = record_hash(normalized)
    payload_json = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    with closing(connect()) as conn:
        existing = conn.execute(
            "SELECT * FROM records WHERE source_id=? AND entity_key=?", (source_id, key)
        ).fetchone()
        if not existing:
            cur = conn.execute(
                """INSERT INTO records
                (source_id,entity_key,content_hash,name,company,phone,address,date,external_id,source_url,extra_json,first_seen,last_seen,last_changed,active)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                (source_id, key, digest, normalized["name"], normalized["company"], normalized["phone"], normalized["address"],
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
                "name": existing["name"], "company": existing["company"], "phone": existing["phone"],
                "address": existing["address"], "date": existing["date"], "external_id": existing["external_id"],
                "source_url": existing["source_url"], "extra": json.loads(existing["extra_json"] or "{}"),
            }, ensure_ascii=False, sort_keys=True)
            conn.execute(
                """UPDATE records SET content_hash=?,name=?,company=?,phone=?,address=?,date=?,external_id=?,source_url=?,extra_json=?,last_seen=?,last_changed=?,active=1 WHERE id=?""",
                (digest, normalized["name"], normalized["company"], normalized["phone"], normalized["address"], normalized["date"],
                 normalized["external_id"], normalized["source_url"], json.dumps(normalized["extra"], ensure_ascii=False), now, now, existing["id"]),
            )
            conn.execute(
                "INSERT INTO record_history(record_id,changed_at,old_json,new_json) VALUES (?,?,?,?)",
                (existing["id"], now, old_payload, payload_json),
            )
            conn.commit()
            return "updated"
        conn.execute("UPDATE records SET last_seen=?, active=1 WHERE id=?", (now, existing["id"]))
        conn.commit()
        return "unchanged"


async def search_records(q: str = "", source_id: int | None = None, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    clauses = ["1=1"]
    params: list[Any] = []
    if source_id:
        clauses.append("r.source_id=?")
        params.append(source_id)
    if q:
        like = f"%{q}%"
        clauses.append("(r.name LIKE ? OR r.company LIKE ? OR r.phone LIKE ? OR r.address LIKE ? OR r.date LIKE ? OR r.source_url LIKE ?)")
        params.extend([like] * 6)
    where = " AND ".join(clauses)
    with closing(connect()) as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM records r WHERE {where}", params).fetchone()["c"]
        rows = conn.execute(
            f"""SELECT r.*, s.name AS source_name FROM records r JOIN sources s ON s.id=r.source_id
            WHERE {where} ORDER BY r.last_changed DESC, r.id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        return {"total": total, "items": [dict(r) for r in rows]}


async def all_records_for_export(q: str = "", source_id: int | None = None) -> list[dict[str, Any]]:
    result = await search_records(q=q, source_id=source_id, limit=250000, offset=0)
    return result["items"]


async def record_history(record_id: int) -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM record_history WHERE record_id=? ORDER BY id DESC", (record_id,)
        ).fetchall()
        return [dict(r) for r in rows]


async def stats() -> dict[str, int]:
    with closing(connect()) as conn:
        source_count = conn.execute("SELECT COUNT(*) c FROM sources").fetchone()["c"]
        record_count = conn.execute("SELECT COUNT(*) c FROM records").fetchone()["c"]
        changed_24h = conn.execute("SELECT COUNT(*) c FROM records WHERE julianday(last_changed) >= julianday('now','-1 day')").fetchone()["c"]
        running = conn.execute("SELECT COUNT(*) c FROM crawl_jobs WHERE status IN ('queued','running')").fetchone()["c"]
        return {"sources": source_count, "records": record_count, "changed_24h": changed_24h, "running_jobs": running}
