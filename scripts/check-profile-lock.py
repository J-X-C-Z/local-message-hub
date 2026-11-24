#!/usr/bin/env python3
"""Synthetic Rust guarded runtime and pinned stock verifier interoperability."""
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import select
import sqlite3
import subprocess
import sys
import tempfile

BIN=Path(sys.argv[1]).resolve(strict=True)
from hub.account_safety import AccountSafety


def raw(data):
    return (json.dumps(data, ensure_ascii=False)+"\n").encode()


def frame(process):
    assert select.select([process.stdout],[],[],8)[0]
    line=process.stdout.readline(8193)
    assert line.endswith(b"\n") and len(line)<8193
    return json.loads(line)


def shell(args, payload=None):
    return subprocess.run([BIN,*args],input=payload,capture_output=True,timeout=8)


def access(port,path,token=None):
    conn=http.client.HTTPConnection("127.0.0.1",port,timeout=5)
    headers={"Connection":"close"}
    if token:
        headers["Authorization"]="Bearer "+token
    conn.request("GET",path,headers=headers)
    r=conn.getresponse()
    assert r.getheader("Cache-Control")=="no-store"
    out=(r.status,json.loads(r.read()))
    conn.close()
    return out


def stock_text_export(root, owner):
    from hub.exports import write_export
    from hub.store import ChatStore
    legacy=ChatStore(root/f"legacy-{owner}.sqlite3")
    legacy.ingest("qq",owner,[{"id":"room","title":"Synthetic room"}],[{
        "id":"same-id","conversation_id":"room","type":"text","timestamp":123,"text":"test text "+owner,
    }])
    path=root/f"{owner}-synthetic.json"
    with path.open("wb") as output:
        write_export([legacy.get_message("qq",owner,"same-id")],output,"json")
    legacy.close()
    return path

def args_for(command, source, path, account, confirmation=None):
    args=[command,"--source",source,"--directory",path,"--platform","qq","--account",account]
    if confirmation is not None:
        args.extend(["--confirm",confirmation])
    return args

def guarded_preview(source,path,owner,good):
    op=shell(args_for("preview-guarded-legacy-json",source,path,owner),raw({"passphrase":good}))
    assert op.returncode==0 and not op.stderr
    value=json.loads(op.stdout)
    assert set(value)=={"rows","confirmation"}
    return value

def guarded_commit(source,path,owner,good,confirmation):
    return shell(args_for("import-guarded-legacy-json",source,path,owner,confirmation),raw({"passphrase":good}))


def run(root):
    path=root/"isolated"
    path.mkdir(mode=0o700)
    ordinary=root/"ordinary"
    ordinary.mkdir(mode=0o700)
    good="合成口令🦀"+secrets.token_hex(20)
    wrong="错误口令🦀"+secrets.token_hex(20)
    token, unscoped=secrets.token_hex(32),secrets.token_hex(32)
    grants=root/"grants.json"
    grants.touch(mode=0o600)
    digest=lambda value:hashlib.sha256(value.encode()).hexdigest()
    grants.write_text(json.dumps({"version":1,"grants":[
        {"id":"guarded.alpha","kind":"desktop","account_scope":{"platform":"qq","account_id":"alpha"},
         "permissions":["messages.read","conversations.read"],"token_sha256":digest(token)},
        {"id":"guarded.ordinary","kind":"desktop","permissions":["messages.read"],"token_sha256":digest(unscoped)},
    ]}))
    assert shell(["init-live","--directory",ordinary]).returncode==0
    malformed=shell(["init-guarded-live","--directory",path],raw({"passphrase":"short"}))
    assert malformed.returncode!=0 and list(path.iterdir())==[]
    nonpipe=root/"not-a-secret-file"
    nonpipe.write_text("{}\n")
    with nonpipe.open("rb") as input_file:
        refusal=subprocess.run([BIN,"init-guarded-live","--directory",path],stdin=input_file,capture_output=True,timeout=5)
    assert refusal.returncode!=0 and b"piped stdin" in refusal.stderr
    created=shell(["init-guarded-live","--directory",path],raw({"passphrase":good}))
    assert created.returncode==0 and not created.stderr
    assert json.loads(created.stdout)["locked"]
    db_path=path/"native-v1.sqlite3"
    assert db_path.stat().st_mode&0o077==0
    alpha=stock_text_export(root,"alpha")
    other=stock_text_export(root,"other")
    original_alpha=alpha.read_bytes()
    with sqlite3.connect(db_path) as db:
        salt,key,iterations=db.execute("SELECT salt,digest,iterations FROM native_profile_lock").fetchone()
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0]==0
    confirm=guarded_preview(alpha,path,"alpha",good)
    assert confirm["rows"]==1
    assert shell(args_for("preview-guarded-legacy-json",alpha,ordinary,"alpha"),raw({"passphrase":good})).returncode!=0
    for command in ("preview-legacy-json","import-legacy-json"):
        bad=args_for(command,alpha,path,"alpha",confirm["confirmation"] if command.startswith("import") else None)
        assert shell(bad).returncode!=0
    script=args_for("import-guarded-legacy-json",alpha,path,"alpha",confirm["confirmation"])
    for payload in (b"",b"{",raw({"passphrase":wrong}),raw({"unknown":good}),raw({"passphrase":good})+b"{}\n",
                    b"x"*6400,raw({"passphrase":good}).rstrip(b"\n")):
        denied=shell(script,payload)
        assert denied.returncode!=0 and not denied.stdout
        assert good.encode() not in denied.stderr and wrong.encode() not in denied.stderr
    wrong_account=guarded_commit(alpha,path,"other",good,confirm["confirmation"])
    assert wrong_account.returncode!=0 and not wrong_account.stdout
    alpha.write_bytes(original_alpha+b" ")
    assert guarded_commit(alpha,path,"alpha",good,confirm["confirmation"]).returncode!=0
    alpha.write_bytes(original_alpha)

    with db_path.open("rb") as active:
        fcntl.flock(active,fcntl.LOCK_EX|fcntl.LOCK_NB)
        busy=guarded_commit(alpha,path,"alpha",good,confirm["confirmation"])
        assert busy.returncode!=0 and b"busy" in busy.stderr
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT count(*) FROM messages").fetchone()[0]==0

    committed=guarded_commit(alpha,path,"alpha",good,confirm["confirmation"])
    assert committed.returncode==0 and json.loads(committed.stdout)=={
        "batch_id":confirm["confirmation"],"rows":1,"created":1,"unchanged":0}
    assert good.encode() not in committed.stdout and str(path).encode() not in committed.stdout
    assert guarded_commit(alpha,path,"alpha",good,confirm["confirmation"]).stdout==committed.stdout
    assert alpha.read_bytes()==original_alpha
    next_confirm=guarded_preview(other,path,"other",good)
    assert guarded_commit(other,path,"other",good,next_confirm["confirmation"]).returncode==0
    records=json.loads(original_alpha)
    records=[{**records[0],"id":"fresh-row"}, {**records[0],"text":"must not replace"}]
    alpha.write_bytes(json.dumps(records).encode())
    bad_confirm=guarded_preview(alpha,path,"alpha",good)
    assert guarded_commit(alpha,path,"alpha",good,bad_confirm["confirmation"]).returncode!=0
    alpha.write_bytes(original_alpha)
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT account_id,id,text FROM messages ORDER BY account_id").fetchall()==[
            ("alpha","same-id","test text alpha"),("other","same-id","test text other")]
        assert db.execute("SELECT count(*) FROM native_event_journal").fetchone()[0]==0
    print("Real guarded CLI imported stock text exports; exclusive lock, malformed/wrong/incomplete and ordinary bypass refused; append rollback and stable receipt")
    settings={"account_lock":{"salt":salt.hex(),"digest":key.hex(),"iterations":iterations}}
    stock=AccountSafety(settings,lambda _:None)
    assert stock.verify(good) and not stock.verify(wrong)
    assert (len(salt),len(key),iterations)==(16,32,310000)
    print("Pinned Python AccountSafety verified exact Rust-derived Unicode verifier")

    # Test both directions, through controlled fixture replacement only. The
    # product has no verifier import. Message rows came from the guarded CLI.
    stock.set_passphrase(good)
    cfg=settings["account_lock"]
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE native_profile_lock SET salt=?,digest=?,iterations=?",
            (bytes.fromhex(cfg["salt"]),bytes.fromhex(cfg["digest"]),cfg["iterations"]))
    assert shell(["serve","--live",path,"--grants",grants]).returncode!=0
    renamed=root/"index.sqlite3"
    with sqlite3.connect(db_path) as source, sqlite3.connect(renamed) as copy:
        source.backup(copy)
    rejected=shell(["serve","--snapshot",renamed,"--grants",grants])
    assert rejected.returncode!=0 and not rejected.stdout
    (root/"index.sqlite3").unlink()

    def start(directory):
        return subprocess.Popen([BIN,"serve-guarded","--directory",directory,"--grants",grants],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
    def ready(p):
        value=frame(p)
        assert value["event"]=="ready" and value["locked"] and value["mode"]=="guarded-read-only"
        assert value["origin"].startswith("http://127.0.0.1:")
        return int(value["origin"].split(":")[-1])
    def send(p,command,**fields):
        p.stdin.write(raw({"command":command,**fields}))
        return frame(p)

    workers=[]
    try:
        invalid=start(ordinary);workers.append(invalid)
        assert invalid.wait(timeout=5)!=0 and not invalid.stdout.read()
        p=start(path);workers.append(p)
        port=ready(p)
        assert not any(x.encode() in Path(f"/proc/{p.pid}/cmdline").read_bytes() for x in (good,wrong,token,unscoped))
        assert access(port,"/healthz")== (200,{"status":"ok"})
        concurrent=guarded_commit(alpha,path,"alpha",good,confirm["confirmation"])
        assert concurrent.returncode!=0 and b"busy" in concurrent.stderr
        second=start(path);workers.append(second)
        assert second.wait(timeout=5)!=0 and b"busy" in second.stderr.read() and not second.stdout.read()
        message="/api/v1/messages?platform=qq&account_id=alpha&conversation_id=room"
        locked=(423,{"error":{"code":"account_locked"}})
        assert access(port,message,token)==access(port,message)==locked
        assert send(p,"unlock",passphrase=good)=={"event":"unlocked"}
        code,data=access(port,message,token)
        assert code==200 and data["items"][0]["text"]=="test text alpha" and "other" not in str(data)
        assert access(port,message,unscoped)[0]==401
        assert access(port,message.replace("alpha","other"),token)[0]==403
        assert access(port,"/api/v1/events/stream",token)[0]==404
        assert send(p,"lock")=={"event":"locked"}
        assert access(port,message,token)==locked
        for _ in range(5):
            assert send(p,"unlock",passphrase=wrong)=={"event":"unlock_refused","reason":"invalid_or_unavailable"}
        assert send(p,"unlock",passphrase=good)=={"event":"unlock_refused","reason":"rate_limited"}
        assert access(port,message,token)==locked
        assert send(p,"stop")=={"event":"stopped"}
        assert p.wait(timeout=5)==0 and not p.stderr.read()
        print("Guarded listener: stock verifier accepted; exact desktop scope, 423 before/after lock; five failures rate limit; no argv secrets or stream")

        q=start(path);workers.append(q)
        port=ready(q)
        assert access(port,message,token)==locked
        assert send(q,"unlock",passphrase=good)=={"event":"unlocked"}
        with sqlite3.connect(db_path) as db:
            db.execute("DELETE FROM native_profile_lock")
        assert access(port,message,token)==locked
        q.stdin.close()
        assert frame(q)=={"event":"stopped"} and q.wait(timeout=5)==0
        malformed=start(path);workers.append(malformed)
        assert malformed.wait(timeout=5)!=0 and not malformed.stdout.read()
        assert shell(["serve","--live",path,"--grants",grants]).returncode!=0
        print("Restart closed, deleted verifier fails in-flight request/new startup, ordinary/snapshot modes refuse guarded archives")
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill();worker.wait(timeout=5)
            for pipe in (worker.stdin,worker.stdout,worker.stderr):
                pipe.close()


with tempfile.TemporaryDirectory(prefix="hub-guarded-test-") as tmp:
    run(Path(tmp))
