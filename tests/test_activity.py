import io
import json
import logging
import zipfile
from contextlib import closing

from app import activity, database as db


async def test_activity_persists_and_pages(database):
    activity.emit("INFO", "First event", job_id=12)
    activity.emit("ERROR", "Something failed", source_id=3, error="a useful explanation")
    result = await db.list_events()
    assert [e["level"] for e in result["items"]] == ["INFO", "ERROR"]
    first = result["items"][0]["id"]
    assert (await db.list_events(after=first))["items"][0]["message"] == "Something failed"
    await db.init_db()
    assert len((await db.list_events())["items"]) == 2


async def test_log_redaction_and_bundle_excludes_records(source):
    activity.emit("ERROR", "Authorization: Bearer very-secret https://user:pass@site.test/path?token=hidden",
                  token="also-secret", url="https://site.test/path?name=private")
    await db.upsert_record(source["id"], {"name": "DO-NOT-EXPORT-THIS-PERSON", "phone": "6085551234", "source_url":"https://fixture.test/p"})
    content = await activity.bundle()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert set(archive.namelist()) == {"summary.json", "activity.json", "activity.txt", "READ-ME.txt"}
        combined = "\n".join(archive.read(n).decode() for n in archive.namelist())
        assert all(secret not in combined for secret in ["very-secret", "also-secret", "token=hidden", "user:pass", "name=private", "DO-NOT-EXPORT-THIS-PERSON"])
        assert "[redacted]" in combined


def test_redaction_preserves_provenance_but_removes_acquisition_secrets():
    raw = (
        "Authorization: Basic abc123 COOKIE=session-value API-KEY=MixedCaseSecret "
        "https://sam.gov/download/archive.zip?X-Amz-Signature=signed-secret&apiKey=query-secret "
        "https://challenge.test/cdn-cgi/challenge?token=browser-secret "
        "/Users/alice/private/report.txt"
    )
    clean = activity.redact(raw)
    assert "https://sam.gov/download/archive.zip" in clean
    assert "https://challenge.test/cdn-cgi/challenge" in clean
    assert "/home/[user]/private/report.txt" in clean
    for secret in ("abc123", "session-value", "MixedCaseSecret", "signed-secret", "query-secret", "browser-secret", "alice"):
        assert secret not in clean


async def test_retention_bound(database):
    with closing(db.connect()) as conn:
        conn.executemany("INSERT INTO activity_events(created_at,level,component,message) VALUES ('now','INFO','test','old')", [()] * 5001)
        conn.commit()
    activity.emit("INFO", "Latest")
    events = await db.list_events(limit=6000)
    assert len(events["items"]) == 5000
    assert events["items"][-1]["message"] == "Latest"


async def test_standard_python_exception_is_saved(database):
    activity.install()
    try:
        raise ValueError("fixture failure")
    except ValueError:
        logging.getLogger("app.test").exception("Operation failed")
    events = await db.list_events()
    assert "ValueError: fixture failure" in events["items"][-1]["message"]
