import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from typing import Any

from . import database as db
from .bidder_schema import (
    BIDDER_COLUMNS,
    BIDDER_DB_COLUMNS,
    bidder_master_row,
    bidder_row,
    bidder_values_equal,
    normalize_match_text,
)


def _db_values(row: dict[str, Any]) -> dict[str, str]:
    return {
        "bidder_id": str(row.get("id") or "").strip(),
        **{column: str(row.get(column) or "").strip() for column in BIDDER_COLUMNS[1:]},
    }


def _address_norm(row: dict[str, Any]) -> str:
    return normalize_match_text(
        " ".join(str(row.get(field) or "") for field in ("address_1", "city", "state", "zip"))
    )


def _master_external(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return bidder_master_row(dict(row))


def _find_match(conn: sqlite3.Connection, incoming: dict[str, Any]) -> tuple[sqlite3.Row | None, str]:
    bidder_id = str(incoming.get("id") or "").strip()
    if bidder_id:
        row = conn.execute("SELECT * FROM bidder_master WHERE bidder_id=?", (bidder_id,)).fetchone()
        if row:
            return row, "bidder_id"

    contractor_norm = normalize_match_text(incoming.get("contractor_name"))
    if not contractor_norm:
        return None, "no_contractor_name"

    candidates = conn.execute(
        "SELECT * FROM bidder_master WHERE contractor_norm=? ORDER BY pk", (contractor_norm,)
    ).fetchall()
    if len(candidates) == 1:
        return candidates[0], "contractor_name"
    if len(candidates) > 1:
        address_norm = _address_norm(incoming)
        if address_norm:
            exact = [row for row in candidates if row["address_norm"] == address_norm]
            if len(exact) == 1:
                return exact[0], "contractor_name_and_address"
        return None, "ambiguous_contractor_name"
    return None, "not_found"


def _insert_master(conn: sqlite3.Connection, row: dict[str, Any], import_id: int | None = None) -> int:
    values = _db_values(row)
    now = db.utcnow()
    columns = [
        *BIDDER_DB_COLUMNS,
        "contractor_norm",
        "address_norm",
        "source_import_id",
        "created_at",
        "updated_at",
    ]
    payload = [
        *[values[column] for column in BIDDER_DB_COLUMNS],
        normalize_match_text(values["contractor_name"]),
        _address_norm(row),
        import_id,
        now,
        now,
    ]
    cur = conn.execute(
        f"INSERT INTO bidder_master({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        payload,
    )
    return int(cur.lastrowid)


def _import_rows(filename: str, rows: list[dict[str, str]], warnings: list[str]) -> dict[str, Any]:
    inserted = updated = unchanged = 0
    now = db.utcnow()
    with closing(db.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """INSERT INTO bidder_imports(filename,imported_at,rows_total,warnings_json)
               VALUES (?,?,?,?)""",
            (filename or "bidder-database.csv", now, len(rows), json.dumps(warnings)),
        )
        import_id = int(cur.lastrowid)

        for raw in rows:
            incoming = {column: str(raw.get(column) or "").strip() for column in BIDDER_COLUMNS}
            existing, _ = _find_match(conn, incoming)
            if existing is None:
                try:
                    _insert_master(conn, incoming, import_id)
                    inserted += 1
                    continue
                except sqlite3.IntegrityError:
                    if not incoming["id"]:
                        raise
                    existing = conn.execute(
                        "SELECT * FROM bidder_master WHERE bidder_id=?", (incoming["id"],)
                    ).fetchone()
                    if existing is None:
                        raise

            values = _db_values(incoming)
            changed = any(str(existing[column] or "") != values[column] for column in BIDDER_DB_COLUMNS)
            if changed:
                assignments = ",".join(f"{column}=?" for column in BIDDER_DB_COLUMNS)
                conn.execute(
                    f"""UPDATE bidder_master SET {assignments},contractor_norm=?,address_norm=?,
                        source_import_id=?,updated_at=? WHERE pk=?""",
                    (
                        *[values[column] for column in BIDDER_DB_COLUMNS],
                        normalize_match_text(values["contractor_name"]),
                        _address_norm(incoming),
                        import_id,
                        now,
                        existing["pk"],
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    "UPDATE bidder_master SET source_import_id=? WHERE pk=?",
                    (import_id, existing["pk"]),
                )
                unchanged += 1

        conn.execute(
            """UPDATE bidder_imports SET rows_inserted=?,rows_updated=?,rows_unchanged=?
               WHERE id=?""",
            (inserted, updated, unchanged, import_id),
        )
        total = conn.execute("SELECT COUNT(*) FROM bidder_master").fetchone()[0]
        conn.commit()

    return {
        "import_id": import_id,
        "filename": filename,
        "rows_total": len(rows),
        "rows_inserted": inserted,
        "rows_updated": updated,
        "rows_unchanged": unchanged,
        "warnings": warnings,
        "master_total": total,
    }


async def import_rows(filename: str, rows: list[dict[str, str]], warnings: list[str]) -> dict[str, Any]:
    return await asyncio.to_thread(_import_rows, filename, rows, warnings)


def _status() -> dict[str, Any]:
    with closing(db.connect()) as conn:
        latest = conn.execute("SELECT * FROM bidder_imports ORDER BY id DESC LIMIT 1").fetchone()
        total = conn.execute("SELECT COUNT(*) FROM bidder_master").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM bidder_proposals WHERE status='pending'").fetchone()[0]
        source_records = conn.execute("SELECT COUNT(*) FROM records WHERE active=1").fetchone()[0]
        latest_dict = dict(latest) if latest else None
        if latest_dict:
            latest_dict["warnings"] = json.loads(latest_dict.pop("warnings_json") or "[]")
        return {
            "master_total": total,
            "pending_updates": pending,
            "active_source_records": source_records,
            "latest_import": latest_dict,
        }


async def status() -> dict[str, Any]:
    return await asyncio.to_thread(_status)


def _field_columns(field: str) -> list[str]:
    if field == "all":
        return BIDDER_DB_COLUMNS
    if field == "contractor":
        return ["bidder_id", "contractor_name", "related_companies"]
    if field == "address":
        return [
            "address_1", "city", "state", "zip", "additional_address",
            "additional_address_city", "additional_address_state", "additional_address_zip",
        ]
    if field == "compliance":
        return BIDDER_DB_COLUMNS[11:]
    if field in BIDDER_COLUMNS:
        return ["bidder_id" if field == "id" else field]
    raise ValueError("Unknown bidder search field")


def _search(q: str, field: str, limit: int, offset: int) -> dict[str, Any]:
    columns = _field_columns(field)
    clauses = ["1=1"]
    params: list[Any] = []
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append("(" + " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in columns) + ")")
        params.extend([like] * len(columns))
    where = " AND ".join(clauses)
    with closing(db.connect()) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM bidder_master WHERE {where}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT * FROM bidder_master WHERE {where}
                ORDER BY contractor_name COLLATE NOCASE,pk LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        return {"total": total, "items": [_master_external(row) for row in rows]}


async def search(q: str = "", field: str = "all", limit: int = 100, offset: int = 0) -> dict[str, Any]:
    return await asyncio.to_thread(_search, q, field, limit, offset)


async def all_rows(q: str = "", field: str = "all") -> list[dict[str, Any]]:
    result = await search(q=q, field=field, limit=250000, offset=0)
    if result["total"] > len(result["items"]):
        raise ValueError("Export exceeds 250000 bidder rows; narrow the search.")
    return result["items"]


def _signature(master_pk: int | None, source_record_id: int | None, proposal_type: str,
               field_name: str | None, new_value: str, proposed_json: str = "") -> str:
    raw = json.dumps(
        [master_pk, source_record_id, proposal_type, field_name or "", new_value, proposed_json],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _was_dismissed(conn: sqlite3.Connection, signature: str) -> bool:
    row = conn.execute(
        "SELECT status FROM bidder_proposals WHERE signature=? ORDER BY id DESC LIMIT 1", (signature,)
    ).fetchone()
    return bool(row and row["status"] == "dismissed")


def _compare() -> dict[str, Any]:
    created = field_changes = new_contractors = ambiguous = dismissed_skipped = 0
    with closing(db.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM bidder_proposals WHERE status='pending'")
        source_rows = conn.execute(
            """SELECT r.*,s.name AS source_name FROM records r
               JOIN sources s ON s.id=r.source_id
               WHERE r.active=1 ORDER BY r.id"""
        ).fetchall()

        for raw_source in source_rows:
            source = dict(raw_source)
            incoming = bidder_row(source, fallback_id=False)
            if not incoming.get("contractor_name"):
                continue

            master, reason = _find_match(conn, incoming)
            source_record_id = int(source["id"])
            source_name = str(source.get("source_name") or "")
            source_url = str(source.get("source_url") or "")
            detected = db.utcnow()

            if master is None:
                proposal_type = "ambiguous" if reason == "ambiguous_contractor_name" else "new_record"
                payload = json.dumps(
                    {column: incoming.get(column, "") for column in BIDDER_COLUMNS}, ensure_ascii=False
                )
                sig = _signature(None, source_record_id, proposal_type, None, "", payload)
                if _was_dismissed(conn, sig):
                    dismissed_skipped += 1
                    continue
                conn.execute(
                    """INSERT INTO bidder_proposals
                       (signature,source_record_id,proposal_type,proposed_json,source_name,source_url,
                        match_reason,status,detected_at)
                       VALUES (?,?,?,?,?,?,?,'pending',?)""",
                    (sig, source_record_id, proposal_type, payload, source_name, source_url, reason, detected),
                )
                created += 1
                if proposal_type == "ambiguous":
                    ambiguous += 1
                else:
                    new_contractors += 1
                continue

            current = _master_external(master)
            for field in BIDDER_COLUMNS:
                new_value = str(incoming.get(field) or "").strip()
                if not new_value:
                    continue
                old_value = str(current.get(field) or "").strip()
                if bidder_values_equal(old_value, new_value):
                    continue
                sig = _signature(master["pk"], source_record_id, "field_update", field, new_value)
                if _was_dismissed(conn, sig):
                    dismissed_skipped += 1
                    continue
                conn.execute(
                    """INSERT INTO bidder_proposals
                       (signature,master_pk,source_record_id,proposal_type,field_name,old_value,new_value,
                        source_name,source_url,match_reason,status,detected_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?)""",
                    (
                        sig, master["pk"], source_record_id, "field_update", field, old_value, new_value,
                        source_name, source_url, reason, detected,
                    ),
                )
                created += 1
                field_changes += 1
        conn.commit()

    return {
        "pending_created": created,
        "field_changes": field_changes,
        "new_contractors": new_contractors,
        "ambiguous_matches": ambiguous,
        "dismissed_not_repeated": dismissed_skipped,
    }


async def compare() -> dict[str, Any]:
    return await asyncio.to_thread(_compare)


def _list_proposals(status_filter: str, limit: int) -> list[dict[str, Any]]:
    if status_filter not in {"pending", "applied", "dismissed", "superseded", "all"}:
        raise ValueError("Unknown proposal status")
    where = "" if status_filter == "all" else "WHERE p.status=?"
    params: tuple[Any, ...] = () if status_filter == "all" else (status_filter,)
    with closing(db.connect()) as conn:
        rows = conn.execute(
            f"""SELECT p.*,m.contractor_name AS master_contractor,m.bidder_id AS master_bidder_id
                FROM bidder_proposals p LEFT JOIN bidder_master m ON m.pk=p.master_pk
                {where}
                ORDER BY CASE p.proposal_type WHEN 'field_update' THEN 0 WHEN 'new_record' THEN 1 ELSE 2 END,
                         COALESCE(m.contractor_name,''),p.field_name,p.id DESC
                LIMIT ?""",
            (*params, limit),
        ).fetchall()
        items = []
        for raw in rows:
            item = dict(raw)
            try:
                item["proposed"] = json.loads(item.get("proposed_json") or "{}")
            except ValueError:
                item["proposed"] = {}
            items.append(item)
        return items


async def list_proposals(status_filter: str = "pending", limit: int = 500) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_list_proposals, status_filter, limit)


def _refresh_norms(conn: sqlite3.Connection, master_pk: int) -> None:
    row = conn.execute("SELECT * FROM bidder_master WHERE pk=?", (master_pk,)).fetchone()
    if not row:
        return
    external = _master_external(row)
    conn.execute(
        "UPDATE bidder_master SET contractor_norm=?,address_norm=?,updated_at=? WHERE pk=?",
        (
            normalize_match_text(external["contractor_name"]),
            _address_norm(external),
            db.utcnow(),
            master_pk,
        ),
    )


def _apply(proposal_id: int) -> dict[str, Any]:
    with closing(db.connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        proposal = conn.execute("SELECT * FROM bidder_proposals WHERE id=?", (proposal_id,)).fetchone()
        if not proposal:
            raise ValueError("Update proposal not found")
        if proposal["status"] != "pending":
            return dict(proposal)

        now = db.utcnow()
        if proposal["proposal_type"] == "ambiguous":
            raise ValueError(
                "This source record matches multiple contractors. Dismiss it or resolve the contractor manually."
            )

        if proposal["proposal_type"] == "new_record":
            incoming = json.loads(proposal["proposed_json"] or "{}")
            existing, _ = _find_match(conn, incoming)
            if existing:
                conn.execute(
                    "UPDATE bidder_proposals SET status='superseded',resolved_at=? WHERE id=?",
                    (now, proposal_id),
                )
                conn.commit()
                return {"id": proposal_id, "status": "superseded", "master_id": existing["pk"]}

            master_pk = _insert_master(conn, incoming)
            conn.execute(
                """INSERT INTO bidder_master_history
                   (master_pk,field_name,old_value,new_value,source_record_id,source_name,source_url,applied_at)
                   VALUES (?,'__new_record__','',?,?,?,?,?)""",
                (
                    master_pk,
                    json.dumps(incoming, ensure_ascii=False),
                    proposal["source_record_id"],
                    proposal["source_name"],
                    proposal["source_url"],
                    now,
                ),
            )
            conn.execute(
                "UPDATE bidder_proposals SET status='applied',master_pk=?,resolved_at=? WHERE id=?",
                (master_pk, now, proposal_id),
            )
            conn.commit()
            return {"id": proposal_id, "status": "applied", "master_id": master_pk}

        field = str(proposal["field_name"] or "")
        if field not in BIDDER_COLUMNS:
            raise ValueError("Proposal contains an unknown bidder field")
        db_field = "bidder_id" if field == "id" else field
        master = conn.execute("SELECT * FROM bidder_master WHERE pk=?", (proposal["master_pk"],)).fetchone()
        if not master:
            raise ValueError("The matching master contractor no longer exists")
        old_value = str(master[db_field] or "")
        new_value = str(proposal["new_value"] or "")

        if db_field == "bidder_id" and new_value:
            duplicate = conn.execute(
                "SELECT pk FROM bidder_master WHERE bidder_id=? AND pk<>?", (new_value, master["pk"])
            ).fetchone()
            if duplicate:
                raise ValueError("That bidder ID is already assigned to another contractor")

        conn.execute(
            f"UPDATE bidder_master SET {db_field}=?,updated_at=? WHERE pk=?",
            (new_value, now, master["pk"]),
        )
        _refresh_norms(conn, int(master["pk"]))
        conn.execute(
            """INSERT INTO bidder_master_history
               (master_pk,field_name,old_value,new_value,source_record_id,source_name,source_url,applied_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                master["pk"], field, old_value, new_value, proposal["source_record_id"],
                proposal["source_name"], proposal["source_url"], now,
            ),
        )
        conn.execute(
            "UPDATE bidder_proposals SET status='applied',resolved_at=? WHERE id=?",
            (now, proposal_id),
        )
        conn.execute(
            """UPDATE bidder_proposals SET status='superseded',resolved_at=?
               WHERE status='pending' AND master_pk=? AND field_name=? AND id<>?""",
            (now, master["pk"], field, proposal_id),
        )
        conn.commit()
        return {"id": proposal_id, "status": "applied", "master_id": master["pk"], "field": field}


async def apply(proposal_id: int) -> dict[str, Any]:
    return await asyncio.to_thread(_apply, proposal_id)


def _dismiss(proposal_id: int) -> dict[str, Any]:
    with closing(db.connect()) as conn:
        now = db.utcnow()
        conn.execute(
            """UPDATE bidder_proposals SET status='dismissed',resolved_at=?
               WHERE id=? AND status='pending'""",
            (now, proposal_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM bidder_proposals WHERE id=?", (proposal_id,)).fetchone()
        if not row:
            raise ValueError("Update proposal not found")
        return dict(row)


async def dismiss(proposal_id: int) -> dict[str, Any]:
    return await asyncio.to_thread(_dismiss, proposal_id)


def _history(master_id: int, limit: int) -> list[dict[str, Any]]:
    with closing(db.connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM bidder_master_history WHERE master_pk=? ORDER BY id DESC LIMIT ?",
            (master_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


async def history(master_id: int, limit: int = 200) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_history, master_id, limit)
