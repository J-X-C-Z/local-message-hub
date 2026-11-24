#!/usr/bin/env python3
"""Extracted Rust CLI + exact pinned Python Telegram adapter on synthetic official-shape exports."""
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tempfile

from integrations.telegram import adapter

spec = importlib.util.spec_from_file_location(
    "runtime_smoke", Path(__file__).with_name("smoke-linux-runtime.py"))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
BIN = runtime.BIN
BLOB = "3c62243d6ed45339404052fef63ba7bd95cdbd0d"


def fixture_message(id, sender, own=False):
    text = ["你好 ",{"type":"text_link","text":"名称🦀","href":"file:///not-followed"},"\n"]
    return dict(id=id,type="message",date="2014-02-03T21:57:03",date_unixtime="1693753543",
                text=text, text_entities=[{"type":"plain","text":"你好 "},
                    {"type":"text_link","text":"名称🦀","href":"file:///not-followed"},
                    {"type":"plain","text":"\n"}],
                **{"from":"合成作者" if own else "合成联系人","from_id":sender})


def fixture_chat(kind, id, messages):
    return {"type":kind,"id":id,"name":"合成会话","messages":messages}


def cmd(args, count=1):
    result = subprocess.run([BIN,*args],capture_output=True,timeout=7)
    if not count:
        assert result.returncode!=0 and not result.stdout, result
        assert b"not-followed" not in result.stderr
        return
    assert result.returncode==0, result.stderr.decode()
    return json.loads(result.stdout) if result.stdout else None


def model(root):
    path = Path(adapter.__file__)
    raw = path.read_bytes()
    assert hashlib.sha1(f"blob {len(raw)}\0".encode()+raw).hexdigest() == BLOB
    source = root/"result.json"
    archive = root/"native"
    archive.mkdir(mode=0o700)
    cmd(["init-live","--directory",archive])
    args = ["--source",source,"--directory",archive]
    preview = lambda who="user123":cmd(["preview-telegram-json",*args,"--account",who])
    def commit(code,who="user123",ok=True):
        return cmd(["import-telegram-json",*args,"--account",who,"--confirm",code],int(ok))

    plain = fixture_message(1,"user42")
    own = fixture_message(2,"user123",True)
    media = fixture_message(3,"user42") | {"photo":"../../not-followed","text":"ignored caption"}
    media.pop("text_entities")
    service = {"id":4,"type":"service","action":"create_group","date_unixtime":"1693754000"}
    private = fixture_chat("personal_chat",42,[plain,own,media,service])
    encoded = json.dumps(private,ensure_ascii=False).encode("utf-8")
    expected = adapter.parse_telegram_export(encoded,"user123")
    assert expected["valid"] == 4 and expected["messages"][1]["is_self"] is False

    header = {"personal_information":{"user_id":123,"phone_number":"fixture-never-copied"},
              "sessions":{"list":[{"ip":"not-followed"}]}}
    chats = [
        private,
        fixture_chat("private_group",42,[fixture_message(1,"user123",True)]),
        fixture_chat("private_supergroup",42,[fixture_message(1,"channel42")]),
        fixture_chat("public_channel",42,[fixture_message(1,"channel42")]),
    ]
    def raw_full():
        return (json.dumps(header | {"chats":{"list":chats}},ensure_ascii=False)).encode()
    source.write_bytes(raw_full())
    original = source.read_bytes()
    archive_file = archive/"native-v1.sqlite3"
    before = archive_file.read_bytes()
    p = preview()
    assert {"rows":4,"skipped_chats":1,"skipped_messages":3} == {k:v for k,v in p.items() if k!="confirmation"}
    assert archive_file.read_bytes() == before
    assert len(p["confirmation"]) == 64
    commit(p["confirmation"],"user321",ok=False)
    cmd(["preview-telegram-json",*args,"--account","user000123"],0)
    source.write_bytes(original+b"\n")
    commit(p["confirmation"],ok=False)
    assert archive_file.read_bytes() == before
    source.write_bytes(original)
    accepted = commit(p["confirmation"])
    assert (accepted["created"],accepted["unchanged"]) == (4,0)
    assert accepted == commit(p["confirmation"])
    assert source.read_bytes() == original

    def query(sql):
        db = sqlite3.connect(archive_file.as_uri()+"?mode=ro",uri=True)
        try:
            return db.execute(sql).fetchall()
        finally:
            db.close()
    got = query("SELECT id,account_id,text,timestamp,sender_id,sender_name,is_self,media_url,extra "
                "FROM messages WHERE conversation_id='tg:user:42' ORDER BY id")
    assert len(got) == 2
    for i,row in enumerate(got):
        old = expected["messages"][i]
        assert row[:6] == (f"tg:user:42:{i+1}",old["account_id"],old["text"],old["timestamp"],
                           old["sender_id"],old["sender_name"])
        assert row[6:] == (int(i==1),None,None)
    assert query("SELECT id,kind FROM conversations ORDER BY id") == [
        ("tg:channel:42","group"),("tg:chat:42","group"),("tg:user:42","private")]

    # Single-chat: explicit owner asserted (both participants may hold identical
    # IDs), exact account namespace separate; legacy unaffected by this import.
    # Build a truly different private export in which user321 is one side.
    others=fixture_chat("personal_chat",42,[fixture_message(1,"user42"),fixture_message(2,"user321",True)])
    source.write_text(json.dumps(others,ensure_ascii=False))
    p_other = preview("user321")
    assert commit(p_other["confirmation"],"user321")["created"] == 2
    assert query("SELECT account_id,count(*) FROM messages GROUP BY account_id ORDER BY account_id") == [
        ("user123",4),("user321",2)]

    # An edited previously owned row causes rollback of the earlier new row.
    modified = fixture_message(1,"user42") | {"text":"edited"}
    modified.pop("text_entities")
    conflict_chat = fixture_chat("personal_chat",42,[fixture_message(9,"user42"),modified])
    source.write_text(json.dumps(conflict_chat,ensure_ascii=False))
    commit(preview()["confirmation"],ok=False)
    assert not query("SELECT id FROM messages WHERE id LIKE '%:9'")
    assert query("SELECT count(*) FROM native_event_journal") == [(0,)]
    symlink = root/"symlink.json"
    symlink.symlink_to(source)
    cmd(["preview-telegram-json","--source",symlink,"--directory",archive,"--account","user123"],0)
    oversize = root/"too-large.json"
    oversize.write_bytes(b" "* (512*1024+1))
    cmd(["preview-telegram-json","--source",oversize,"--directory",archive,"--account","user123"],0)

    config = root/"grants.json"
    config.touch(mode=0o600)
    token = secrets.token_hex(32)
    config.write_text(json.dumps({"version":1,"grants":[{
        "id":"fixture.desktop","kind":"desktop","account_scope":{"platform":"telegram","account_id":"user123"},
        "permissions":["conversations.read","messages.read","events.read"],
        "token_sha256":hashlib.sha256(token.encode()).hexdigest()}]}))
    job = runtime.start(archive,config)
    try:
        port,_ = runtime.ready(job)
        assert runtime.get(port,"/api/v1/events/checkpoint",token)["checkpoint"] == 0
        read = runtime.get(port,"/api/v1/messages?conversation_id=tg%3Auser%3A42",token)
        assert {r["id"] for r in read["items"]} == {"tg:user:42:1","tg:user:42:2"}
        runtime.get(port,"/api/v1/messages?account_id=user321",token,status=403)
        runtime.finish(job,port)
    finally:
        if job.poll() is None:
            job.kill()
            job.wait(timeout=5)
        for pipe in (job.stdin,job.stdout,job.stderr):
            pipe.close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="lmh-telegram-") as tmp:
        model(Path(tmp))
    print("extracted Rust Telegram import: official single/full synthetic shapes, pinned Python shared fields, IDs/owner differences, independent accounts, stable retry, fail-closed rollback, no path/network reads or journal")
