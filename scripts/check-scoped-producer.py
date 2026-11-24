#!/usr/bin/env python3
"""Scoped source grant smoke on the extracted Linux runtime; synthetic accounts only."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import subprocess
import tempfile

from jsonschema import Draft202012Validator

spec = importlib.util.spec_from_file_location(
    "runtime_smoke", Path(__file__).with_name("smoke-linux-runtime.py")
)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
INGEST = "/api/v1/ingest"
MANIFEST = "/api/v1/manifest"


def grant(id, kind, scope, permissions, token):
    g = dict(id=id, kind=kind, permissions=permissions,
             token_sha256=hashlib.sha256(token.encode()).hexdigest())
    if scope:
        g["account_scope"] = dict(platform=scope[0], account_id=scope[1])
    return g


def request(key, account="A", id="same-id", text="测试", **extra):
    return dict(idempotency_key=key, messages=[dict(
        platform="qq", account_id=account, message_id=id, conversation_id="test-room",
        timestamp=1724123456, text=text, is_self=False,
    ) | extra])


def post(port, token, payload, status=200, code=None):
    conn, response = runtime.dial(port, "POST", INGEST, token, payload)
    try:
        data = json.loads(response.read())
        assert response.status == status, (response.status, data)
        if code:
            assert data == {"error": {"code": code}}
        return data
    finally:
        conn.close()


def check(root):
    archive = root / "native"
    archive.mkdir(mode=0o700)
    subprocess.run([runtime.BIN, "init-live", "--directory", archive],
                   check=True, capture_output=True, timeout=8)
    config = root / "grants.json"
    config.touch(mode=0o600)
    a, b, d, legacy, rotated = [secrets.token_hex(32) for _ in range(5)]
    src_a = grant("test.source-a", "scoped-producer", ("qq", "A"), ["messages.ingest"], a)
    src_b = grant("test.source-b", "scoped-producer", ("qq", "B"), ["messages.ingest"], b)
    desktop = grant("test.desktop", "desktop", ("qq", "A"), ["messages.read", "events.read"], d)
    trusted = grant("test.trusted", "producer", None, ["messages.ingest"], legacy)
    schema = json.loads((runtime.BIN.parent / "plugin-grants.schema.json").read_text())
    validator = Draft202012Validator(schema)
    validator.check_schema(schema)

    def set_grants(grants):
        doc = dict(version=1, grants=grants)
        validator.validate(doc)
        config.write_text(json.dumps(doc))

    set_grants([src_a, src_b, desktop, trusted])
    assert not validator.is_valid(dict(version=1, grants=[src_a | {"account_scope": None}]))
    assert not validator.is_valid(dict(version=1, grants=[src_a | {
        "permissions": ["messages.ingest", "messages.read"]}]))
    assert not validator.is_valid(dict(version=1, grants=[{
        k: v for k, v in src_a.items() if k != "account_scope"}]))
    tasks = []
    try:
        proc = runtime.start(archive, config)
        tasks.append(proc)
        port, _ = runtime.ready(proc)
        contract = runtime.get(port, "/api/v1/openapi.json", a)
        actual = runtime.get(port, MANIFEST, a)
        properties = contract["paths"][MANIFEST]["get"]["responses"]["200"]["content"]
        Draft202012Validator(properties["application/json"]["schema"]).validate(actual)
        assert actual["plugin"] == {
            "id": "test.source-a", "kind": "scoped-producer",
            "permissions": ["messages.ingest"], "account_scope": {"platform": "qq", "account_id": "A"}
        }
        assert {k: v for k, v in actual["resources"].items() if v is not None} == {"ingest": INGEST}
        for route in ("/status", "/messages", "/conversations", "/contacts", "/search",
                      "/events", "/events/checkpoint", "/events/stream", "/analytics/pair",
                      "/metadata/audit"):
            assert runtime.get(port, "/api/v1" + route, a, status=403) == {
                "error": {"code": "permission_required"}}
        post(port, d, request("blocked-desktop"), 403, "permission_required")
        post(port, None, request("blocked-no-token"), 401, "unauthorized")
        mixed = request("mixed")
        mixed["messages"].append(request("irrelevant", account="B", id="mixed-foreign")["messages"][0])
        post(port, a, mixed, 403, "permission_required")
        post(port, a, request("case", account="a"), 403, "permission_required")
        post(port, a, request("platform", platform="wechat"), 403, "permission_required")
        post(port, a, request("omitted-as-empty", account=""), 403, "permission_required")
        assert runtime.get(port, "/api/v1/messages?conversation_id=test-room", d)["items"] == []
        over_count = dict(idempotency_key="over-count", messages=[
            request("unused", id=str(i))["messages"][0] for i in range(101)
        ])
        post(port, a, over_count, 400, "bad_event_request")
        post(port, a, request("over-transport", text="x" * (531 * 1024)), 413, "ingest_body_too_large")
        assert runtime.get(port, "/api/v1/events/checkpoint", d)["checkpoint"] == 0
        # Seed two B events with independent trusted and scoped identities.
        trusted_other = request("old-key", "B", "trusted-id")
        receipt_old = post(port, legacy, trusted_other)
        assert receipt_old["checkpoint"] == 1
        assert post(port, b, request("same-key", "B"))["checkpoint"] == 2
        conflict = request("old-key", "B", "would-be-new")
        conflict["messages"].extend(trusted_other["messages"])
        post(port, b, conflict, 409, "idempotency_conflict")
        assert post(port, b, request("after-conflict", "B", "would-be-new", is_self=True)) == {
            "created": 1, "revised": 0, "unchanged": 0, "checkpoint": 2
        }
        payload_a = request("same-key")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(post, port, a, payload_a) for _ in range(2)]
            receipts = [f.result(timeout=9) for f in futures]
        assert receipts[0] == receipts[1] == dict(created=1, revised=0, unchanged=0, checkpoint=1)
        post(port, a, request("same-key", text="changed"), 409, "idempotency_conflict")
        post(port, a, request("same-key", "B"), 403, "permission_required")
        assert post(port, a, request("own", id="own", is_self=True))["checkpoint"] == 1
        assert post(port, a, request("edit-key", text="修订"))["checkpoint"] == 2
        assert post(port, a, payload_a) == receipts[0]
        check1 = runtime.get(port, "/api/v1/events/checkpoint", d)
        assert check1["checkpoint"] == 2 and isinstance(check1["cursor"], str)
        rows = runtime.get(port, "/api/v1/messages?conversation_id=test-room", d)["items"]
        assert {r["id"]: r["text"] for r in rows} == {"same-id": "修订", "own": "测试"}
        runtime.finish(proc, port)

        # Same ID and scope with fresh token keeps receipt and ownership.
        same_a = grant("test.source-a", "scoped-producer", ("qq", "A"), ["messages.ingest"], rotated)
        set_grants([same_a, src_b, desktop, trusted])
        proc = runtime.start(archive, config)
        tasks.append(proc)
        port, _ = runtime.ready(proc)
        post(port, a, payload_a, 401, "unauthorized")
        assert post(port, rotated, payload_a) == receipts[0]
        assert post(port, legacy, trusted_other) == receipt_old
        assert runtime.get(port, "/api/v1/events/checkpoint", d) == check1
        runtime.finish(proc, port)

        # Regrant the same ID to another exact scope; it cannot consume A's
        # receipt or the previous B writer's ownership. Its own key is free.
        regrant_b = grant("test.source-a", "scoped-producer", ("qq", "B"), ["messages.ingest"], rotated)
        set_grants([regrant_b, desktop, trusted])
        proc = runtime.start(archive, config)
        tasks.append(proc)
        port, _ = runtime.ready(proc)
        post(port, rotated, payload_a, 403, "permission_required")
        post(port, rotated, request("same-key", "B"), 409, "idempotency_conflict")
        assert post(port, rotated, request("same-key", "B", "new"))["checkpoint"] == 3
        assert runtime.get(port, "/api/v1/events/checkpoint", d) == check1
        runtime.finish(proc, port)
    finally:
        for proc in tasks:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                pipe.close()
    print("extracted runtime: scoped producer discovery/schema, write-only, exact/mixed scope denial, colliding IDs, atomic retry race, account-local checkpoint, identity isolation, token rotation and restart")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="lmh-source-smoke-") as path:
        check(Path(path))
