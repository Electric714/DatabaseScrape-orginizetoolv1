"""Generated-data benchmark for local-office scale. Not part of normal CI."""
import argparse
import sqlite3
import tempfile
import time
from pathlib import Path

from app import database as db


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bidders", type=int, default=25000)
    parser.add_argument("--evidence", type=int, default=100000)
    parser.add_argument("--history", type=int, default=200000)
    parser.add_argument("--activity", type=int, default=50000)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="paralegal-scale-") as temp:
        path = Path(temp) / "benchmark.db"
        original = db.DB_PATH
        db.DB_PATH = path
        import asyncio
        asyncio.run(db.init_db())
        now = db.utcnow()
        with db.connect() as conn:
            source_id = conn.execute(
                "INSERT INTO sources(name,start_url,created_at) VALUES ('Benchmark','https://benchmark.test/',?)", (now,)
            ).lastrowid
            started = time.perf_counter()
            conn.execute("BEGIN IMMEDIATE")
            for i in range(args.bidders):
                conn.execute(
                    "INSERT INTO bidder_master(bidder_id,contractor_name,contractor_norm,address_norm,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                    (str(i + 1), f"Contractor {i}", f"contractor {i}", "", now, now),
                )
            record_ids = []
            for i in range(args.evidence):
                cur = conn.execute(
                    "INSERT INTO records(source_id,entity_key,content_hash,company,source_url,extra_json,first_seen,last_seen,last_changed,active) VALUES (?,?,?,?,?,?,?,?,?,1)",
                    (source_id, f"key-{i}", f"hash-{i}", f"Evidence {i}", "https://benchmark.test/", "{}", now, now, now),
                )
                record_ids.append(cur.lastrowid)
            for i in range(args.history):
                rid = record_ids[i % len(record_ids)]
                conn.execute("INSERT INTO record_history(record_id,changed_at,new_json) VALUES (?,?,?)", (rid, now, "{}"))
            for i in range(args.activity):
                conn.execute("INSERT INTO activity_events(created_at,level,component,message,details_json) VALUES (?,?,?,?,?)", (now, "INFO", "benchmark", f"event-{i}", "{}"))
            conn.commit()
            load_seconds = time.perf_counter() - started
        async def queries():
            start = time.perf_counter()
            page = await db.search_records(source_id=source_id, limit=100, offset=max(0, args.evidence - 100))
            elapsed = time.perf_counter() - start
            return page["total"], elapsed
        total, query_seconds = asyncio.run(queries())
        print({"db": str(path), "load_seconds": round(load_seconds, 3), "evidence_total": total, "paged_query_seconds": round(query_seconds, 4)})
        db.DB_PATH = original


if __name__ == "__main__":
    main()
