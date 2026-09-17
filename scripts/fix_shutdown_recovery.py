from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path, old, new):
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: expected one match for shutdown recovery patch, found {text.count(old)}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "app/database.py",
    '''        conn.commit()\n        return [int(row["id"]) for row in rows]\n\n\ndef _sync_create_source''',
    '''        recoverable = conn.execute(\n            """SELECT j.id FROM crawl_jobs j\n               WHERE j.status='interrupted' AND COALESCE(j.attempt_no,1) < 3\n                 AND NOT EXISTS (SELECT 1 FROM crawl_jobs child WHERE child.parent_job_id=j.id)\n               ORDER BY j.id"""\n        ).fetchall()\n        conn.commit()\n        return [int(row["id"]) for row in recoverable]\n\n\ndef _sync_create_source'''
)

replace_once(
    "app/crawler.py",
    '''        except asyncio.CancelledError:\n            activity.emit("WARNING", "Scan cancelled", source_id=self.source_id, job_id=self.job_id)\n            await db.transition_job(self.job_id, "cancelled", message="Crawl cancelled")\n            await db.record_job_diagnostic(self.job_id, "WARNING", "cancellation", "Scan cancelled", source_id=self.source_id)\n            raise\n''',
    '''        except asyncio.CancelledError as exc:\n            shutdown_interruption = bool(exc.args and exc.args[0] == "application_shutdown")\n            if shutdown_interruption:\n                reason = "Application shutdown interrupted the scan before completion"\n                activity.emit("WARNING", reason, source_id=self.source_id, job_id=self.job_id)\n                await db.transition_job(\n                    self.job_id, "interrupted", message=reason, restart_reason=reason,\n                    completeness_json=json.dumps({"complete": False, "reason": "application_shutdown"}),\n                )\n                await db.record_job_diagnostic(\n                    self.job_id, "WARNING", "interruption", reason, source_id=self.source_id,\n                    details={"automatic_recovery_eligible": True},\n                )\n            else:\n                activity.emit("WARNING", "Scan cancelled", source_id=self.source_id, job_id=self.job_id)\n                await db.transition_job(self.job_id, "cancelled", message="Crawl cancelled")\n                await db.record_job_diagnostic(\n                    self.job_id, "WARNING", "cancellation", "Scan cancelled", source_id=self.source_id,\n                    details={"automatic_recovery_eligible": False},\n                )\n            raise\n'''
)

replace_once(
    "app/main.py",
    '''            tasks = list(TASKS.values())\n            for task in tasks:\n                task.cancel()\n            await asyncio.gather(*tasks, return_exceptions=True)\n''',
    '''            tasks = list(TASKS.values())\n            for task in tasks:\n                task.cancel("application_shutdown")\n            await asyncio.gather(*tasks, return_exceptions=True)\n'''
)

for script in ("scripts/backup-workspace.py", "scripts/restore-workspace.py", "benchmarks/reliability_scale.py"):
    target = ROOT / script
    text = target.read_text(encoding="utf-8")
    if "sys.path.insert" not in text:
        if script.startswith("benchmarks/"):
            marker = "from pathlib import Path\n\nfrom app import database as db\n"
            replacement = "from pathlib import Path\nimport sys\n\nROOT = Path(__file__).resolve().parents[1]\nsys.path.insert(0, str(ROOT))\n\nfrom app import database as db\n"
        else:
            marker = "from pathlib import Path\n\nfrom app import backup\n"
            replacement = "from pathlib import Path\nimport sys\n\nROOT = Path(__file__).resolve().parents[1]\nsys.path.insert(0, str(ROOT))\n\nfrom app import backup\n"
        if marker not in text:
            raise RuntimeError(f"{script}: import marker not found")
        target.write_text(text.replace(marker, replacement, 1), encoding="utf-8")

# Extend recovery coverage to interrupted jobs that were already persisted before startup.
test_path = ROOT / "tests/test_reliability_hardening.py"
tests = test_path.read_text(encoding="utf-8")
addition = '''\n\nasync def test_persisted_interruption_is_selected_for_startup_recovery(database, source):\n    job_id = await db.create_job(source["id"], False)\n    await db.transition_job(job_id, "running")\n    await db.transition_job(\n        job_id, "interrupted", message="Application shutdown interrupted the scan before completion",\n        restart_reason="Application shutdown interrupted the scan before completion",\n    )\n    recoverable = await db.mark_interrupted_jobs()\n    assert job_id in recoverable\n    child = await db.create_recovery_job(job_id)\n    assert child["parent_job_id"] == job_id\n    assert child["recovery_mode"] == "restart"\n    assert child["status"] == "queued"\n\n\nasync def test_explicit_cancellation_is_not_selected_for_startup_recovery(database, source):\n    job_id = await db.create_job(source["id"], False)\n    await db.transition_job(job_id, "running")\n    await db.transition_job(job_id, "cancelled", message="operator cancelled")\n    recoverable = await db.mark_interrupted_jobs()\n    assert job_id not in recoverable\n'''
if "test_persisted_interruption_is_selected_for_startup_recovery" not in tests:
    test_path.write_text(tests + addition, encoding="utf-8")

for transient in [
    ROOT / "scripts/fix_shutdown_recovery.py",
    ROOT / ".github/workflows/shutdown-recovery-fix.yml",
]:
    if transient.exists():
        transient.unlink()

print("graceful shutdown recovery hardening applied")
