import json
from urllib.parse import parse_qs, urlsplit

import httpx

from app import bidder_master as bidder_db, database as db, main
from app.bbb_adapter import (
    BBB_MASTER_FIELDS,
    BBB_SEARCH_ENDPOINT,
    BbbComplaintsAdapter,
    _complaint_summary,
    _profile_details,
    _search_candidates,
)
from app.bidder_schema import BIDDER_COLUMNS, bidder_row
from app.crawler import CrawlEngine
from app.models import SourceCreate, SourceUpdate


EXAMPLE_BIDDERS = [
    ("1220", '"C" SCHLICHT PLUMBING INC', "", "2807 West Vliet St", "Milwaukee", "WI", "53208"),
    ("13205", "*PAINTWORKS* LLC", "TIM COLE PAINTING", "11720 W Dearbourn Ave", "Wauwatosa", "WI", "53226"),
    ("11391", "#1 TRANSPORTATION LLC", "", "1701 W 6th St", "Racine", "WI", "53404"),
    ("14566", "10330 EXCEEDING LLC", "", "8200 W Brown Deer Rd Ste 301", "Milwaukee", "WI", "53223"),
    ("5596", "1130 PARTNERS INC", "P.D.C. MIDWEST INC", "1130 James Dr, Suite 106", "Hartland", "WI", "53029"),
    ("11950", "117 INSTALLATIONS", "", "13340 Old Pleasant Valley Rd", "Middleburg Heights", "OH", "44130"),
    ("3", "12 GAUGE CONSTRUCTION LLC", "", "2013 S Stoughton Rd", "Madison", "WI", "53716"),
    ("14475", "123 EXTERIORS INC", "", "4600 Maryland Ave", "St Louis", "MO", "63108"),
    ("11299", "1848 CONSTRUCTION INC", "", "3302 Latham Dr", "Madison", "WI", "53713"),
    ("10423", "187 DRYWALL LLC", "", "3310 93rd Curve NE", "Blaine", "MN", "55449"),
    ("3231", "1901 INC", "H&H INDUSTRIES INC", "2801 Syene Rd", "Madison", "WI", "53713"),
    ("11862", "1ST CHANCE CONTRACTING INC", "", "748 Bow Bells Rd", "Oneida", "WI", "54155"),
    ("10422", "5WAY CONTRACTORS INC", "", "1566 Germain Landings", "St Paul", "MN", "55106"),
    ("9060", "8 ACES CONSTRUCTION INC", "", "17542 Chicago Ave", "Lansing", "IL", "60438"),
    ("133", "A-1 DURAN ROOFING INC", "A-1 DURAN ROOFING & INSULATION SERVICES INC", "8095 NW 64th St", "Miami", "FL", "33166"),
    ("62", "A. LAMP CONCRETE CONTRACTORS INC", "", "1900 Wright Blvd", "Schaumburg", "IL", "60193"),
    ("154", "ABATEMENT MANAGEMENT INC", "", "6990 State Route 111", "South Roxana", "IL", "62087"),
    ("161", "ABEL ELECTRIC INC", "", "3385 Belmar Rd", "Green Bay", "WI", "54313"),
    ("160", "ABEL, MIKE", "", "3385 Belmar Rd", "Green Bay", "WI", "54313"),
    ("187", "ACCURATE STEEL INSTALLERS INC", "", "14631 S New Avenue", "Lockport", "IL", "60441"),
    ("275", "AGOUDEMOS, TELEMACHOS", "", "W230S8805 Clark St", "Big Bend", "WI", ""),
    ("292", "ALACRAN CONTRACTING LLC", "", "308 W State St #210", "Rockford", "IL", "61101"),
    ("387", "ALPHA ELECTRIC LLC", "", "350 Business Park Dr", "Sun Prairie", "WI", "53590"),
    ("415", "AMERICAN CONTRACTORS AND ASSOCIATES LLC", "", "5644 Vera Cruz Ave N", "Crystal", "MN", "55429"),
]


def bidder_tuple_to_row(item, **changes):
    bidder_id, name, related, address, city, state, zip_code = item
    row = {column: "" for column in BIDDER_COLUMNS}
    row.update({
        "id": bidder_id,
        "contractor_name": name,
        "related_companies": related,
        "address_1": address,
        "city": city,
        "state": state,
        "zip": zip_code,
        "better_business_bureau_complaints": "N",
    })
    row.update(changes)
    return row


def a_lamp(**changes):
    return bidder_tuple_to_row(EXAMPLE_BIDDERS[15], **changes)


A_LAMP_SEARCH_HTML = """
<html><body>
<main>
  <article class="result">
    <h3><a href="/us/il/northbrook/profile/concrete-contractors/a-lamp-concrete-contractors-0654-90006663">A Lamp Concrete Contractors</a></h3>
    <p>2900 Old Willow Rd, Northbrook, IL 60062-6816</p>
  </article>
  <article class="result">
    <h3><a href="/us/il/schaumburg/profile/paving-contractors/a-lamp-concrete-contractors-0654-28001535">A. Lamp Concrete Contractors</a></h3>
    <p>1900 Wright Blvd, Schaumburg, IL 60193-4587</p>
  </article>
  <article class="result">
    <h3><a href="/us/il/des-plaines/profile/paving-contractors/a-lamp-concrete-contractors-inc-0654-14004084">A. Lamp Concrete Contractors, Inc.</a></h3>
    <p>1308 Rand Road, Des Plaines, IL 60016-3441</p>
  </article>
</main>
</body></html>
"""

A_LAMP_PROFILE_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context":"https://schema.org",
  "@type":"LocalBusiness",
  "name":"A. Lamp Concrete Contractors",
  "telephone":"(847) 891-6000",
  "address":{
    "@type":"PostalAddress",
    "streetAddress":"1900 Wright Blvd",
    "addressLocality":"Schaumburg",
    "addressRegion":"IL",
    "postalCode":"60193-4587"
  }
}
</script>
</head><body>
<h1>A. Lamp Concrete Contractors</h1>
<h2>Overview</h2>
<p>1900 Wright Blvd</p><p>Schaumburg, IL 60193-4587</p>
<p>Alternate Names: A Lamp Concrete</p>
<a href="/us/il/schaumburg/profile/paving-contractors/a-lamp-concrete-contractors-0654-28001535/complaints">Complaints</a>
</body></html>
"""

WRONG_NORTHBROOK_PROFILE = """
<html><head><script type="application/ld+json">
{"@type":"LocalBusiness","name":"A Lamp Concrete Contractors",
 "address":{"@type":"PostalAddress","streetAddress":"2900 Old Willow Rd",
 "addressLocality":"Northbrook","addressRegion":"IL","postalCode":"60062"}}
</script></head><body><h1>A Lamp Concrete Contractors</h1></body></html>
"""

WRONG_DES_PLAINES_PROFILE = """
<html><head><script type="application/ld+json">
{"@type":"LocalBusiness","name":"A. Lamp Concrete Contractors, Inc.",
 "address":{"@type":"PostalAddress","streetAddress":"1308 Rand Road",
 "addressLocality":"Des Plaines","addressRegion":"IL","postalCode":"60016"}}
</script></head><body><h1>A. Lamp Concrete Contractors, Inc.</h1></body></html>
"""

A_LAMP_COMPLAINTS_HTML = """
<html><body>
<h1>A. Lamp Concrete Contractors</h1>
<h2>Customer Complaints Summary</h2>
<ul>
<li>2 total complaints in the last 3 years.</li>
<li>1 complaint closed in the last 12 months.</li>
</ul>
<section class="complaint">
<h3>Initial Complaint</h3>
<p>Date: 08/07/2026</p>
<p>Type: Service or Repair Issues</p>
<p>Status: Answered</p>
<p>THIS CONSUMER NARRATIVE MUST NEVER BE RETAINED BY OUR ADAPTER.</p>
</section>
<section class="complaint">
<h3>Initial Complaint</h3>
<p>Date: 03/11/2025</p>
<p>Type: Billing Issues</p>
<p>Status: Resolved</p>
<p>SECOND PRIVATE-LIKE NARRATIVE SHOULD ALSO BE DISCARDED.</p>
</section>
</body></html>
"""

ZERO_COMPLAINTS_HTML = """
<html><body>
<h2>Customer Complaints Summary</h2>
<p>0 total complaints in the last 3 years.</p>
<p>0 complaints closed in the last 12 months.</p>
</body></html>
"""


def test_all_24_example_bidders_generate_targeted_queries_only_from_master_rows():
    adapter = BbbComplaintsAdapter()
    rows = [bidder_tuple_to_row(item) for item in EXAMPLE_BIDDERS]
    urls = adapter.seed_urls(rows)

    assert len(rows) == 24
    # Four rows in the supplied example have a related-company alias.
    assert len(urls) == 28
    assert all(urlsplit(url).hostname == "www.bbb.org" for url in urls)
    assert all(urlsplit(url).path == "/search" for url in urls)

    terms = [parse_qs(urlsplit(url).query)["find_text"][0] for url in urls]
    locations = [parse_qs(urlsplit(url).query).get("find_loc", [""])[0] for url in urls]
    assert 'C SCHLICHT PLUMBING INC' in terms
    assert "PAINTWORKS LLC" in terms
    assert "TIM COLE PAINTING" in terms
    assert "1 TRANSPORTATION LLC" in terms
    assert "A-1 DURAN ROOFING & INSULATION SERVICES INC" in terms
    assert "Madison, WI 53716" in locations
    assert "Schaumburg, IL 60193" in locations


def test_search_parser_extracts_profile_candidates_without_css_class_dependency():
    candidates = _search_candidates(
        A_LAMP_SEARCH_HTML,
        "https://www.bbb.org/search?find_country=USA&find_text=A+LAMP+CONCRETE+CONTRACTORS+INC&find_loc=Schaumburg%2C+IL+60193&page=1",
    )
    assert len(candidates) == 3
    by_name = {candidate["name"]: candidate for candidate in candidates}
    assert by_name["A. Lamp Concrete Contractors"]["city"] == "Schaumburg"
    assert by_name["A. Lamp Concrete Contractors"]["state"] == "IL"
    assert by_name["A. Lamp Concrete Contractors"]["zip"].startswith("60193")
    assert by_name["A. Lamp Concrete Contractors"]["profile_url"].endswith("0654-28001535")


def test_search_parser_has_embedded_json_fallback_for_frontend_revisions():
    html = """
    <html><body><div id="root"></div>
    <script type="application/json">
    {"searchResult":{"results":[{"businessName":"Alpha Electric, LLC",
      "address":"350 Business Park Dr","city":"Sun Prairie","state":"WI",
      "postalcode":"53590",
      "profileUrl":"https://www.bbb.org/us/wi/sun-prairie/profile/electrician/alpha-electric-llc-0694-1000047349"}]}}
    </script></body></html>
    """
    candidates = _search_candidates(html, BBB_SEARCH_ENDPOINT)
    assert len(candidates) == 1
    assert candidates[0]["name"] == "Alpha Electric, LLC"
    assert candidates[0]["address"] == "350 Business Park Dr"
    assert candidates[0]["zip"] == "53590"


def test_profile_and_complaint_parsers_capture_only_needed_evidence():
    profile = _profile_details(
        A_LAMP_PROFILE_HTML,
        "https://www.bbb.org/us/il/schaumburg/profile/paving-contractors/a-lamp-concrete-contractors-0654-28001535",
    )
    assert profile["name"] == "A. Lamp Concrete Contractors"
    assert profile["address"] == "1900 Wright Blvd"
    assert profile["city"] == "Schaumburg"
    assert profile["state"] == "IL"
    assert profile["zip"].startswith("60193")

    summary = _complaint_summary(
        A_LAMP_COMPLAINTS_HTML,
        profile["profile_url"] + "/complaints",
    )
    assert summary["total_complaints_3y"] == 2
    assert summary["closed_complaints_12m"] == 1
    assert summary["summary_parsed"] is True
    assert {row["status"] for row in summary["recent_complaint_metadata"]} == {"Answered", "Resolved"}
    serialized = json.dumps(summary)
    assert "CONSUMER NARRATIVE" not in serialized
    assert "PRIVATE-LIKE NARRATIVE" not in serialized


def test_a_lamp_multiple_bbb_profiles_are_disambiguated_by_csv_location():
    adapter = BbbComplaintsAdapter()
    search_url = adapter.seed_urls([a_lamp()])[0]

    profile_links = adapter.links(A_LAMP_SEARCH_HTML, search_url)
    assert len(profile_links) == 3

    for url in profile_links:
        if "northbrook" in url:
            adapter.links(WRONG_NORTHBROOK_PROFILE, url)
        elif "des-plaines" in url:
            adapter.links(WRONG_DES_PLAINES_PROFILE, url)
        else:
            complaint_links = adapter.links(A_LAMP_PROFILE_HTML, url)
            assert complaint_links == [url + "/complaints"]
            adapter.links(A_LAMP_COMPLAINTS_HTML, complaint_links[0])

    record, = adapter.finalize_records(complete=True)
    assert record["company"] == "A. LAMP CONCRETE CONTRACTORS INC"
    assert record["better_business_bureau_complaints"] == "Y"
    assert len(record["extra"]["matched_profiles"]) == 1
    assert record["extra"]["matched_profiles"][0]["city"] == "Schaumburg"
    assert record["extra"]["matched_profiles"][0]["total_complaints_3y"] == 2
    assert len(record["extra"]["ambiguous_candidates"]) == 2
    assert set(record) & set(BIDDER_COLUMNS) == set(BBB_MASTER_FIELDS)


def test_exact_profile_with_zero_published_complaints_is_negative():
    adapter = BbbComplaintsAdapter()
    search_url = adapter.seed_urls([a_lamp(better_business_bureau_complaints="Y")])[0]
    profile_url = next(url for url in adapter.links(A_LAMP_SEARCH_HTML, search_url) if "schaumburg" in url)
    complaint_url, = adapter.links(A_LAMP_PROFILE_HTML, profile_url)
    adapter.links(ZERO_COMPLAINTS_HTML, complaint_url)

    record, = adapter.finalize_records(complete=True)
    assert record["better_business_bureau_complaints"] == "N"
    assert record["extra"]["matched_profiles"][0]["total_complaints_3y"] == 0


def test_completed_search_with_no_profile_is_negative_but_partial_run_is_unknown():
    adapter = BbbComplaintsAdapter()
    search_url = adapter.seed_urls([bidder_tuple_to_row(EXAMPLE_BIDDERS[3])])[0]
    adapter.links("<html><body><h1>No matching businesses</h1></body></html>", search_url)

    complete_record, = adapter.finalize_records(complete=True)
    assert complete_record["better_business_bureau_complaints"] == "N"

    adapter = BbbComplaintsAdapter()
    adapter.seed_urls([bidder_tuple_to_row(EXAMPLE_BIDDERS[3])])
    assert adapter.finalize_records(complete=False) == []


def test_unrecognized_200_search_page_fails_closed_instead_of_false_negative():
    adapter = BbbComplaintsAdapter()
    search_url = adapter.seed_urls([bidder_tuple_to_row(EXAMPLE_BIDDERS[3])])[0]

    import pytest
    with pytest.raises(ValueError, match="layout was not recognized"):
        adapter.links(
            "<html><body><main><div id='new-search-app'>Loading complete</div></main></body></html>",
            search_url,
        )


def test_wrong_city_candidate_keeps_paginating_until_exact_location_is_found():
    adapter = BbbComplaintsAdapter()
    search_url = adapter.seed_urls([a_lamp()])[0]
    page_one = """
    <html><body>
      <article><a href="/us/il/northbrook/profile/concrete-contractors/a-lamp-concrete-contractors-0654-90006663">A Lamp Concrete Contractors</a>
      <p>2900 Old Willow Rd, Northbrook, IL 60062</p></article>
      <a aria-label="Next page" href="/search?find_country=USA&find_text=A+LAMP+CONCRETE+CONTRACTORS+INC&find_loc=Schaumburg%2C+IL+60193&page=2">Next</a>
    </body></html>
    """
    first_links = adapter.links(page_one, search_url)
    assert any("northbrook" in url for url in first_links)
    page_two_url = next(url for url in first_links if urlsplit(url).path == "/search")
    assert parse_qs(urlsplit(page_two_url).query)["page"] == ["2"]

    page_two = """
    <html><body>
      <article><a href="/us/il/schaumburg/profile/paving-contractors/a-lamp-concrete-contractors-0654-28001535">A. Lamp Concrete Contractors</a>
      <p>1900 Wright Blvd, Schaumburg, IL 60193-4587</p></article>
    </body></html>
    """
    second_links = adapter.links(page_two, page_two_url)
    assert any("schaumburg" in url for url in second_links)


async def test_bbb_full_crawl_proposes_only_bbb_complaint_field(database):
    baseline = a_lamp()
    await bidder_db.import_rows("Bidder Database-Example(2).csv", [baseline], [])

    source_data = SourceCreate(
        name="BBB Business Profiles / Complaints",
        start_url=BBB_SEARCH_ENDPOINT,
        delay_ms=0,
        render_mode="http",
        max_pages=100,
        max_depth=2,
        concurrency=1,
        respect_robots=False,
    ).model_dump(mode="json")
    source = await db.create_source(source_data)

    def site(request):
        path = request.url.path
        if path == "/search":
            raw_query = request.url.query.decode() if isinstance(request.url.query, bytes) else str(request.url.query)
            query = parse_qs(raw_query)
            assert query["find_text"][0]
            assert "Schaumburg" in query["find_loc"][0]
            return httpx.Response(200, text=A_LAMP_SEARCH_HTML, headers={"content-type": "text/html"})
        if "northbrook" in path:
            return httpx.Response(200, text=WRONG_NORTHBROOK_PROFILE, headers={"content-type": "text/html"})
        if "des-plaines" in path:
            return httpx.Response(200, text=WRONG_DES_PLAINES_PROFILE, headers={"content-type": "text/html"})
        if path.endswith("/complaints"):
            return httpx.Response(200, text=A_LAMP_COMPLAINTS_HTML, headers={"content-type": "text/html"})
        if "schaumburg" in path:
            return httpx.Response(200, text=A_LAMP_PROFILE_HTML, headers={"content-type": "text/html"})
        return httpx.Response(404, text="not found")

    job_id = await db.create_job(source["id"], False)
    await CrawlEngine(source, job_id, transport=httpx.MockTransport(site)).run()
    job = await db.get_job(job_id)
    assert job["status"] == "completed", job

    records = await db.search_records(source_id=source["id"])
    assert records["total"] == 1
    projected = bidder_row(records["items"][0], fallback_id=False)
    assert projected["contractor_name"] == baseline["contractor_name"]
    assert projected["better_business_bureau_complaints"] == "Y"

    result = await bidder_db.compare()
    assert result["field_changes"] == 1
    proposals = [
        proposal for proposal in await bidder_db.list_proposals()
        if proposal["proposal_type"] == "field_update"
    ]
    assert {proposal["field_name"] for proposal in proposals} == set(BBB_MASTER_FIELDS)
    assert proposals[0]["old_value"] == "N"
    assert proposals[0]["new_value"] == "Y"

    extra = json.loads(records["items"][0]["extra_json"])
    assert extra["consumer_complaint_text_retained"] is False
    assert "CONSUMER NARRATIVE" not in json.dumps(extra)


async def test_builtin_bbb_source_is_created_once_reuses_legacy_and_is_locked(database):
    legacy = await database.create_source({
        "name": "Old BBB Wisconsin",
        "start_url": "http://www.bbb.org/wisconsin",
        "auto_scan": False,
        "interval_minutes": 60,
        "max_pages": 100,
        "max_depth": 12,
        "concurrency": 4,
        "delay_ms": 350,
        "render_mode": "browser",
        "respect_robots": True,
    })

    first = await main.ensure_builtin_bbb_source()
    second = await main.ensure_builtin_bbb_source()
    sources = await database.list_sources()

    assert first["id"] == legacy["id"] == second["id"]
    assert first["name"] == main.BBB_SOURCE_NAME
    assert first["start_url"] == BBB_SEARCH_ENDPOINT
    assert first["max_depth"] == 2
    assert first["concurrency"] == 1
    assert first["delay_ms"] == 1500
    assert first["render_mode"] == "auto"
    assert not bool(first["respect_robots"])
    assert len([source for source in sources if main._is_bbb_source(source)]) == 1

    try:
        await main.patch_source(first["id"], SourceUpdate(name="Changed BBB"))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("Built-in BBB source should not be editable")

    try:
        await main._delete_source(first["id"])
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("Built-in BBB source should not be removable")

    duplicate = SourceCreate(
        name="Duplicate BBB",
        start_url=BBB_SEARCH_ENDPOINT,
        render_mode="http",
        respect_robots=False,
    )
    try:
        await main.post_source(duplicate)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert "built in" in str(getattr(exc, "detail", "")).lower()
    else:
        raise AssertionError("Generic source setup should not create a second BBB source")
