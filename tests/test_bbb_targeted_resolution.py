import json

from app.bbb_sitemap_adapter import BBB_SITEMAP_INDEX
from app.bbb_targeted_adapter import BbbTargetedProfileAdapter
from app.bidder_schema import BIDDER_COLUMNS


PROFILE = "https://www.bbb.org/us/wi/madison/profile/general-contractor/example-builders-llc-0694-1000000000"
PROFILE_HTML = """
<html><head><script type="application/ld+json">
{"@type":"LocalBusiness","name":"Example Builders LLC","telephone":"608-555-0100",
"address":{"@type":"PostalAddress","streetAddress":"123 Main St","addressLocality":"Madison",
"addressRegion":"WI","postalCode":"53703"}}
</script></head><body><h1>Example Builders LLC</h1>
<dl><dt>BBB Rating</dt><dd>A+</dd><dt>Type of Entity</dt><dd>Limited Liability Company</dd></dl>
<p>123 Main St, Madison, WI 53703</p></body></html>
"""
COMPLAINTS_HTML = """
<html><body><h2>Customer Complaints Summary</h2>
<p>2 total complaints in the last 3 years.</p><p>1 complaint closed in the last 12 months.</p>
</body></html>
"""


def bidder(**changes):
    row = {column: "" for column in BIDDER_COLUMNS}
    row.update({
        "id": "synthetic-1",
        "_master_id": "master-1",
        "contractor_name": "EXAMPLE BUILDERS LLC",
        "address_1": "123 Main St",
        "city": "Madison",
        "state": "WI",
        "zip": "53703",
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


def resolve_once(cache_path):
    adapter = BbbTargetedProfileAdapter(cache_path=cache_path, refresh_batch_size=10)
    assert adapter.seed_urls([bidder()]) == [BBB_SITEMAP_INDEX]
    child, = adapter.links(xml_index([1]), BBB_SITEMAP_INDEX)
    assert adapter.links(xml_urls([PROFILE]), child) == [PROFILE]
    assert adapter.links(PROFILE_HTML, PROFILE) == [PROFILE + "/complaints"]
    adapter.links(COMPLAINTS_HTML, PROFILE + "/complaints")
    return adapter


def test_verified_profile_cache_turns_future_runs_into_direct_name_driven_lookup(tmp_path):
    cache_path = tmp_path / "bbb-index.json"
    resolve_once(cache_path)

    restarted = BbbTargetedProfileAdapter(cache_path=cache_path)
    assert restarted.seed_urls([bidder()]) == [PROFILE]
    assert restarted.profile_resolution_cache_hits == {"master-1"}
    assert restarted.state_contexts == {}


def test_master_identity_change_invalidates_verified_profile_resolution(tmp_path):
    cache_path = tmp_path / "bbb-index.json"
    resolve_once(cache_path)

    restarted = BbbTargetedProfileAdapter(cache_path=cache_path)
    assert restarted.seed_urls([bidder(address_1="500 New Address Ave")]) == [BBB_SITEMAP_INDEX]
    assert restarted.profile_resolution_cache_hits == set()
    assert restarted.profile_resolution_cache_misses == {"master-1"}
    assert restarted.profile_resolution_cache_invalidated == {"master-1"}


def test_cached_profile_that_no_longer_verifies_is_evicted_and_falls_back(tmp_path):
    cache_path = tmp_path / "bbb-index.json"
    resolve_once(cache_path)

    restarted = BbbTargetedProfileAdapter(cache_path=cache_path)
    assert restarted.seed_urls([bidder()]) == [PROFILE]
    mismatched = PROFILE_HTML.replace("Example Builders LLC", "Different Builders LLC")
    links = restarted.links(mismatched, PROFILE)
    assert BBB_SITEMAP_INDEX in links
    assert restarted.profile_resolution_cache_invalidated == {"master-1"}
    record, = restarted.finalize_records(complete=False)
    assert record["better_business_bureau_complaints"] == ""
    assert record["extra"]["bbb_lookup_status"] == "incomplete"


def test_profile_snapshot_keeps_current_public_bbb_facts_as_evidence_only(tmp_path):
    adapter = resolve_once(tmp_path / "bbb-index.json")
    record, = adapter.finalize_records(complete=True)
    assert record["better_business_bureau_complaints"] == "Y"
    assert record["extra"]["bbb_lookup_status"] == "matched"
    profile, = record["extra"]["matched_profiles"]
    assert profile["telephone"] == "608-555-0100"
    assert profile["bbb_rating"] == "A+"
    assert profile["type_of_entity"] == "Limited Liability Company"
    assert profile["profile_snapshot_master_authority"] is False
    assert record["extra"]["profile_snapshot_fields_are_evidence_only"] is True


def test_profile_cache_never_stores_complaint_narratives(tmp_path):
    cache_path = tmp_path / "bbb-index.json"
    resolve_once(cache_path)
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    raw = json.dumps(saved).lower()
    assert "complaint narrative" not in raw
    assert saved["verified_profiles"]["master-1"]["profile_url"] == PROFILE
