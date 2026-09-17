\
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
