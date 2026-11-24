#!/usr/bin/python3
"""One synthetic record. Never reads files, providers or credentials."""
import json
import os
import sys


def read():
    line = sys.stdin.buffer.readline(2049)
    if len(line) > 2048 or not line.endswith(b"\n"):
        raise SystemExit(1)
    return json.loads(line)


def send(value):
    print(json.dumps(value, ensure_ascii=False, separators=(",",":")), flush=True)


def main():
    start = read()
    if (set(os.environ) - {"PATH", "LANG", "PYTHONNOUSERSITE","LC_CTYPE"}
        or len(sys.argv) != 1 or start["type"] != "resume" or start["protocol"] != 1
        or set(start) != {"protocol","type","scope","sequence"}):
        return 1
    if start["sequence"] > 0:
        send(dict(protocol=1,type="idle"))
        return 0
    row = dict(start["scope"],conversation_id="synthetic-room",message_id="synthetic-message",
               timestamp=1_724_123_456,text="合成消息",is_self=False)
    send(dict(protocol=1,type="batch",sequence=1,
              batch=dict(idempotency_key="synthetic-source-1",messages=[row])))
    reply = read()
    return int(reply != {"protocol":1,"type":"committed","sequence":1,
                         "created":1,"revised":0,"unchanged":0})


if __name__ == "__main__":
    sys.exit(main())
