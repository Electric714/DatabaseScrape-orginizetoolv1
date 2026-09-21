import json
from datetime import date
import pytest
import httpx
from app import database as db, bidder_master as bm, main
from app.identity import location_corroborates
from app.bbb_adapter import BbbComplaintsAdapter
from app.osha_adapter import OshaEstablishmentAdapter
from app.state_adapter import MinnesotaDebarmentAdapter, parse_minnesota, active_on, MN_URL
from app.crawler import CrawlEngine
from app.models import SourceCreate
from app.bidder_schema import BIDDER_COLUMNS, parse_bidder_csv


def contractor(**changes):
    return {**dict.fromkeys(BIDDER_COLUMNS, ""), "id": "001", "contractor_name": "Example Builders LLC", "city": "Madison", "state": "WI", "zip": "00501", **changes}


def mn_html(end="9/26/2026"):
    return f'''<div class="search-results">Results 1 - 1 of 1
    <div class="results"><div class="result-link"><a id="1Anchor">Example Builders LLC</a></div>
    <table><tr><td>123 Main St</td></tr><tr><td>Madison, WI 00501</td></tr>
    <tr><td>Debarment Date:</td><td>9/26/2025</td></tr>
    <tr><td>Debarment End Date:</td><td>{end}</td></tr></table></div></div>'''


def test_matching_requires_location_even_with_one_name(monkeypatch):
    monkeypatch.setenv("DOL_API_KEY", "synthetic-test-key")
    a = OshaEstablishmentAdapter()
    a.seed_urls([contractor()])
    candidate = {"estab_name": "Example Builders LLC", "site_state": "FL", "site_city": "Madison", "site_zip": "00501"}
    assert a._choose_exact_context(candidate, list(a.contractors)) is None
    assert not location_corroborates({}, contractor())
    assert location_corroborates({"state": "IL", "city": "Chicago"}, contractor(additional_address_state="IL", additional_address_city="Chicago"))


def test_bbb_cap_is_incomplete():
    a = BbbComplaintsAdapter()
    query = a.seed_urls([contractor()])[0]
    page3 = query.replace("page=1", "page=3")
    page4 = query.replace("page=1", "page=4")
    a.links(f'<h1>No results</h1><a href="{page4}">Next</a>', page3)
    record, = a.finalize_records(True)
    assert not record["extra"]["complete_aggregate"]
    assert record["better_business_bureau_complaints"] == ""


def test_mn_historical_and_unknown_dates_never_active():
    record, = parse_minnesota(mn_html())
    assert active_on(record, date(2026, 9, 14)) is True
    assert active_on(record, date(2027, 1, 1)) is False
    record, = parse_minnesota(mn_html("unknown"))
    assert active_on(record, date(2026, 9, 14)) is None
    with pytest.raises(ValueError):
        parse_minnesota(mn_html().replace("of 1", "of 2"))
    with pytest.raises(ValueError):
        parse_minnesota("<h1>Service unavailable</h1>")


async def test_selected_scan_preserves_other_contractors(database):
    await bm.import_rows("test.csv", [contractor(), contractor(id="002", contractor_name="Other Builder")], [])
    rows = await bm.all_rows()
    source = await db.create_source(SourceCreate(name="MN", start_url=MN_URL, delay_ms=0, render_mode="http").model_dump(mode="json"))
    def respond(request):
        return httpx.Response(200, text="User-agent: *\nAllow: /" if request.url.path == "/robots.txt" else mn_html(), headers={"content-type": "text/html"})
    for selected in [None, [rows[0]["_master_id"]]]:
        jid = await db.create_job(source["id"], False)
        await CrawlEngine(source, jid, master_ids=selected, transport=httpx.MockTransport(respond)).run()
        assert (await db.get_job(jid))["status"] == "completed"
    assert (await db.search_records(source_id=source["id"]))["total"] == 2
    assert (await bm.all_rows()) == rows


async def test_failed_refresh_cannot_apply_stale_proposal(database):
    await bm.import_rows("test.csv", [contractor(osha="N")], [])
    source = await db.create_source(SourceCreate(name="OSHA", start_url="https://apiprod.dol.gov/v4/get/OSHA/inspection/json").model_dump(mode="json"))
    jid = await db.create_job(source["id"], False)
    await db.update_job(jid, status="completed")
    await db.upsert_record(source["id"], {"external_id": "x", "company": "Example Builders LLC", "osha": "Y", "source_url": source["start_url"], "extra": {"job_id": jid, "complete_aggregate": True}})
    assert (await bm.compare())["field_changes"] == 1
    proposal, = await bm.list_proposals()
    next_id = await db.create_job(source["id"], False)
    await db.update_job(next_id, status="failed")
    with pytest.raises(ValueError, match="stale"):
        await bm.apply(proposal["id"])
    assert (await bm.compare())["field_changes"] == 0
    assert (await bm.all_rows())[0]["osha"] == "N"


async def test_csv_preserves_cell_text(database):
    import csv, io
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=BIDDER_COLUMNS)
    writer.writeheader()
    original = contractor(related_companies="  A & B; C, Inc.  ")
    writer.writerow(original)
    parsed, _ = parse_bidder_csv(buffer.getvalue().encode())
    await bm.import_rows("test.csv", parsed, [])
    saved, = await bm.all_rows()
    assert {k: saved[k] for k in BIDDER_COLUMNS} == original


async def test_catalog_excludes_pacer_and_selection_validates(database):
    catalog = await main.source_catalog()
    assert next(s for s in catalog["sources"] if s["key"] == "pacer")["status"] == "Excluded from POC"
    source = await main.ensure_builtin_bbb_source()
    with pytest.raises(Exception) as error:
        await main.launch_scan(source["id"], master_ids=[999])
    assert error.value.status_code == 422


def test_api_error_payloads_are_not_successful_empty_results():
    from app.sam_adapter import _payload
    from app.osha_adapter import _rows
    with pytest.raises(ValueError):
        _payload('{"error":"unavailable"}')
    with pytest.raises(ValueError):
        _rows('{"data":[null]}')
