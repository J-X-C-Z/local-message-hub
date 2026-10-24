"""Synthetic, disposable SQLite workload; never opens the user's index."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hub.store import ChatStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--messages", type=int, default=30000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.messages < 100:
        parser.error("--messages must be at least 100")
    rows = [{"id": str(i), "conversation_id": str(i % 60),
             "sender_id": "sender", "timestamp": 1700000000 + i // 4,
             "text": f"synthetic message {i} searchable needle workload"}
            for i in range(args.messages)]
    result = {"messages": args.messages, "batch_size": 500}
    with tempfile.TemporaryDirectory() as folder, ChatStore(Path(folder) / "index.db") as store:
        def ingest():
            for start in range(0, len(rows), 500):
                store.ingest("email", "synthetic", [], rows[start:start + 500])
        start = time.perf_counter()
        ingest()
        result["initial_ingest_seconds"] = time.perf_counter() - start
        start = time.perf_counter()
        changes = store._conn.total_changes
        ingest()
        result["repeat_ingest_seconds"] = time.perf_counter() - start
        result["repeat_sqlite_changes"] = store._conn.total_changes - changes
        assert store.count() == len(rows)
        for name, query in (
            ("search", lambda: store.search("needle", limit=50)),
            ("search_deep_offset", lambda: store.search("needle", limit=50, offset=len(rows) // 2)),
            ("conversation_page", lambda: store.messages("email", "synthetic", "0", limit=50)),
            ("stats", store.stats),
        ):
            samples = []
            for _ in range(7):
                start = time.perf_counter()
                page = query()
                samples.append(time.perf_counter() - start)
                assert page
            result[name + "_median_ms"] = statistics.median(samples) * 1000
        page = store.search("needle", limit=50)
        assert page["total"] == len(rows)
        next_page = store.search("needle", limit=50, cursor=page["next_cursor"])
        assert not {m["id"] for m in page["items"]} & {m["id"] for m in next_page["items"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
