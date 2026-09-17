from app.bbb_sitemap_adapter import (
    BBB_SITEMAP_INDEX,
    BbbSitemapComplaintsAdapter,
    _expanded_sitemap_numbers,
    _resilient_complaint_summary,
)


def bidder(**changes):
    row = {
        "_master_id": 1,
        "id": "001",
        "contractor_name": "Example Builders LLC",
        "related_companies": "",
        "address_1": "123 Main St",
        "city": "Madison",
        "state": "WI",
        "zip": "53703",
        "better_business_bureau_complaints": "",
    }
    row.update(changes)
    return row


def xml_index(numbers=range(1, 576)):
    body = "".join(
        f"<sitemap><loc>https://www.bbb.org/sitemap-business-profiles-{number}.xml</loc></sitemap>"
        for number in numbers
    )
    return f"<sitemapindex>{body}</sitemapindex>"


def xml_urls(urls):
    return "<urlset>" + "".join(f"<url><loc>{url}</loc></url>" for url in urls) + "</urlset>"


PROFILE = "https://www.bbb.org/us/wi/madison/profile/general-contractor/example-builders-llc-0694-1000000000"
SECOND_PROFILE = "https://www.bbb.org/us/wi/madison/profile/electrician/second-electric-llc-0694-2000000000"


def profile_html(name="Example Builders LLC", address="123 Main St", city="Madison", state="WI", zip_code="53703"):
    return f"""
    <html><head><script type="application/ld+json">
    {{"@type":"LocalBusiness","name":"{name}","address":{{"@type":"PostalAddress",
    "streetAddress":"{address}","addressLocality":"{city}","addressRegion":"{state}","postalCode":"{zip_code}"}}}}
    </script></head><body><h1>{name}</h1><p>{address}, {city}, {state} {zip_code}</p></body></html>
    """


POSITIVE = "<html><body><h2>Customer Complaints Summary</h2><p>3 total complaints in the last 3 years.</p><p>1 complaint closed in the last 12 months.</p></body></html>"
ZERO = "<html><body><h2>Customer Complaints Summary</h2><p>0 total complaints in the last 3 years.</p><p>0 complaints closed in the last 12 months.</p></body></html>"
MALFORMED = "<html><body><h2>Customer Complaints Summary</h2><p>Complaints are available below.</p></body></html>"
ALT_LAYOUT = "<html><body><section><strong>Complaints closed in last 3 years: 4</strong><span>Complaints closed in last 12 months: 2</span></section></body></html>"


def prepare(adapter, rows, profile_urls=()):
    adapter.seed_urls(rows)
    adapter.links(xml_index(), BBB_SITEMAP_INDEX)
    numbers = sorted(_expanded_sitemap_numbers("WI"))
    for number in numbers:
        sitemap = f"https://www.bbb.org/sitemap-business-profiles-{number}.xml"
        urls = list(profile_urls) if number == 327 else []
        adapter.links(xml_urls(urls), sitemap)
    return adapter


def test_complaint_parser_supports_explicit_alternate_layout_but_not_malformed():
    parsed = _resilient_complaint_summary(ALT_LAYOUT, PROFILE + "/complaints")
    assert parsed["summary_parsed"] is True
    assert parsed["total_complaints_3y"] == 4
    assert parsed["closed_complaints_12m"] == 2

    malformed = _resilient_complaint_summary(MALFORMED, PROFILE + "/complaints")
    assert malformed["summary_parsed"] is False
    assert malformed["total_complaints_3y"] is None


def test_exact_positive_identity_records_match_reason_and_positive():
    adapter = prepare(BbbSitemapComplaintsAdapter(), [bidder()], [PROFILE])
    complaint_url, = adapter.links(profile_html(), PROFILE)
    adapter.links(POSITIVE, complaint_url)
    record, = adapter.finalize_records(True)
    assert record["better_business_bureau_complaints"] == "Y"
    assert record["extra"]["complete_aggregate"] is True
    assert record["extra"]["contractor_collection_status"] == "verified_positive"
    identity = record["extra"]["matched_profiles"][0]["identity_evidence"]
    assert identity["name_exact"] is True
    assert identity["location_corroborated"] is True


def test_explicit_zero_requires_exact_verified_profile_and_successful_summary():
    adapter = prepare(BbbSitemapComplaintsAdapter(), [bidder()], [PROFILE])
    complaint_url, = adapter.links(profile_html(), PROFILE)
    adapter.links(ZERO, complaint_url)
    record, = adapter.finalize_records(True)
    assert record["better_business_bureau_complaints"] == "N"
    assert record["extra"]["contractor_collection_status"] == "verified_zero"
    assert record["extra"]["negative_inference_from_absence"] is False


def test_similar_name_and_location_mismatch_are_unresolved_not_negative():
    adapter = prepare(BbbSitemapComplaintsAdapter(), [bidder()], [PROFILE])
    assert adapter.links(profile_html(name="Example Builder LLC"), PROFILE) == []
    record, = adapter.finalize_records(True)
    assert record["better_business_bureau_complaints"] == ""
    assert record["extra"]["ambiguous_candidates"]
    assert record["extra"]["contractor_collection_status"] == "unresolved_identity"

    adapter = prepare(BbbSitemapComplaintsAdapter(), [bidder()], [PROFILE])
    assert adapter.links(profile_html(city="Orlando", state="FL", zip_code="32801"), PROFILE) == []
    record, = adapter.finalize_records(True)
    assert record["better_business_bureau_complaints"] == ""
    assert record["extra"]["ambiguous_candidates"]


def test_no_candidate_is_unknown_even_after_complete_sitemap_acquisition():
    adapter = prepare(BbbSitemapComplaintsAdapter(), [bidder()], [])
    record, = adapter.finalize_records(True)
    assert record["better_business_bureau_complaints"] == ""
    assert record["extra"]["complete_aggregate"] is True
    assert record["extra"]["contractor_collection_status"] == "no_verified_profile"
    assert "UNKNOWN" in record["extra"]["unknown_reason"]


def test_malformed_complaint_summary_stays_unknown_and_partial():
    adapter = prepare(BbbSitemapComplaintsAdapter(), [bidder()], [PROFILE])
    complaint_url, = adapter.links(profile_html(), PROFILE)
    adapter.links(MALFORMED, complaint_url)
    record, = adapter.finalize_records(True)
    assert record["better_business_bureau_complaints"] == ""
    assert record["extra"]["complete_aggregate"] is False
    assert record["extra"]["contractor_collection_status"] == "partial"
    assert record["extra"]["matched_profiles"][0]["summary_parsed"] is False


def test_blocked_profile_and_partial_sitemap_are_explicit_unknown():
    adapter = prepare(BbbSitemapComplaintsAdapter(), [bidder()], [PROFILE])
    adapter.record_page_error(PROFILE, "HTTP 403")
    record, = adapter.finalize_records(False)
    assert record["better_business_bureau_complaints"] == ""
    assert record["extra"]["contractor_collection_status"] == "blocked"
    assert record["extra"]["page_failures"][0]["stage"] == "profile"

    adapter = BbbSitemapComplaintsAdapter()
    adapter.seed_urls([bidder()])
    adapter.links(xml_index(), BBB_SITEMAP_INDEX)
    numbers = sorted(_expanded_sitemap_numbers("WI"))
    for number in numbers[:-1]:
        adapter.links(xml_urls([]), f"https://www.bbb.org/sitemap-business-profiles-{number}.xml")
    record, = adapter.finalize_records(False)
    assert record["better_business_bureau_complaints"] == ""
    assert record["extra"]["complete_aggregate"] is False
    assert record["extra"]["contractor_collection_status"] == "partial"


def test_one_failed_contractor_does_not_poison_verified_zero_for_another():
    first = bidder(_master_id=1, id="001")
    second = bidder(
        _master_id=2,
        id="002",
        contractor_name="Second Electric LLC",
        address_1="500 State St",
    )
    adapter = prepare(BbbSitemapComplaintsAdapter(), [first, second], [PROFILE, SECOND_PROFILE])
    adapter.record_page_error(PROFILE, "Explicit access challenge detected")
    complaint_url, = adapter.links(
        profile_html(name="Second Electric LLC", address="500 State St"),
        SECOND_PROFILE,
    )
    adapter.links(ZERO, complaint_url)
    records = {record["bidder_id"]: record for record in adapter.finalize_records(False)}
    assert records["001"]["better_business_bureau_complaints"] == ""
    assert records["001"]["extra"]["contractor_collection_status"] == "blocked"
    assert records["002"]["better_business_bureau_complaints"] == "N"
    assert records["002"]["extra"]["complete_aggregate"] is True
    assert records["002"]["extra"]["global_scan_complete"] is False
