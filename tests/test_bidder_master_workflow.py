import csv
import io

import httpx
import pytest

from app import bidder_master as bm, database as db, main
from app.bidder_schema import BIDDER_COLUMNS, parse_bidder_csv


def make_csv(rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=BIDDER_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def baseline_row(**changes):
    row = {column: "" for column in BIDDER_COLUMNS}
    row.update({
        "id": "1001",
        "contractor_name": "Example Builders LLC",
        "address_1": "12 Oak Rd",
        "city": "Madison",
        "state": "WI",
        "zip": "01234",
        "dfi": "N",
        "wc": "N",
        "wc_date": "3/4/2025",
        "osha": "N",
        "state_federal_debarment": "N",
        "federal_court": "N",
        "circuit_court": "N",
        "dwd": "N",
        "better_business_bureau_complaints": "N",
    })
    row.update(changes)
    return row


def test_csv_parser_preserves_exact_schema_and_leading_zero_zip():
    rows, warnings = parse_bidder_csv(make_csv([baseline_row()]))
    assert warnings == []
    assert list(rows[0]) == BIDDER_COLUMNS
    assert rows[0]["zip"] == "01234"
    assert rows[0]["id"] == "1001"


async def test_import_compare_apply_and_dismiss_workflow(database, source):
    imported = await bm.import_rows("old-database.csv", [baseline_row()], [])
    assert imported["rows_inserted"] == 1
    assert imported["master_total"] == 1
    master = await bm.search()
    assert master["items"][0]["zip"] == "01234"

    await db.upsert_record(source["id"], {
        "external_id": "source-example-1",
        "company": "Example Builders LLC",
        "address": "12 Oak Rd",
        "location": "Madison, WI 01234",
        "dfi": "Y",
        "wc": "Y",
        "osha": "Y",
        "source_url": "https://fixture.test/example-builders",
    })
    compared = await bm.compare()
    assert compared["field_changes"] >= 3

    proposals = await bm.list_proposals()
    by_field = {proposal["field_name"]: proposal for proposal in proposals if proposal["proposal_type"] == "field_update"}
    assert by_field["dfi"]["old_value"] == "N"
    assert by_field["dfi"]["new_value"] == "Y"
    assert by_field["wc"]["new_value"] == "Y"
    assert by_field["osha"]["new_value"] == "Y"

    await bm.apply(by_field["dfi"]["id"])
    after = (await bm.search())["items"][0]
    assert after["dfi"] == "Y"
    history = await bm.history(after["_master_id"])
    assert history[0]["field_name"] == "dfi"
    assert history[0]["old_value"] == "N"
    assert history[0]["new_value"] == "Y"

    await bm.dismiss(by_field["wc"]["id"])
    await bm.compare()
    remaining = await bm.list_proposals()
    remaining_fields = [p["field_name"] for p in remaining if p["proposal_type"] == "field_update"]
    assert "dfi" not in remaining_fields
    assert "wc" not in remaining_fields
    assert "osha" in remaining_fields


async def test_new_contractor_becomes_reviewable_then_approved(database, source):
    await bm.import_rows("old.csv", [baseline_row()], [])
    await db.upsert_record(source["id"], {
        "external_id": "new-source-record",
        "company": "Brand New Electric LLC",
        "address": "99 New Ave",
        "location": "Milwaukee, WI 53202",
        "dfi": "Y",
        "source_url": "https://fixture.test/new",
    })
    result = await bm.compare()
    assert result["new_contractors"] == 1
    proposal = next(p for p in await bm.list_proposals() if p["proposal_type"] == "new_record")
    assert proposal["proposed"]["contractor_name"] == "Brand New Electric LLC"
    await bm.apply(proposal["id"])
    search = await bm.search(q="Brand New Electric")
    assert search["total"] == 1
    assert search["items"][0]["dfi"] == "Y"


async def test_bidder_import_compare_and_export_api(database, source):
    payload = make_csv([baseline_row(related_companies="=FORMULA()")])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/bidder/import",
            content=payload,
            headers={"content-type": "text/csv", "x-filename": "Bidder Database.csv"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["master_total"] == 1

        status = (await client.get("/api/bidder/status")).json()
        assert status["master_total"] == 1
        assert status["latest_import"]["filename"] == "Bidder Database.csv"

        master = (await client.get("/api/bidder/master", params={"q": "Example Builders"})).json()
        assert master["total"] == 1
        assert master["items"][0]["zip"] == "01234"

        csv_response = await client.get("/api/bidder/export", params={"format": "csv"})
        exported = list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
        assert list(exported[0]) == BIDDER_COLUMNS
        assert exported[0]["related_companies"] == "'=FORMULA()"
        assert exported[0]["zip"] == "01234"

        json_response = await client.get("/api/bidder/export", params={"format": "json"})
        assert list(json_response.json()[0]) == BIDDER_COLUMNS
        assert json_response.json()[0]["related_companies"] == "=FORMULA()"


async def test_import_rejects_missing_columns(database):
    bad = b"id,contractor_name\n1,Example LLC\n"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/bidder/import",
            content=bad,
            headers={"content-type": "text/csv", "x-filename": "bad.csv"},
        )
        assert response.status_code == 422
        assert "Missing bidder database columns" in response.json()["detail"]
