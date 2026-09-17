from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def write(path, content):
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path, old, new):
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}")
    write(path, content.replace(old, new, 1))


# ------------------------- database.py -------------------------
replace_once(
    "app/database.py",
    '''def utcnow() -> str:\n    return datetime.now(timezone.utc).isoformat()\n\n\ndef connect() -> sqlite3.Connection:\n''',
    '''def utcnow() -> str:\n    return datetime.now(timezone.utc).isoformat()\n\n\nJOB_STATES = {"queued", "running", "partial", "completed", "failed", "cancelled", "interrupted"}\nJOB_TRANSITIONS = {\n    "queued": {"running", "failed", "cancelled", "interrupted"},\n    "running": {"partial", "completed", "failed", "cancelled", "interrupted"},\n    "partial": set(),\n    "completed": set(),\n    "failed": set(),\n    "cancelled": set(),\n    "interrupted": set(),\n}\n\n\ndef _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:\n    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}\n\n\ndef _run_migration(conn: sqlite3.Connection, target_version: int, migration) -> None:\n    current = int(conn.execute("PRAGMA user_version").fetchone()[0])\n    if current >= target_version:\n        return\n    savepoint = f"migration_{target_version}"\n    conn.execute(f"SAVEPOINT {savepoint}")\n    try:\n        migration(conn)\n        conn.execute(f"PRAGMA user_version={int(target_version)}")\n        conn.execute(f"RELEASE SAVEPOINT {savepoint}")\n    except Exception:\n        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")\n        conn.execute(f"RELEASE SAVEPOINT {savepoint}")\n        raise\n\n\ndef _migrate_v4(conn: sqlite3.Connection) -> None:\n    columns = _table_columns(conn, "crawl_jobs")\n    additions = {\n        "selection_json": "TEXT NOT NULL DEFAULT 'null'",\n        "parent_job_id": "INTEGER REFERENCES crawl_jobs(id)",\n        "recovery_mode": "TEXT NOT NULL DEFAULT 'fresh'",\n        "restart_reason": "TEXT",\n        "attempt_no": "INTEGER NOT NULL DEFAULT 1",\n        "retry_count": "INTEGER NOT NULL DEFAULT 0",\n        "heartbeat_at": "TEXT",\n        "interrupted_at": "TEXT",\n        "acquisition_limits_json": "TEXT NOT NULL DEFAULT '{}'",\n        "completeness_json": "TEXT NOT NULL DEFAULT '{}'",\n    }\n    for name, declaration in additions.items():\n        if name not in columns:\n            conn.execute(f"ALTER TABLE crawl_jobs ADD COLUMN {name} {declaration}")\n\n    history_columns = _table_columns(conn, "bidder_master_history")\n    if "proposal_id" not in history_columns:\n        conn.execute("ALTER TABLE bidder_master_history ADD COLUMN proposal_id INTEGER")\n\n    conn.executescript(\n        """\n        CREATE TABLE IF NOT EXISTS crawl_job_diagnostics (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            job_id INTEGER NOT NULL REFERENCES crawl_jobs(id) ON DELETE CASCADE,\n            source_id INTEGER,\n            created_at TEXT NOT NULL,\n            level TEXT NOT NULL,\n            category TEXT NOT NULL,\n            message TEXT NOT NULL,\n            url TEXT,\n            retryable INTEGER NOT NULL DEFAULT 0,\n            attempt_no INTEGER,\n            details_json TEXT NOT NULL DEFAULT '{}'\n        );\n        CREATE TABLE IF NOT EXISTS crawl_frontier (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            job_id INTEGER NOT NULL REFERENCES crawl_jobs(id) ON DELETE CASCADE,\n            source_id INTEGER NOT NULL,\n            url TEXT NOT NULL,\n            depth INTEGER NOT NULL DEFAULT 0,\n            state TEXT NOT NULL DEFAULT 'queued',\n            attempts INTEGER NOT NULL DEFAULT 0,\n            last_error TEXT,\n            discovered_links_json TEXT NOT NULL DEFAULT '[]',\n            updated_at TEXT NOT NULL,\n            UNIQUE(job_id, url)\n        );\n        CREATE INDEX IF NOT EXISTS idx_jobs_status_id ON crawl_jobs(status, id DESC);\n        CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_recovery_parent ON crawl_jobs(parent_job_id) WHERE parent_job_id IS NOT NULL;\n        CREATE INDEX IF NOT EXISTS idx_job_diagnostics_job ON crawl_job_diagnostics(job_id, id);\n        CREATE INDEX IF NOT EXISTS idx_job_diagnostics_source ON crawl_job_diagnostics(source_id, id DESC);\n        CREATE INDEX IF NOT EXISTS idx_frontier_job_state ON crawl_frontier(job_id, state, depth, id);\n        CREATE INDEX IF NOT EXISTS idx_record_history_record ON record_history(record_id, id DESC);\n        CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_events(created_at, id);\n        CREATE INDEX IF NOT EXISTS idx_activity_job ON activity_events(job_id, id);\n        CREATE INDEX IF NOT EXISTS idx_records_active_changed ON records(source_id, active, last_changed DESC, id DESC);\n        CREATE INDEX IF NOT EXISTS idx_proposals_signature_status ON bidder_proposals(signature, status, id DESC);\n        CREATE UNIQUE INDEX IF NOT EXISTS idx_bidder_history_proposal ON bidder_master_history(proposal_id) WHERE proposal_id IS NOT NULL;\n        """\n    )\n\n\ndef connect() -> sqlite3.Connection:\n'''
)

replace_once(
    "app/database.py",
    '''    conn.execute("PRAGMA journal_mode=WAL")\n    conn.execute("PRAGMA foreign_keys=ON")\n    conn.execute("PRAGMA busy_timeout=30000")\n''',
    '''    conn.execute("PRAGMA journal_mode=WAL")\n    conn.execute("PRAGMA synchronous=FULL")\n    conn.execute("PRAGMA foreign_keys=ON")\n    conn.execute("PRAGMA busy_timeout=30000")\n    conn.execute("PRAGMA wal_autocheckpoint=1000")\n'''
)

replace_once(
    "app/database.py",
    '''        if version < 3:\n            # New research fields have neutral defaults. Recompute fingerprints\n            # without creating false change history or relabeling old people as\n            # owners. Fetch pages again so explicitly labeled fields can be read.\n            for row in conn.execute("SELECT * FROM records").fetchall():\n                payload = dict(row)\n                payload["extra"] = json.loads(payload["extra_json"] or "{}")\n                conn.execute("UPDATE records SET content_hash=? WHERE id=?", (record_hash(payload), row["id"]))\n            conn.execute("UPDATE pages SET content_hash=NULL, etag=NULL, last_modified=NULL")\n            conn.execute("PRAGMA user_version=3")\n        conn.commit()\n''',
    '''        if version < 3:\n            # New research fields have neutral defaults. Recompute fingerprints\n            # without creating false change history or relabeling old people as\n            # owners. Fetch pages again so explicitly labeled fields can be read.\n            for row in conn.execute("SELECT * FROM records").fetchall():\n                payload = dict(row)\n                payload["extra"] = json.loads(payload["extra_json"] or "{}")\n                conn.execute("UPDATE records SET content_hash=? WHERE id=?", (record_hash(payload), row["id"]))\n            conn.execute("UPDATE pages SET content_hash=NULL, etag=NULL, last_modified=NULL")\n            conn.execute("PRAGMA user_version=3")\n        if int(conn.execute("PRAGMA user_version").fetchone()[0]) < 4:\n            _run_migration(conn, 4, _migrate_v4)\n        quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]\n        if quick_check != "ok":\n            raise RuntimeError(f"SQLite quick_check failed: {quick_check}")\n        conn.commit()\n'''
)

replace_once(
    "app/database.py",
    '''def _sync_mark_interrupted_jobs() -> None:\n    with closing(connect()) as conn:\n        conn.execute(\n            "UPDATE crawl_jobs SET status='interrupted', finished_at=?, message='Application restarted during crawl' WHERE status IN ('queued','running')",\n            (utcnow(),),\n        )\n        conn.commit()\n''',
    '''def _sync_mark_interrupted_jobs() -> list[int]:\n    now = utcnow()\n    with closing(connect()) as conn:\n        conn.execute("BEGIN IMMEDIATE")\n        rows = conn.execute(\n            "SELECT id,source_id,status FROM crawl_jobs WHERE status IN ('queued','running') ORDER BY id"\n        ).fetchall()\n        for row in rows:\n            conn.execute(\n                """UPDATE crawl_jobs SET status='interrupted', finished_at=?, interrupted_at=?, heartbeat_at=?,\n                   message='Application restarted during crawl', restart_reason='Process ended before the scan reached a terminal state'\n                   WHERE id=? AND status IN ('queued','running')""",\n                (now, now, now, row["id"]),\n            )\n            conn.execute(\n                """INSERT INTO crawl_job_diagnostics\n                   (job_id,source_id,created_at,level,category,message,retryable,details_json)\n                   VALUES (?,?,?,?,?,?,0,'{}')""",\n                (row["id"], row["source_id"], now, "WARNING", "interruption",\n                 "Process ended before the scan reached a terminal state"),\n            )\n        conn.commit()\n        return [int(row["id"]) for row in rows]\n'''
)

replace_once(
    "app/database.py",
    '''def _sync_create_job(source_id: int, force_full: bool) -> int:\n    with closing(connect()) as conn:\n        cur = conn.execute(\n            "INSERT INTO crawl_jobs(source_id,status,force_full) VALUES (?, 'queued', ?)",\n            (source_id, int(force_full)),\n        )\n        conn.commit()\n        return int(cur.lastrowid)\n''',
    '''def _sync_create_job(source_id: int, force_full: bool, selection=None, *, parent_job_id: int | None = None,\n                     recovery_mode: str = "fresh", restart_reason: str | None = None, attempt_no: int = 1) -> int:\n    selection_json = json.dumps(selection if selection is not None else None, separators=(",", ":"))\n    with closing(connect()) as conn:\n        cur = conn.execute(\n            """INSERT INTO crawl_jobs\n               (source_id,status,force_full,selection_json,parent_job_id,recovery_mode,restart_reason,attempt_no,heartbeat_at)\n               VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?)""",\n            (source_id, int(force_full), selection_json, parent_job_id, recovery_mode, restart_reason, attempt_no, utcnow()),\n        )\n        conn.commit()\n        return int(cur.lastrowid)\n'''
)

replace_once(
    "app/database.py",
    '''def _sync_increment_job(job_id: int, **increments: int) -> None:\n''',
    '''def _sync_transition_job(job_id: int, new_status: str, message: str | None = None, **fields: Any) -> dict[str, Any]:\n    if new_status not in JOB_STATES:\n        raise ValueError(f"Unknown job status: {new_status}")\n    with closing(connect()) as conn:\n        conn.execute("BEGIN IMMEDIATE")\n        row = conn.execute("SELECT * FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()\n        if not row:\n            raise ValueError("Job not found")\n        old_status = row["status"]\n        if old_status == new_status:\n            conn.commit()\n            return dict(row)\n        if new_status not in JOB_TRANSITIONS.get(old_status, set()):\n            raise ValueError(f"Invalid job transition: {old_status} -> {new_status}")\n        now = utcnow()\n        values = dict(fields)\n        values["status"] = new_status\n        values["heartbeat_at"] = now\n        if message is not None:\n            values["message"] = message\n        if new_status == "running" and not row["started_at"]:\n            values["started_at"] = now\n        if new_status in {"partial", "completed", "failed", "cancelled", "interrupted"}:\n            values.setdefault("finished_at", now)\n        if new_status == "interrupted":\n            values.setdefault("interrupted_at", now)\n        clause = ", ".join(f"{key}=?" for key in values)\n        conn.execute(f"UPDATE crawl_jobs SET {clause} WHERE id=?", (*values.values(), job_id))\n        conn.commit()\n        return dict(conn.execute("SELECT * FROM crawl_jobs WHERE id=?", (job_id,)).fetchone())\n\n\ndef _sync_record_job_diagnostic(job_id: int, level: str, category: str, message: str, *,\n                                source_id: int | None = None, url: str | None = None,\n                                retryable: bool = False, attempt_no: int | None = None,\n                                details: dict[str, Any] | None = None) -> int:\n    with closing(connect()) as conn:\n        if source_id is None:\n            row = conn.execute("SELECT source_id FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()\n            source_id = int(row["source_id"]) if row else None\n        cur = conn.execute(\n            """INSERT INTO crawl_job_diagnostics\n               (job_id,source_id,created_at,level,category,message,url,retryable,attempt_no,details_json)\n               VALUES (?,?,?,?,?,?,?,?,?,?)""",\n            (job_id, source_id, utcnow(), level, category, str(message)[:4000], url, int(retryable), attempt_no,\n             json.dumps(details or {}, ensure_ascii=False, sort_keys=True, default=str)),\n        )\n        conn.commit()\n        return int(cur.lastrowid)\n\n\ndef _sync_record_retry(job_id: int, url: str, attempt_no: int, reason: str, *, source_id: int | None = None) -> None:\n    with closing(connect()) as conn:\n        conn.execute("BEGIN IMMEDIATE")\n        conn.execute("UPDATE crawl_jobs SET retry_count=retry_count+1,heartbeat_at=? WHERE id=?", (utcnow(), job_id))\n        if source_id is None:\n            row = conn.execute("SELECT source_id FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()\n            source_id = int(row["source_id"]) if row else None\n        conn.execute(\n            """INSERT INTO crawl_job_diagnostics\n               (job_id,source_id,created_at,level,category,message,url,retryable,attempt_no,details_json)\n               VALUES (?,?,?,?,?,?,?,1,?,'{}')""",\n            (job_id, source_id, utcnow(), "WARNING", "retry", str(reason)[:4000], url, attempt_no),\n        )\n        conn.commit()\n\n\ndef _sync_create_recovery_job(parent_job_id: int, max_attempts: int = 3) -> dict[str, Any] | None:\n    with closing(connect()) as conn:\n        conn.execute("BEGIN IMMEDIATE")\n        parent = conn.execute("SELECT * FROM crawl_jobs WHERE id=?", (parent_job_id,)).fetchone()\n        if not parent or parent["status"] != "interrupted":\n            conn.commit()\n            return None\n        existing = conn.execute("SELECT * FROM crawl_jobs WHERE parent_job_id=?", (parent_job_id,)).fetchone()\n        if existing:\n            conn.commit()\n            return dict(existing)\n        next_attempt = int(parent["attempt_no"] or 1) + 1\n        if next_attempt > max_attempts:\n            conn.execute(\n                """INSERT INTO crawl_job_diagnostics\n                   (job_id,source_id,created_at,level,category,message,retryable,details_json)\n                   VALUES (?,?,?,?,?,?,0,'{}')""",\n                (parent_job_id, parent["source_id"], utcnow(), "ERROR", "recovery_exhausted",\n                 f"Automatic restart limit reached after {max_attempts} attempts"),\n            )\n            conn.commit()\n            return None\n        reason = "Previous attempt was interrupted. Targeted adapters keep aggregate state in memory, so this attempt restarts cleanly rather than claiming an unsafe checkpoint resume."\n        cur = conn.execute(\n            """INSERT INTO crawl_jobs\n               (source_id,status,force_full,selection_json,parent_job_id,recovery_mode,restart_reason,attempt_no,heartbeat_at,acquisition_limits_json)\n               VALUES (?, 'queued', ?, ?, ?, 'restart', ?, ?, ?, ?)""",\n            (parent["source_id"], parent["force_full"], parent["selection_json"], parent_job_id, reason, next_attempt,\n             utcnow(), parent["acquisition_limits_json"] or "{}"),\n        )\n        child_id = int(cur.lastrowid)\n        conn.execute(\n            """INSERT INTO crawl_job_diagnostics\n               (job_id,source_id,created_at,level,category,message,retryable,details_json)\n               VALUES (?,?,?,?,?,?,0,?)""",\n            (child_id, parent["source_id"], utcnow(), "WARNING", "recovery_restart", reason,\n             json.dumps({"parent_job_id": parent_job_id, "attempt_no": next_attempt})),\n        )\n        conn.commit()\n        return dict(conn.execute("SELECT * FROM crawl_jobs WHERE id=?", (child_id,)).fetchone())\n\n\ndef _sync_seed_frontier(job_id: int, source_id: int, urls: list[str], depth: int) -> None:\n    if not urls:\n        return\n    now = utcnow()\n    with closing(connect()) as conn:\n        conn.execute("BEGIN IMMEDIATE")\n        conn.executemany(\n            """INSERT OR IGNORE INTO crawl_frontier(job_id,source_id,url,depth,state,updated_at)\n               VALUES (?,?,?,?,'queued',?)""",\n            [(job_id, source_id, url, depth, now) for url in urls],\n        )\n        conn.commit()\n\n\ndef _sync_frontier_state(job_id: int, url: str, state: str, *, last_error: str | None = None,\n                         discovered_links: list[str] | None = None, increment_attempt: bool = False) -> None:\n    if state not in {"queued", "processing", "done", "failed"}:\n        raise ValueError("Unknown frontier state")\n    fields = ["state=?", "updated_at=?", "last_error=?"]\n    params: list[Any] = [state, utcnow(), last_error]\n    if discovered_links is not None:\n        fields.append("discovered_links_json=?")\n        params.append(json.dumps(discovered_links, separators=(",", ":")))\n    if increment_attempt:\n        fields.append("attempts=attempts+1")\n    with closing(connect()) as conn:\n        conn.execute(f"UPDATE crawl_frontier SET {', '.join(fields)} WHERE job_id=? AND url=?", (*params, job_id, url))\n        conn.commit()\n\n\ndef _sync_frontier_summary(job_id: int) -> dict[str, Any]:\n    with closing(connect()) as conn:\n        rows = conn.execute("SELECT state,COUNT(*) count FROM crawl_frontier WHERE job_id=? GROUP BY state", (job_id,)).fetchall()\n        counts = {row["state"]: row["count"] for row in rows}\n        total = sum(counts.values())\n        return {"job_id": job_id, "total": total, "states": counts}\n\n\ndef _sync_job_diagnostics(job_id: int, limit: int = 1000) -> list[dict[str, Any]]:\n    with closing(connect()) as conn:\n        rows = conn.execute(\n            "SELECT * FROM crawl_job_diagnostics WHERE job_id=? ORDER BY id LIMIT ?", (job_id, limit)\n        ).fetchall()\n        items = []\n        for row in rows:\n            item = dict(row)\n            item["details"] = json.loads(item.pop("details_json") or "{}")\n            items.append(item)\n        return items\n\n\ndef _sync_integrity_check(full: bool = False) -> dict[str, Any]:\n    with closing(connect()) as conn:\n        pragma = "integrity_check" if full else "quick_check"\n        checks = [row[0] for row in conn.execute(f"PRAGMA {pragma}").fetchall()]\n        foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]\n        return {"ok": checks == ["ok"] and not foreign_keys, "check": pragma, "results": checks, "foreign_key_issues": foreign_keys}\n\n\ndef _sync_increment_job(job_id: int, **increments: int) -> None:\n'''
)

replace_once(
    "app/database.py",
    '''mark_interrupted_jobs = _async_database(_sync_mark_interrupted_jobs)\ncreate_source = _async_database(_sync_create_source)\n''',
    '''mark_interrupted_jobs = _async_database(_sync_mark_interrupted_jobs)\ncreate_recovery_job = _async_database(_sync_create_recovery_job)\ntransition_job = _async_database(_sync_transition_job)\nrecord_job_diagnostic = _async_database(_sync_record_job_diagnostic)\nrecord_retry = _async_database(_sync_record_retry)\nseed_frontier = _async_database(_sync_seed_frontier)\nfrontier_state = _async_database(_sync_frontier_state)\nfrontier_summary = _async_database(_sync_frontier_summary)\njob_diagnostics = _async_database(_sync_job_diagnostics)\nintegrity_check = _async_database(_sync_integrity_check)\ncreate_source = _async_database(_sync_create_source)\n'''
)

# ------------------------- bidder_master.py -------------------------
replace_once(
    "app/bidder_master.py",
    '''            """INSERT INTO bidder_master_history\n               (master_pk,field_name,old_value,new_value,source_record_id,source_name,source_url,applied_at)\n               VALUES (?,?,?,?,?,?,?,?)""",\n            (\n                master["pk"], field, old_value, new_value, proposal["source_record_id"],\n                proposal["source_name"], proposal["source_url"], now,\n            ),\n''',
    '''            """INSERT INTO bidder_master_history\n               (master_pk,field_name,old_value,new_value,source_record_id,source_name,source_url,applied_at,proposal_id)\n               VALUES (?,?,?,?,?,?,?,?,?)""",\n            (\n                master["pk"], field, old_value, new_value, proposal["source_record_id"],\n                proposal["source_name"], proposal["source_url"], now, proposal_id,\n            ),\n'''
)

# ------------------------- crawler.py -------------------------
replace_once(
    "app/crawler.py",
    '''        activity.emit("INFO", "Scan started", source_id=self.source_id, job_id=self.job_id, url=self.start_url)\n        await db.update_job(self.job_id, status="running", started_at=db.utcnow(), message="Preparing crawl")\n''',
    '''        activity.emit("INFO", "Scan started", source_id=self.source_id, job_id=self.job_id, url=self.start_url)\n        await db.transition_job(self.job_id, "running", message="Preparing crawl")\n        await db.update_job(\n            self.job_id,\n            heartbeat_at=db.utcnow(),\n            acquisition_limits_json=json.dumps({\n                "max_pages": self.max_pages, "max_depth": self.max_depth, "concurrency": self.concurrency,\n                "delay_ms": int(self.delay * 1000), "force_full": bool(self.force_full),\n                "contractor_selection_count": None if self.master_ids is None else len(self.master_ids),\n            }, sort_keys=True),\n        )\n        await db.record_job_diagnostic(\n            self.job_id, "INFO", "lifecycle", "Scan entered running state", source_id=self.source_id,\n            details={"recovery_safe_checkpoint": False if getattr(self.adapter, "query_mode", False) else None},\n        )\n'''
)

replace_once(
    "app/crawler.py",
    '''                else:\n                    activity.emit("INFO", "Crawl policy checked; discovering sitemap links", source_id=self.source_id, job_id=self.job_id)\n                    frontier = [self.start_url, *await self._discover_sitemap_urls(client)]\n                # Level barriers prevent a fast deep path from hiding a shorter path.\n''',
    '''                else:\n                    activity.emit("INFO", "Crawl policy checked; discovering sitemap links", source_id=self.source_id, job_id=self.job_id)\n                    frontier = [self.start_url, *await self._discover_sitemap_urls(client)]\n                await db.seed_frontier(self.job_id, self.source_id, [canonicalize_url(url) for url in frontier if canonicalize_url(url)], 0)\n                # Level barriers prevent a fast deep path from hiding a shorter path.\n'''
)

replace_once(
    "app/crawler.py",
    '''                    for offset in range(0, len(batch), self.concurrency):\n                        results = await asyncio.gather(*(self._process_safely(client, u) for u in batch[offset:offset + self.concurrency]))\n''',
    '''                    for offset in range(0, len(batch), self.concurrency):\n                        results = await asyncio.gather(*(self._process_safely(client, u, depth) for u in batch[offset:offset + self.concurrency]))\n'''
)

replace_once(
    "app/crawler.py",
    '''                await db.update_job(self.job_id, status="completed" if complete else "partial", finished_at=db.utcnow(),\n                    message=f"{self.processed} pages processed. " + ("Crawl boundary exhausted." if complete else "Limits or errors prevented a complete scan; missing records were not marked inactive."))\n        except asyncio.CancelledError:\n            activity.emit("WARNING", "Scan cancelled", source_id=self.source_id, job_id=self.job_id)\n            await db.update_job(self.job_id, status="cancelled", finished_at=db.utcnow(), message="Crawl cancelled")\n            raise\n        except Exception as exc:\n            safe_error = activity.redact(str(exc))\n            activity.emit("ERROR", "Scan failed", source_id=self.source_id, job_id=self.job_id, error=safe_error)\n            await db.increment_job(self.job_id, errors=1)\n            await db.update_job(self.job_id, status="failed", finished_at=db.utcnow(), message=safe_error[:1000])\n            raise\n''',
    '''                final_status = "completed" if complete else "partial"\n                completeness = {\n                    "complete": complete, "limited": self.limited, "errors": int(job["errors"]),\n                    "pages_processed": self.processed, "missing_records_marked_inactive": bool(complete and self.master_ids is None),\n                }\n                await db.transition_job(\n                    self.job_id, final_status,\n                    message=f"{self.processed} pages processed. " + ("Crawl boundary exhausted." if complete else "Limits or errors prevented a complete scan; missing records were not marked inactive."),\n                    completeness_json=json.dumps(completeness, sort_keys=True),\n                )\n                await db.record_job_diagnostic(\n                    self.job_id, "INFO" if complete else "WARNING", "completion",\n                    "Scan completed" if complete else "Scan ended without a completeness guarantee",\n                    source_id=self.source_id, details=completeness,\n                )\n        except asyncio.CancelledError:\n            activity.emit("WARNING", "Scan cancelled", source_id=self.source_id, job_id=self.job_id)\n            await db.transition_job(self.job_id, "cancelled", message="Crawl cancelled")\n            await db.record_job_diagnostic(self.job_id, "WARNING", "cancellation", "Scan cancelled", source_id=self.source_id)\n            raise\n        except Exception as exc:\n            safe_error = activity.redact(str(exc))\n            activity.emit("ERROR", "Scan failed", source_id=self.source_id, job_id=self.job_id, error=safe_error)\n            await db.increment_job(self.job_id, errors=1)\n            await db.transition_job(self.job_id, "failed", message=safe_error[:1000],\n                                    completeness_json=json.dumps({"complete": False, "reason": "failed"}))\n            await db.record_job_diagnostic(self.job_id, "ERROR", "failure", safe_error, source_id=self.source_id)\n            raise\n'''
)

replace_once(
    "app/crawler.py",
    '''    async def _process_safely(self, client, url):\n        activity.emit("INFO", "Fetching page", source_id=self.source_id, job_id=self.job_id, url=url)\n        try:\n            links = await self._process_url(client, url)\n            activity.emit("INFO", "Page processed", source_id=self.source_id, job_id=self.job_id, url=url, links=len(links))\n            return links\n        except Exception as exc:\n            safe_error = activity.redact(str(exc))\n            activity.emit("ERROR", "Page could not be processed", source_id=self.source_id, job_id=self.job_id, url=url, error=safe_error)\n            await db.increment_job(self.job_id, errors=1)\n            await db.upsert_page(self.source_id, url, last_error=safe_error[:1000])\n            if getattr(self.adapter, "fail_fast_access_errors", False) and (\n                "HTTP 401" in safe_error or "HTTP 403" in safe_error or "HTTP 429" in safe_error\n                or "access challenge" in safe_error.lower()\n            ):\n                raise\n            return []\n        finally:\n            self.processed += 1\n            await db.increment_job(self.job_id, pages_processed=1)\n''',
    '''    async def _process_safely(self, client, url, depth=0):\n        activity.emit("INFO", "Fetching page", source_id=self.source_id, job_id=self.job_id, url=url)\n        await db.frontier_state(self.job_id, url, "processing", increment_attempt=True)\n        try:\n            links = await self._process_url(client, url)\n            await db.frontier_state(self.job_id, url, "done", discovered_links=links)\n            await db.seed_frontier(self.job_id, self.source_id, links, depth + 1)\n            activity.emit("INFO", "Page processed", source_id=self.source_id, job_id=self.job_id, url=url, links=len(links))\n            return links\n        except Exception as exc:\n            safe_error = activity.redact(str(exc))\n            category = "page_error"\n            lowered = safe_error.lower()\n            if "http 401" in lowered or "http 403" in lowered or "access challenge" in lowered or "access denied" in lowered:\n                category = "access_block"\n            elif "http 429" in lowered:\n                category = "rate_limit"\n            elif "response exceeds" in lowered or "decoder" in lowered or "unrecognized" in lowered:\n                category = "malformed_response"\n            await db.frontier_state(self.job_id, url, "failed", last_error=safe_error[:1000])\n            await db.record_job_diagnostic(\n                self.job_id, "ERROR", category, safe_error, source_id=self.source_id, url=url,\n                retryable=False, details={"depth": depth},\n            )\n            activity.emit("ERROR", "Page could not be processed", source_id=self.source_id, job_id=self.job_id, url=url, error=safe_error)\n            await db.increment_job(self.job_id, errors=1)\n            await db.upsert_page(self.source_id, url, last_error=safe_error[:1000])\n            if getattr(self.adapter, "fail_fast_access_errors", False) and (\n                "HTTP 401" in safe_error or "HTTP 403" in safe_error or "HTTP 429" in safe_error\n                or "access challenge" in safe_error.lower()\n            ):\n                raise\n            return []\n        finally:\n            self.processed += 1\n            await db.increment_job(self.job_id, pages_processed=1)\n            await db.update_job(self.job_id, heartbeat_at=db.utcnow())\n'''
)

replace_once(
    "app/crawler.py",
    '''                    if result.status not in {429, 500, 502, 503, 504} or attempt == 3:\n                        break\n                    wait = min(2 ** attempt, 20)\n''',
    '''                    if result.status not in {429, 500, 502, 503, 504}:\n                        break\n                    if attempt == 3:\n                        await db.record_job_diagnostic(\n                            self.job_id, "ERROR", "retry_exhausted", f"HTTP {result.status} after bounded retries",\n                            source_id=self.source_id, url=url, retryable=True, attempt_no=attempt + 1,\n                        )\n                        break\n                    await db.record_retry(\n                        self.job_id, url, attempt + 1, f"HTTP {result.status}", source_id=self.source_id\n                    )\n                    wait = min(2 ** attempt, 20)\n'''
)

replace_once(
    "app/crawler.py",
    '''                except (httpx.TimeoutException, httpx.NetworkError):\n                    if attempt == 3:\n                        raise\n                    await asyncio.sleep(2 ** attempt)\n''',
    '''                except (httpx.TimeoutException, httpx.NetworkError) as exc:\n                    if attempt == 3:\n                        await db.record_job_diagnostic(\n                            self.job_id, "ERROR", "retry_exhausted", type(exc).__name__,\n                            source_id=self.source_id, url=url, retryable=True, attempt_no=attempt + 1,\n                        )\n                        raise\n                    await db.record_retry(\n                        self.job_id, url, attempt + 1, type(exc).__name__, source_id=self.source_id\n                    )\n                    await asyncio.sleep(2 ** attempt)\n'''
)

# ------------------------- main.py -------------------------
replace_once(
    "app/main.py",
    '''from . import bidder_master as bidder_master_db\n''',
    '''from . import bidder_master as bidder_master_db\nfrom . import backup as backup_tools\n'''
)

replace_once(
    "app/main.py",
    '''async def _launch_scan(source_id: int, force_full: bool = False, master_ids=None) -> dict:\n''',
    '''def _job_selection(job: dict):\n    try:\n        return json.loads(job.get("selection_json") or "null")\n    except (TypeError, ValueError):\n        return None\n\n\ndef _start_job_task(source: dict, job: dict) -> None:\n    job_id = int(job["id"])\n    engine = CrawlEngine(\n        source, job_id, force_full=bool(job.get("force_full")), master_ids=_job_selection(job)\n    )\n    task = asyncio.create_task(engine.run(), name=f"crawl-job-{job_id}")\n    TASKS[job_id] = task\n\n    def _done(_task: asyncio.Task):\n        TASKS.pop(job_id, None)\n        with suppress(asyncio.CancelledError, Exception):\n            _task.result()\n\n    task.add_done_callback(_done)\n\n\nasync def _launch_scan(source_id: int, force_full: bool = False, master_ids=None) -> dict:\n'''
)

replace_once(
    "app/main.py",
    '''    job_id = await db.create_job(source_id, force_full)\n    activity.emit("INFO", "Scan queued", source_id=source_id, job_id=job_id, mode="full" if force_full else "incremental")\n    engine = CrawlEngine(source, job_id, force_full=force_full, master_ids=master_ids)\n    task = asyncio.create_task(engine.run(), name=f"crawl-job-{job_id}")\n    TASKS[job_id] = task\n\n    def _done(_task: asyncio.Task):\n        TASKS.pop(job_id, None)\n        with suppress(asyncio.CancelledError, Exception):\n            _task.result()\n\n    task.add_done_callback(_done)\n    return await db.get_job(job_id)\n''',
    '''    job_id = await db.create_job(source_id, force_full, selection=master_ids)\n    activity.emit("INFO", "Scan queued", source_id=source_id, job_id=job_id, mode="full" if force_full else "incremental")\n    job = await db.get_job(job_id)\n    _start_job_task(source, job)\n    return job\n'''
)

replace_once(
    "app/main.py",
    '''        activity.emit("INFO", "Paralegal Database Tool started. Ready to maintain the master bidder database and collect public-record evidence.", version=activity.APP_VERSION)\n        await db.mark_interrupted_jobs()\n        SCHEDULER_TASK = asyncio.create_task(scheduler_loop(), name="source-scheduler")\n''',
    '''        activity.emit("INFO", "Paralegal Database Tool started. Ready to maintain the master bidder database and collect public-record evidence.", version=activity.APP_VERSION)\n        interrupted = await db.mark_interrupted_jobs()\n        for interrupted_job_id in interrupted:\n            recovery = await db.create_recovery_job(interrupted_job_id)\n            if not recovery:\n                continue\n            source = await db.get_source(recovery["source_id"])\n            if not source:\n                continue\n            activity.emit(\n                "WARNING", "Interrupted scan queued for a clean recovery attempt",\n                source_id=source["id"], job_id=recovery["id"], parent_job_id=interrupted_job_id,\n                attempt_no=recovery.get("attempt_no"),\n            )\n            _start_job_task(source, recovery)\n        SCHEDULER_TASK = asyncio.create_task(scheduler_loop(), name="source-scheduler")\n'''
)

replace_once(
    "app/main.py",
    '''@app.get("/api/stats")\nasync def get_stats():\n    return await db.stats()\n''',
    '''@app.get("/api/stats")\nasync def get_stats():\n    return await db.stats()\n\n\n@app.get("/api/maintenance/integrity")\nasync def maintenance_integrity(full: bool = False):\n    return await db.integrity_check(full=full)\n\n\n@app.get("/api/maintenance/backup")\nasync def maintenance_backup():\n    archive, manifest = await asyncio.to_thread(backup_tools.create_backup_bytes)\n    filename = f"paralegal-research-desk-backup-{manifest['created_at'].replace(':', '').replace('+00:00', 'Z')}.zip"\n    activity.emit("INFO", "Operator-safe database backup created", schema_version=manifest["schema_version"])\n    return StreamingResponse(\n        io.BytesIO(archive), media_type="application/zip",\n        headers={"Content-Disposition": f'attachment; filename="{filename}"'},\n    )\n'''
)

replace_once(
    "app/main.py",
    '''@app.get("/api/jobs/{job_id}")\nasync def job(job_id: int):\n    result = await db.get_job(job_id)\n    if not result:\n        raise HTTPException(status_code=404, detail="Job not found")\n    return result\n''',
    '''@app.get("/api/jobs/{job_id}")\nasync def job(job_id: int):\n    result = await db.get_job(job_id)\n    if not result:\n        raise HTTPException(status_code=404, detail="Job not found")\n    return result\n\n\n@app.get("/api/jobs/{job_id}/diagnostics")\nasync def job_diagnostics(job_id: int, limit: int = Query(default=1000, ge=1, le=5000)):\n    result = await db.get_job(job_id)\n    if not result:\n        raise HTTPException(status_code=404, detail="Job not found")\n    return {\n        "job": result,\n        "diagnostics": await db.job_diagnostics(job_id, limit),\n        "frontier": await db.frontier_summary(job_id),\n    }\n'''
)

# ------------------------- backup module and scripts -------------------------
write("app/backup.py", textwrap.dedent(r'''\
    """Crash-consistent SQLite backup and verified restore helpers.

    API credentials live outside crawler.db and are intentionally excluded.
    """
    from __future__ import annotations

    import hashlib
    import io
    import json
    import os
    import shutil
    import sqlite3
    import tempfile
    import zipfile
    from datetime import datetime, timezone
    from pathlib import Path

    from . import database as db

    DATABASE_MEMBER = "workspace/crawler.db"
    MANIFEST_MEMBER = "manifest.json"


    def _utcnow() -> str:
        return datetime.now(timezone.utc).isoformat()


    def _open_sqlite(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn


    def _validate_database(path: Path) -> dict:
        with _open_sqlite(path) as conn:
            integrity = [row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()]
            foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            tables = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            required = {"bidder_master", "records", "record_history", "bidder_master_history", "bidder_proposals", "activity_events", "crawl_jobs"}
            missing = sorted(required - tables)
            if integrity != ["ok"] or foreign_keys or missing:
                raise ValueError(
                    f"Backup database validation failed: integrity={integrity}, foreign_keys={foreign_keys}, missing_tables={missing}"
                )
            counts = {}
            for table in sorted(required):
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            return {"schema_version": version, "counts": counts}


    def _snapshot_database(source_path: Path, destination_path: Path) -> None:
        source = _open_sqlite(source_path)
        destination = _open_sqlite(destination_path)
        try:
            source.backup(destination, pages=256, sleep=0.01)
            destination.commit()
        finally:
            destination.close()
            source.close()


    def create_backup_bytes(source_path: str | os.PathLike | None = None) -> tuple[bytes, dict]:
        source = Path(source_path or db.DB_PATH)
        if not source.exists():
            raise FileNotFoundError(f"Database not found: {source}")
        with tempfile.TemporaryDirectory(prefix="paralegal-backup-") as temp_dir:
            snapshot = Path(temp_dir) / "crawler.db"
            _snapshot_database(source, snapshot)
            validation = _validate_database(snapshot)
            payload = snapshot.read_bytes()
            manifest = {
                "format": 1,
                "application": "Paralegal Research Desk",
                "created_at": _utcnow(),
                "schema_version": validation["schema_version"],
                "counts": validation["counts"],
                "database_member": DATABASE_MEMBER,
                "database_sha256": hashlib.sha256(payload).hexdigest(),
                "credentials_included": False,
                "notes": "Contains approved master data, research evidence, history, proposals, jobs, diagnostics and activity stored in SQLite. API credentials are intentionally excluded.",
            }
            output = io.BytesIO()
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(MANIFEST_MEMBER, json.dumps(manifest, indent=2, sort_keys=True))
                archive.writestr(DATABASE_MEMBER, payload)
                archive.writestr(
                    "RESTORE.txt",
                    "Stop the application before replacing an existing workspace. Use scripts/restore-workspace.py for a verified restore. API credentials are not part of this backup.\n",
                )
            return output.getvalue(), manifest


    def create_backup_file(output_path: str | os.PathLike, source_path: str | os.PathLike | None = None) -> dict:
        archive, manifest = create_backup_bytes(source_path)
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_suffix(output.suffix + ".tmp")
        temp.write_bytes(archive)
        os.replace(temp, output)
        return manifest


    def restore_backup(archive_path: str | os.PathLike, target_path: str | os.PathLike | None = None, *, overwrite: bool = False) -> dict:
        archive_path = Path(archive_path)
        target = Path(target_path or db.DB_PATH)
        if target.exists() and not overwrite:
            raise FileExistsError("Target database already exists; restore into a clean workspace or pass overwrite=True with the application stopped")
        with zipfile.ZipFile(archive_path, "r") as archive:
            names = set(archive.namelist())
            if MANIFEST_MEMBER not in names or DATABASE_MEMBER not in names:
                raise ValueError("Not a Paralegal Research Desk backup archive")
            manifest = json.loads(archive.read(MANIFEST_MEMBER).decode("utf-8"))
            payload = archive.read(DATABASE_MEMBER)
        digest = hashlib.sha256(payload).hexdigest()
        if digest != manifest.get("database_sha256"):
            raise ValueError("Backup database checksum does not match manifest")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="paralegal-restore-") as temp_dir:
            extracted = Path(temp_dir) / "crawler.db"
            extracted.write_bytes(payload)
            validation = _validate_database(extracted)
            staged = target.with_suffix(target.suffix + ".restore-tmp")
            shutil.copy2(extracted, staged)
            _validate_database(staged)
            os.replace(staged, target)
        return {**manifest, "restored_to": str(target), "validated_schema_version": validation["schema_version"]}
'''))

write("scripts/backup-workspace.py", textwrap.dedent(r'''\
    import argparse
    from datetime import datetime
    from pathlib import Path

    from app import backup

    parser = argparse.ArgumentParser(description="Create a crash-consistent Paralegal Research Desk backup")
    parser.add_argument("output", nargs="?", help="ZIP path; defaults to data/backups/<timestamp>.zip")
    args = parser.parse_args()
    output = Path(args.output) if args.output else Path("data/backups") / f"paralegal-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    manifest = backup.create_backup_file(output)
    print(f"Backup created: {output}")
    print(f"Schema version: {manifest['schema_version']}; credentials included: {manifest['credentials_included']}")
'''))

write("scripts/restore-workspace.py", textwrap.dedent(r'''\
    import argparse
    from pathlib import Path

    from app import backup

    parser = argparse.ArgumentParser(description="Verify and restore a Paralegal Research Desk backup")
    parser.add_argument("archive", help="Backup ZIP created by backup-workspace.py or the maintenance API")
    parser.add_argument("--target", help="Destination SQLite path; defaults to the configured workspace database")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing database. Stop the application first.")
    args = parser.parse_args()
    result = backup.restore_backup(Path(args.archive), args.target, overwrite=args.overwrite)
    print(f"Restore complete: {result['restored_to']}")
    print(f"Validated schema version: {result['validated_schema_version']}")
'''))

# ------------------------- tests -------------------------
write("tests/test_reliability_hardening.py", textwrap.dedent(r'''\
    import asyncio
    import json
    import sqlite3

    import httpx
    import pytest

    from app import backup, bidder_master as bm, database as db
    from app.crawler import CrawlEngine
    from app.bidder_schema import BIDDER_COLUMNS


    async def _no_sleep(*_args, **_kwargs):
        return None


    async def test_hard_interruption_is_durable_and_recoverable(database, source):
        job_id = await db.create_job(source["id"], False, selection=[11, 12])
        await db.transition_job(job_id, "running", message="working")
        interrupted = await db.mark_interrupted_jobs()
        assert interrupted == [job_id]
        parent = await db.get_job(job_id)
        assert parent["status"] == "interrupted"
        assert parent["interrupted_at"]
        diagnostics = await db.job_diagnostics(job_id)
        assert diagnostics[-1]["category"] == "interruption"

        child = await db.create_recovery_job(job_id)
        assert child["status"] == "queued"
        assert child["parent_job_id"] == job_id
        assert child["recovery_mode"] == "restart"
        assert child["attempt_no"] == 2
        assert json.loads(child["selection_json"]) == [11, 12]
        assert (await db.create_recovery_job(job_id))["id"] == child["id"]


    async def test_state_machine_rejects_terminal_rewrite(database, source):
        job_id = await db.create_job(source["id"], False)
        await db.transition_job(job_id, "running")
        await db.transition_job(job_id, "cancelled", message="operator cancelled")
        with pytest.raises(ValueError, match="Invalid job transition"):
            await db.transition_job(job_id, "completed")
        assert (await db.get_job(job_id))["status"] == "cancelled"


    async def test_retryable_failure_records_attempt_and_then_succeeds(database, source, monkeypatch):
        monkeypatch.setattr("app.crawler.asyncio.sleep", _no_sleep)
        job_id = await db.create_job(source["id"], False)
        engine = CrawlEngine(source, job_id)
        calls = 0

        def respond(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(503, text="temporary", headers={"content-type": "text/plain"})
            return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=False) as client:
            result = await engine._http_fetch_with_retry(client, source["start_url"], {})
        await engine.renderer.close()
        assert result.status == 200
        assert (await db.get_job(job_id))["retry_count"] == 1
        assert any(item["category"] == "retry" for item in await db.job_diagnostics(job_id))


    async def test_non_retryable_403_is_not_retried(database, source, monkeypatch):
        monkeypatch.setattr("app.crawler.asyncio.sleep", _no_sleep)
        job_id = await db.create_job(source["id"], False)
        engine = CrawlEngine(source, job_id)
        calls = 0

        def respond(request):
            nonlocal calls
            calls += 1
            return httpx.Response(403, text="denied", headers={"content-type": "text/plain"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=False) as client:
            result = await engine._http_fetch_with_retry(client, source["start_url"], {})
        await engine.renderer.close()
        assert result.status == 403
        assert calls == 1
        assert (await db.get_job(job_id))["retry_count"] == 0


    async def test_exhausted_transient_retries_are_durable(database, source, monkeypatch):
        monkeypatch.setattr("app.crawler.asyncio.sleep", _no_sleep)
        job_id = await db.create_job(source["id"], False)
        engine = CrawlEngine(source, job_id)
        calls = 0

        def respond(request):
            nonlocal calls
            calls += 1
            return httpx.Response(503, text="temporary", headers={"content-type": "text/plain"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=False) as client:
            result = await engine._http_fetch_with_retry(client, source["start_url"], {})
        await engine.renderer.close()
        assert result.status == 503
        assert calls == 4
        assert (await db.get_job(job_id))["retry_count"] == 3
        diagnostics = await db.job_diagnostics(job_id)
        assert diagnostics[-1]["category"] == "retry_exhausted"


    async def test_partial_completion_and_frontier_are_persisted(database, source):
        job_id = await db.create_job(source["id"], False)
        await db.transition_job(job_id, "running")
        await db.seed_frontier(job_id, source["id"], ["https://fixture.test/a", "https://fixture.test/b"], 0)
        await db.frontier_state(job_id, "https://fixture.test/a", "done", discovered_links=[])
        await db.frontier_state(job_id, "https://fixture.test/b", "failed", last_error="HTTP 503")
        await db.transition_job(job_id, "partial", completeness_json=json.dumps({"complete": False}))
        assert (await db.get_job(job_id))["status"] == "partial"
        summary = await db.frontier_summary(job_id)
        assert summary["states"] == {"done": 1, "failed": 1}


    def test_migration_failure_rolls_back(tmp_path):
        path = tmp_path / "migration.db"
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA user_version=4")

        def failing(connection):
            connection.execute("CREATE TABLE should_rollback(id INTEGER)")
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            db._run_migration(conn, 5, failing)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='should_rollback'").fetchone() is None
        conn.close()


    async def test_integrity_and_backup_restore_clean_workspace(database, source, tmp_path):
        await db.upsert_record(source["id"], {
            "external_id": "backup-evidence", "company": "Backup Electric", "source_url": source["start_url"]
        })
        result = await db.integrity_check(full=True)
        assert result["ok"] is True

        archive_bytes, manifest = await asyncio.to_thread(backup.create_backup_bytes, db.DB_PATH)
        assert manifest["credentials_included"] is False
        archive_path = tmp_path / "backup.zip"
        archive_path.write_bytes(archive_bytes)
        restored = tmp_path / "clean" / "crawler.db"
        restore = await asyncio.to_thread(backup.restore_backup, archive_path, restored)
        assert restore["validated_schema_version"] >= 4
        with sqlite3.connect(restored) as conn:
            assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


    def _baseline_row():
        row = {column: "" for column in BIDDER_COLUMNS}
        row.update({
            "id": "1001", "contractor_name": "Restart Safe LLC", "address_1": "1 Main St",
            "city": "Madison", "state": "WI", "zip": "53703", "osha": "N",
        })
        return row


    async def test_approved_update_is_not_duplicated_after_reinitialization(database, source):
        await db.update_source(source["id"], {"start_url": "https://apiprod.dol.gov/v4/get/OSHA/inspection/json"})
        await bm.import_rows("master.csv", [_baseline_row()], [])
        await db.upsert_record(source["id"], {
            "external_id": "ev-1", "company": "Restart Safe LLC", "osha": "Y",
            "source_url": "https://apiprod.dol.gov/v4/get/OSHA/inspection/json",
            "extra": {"complete_aggregate": True},
        })
        await bm.compare()
        proposal = (await bm.list_proposals())[0]
        await bm.apply(proposal["id"])
        master = (await bm.search())["items"][0]
        assert len(await bm.history(master["_master_id"])) == 1
        await db.init_db()  # simulate reopening the same workspace after restart
        second = await bm.apply(proposal["id"])
        assert second["status"] == "applied"
        assert len(await bm.history(master["_master_id"])) == 1
'''))

write("tests/test_scale_reliability.py", textwrap.dedent(r'''\
    import json

    from app import bidder_master as bm, database as db


    async def test_large_evidence_history_activity_pagination(database, source):
        now = db.utcnow()
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for index in range(2500):
                conn.execute(
                    """INSERT INTO bidder_master(bidder_id,contractor_name,contractor_norm,address_norm,created_at,updated_at)
                       VALUES (?,?,?,?,?,?)""",
                    (str(100000 + index), f"Generated Contractor {index:05d}", f"generated contractor {index:05d}", "", now, now),
                )
            for index in range(5000):
                cur = conn.execute(
                    """INSERT INTO records(source_id,entity_key,content_hash,name,company,source_url,extra_json,first_seen,last_seen,last_changed,active)
                       VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
                    (source["id"], f"key-{index}", f"hash-{index}", "", f"Generated Evidence {index:05d}", source["start_url"], "{}", now, now, now),
                )
                record_id = int(cur.lastrowid)
                conn.execute(
                    "INSERT INTO record_history(record_id,changed_at,old_json,new_json) VALUES (?,?,NULL,?)",
                    (record_id, now, json.dumps({"company": f"Generated Evidence {index:05d}"})),
                )
            for index in range(5000):
                conn.execute(
                    "INSERT INTO activity_events(created_at,level,component,message,details_json,source_id) VALUES (?,?,?,?,?,?)",
                    (now, "INFO", "scale", f"event-{index}", "{}", source["id"]),
                )
            conn.commit()

        evidence = await db.search_records(source_id=source["id"], limit=100, offset=4900)
        assert evidence["total"] == 5000
        assert len(evidence["items"]) == 100
        bidders = await bm.search(q="Generated Contractor 024", limit=50, offset=0)
        assert bidders["total"] >= 50
        history = await db.record_history(evidence["items"][0]["id"])
        assert len(history) == 1
        events = await db.list_events(limit=300)
        assert len(events["items"]) == 300

        with db.connect() as conn:
            history_plan = " ".join(row[3] for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM record_history WHERE record_id=? ORDER BY id DESC", (evidence["items"][0]["id"],)
            ))
            assert "idx_record_history_record" in history_plan
'''))

write("benchmarks/reliability_scale.py", textwrap.dedent(r'''\
    """Generated-data benchmark for local-office scale. Not part of normal CI."""
    import argparse
    import sqlite3
    import tempfile
    import time
    from pathlib import Path

    from app import database as db


    def main():
        parser = argparse.ArgumentParser()
        parser.add_argument("--bidders", type=int, default=25000)
        parser.add_argument("--evidence", type=int, default=100000)
        parser.add_argument("--history", type=int, default=200000)
        parser.add_argument("--activity", type=int, default=50000)
        args = parser.parse_args()
        with tempfile.TemporaryDirectory(prefix="paralegal-scale-") as temp:
            path = Path(temp) / "benchmark.db"
            original = db.DB_PATH
            db.DB_PATH = path
            import asyncio
            asyncio.run(db.init_db())
            now = db.utcnow()
            with db.connect() as conn:
                source_id = conn.execute(
                    "INSERT INTO sources(name,start_url,created_at) VALUES ('Benchmark','https://benchmark.test/',?)", (now,)
                ).lastrowid
                started = time.perf_counter()
                conn.execute("BEGIN IMMEDIATE")
                for i in range(args.bidders):
                    conn.execute(
                        "INSERT INTO bidder_master(bidder_id,contractor_name,contractor_norm,address_norm,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                        (str(i + 1), f"Contractor {i}", f"contractor {i}", "", now, now),
                    )
                record_ids = []
                for i in range(args.evidence):
                    cur = conn.execute(
                        "INSERT INTO records(source_id,entity_key,content_hash,company,source_url,extra_json,first_seen,last_seen,last_changed,active) VALUES (?,?,?,?,?,?,?,?,?,1)",
                        (source_id, f"key-{i}", f"hash-{i}", f"Evidence {i}", "https://benchmark.test/", "{}", now, now, now),
                    )
                    record_ids.append(cur.lastrowid)
                for i in range(args.history):
                    rid = record_ids[i % len(record_ids)]
                    conn.execute("INSERT INTO record_history(record_id,changed_at,new_json) VALUES (?,?,?)", (rid, now, "{}"))
                for i in range(args.activity):
                    conn.execute("INSERT INTO activity_events(created_at,level,component,message,details_json) VALUES (?,?,?,?,?)", (now, "INFO", "benchmark", f"event-{i}", "{}"))
                conn.commit()
                load_seconds = time.perf_counter() - started
            async def queries():
                start = time.perf_counter()
                page = await db.search_records(source_id=source_id, limit=100, offset=max(0, args.evidence - 100))
                elapsed = time.perf_counter() - start
                return page["total"], elapsed
            total, query_seconds = asyncio.run(queries())
            print({"db": str(path), "load_seconds": round(load_seconds, 3), "evidence_total": total, "paged_query_seconds": round(query_seconds, 4)})
            db.DB_PATH = original


    if __name__ == "__main__":
        main()
'''))

# Remove temporary patch plumbing from the final branch. The workflow remains
# loaded for this run even after the files are deleted from the commit.
for transient in (ROOT / "scripts/apply_reliability_hardening.py", ROOT / ".github/workflows/reliability-patch.yml"):
    if transient.exists():
        transient.unlink()

print("Reliability hardening patch applied")
