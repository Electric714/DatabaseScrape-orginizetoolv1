from datetime import date

import httpx
import pytest

from app import bidder_master as bm, database as db
from app.adapters import ADAPTERS
from app.bidder_schema import BIDDER_COLUMNS
from app.crawler import CrawlEngine
from app.models import SourceCreate
from app.source_catalog import owned_fields
from app.state_sources import (
    IL_URL,
    WI_URL,
    IllinoisDebarmentAdapter,
    WisconsinDebarmentAdapter,
    find_wisconsin_candidates,
    parse_illinois,
)


def contractor(**changes):
    return {
        **dict.fromkeys(BIDDER_COLUMNS, ""),
        "id": "001",
        "contractor_name": "Seven Brothers Painting, Inc.",
        "address_1": "100 Main St",
        "city": "Oak Creek",
        "state": "WI",
        "zip": "53154",
        **changes,
    }


IL_HTML = """
<html><body>
<h1>Public Works Debarred Contractors</h1>
<p>LIST OF CONTRACTORS PROHIBITED FROM AN AWARD OF A CONTRACT OR A SUBCONTRACT FOR PUBLIC WORKS PROJECTS</p>
<p>Pursuant to the Prevailing Wage Act, the following contractors are prohibited.</p>
<p>Seven Brothers Painting, Inc. has been debarred from any public works project for a period of four years effective May 5, 2025</p>
</body></html>
"""

WI_TEXT = """
List of Debarred, Suspended and Ineligible Contractors
Prepared and Issued by Wisconsin Department of Transportation
Debarred Contractors
Name of Contractor Address Effective Date Termination Date Action Restricted Area Acting Agency Cause Code
Seven Brothers Painting, Inc. 100 Main St Oak Creek, WI 53154 5/5/2025 5/5/2029 Debarment Statewide WisDOT 3
Ineligible Contractors
Suspended Contractors
"""


def test_illinois_parser_and_exact_match_positive():
    entries = parse_illinois(IL_HTML)
    assert entries == [{
        "name": "Seven Brothers Painting, Inc.",
        "effective": "2025-05-05",
        "termination": "2029-05-05",
        "source_url": IL_URL,
    }]
    adapter = IllinoisDebarmentAdapter()
    adapter.seed_urls([contractor(_master_id=7)])
    adapter.links(IL_HTML, IL_URL)
    record, = adapter.finalize_records(True)
    assert record["state_federal_debarment"] == "Y"
    assert record["prevailing_wage_violations"] == "Y"
    assert record["extra"]["master_id"] == 7
    assert record["extra"]["complete_aggregate"] is True


def test_illinois_does_not_promote_near_name_or_bad_layout():
    adapter = IllinoisDebarmentAdapter()
    adapter.seed_urls([contractor(contractor_name="Seven Brothers Painting LLC")])
    adapter.links(IL_HTML, IL_URL)
    record, = adapter.finalize_records(True)
    assert record["state_federal_debarment"] == ""
    assert record["prevailing_wage_violations"] == ""
    with pytest.raises(ValueError):
        parse_illinois("<html><h1>Service unavailable</h1></html>")


def test_wisconsin_requires_location_and_active_dates():
    row = contractor()
    candidate, = find_wisconsin_candidates(WI_TEXT, row)
    assert candidate["location_confirmed"] is True
    assert candidate["active"] is True
    assert candidate["action"] == "Debarment"

    wrong_location = contractor(city="Madison", zip="53703")
    candidate, = find_wisconsin_candidates(WI_TEXT, wrong_location)
    assert candidate["location_confirmed"] is False

    historical = WI_TEXT.replace("5/5/2029", "5/5/2026")
    candidate, = find_wisconsin_candidates(historical, row)
    assert candidate["active"] is False


def test_wisconsin_adapter_positive_only_when_corroborated():
    adapter = WisconsinDebarmentAdapter()
    adapter.seed_urls([contractor(_master_id=9)])
    adapter.links(WI_TEXT, WI_URL)
    record, = adapter.finalize_records(True)
    assert record["state_federal_debarment"] == "Y"
    assert record["extra"]["master_id"] == 9


def test_state_source_field_ownership():
    assert owned_fields(IL_URL) == ["state_federal_debarment", "prevailing_wage_violations"]
    assert owned_fields(WI_URL) == ["state_federal_debarment"]


async def test_binary_adapter_opt_in_allows_pdf_content(database, monkeypatch):
    class BinaryAdapter:
        query_mode = always_parse = ignore_robots = True
        canonical_start_url = "https://pdf.test/list.pdf"
        accepted_content_types = ("application/pdf",)
        allowed_asset_extensions = (".pdf",)

        def seed_urls(self, rows):
            return [self.canonical_start_url]

        def allowed_url(self, url):
            return url == self.canonical_start_url

        def decode_body(self, body, headers, url):
            assert body == b"%PDF-fake-fixture"
            return "decoded fixture"

        def links(self, text, url):
            assert text == "decoded fixture"
            return []

        def extract(self, text, url):
            return []

        def finalize_records(self, complete):
            return []

    monkeypatch.setitem(ADAPTERS, "pdf.test", BinaryAdapter)
    await bm.import_rows("test.csv", [contractor()], [])
    source = await db.create_source(SourceCreate(
        name="PDF fixture",
        start_url="https://pdf.test/list.pdf",
        delay_ms=0,
        render_mode="http",
        respect_robots=False,
    ).model_dump(mode="json"))

    def respond(request):
        return httpx.Response(200, content=b"%PDF-fake-fixture", headers={"content-type": "application/pdf"})

    job_id = await db.create_job(source["id"], False)
    await CrawlEngine(source, job_id, transport=httpx.MockTransport(respond)).run()
    job = await db.get_job(job_id)
    assert job["status"] == "completed"
    assert job["errors"] == 0
