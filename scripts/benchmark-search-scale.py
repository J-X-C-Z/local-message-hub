#!/usr/bin/env python3
"""Paired loopback search benchmark on the pinned synthetic 30k-row fixture."""
import ast
from contextlib import closing, contextmanager
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import importlib.util
import json
from itertools import permutations
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlencode

spec = importlib.util.spec_from_file_location("legacy_benchmark", Path(__file__).with_name("benchmark-legacy-read.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
fixture = base.fixture
GENERATOR_BLOB = "27c22832814b7f1738c5f51bd0e588553fbdd3fa"
TOTAL, PAGE, PAIRS, REPEATS = 30000, 50, 6, 12
CASES = ("top", "deep_direct_offset", "top_continuation", "deep_continuation")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_rows(generator):
    source = generator.read_bytes()
    assert hashlib.sha1(b"blob %d\0" % len(source) + source).hexdigest() == GENERATOR_BLOB
    main = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "main")
    rows = next(node.value for node in main.body if isinstance(node, ast.Assign) and
                len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "rows")
    assert isinstance(rows, ast.ListComp)
    # Only evaluate the verified pinned synthetic row-generation expression;
    # never execute original CLI or leave its workload database live in Rust.
    return eval(compile(ast.Expression(rows), str(generator), "eval"), {"__builtins__": {"range": range, "str": str}},
                {"args": SimpleNamespace(messages=TOTAL)})


def setup(root, generator):
    path = root / "stock-live" / "hub.sqlite3"
    path.parent.mkdir()
    rows = fixture_rows(generator)
    store = fixture.ChatStore(path)
    for i in range(0, len(rows), 500):
        store.ingest("email", "synthetic", [], rows[i:i+500])
    assert store.count() == TOTAL and store._fts_enabled
    pristine = store.backup_to(root / "rust" / "index.sqlite3")
    # Separate snapshot checks the exact-account grant against a genuinely
    # present other account with one matching term and a colliding raw ID.
    store.ingest("email", "foreign", [], [{"id": "29999", "conversation_id": "59",
        "sender_id": "different", "timestamp": 1705000000, "text": "foreign needle marker"}])
    store.ingest("email", "synthetic", [], [
        {"id":"035-a","conversation_id":"35","timestamp":1700086400,"text":'甲乙丙 测试 A%_B phrase "quote"'},
        {"id":"035-b","conversation_id":"35","timestamp":1700086401,"text":"甲乙丙 测试 AxxB phrase quote"},
        {"id":"035-c","conversation_id":"36","timestamp":1700086401,"text":'甲乙丙 测试 A%_B phrase "quote"'},
    ])
    store.ingest("email", "foreign", [], [
        {"id":"035-a","conversation_id":"35","timestamp":1700086400,"text":'甲乙丙 A%_B "quote"'}
    ])
    scoped = store.backup_to(root / "scope-check" / "index.sqlite3")
    store.close()
    old = root / "python" / "index.sqlite3"
    old.parent.mkdir()
    original_sha = sha(pristine)
    shutil.copyfile(pristine, old)
    assert sha(old) == original_sha
    with closing(sqlite3.connect(pristine.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        fts_sql = db.execute("SELECT sql FROM sqlite_master WHERE name='messages_fts'").fetchone()[0]
        assert version == 9 and "USING fts5" in fts_sql
        assert db.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'needle'").fetchone()[0] == TOTAL
    token = secrets.token_hex(32)
    grants = root / "grants.json"
    grants.touch(mode=0o600)
    grants.write_text(json.dumps({"version":1, "grants":[{"id":"benchmark.read", "kind":"desktop",
        "account_scope":{"platform":"email", "account_id":"synthetic"}, "permissions":["messages.read"],
        "token_sha256":hashlib.sha256(token.encode()).hexdigest()}]}))
    return old, pristine, scoped, grants, token, original_sha, version, fts_sql


def stock_child(path):
    store = fixture.ChatStore(path)
    # EXPLAIN/metadata came from the pristine backup; verify Python still uses FTS.
    assert store._fts_enabled
    handler, _ = fixture.stock_handler()
    settings = {"auto_sync":False,"mcp_write_enabled":False}
    lock = fixture.AccountSafety(settings, lambda _: None)
    cookie = lock.setup(secrets.token_urlsafe(32))
    state = SimpleNamespace(store=store, settings=settings, storage=path.parent, lock=threading.RLock(),
        account_safety=lock, capability_enabled=lambda key:key in {"chat","search"})
    with ThreadingHTTPServer(("127.0.0.1",0), handler) as listener:
        listener.hub = state
        print(json.dumps({"port":listener.server_port, "cookie":cookie, "fts_enabled":True,
                          "sqlite_version":sqlite3.sqlite_version}), flush=True)
        listener.serve_forever()


@contextmanager
def endpoint(engine, path, grants, token, baseline=None):
    args = ([sys.executable, __file__, fixture.BIN, "--stock-child", path] if engine == "python"
            else [baseline if engine == "rust_034" else fixture.BIN, "serve", "--snapshot", path, "--grants", grants])
    with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, bufsize=0) as child:
        try:
            if engine == "python":
                msg = fixture.proc_json(child)
                assert msg["fts_enabled"]
                yield (child.pid, msg["port"], "/api/search", {"Cookie":"lmh_session="+msg["cookie"]})
            else:
                import select
                assert select.select([child.stdout],[],[],8)[0]
                line = child.stdout.readline().decode()
                port = int(re.search(r"127\.0\.0\.1:(\d+)", line)[1])
                peer = (child.pid, port, "/api/v1/search", {"Authorization":"Bearer "+token})
                status, _, _, payload, _ = get(peer, {}, "/api/v1/status")
                assert status == 200 and payload["fts_enabled"] and payload["schema_version"] == 9
                yield peer
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                    raise
                assert not child.stderr.read()


def get(peer, params, route=None):
    _, port, default_route, headers = peer
    path = (route or default_route) + ("?" + urlencode(params) if params else "")
    start = time.perf_counter_ns()
    with closing(http.client.HTTPConnection("127.0.0.1", port, timeout=12)) as connection:
        connection.request("GET", path, headers={**headers,"Connection":"close"})
        response = connection.getresponse()
        body = response.read()
    elapsed = time.perf_counter_ns() - start
    return response.status, elapsed, len(body), json.loads(body), hashlib.sha256(body).hexdigest()


def project(value):
    return {"total":value["total"],"has_more":value["has_more"],
            "items":[[m[k] for k in fixture.CORE_MSG] for m in value["items"]]}


def requests(peer):
    common = {"q":"needle","platform":"email","limit":PAGE}
    queries = {}
    for tag, offset, cursor_tag in (("top",0,"top_continuation"),("deep_direct_offset",TOTAL//2,"deep_continuation")):
        query = {**common,"offset":offset}
        status, _, _, page, _ = get(peer, query)
        assert status == 200 and len(page["items"]) == PAGE and page["next_cursor"]
        queries[tag] = query
        queries[cursor_tag] = {**common, "cursor":page["next_cursor"]}
    return queries


def check_initial(old, new, qa, qb):
    canonical = {}
    for name in CASES:
        status_left, _, _, left, _ = get(old, qa[name])
        status_right, _, _, right, _ = get(new, qb[name])
        assert status_left == status_right == 200 and project(left) == project(right), name
        canonical[name] = project(left)
        left_pagination = canonical[name]
        assert left_pagination["total"] == TOTAL and len(left_pagination["items"]) == PAGE
        offset = {"top":0,"top_continuation":PAGE,"deep_direct_offset":TOTAL//2,
                  "deep_continuation":TOTAL//2+PAGE}[name]
        assert [m[0] for m in left_pagination["items"]] == list(map(str,range(TOTAL-1-offset,TOTAL-1-offset-PAGE,-1))), name
        assert all(row[1:3] == ["email","synthetic"] for row in left_pagination["items"])
    # Pin explicitly out-of-scope rowid-vs-ID tie ordering, no timing ratio.
    edge = {"q":"needle","platform":"email","limit":16,"offset":TOTAL-16}
    a = [m["id"] for m in get(old,edge)[3]["items"]]
    b = [m["id"] for m in get(new,edge)[3]["items"]]
    assert sorted(a) == sorted(b) and a != b
    return canonical, {"query":edge,"stock_ids":a,"scoped_rust_ids":b,"timed":False}


def sample(peer, queries, canonical, index, rust_digest=None):
    pid=peer[0]
    c0, rss0, _ = base.proc(pid)
    stop = threading.Event()
    rss = [rss0]
    def monitor():
        while not stop.wait(.005):
            rss.append(base.proc(pid)[1])
    thread = threading.Thread(target=monitor)
    thread.start()
    samples=[]
    try:
        for repeat in range(REPEATS):
            for name in CASES[(repeat+index)%4:]+CASES[:(repeat+index)%4]:
                code, elapsed, size, page, digest = get(peer, queries[name])
                assert code == 200 and project(page) == canonical[name]
                if rust_digest:
                    assert digest == rust_digest[name]
                samples.append({"case":name, "wall_ns":elapsed, "response_body_bytes":size})
    finally:
        stop.set()
        thread.join()
    c1, rss1, hwm = base.proc(pid)
    return {"cpu_ticks":c1-c0, "rss_samples":len(rss)+1, "sampled_peak_rss_kib":max(rss1,*rss),
            "process_hwm_kib":hwm, "requests":samples}


def edge_cases(peer):
    prefix = {"platform":"email", "limit":50}
    utc8_date = datetime.fromtimestamp(1700086400,timezone(timedelta(hours=8))).date().isoformat()
    specs = [
        ({"q":"A%_B"},2),
        ({"q":"甲乙丙"},3),
        ({"q":'"quote"'},2),
        ({"q":"A%","conversation_id":"35"},1),
        ({"q":"A%_B","conversation_id":"35","start":utc8_date,"end":utc8_date},1),
        ({"q":"A%_B","type":"not-a-type"},0),
        ({"q":"A%_B","offset":99},2),
        ({"q":"synthetic message 29999"},1),
        ({"q":"never-matches-035"},0),
    ]
    checks = []
    for query,total in specs:
        reply = get(peer,{**prefix,**query})
        assert reply[0]==200 and reply[3]["total"]==total,(query,reply[0],reply[3])
        assert all(row["account_id"]=="synthetic" for row in reply[3]["items"])
        checks.append({"query":query,"status":reply[0],"total":total,"body_sha256":reply[4]})
    first=get(peer,{**prefix,"q":"甲乙丙","limit":1})
    cursor=first[3]["next_cursor"]
    assert first[0]==200 and first[3]["has_more"] and cursor
    next_page=get(peer,{**prefix,"q":"甲乙丙","limit":1,"cursor":cursor})
    assert next_page[0]==200 and next_page[3]["items"][0]["id"]!=first[3]["items"][0]["id"]
    assert next_page[3]["total"]==3
    assert get(peer,{**prefix,"q":"different","cursor":cursor})[0]==400
    assert get(peer,{**prefix,"q":"甲乙丙","account_id":"foreign","cursor":cursor})[0]==403
    return {"pages":checks,"first_and_next_sha256":[first[4],next_page[4]],"wrong_query":400,"foreign_account":403}


def query_plan_probe(pristine, edges):
    membership = "m.rowid IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)"
    scope = "m.platform = ? AND m.account_id = ?"
    literal = "m.text LIKE ? ESCAPE '\\'"
    original_count = f"SELECT COUNT(*) FROM messages m WHERE {scope} AND {membership} AND {literal}"
    candidate_count = ("SELECT COUNT(*) FROM main.messages_fts CROSS JOIN main.messages m "
                       f"ON m.rowid = messages_fts.rowid WHERE messages_fts MATCH ? AND {scope} AND {literal}")
    # The inner paging SELECT only, unchanged in 035. The final row projection
    # and serialization are deliberately captured only in the real HTTP benchmark.
    inner_page = (f"SELECT m.rowid,m.id FROM messages m WHERE {scope} AND {membership} AND {literal} "
                  "ORDER BY m.timestamp DESC,m.id DESC LIMIT ? OFFSET ?")
    cases = [(pristine, "all_matching", "needle", TOTAL),
             (pristine, "selective_ascii", "synthetic message 29999", 1),
             (edges, "selective_unicode", "甲乙丙", 3),
             (edges, "escaped_literal", "A%_B", 2)]
    results = []
    for archive, label, term, expected in cases:
        escaped = "%" + term.replace("\\","\\\\").replace("%","\\%").replace("_","\\_") + "%"
        phrase = '"' + term.replace('"','""') + '"'
        scoped = ("email","synthetic")
        queries = {"original_count":(original_count, (*scoped,phrase,escaped)),
                   "candidate_count":(candidate_count, (phrase,*scoped,escaped)),
                   "unchanged_rank_select":(inner_page, (*scoped,phrase,escaped,51,0))}
        with closing(sqlite3.connect(archive.as_uri()+"?mode=ro&immutable=1",uri=True)) as sql:
            plans = {key:sql.execute("EXPLAIN QUERY PLAN "+q,params).fetchall() for key,(q,params) in queries.items()}
            samples = {key: [] for key in queries}
            for step in range(11):
                for key in (list(queries) if not step % 2 else list(reversed(queries))):
                    q, params = queries[key]
                    start = time.perf_counter_ns()
                    result = sql.execute(q, params).fetchall()
                    elapsed = time.perf_counter_ns()-start
                    assert (result==[(expected,)] if key.endswith("count") else len(result)==min(51,expected))
                    if step>=2:
                        samples[key].append(elapsed)
            results.append({"label":label,"matched":expected,"plan":plans,"samples_ns":samples,
                            "p50_ns":{key:base.percentile(times,.5) for key,times in samples.items()}})
    return {"engine":"Python-linked SQLite "+sqlite3.sqlite_version,
            "caveat":"SQL stage probe via Python SQLite, not operator timings from either released service",
            "cases":results}

def main(generator, output, baseline=None):
    if baseline:
        assert sha(baseline) == "be2dc9c992a9795841aae1513be51f06f3b2969a249594d6dc680454dcb18f14"
    engines = ("python","rust","rust_034") if baseline else ("python","rust")
    schedules=list(permutations(engines))
    base.verified_blobs()
    with tempfile.TemporaryDirectory(prefix="hub-synthetic-search-") as folder:
        old, new, scoped, grants, token, digest, schema, fts_sql = setup(Path(folder), generator)
        with endpoint("python",old,grants,token) as a, endpoint("rust",new,grants,token) as b:
            check_a,check_b=requests(a),requests(b)
            canonical, excluded = check_initial(a,b,check_a,check_b)
            rust_digest = {name:get(b,check_b[name])[4] for name in CASES}
            if baseline:
                with endpoint("rust_034",new,grants,token,baseline) as previous:
                    queries=requests(previous)
                    for case in CASES:
                        assert get(previous,queries[case])[4]==rust_digest[case]
                    assert check_initial(a,previous,check_a,queries)[1]==excluded
        with endpoint("rust",scoped,grants,token) as isolated:
            edges = edge_cases(isolated)
            assert project(get(isolated,{"q":"needle","platform":"email","limit":PAGE})[3]) == canonical["top"]
            assert get(isolated,{"q":"needle","platform":"email","account_id":"foreign"})[0]==403
        if baseline:
            with endpoint("rust_034",scoped,grants,token,baseline) as before:
                assert edge_cases(before)==edges
        trials=[]
        for trial in range(PAIRS):
            for engine in schedules[trial % len(schedules)]:
                with endpoint(engine,old if engine=="python" else new,grants,token,baseline) as peer:
                    queries=requests(peer)
                    for _ in range(2):
                        for case in CASES:
                            assert project(get(peer,queries[case])[3])==canonical[case]
                    trials.append({"trial":trial,"engine":engine,**sample(peer,queries,canonical,trial,None if engine=="python" else rust_digest)})
        assert sha(new)==digest, "Rust modified detached fixture"
        plan_evidence=query_plan_probe(new,scoped)
        assert sha(new)==digest, "immutable diagnostic probe modified fixture"
        summary={}
        for engine in engines:
            own=[t for t in trials if t["engine"]==engine]
            cases={}
            for case in CASES:
                values=[s for t in own for s in t["requests"] if s["case"]==case]
                ns=[s["wall_ns"] for s in values]
                cases[case]={"requests":len(ns),"p50_ns":base.percentile(ns,.5),"p95_ns":base.percentile(ns,.95),
                    "median_body_bytes":base.percentile([s["response_body_bytes"] for s in values],.5)}
            summary[engine]={"cases":cases, "cpu_ticks_sum":sum(t["cpu_ticks"] for t in own),
                "median_trial_sampled_peak_rss_kib":base.percentile([t["sampled_peak_rss_kib"] for t in own],.5),
                "max_process_hwm_kib":max(t["process_hwm_kib"] for t in own)}
        result={"legacy_revision":"c7052745fab3fa1880c6e3a496f40609185668b9","generator_blob":GENERATOR_BLOB,
                "legacy_blob_sha1":base.BLOBS,"synthetic_rows":TOTAL,"conversations":60,"detached_sha256_both_before_open":digest,
                "rust_snapshot_sha256_unchanged":True,"sqlite_user_version":schema,"sqlite_fts_schema":fts_sql,
                "python_sqlite_version":sqlite3.sqlite_version,"python_version":base.platform.python_version(),
                "arch":base.platform.machine(),"linux_kernel":base.platform.release(),
                "logical_cpus_affinity":len(base.os.sched_getaffinity(0)),
                "cpu":next(s.split(":",1)[1].strip() for s in Path("/proc/cpuinfo").read_text().splitlines() if s.startswith("model name")),
                "clock_ticks_per_sec":base.os.sysconf("SC_CLK_TCK"),
                "rust_elf_sha256":sha(fixture.BIN),"excluded_tie_edge":excluded,
                "matched_fields":fixture.CORE_MSG,"projected_payloads":canonical,"rust_body_sha256":rust_digest,
                "rust_034_baseline_elf_sha256":sha(baseline) if baseline else None,"order_schedule":schedules,"rust_edge_checks":edges,"sql_stage_diagnostics":plan_evidence,"trials":trials,"summary":summary}
        output.write_text(json.dumps(result,indent=2)+"\n")
        print("Same detached v9 FTS snapshot; all four timed cases ordered-equal, low-ID tie divergence and exact-account rejection verified separately.")
        print(json.dumps(summary,indent=2))


if __name__ == "__main__":
    if len(sys.argv)==4 and sys.argv[2]=="--stock-child":
        stock_child(Path(sys.argv[3]))
    elif len(sys.argv) in (4,5):
        main(Path(sys.argv[2]),Path(sys.argv[3]),Path(sys.argv[4]).resolve(strict=True) if len(sys.argv)==5 else None)
    else:
        raise SystemExit("usage: benchmark-search-scale.py RUST_ELF PINNED_GENERATOR.py OUTPUT.json [EXACT_034_ELF]")
