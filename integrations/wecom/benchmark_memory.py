"""Synthetic WeCom query/release benchmark; never opens account data or config."""
from __future__ import annotations

import argparse
import gc
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import tracemalloc
import types
from unittest.mock import patch


def load_synthetic_modules():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    config = types.ModuleType("integrations.wecom.config")
    config.CONFIG_LOCK = threading.RLock()
    config.load_local_config = lambda: {"key_hex": "00" * 16,
                                       "database_root": "synthetic", "user_slot_id": "synthetic"}
    config.local_data_dir = lambda: "__synthetic_benchmark__"
    decrypt = types.ModuleType("integrations.wecom.decrypt_all")
    decrypt.decrypt_db = lambda *_args: None
    decrypt.PAGE_CONST = 4096
    extract = types.ModuleType("integrations.wecom.extract_chat2")
    extract.best_text = lambda *_args: ""
    extract.CTYPE = {}
    extract.parse_fields = lambda *_args: []

    def load(name, filename):
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    with patch.dict(sys.modules, {m.__name__: m for m in (config, decrypt, extract)}):
        engine = load("integrations.wecom._benchmark_engine", "engine.py")
        with patch.dict(sys.modules, {"integrations.wecom.engine": engine}):
            adapter = load("integrations.wecom._benchmark_adapter", "adapter.py")
    return engine, adapter


def measure(call):
    gc.collect()
    tracemalloc.start()
    start = time.perf_counter()
    result = call()
    seconds = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, {"seconds": round(seconds, 6), "peak_bytes": peak}


def run(rows=100000, limit=50, text_bytes=512):
    engine_module, adapter_module = load_synthetic_modules()
    engine = object.__new__(engine_module.SyncEngine)
    engine.lock = threading.Lock()
    engine._released_summary = None
    engine.stats_cache = None
    adapter = object.__new__(adapter_module.WeComAdapter)
    adapter.engine = engine
    adapter._loaded = True
    adapter._messages_released = False
    engine.messages = [{"message_id": str(i), "conversation_id": "room",
                        "send_time": i, "text": "fixture search", "_k": str(i)}
                       for i in range(rows)]

    def legacy_search():
        # Reproduce the pre-optimization clone-all/filter-all/sort-all algorithm.
        messages = [engine_module.strip_internal(row) for row in engine.snapshot_messages()]
        matches = [row for row in messages if "search" in row["text"].casefold()]
        matches.sort(key=lambda row: row.get("send_time") or 0, reverse=True)
        return matches[:limit]

    legacy_result, legacy = measure(legacy_search)
    optimized_result, optimized = measure(lambda: adapter.search_messages("search", limit=limit))
    if legacy_result != optimized_result:
        raise AssertionError("Optimized query differs from the legacy result")
    del legacy_result, optimized_result
    engine.messages = []
    gc.collect()

    tracemalloc.start()
    engine.messages = [{"message_id": str(i), "conversation": "synthetic room",
                        "conversation_id": "room", "content_type": 1,
                        "send_time": i, "text": str(i) + "x" * text_bytes}
                       for i in range(rows)]
    # Include sparse media so the measured retained index is not a text-only case.
    for i in range(0, rows, 100):
        engine.messages[i]["media"] = [{"p": f"fixture-{i}.png", "n": "fixture.png"}]
    adapter._last_counts = {"messages": rows}
    gc.collect()
    retained_before = tracemalloc.get_traced_memory()[0]
    start = time.perf_counter()
    release = adapter.release_message_cache()
    seconds = time.perf_counter() - start
    gc.collect()
    retained_after = tracemalloc.get_traced_memory()[0]
    tracemalloc.stop()
    if engine.messages or release["messages"] != rows or len(adapter._released_ids) != rows:
        raise AssertionError("Release did not preserve the message identity index")
    if len(adapter._released_media) != (rows + 99) // 100:
        raise AssertionError("Release lost attachment metadata")
    return {"dataset": {"rows": rows, "limit": limit, "text_bytes": text_bytes,
                         "media_every": 100},
            "query": {"legacy": legacy, "optimized": optimized,
                      "peak_reduction_pct": round((1 - optimized["peak_bytes"] / legacy["peak_bytes"]) * 100, 2),
                      "results_equal": True},
            "idle_release": {"retained_before_bytes": retained_before,
                             "retained_after_bytes": retained_after,
                             "reduction_pct": round((1 - retained_after / retained_before) * 100, 2),
                             "seconds": round(seconds, 6),
                             "media_messages": len(adapter._released_media)}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=100000)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--text-bytes", type=int, default=512)
    args = parser.parse_args()
    if not 1 <= args.rows <= 1000000 or not 1 <= args.limit <= args.rows or not 1 <= args.text_bytes <= 4096:
        parser.error("require rows=1..1000000, limit=1..rows, text-bytes=1..4096")
    print(json.dumps(run(args.rows, args.limit, args.text_bytes), indent=2))
