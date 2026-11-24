#!/usr/bin/env python3
"""Minimal out-of-process desktop plugin client; standard Python 3 only."""
import argparse
import getpass
import http.client
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlencode

MAX_JSON = 5 * 1024 * 1024
MAX_SSE_LINE = 64 * 1024
CURSOR = re.compile(r"[0-9a-f]{32}:[0-9]{1,19}")


def read_cursor(path):
    try:
        with path.open("rb") as file:
            raw = file.read(128)
    except FileNotFoundError:
        return None
    cursor = raw.decode("ascii").strip()
    if not CURSOR.fullmatch(cursor):
        raise RuntimeError("invalid saved cursor")
    return cursor


def save_cursor(path, cursor):
    if not CURSOR.fullmatch(cursor):
        raise RuntimeError("invalid host cursor")
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="ascii", dir=path.parent, prefix=".cursor-", delete=False
    ) as out:
        stage = Path(out.name)
        try:
            out.write(cursor + "\n")
            out.flush()
            os.fsync(out.fileno())
            out.close()
            os.replace(stage, path)
        finally:
            stage.unlink(missing_ok=True)


def get_json(connection, target, headers):
    if not target.startswith("/api/v1/"):
        raise RuntimeError("unrecognized local resource")
    with connection() as conn:
        conn.request("GET", target, headers=headers)
        response = conn.getresponse()
        payload = response.read(MAX_JSON + 1)
        if len(payload) > MAX_JSON:
            raise RuntimeError("response exceeds the client limit")
        data = json.loads(payload)
        if response.status != 200:
            raise RuntimeError(f"host error {response.status}: {data['error']['code']}")
        return data


def watch(connect, target, headers, saved, path, maximum):
    # Main process owns the bearer; browser EventSource cannot set that header.
    conn = connect()
    try:
        conn.request("GET", target, headers={**headers, "Last-Event-ID": saved})
        response = conn.getresponse()
        if response.status != 200:
            detail = json.loads(response.read(MAX_JSON + 1))["error"]["code"]
            raise RuntimeError(f"event stream refused ({response.status}): {detail}")
        frame = {}
        events = 0
        while True:
            line = response.readline(MAX_SSE_LINE + 1)
            if len(line) > MAX_SSE_LINE:
                raise RuntimeError("event exceeds client line limit")
            if not line:
                break
            if line in (b"\n", b"\r\n"):
                if frame.get("event") in ("message.created", "message.revised"):
                    cursor = frame["id"]
                    event = json.loads(frame["data"])
                    # Real UIs would refresh/upsert by the scoped message ID here.
                    print(json.dumps({"cursor": cursor, "event": event}, ensure_ascii=False),
                          flush=True)
                    save_cursor(path, cursor)  # persist only after applying/printing
                    events += 1
                    if maximum is not None and events >= maximum:
                        break
                frame.clear()
            elif line.startswith((b"id:", b"event:", b"data:")):
                key, value = line.rstrip(b"\r\n").split(b":", 1)
                frame[key.decode("ascii")] = value.lstrip(b" ").decode("utf-8")
    finally:
        conn.close()


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="origin from the host ready event")
    parser.add_argument("--cursor-file", type=Path, help="follow events and save the applied cursor")
    parser.add_argument("--max-events", type=int, help="stop after N events instead of following")
    args = parser.parse_args()
    if args.max_events is not None and (args.max_events < 1 or args.cursor_file is None):
        parser.error("--max-events must be positive and needs --cursor-file")
    origin = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", args.origin)
    if not origin or int(origin.group(1)) > 65535:
        parser.error("origin must be an explicit http://127.0.0.1:PORT")
    token = os.environ.get("LMH_PLUGIN_TOKEN") or getpass.getpass("Plugin token: ")
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        parser.error("expected a 64-character lowercase hex plugin token")
    port = int(origin.group(1))

    def connection():
        return http.client.HTTPConnection("127.0.0.1", port, timeout=20)

    # HTTPConnection is not a Python context manager; make cleanup explicit.
    from contextlib import closing

    def query(target):
        return get_json(lambda: closing(connection()), target, {"Authorization": f"Bearer {token}"})

    headers = {"Authorization": f"Bearer {token}"}
    contract = query("/api/v1/manifest")
    if contract["api_version"] != 1 or contract["plugin_protocol_version"] != 1:
        raise RuntimeError("unsupported host/plugin contract version")
    resource = contract["resources"]["conversations"]
    if resource is None:
        raise RuntimeError("grant does not permit conversations.read")
    saved = None
    if args.cursor_file:
        if not contract["resources"].get("event_stream"):
            raise RuntimeError("native live mode and events.read are needed to watch")
        saved = read_cursor(args.cursor_file)
        if saved is None:
            # Capture high water BEFORE initial reads so a concurrent commit is never lost.
            saved = query(contract["resources"]["event_checkpoint"])["cursor"]
    page = query(f"{resource}?{urlencode({'limit': 10})}")
    print(json.dumps({"plugin": contract["plugin"]["id"], "total": page["total"],
                      "items": [{"id": row["id"], "title": row["title"]}
                                for row in page["items"]]}, ensure_ascii=False), flush=True)
    if args.cursor_file:
        save_cursor(args.cursor_file, saved)
        watch(connection, contract["resources"]["event_stream"], headers, saved,
              args.cursor_file, args.max_events)


if __name__ == "__main__":
    run()
