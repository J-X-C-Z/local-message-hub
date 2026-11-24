#!/usr/bin/env python3
"""Old Hub QQ CSV generator/interpreter versus extracted Rust CLI, synthetic only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import tempfile

from hub import exports, imports

spec = importlib.util.spec_from_file_location(
    "runtime_smoke", Path(__file__).with_name("smoke-linux-runtime.py"))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
BIN = runtime.BIN


def exact_blob(module, expected):
    raw=Path(module.__file__).read_bytes()
    assert hashlib.sha1(f"blob {len(raw)}\0".encode()+raw).hexdigest()==expected


def message(id,account="sample-A",sender="literal-peer",text="合成, \"字\"\r\n🦀",own=False,**extra):
    return dict(platform="qq",account_id=account,conversation_id="frozen-room",
                conversation_title="合成会话",sender_id=sender,sender_name="合成发送者",
                id=id,timestamp=1_724_123_456,is_self=own,type="text",text=text) | extra


def output(args, ok=True):
    done=subprocess.run([BIN,*args],capture_output=True,timeout=8)
    if not ok:
        assert done.returncode!=0 and not done.stdout, done
        for sensitive in (b"sample-A",b"literal-self",b"not-opened"):
            assert sensitive not in done.stderr
        return None
    assert done.returncode==0,done.stderr.decode()
    return json.loads(done.stdout) if done.stdout else None


def checked(root):
    exact_blob(exports,"c684c4e62f6a0d9a83d67a80e96669f235b5f698")
    exact_blob(imports,"c1b524be08b100100583c7d5f3f5352c24a1c752")
    archive=root/"native"
    archive.mkdir(mode=0o700)
    output(["init-live","--directory",archive])
    dbfile=archive/"native-v1.sqlite3"
    source=root/"messages.csv"
    args=["--source",source,"--directory",archive]

    def preview(account="sample-A",sender="literal-self",ok=True,path=source):
        return output(["preview-qq-hub-csv","--source",path,"--directory",archive,
                       "--account",account,"--self-sender",sender],ok)
    def commit(code,account="sample-A",sender="literal-self",ok=True):
        return output(["import-qq-hub-csv",*args,"--account",account,"--self-sender",sender,"--confirm",code],ok)
    def csv(rows, target=source):
        data=exports.render_export(rows,"csv")[0]
        target.write_bytes(data)
        return data
    def query(sql):
        db=sqlite3.connect(dbfile.as_uri()+"?mode=ro",uri=True)
        try:
            return db.execute(sql).fetchall()
        finally:
            db.close()

    normal=message("same-id")
    own=message("mine",sender="literal-self",text="=not executed",own=True)
    protected=message("apostrophe",text="'=already literal")
    media=message("media",type="image",media_url="file:///not-opened",file_name="not-opened")
    attached=message("text-media",extra={"roaming_resources":[
        {"type":"image","name":"not-opened","file_size":100}]})
    original=csv([normal,own,protected,media,attached])
    old=imports.parse_import(original,"csv","qq","sample-A")
    assert old["valid"]==5 and old["messages"][1]["is_self"] is False
    assert (old["messages"][1]["text"],old["messages"][2]["text"])==(
        "'=not executed","'=already literal")

    before=dbfile.read_bytes()
    p=preview()
    p_peer=preview(sender="literal-peer")
    assert p["confirmation"]!=p_peer["confirmation"]
    assert (p["rows"],p["skipped"])==(3,2) and dbfile.read_bytes()==before
    preview(account="sample-B",ok=False)
    preview(sender="wrong-sender",ok=False)
    commit(p["confirmation"],sender="literal-peer",ok=False)
    csv([normal,own,media|{"account_id":"sample-B"}])
    preview(ok=False)
    source.write_bytes(original[:-2])
    preview(ok=False)
    source.write_bytes(b"{\"messages\":[]}")
    preview(ok=False)
    source.write_bytes(original+b"\r\n")
    preview(ok=False)
    source.write_bytes(original)
    symlink=root/"redirected.csv"
    symlink.symlink_to(source)
    preview(ok=False,path=symlink)
    giant=root/"too-large.csv"
    giant.write_bytes(b" "* (512*1024+1))
    preview(ok=False,path=giant)
    assert dbfile.read_bytes()==before

    # 008 old JSON and this CSV share regular legacy import ownership
    # for lossless rows, and use independent receipt domains.
    old_path=root/"same-old-hub-export.json"
    old_path.write_bytes(exports.render_export([normal],"json")[0])
    old_args=["--source",old_path,"--directory",archive,"--platform","qq","--account","sample-A"]
    old_check=output(["preview-legacy-json",*old_args])
    output(["import-legacy-json",*old_args,"--confirm",old_check["confirmation"]])
    receipt=commit(p["confirmation"])
    assert (receipt["created"],receipt["unchanged"],receipt["skipped"])==(2,1,2)
    assert receipt==commit(p["confirmation"]) and source.read_bytes()==original
    got=query("SELECT id,text,timestamp,sender_id,sender_name,is_self,media_url,file_name,extra "
              "FROM messages ORDER BY id")
    old_by_id={row["id"]:row for row in old["messages"][:3]}
    assert len(got)==3
    for id,text,ts,sender,name,is_self,media_url,file_name,extra in got:
        expected=old_by_id[id]
        assert (text,ts,sender,name)==tuple(expected[k] for k in ("text","timestamp","sender_id","sender_name"))
        assert is_self==int(id=="mine") and all(v is None for v in (media_url,file_name,extra))

    # BOM compatibility and same literal source IDs under another account.
    second=csv([normal|{"account_id":"sample-B"},own|{"account_id":"sample-B"}])
    source.write_bytes(b"\xef\xbb\xbf"+second)
    p_b=preview(account="sample-B")
    assert commit(p_b["confirmation"],account="sample-B")["created"]==2
    assert query("SELECT account_id,count(*) FROM messages GROUP BY account_id ORDER BY 1")==[("sample-A",3),("sample-B",2)]

    changed=normal|{"text":"attempt a revision"}
    csv([message("must-roll-back"),changed,own])
    commit(preview()["confirmation"],ok=False)
    assert not query("SELECT id FROM messages WHERE id='must-roll-back'")
    assert query("SELECT count(*) FROM native_event_journal")==[(0,)]

    cfg=root/"grants.json"
    cfg.touch(mode=0o600)
    token=secrets.token_hex(32)
    cfg.write_text(json.dumps({"version":1,"grants":[{
        "id":"fixture.desktop","kind":"desktop","account_scope":{"platform":"qq","account_id":"sample-A"},
        "permissions":["messages.read","events.read"],
        "token_sha256":hashlib.sha256(token.encode()).hexdigest()}]}))
    proc=runtime.start(archive,cfg)
    try:
        port,_=runtime.ready(proc)
        read=runtime.get(port,"/api/v1/messages?conversation_id=frozen-room",token)
        assert {r["id"] for r in read["items"]}=={"mine","same-id","apostrophe"}
        assert runtime.get(port,"/api/v1/events/checkpoint",token)["checkpoint"]==0
        runtime.get(port,"/api/v1/messages?account_id=sample-B",token,status=403)
        runtime.finish(proc,port)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        for pipe in (proc.stdin,proc.stdout,proc.stderr):
            pipe.close()


if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="lmh-qq-csv-") as directory:
        checked(Path(directory))
    print("extracted Rust legacy Hub QQ CSV: exact pinned writer/interpreter shared fields, sender confirmation, literal escaping, no media, cross-format dedupe, isolated accounts, rollback and restart")
