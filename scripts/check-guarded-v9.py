#!/usr/bin/env python3
"""Exercise actual guarded v9 import and legacy read endpoints with a pinned backup."""
import hashlib
import http.client
import json
from pathlib import Path
import secrets
import select
import sqlite3
import subprocess
import sys
import tempfile

from hub.store import ChatStore

BIN=Path(sys.argv[1]).resolve(strict=True)
SOURCE_ID="765432198765432"
PLATFORM="wechat"


def raw(value):
    return (json.dumps(value,ensure_ascii=False)+"\n").encode()


def run(args,frame):
    return subprocess.run([BIN,*args],input=frame,capture_output=True,timeout=12)


def ok(result):
    assert result.returncode==0,result.stderr.decode()
    assert not result.stderr
    return json.loads(result.stdout)


def source_args(command,source,target,account,confirm=None):
    args=[command,"--source",source,"--directory",target,"--platform",PLATFORM,"--account",account]
    if confirm is not None:
        args.extend(["--confirm",confirm])
    return args


def prepare(store,account,server_id):
    store.ingest(PLATFORM,account,[{
        "id":"room","title":"会议 "+account,"avatar_url":"file:///legacy-provider-avatar",
        "private_proxy":"offline-runtime-secret"
    }],[
        {"id":"m","conversation_id":"room","type":"text","text":"原文 "+account,"timestamp":20,
         "sender_name":"某人","_hub_wechat_server_id":server_id,
         "media_url":"file:///raw-media","raw_type":"provider-type","custom_url":"https://example.invalid/media","file_name":"raw-file"},
        {"id":"ref","conversation_id":"room","type":"text","text":"引用 "+account,"timestamp":20,
         "wechat_quote":{"target_server_id":server_id,"attachment_url":"raw-pointer","body":"quoted-private-fields"}},
    ], contacts=[{"id":"same-contact","name":"联系人 "+account,"avatar_url":"file:///other-avatar",
        "extra":{"alias":"raw-contact-alias","config":"raw-private-config"}}])
    store.update_conversation_metadata(PLATFORM,account,"room",note="备注 "+account,tags=["第二","第一"],archived=True)
    store.set_contact_identity(PLATFORM,account,"same-contact","account-local-"+account)


def request(port,path,token):
    c=http.client.HTTPConnection("127.0.0.1",port,timeout=5)
    c.request("GET",path,headers={"Authorization":"Bearer "+token})
    r=c.getresponse()
    result=(r.status,json.loads(r.read()))
    c.close()
    return result


def frame(p):
    assert select.select([p.stdout],[],[],6)[0]
    return json.loads(p.stdout.readline())


def exercise(root):
    old_directory=root/"old"
    old_directory.mkdir()
    old_source=old_directory/"hub.sqlite3"
    pinned=ChatStore(old_source)
    prepare(pinned,"Alpha",SOURCE_ID)
    prepare(pinned,"alpha",SOURCE_ID)
    assert pinned.quoted_message_target_for_reference(PLATFORM,"alpha","room","ref")=="m"
    assert (old_directory/"hub.sqlite3-wal").stat().st_size>0
    source=pinned.backup_to(root/"detached"/"index.sqlite3")
    old_bytes=old_source.read_bytes()
    before=source.read_bytes()

    native=root/"guarded"
    native.mkdir(mode=0o700)
    password="synthetic 加锁备份 "+secrets.token_hex(14)
    pass_frame=raw({"passphrase":password})
    wrong=raw({"passphrase":"synthetic wrong "+secrets.token_hex(14)})
    ok(run(["init-guarded-live","--directory",native],pass_frame))
    native_db=native/"native-v1.sqlite3"
    args=lambda action, account="alpha", digest=None:source_args(action,source,native,account,digest)
    preview=ok(run(args("preview-guarded-v9"),pass_frame))
    expected={"conversations":1,"messages":2,"contacts":1,"conversation_metadata":1,"contact_identities":1}
    assert preview["rows"]==expected
    proof=preview["confirmation"]
    assert ok(run(args("preview-guarded-v9","Alpha"),pass_frame))["confirmation"]!=proof
    commit=args("import-guarded-v9",digest=proof)
    assert run(commit,wrong).returncode!=0
    assert run(commit,b'{"passphrase":').returncode!=0
    assert run(args("import-guarded-v9","Alpha",proof),pass_frame).returncode!=0
    assert run(source_args("preview-guarded-v9",old_source,native,"alpha"),pass_frame).returncode!=0
    syntactic_alias=root/"alias"
    syntactic_alias.mkdir()
    (syntactic_alias/"index.sqlite3").symlink_to(source)
    assert run(source_args("preview-guarded-v9",syntactic_alias/"index.sqlite3",native,"alpha"),pass_frame).returncode!=0
    sidecar=source.with_name("index.sqlite3-wal")
    sidecar.touch()
    assert run(args("preview-guarded-v9"),pass_frame).returncode!=0
    sidecar.unlink()
    future=root/"future"
    future.mkdir()
    unsupported=future/"index.sqlite3"
    unsupported.write_bytes(before)
    with sqlite3.connect(unsupported) as db:
        db.execute("PRAGMA user_version=10")
    db.close()
    assert run(source_args("preview-guarded-v9",unsupported,native,"alpha"),pass_frame).returncode!=0
    excess=root/"excess"
    excess.mkdir()
    over_file=excess/"index.sqlite3"
    with over_file.open("wb") as file:
        file.truncate(128*1024*1024+1)
    assert run(source_args("preview-guarded-v9",over_file,native,"alpha"),pass_frame).returncode!=0
    over_file.write_bytes(before)
    with sqlite3.connect(over_file) as db:
        db.executemany("""INSERT INTO messages(platform,account_id,id,conversation_id,timestamp,type,text,is_self)
            VALUES('wechat','alpha',?,'room',3,'text','synthetic row',0)""",((f"extra-{x}",) for x in range(1000)))
    db.close()
    reject=run(source_args("preview-guarded-v9",over_file,native,"alpha"),pass_frame)
    assert reject.returncode!=0 and b"over-budget" in reject.stderr
    source.write_bytes(before+b" ")
    assert run(commit,pass_frame).returncode!=0
    source.write_bytes(before)

    # Fault injection only: force failure after conversations, contact,
    # message and metadata writes, then prove all rows rolled back.
    with sqlite3.connect(native_db) as db:
        db.execute("""CREATE TRIGGER fixture_abort_v9 BEFORE INSERT ON contact_identities
            BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END""")
    rejected=run(commit,pass_frame)
    assert rejected.returncode!=0 and b"not confirmed" in rejected.stderr
    with sqlite3.connect(native_db) as db:
        for table in (*expected,"native_event_journal","native_ingest_receipts"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]==0,table
        db.execute("DROP TRIGGER fixture_abort_v9")
    receipt=ok(run(commit,pass_frame))
    assert receipt=={"batch_id":proof,"rows":expected}
    assert ok(run(commit,pass_frame))==receipt
    assert ok(run(args("preview-guarded-v9"),pass_frame))==preview
    assert run(args("preview-guarded-v9","Alpha"),pass_frame).returncode!=0
    assert source.read_bytes()==before and old_source.read_bytes()==old_bytes

    tiny=root/"text.json"
    tiny.write_bytes(raw([{"platform":PLATFORM,"account_id":"alpha","id":"unrelated","conversation_id":"room",
        "type":"text","timestamp":1,"text":"do not merge","is_self":False}]))
    assert run(source_args("preview-guarded-legacy-json",tiny,native,"alpha"),pass_frame).returncode!=0
    with sqlite3.connect(native_db) as db:
        assert db.execute("SELECT count(*) FROM native_event_journal").fetchone()[0]==0
        assert db.execute("SELECT DISTINCT account_id FROM messages").fetchall()==[("alpha",)]
        assert db.execute("SELECT identity_id FROM contact_identities").fetchone()==("account-local-alpha",)
        cols=db.execute("SELECT id,media_url,raw_type,file_name,extra FROM messages ORDER BY id").fetchall()
        assert [(rid,url,kind,file) for rid,url,kind,file,_ in cols]==[("m",None,None,None),("ref",None,None,None)]
        assert json.loads(cols[0][4])=={"_hub_wechat_server_id":SOURCE_ID}
        assert json.loads(cols[1][4])=={"wechat_quote":{"target_server_id":SOURCE_ID}}
        assert db.execute("SELECT avatar_url,extra FROM contacts").fetchone()==(None,None)
    assert run(["serve","--live",native,"--grants",root/"missing"],b"").returncode!=0

    token=secrets.token_hex(32)
    grants=root/"grants.json"
    grants.touch(mode=0o600)
    grants.write_text(json.dumps({"version":1,"grants":[{
        "id":"fixture.desktop","kind":"desktop","account_scope":{"platform":PLATFORM,"account_id":"alpha"},
        "permissions":["legacy.http.read","messages.read","conversations.read","contacts.read"],
        "token_sha256":hashlib.sha256(token.encode()).hexdigest(),
    }]}))
    task=subprocess.Popen([BIN,"serve-guarded-legacy","--directory",native,"--grants",grants],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
    try:
        ready=frame(task)
        port=int(ready["origin"].removeprefix("http://127.0.0.1:"))
        assert ready["locked"] and request(port,"/api/contacts",token)[0]==423
        assert run(args("preview-guarded-v9"),pass_frame).returncode!=0
        task.stdin.write(raw({"command":"unlock","passphrase":password}))
        assert frame(task)=={"event":"unlocked"}
        status,res=request(port,"/api/contacts",token)
        assert status==200 and res["items"][0]["name"]=="联系人 alpha"
        assert "account-local-alpha" not in str(res) and "raw-contact-alias" not in str(res)
        status,res=request(port,"/api/conversations",token)
        assert status==200 and res["items"][0]["note"]=="备注 alpha"
        assert res["items"][0]["tags"]==["第二","第一"] and res["items"][0]["archived"]
        assert "legacy-provider-avatar" not in str(res)
        canonical="/api/quoted-message?account_id=alpha&conversation_id=room&reference_message_id=ref"
        status,res=request(port,canonical,token)
        assert status==200 and res["target_message_id"]=="m" and res["items"][res["target_index"]]["text"]=="原文 alpha"
        for secret in (SOURCE_ID,"attachment_url","quoted-private-fields","raw-pointer","raw-media"):
            assert secret not in str(res)
        assert request(port,canonical.replace("account_id=alpha","account_id=Alpha"),token)[0]==404
        task.stdin.write(raw({"command":"stop"}))
        assert frame(task)=={"event":"stopped"} and task.wait(timeout=5)==0 and not task.stderr.read()
    finally:
        if task.poll() is None:
            task.kill(); task.wait(timeout=5)
        for pipe in (task.stdin,task.stdout,task.stderr):
            pipe.close()
        pinned.close()
    print("Pinned stock live-WAL backup imported into fresh guarded archive; exact case scope, persistent contact/link and ordered note/tags/archive, canonical WeChat quote and HTTP redaction verified without native fixture row injection")
    print("Stable exact receipt; wrong/stale/active/redirected input and merge refused; late DB fault rolled back every table; no journal events")


with tempfile.TemporaryDirectory(prefix="hub-guarded-v9-") as d:
    exercise(Path(d))
