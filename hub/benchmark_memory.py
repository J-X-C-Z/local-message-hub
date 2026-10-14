"""Synthetic-only export memory and deep-cursor query comparison."""

import sqlite3
import time
import tracemalloc

from .exports import render_export
from .store import ChatStore, _cursor_scope, _encode_cursor


class _OldCursorConnection:
    """Replay the old predicate through the same complete store query pipeline."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, query, params=()):
        for prefix in ("", "m."):
            predicate = f"({prefix}timestamp, {prefix}rowid) < (?, ?)"
            if predicate in query:
                query = query.replace(predicate,
                    f"({prefix}timestamp < ? OR ({prefix}timestamp = ? AND {prefix}rowid < ?))")
                params = [*params[:-4], params[-4], params[-4], *params[-3:]]
        return self.conn.execute(query, params)


def store_benchmark():
    store = ChatStore(":memory:")
    platforms = ("wechat", "wecom", "qq")
    for platform_index, platform in enumerate(platforms):
        for lower in range(0, 100000, 6000):
            store.ingest(platform, "a", [{"id": "c", "title": "synthetic", "kind": "private"}],
                ({"id": str(index), "conversation_id": "c", "timestamp": index,
                  "text": "x" * 1024, "sender_id": "s", "type": "text"}
                 for index in range(lower + platform_index, min(lower + 6000, 100000), 3)))
    for label, operation in (
        ("sources_old_stats_x3", lambda: [store.stats(platform) for platform in platforms]),
        ("sources_new_counts", store.source_counts),
    ):
        tracemalloc.start()
        started = time.perf_counter()
        for _ in range(10):
            operation()
        print(label, "10_reads_seconds", round(time.perf_counter() - started, 4),
              "python_peak_mib", round(tracemalloc.get_traced_memory()[1] / 1048576, 3))
        tracemalloc.stop()
    conn = store._require_connection()
    original = store._require_connection
    cursor = _encode_cursor(1000, 1000000, _cursor_scope(["qq", "a", "c", "", "", "", ""]))
    search_cursor = _encode_cursor(1000, 1000000, _cursor_scope(["", "all", "", "", ""]))
    for label, use_old in (("old", True), ("new", False)):
        store._require_connection = (lambda: _OldCursorConnection(conn)) if use_old else original
        for name, operation in (
            ("messages", lambda: store.messages("qq", "a", "c", limit=100, cursor=cursor)),
            ("search", lambda: store.search("", limit=100, cursor=search_cursor)),
        ):
            tracemalloc.start()
            started = time.perf_counter()
            for _ in range(30):
                result = operation()
            print(label, name, "30_full_calls_seconds", round(time.perf_counter() - started, 4),
                  "rows", len(result["items"]), "total", result["total"],
                  "python_peak_mib", round(tracemalloc.get_traced_memory()[1] / 1048576, 3))
            tracemalloc.stop()
    store._require_connection = original
    store.close()


def main():
    for format in ("json", "xlsx", "txt", "html", "chatlab", "chatlab-jsonl"):
        messages = ({"id": str(index), "timestamp": 1700000000 + index,
                     "text": "x" * 1024, "sender_name": "sender", "platform": "qq"}
                    for index in range(20000))
        tracemalloc.start()
        started = time.perf_counter()
        payload = render_export(messages, format)[0]
        print(format, "bytes", len(payload), "peak_mib",
              round(tracemalloc.get_traced_memory()[1] / 1048576, 2),
              "seconds", round(time.perf_counter() - started, 3))
        tracemalloc.stop()
        del payload

    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE messages(platform,account_id,conversation_id,timestamp,text)")
        conn.execute("CREATE INDEX idx ON messages(platform,account_id,conversation_id,timestamp)")
        conn.executemany("INSERT INTO messages VALUES(?,?,?,?,?)",
                         (("qq", "a", "c", index, "x" * 1024) for index in range(100000)))
        base = ("SELECT rowid,* FROM messages WHERE platform='qq' AND account_id='a' "
                "AND conversation_id='c' AND ")
        for predicate, params in (
            ("(timestamp < ? OR (timestamp = ? AND rowid < ?))", (1000, 1000, 1001)),
            ("(timestamp,rowid) < (?,?)", (1000, 1001)),
        ):
            query = base + predicate + " ORDER BY timestamp DESC,rowid DESC LIMIT 101"
            print("plan", list(conn.execute("EXPLAIN QUERY PLAN " + query, params)))
            started = time.perf_counter()
            for _ in range(100):
                conn.execute(query, params).fetchall()
            print("cursor_100_reads_seconds", round(time.perf_counter() - started, 4))
    store_benchmark()


if __name__ == "__main__":
    main()
