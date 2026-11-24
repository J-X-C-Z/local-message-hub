#!/usr/bin/env python3
"""Extracted safe EML path against pinned stock parser, synthetic mail only."""
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import tempfile

from integrations.email import adapter

spec = importlib.util.spec_from_file_location(
    "runtime_smoke", Path(__file__).with_name("smoke-linux-runtime.py"))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
BIN = runtime.BIN
A = "Mailbox@example.invalid"
B = "Peer@example.invalid"
ROOT = "<root@example.invalid>"
FIRST = "<first@example.invalid>"
SECOND = "<second@example.invalid>"
BODY = '合成邮件\r\n链接：https://example.invalid/or/local/path\r\n引号"🦀"'


def mail(identity=FIRST, own=False, body=BODY, encoding="base64"):
    result = EmailMessage(policy=policy.SMTP)
    result["From"] = f"{'Synthetic Owner' if own else 'Synthetic Peer'} <{A if own else B}>"
    result["To"] = A if not own else B
    result["Subject"] = "Synthetic subject"
    result["Date"] = datetime(2025,10,25,4,2,1,tzinfo=timezone(timedelta(hours=-5)))
    result["Message-ID"] = identity
    result["References"] = ROOT+" <middle@example.invalid>"
    result["In-Reply-To"] = "<parent@example.invalid>"
    result["Cc"] = "omitted-cc@example.invalid"
    result["X-Secret-Fixture"] = "removed metadata"
    result.set_content(body,subtype="plain",charset="utf-8",cte=encoding)
    return result


def command(args, ok=True):
    done=subprocess.run([BIN,*args],capture_output=True,timeout=8)
    if not ok:
        assert done.returncode!=0 and not done.stdout,done
        for sensitive in (b"Mailbox@",b"Synthetic Peer",b"omitted-cc",b"removed metadata"):
            assert sensitive not in done.stderr
        return None
    assert done.returncode==0,done.stderr.decode()
    return json.loads(done.stdout) if done.stdout else None


def model(root):
    raw=Path(adapter.__file__).read_bytes()
    assert hashlib.sha1(f"blob {len(raw)}\0".encode()+raw).hexdigest() == "e895854cc7dd0555350d6525098cbb9d14e9643d"
    directory=root/"native"
    directory.mkdir(mode=0o700)
    command(["init-live","--directory",directory])
    db_path=directory/"native-v1.sqlite3"
    path=root/"message.eml"
    def preview(account=A,ok=True,src=path,db=directory):
        return command(["preview-eml","--source",src,"--directory",db,"--account",account],ok)
    def commit(digest,account=A,ok=True):
        return command(["import-eml","--source",path,"--directory",directory,"--account",account,"--confirm",digest],ok)
    def rows(sql):
        db=sqlite3.connect(db_path.as_uri()+"?mode=ro",uri=True)
        try:
            return db.execute(sql).fetchall()
        finally:
            db.close()

    first=mail().as_bytes()
    expected=adapter.parse_email_export(first,"eml",A)
    assert expected["valid"]==1
    path.write_bytes(first)
    db_before=db_path.read_bytes()
    p=preview()
    assert p["rows"]==1 and len(p["confirmation"])==64
    assert db_before==db_path.read_bytes()
    assert preview(B)["confirmation"] != p["confirmation"]
    preview("Foreign@example.invalid",False)
    preview("mailbox@example.invalid",False)
    invalid=root/"too-large.eml"
    invalid.write_bytes(b"x"*(256*1024+1))
    preview(ok=False,src=invalid)
    alias=root/"alias.eml"
    alias.symlink_to(path)
    preview(ok=False,src=alias)
    second_dir=root/"other-archive"
    second_dir.mkdir(mode=0o700)
    command(["init-live","--directory",second_dir])
    assert preview(db=second_dir)["confirmation"] != p["confirmation"]
    commit(p["confirmation"],B,False)
    path.write_bytes(first.replace(b"removed metadata",b"changed ignored header"))
    commit(p["confirmation"],ok=False)
    path.write_bytes(first)
    assert db_before==db_path.read_bytes()

    receipt=commit(p["confirmation"])
    assert receipt["created"]==1 and receipt["unchanged"]==0
    assert receipt==commit(p["confirmation"]) and path.read_bytes()==first
    old=expected["messages"][0]
    got=rows("SELECT id,conversation_id,timestamp,sender_id,sender_name,is_self,text,media_url,file_name,extra FROM messages")
    assert len(got)==1
    assert got[0][:6]==tuple(old[k] for k in ("id","conversation_id","timestamp","sender_id","sender_name","is_self"))
    assert got[0][6]==old["text"].split("\n\n",1)[1]
    assert "omitted-cc" not in got[0][6] and "removed metadata" not in got[0][6]
    assert got[0][7:]==(None,None,None)
    assert rows("SELECT title,kind FROM conversations")==[("Synthetic subject","private")]

    p_b=preview(B)
    assert commit(p_b["confirmation"],B)["created"]==1
    assert rows("SELECT account_id,is_self FROM messages ORDER BY account_id")==[(A,0),(B,1)]
    assert len({r[0] for r in rows("SELECT id FROM messages")})==2

    reply=mail(identity=SECOND,own=True,body="outgoing UTF-8: 回信",encoding="quoted-printable").as_bytes()
    path.write_bytes(reply)
    reply_old=adapter.parse_email_export(reply,"eml",A)["messages"][0]
    assert commit(preview()["confirmation"])["created"]==1
    assert rows("SELECT count(DISTINCT conversation_id) FROM messages WHERE account_id='Mailbox@example.invalid'")==[(1,)]
    assert rows("SELECT is_self,text FROM messages WHERE id='"+reply_old["id"]+"'")==[(1,"outgoing UTF-8: 回信")]
    path.write_bytes(first.replace(b"removed metadata",b"unretained header changed"))
    duplicate=commit(preview()["confirmation"])
    assert duplicate["created"]==0 and duplicate["unchanged"]==1
    path.write_bytes(mail(body="different text for same ID").as_bytes())
    commit(preview()["confirmation"],ok=False)
    assert len(rows("SELECT id FROM messages"))==3

    multipart=mail()
    multipart.add_attachment(b"synthetic",maintype="application",subtype="octet-stream",filename="unopened.bin")
    html=mail()
    html.set_content('<img src="https://example.invalid/remote">',subtype="html")
    for bad in (multipart.as_bytes(),html.as_bytes(),b"From mailbox Tue Nov 24\n"+first,
                first.replace(b"To: Mailbox@example.invalid",b"To: Mailbox@example.invalid, peer@example.invalid"),
                first.replace(b"Message-ID: <first@example.invalid>",b"Message-ID: invalid"),
                first.replace(b"X-Secret-Fixture:",b"From:"),
                first.replace(b"-0500",b"-0000"),
                first.split(b"\r\n\r\n",1)[0]):
        path.write_bytes(bad)
        preview(ok=False)
    assert rows("SELECT count(*) FROM native_event_journal")==[(0,)]

    cfg=root/"grants.json"
    cfg.touch(mode=0o600)
    token=secrets.token_hex(32)
    cfg.write_text(json.dumps({"version":1,"grants":[{
        "id":"email.desktop","kind":"desktop","account_scope":{"platform":"email","account_id":A},
        "permissions":["messages.read","events.read"],"token_sha256":hashlib.sha256(token.encode()).hexdigest()}]}))
    task=runtime.start(directory,cfg)
    try:
        port,_=runtime.ready(task)
        read=runtime.get(port,"/api/v1/messages?conversation_id="+old["conversation_id"],token)
        assert {r["id"] for r in read["items"]}=={old["id"],reply_old["id"]}
        runtime.get(port,"/api/v1/messages?account_id=Peer%40example.invalid",token,status=403)
        assert runtime.get(port,"/api/v1/events/checkpoint",token)["checkpoint"]==0
        runtime.finish(task,port)
    finally:
        if task.poll() is None:
            task.kill()
            task.wait(timeout=5)
        for pipe in (task.stdin,task.stdout,task.stderr):
            pipe.close()


if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="lmh-eml-") as folder:
        model(Path(folder))
    print("extracted Rust EML: exact pinned shared ID/thread/time/sender fields, decoded plain body privacy, explicit From/To owner, isolated grants and IDs, stable retry, attachment/malformed rejection and no journal")
