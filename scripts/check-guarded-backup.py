#!/usr/bin/env python3
"""Extracted encrypted backup + restore check using an independent Python AES-GCM oracle."""
import hashlib
import http.client
from importlib.metadata import version
import json
from pathlib import Path
import secrets
import select
import sqlite3
import struct
import subprocess
import sys
import tempfile

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from hub.store import ChatStore

BIN=Path(sys.argv[1]).resolve(strict=True)
HEADER=48
MESSAGE="加密备份的 synthetic canonical text"
ROUNDS=600_000


def frame(data):
    return (json.dumps(data,ensure_ascii=False)+"\n").encode()


def run(args,data,binary=BIN):
    return subprocess.run([binary,*args],input=frame(data),capture_output=True,timeout=12)


def ok(result):
    assert result.returncode==0,result.stderr.decode()
    assert not result.stderr
    return json.loads(result.stdout)


def encoded_key(password,header):
    return hashlib.pbkdf2_hmac("sha256",password.encode(),header[20:36],ROUNDS,32)


def decrypt(password,blob):
    h=blob[:HEADER]
    assert h[:12]==b"LMHENC01\x01\x01\x01\x00"
    assert struct.unpack(">II",h[12:20])==(ROUNDS,len(blob)-HEADER)
    return AESGCM(encoded_key(password,h)).decrypt(h[36:48],blob[HEADER:],h)


def seal(password,plaintext):
    header=b"LMHENC01\x01\x01\x01\x00"+struct.pack(">II",ROUNDS,len(plaintext)+16)+secrets.token_bytes(28)
    return header+AESGCM(encoded_key(password,header)).encrypt(header[36:48],plaintext,header)


def request(port,path,token):
    conn=http.client.HTTPConnection("127.0.0.1",port,timeout=4)
    conn.request("GET",path,headers={"Authorization":"Bearer "+token})
    r=conn.getresponse()
    value=(r.status,json.loads(r.read()))
    conn.close()
    return value


def receive(host):
    assert select.select([host.stdout],[],[],6)[0]
    return json.loads(host.stdout.readline())


def seed_from_python(root,archive,password):
    old=ChatStore(root/"legacy.sqlite3")
    old.ingest("wechat","account",[{"id":"room","title":"备份"}],[
        {"id":"m","conversation_id":"room","text":MESSAGE,"timestamp":123,"_hub_wechat_server_id":"123456123456",
         "media_url":"file:///literal-url-should-not-be-backed-up","private_proof":"private-provider"},
        {"id":"ref","conversation_id":"room","text":"canonical quoted","timestamp":125,
         "wechat_quote":{"target_server_id":"123456123456","extra":"discard-this"}},
    ],contacts=[{"id":"c","name":"联系人"}])
    old.update_conversation_metadata("wechat","account","room","保留备注",["第二","第一"],True)
    old.set_contact_identity("wechat","account","c","private-identity")
    source=old.backup_to(root/"detached"/"index.sqlite3")
    old.close()
    flags=["--source",source,"--directory",archive,"--platform","wechat","--account","account"]
    auth={"passphrase":password}
    tag=ok(run(["preview-guarded-v9",*flags],auth))["confirmation"]
    ok(run(["import-guarded-v9",*flags,"--confirm",tag],auth))


def inspect(path):
    with sqlite3.connect(path/"native-v1.sqlite3") as db:
        result={x:db.execute(q).fetchall() for x,q in {
            "generation":"SELECT instance_id FROM native_meta",
            "guard":"SELECT salt,digest FROM native_profile_lock",
            "messages":"SELECT id,text,extra,media_url FROM messages ORDER BY id",
            "contacts":"SELECT id,name FROM contacts",
            "meta":"SELECT note,tags,archived FROM conversation_metadata",
            "identities":"SELECT contact_id,identity_id FROM contact_identities",
            "events":"SELECT event_id FROM native_event_journal",
            "owners":"SELECT producer FROM native_owners",
        }.items()}
    db.close()
    return result


def exercise(root):
    old_password="源 profile "+secrets.token_hex(14)
    archive_password="archive secret "+secrets.token_hex(14)
    new_password="new profile "+secrets.token_hex(14)
    guarded=root/"guarded"
    guarded.mkdir(mode=0o700)
    ok(run(["init-guarded-live","--directory",guarded],{"passphrase":old_password}))
    seed_from_python(root,guarded,old_password)
    original=inspect(guarded)
    original_sqlite=(guarded/"native-v1.sqlite3").read_bytes()

    private=root/"private"
    private.mkdir(mode=0o700)
    first=private/"first.lmhenc"
    second=private/"second.lmhenc"
    create=lambda output:["create-guarded-backup","--directory",guarded,"--output",output,
        "--platform","wechat","--account","account"]
    creds={"profile_passphrase":old_password,"backup_passphrase":archive_password}
    assert run(create(first),{**creds,"profile_passphrase":"synthetic wrong profile password"}).returncode!=0
    assert not first.exists()
    assert ok(run(create(first),creds))["event"]=="backup_created"
    assert ok(run(create(second),creds))["event"]=="backup_created"
    a,b=first.read_bytes(),second.read_bytes()
    assert a[20:36]!=b[20:36] and a[36:48]!=b[36:48] and a[48:]!=b[48:]
    assert first.stat().st_mode&0o077==0 and MESSAGE.encode() not in a
    plain=decrypt(archive_password,a)
    assert plain==decrypt(archive_password,b)
    decoded=json.loads(plain)
    assert decoded["account_id"]=="account" and decoded["platform"]=="wechat"
    assert MESSAGE.encode() in plain and b"literal-url-should-not-be-backed-up" not in plain
    assert b"private-provider" not in plain and b"discard-this" not in plain
    assert run(create(first),creds).returncode!=0 and first.read_bytes()==a

    # Exercise account isolation against a native archive actually containing
    # two accounts with the same raw IDs: 013 supported text route, no direct edits.
    split=root/"split"
    split.mkdir(mode=0o700)
    ok(run(["init-guarded-live","--directory",split],{"passphrase":old_password}))
    for owner in ("account","Account"):
        text=root/(owner+".json")
        text.write_bytes(frame([{"platform":"wechat","account_id":owner,"conversation_id":"rr",
            "id":"same","type":"text","text":"literal-"+owner,"timestamp":1,"is_self":False}]))
        flags=["--source",text,"--directory",split,"--platform","wechat","--account",owner]
        approved=ok(run(["preview-guarded-legacy-json",*flags],{"passphrase":old_password}))["confirmation"]
        ok(run(["import-guarded-legacy-json",*flags,"--confirm",approved],{"passphrase":old_password}))
    scoped=private/"scoped.lmhenc"
    ok(run(["create-guarded-backup","--directory",split,"--output",scoped,
        "--platform","wechat","--account","account"],creds))
    actual=decrypt(archive_password,scoped.read_bytes())
    assert b"literal-account" in actual and b"literal-Account" not in actual
    assert run(create(guarded/"inside-profile.lmhenc"),creds).returncode!=0
    assert not (guarded/"inside-profile.lmhenc").exists()

    restored=private/"restored"
    restore=lambda source,target=restored:["restore-guarded-backup","--source",source,"--directory",target]
    restore_creds={"profile_passphrase":new_password,"backup_passphrase":archive_password}
    assert run(restore(first),{**restore_creds,"backup_passphrase":"synthetic wrong archive password"}).returncode!=0
    for offset in [0,11,15,20,37,len(a)-1]:
        changed=bytearray(a)
        changed[offset]^=1
        invalid=private/"invalid.lmhenc"
        invalid.write_bytes(changed)
        failed=run(restore(invalid),restore_creds)
        assert failed.returncode!=0 and not failed.stdout and not restored.exists()
        invalid.unlink()
    # Independently authenticated but schema-invalid payload is not restored.
    # Valid auth is not a substitute for validation of normalized fields.
    bad=private/"bad-data.lmhenc"
    malformed=json.loads(plain)
    malformed["tables"][2][0][-1]='{"unrecognized_provider_json":"must not preserve"}'
    bad.write_bytes(seal(archive_password,frame(malformed)))
    assert run(restore(bad),restore_creds).returncode!=0
    assert not restored.exists()
    redirect=private/"redirect"
    redirect.symlink_to(root)
    assert run(restore(first,redirect),restore_creds).returncode!=0 and redirect.is_symlink()
    assert run(restore(first,guarded/"nested"),restore_creds).returncode!=0 and not (guarded/"nested").exists()
    assert not any(path.name.startswith(".lmh-") for path in private.iterdir())

    # The first restore input was generated independently in Python.
    independent=private/"independent.lmhenc"
    independent.write_bytes(seal(archive_password,plain))
    outcome=ok(run(restore(independent),restore_creds))
    assert outcome["event"]=="backup_restored" and outcome["rows"]["messages"]==2
    assert restored.stat().st_mode&0o077==0 and (restored/"native-v1.sqlite3").stat().st_mode&0o077==0
    race=private/"race"
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        requests=[pool.submit(run,restore(first,race),restore_creds) for _ in range(2)]
        results=[request.result() for request in requests]
    assert sum(x.returncode==0 for x in results)==1
    assert inspect(race)["messages"]==original["messages"]
    post=inspect(restored)
    for field in ["messages","contacts","meta","identities","events","owners"]:
        assert post[field]==original[field],field
    assert post["generation"]!=original["generation"] and post["guard"]!=original["guard"]
    assert not post["events"] and not post["owners"]
    saved=(restored/"native-v1.sqlite3").read_bytes()
    assert run(restore(first),restore_creds).returncode!=0 and (restored/"native-v1.sqlite3").read_bytes()==saved
    assert (guarded/"native-v1.sqlite3").read_bytes()==original_sqlite and first.read_bytes()==a

    token=secrets.token_hex(32)
    grants=root/"restored-grants.json"
    grants.touch(mode=0o600)
    grants.write_text(json.dumps({"version":1,"grants":[{
        "id":"fixture.restored","kind":"desktop","account_scope":{"platform":"wechat","account_id":"account"},
        "permissions":["legacy.http.read","messages.read","conversations.read","contacts.read"],
        "token_sha256":hashlib.sha256(token.encode()).hexdigest(),
    }]}))
    host=subprocess.Popen([BIN,"serve-guarded-legacy","--directory",restored,"--grants",grants],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
    try:
        ready=receive(host)
        port=int(ready["origin"].removeprefix("http://127.0.0.1:"))
        assert ready["locked"] and request(port,"/api/contacts",token)[0]==423
        # Runtime lease refuses creating backup while it is serving.
        blocked=["create-guarded-backup","--directory",restored,"--output",private/"busy.lmhenc",
                "--platform","wechat","--account","account"]
        assert run(blocked,restore_creds).returncode!=0
        host.stdin.write(frame({"command":"unlock","passphrase":old_password}))
        assert receive(host)["event"]=="unlock_refused"
        host.stdin.write(frame({"command":"unlock","passphrase":new_password}))
        assert receive(host)=={"event":"unlocked"}
        status,body=request(port,"/api/quoted-message?account_id=account&conversation_id=room&reference_message_id=ref",token)
        assert status==200 and body["target_message_id"]=="m" and body["items"][body["target_index"]]["text"]==MESSAGE
        host.stdin.write(frame({"command":"stop"}))
        assert receive(host)=={"event":"stopped"} and host.wait(timeout=5)==0 and not host.stderr.read()
    finally:
        if host.poll() is None:
            host.kill();host.wait(timeout=5)
        for pipe in (host.stdin,host.stdout,host.stderr):
            pipe.close()

    print(f"Independent hashlib/Python cryptography {version('cryptography')} AES-256-GCM opened two Rust sealed snapshots; random salt/nonce differed, identical normalized plaintext; Rust restored independent Python envelope")
    print("Scoped two-account fixture, malformed authenticated payload, wrong passphrase, header/ciphertext tampering, occupied/concurrent/redirected target and running archive refused; unchanged inputs, fresh instance/verifier, restart locked, restored quote reads verified")


with tempfile.TemporaryDirectory(prefix="hub-native-backup-") as d:
    exercise(Path(d))
