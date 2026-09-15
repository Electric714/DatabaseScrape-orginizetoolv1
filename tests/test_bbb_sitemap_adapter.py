import json

import httpx

from app import bidder_master as bidder_db, database as db
from app.adapters import adapter_for_url
from app.bbb_sitemap_adapter import (
    BBB_SITEMAP_INDEX,
    BbbSitemapComplaintsAdapter,
    _expanded_sitemap_numbers,
)
from app.bidder_schema import BIDDER_COLUMNS, bidder_row
from app.crawler import CrawlEngine
from app.models import SourceCreate


def bidder(**changes):
    row = {column: "" for column in BIDDER_COLUMNS}
    row.update({
        "id": "synthetic-1",
        "contractor_name": "EXAMPLE BUILDERS LLC",
        "address_1": "123 Main St",
        "city": "Madison",
        "state": "WI",
        "zip": "53703",
        "better_business_bureau_complaints": "",
    })
    row.update(changes)
    return row


def xml_index(numbers):
    body = "".join(
        f"<sitemap><loc>https://www.bbb.org/sitemap-business-profiles-{number}.xml</loc></sitemap>"
        for number in numbers
    )
    return f"<?xml version='1.0' encoding='UTF-8'?><sitemapindex>{body}</sitemapindex>"


def xml_urls(urls):
    body = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    return f"<?xml version='1.0' encoding='UTF-8'?><urlset>{body}</urlset>"


PROFILE = "https://www.bbb.org/us/wi/madison/profile/general-contractor/example-builders-llc-0694-1000000000"
PROFILE_HTML = """
<html><head><script type="application/ld+json">
{"@type":"LocalBusiness","name":"Example Builders LLC","address":{"@type":"PostalAddress",
"streetAddress":"123 Main St","addressLocality":"Madison","addressRegion":"WI","postalCode":"53703"}}
</script></head><body><h1>Example Builders LLC</h1><p>123 Main St, Madison, WI 53703</p></body></html>
"""
COMPLAINTS_HTML = """
<html><body><h2>Customer Complaints Summary</h2>
<p>3 total complaints in the last 3 years.</p>
<p>1 complaint closed in the last 12 months.</p></body></html>
"""


def test_seed_uses_published_sitemap_and_never_bbb_search():
    adapter = BbbSitemapComplaintsAdapter()
    assert adapter.seed_urls([bidder()]) == [BBB_SITEMAP_INDEX]
    assert adapter.allowed_url(BBB_SITEMAP_INDEX)
    assert adapter.allowed_url(PROFILE)
    assert adapter.allowed_url(PROFILE + "/complaints")
    assert not adapter.allowed_url("https://www.bbb.org/search?find_text=Example+Builders")
    assert not adapter.allowed_url(PROFILE + "?page=2")
    assert not adapter.allowed_url("https://www.bbb.org/sitemap-business-profiles-327.xml")


def test_wisconsin_index_selection_is_narrow_and_includes_boundary_padding():
    adapter = BbbSitemapComplaintsAdapter()
    adapter.seed_urls([bidder()])
    expected = _expanded_sitemap_numbers("WI")
    index_html = xml_index(range(1, 576))
    links = adapter.links(index_html, BBB_SITEMAP_INDEX)
    selected = {
        int(url.rsplit("-", 1)[1].split(".", 1)[0])
        for url in links
    }
    assert selected == expected
    assert min(selected) == 324
    assert max(selected) == 332
    assert len(selected) == 9
    assert all("/search" not in url for url in links)
    assert adapter.allowed_url("https://www.bbb.org/sitemap-business-profiles-327.xml")
    assert not adapter.allowed_url("https://www.bbb.org/sitemap-business-profiles-333.xml")


def test_sitemap_slug_only_discovers_plausible_same_state_profile():
    adapter = BbbSitemapComplaintsAdapter()
    adapter.seed_urls([bidder()])
    sitemap = "https://www.bbb.org/sitemap-business-profiles-327.xml"
    adapter.links(xml_index(range(324, 333)), BBB_SITEMAP_INDEX)
    links = adapter.links(xml_urls([
        PROFILE,
        "https://www.bbb.org/us/il/chicago/profile/general-contractor/example-builders-llc-0000-1111111111",
        "https://www.bbb.org/us/wi/madison/profile/general-contractor/completely-different-company-0000-2222222222",
    ]), sitemap)
    assert links == [PROFILE]


async def test_full_sitemap_to_profile_to_complaints_flow_never_uses_search(database):
    await bidder_db.import_rows("synthetic.csv", [bidder()], [])
    source = await db.create_source(SourceCreate(
        name="BBB Business Profiles / Complaints",
        start_url=BBB_SITEMAP_INDEX,
        delay_ms=0,
        render_mode="http",
        max_pages=100,
        max_depth=5,
        concurrency=1,
        respect_robots=True,
    ).model_dump(mode="json"))

    requested = []
    wi_numbers = _expanded_sitemap_numbers("WI")

    def site(request):
        requested.append(str(request.url))
        path = request.url.path
        assert path != "/search"
        if path == "/robots.txt":
            return httpx.Response(200, text=(
                "User-agent: *\n"
                "Disallow: /*?\n"
                "Sitemap: https://www.bbb.org/sitemap-business-profiles-index.xml\n"
            ), headers={"content-type": "text/plain"})
        if path == "/sitemap-business-profiles-index.xml":
            return httpx.Response(200, text=xml_index(range(1, 576)), headers={"content-type": "text/xml"})
        if path.startswith("/sitemap-business-profiles-"):
            number = int(path.rsplit("-", 1)[1].split(".", 1)[0])
            urls = [PROFILE] if number == 327 else []
            assert number in wi_numbers
            return httpx.Response(200, text=xml_urls(urls), headers={"content-type": "text/xml"})
        if path == "/us/wi/madison/profile/general-contractor/example-builders-llc-0694-1000000000":
            return httpx.Response(200, text=PROFILE_HTML, headers={"content-type": "text/html"})
        if path == "/us/wi/madison/profile/general-contractor/example-builders-llc-0694-1000000000/complaints":
            return httpx.Response(200, text=COMPLAINTS_HTML, headers={"content-type": "text/html"})
        return httpx.Response(404, text="not found")

    job_id = await db.create_job(source["id"], False)
    await CrawlEngine(source, job_id, transport=httpx.MockTransport(site)).run()
    job = await db.get_job(job_id)
    assert job["status"] == "completed", job
    assert all("/search" not in url for url in requested)

    records = await db.search_records(source_id=source["id"])
    assert records["total"] == 1
    raw = records["items"][0]
    projected = bidder_row(raw, fallback_id=False)
    assert projected["contractor_name"] == "EXAMPLE BUILDERS LLC"
    assert projected["better_business_bureau_complaints"] == "Y"
    assert raw["source_url"] == PROFILE + "/complaints"
    extra = json.loads(raw["extra_json"])
    assert extra["discovery_method"] == "BBB-published business-profile sitemap index"

    comparison = await bidder_db.compare()
    assert comparison["field_changes"] == 1
    proposals = [
        proposal for proposal in await bidder_db.list_proposals()
        if proposal["proposal_type"] == "field_update"
    ]
    assert len(proposals) == 1
    assert proposals[0]["field_name"] == "better_business_bureau_complaints"
    assert proposals[0]["old_value"] == ""
    assert proposals[0]["new_value"] == "Y"


def test_unmapped_state_stays_unknown():
    adapter = BbbSitemapComplaintsAdapter()
    adapter.seed_urls([bidder(state="TX")])
    record, = adapter.finalize_records(complete=True)
    assert record["better_business_bureau_complaints"] == ""
    assert record["source_url"] == BBB_SITEMAP_INDEX
    assert record["extra"]["unsupported_selected_states"] == ["TX"]


def test_registered_bbb_adapter_is_sitemap_adapter():
    adapter = adapter_for_url(BBB_SITEMAP_INDEX)
    assert isinstance(adapter, BbbSitemapComplaintsAdapter)
    assert adapter.canonical_start_url == BBB_SITEMAP_INDEX
