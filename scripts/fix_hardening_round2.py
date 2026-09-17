from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]

GENERATED = [
    "app/backup.py",
    "scripts/backup-workspace.py",
    "scripts/restore-workspace.py",
    "tests/test_reliability_hardening.py",
    "tests/test_scale_reliability.py",
    "benchmarks/reliability_scale.py",
]

for name in GENERATED:
    path = ROOT / name
    text = path.read_text(encoding="utf-8")
    if text.startswith("\\\n"):
        text = text[2:]
    text = textwrap.dedent(text)
    path.write_text(text, encoding="utf-8")

path = ROOT / "app/database.py"
text = path.read_text(encoding="utf-8")
start = text.index("def _migrate_v4(conn: sqlite3.Connection) -> None:\n")
end = text.index("\n\ndef connect() -> sqlite3.Connection:\n", start)
replacement = '''def _migrate_v4(conn: sqlite3.Connection) -> None:\n    columns = _table_columns(conn, "crawl_jobs")\n    additions = {\n        "selection_json": "TEXT NOT NULL DEFAULT 'null'",\n        "parent_job_id": "INTEGER REFERENCES crawl_jobs(id)",\n        "recovery_mode": "TEXT NOT NULL DEFAULT 'fresh'",\n        "restart_reason": "TEXT",\n        "attempt_no": "INTEGER NOT NULL DEFAULT 1",\n        "retry_count": "INTEGER NOT NULL DEFAULT 0",\n        "heartbeat_at": "TEXT",\n        "interrupted_at": "TEXT",\n        "acquisition_limits_json": "TEXT NOT NULL DEFAULT '{}'",\n        "completeness_json": "TEXT NOT NULL DEFAULT '{}'",\n    }\n    for name, declaration in additions.items():\n        if name not in columns:\n            conn.execute(f"ALTER TABLE crawl_jobs ADD COLUMN {name} {declaration}")\n\n    history_columns = _table_columns(conn, "bidder_master_history")\n    if "proposal_id" not in history_columns:\n        conn.execute("ALTER TABLE bidder_master_history ADD COLUMN proposal_id INTEGER")\n\n    statements = (\n        """CREATE TABLE IF NOT EXISTS crawl_job_diagnostics (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            job_id INTEGER NOT NULL REFERENCES crawl_jobs(id) ON DELETE CASCADE,\n            source_id INTEGER,\n            created_at TEXT NOT NULL,\n            level TEXT NOT NULL,\n            category TEXT NOT NULL,\n            message TEXT NOT NULL,\n            url TEXT,\n            retryable INTEGER NOT NULL DEFAULT 0,\n            attempt_no INTEGER,\n            details_json TEXT NOT NULL DEFAULT '{}'\n        )""",\n        """CREATE TABLE IF NOT EXISTS crawl_frontier (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            job_id INTEGER NOT NULL REFERENCES crawl_jobs(id) ON DELETE CASCADE,\n            source_id INTEGER NOT NULL,\n            url TEXT NOT NULL,\n            depth INTEGER NOT NULL DEFAULT 0,\n            state TEXT NOT NULL DEFAULT 'queued',\n            attempts INTEGER NOT NULL DEFAULT 0,\n            last_error TEXT,\n            discovered_links_json TEXT NOT NULL DEFAULT '[]',\n            updated_at TEXT NOT NULL,\n            UNIQUE(job_id, url)\n        )""",\n        "CREATE INDEX IF NOT EXISTS idx_jobs_status_id ON crawl_jobs(status, id DESC)",\n        "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_recovery_parent ON crawl_jobs(parent_job_id) WHERE parent_job_id IS NOT NULL",\n        "CREATE INDEX IF NOT EXISTS idx_job_diagnostics_job ON crawl_job_diagnostics(job_id, id)",\n        "CREATE INDEX IF NOT EXISTS idx_job_diagnostics_source ON crawl_job_diagnostics(source_id, id DESC)",\n        "CREATE INDEX IF NOT EXISTS idx_frontier_job_state ON crawl_frontier(job_id, state, depth, id)",\n        "CREATE INDEX IF NOT EXISTS idx_record_history_record ON record_history(record_id, id DESC)",\n        "CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_events(created_at, id)",\n        "CREATE INDEX IF NOT EXISTS idx_activity_job ON activity_events(job_id, id)",\n        "CREATE INDEX IF NOT EXISTS idx_records_active_changed ON records(source_id, active, last_changed DESC, id DESC)",\n        "CREATE INDEX IF NOT EXISTS idx_proposals_signature_status ON bidder_proposals(signature, status, id DESC)",\n        "CREATE UNIQUE INDEX IF NOT EXISTS idx_bidder_history_proposal ON bidder_master_history(proposal_id) WHERE proposal_id IS NOT NULL",\n    )\n    for statement in statements:\n        conn.execute(statement)\n'''
text = text[:start] + replacement + text[end:]
path.write_text(text, encoding="utf-8")

for transient in [
    ROOT / "scripts/fix_hardening_round2.py",
    ROOT / "scripts/fix_reliability_patch.py",
    ROOT / ".github/workflows/hardening-fix.yml",
]:
    if transient.exists():
        transient.unlink()

print("hardening output fixes applied")
