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
    row = {"id": "1", "contractor_name": "Example Builders LLC", "address_1": "12 Oak Rd",
           "city": "Madison", "state": "WI", "zip": "53703", "uei": "ABCDEF123456", "cage_code": "1A2B3"}
    row.update(values)
    return row


def test_manifest_deterministically_selects_latest_public_v2():
    selected = select_manifest_artifact({"files": [
        {"path": "Exclusions/Historical/", "fileName": "old.zip", "publicationTimestamp": "2026-09-21T00:00:00Z", "size": 1, "url": "https://sam.gov/old.zip"},
        {"path": "Exclusions/Public V2/", "fileName": "a.zip", "publicationTimestamp": "2026-09-20T00:00:00Z", "size": 1, "contentType": "application/zip", "url": "https://sam.gov/a.zip"},
        {"path": "Exclusions/Public V2/", "fileName": "b.zip", "publicationTimestamp": "2026-09-21T00:00:00Z", "size": 1, "contentType": "application/zip", "url": "https://sam.gov/b.zip"}]})
    assert selected["file_name"] == "b.zip"
    assert SamExclusionsAdapter.canonical_start_url == SAM_DATA_SERVICES_ENDPOINT


def test_fixture_streams_and_matches_multiple_exclusions_without_creating_unrelated_master(tmp_path):
    adapter = SamExclusionsAdapter()
    assert adapter.seed_urls([bidder()]) == [SAM_DATA_SERVICES_ENDPOINT]
    adapter.process_artifact(archive(tmp_path), {"file_name": "public-v2.zip", "schema_version": "Public V2"})
    record, = adapter.finalize_records(True)
    assert record["state_federal_debarment"] == "Y"
    assert len(record["extra"]["confirmed_exclusions"]) == 2
    assert all(item["exclusion_identifier"] != "EX-200" for item in record["extra"]["confirmed_exclusions"])


def test_clean_and_incomplete_scans_are_positive_only(tmp_path):
    adapter = SamExclusionsAdapter(); adapter.seed_urls([bidder(uei="OTHER", cage_code="OTHER", contractor_name="No Match LLC")])
    adapter.process_artifact(archive(tmp_path), {"file_name": "public-v2.zip"})
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


def test_redirect_host_validation():
    assert SamExclusionsAdapter._validated_download_url("https://files.sam.gov/public.zip")
    with pytest.raises(ValueError, match="unapproved"):
        SamExclusionsAdapter._validated_download_url("https://evil.example/public.zip")
