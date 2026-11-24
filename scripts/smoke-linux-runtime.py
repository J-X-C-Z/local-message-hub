#!/usr/bin/env python3
"""Exercise the exact Linux binary using temporary synthetic data; never installs it."""
import ctypes
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time

TIMEOUT = 8
BIN = Path(sys.argv[1]).resolve(strict=True)


def jsonline(task):
    assert select.select([task.stdout], [], [], TIMEOUT)[0], "supervisor stdout timeout"
    line = task.stdout.readline(2049)
    assert line and line.endswith(b"\n") and len(line) < 2049, "invalid supervisor frame"
    return json.loads(line)


def start(target, grants):
    return subprocess.Popen(
        [BIN, "desktop-supervise", "--live", target, "--grants", grants],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        env={**os.environ, "LMH_SMOKE_ENV_SENTINEL": "not-forwarded"},
    )


def dial(port, method, path, token=None, content=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT)
    fields = {"Connection": "close", **(headers or {})}
    if token:
        fields["Authorization"] = "Bearer " + token
    if content is not None:
        fields["Content-Type"] = "application/json"
        content = json.dumps(content).encode()
    conn.request(method, path, body=content, headers=fields)
    return conn, conn.getresponse()


def get(port, path, token=None, status=200):
    conn, res = dial(port, "GET", path, token)
    try:
        assert res.status == status
        return json.loads(res.read())
    finally:
        conn.close()


def ready(task):
    frame = jsonline(task)
    assert list(sorted(frame)) == ["api_version", "backend_pid", "event", "mode", "origin"]
    assert (frame["api_version"], frame["event"], frame["mode"]) == (1, "ready", "native-live")
    port = int(frame["origin"].removeprefix("http://127.0.0.1:"))
    assert 0 < port <= 65535
    assert get(port, "/healthz") == {"status": "ok"}
    assert frame["backend_pid"] > 1
    return port, frame["backend_pid"]


def wait_closed(port):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.02)
    raise AssertionError("backend listener remained open")


def finish(task, port, forced=False, eof=False):
    if eof:
        task.stdin.close()
    else:
        task.stdin.write(b"stop\n")
        task.stdin.flush()
    assert jsonline(task) == {"event": "stopped", "forced": forced}
    assert task.wait(timeout=4) == 0
    assert not task.stdout.read() and not task.stderr.read()
    wait_closed(port)


def sse_frame(response):
    fields = {}
    for _ in range(8):
        item = response.readline(8193)
        assert item and len(item) < 8193
        if item == b"\n":
            return fields
        if b":" in item:
            key, value = item.decode().strip().split(":", 1)
            fields[key] = value.strip()
    raise AssertionError("oversized event frame")


def check_reap(pid):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        ended, status = os.waitpid(pid, os.WNOHANG)
        if ended:
            assert ended == pid and os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
            return
        time.sleep(0.02)
    raise AssertionError("parent-death child did not exit")


def smoke(root):
    archive = root / "native"
    archive.mkdir(mode=0o700)
    subprocess.run([BIN, "init-live", "--directory", archive], check=True, capture_output=True, timeout=TIMEOUT)
    grants = root / "desktop-grants.json"
    grants.touch(mode=0o600)
    desktop, producer = secrets.token_hex(32), secrets.token_hex(32)
    grants.write_text(json.dumps({"version": 1, "grants": [
        {"id": "smoke.desktop", "kind": "desktop", "account_scope": {"platform": "qq", "account_id": "synthetic"},
         "permissions": ["messages.read", "events.read"], "token_sha256": hashlib.sha256(desktop.encode()).hexdigest()},
        {"id": "smoke.producer", "kind": "producer", "permissions": ["messages.ingest"],
         "token_sha256": hashlib.sha256(producer.encode()).hexdigest()},
    ]}))
    tasks = []
    stream = None
    try:
        task = start(archive, grants)
        tasks.append(task)
        port, child = ready(task)
        for pid in (task.pid, child):
            argv = Path(f"/proc/{pid}/cmdline").read_bytes()
            assert all(val.encode() not in argv for val in (desktop, producer))
        assert b"LMH_SMOKE_ENV_SENTINEL" not in Path(f"/proc/{child}/environ").read_bytes()
        get(port, "/api/v1/manifest", status=401)
        manifest = get(port, "/api/v1/manifest", desktop)
        assert manifest["mode"] == "native-live" and manifest["plugin"]["id"] == "smoke.desktop"
        checkpoint = get(port, "/api/v1/events/checkpoint", desktop)
        stream, events = dial(port, "GET", "/api/v1/events/stream", desktop)
        assert events.status == 200
        delivery = {
            "idempotency_key": "synthetic-package-smoke",
            "messages": [{"platform": "qq", "account_id": "synthetic", "conversation_id": "test-room",
                          "message_id": "generated-1", "timestamp": 1724123456,
                          "is_self": False, "text": "synthetic package smoke"}],
        }
        sender, res = dial(port, "POST", "/api/v1/ingest", producer, delivery)
        assert res.status == 200 and json.loads(res.read())["created"] == 1
        sender.close()
        assert sse_frame(events)["event"] == "checkpoint"
        frame = sse_frame(events)
        assert frame["event"] == "message.created"
        assert json.loads(frame["data"])["message_id"] == "generated-1"
        if "cursor" in checkpoint:
            assert frame["id"] != checkpoint["cursor"]
        assert get(port, "/api/v1/messages?conversation_id=test-room", desktop)["items"][0]["id"] == "generated-1"
        # Leaving authenticated SSE connected exercises the bounded forced-stop fallback.
        finish(task, port, forced=True)
        stream.close()
        stream = None
        print("extracted binary: health, scoped plugin auth, ingest, message read, live SSE, stop/reap")

        normal = start(archive, grants)
        tasks.append(normal)
        reopened, _ = ready(normal)
        assert get(reopened, "/api/v1/messages?conversation_id=test-room", desktop)["items"][0]["id"] == "generated-1"
        assert get(reopened, "/api/v1/events/checkpoint", desktop) != checkpoint
        finish(normal, reopened, eof=True)
        print("stdin EOF: clean graceful shutdown, previous journal persisted")

        # Become a child subreaper only in this isolated test, to reap the
        # backend if its parent is killed before it can wait on the child.
        libc = ctypes.CDLL(None, use_errno=True)
        assert libc.prctl(36, 1, 0, 0, 0) == 0  # PR_SET_CHILD_SUBREAPER
        killed = start(archive, grants)
        tasks.append(killed)
        port, child = ready(killed)
        killed.kill()
        assert killed.wait(timeout=4) == -signal.SIGKILL
        wait_closed(port)
        check_reap(child)
        print("supervisor SIGKILL: Linux parent-death signal terminated backend")

        invalid_control = start(archive, grants)
        tasks.append(invalid_control)
        port, _ = ready(invalid_control)
        invalid_control.stdin.write(b"invalid\n")
        assert invalid_control.wait(timeout=4) != 0
        assert b"control frame too long" in invalid_control.stderr.read()
        wait_closed(port)
        print("malformed control: supervisor closed backend and failed safely")

        grants.chmod(0o644)
        rejected = start(archive, grants)
        tasks.append(rejected)
        assert rejected.wait(timeout=4) != 0
        assert not rejected.stdout.read()
        assert b"private regular grant file" in rejected.stderr.read()
        print("non-private grant file refused before readiness")
    finally:
        if stream is not None:
            stream.close()
        for task in tasks:
            if task.poll() is None:
                task.kill()
                task.wait(timeout=4)
            for pipe in (task.stdin, task.stdout, task.stderr):
                pipe.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="lmh-runtime-smoke-") as path:
        smoke(Path(path))
