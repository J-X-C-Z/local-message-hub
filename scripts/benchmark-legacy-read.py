#!/usr/bin/env python3
"""Synthetic loopback benchmark. Requires Linux /proc and exact pinned Python blobs."""
from contextlib import closing, contextmanager
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import importlib.util
import json
from itertools import permutations
import math
import os
from pathlib import Path
import platform
import random
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlencode

spec = importlib.util.spec_from_file_location("legacy_fixture", Path(__file__).with_name("check-legacy-http.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
BLOBS = {
    "store.py": "1065ba686034b9cc052652328d6a668a6b42bf86",
    "server.py": "9dff68b768542740706429cd1f920d7639577c8d",
    "account_safety.py": "21b27d8d12ed3f4dae92f3523c19b7331012496a",
    "capabilities.py": "cd02eda4025c3c6c6721dee76fbb02bd86cf3ee7",
}
SEED = 0x4C4D48
ROOMS, PER_ROOM = 8, 125
COLD, WARM_BLOCKS, WARM_PER_BLOCK = 30, 8, 90


def verified_blobs():
    for name, expected in BLOBS.items():
        data = next(Path(p, name) for p in fixture.hub.__path__ if Path(p, name).exists()).read_bytes()
        git_blob = b"blob %d\0" % len(data) + data
        assert hashlib.sha1(git_blob).hexdigest() == expected, name


def stock_child(detached):
    store = fixture.ChatStore(detached)
    handler, source_info = fixture.stock_handler()
    settings = {"auto_sync": False, "mcp_write_enabled": False}
    lock = fixture.AccountSafety(settings, lambda _: None)
    cookie = lock.setup(secrets.token_urlsafe(32))
    state = SimpleNamespace(store=store, settings=settings, storage=detached.parent, lock=threading.RLock(),
                            account_safety=lock, capability_enabled=lambda key: key == "chat")
    state.source_info = lambda: source_info(state)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        server.hub = state
        # Private child stdout only; controller never persists the cookie.
        print(json.dumps({"port": server.server_port, "cookie": cookie}), flush=True)
        server.serve_forever()


def seed(root):
    source = fixture.ChatStore(root / "stock.sqlite3")
    rng = random.Random(SEED)
    for r in range(ROOMS):
        room = f"room-{r:02d}"
        source.ingest("qq", "bench", [{"id": room, "title": room}], [
            {"id": f"{room}-{i:04d}", "conversation_id": room, "type": "text",
             "text": f"合成 synthetic row {r}/{i} " + "a" * rng.randint(8, 96),
             "timestamp": 1700000000 + r * PER_ROOM + i,
             "sender_id": f"u-{i % 7}", "sender_name": f"同学{i % 7}", "is_self": i % 7 == 0}
            for i in range(PER_ROOM)
        ])
    detached = source.backup_to(root / "detached" / "index.sqlite3")
    source.close()
    directory = root / "guarded"
    directory.mkdir(mode=0o700)
    password, token = [secrets.token_hex(32) for _ in range(2)]
    credential = {"passphrase": password}
    fixture.cli(["init-guarded-live", "--directory", directory], credential)
    args = ["--source", detached, "--directory", directory, "--platform", "qq", "--account", "bench"]
    preview = fixture.cli(["preview-guarded-v9", *args], credential)
    assert preview["rows"]["messages"] == ROOMS * PER_ROOM
    assert fixture.cli(["import-guarded-v9", *args, "--confirm", preview["confirmation"]], credential) == {"batch_id": preview["confirmation"], "rows": preview["rows"]}
    grants = root / "grants.json"
    grants.touch(mode=0o600)
    grants.write_text(json.dumps({"version": 1, "grants": [{
        "id": "benchmark.synthetic", "kind": "desktop", "account_scope": {"platform": "qq", "account_id": "bench"},
        "permissions": ["messages.read", "conversations.read", "legacy.http.read"],
        "token_sha256": hashlib.sha256(token.encode()).hexdigest()}]}))
    return detached, directory, grants, credential, token


@contextmanager
def server(engine, setup, baseline=None):
    detached, directory, grants, credential, token = setup
    command = ([sys.executable, Path(__file__), fixture.BIN, "--stock-child", detached] if engine == "python"
               else [baseline if engine == "rust_032" else fixture.BIN, "serve-guarded-legacy", "--directory", directory, "--grants", grants])
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0) as p:
        try:
            ready = fixture.proc_json(p)
            if engine == "python":
                port, headers = ready["port"], {"Cookie": "lmh_session=" + ready["cookie"]}
            else:
                assert ready["mode"] == "guarded-legacy-read-only" and ready["locked"]
                port = int(ready["origin"].removeprefix("http://127.0.0.1:"))
                p.stdin.write(fixture.raw({"command": "unlock", **credential}))
                assert fixture.proc_json(p) == {"event": "unlocked"}
                headers = {"Authorization": "Bearer " + token}
            yield p.pid, port, headers
        finally:
            if p.poll() is None:
                if engine != "python":
                    p.stdin.close()
                    try:
                        p.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        p.wait(timeout=3)
                        raise
                    assert p.returncode == 0 and not p.stderr.read()
                else:
                    p.terminate()
                    p.wait(timeout=3)
                    assert not p.stderr.read()


def get(endpoint, query):
    _, port, headers = endpoint
    start = time.perf_counter_ns()
    with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=6)) as conn:
        conn.request("GET", query, headers={**headers, "Connection": "close"})
        response = conn.getresponse()
        body = response.read()
    elapsed = time.perf_counter_ns() - start
    assert response.status == 200, (response.status, query)
    data = json.loads(body)
    # Cursor is opaque and implementations differ. No timestamp ties in this fixture.
    projection = {"total": data["total"], "items": [[item[key] for key in fixture.CORE_MSG] for item in data["items"]]}
    fingerprint = hashlib.sha256(json.dumps(projection, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return elapsed, len(body), fingerprint, projection, hashlib.sha256(body).hexdigest()


def proc(pid):
    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    status = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines() if ":" in line)
    return [int(stat[11]) + int(stat[12]), int(status["VmRSS"].split()[0]), int(status["VmHWM"].split()[0])]


def query_plan():
    rng = random.Random(SEED ^ 0x53)
    return ["/api/messages?" + urlencode({"platform": "qq", "account_id": "bench",
              "conversation_id": f"room-{rng.randrange(ROOMS):02d}", "limit": rng.choice([20, 40, 80]),
              "offset": rng.choice([0, 20, 40, 80])}) for _ in range(COLD + WARM_BLOCKS * WARM_PER_BLOCK)]


def percentile(values, fraction):
    return sorted(values)[math.ceil(len(values) * fraction) - 1]


def summarize(trials, engines):
    result = {}
    for phase in ["fresh_process", "warm"]:
        result[phase] = {}
        for engine in engines:
            selected = [t for t in trials if t["phase"] == phase and t["engine"] == engine]
            samples = [s for t in selected for s in t["samples"]]
            latencies = [s["wall_ns"] for s in samples]
            result[phase][engine] = {
                "requests": len(samples), "p50_ns": percentile(latencies, .5), "p95_ns": percentile(latencies, .95),
                "median_trial_sampled_peak_rss_kib": percentile([t["sampled_peak_rss_kib"] for t in selected], .5),
                "max_trial_process_hwm_kib": max(t["process_hwm_kib"] for t in selected),
                "server_cpu_ticks_sum": sum(t["cpu_ticks"] for t in selected),
                "median_response_body_bytes": percentile([s["response_body_bytes"] for s in samples], .5),
            }
    return result


def run(output, baseline=None):
    if baseline:
        assert hashlib.sha256(baseline.read_bytes()).hexdigest() == "1e615a99952c5154e4faac643189098283920e55f4c266fd7171d09194b188a4"
    engines = ["python", "rust", "rust_032"] if baseline else ["python", "rust"]
    schedules = list(permutations(engines))
    verified_blobs()
    plan = query_plan()
    canonical, rust_wire, trials = {}, {}, []
    with tempfile.TemporaryDirectory(prefix="hub-read-bench-") as tmp:
        setup = seed(Path(tmp))
        original_sha = hashlib.sha256(setup[0].read_bytes()).hexdigest()
        with server("python", setup) as old, server("rust", setup) as new:
            for query in dict.fromkeys(plan):
                left, right = get(old, query), get(new, query)
                assert left[3] == right[3], query
                canonical[query] = left[2]
                rust_wire[query] = right[4]
        if baseline:
            with server("rust_032", setup, baseline) as previous:
                for query in canonical:
                    reply = get(previous, query)
                    assert (reply[2], reply[4]) == (canonical[query], rust_wire[query]), query
        for phase, blocks, count, start in [("fresh_process", COLD, 1, 0), ("warm", WARM_BLOCKS, WARM_PER_BLOCK, COLD)]:
            for trial in range(blocks):
                ids = range(start + trial * count, start + (trial + 1) * count)
                for engine in schedules[trial % len(schedules)]:
                    with server(engine, setup, baseline) as end:
                        if phase == "warm":
                            for i in range(24):
                                assert get(end, plan[(trial * 7 + i) % len(plan)])[2] == canonical[plan[(trial * 7 + i) % len(plan)]]
                        pid = end[0]
                        c0, rss0, _ = proc(pid)
                        stop = threading.Event()
                        rss = [rss0]
                        def monitor():
                            while not stop.wait(0.005):
                                rss.append(proc(pid)[1])
                        thread = threading.Thread(target=monitor)
                        thread.start()
                        samples = []
                        try:
                            for i in ids:
                                ns, size, digest, _, wire = get(end, plan[i])
                                assert digest == canonical[plan[i]]
                                if engine != "python":
                                    assert wire == rust_wire[plan[i]]
                                samples.append({"query_index": i, "wall_ns": ns, "response_body_bytes": size})
                        finally:
                            stop.set()
                            thread.join()
                        c1, end_rss, hwm = proc(pid)
                        trials.append({"phase": phase, "trial": trial, "engine": engine, "samples": samples,
                                       "cpu_ticks": c1-c0, "rss_samples": len(rss)+1,
                                       "sampled_peak_rss_kib": max(end_rss, *rss), "process_hwm_kib": hwm})
        stats = summarize(trials, engines)
        cpu = next(line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                   if line.startswith("model name"))
        result = {"route": "GET /api/messages", "legacy_blob_sha1": BLOBS,
                  "engine_orders": schedules, "baseline_is_exact_rebuild_032_elf": bool(baseline),
                  "fixture_seed": SEED, "synthetic_message_count": ROOMS*PER_ROOM,
                  "fixture_detached_sha256": original_sha,
                  "python_version": platform.python_version(), "linux_kernel": platform.release(),
                  "arch": platform.machine(), "cpu_model": cpu, "logical_cpus_host": os.cpu_count(),
                  "logical_cpus_affinity": len(os.sched_getaffinity(0)),
                  "libc": platform.libc_ver(), "clock_ticks_per_sec": os.sysconf("SC_CLK_TCK"),
                  "runtime_elf_sha256": hashlib.sha256(fixture.BIN.read_bytes()).hexdigest(),
                  "distinct_queries_equal": len(canonical), "shared_fields": fixture.CORE_MSG,
                  "query_plan": plan, "expected_projection_sha256": canonical, "rust_response_body_sha256": rust_wire, "trials": trials, "summary": stats}
        output.write_text(json.dumps(result, indent=2) + "\n")
        print("Matched ordered ten-field projection + total on", len(canonical), "distinct queries;", ROOMS*PER_ROOM, "synthetic messages.")
        print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[2] == "--stock-child":
        stock_child(Path(sys.argv[3]))
    elif len(sys.argv) in (3, 4):
        
        try: run(Path(sys.argv[2]), Path(sys.argv[3]).resolve(strict=True) if len(sys.argv) == 4 else None)
        except subprocess.CalledProcessError as error: raise RuntimeError(error.stderr.decode()) from error
    else:
        raise SystemExit("usage: benchmark-legacy-read.py RUST_ELF OUTPUT.json [EXACT_032_ELF]")
