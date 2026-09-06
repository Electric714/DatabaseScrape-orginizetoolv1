from app.bidder_schema import BIDDER_COLUMNS, bidder_row
from app.extractor import extract_records
from app.normalizer import canonical_record


def test_exact_bidder_database_headers_and_projection():
    values = {
        "id": "1220",
        "contractor_name": '"C" SCHLICHT PLUMBING INC',
        "related_companies": "TIM COLE PAINTING",
        "address_1": "2807 West Vliet St",
        "city": "Milwaukee",
        "state": "WI",
        "zip": "53208",
        "additional_address": "PO Box 100",
        "additional_address_city": "Milwaukee",
        "additional_address_state": "WI",
        "additional_address_zip": "53201",
        "dfi": "Dissolved 12/3/14",
        "wc": "N",
        "wc_date": "8/27/2020",
        "osha_severe_violations": "2",
        "years": "2020; 2022",
        "osha": "Y",
        "state_federal_debarment": "N",
        "mndol_ineligibility": "N",
        "public_works_projects_budget_time_quality_complaint": "N",
        "federal_court": "N",
        "circuit_court": "Y",
        "ccap_show150": "N",
        "environmental_violations": "N",
        "prevailing_wage_violations": "Y",
        "dwd": "N",
        "dwd_substance_abuse_plan": "Y",
        "better_business_bureau_complaints": "N",
        "misc_violations": "N",
        "tax_liability": "N",
    }
    headers = "".join(f"<th>{column}</th>" for column in BIDDER_COLUMNS)
    cells = "".join(f"<td>{values[column]}</td>" for column in BIDDER_COLUMNS)
    html = f"<table><tr>{headers}</tr><tr>{cells}</tr></table>"

    record, = extract_records(html, "https://fixture.test/bidders")
    assert record["company"] == values["contractor_name"]
    assert record["address"] == values["address_1"]
    assert record["location"] == "Milwaukee, WI 53208"
    assert record["extra"]["bidder_fields"]["id"] == "1220"

    projected = bidder_row({**record, "id": 999})
    assert projected["_record_id"] == 999
    assert [projected[column] for column in BIDDER_COLUMNS] == [values[column] for column in BIDDER_COLUMNS]


def test_internal_database_id_is_not_accidentally_captured_as_bidder_id():
    record = canonical_record({
        "id": 42,
        "company": "Example LLC",
        "address": "1 Main St",
        "source_url": "https://fixture.test/example",
    })
    assert "bidder_fields" not in record["extra"]
    projected = bidder_row({**record, "id": 42})
    assert projected["id"] == 42
    assert projected["_record_id"] == 42
