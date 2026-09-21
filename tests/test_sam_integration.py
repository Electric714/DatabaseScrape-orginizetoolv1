import zipfile
from pathlib import Path

import pytest

from app.sam_adapter import SAM_DATA_SERVICES_ENDPOINT, SamExclusionsAdapter, select_manifest_artifact
from app.sam_extract import SamExtractError, iter_public_v2_rows

FIXTURE = Path(__file__).parent / "fixtures" / "sam_public_v2.csv"


def archive(tmp_path, name="public-v2.csv"):
    target = tmp_path / "public-v2.zip"
    with zipfile.ZipFile(target, "w") as value:
        value.write(FIXTURE, name)
    return target


def bidder(**values):
    row = {
        "id": "1",
        "contractor_name": "Example Builders LLC",
        "address_1": "12 Oak Rd",
        "city": "Madison",
        "state": "WI",
        "zip": "53703",
        "uei": "ABCDEF123456",
        "cage_code": "1A2B3",
    }
    row.update(values)
    return row


def sam_row(**values):
    row = {
        "exclusion_identifier": "EX-X",
        "classification": "Firm",
        "entity_name": "Example Builders LLC",
        "record_status": "Active",
        "active": True,
        "uei": "",
        "cage_code": "",
        "address_1": "12 Oak Road",
        "city": "Madison",
        "state": "WI",
        "zip": "53703",
        "exclusion_type": "Ineligible (Proceedings Completed)",
        "agency": "GSA",
    }
    row.update(values)
    return row


def test_manifest_deterministically_selects_latest_public_v2():
    selected = select_manifest_artifact({"files": [
        {"path": "Exclusions/Historical/", "fileName": "old.zip", "publicationTimestamp": "2026-09-21T00:00:00Z", "size": 1, "url": "https://sam.gov/old.zip"},
        {"path": "Exclusions/Public V2/", "fileName": "a.zip", "publicationTimestamp": "2026-09-20T00:00:00Z", "size": 1, "contentType": "application/zip", "url": "https://sam.gov/a.zip"},
        {"path": "Exclusions/Public V2/", "fileName": "b.zip", "publicationTimestamp": "2026-09-21T00:00:00Z", "size": 1, "contentType": "application/zip", "url": "https://sam.gov/b.zip"},
        {"path": "Exclusions/Public V2/", "fileName": "c.csv", "publicationTimestamp": "2026-09-19T00:00:00Z", "size": 1, "contentType": "text/csv", "url": "https://sam.gov/c.csv"},
    ]})
    assert selected["file_name"] == "b.zip"
    assert SamExclusionsAdapter.canonical_start_url == SAM_DATA_SERVICES_ENDPOINT


def test_fixture_streams_and_matches_multiple_exclusions_without_creating_unrelated_master(tmp_path):
    adapter = SamExclusionsAdapter()
    assert adapter.seed_urls([bidder()]) == [SAM_DATA_SERVICES_ENDPOINT]
    adapter.process_artifact(archive(tmp_path), {"file_name": "public-v2.zip", "schema_version": "Public V2", "extension": ".zip"})
    record, = adapter.finalize_records(True)
    assert record["state_federal_debarment"] == "Y"
    assert len(record["extra"]["confirmed_exclusions"]) == 2
    assert all(item["exclusion_identifier"] != "EX-200" for item in record["extra"]["confirmed_exclusions"])


def test_direct_csv_artifact_is_supported_and_validated(tmp_path):
    direct = tmp_path / "public-v2.csv"
    direct.write_bytes(FIXTURE.read_bytes())
    rows = list(iter_public_v2_rows(direct, max_uncompressed=100000, artifact={"extension": ".csv"}))
    assert len(rows) == 3

    adapter = SamExclusionsAdapter()
    adapter.seed_urls([bidder()])
    adapter.process_artifact(direct, {"file_name": "public-v2.csv", "extension": ".csv"})
    record, = adapter.finalize_records(True)
    assert record["state_federal_debarment"] == "Y"
    assert len(record["extra"]["confirmed_exclusions"]) == 2


def test_exact_name_and_real_location_corroboration_confirms_without_identifier():
    adapter = SamExclusionsAdapter()
    adapter.seed_urls([bidder(uei="", cage_code="")])
    adapter.consume_rows([sam_row(exclusion_identifier="EX-NAME")])
    record, = adapter.finalize_records(True)
    assert record["state_federal_debarment"] == "Y"
    assert record["extra"]["confirmed_exclusions"][0]["matching_basis"].startswith("exact approved name/alias plus")


def test_exact_name_same_city_but_wrong_street_is_manual_review_not_confirmed():
    adapter = SamExclusionsAdapter()
    adapter.seed_urls([bidder(uei="", cage_code="")])
    adapter.consume_rows([sam_row(
        exclusion_identifier="EX-WRONG-ADDRESS",
        address_1="999 Completely Different Street",
        city="Madison",
        state="WI",
        zip="99999",
    )])
    record, = adapter.finalize_records(True)
    assert record["state_federal_debarment"] == ""
    assert not record["extra"]["confirmed_exclusions"]
    assert len(record["extra"]["ambiguous_candidates"]) == 1
    assert "location" in record["extra"]["ambiguous_candidates"][0]["reason"]


def test_unrelated_miami_companies_do_not_become_ambiguous_candidates():
    """Regression for A-1 Duran Roofing being paired with unrelated Miami firms."""
    adapter = SamExclusionsAdapter()
    adapter.seed_urls([bidder(
        contractor_name="A-1 Duran Roofing Inc",
        address_1="8095 NW 64th St",
        city="Miami",
        state="FL",
        zip="33166",
        uei="",
        cage_code="",
    )])
    adapter.consume_rows([
        sam_row(exclusion_identifier="M1", entity_name="A & A Medical Center Corp.", address_1="8231 Northwest 8th St., Suite 510", city="Miami", state="FL", zip="33128"),
        sam_row(exclusion_identifier="M2", entity_name="Florida Wire & Rigging Works, Inc.", address_1="2475 NW 38th St.", city="Miami", state="FL", zip="33142"),
        sam_row(exclusion_identifier="M3", entity_name="ALL EQUIPMENTS SERVICES, INC", address_1="7209 SW 24 STREET", city="Miami", state="FL", zip="33155"),
        sam_row(exclusion_identifier="M4", entity_name="UROLOGY P A", address_1="33 NORTHEAST 4TH ST", city="Miami", state="FL", zip="33101"),
    ])
    record, = adapter.finalize_records(True)
    assert record["state_federal_debarment"] == ""
    assert record["extra"]["confirmed_exclusions"] == []
    assert record["extra"]["ambiguous_candidates"] == []


def test_unrelated_electric_company_does_not_match_abel_electric():
    adapter = SamExclusionsAdapter()
    adapter.seed_urls([bidder(
        contractor_name="ABEL ELECTRIC INC",
        address_1="100 Main Street",
        city="Madison",
        state="WI",
        zip="53703",
        uei="",
        cage_code="",
    )])
    adapter.consume_rows([
        sam_row(exclusion_identifier="E1", entity_name="China Machinery and Electric Equipment Import and Export Company", address_1="", city="", state="", zip=""),
        sam_row(exclusion_identifier="E2", entity_name="West Virginia Electric Corporation", address_1="2011 Pleasant Valley Road", city="Fairmont", state="WV", zip="26554"),
        sam_row(exclusion_identifier="E3", entity_name="Jiangsu Hailan Ship Electric System Technology Co., Ltd.", address_1="No. 17 Wei Fourteenth Rd", city="Nantong", state="", zip=""),
    ])
    record, = adapter.finalize_records(True)
    assert record["extra"]["confirmed_exclusions"] == []
    assert record["extra"]["ambiguous_candidates"] == []


def test_near_exact_name_requires_location_and_remains_manual_review():
    adapter = SamExclusionsAdapter()
    adapter.seed_urls([bidder(contractor_name="Example Builders LLC", uei="", cage_code="")])
    adapter.consume_rows([sam_row(
        exclusion_identifier="TYPO",
        entity_name="Example Builder LLC",
        address_1="12 Oak Road",
        city="Madison",
        state="WI",
        zip="53703",
    )])
    record, = adapter.finalize_records(True)
    assert record["state_federal_debarment"] == ""
    assert not record["extra"]["confirmed_exclusions"]
    candidates = record["extra"]["ambiguous_candidates"]
    assert len(candidates) == 1
    assert candidates[0]["name_similarity"] >= 0.94
    assert "manual review" in candidates[0]["reason"]


def test_clean_and_incomplete_scans_are_positive_only(tmp_path):
    adapter = SamExclusionsAdapter()
    adapter.seed_urls([bidder(uei="OTHER", cage_code="OTHER", contractor_name="No Match LLC")])
    adapter.process_artifact(archive(tmp_path), {"file_name": "public-v2.zip", "extension": ".zip"})
    assert adapter.finalize_records(True)[0]["state_federal_debarment"] == ""
    adapter.extract_complete = False
    incomplete = adapter.finalize_records(False)[0]
    assert incomplete["state_federal_debarment"] == ""
    assert incomplete["extra"]["complete_aggregate"] is False


def test_zip_traversal_and_duplicate_headers_are_rejected(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as value:
        value.writestr("../escape.csv", FIXTURE.read_bytes())
    with pytest.raises(SamExtractError, match="unsafe path"):
        list(iter_public_v2_rows(bad, max_uncompressed=100000))

    duplicate = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(duplicate, "w") as value:
        value.writestr("x.csv", "Exclusion ID,Classification,Name,Name,Record Status\n1,Firm,A,A,Active\n")
    with pytest.raises(SamExtractError, match="duplicate headers"):
        list(iter_public_v2_rows(duplicate, max_uncompressed=100000))


def test_malformed_direct_csv_is_rejected(tmp_path):
    malformed = tmp_path / "bad.csv"
    malformed.write_text("Exclusion ID,Classification,Name,Record Status\n1,Firm,Only three columns\n", encoding="utf-8")
    with pytest.raises(SamExtractError, match="malformed or truncated"):
        list(iter_public_v2_rows(malformed, max_uncompressed=100000, artifact={"extension": ".csv"}))


def test_redirect_host_validation():
    assert SamExclusionsAdapter._validated_download_url("https://files.sam.gov/public.zip")
    with pytest.raises(ValueError, match="unapproved"):
        SamExclusionsAdapter._validated_download_url("https://evil.example/public.zip")
