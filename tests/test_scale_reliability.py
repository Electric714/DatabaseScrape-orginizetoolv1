import json

from app import bidder_master as bm, database as db


async def test_large_evidence_history_activity_pagination(database, source):
    now = db.utcnow()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for index in range(2500):
            conn.execute(
                """INSERT INTO bidder_master(bidder_id,contractor_name,contractor_norm,address_norm,created_at,updated_at)
                   VALUES (?,?,?,?,?,?)""",
                (str(100000 + index), f"Generated Contractor {index:05d}", f"generated contractor {index:05d}", "", now, now),
            )
        for index in range(5000):
            cur = conn.execute(
                """INSERT INTO records(source_id,entity_key,content_hash,name,company,source_url,extra_json,first_seen,last_seen,last_changed,active)
                   VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
                (source["id"], f"key-{index}", f"hash-{index}", "", f"Generated Evidence {index:05d}", source["start_url"], "{}", now, now, now),
            )
            record_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO record_history(record_id,changed_at,old_json,new_json) VALUES (?,?,NULL,?)",
                (record_id, now, json.dumps({"company": f"Generated Evidence {index:05d}"})),
            )
        for index in range(5000):
            conn.execute(
                "INSERT INTO activity_events(created_at,level,component,message,details_json,source_id) VALUES (?,?,?,?,?,?)",
                (now, "INFO", "scale", f"event-{index}", "{}", source["id"]),
            )
        conn.commit()

    evidence = await db.search_records(source_id=source["id"], limit=100, offset=4900)
    assert evidence["total"] == 5000
    assert len(evidence["items"]) == 100
    bidders = await bm.search(q="Generated Contractor 024", limit=50, offset=0)
    assert bidders["total"] >= 50
    history = await db.record_history(evidence["items"][0]["id"])
    assert len(history) == 1
    events = await db.list_events(limit=300)
    assert len(events["items"]) == 300

    with db.connect() as conn:
        history_plan = " ".join(row[3] for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM record_history WHERE record_id=? ORDER BY id DESC", (evidence["items"][0]["id"],)
        ))
        assert "idx_record_history_record" in history_plan
