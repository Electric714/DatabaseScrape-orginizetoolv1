import pytest

from app.violation_tracker_adapter import VT_FIELDS, ViolationTrackerAdapter, offense_field


def contractor(**changes):
    row = {
        "_master_id": 1,
        "id": "001",
        "contractor_name": "Example Builders LLC",
        "related_companies": "",
        "address_1": "123 Main St",
        "city": "Madison",
        "state": "WI",
        "zip": "53703",
    }
    row.update(changes)
    return row


def search_html(rows, count=None):
    count = len(rows) if count is None else count
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td><a href='{row.get('href', '')}'>{row.get('company', '')}</a></td>"
            f"<td>{row.get('parent', '')}</td>"
            "<td>Construction</td>"
            f"<td>{row.get('offense', '')}</td>"
            f"<td>{row.get('year', '2026')}</td>"
            f"<td>{row.get('agency', 'EPA')}</td>"
            f"<td>{row.get('penalty', '$10,000')}</td>"
            "</tr>"
        )
    return f"""
    <html><body><p>{count} Violation Tracker results found</p>
    <table><tr><th>Company</th><th>Current Parent</th><th>Current Parent Industry</th>
    <th>Primary Offense Type</th><th>Year</th><th>Agency</th><th>Penalty Amount</th></tr>
    {''.join(body)}</table></body></html>
    """


def detail_html(company="Example Builders LLC", offense="air pollution violation", **fields):
    values = {
        "Company": company,
        "Current Parent Company": fields.pop("parent", ""),
        "Penalty": fields.pop("penalty", "$10,000"),
        "Year": fields.pop("year", "2026"),
        "Offense Group": fields.pop("offense_group", "environment-related offenses"),
        "Primary Offense": offense,
        "Violation Description": fields.pop("description", "Synthetic public enforcement fixture"),
        "Agency": fields.pop("agency", "EPA"),
        **fields,
    }
    return "<html><body>" + "".join(f"<p>{key}: {value}</p>" for key, value in values.items() if value != "") + "</body></html>"


def run_one(adapter, query, row, detail=None):
    links = adapter.links(search_html([row]), query)
    assert len(links) == 1
    adapter.links(detail or detail_html(offense=row.get("offense", "air pollution violation")), links[0])
    return adapter.finalize_records(True)[0]


def test_valid_exact_positive_and_current_detail_layout():
    adapter = ViolationTrackerAdapter()
    query, = adapter.seed_urls([contractor()])
    row = {
        "company": "Example Builders LLC",
        "offense": "air pollution violation",
        "href": "/violation-tracker/example-builders-1",
    }
    record = run_one(adapter, query, row)
    assert record["environmental_violations"] == "Y"
    assert record["prevailing_wage_violations"] == ""
    assert record["misc_violations"] == ""
    assert record["extra"]["complete_aggregate"] is True
    assert record["extra"]["acquisition_status"] == "verified_positive"
    assert record["extra"]["matched_records"][0]["identity_decision"] == "confirmed_exact_company"


def test_generic_wage_hour_is_not_prevailing_wage_but_explicit_davis_bacon_is():
    assert offense_field("wage and hour violation") is None
    assert offense_field("wage and hour violation", description="Davis-Bacon prevailing wage underpayment") == "prevailing_wage_violations"

    adapter = ViolationTrackerAdapter()
    query, = adapter.seed_urls([contractor()])
    row = {
        "company": "Example Builders LLC",
        "offense": "wage and hour violation",
        "href": "/violation-tracker/example-builders-wage-1",
    }
    record = run_one(
        adapter, query, row,
        detail_html(offense="wage and hour violation", offense_group="labor-related offenses", description="Generic overtime finding"),
    )
    assert all(record[field] == "" for field in VT_FIELDS)
    assert record["extra"]["matched_records"][0]["classification_decision"] == "unsupported_offense"

    adapter = ViolationTrackerAdapter()
    query, = adapter.seed_urls([contractor()])
    row["href"] = "/violation-tracker/example-builders-davis-bacon-1"
    record = run_one(
        adapter, query, row,
        detail_html(offense="wage and hour violation", offense_group="labor-related offenses", description="Davis-Bacon prevailing wage underpayment"),
    )
    assert record["prevailing_wage_violations"] == "Y"


def test_parent_company_result_does_not_prove_selected_contractor():
    adapter = ViolationTrackerAdapter()
    query, = adapter.seed_urls([contractor()])
    html = search_html([{
        "company": "Different Operating Subsidiary Inc",
        "parent": "Example Builders LLC",
        "offense": "air pollution violation",
        "href": "/violation-tracker/different-subsidiary-1",
    }])
    assert adapter.links(html, query) == []
    record, = adapter.finalize_records(True)
    assert all(record[field] == "" for field in VT_FIELDS)
    assert record["extra"]["complete_aggregate"] is True
    assert record["extra"]["acquisition_status"] == "complete_with_unresolved_identity"
    assert "parent-only" in record["extra"]["unresolved_candidates"][0]["reason"]


def test_similar_name_and_location_mismatch_remain_unresolved():
    adapter = ViolationTrackerAdapter()
    query, = adapter.seed_urls([contractor()])
    html = search_html([{
        "company": "Example Builder LLC",
        "offense": "air pollution violation",
        "href": "/violation-tracker/example-builder-1",
    }])
    adapter.links(html, query)
    record, = adapter.finalize_records(True)
    assert all(record[field] == "" for field in VT_FIELDS)
    assert record["extra"]["unresolved_candidates"]

    adapter = ViolationTrackerAdapter()
    query, = adapter.seed_urls([contractor()])
    row = {
        "company": "Example Builders LLC",
        "offense": "air pollution violation",
        "href": "/violation-tracker/example-builders-location-1",
    }
    links = adapter.links(search_html([row]), query)
    adapter.links(
        detail_html(
            offense="air pollution violation",
            Address="999 Wrong Ave", City="Orlando", State="FL", Zip="32801",
        ),
        links[0],
    )
    record, = adapter.finalize_records(True)
    assert record["environmental_violations"] == ""
    assert record["extra"]["unresolved_candidates"]


def test_no_candidate_is_complete_but_never_negative():
    adapter = ViolationTrackerAdapter()
    query, = adapter.seed_urls([contractor()])
    assert adapter.links(search_html([], count=0), query) == []
    record, = adapter.finalize_records(True)
    assert all(record[field] == "" for field in VT_FIELDS)
    assert record["extra"]["complete_aggregate"] is True
    assert record["extra"]["acquisition_status"] == "complete_no_supported_positive"


def test_layout_change_and_access_failure_fail_closed():
    adapter = ViolationTrackerAdapter()
    query, = adapter.seed_urls([contractor()])
    with pytest.raises(ValueError, match="structure unrecognized"):
        adapter.links("<html><body><div id='new-app'>results loaded</div></body></html>", query)

    adapter.record_page_error(query, "HTTP 403")
    record, = adapter.finalize_records(False)
    assert all(record[field] == "" for field in VT_FIELDS)
    assert record["extra"]["complete_aggregate"] is False
    assert record["extra"]["acquisition_status"] == "blocked"
    assert record["extra"]["page_failures"][0]["category"] == "blocked"


def test_detail_layout_change_is_unknown():
    adapter = ViolationTrackerAdapter()
    query, = adapter.seed_urls([contractor()])
    links = adapter.links(search_html([{
        "company": "Example Builders LLC",
        "offense": "air pollution violation",
        "href": "/violation-tracker/example-builders-layout-1",
    }]), query)
    with pytest.raises(ValueError, match="detail layout unverified"):
        adapter.links("<html><body><article>Redesigned record with no labels</article></body></html>", links[0])
    adapter.record_page_error(links[0], "detail layout unverified")
    record, = adapter.finalize_records(False)
    assert record["environmental_violations"] == ""
    assert record["extra"]["complete_aggregate"] is False


def test_partial_search_preserves_verified_positive_without_claiming_complete():
    adapter = ViolationTrackerAdapter()
    query, = adapter.seed_urls([contractor()])
    row = {
        "company": "Example Builders LLC",
        "offense": "air pollution violation",
        "href": "/violation-tracker/example-builders-partial-1",
    }
    links = adapter.links(search_html([row], count=2), query)
    adapter.links(detail_html(), links[0])
    record, = adapter.finalize_records(False)
    assert record["environmental_violations"] == "Y"
    assert record["extra"]["complete_aggregate"] is False
    assert record["extra"]["acquisition_status"] == "partial"
    only_status = next(iter(record["extra"]["search_status"].values()))
    assert only_status["complete"] is False


def test_one_contractor_failure_does_not_poison_another():
    first = contractor(_master_id=1, id="001")
    second = contractor(
        _master_id=2,
        id="002",
        contractor_name="Other Electric LLC",
        address_1="500 State St",
    )
    adapter = ViolationTrackerAdapter()
    queries = adapter.seed_urls([first, second])
    first_query = next(q for q in queries if "Example" in q or "example" in q)
    second_query = next(q for q in queries if "Other" in q or "other" in q)
    adapter.record_page_error(first_query, "Explicit access challenge detected")
    links = adapter.links(search_html([{
        "company": "Other Electric LLC",
        "offense": "government contracting violation",
        "href": "/violation-tracker/other-electric-1",
    }]), second_query)
    adapter.links(
        detail_html(company="Other Electric LLC", offense="government contracting violation", offense_group="government-contracting-related offenses"),
        links[0],
    )
    records = {record["bidder_id"]: record for record in adapter.finalize_records(False)}
    assert all(records["001"][field] == "" for field in VT_FIELDS)
    assert records["001"]["extra"]["acquisition_status"] == "blocked"
    assert records["002"]["misc_violations"] == "Y"
    assert records["002"]["extra"]["complete_aggregate"] is True
