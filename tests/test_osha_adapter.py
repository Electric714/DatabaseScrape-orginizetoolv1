import json
from urllib.parse import parse_qs, urlsplit

import httpx

from app import bidder_master as bidder_db, database as db
from app.bidder_schema import BIDDER_COLUMNS, bidder_row
from app.crawler import CrawlEngine
from app.models import SourceCreate
from app.osha_adapter import (
    DOL_INSPECTION_ENDPOINT,
    DOL_INSPECTION_PATH,
    DOL_VIOLATION_PATH,
    OSHA_MASTER_FIELDS,
    OshaEstablishmentAdapter,
    company_core,
)


INSPECTION_ROWS = {
    "data": [
        {
            "activity_nr": "305160491",
            "estab_name": "A-Lamp Concrete Contractors, Inc.",
            "site_address": "800 W Irving Park Rd",
            "site_city": "Schaumburg",
            "site_state": "IL",
            "site_zip": "60193",
            "open_date": "2003-04-29",
            "close_case_date": "2003-10-01",
            "naics_code": "237110",
        },
        {
            "activity_nr": "999999999",
            "estab_name": "Different Concrete Company LLC",
            "site_address": "1 Other Street",
            "site_city": "Chicago",
            "site_state": "IL",
            "site_zip": "60601",
            "open_date": "2004-01-01",
            "close_case_date": "",
            "naics_code": "238990",
        },
    ]
}

VIOLATION_ROWS = {
    "data": [
        {
            "activity_nr": "305160491",
            "citation_id": "01001",
            "delete_flag": "",
            "viol_type": "S",
            "issuance_date": "2003-05-01",
            "current_penalty": "500",
            "initial_penalty": "750",
            "standard": "19260020",
            "nr_instances": "1",
            "nr_exposed": "2",
        },
        {
            "activity_nr": "305160491",
            "citation_id": "01002",
            "delete_flag": "",
            "viol_type": "R",
            "issuance_date": "2003-05-01",
            "current_penalty": "1000",
            "initial_penalty": "1000",
            "standard": "19260501",
            "nr_instances": "1",
            "nr_exposed": "1",
        },
        {
            "activity_nr": "305160491",
            "citation_id": "01003",
            "delete_flag": "",
            "viol_type": "O",
            "issuance_date": "2003-05-01",
            "current_penalty": "0",
            "initial_penalty": "0",
            "standard": "19040001",
            "nr_instances": "1",
            "nr_exposed": "1",
        },
        {
            "activity_nr": "305160491",
            "citation_id": "01004",
            "delete_flag": "X",
            "viol_type": "S",
            "issuance_date": "2003-05-01",
            "current_penalty": "0",
            "initial_penalty": "250",
            "standard": "19260021",
            "nr_instances": "1",
            "nr_exposed": "1",
        },
    ]
}


def bidder(**changes):
    row = {column: "" for column in BIDDER_COLUMNS}
    row.update({
        "id": "62",
        "contractor_name": "A. LAMP CONCRETE CONTRACTORS INC",
        "address_1": "800 W Irving Park Rd",
        "city": "Schaumburg",
        "state": "IL",
        "zip": "60193",
        "osha": "N",
    })
    row.update(changes)
    return row


def test_osha_adapter_is_authenticated_api_and_owns_only_three_fields(monkeypatch):
    monkeypatch.setenv("DOL_API_KEY", "test-dol-api-key")
    adapter = OshaEstablishmentAdapter()

    assert adapter.api_source is True
    assert adapter.canonical_start_url == DOL_INSPECTION_ENDPOINT
    assert adapter.master_fields == ("osha", "osha_severe_violations", "years")
    assert set(adapter.master_fields) == set(OSHA_MASTER_FIELDS)
    assert adapter.request_headers(DOL_INSPECTION_ENDPOINT)["X-API-KEY"] == "test-dol-api-key"
    assert adapter.allowed_url(DOL_INSPECTION_ENDPOINT)
    assert adapter.allowed_url("https://apiprod.dol.gov" + DOL_VIOLATION_PATH + "?limit=10")
    assert not adapter.allowed_url("https://apiprod.dol.gov/v4/datasets")
    assert not adapter.allowed_url("https://www.osha.gov/ords/imis/establishment.html")


def test_company_core_handles_osha_punctuation_and_state_prefixes():
    assert company_core("A. LAMP CONCRETE CONTRACTORS INC") == company_core(
        "A-Lamp Concrete Contractors, Inc."
    )
    assert company_core("8 ACES CONSTRUCTION INC") == company_core(
        "106836 - 8 Aces Construction"
    )
    assert company_core("10330 EXCEEDING LLC") == "10330 exceeding"


def test_targeted_queries_use_master_names_without_putting_key_in_url(monkeypatch):
    monkeypatch.setenv("DOL_API_KEY", "secret-test-key")
    adapter = OshaEstablishmentAdapter()
    urls = adapter.seed_urls([bidder()])

    assert urls
    for url in urls:
        parts = urlsplit(url)
        assert parts.hostname == "apiprod.dol.gov"
        assert parts.path == DOL_INSPECTION_PATH
        assert "secret-test-key" not in url
        query = parse_qs(parts.query)
        assert "filter_object" in query
        filter_object = json.loads(query["filter_object"][0])
        assert filter_object["field"] == "estab_name"
        assert filter_object["operator"] == "like"
        assert "A LAMP CONCRETE CONTRACTORS" in filter_object["value"]
        requested_fields = set(query["fields"][0].split(","))
        assert {"activity_nr", "estab_name", "site_state", "site_zip"} <= requested_fields


def test_inspection_and_violation_aggregation_writes_only_osha_fields(monkeypatch):
    monkeypatch.setenv("DOL_API_KEY", "test-dol-api-key")
    adapter = OshaEstablishmentAdapter()
    search_url = adapter.seed_urls([bidder()])[0]

    links = adapter.links(json.dumps(INSPECTION_ROWS), search_url)
    violation_url = next(link for link in links if urlsplit(link).path == DOL_VIOLATION_PATH)
    assert "305160491" in violation_url

    assert adapter.extract(json.dumps(VIOLATION_ROWS), violation_url) == []
    record, = adapter.finalize_records(complete=True)

    assert record["company"] == "A. LAMP CONCRETE CONTRACTORS INC"
    assert record["osha"] == "Y"
    assert record["osha_severe_violations"] == "2"
    assert record["years"] == "2003"
    assert record["extra"]["api_fields_written_to_master"] == list(OSHA_MASTER_FIELDS)
    assert set(record["extra"]["identity_fields_used_for_matching_only"]) == {
        "contractor_name", "related_companies", "address_1", "city", "state", "zip"
    }
    master_fields_present = set(record) & set(BIDDER_COLUMNS)
    assert master_fields_present == set(OSHA_MASTER_FIELDS)


def test_similar_name_candidate_stays_unknown_instead_of_false_negative(monkeypatch):
    monkeypatch.setenv("DOL_API_KEY", "test-dol-api-key")
    adapter = OshaEstablishmentAdapter()
    search_url = adapter.seed_urls([bidder()])[0]
    similar = {
        "data": [{
            "activity_nr": "777",
            "estab_name": "A Lamp Concrete Contracting Inc.",
            "site_address": "800 W Irving Park Rd",
            "site_city": "Schaumburg",
            "site_state": "IL",
            "site_zip": "60193",
            "open_date": "2025-01-01",
        }]
    }

    adapter.links(json.dumps(similar), search_url)
    record, = adapter.finalize_records(complete=True)

    assert record["osha"] == ""
    assert record["extra"]["ambiguous_candidates"]
    assert "manual identity review" in record["osha_details"]


def test_negative_osha_result_requires_complete_api_run(monkeypatch):
    monkeypatch.setenv("DOL_API_KEY", "test-dol-api-key")
    adapter = OshaEstablishmentAdapter()
    adapter.seed_urls([bidder()])

    assert adapter.finalize_records(complete=False) == []

    record, = adapter.finalize_records(complete=True)
    assert record["osha"] == "N"
    assert record["osha_severe_violations"] == ""
    assert record["years"] == ""


async def test_osha_api_crawl_proposes_only_osha_owned_master_fields(database, monkeypatch):
    monkeypatch.setenv("DOL_API_KEY", "integration-test-key")
    baseline = bidder()
    await bidder_db.import_rows("baseline.csv", [baseline], [])

    source_data = SourceCreate(
        name="OSHA / DOL Enforcement API",
        start_url=DOL_INSPECTION_ENDPOINT,
        delay_ms=0,
        render_mode="http",
        max_pages=100,
        max_depth=3,
        concurrency=4,
        respect_robots=False,
    ).model_dump(mode="json")
    source = await db.create_source(source_data)

    def site(request):
        assert request.url.path != "/robots.txt"
        assert request.headers.get("x-api-key") == "integration-test-key"
        path = request.url.path
        if path == DOL_INSPECTION_PATH:
            return httpx.Response(
                200, json=INSPECTION_ROWS, headers={"content-type": "application/json"}
            )
        if path == DOL_VIOLATION_PATH:
            return httpx.Response(
                200, json=VIOLATION_ROWS, headers={"content-type": "application/json"}
            )
        return httpx.Response(404, text="not found")

    job_id = await db.create_job(source["id"], False)
    await CrawlEngine(source, job_id, transport=httpx.MockTransport(site)).run()
    job = await db.get_job(job_id)
    assert job["status"] == "completed", job

    source_records = await db.search_records(source_id=source["id"])
    assert source_records["total"] == 1
    projected = bidder_row(source_records["items"][0], fallback_id=False)
    assert projected["contractor_name"] == baseline["contractor_name"]
    assert projected["osha"] == "Y"
    assert projected["osha_severe_violations"] == "2"
    assert projected["years"] == "2003"

    result = await bidder_db.compare()
    assert result["field_changes"] == 3
    proposals = await bidder_db.list_proposals()
    field_proposals = [
        proposal for proposal in proposals
        if proposal["proposal_type"] == "field_update"
    ]
    assert {proposal["field_name"] for proposal in field_proposals} == set(OSHA_MASTER_FIELDS)
    assert all(proposal["field_name"] in OSHA_MASTER_FIELDS for proposal in field_proposals)

    by_field = {proposal["field_name"]: proposal for proposal in field_proposals}
    assert by_field["osha"]["old_value"] == "N"
    assert by_field["osha"]["new_value"] == "Y"
    assert by_field["osha_severe_violations"]["new_value"] == "2"
    assert by_field["years"]["new_value"] == "2003"
