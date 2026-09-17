\
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
