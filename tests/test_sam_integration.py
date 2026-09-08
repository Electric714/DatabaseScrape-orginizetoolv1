import json
from urllib.parse import parse_qs, urlsplit

import httpx

from app import bidder_master as bidder_db, database as db, main
from app.bidder_schema import BIDDER_COLUMNS, bidder_row
from app.crawler import CrawlEngine
from app.models import SourceCreate, SourceUpdate
from app.sam_adapter import (
    SAM_EXCLUSIONS_ENDPOINT,
    SAM_EXCLUSIONS_PATH,
    SAM_MASTER_FIELDS,
    SamExclusionsAdapter,
)


SAM_MATCH = {
    "totalRecords": 1,
    "excludedEntity": [{
        "exclusionDetails": {
            "classificationType": "Firm",
            "exclusionType": "Ineligible (Proceedings Completed)",
            "exclusionProgram": "Reciprocal",
            "excludingAgencyCode": "GSA",
            "excludingAgencyName": "GENERAL SERVICES ADMINISTRATION",
        },
        "exclusionIdentification": {
            "ueiSAM": "ABCDEF123456",
            "cageCode": "1A2B3",
            "entityName": "Example Builders LLC",
        },
        "exclusionActions": {
            "listOfActions": [{
                "createDate": "01-02-2025",
                "updateDate": "04-05-2026",
                "activateDate": "01-02-2025",
                "terminationDate": None,
                "terminationType": "Indefinite",
                "recordStatus": "Active",
            }]
        },
        "exclusionPrimaryAddress": {
            "addressLine1": "12 Oak Rd",
            "city": "Madison",
            "stateOrProvinceCode": "WI",
            "zipCode": "53703",
            "countryCode": "USA",
        },
    }],
    "links": {},
}

SAM_EMPTY = {"totalRecords": 0, "excludedEntity": [], "links": {}}


def bidder(**changes):
    row = {column: "" for column in BIDDER_COLUMNS}
    row.update({
        "id": "1001",
        "contractor_name": "Example Builders LLC",
        "address_1": "12 Oak Rd",
        "city": "Madison",
        "state": "WI",
        "zip": "53703",
        "state_federal_debarment": "N",
    })
    row.update(changes)
    return row


def test_sam_adapter_uses_documented_alpha_endpoint_and_only_owns_combined_debarment(monkeypatch):
    monkeypatch.setenv("SAM_API_KEY", "synthetic-sam-test-key")
    adapter = SamExclusionsAdapter()

    assert adapter.api_source is True
    assert adapter.canonical_start_url == SAM_EXCLUSIONS_ENDPOINT
    assert adapter.master_fields == ("state_federal_debarment",)
    assert set(adapter.master_fields) == set(SAM_MASTER_FIELDS)
    assert adapter.allowed_url(SAM_EXCLUSIONS_ENDPOINT)
    assert not adapter.allowed_url("https://api-alpha.sam.gov/entity-information/v4/entities")

    logical = adapter.seed_urls([bidder()])[0]
    assert "synthetic-sam-test-key" not in logical
    query = parse_qs(urlsplit(logical).query)
    assert query["classification"] == ["Firm"]
    assert query["recordStatus"] == ["Active"]
    assert query["size"] == ["10"]
    assert query["exclusionName"][0]

    network = adapter.request_url(logical)
    assert parse_qs(urlsplit(network).query)["api_key"] == ["synthetic-sam-test-key"]


def test_sam_exact_active_match_writes_positive_only(monkeypatch):
    monkeypatch.setenv("SAM_API_KEY", "synthetic-sam-test-key")
    adapter = SamExclusionsAdapter()
    query = adapter.seed_urls([bidder()])[0]

    assert adapter.links(json.dumps(SAM_MATCH), query) == []
    record, = adapter.finalize_records(complete=True)

    assert record["company"] == "Example Builders LLC"
    assert record["state_federal_debarment"] == "Y"
    assert record["extra"]["federal_component_only"] is True
    assert record["extra"]["negative_result_writes_combined_field"] is False
    assert len(record["extra"]["active_federal_exclusions"]) == 1
    assert set(record) & set(BIDDER_COLUMNS) == set(SAM_MASTER_FIELDS)


def test_sam_clean_federal_search_does_not_write_false_combined_negative(monkeypatch):
    monkeypatch.setenv("SAM_API_KEY", "synthetic-sam-test-key")
    adapter = SamExclusionsAdapter()
    query = adapter.seed_urls([bidder(state_federal_debarment="")])[0]

    adapter.links(json.dumps(SAM_EMPTY), query)
    record, = adapter.finalize_records(complete=True)

    assert record.get("state_federal_debarment") == ""
    assert record["extra"]["active_federal_exclusions"] == []
    assert "state debarment still requires separate research" in record["extra"]["narrative"]


async def test_sam_api_crawl_proposes_only_positive_debarment_and_never_stores_key(database, monkeypatch):
    monkeypatch.setenv("SAM_API_KEY", "integration-secret-sam-key")
    baseline = bidder()
    await bidder_db.import_rows("baseline.csv", [baseline], [])

    source_data = SourceCreate(
        name="SAM.gov Federal Debarment / Exclusions (Alpha Test API)",
        start_url=SAM_EXCLUSIONS_ENDPOINT,
        delay_ms=0,
        render_mode="http",
        max_pages=100,
        max_depth=2,
        concurrency=1,
        respect_robots=False,
    ).model_dump(mode="json")
    source = await db.create_source(source_data)

    def site(request):
        assert request.url.path == SAM_EXCLUSIONS_PATH
        query = parse_qs(request.url.query.decode() if isinstance(request.url.query, bytes) else str(request.url.query))
        assert query["api_key"] == ["integration-secret-sam-key"]
        return httpx.Response(200, json=SAM_MATCH, headers={"content-type": "application/json"})

    job_id = await db.create_job(source["id"], False)
    await CrawlEngine(source, job_id, transport=httpx.MockTransport(site)).run()
    job = await db.get_job(job_id)
    assert job["status"] == "completed", job

    records = await db.search_records(source_id=source["id"])
    assert records["total"] == 1
    stored = records["items"][0]
    assert "integration-secret-sam-key" not in stored["source_url"]
    projected = bidder_row(stored, fallback_id=False)
    assert projected["state_federal_debarment"] == "Y"

    # Inspect every persisted crawl URL directly. The contractor-level aggregate
    # evidence URL need not be byte-for-byte identical to the canonical page-cache
    # key, but no persisted page may contain the credential.
    with db.connect() as conn:
        page_urls = [
            row["url"] for row in conn.execute(
                "SELECT url FROM pages WHERE source_id=? ORDER BY id", (source["id"],)
            ).fetchall()
        ]
    assert page_urls
    assert all("integration-secret-sam-key" not in url for url in page_urls)
    assert all("api_key=" not in url.lower() for url in page_urls)

    result = await bidder_db.compare()
    assert result["field_changes"] == 1
    proposals = [
        proposal for proposal in await bidder_db.list_proposals()
        if proposal["proposal_type"] == "field_update"
    ]
    assert {proposal["field_name"] for proposal in proposals} == set(SAM_MASTER_FIELDS)
    assert proposals[0]["old_value"] == "N"
    assert proposals[0]["new_value"] == "Y"


async def test_builtin_sam_source_is_created_once_and_locked(database):
    first = await main.ensure_builtin_sam_source()
    second = await main.ensure_builtin_sam_source()
    sources = await database.list_sources()

    assert first["id"] == second["id"]
    assert first["name"] == main.SAM_SOURCE_NAME
    assert first["start_url"] == SAM_EXCLUSIONS_ENDPOINT
    assert first["render_mode"] == "http"
    assert not bool(first["respect_robots"])
    assert len([source for source in sources if main._is_sam_source(source)]) == 1

    try:
        await main.patch_source(first["id"], SourceUpdate(name="Changed SAM"))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("Built-in SAM source should not be editable")

    try:
        await main._delete_source(first["id"])
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("Built-in SAM source should not be removable")

    duplicate = SourceCreate(
        name="Duplicate SAM",
        start_url=SAM_EXCLUSIONS_ENDPOINT,
        render_mode="http",
        respect_robots=False,
    )
    try:
        await main.post_source(duplicate)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert "built in" in str(getattr(exc, "detail", "")).lower()
    else:
        raise AssertionError("Generic source setup should not create a second SAM source")


async def test_sam_api_key_is_validated_before_save_and_never_returned(database, monkeypatch):
    observed = {}
    saved = {}

    async def fake_validate(value):
        observed["value"] = value

    def fake_save(value):
        saved["value"] = value

    monkeypatch.setattr(main, "validate_sam_api_key", fake_validate)
    monkeypatch.setattr(main, "save_sam_api_key", fake_save)
    monkeypatch.setattr(main, "sam_api_key_configured", lambda: True)

    result = await main.configure_sam_integration(
        main.SamApiKeyPayload(api_key="synthetic-valid-sam-key")
    )
    assert observed["value"] == "synthetic-valid-sam-key"
    assert saved["value"] == "synthetic-valid-sam-key"
    assert result["configured"] is True
    assert result["validated"] is True

    status = await main.sam_integration_status()
    assert status["configured"] is True
    assert status["environment"] == "alpha"
    assert "api_key" not in status
    assert "synthetic-valid-sam-key" not in json.dumps(status)


async def test_rejected_sam_key_is_not_saved(database, monkeypatch):
    saved = []

    async def fake_validate(_value):
        raise ValueError("SAM.gov Alpha rejected this API key")

    monkeypatch.setattr(main, "validate_sam_api_key", fake_validate)
    monkeypatch.setattr(main, "save_sam_api_key", lambda value: saved.append(value))

    try:
        await main.configure_sam_integration(
            main.SamApiKeyPayload(api_key="synthetic-invalid-sam-key")
        )
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        assert "rejected" in str(getattr(exc, "detail", "")).lower()
    else:
        raise AssertionError("Invalid SAM key should be rejected")

    assert saved == []
