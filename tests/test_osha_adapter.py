from datetime import date
from urllib.parse import parse_qs, urlsplit

import httpx

from app import bidder_master as bidder_db, database as db
from app.bidder_schema import BIDDER_COLUMNS, bidder_row
from app.crawler import CrawlEngine
from app.models import SourceCreate
from app.osha_adapter import (
    OSHA_DETAIL_PATH,
    OSHA_FORM_PATH,
    OSHA_SEARCH_PATH,
    OshaEstablishmentAdapter,
    company_core,
    parse_inspection_detail,
    year_windows,
)


SEARCH_ROW = """
<table>
  <tr>
    <th></th><th>#</th><th>Activity</th><th>Date Opened</th><th>RID</th><th>ST</th>
    <th>Type</th><th>Scope</th><th>SIC</th><th>NAICS</th><th>Violations</th><th>Establishment Name</th>
  </tr>
  <tr>
    <td></td><td>1</td>
    <td><a href="/ords/imis/establishment.inspection_detail?id=305160491">305160491</a></td>
    <td>04/29/2003</td><td>0521400</td><td>IL</td><td>Complaint</td><td>Partial</td>
    <td>1623</td><td>237110</td><td>2</td><td>A-Lamp Concrete Contractors, Inc.</td>
  </tr>
  <tr>
    <td></td><td>2</td>
    <td><a href="/ords/imis/establishment.inspection_detail?id=999999999">999999999</a></td>
    <td>01/01/2004</td><td>0000000</td><td>IL</td><td>Planned</td><td>Complete</td>
    <td></td><td></td><td>5</td><td>Different Concrete Company LLC</td>
  </tr>
</table>
"""

DETAIL = """
<html><body>
<h3>Inspection: 305160491 - A-Lamp Concrete Contractors, Inc.</h3>
<div>Date Opened: 04/29/2003</div>
<table>
<tr><th>Violations/Penalties</th><th>Serious</th><th>Willful</th><th>Repeat</th><th>Other</th><th>Unclass</th><th>Total</th></tr>
<tr><td>Initial Violations</td><td>1</td><td></td><td>1</td><td></td><td></td><td>2</td></tr>
<tr><td>Current Violations</td><td>1</td><td>0</td><td>1</td><td>0</td><td>0</td><td>2</td></tr>
</table>
</body></html>
"""


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


def test_osha_adapter_uses_scoped_browser_session():
    adapter = OshaEstablishmentAdapter()
    assert adapter.direct_browser is True
    assert adapter.visible_browser is True
    assert adapter.fail_fast_access_errors is True
    assert adapter.browser_prime_url.endswith(OSHA_FORM_PATH)
    assert adapter.browser_allowed_url("https://www.osha.gov" + OSHA_FORM_PATH)
    assert adapter.browser_allowed_url("https://www.osha.gov" + OSHA_SEARCH_PATH + "?establishment=Example")
    assert adapter.browser_allowed_url("https://www.osha.gov" + OSHA_DETAIL_PATH + "?id=123")
    assert not adapter.browser_allowed_url("https://www.osha.gov/news")
    assert not adapter.browser_allowed_url("https://example.com" + OSHA_SEARCH_PATH)


def test_company_core_handles_osha_punctuation_and_state_prefixes():
    assert company_core("A. LAMP CONCRETE CONTRACTORS INC") == company_core("A-Lamp Concrete Contractors, Inc.")
    assert company_core("8 ACES CONSTRUCTION INC") == company_core("106836 - 8 Aces Construction")
    assert company_core("10330 EXCEEDING LLC") == "10330 exceeding"


def test_osha_uses_ten_year_windows_from_1972():
    windows = year_windows(date(2026, 9, 6))
    assert windows[0] == (date(1972, 1, 1), date(1981, 12, 31))
    assert windows[-1] == (date(2022, 1, 1), date(2026, 9, 6))
    assert all((end.year - start.year) <= 9 for start, end in windows)


def test_targeted_seed_urls_are_master_contractor_queries_only():
    adapter = OshaEstablishmentAdapter()
    urls = adapter.seed_urls([bidder()], today=date(2026, 9, 6))
    assert urls
    assert all(urlsplit(url).path == OSHA_SEARCH_PATH for url in urls)
    assert all(parse_qs(urlsplit(url).query)["p_case"] == ["all"] for url in urls)
    assert all(parse_qs(urlsplit(url).query)["p_violations_exist"] == ["both"] for url in urls)
    assert all("A" in parse_qs(urlsplit(url).query)["establishment"][0].upper() for url in urls)


def test_search_results_follow_only_exact_contractor_and_pagination():
    adapter = OshaEstablishmentAdapter()
    url = adapter.seed_urls([bidder()], today=date(2026, 9, 6))[0]
    pagination = url + "&p_start=20&p_finish=40&p_direction=Next"
    html = SEARCH_ROW + f'<a href="{pagination}">2</a>'
    links = adapter.links(html, url)
    assert any(OSHA_DETAIL_PATH in link and "305160491" in link for link in links)
    assert not any("999999999" in link for link in links)
    assert any("p_start=20" in link for link in links)


def test_inspection_detail_and_aggregate_severe_criteria():
    adapter = OshaEstablishmentAdapter()
    search_url = adapter.seed_urls([bidder()], today=date(2026, 9, 6))[0]
    links = adapter.links(SEARCH_ROW, search_url)
    detail_url = next(link for link in links if OSHA_DETAIL_PATH in link)
    assert adapter.extract(DETAIL, detail_url) == []

    parsed = parse_inspection_detail(DETAIL, detail_url)
    assert parsed["inspection_id"] == "305160491"
    assert parsed["severe_current_violations"] == 2

    record, = adapter.finalize_records(complete=True)
    assert record["company"] == "A. LAMP CONCRETE CONTRACTORS INC"
    assert record["osha"] == "Y"
    assert record["osha_severe_violations"] == "2"
    assert record["years"] == "2003"
    assert "Serious + Willful + Repeat" in record["extra"]["severe_definition"]


def test_similar_name_candidate_stays_unknown_instead_of_false_negative():
    adapter = OshaEstablishmentAdapter()
    search_url = adapter.seed_urls([bidder()], today=date(2026, 9, 6))[0]
    html = """<table><tr><th></th><th>#</th><th>Activity</th><th>Establishment Name</th></tr><tr><td></td><td>1</td><td><a href="/ords/imis/establishment.inspection_detail?id=777">777</a></td><td>A Lamp Concrete Contracting Inc.</td></tr></table>"""
    adapter.links(html, search_url)
    record, = adapter.finalize_records(complete=True)
    assert record["osha"] == ""
    assert record["extra"]["ambiguous_candidates"]
    assert "manual identity review" in record["osha_details"]


def test_negative_osha_result_requires_complete_query_set():
    adapter = OshaEstablishmentAdapter()
    adapter.seed_urls([bidder()], today=date(2026, 9, 6))
    assert adapter.finalize_records(complete=False) == []
    record, = adapter.finalize_records(complete=True)
    assert record["osha"] == "N"
    assert record["osha_severe_violations"] == ""
    assert "No exact-name or plausible similar-name OSHA inspection match" in record["osha_details"]


async def test_osha_query_mode_crawls_master_and_proposes_update(database):
    baseline = bidder()
    await bidder_db.import_rows("baseline.csv", [baseline], [])

    source_data = SourceCreate(
        name="OSHA Establishment Search",
        start_url="https://www.osha.gov/pls/imis/establishment.html",
        delay_ms=0,
        render_mode="http",
        max_pages=100,
        max_depth=3,
        concurrency=4,
    ).model_dump(mode="json")
    source = await db.create_source(source_data)

    def site(request):
        path = request.url.path
        if path == "/robots.txt":
            raise AssertionError("OSHA proof-of-concept must not request robots.txt")
        if path == OSHA_SEARCH_PATH:
            query = parse_qs(request.url.query.decode())
            # Put the known historical inspection in one decade. All other
            # targeted windows are valid no-result searches.
            if query.get("startyear") == ["2002"]:
                return httpx.Response(200, text=SEARCH_ROW, headers={"content-type": "text/html"})
            return httpx.Response(200, text="<html><body>Your search did not return any results.</body></html>", headers={"content-type": "text/html"})
        if path == OSHA_DETAIL_PATH and request.url.params.get("id") == "305160491":
            return httpx.Response(200, text=DETAIL, headers={"content-type": "text/html"})
        return httpx.Response(404, text="not found", headers={"content-type": "text/html"})

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
    assert result["field_changes"] >= 1
    proposals = await bidder_db.list_proposals()
    by_field = {proposal["field_name"]: proposal for proposal in proposals if proposal["proposal_type"] == "field_update"}
    assert by_field["osha"]["old_value"] == "N"
    assert by_field["osha"]["new_value"] == "Y"
