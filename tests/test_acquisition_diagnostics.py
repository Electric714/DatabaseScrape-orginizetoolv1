import json
import zipfile
from io import BytesIO

import httpx
import pytest

from app import activity, database as db
from app.diagnostics import (
    AcquisitionFailure, AcquisitionStage, AcquisitionStatus, RetryClass,
    classify_failure, sanitize,
)


def test_typed_failure_classification_propagates_stage_status_and_retry():
    rate = classify_failure(ValueError("HTTP 429"), AcquisitionStage.DOWNLOAD)
    assert isinstance(rate, AcquisitionFailure)
    assert rate.code == "rate_limited"
    assert rate.stage is AcquisitionStage.DOWNLOAD
    assert rate.status is AcquisitionStatus.INCOMPLETE
    assert rate.retry is RetryClass.RATE_LIMITED
    assert rate.retryable

    blocked = classify_failure(ValueError("HTTP 403"), AcquisitionStage.DISCOVERY)
    assert blocked.status is AcquisitionStatus.BLOCKED
    assert blocked.retry is RetryClass.AFTER_SOURCE_CHANGE
    assert not blocked.retryable

    transient = classify_failure(httpx.ReadTimeout("late"), AcquisitionStage.DOWNLOAD)
    assert transient.retry is RetryClass.TRANSIENT


def test_recursive_sanitization_removes_nested_secrets_and_source_bodies():
    value = sanitize({
        "request": {"Authorization": "Bearer abc", "query": "api_key=abc"},
        "response": "private source body",
        "items": [{"cookie": "session=abc"}],
        "url": "https://example.test/path?token=abc",
    })
    encoded = json.dumps(value)
    assert "abc" not in encoded
    assert "private source body" not in encoded
    assert "?" not in value["url"]


@pytest.mark.asyncio
async def test_persisted_diagnostics_and_export_are_redacted(database):
    source = await db.create_source({"name": "Diagnostic fixture", "start_url": "https://example.test/", "auto_scan": False,
        "interval_minutes": 60, "max_pages": 1, "max_depth": 0, "concurrency": 1, "delay_ms": 0,
        "render_mode": "http", "respect_robots": False})
    job_id = await db.create_job(source["id"], False)
    await db.record_job_diagnostic(job_id, "ERROR", "download", "api_key=super-secret",
        details={"payload": "sensitive body", "nested": {"cookie": "super-secret"}})
    item = (await db.job_diagnostics(job_id))[0]
    assert "super-secret" not in json.dumps(item)
    assert "sensitive body" not in json.dumps(item)

    activity.emit("ERROR", "token=super-secret", job_id=job_id,
                  response="sensitive body", nested={"password": "super-secret"})
    await db.stats()
    archive = await activity.bundle()
    with zipfile.ZipFile(BytesIO(archive)) as bundle:
        contents = b"".join(bundle.read(name) for name in bundle.namelist())
    assert b"super-secret" not in contents
    assert b"sensitive body" not in contents
