"""Synthetic Local Message Hub performance and recovery smoke benchmark."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hub.store import ChatStore


ROWS = 25_000
PAGE_SIZE = 500
SEARCH_TERM = "benchmark"


def timed(action):
    started = time.perf_counter()
    result = action()
    return result, round(time.perf_counter() - started, 3)


def main():
    with tempfile.TemporaryDirectory(prefix="lmh-benchmark-") as directory:
        database = Path(directory) / "synthetic.sqlite3"
        store = ChatStore(database)
        messages = [{
            "id": f"synthetic-{index}", "conversation_id": "synthetic-room",
            "timestamp": 1_800_000_000 + index, "type": "text",
            "text": f"benchmark 中文消息 {index} marker-{index % 17}",
            "sender_id": f"sender-{index % 8}",
        } for index in range(ROWS)]
        _, ingest_seconds = timed(lambda: store.ingest(
            "wechat", "synthetic-account",
            [{"id": "synthetic-room", "title": "Synthetic benchmark"}], messages))

        search, search_seconds = timed(lambda: store.search(
            SEARCH_TERM, platform="wechat", limit=PAGE_SIZE))
        total = search["total"]
        chinese_short_search = store.search("消息", platform="wechat", limit=1)["total"]

        def walk_pages():
            cursor = None
            page_count = 0
            row_count = 0
            while True:
                page = store.search(SEARCH_TERM, platform="wechat",
                                    limit=PAGE_SIZE, cursor=cursor)
                page_count += 1
                row_count += len(page["items"])
                cursor = page["next_cursor"]
                if not cursor:
                    return page_count, row_count

        (pages, paged_rows), pagination_seconds = timed(walk_pages)

        writer = ChatStore(database)
        barrier = threading.Barrier(2)
        failures = []

        def concurrent_writer():
            try:
                barrier.wait(timeout=5)
                for batch in range(20):
                    writer.ingest("qq", "synthetic-account",
                                  [{"id": "wal-room", "title": "WAL"}], [{
                        "id": f"wal-{index}", "conversation_id": "wal-room",
                        "timestamp": index, "type": "text", "text": "wal fixture",
                    } for index in range(batch * 10, batch * 10 + 10)])
                    time.sleep(0.002)
            except Exception as exc:
                failures.append(type(exc).__name__)

        thread = threading.Thread(target=concurrent_writer)
        thread.start()
        barrier.wait(timeout=5)
        read_counts = []
        while thread.is_alive():
            read_counts.append(store.search(SEARCH_TERM, platform="wechat", limit=20)["total"])
        thread.join()

        backup_path = Path(directory) / "snapshot.sqlite3"
        _, backup_seconds = timed(lambda: store.backup_to(backup_path))
        store.ingest("qq", "synthetic-account", [], [{
            "id": "post-backup", "conversation_id": "wal-room", "timestamp": 201,
            "type": "text", "text": "after snapshot",
        }])
        _, restore_seconds = timed(lambda: store.restore_from(backup_path))
        recovered_count = store.count("wechat")
        wal_count = store.count("qq")
        result = {
            "synthetic_messages": ROWS,
            "ingest_seconds": ingest_seconds,
            "fts_enabled": store._fts_enabled,
            "search_total": total,
            "chinese_short_search_total": chinese_short_search,
            "first_search_seconds": search_seconds,
            "cursor_pages": pages,
            "page_size": PAGE_SIZE,
            "paged_rows": paged_rows,
            "pagination_recheck_seconds": pagination_seconds,
            "wal_writer_messages": 200,
            "wal_reader_samples": len(read_counts),
            "wal_failures": failures,
            "backup_seconds": backup_seconds,
            "restore_seconds": restore_seconds,
            "recovered_wechat_messages": recovered_count,
            "restored_qq_messages": wal_count,
        }
        writer.close()
        store.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if total != ROWS or chinese_short_search != ROWS:
            raise SystemExit("synthetic search count mismatch")
        if failures or recovered_count != ROWS or wal_count != 200 or paged_rows != total:
            raise SystemExit("concurrency or backup recovery check failed")


if __name__ == "__main__":
    main()
