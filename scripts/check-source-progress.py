#!/usr/bin/env python3
"""Exercise additive resume support using the exact 020 and 021 Linux binaries."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile

from jsonschema import Draft202012Validator

spec = importlib.util.spec_from_file_location("source_020", Path(__file__).with_name("check-scoped-producer.py"))
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)
runtime = source.runtime
NEW = runtime.BIN
OLD = Path(sys.argv[2]).resolve(strict=True)
PATH = "/api/v1/source/checkpoint"
INGEST = "/api/v1/source/ingest"


def send(port, token, marker, sequence, batch, status=200, code=None):
    data = {"batch": batch, "advance": {"expected_revision": marker["revision"], "sequence": sequence}}
    conn, response = runtime.dial(port, "POST", INGEST, token, data)
    try:
        result = json.loads(response.read())
        assert response.status == status, (response.status, result)
        if code:
            assert result == {"error": {"code": code}}
        return result
    finally:
        conn.close()


def checked(root):
    tasks = []

    def init(path, binary):
        path.mkdir(mode=0o700)
        subprocess.run([binary,"init-live","--directory",path],
                       check=True, capture_output=True, timeout=8)

    def start(path, binary=NEW):
        runtime.BIN = binary
        try:
            proc = runtime.start(path, config)
        finally:
            runtime.BIN = NEW
        tasks.append(proc)
        return proc, runtime.ready(proc)[0]

    def tables(path):
        db = sqlite3.connect(path.as_uri() + "/native-v1.sqlite3?mode=ro", uri=True)
        try:
            return db.execute("SELECT count(*) FROM sqlite_schema WHERE name LIKE 'native_source_progress%'").fetchone()[0]
        finally:
            db.close()

    archive = root/"archive-v020"
    other = root/"another-archive"
    init(archive,OLD)
    init(other,NEW)
    config = root/"grants.json"
    config.touch(mode=0o600)
    a, b, c, d, legacy, rotated = [secrets.token_hex(32) for _ in range(6)]
    scoped = lambda id, account, token: source.grant(id,"scoped-producer",("qq",account),["messages.ingest"],token)
    src_a = scoped("test.source-a","A",a)
    src_b = scoped("test.source-b","B",b)
    src_c = scoped("test.source-c","A",c)
    desktop = source.grant("test.desktop","desktop",("qq","A"),["messages.read","events.read"],d)
    trusted = source.grant("test.trusted","producer",None,["messages.ingest"],legacy)

    def grants(*gr):
        config.write_text(json.dumps({"version":1,"grants":gr}))

    grants(src_a,src_b,src_c,desktop,trusted)
    try:
        old, port = start(archive, OLD)
        legacy_batch = source.request("old-key",id="historical")
        trusted_batch = source.request("trusted-key",id="unclaimed")
        assert source.post(port,a,legacy_batch)["checkpoint"] == 1
        assert source.post(port,legacy,trusted_batch)["checkpoint"] == 2
        runtime.finish(old,port)
        assert tables(archive) == 0
        proc, port = start(archive)
        old_contract = json.loads((OLD.parent/"openapi.json").read_text())
        old_manifest_schema = old_contract["paths"]["/api/v1/manifest"]["get"]["responses"]["200"]
        old_manifest_schema = old_manifest_schema["content"]["application/json"]["schema"]
        manifest = runtime.get(port,"/api/v1/manifest",a)
        Draft202012Validator(old_manifest_schema).validate(manifest)
        assert manifest["source_resume"] == {"checkpoint":PATH,"ingest":INGEST}
        for other_token in (d,legacy):
            assert "source_resume" not in runtime.get(port,"/api/v1/manifest",other_token)
        initial = runtime.get(port,PATH,a)
        peer = runtime.get(port,PATH,c)
        away = runtime.get(port,PATH,b)
        assert initial["sequence"] == peer["sequence"] == away["sequence"] == 0
        assert len({initial["revision"],peer["revision"],away["revision"]}) == 3
        assert tables(archive) == 0   # GET never creates state.

        for token in [d,legacy]:
            runtime.get(port,PATH,token,status=403)
            send(port,token,initial,1,source.request("nope"),403,"permission_required")
        runtime.get(port,PATH+"?source=test.source-b",a,status=400)
        send(port,a,initial,2**53,source.request("invalid"),400,"bad_event_request")
        send(port,a,initial,1,source.request("foreign",account="B"),403,"permission_required")
        send(port,a,away,1,source.request("foreign-revision"),409,"source_checkpoint_conflict")
        send(port,a,peer,1,source.request("peer-revision"),409,"source_checkpoint_conflict")
        send(port,a,initial,1,legacy_batch,409,"idempotency_conflict")
        assert tables(archive) == 0   # Failed first use leaves old DB schema untouched.

        rollback = source.request("must-rollback",id="must-not-exist")
        rollback["messages"] += trusted_batch["messages"]
        send(port,a,initial,1,rollback,409,"idempotency_conflict")
        assert runtime.get(port,PATH,a) == initial and tables(archive) == 0
        assert runtime.get(port,"/api/v1/events/checkpoint",d)["checkpoint"] == 2
        initial_batch = source.request("step-a",id="must-not-exist")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(send,port,a,initial,11,initial_batch) for _ in range(2)]
            first, retry = [f.result(timeout=9) for f in results]
        assert first == retry
        assert first["receipt"] == dict(created=1,revised=0,unchanged=0,checkpoint=3)
        step = first["source_checkpoint"]
        assert step["sequence"] == 11 and step == runtime.get(port,PATH,a)
        assert tables(archive) == 2
        assert runtime.get(port,PATH,c) == peer and runtime.get(port,PATH,b) == away
        send(port,a,initial,12,initial_batch,409,"idempotency_conflict")
        send(port,a,initial,12,source.request("stale"),409,"source_checkpoint_conflict")
        send(port,a,step,11,source.request("nonincreasing"),409,"source_checkpoint_conflict")
        send(port,c,peer,1,initial_batch,409,"idempotency_conflict")
        source.post(port,a,initial_batch,409,"idempotency_conflict")
        revision = source.request("step-b",id="must-not-exist",text="新版")
        second = send(port,a,step,42,revision)
        assert second["receipt"]["revised"] == 1
        assert send(port,a,initial,11,initial_batch) == first
        closed = second["source_checkpoint"]
        assert closed["sequence"] == 42 and runtime.get(port,PATH,a) == closed
        runtime.finish(proc,port)

        # Exact old binary can reopen upgraded archive and perform old I/O
        # without advancing the new source sequence.
        old, port = start(archive, OLD)
        assert source.post(port,a,legacy_batch)["checkpoint"] == 1
        assert source.post(port,a,source.request("old-write",id="downgrade"))["created"] == 1
        runtime.finish(old,port)

        grants(scoped("test.source-a","A",rotated),src_b,src_c,desktop,trusted)
        proc, port = start(archive)
        runtime.get(port,PATH,a,status=401)
        assert runtime.get(port,PATH,rotated) == closed
        assert send(port,rotated,initial,11,initial_batch) == first
        assert runtime.get(port,PATH,b) == away
        runtime.finish(proc,port)
        separate, port = start(other)
        assert runtime.get(port,PATH,rotated) != initial
        send(port,rotated,closed,43,source.request("wrong-archive"),409,"source_checkpoint_conflict")
        runtime.finish(separate,port)

        # A different account using the same grant ID cannot see or reuse
        # the previous state's marker; removing the previous grant orphans it.
        grants(scoped("test.source-a","B",rotated),src_c,desktop)
        proc, port = start(archive)
        regranted = runtime.get(port,PATH,rotated)
        assert regranted["sequence"] == 0 and regranted["revision"] not in {
            closed["revision"], away["revision"], peer["revision"]}
        send(port,rotated,closed,43,source.request("wrong-owner","B"),409,"source_checkpoint_conflict")
        assert send(port,rotated,regranted,7,source.request("step-a","B"))["source_checkpoint"]["sequence"] == 7
        assert runtime.get(port,PATH,c) == peer
        runtime.finish(proc,port)
    finally:
        for proc in tasks:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                pipe.close()
    print("extracted 020→021→020→021: additive rollback/read-only initial GET, scoped CAS, stable original receipt across race/revision/restart/rotation, no legacy/peer/scope/archive carryover")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="lmh-progress-smoke-") as tmp:
        checked(Path(tmp))
