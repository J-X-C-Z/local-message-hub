#!/usr/bin/env python3
"""Reference one-batch broker for explicitly trusted local executables (Linux)."""

import argparse
from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time

LIMIT = 530 * 1024
MAX_SEQUENCE = 9007199254740991
PLATFORMS = {"qq", "wechat", "wecom", "telegram", "email"}
CHECKPOINT = "/api/v1/source/checkpoint"
INGEST = "/api/v1/source/ingest"
ORIGIN = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})\Z", re.ASCII)
ID = re.compile(r"[a-z](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z](?:[a-z0-9-]*[a-z0-9])?)*\Z", re.ASCII)
HEX_TOKEN = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
REVISION = re.compile(r"s1-[a-f0-9]{64}:[0-9]{1,16}\Z", re.ASCII)
PROTOCOL = 1
HTTP_SECONDS = 5
PLUGIN_SECONDS = 5


class Error(Exception):
    """Only constant, redacted error identifiers may enter this exception."""


def fail(code):
    raise Error(code)


def pairs(pairs_):
    obj = {}
    for k, v in pairs_:
        if k in obj:
            fail("invalid_json")
        obj[k] = v
    return obj


def bad_number(_):
    fail("invalid_json")


def parse(data):
    try:
        return json.loads(data.decode("utf-8", "strict"), object_pairs_hook=pairs,
                          parse_constant=bad_number)
    except (ValueError, RecursionError):
        fail("invalid_json")


def encode(obj):
    try:
        return json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, RecursionError):
        fail("invalid_json")


def exact(obj, keys):
    return type(obj) is dict and set(obj) == set(keys)


def integer(v, lo=0, hi=MAX_SEQUENCE):
    return type(v) is int and lo <= v <= hi


def select_fd(fd, mode, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        fail("pipe_timeout")
    read, write, _ = select.select([fd] if mode == "read" else [],
                                   [fd] if mode == "write" else [], [], remaining)
    if not (read or write):
        fail("pipe_timeout")


def frame(fd, limit, deadline):
    data = bytearray()
    while len(data) <= limit:
        select_fd(fd, "read", deadline)
        chunk = os.read(fd, min(8192, limit + 1 - len(data)))
        if not chunk:
            fail("pipe_closed")
        data.extend(chunk)
        index = data.find(b"\n", len(data) - len(chunk))
        if index >= 0:
            if index != len(data) - 1 or index > limit:
                fail("invalid_frame")
            return parse(bytes(data[:index]))
    fail("frame_too_large")


def send(fd, obj, deadline):
    view = memoryview(encode(obj) + b"\n")
    # Every broker-to-plugin frame is small. Chunked nonblocking writes also
    # give an explicit deadline if the trusted process stops reading.
    os.set_blocking(fd, False)
    while view:
        select_fd(fd, "write", deadline)
        try:
            sent = os.write(fd, view[:4096])
        except BlockingIOError:
            continue
        view = view[sent:]


def control():
    if not stat.S_ISFIFO(os.fstat(0).st_mode):
        fail("private_pipe_required")
    obj = frame(0, 2048, time.monotonic() + PLUGIN_SECONDS)
    if not exact(obj, {"token", "grant_id", "platform", "account_id"}):
        fail("invalid_control")
    token, grant, platform, account = (obj[k] for k in ("token","grant_id","platform","account_id"))
    if (not all(type(v) is str for v in (token,grant,platform,account))
        or not HEX_TOKEN.fullmatch(token) or len(grant) > 64 or not ID.fullmatch(grant)
        or platform not in PLATFORMS or not 1 <= len(account.encode("utf-8")) <= 256
        or account.strip() != account or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in account)):
        fail("invalid_control")
    return token, grant, {"platform":platform,"account_id":account}


def alarm(*_):
    fail("hub_timeout")


@contextmanager
def http_deadline():
    # Linux CLI runs on its main thread. Bound total read even if local
    # transport trickles bytes instead of completing its HTTP response.
    signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, HTTP_SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def rpc(port, token, method, path, payload=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=HTTP_SECONDS)
    headers = {"Authorization":"Bearer "+token,"Connection":"close","Accept":"application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    with http_deadline():
        try:
            conn.request(method,path,body=payload,headers=headers)
            response = conn.getresponse()
            if response.status != 200:
                fail({400:"hub_bad_request",401:"hub_unauthorized",403:"hub_permission_denied",
                      409:"hub_conflict",413:"hub_body_too_large",503:"hub_unavailable"}.get(
                          response.status,"unexpected_hub_response"))
            if response.headers.get_content_type() != "application/json":
                fail("unexpected_hub_response")
            data = response.read(16385)
            if len(data) > 16384:
                fail("unexpected_hub_response")
            return parse(data)
        except (OSError, http.client.HTTPException):
            fail("hub_transport")
        finally:
            conn.close()


def checkpoint(data):
    if (not exact(data, {"sequence", "revision"}) or not integer(data["sequence"])
        or type(data["revision"]) is not str or not REVISION.fullmatch(data["revision"])):
        fail("invalid_hub_checkpoint")
    return data


def discover(port, token, grant, scope):
    info = rpc(port,token,"GET","/api/v1/manifest")
    plugin = info.get("plugin") if type(info) is dict else None
    if (type(plugin) is not dict or info.get("api_version") != 1
        or info.get("backend") != "local-message-hub-rs" or info.get("mode") != "native-live"
        or plugin != {"id":grant,"kind":"scoped-producer","permissions":["messages.ingest"],
                      "account_scope":scope}
        or info.get("source_resume") != {"checkpoint":CHECKPOINT,"ingest":INGEST}):
        fail("hub_grant_mismatch")


def safe_executable(value):
    path = Path(value)
    if (not path.is_absolute() or path != path.resolve(strict=True)):
        fail("trusted_executable_required")
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
        or info.st_mode & 0o7022 or not os.access(path, os.X_OK)):
        fail("trusted_executable_required")
    return str(path)


def prepare(message, scope, current):
    if (message == {"protocol":PROTOCOL,"type":"idle"}
        and type(message["protocol"]) is int):
        return None
    if (not exact(message,{"protocol","type","batch","sequence"})
        or type(message["protocol"]) is not int or message["protocol"] != PROTOCOL
        or message["type"] != "batch" or not integer(message["sequence"],current["sequence"] + 1)):
        fail("invalid_plugin_frame")
    batch = message["batch"]
    if (not exact(batch,{"idempotency_key","messages"})
        or type(batch["idempotency_key"]) is not str
        or not 1 <= len(batch["idempotency_key"].encode("utf-8")) <= 256
        or type(batch["messages"]) is not list or not 1 <= len(batch["messages"]) <= 100):
        fail("invalid_plugin_batch")
    for row in batch["messages"]:
        if type(row) is not dict or any(row.get(k) != v for k,v in scope.items()):
            fail("plugin_scope_mismatch")
    payload = encode({"batch":batch,"advance":{
        "expected_revision":current["revision"],"sequence":message["sequence"]}})
    if len(payload) > LIMIT:
        fail("plugin_batch_too_large")
    return payload


def accepted(obj, wanted, count):
    if not exact(obj,{"receipt","source_checkpoint"}):
        fail("invalid_hub_receipt")
    receipt, progress = obj["receipt"], checkpoint(obj["source_checkpoint"])
    if (progress["sequence"] != wanted or not exact(receipt,{"created","revised","unchanged","checkpoint"})
        or not integer(receipt["checkpoint"]) or not all(integer(receipt[k],0,count)
            for k in ("created","revised","unchanged")) or sum(receipt[k] for k in
            ("created","revised","unchanged")) != count):
        fail("invalid_hub_receipt")
    # The child does not receive the CAS marker or the account event high water.
    return dict(protocol=PROTOCOL,type="committed",sequence=wanted,
                **{k:receipt[k] for k in ("created","revised","unchanged")})


def exit_status(proc, seconds=1):
    # Observe exit without reaping the leader. Its reserved PID prevents
    # accidental signalling of an unrelated reused process-group ID.
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        info = os.waitid(os.P_PID,proc.pid,os.WEXITED | os.WNOWAIT | os.WNOHANG)
        if info is not None:
            return info.si_status if info.si_code == os.CLD_EXITED else -info.si_status
        time.sleep(0.015)
    fail("plugin_exit_timeout")


def cleanup(proc):
    # This is lifecycle hygiene, not confinement. Trusted children must not
    # detach, fork unbounded work or deliberately manipulate the session.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=1)
    finally:
        proc.stdin.close()
        proc.stdout.close()


def exchange(path, port, token, scope, current):
    with tempfile.TemporaryDirectory(prefix="lmh-source-sdk-") as work:
        proc = subprocess.Popen([path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=work, start_new_session=True, close_fds=True,
            env={"PATH":"/usr/bin:/bin","LANG":"C.UTF-8","PYTHONNOUSERSITE":"1"})
        try:
            deadline = time.monotonic() + PLUGIN_SECONDS
            send(proc.stdin.fileno(),dict(protocol=PROTOCOL,type="resume",scope=scope,
                 sequence=current["sequence"]),deadline)
            incoming = frame(proc.stdout.fileno(),LIMIT,deadline)
            payload = prepare(incoming,scope,current)
            if payload is None:
                result = {"status":"idle","sequence":current["sequence"]}
            else:
                obj = rpc(port,token,"POST",INGEST,payload)
                committed = accepted(obj,incoming["sequence"],len(incoming["batch"]["messages"]))
                try:
                    send(proc.stdin.fileno(),committed,time.monotonic() + 1)
                except (Error,OSError):
                    fail("commit_outcome_check_checkpoint")
                result = {"status":"committed","sequence":committed["sequence"]}
            proc.stdin.close()
            if exit_status(proc) != 0:
                fail("plugin_exit_failed")
            # Reject unsolicited output; at most the single POST has happened.
            if select.select([proc.stdout.fileno()],[],[],0)[0] and os.read(proc.stdout.fileno(),1):
                fail("unexpected_plugin_output")
            return result
        finally:
            cleanup(proc)


class Arguments(argparse.ArgumentParser):
    def error(self, _):
        fail("invalid_arguments")


def stop(*_):
    fail("broker_interrupted")


def run():
    signal.signal(signal.SIGINT,stop)
    signal.signal(signal.SIGTERM,stop)
    parser = Arguments(description=__doc__,allow_abbrev=False)
    parser.add_argument("--origin",required=True)
    parser.add_argument("--plugin",required=True)
    args = parser.parse_args()
    match = ORIGIN.fullmatch(args.origin)
    if not match or not 1 <= int(match[1]) <= 65535:
        fail("literal_loopback_origin_required")
    port = int(match[1])
    path = safe_executable(args.plugin)
    token, grant, scope = control()
    discover(port,token,grant,scope)
    current = checkpoint(rpc(port,token,"GET",CHECKPOINT))
    return exchange(path,port,token,scope,current)


if __name__ == "__main__":
    try:
        print(json.dumps(run(),separators=(",",":")),flush=True)
    except Error as err:
        print(json.dumps({"error":str(err)},separators=(",",":")),file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, RecursionError, subprocess.SubprocessError):
        # Never send child output, paths, archive records or credentials to logs.
        print('{"error":"broker_io_failed"}',file=sys.stderr)
        sys.exit(1)
