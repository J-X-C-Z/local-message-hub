#!/usr/bin/env python3
"""Synthetic old→new→old switch; never replaces, modifies, or migrates a live profile."""
import argparse
from contextlib import closing, contextmanager
from hashlib import sha1, sha256
from http.server import ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace

# Integrity and scenario acceptance assertions are part of this verifier.
if not __debug__:
    raise SystemExit("portable rehearsal requires Python assertions (no -O or PYTHONOPTIMIZE)")
sys.dont_write_bytecode = True

import hub
from hub.store import ChatStore

def plain_path(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path.resolve(strict=False) != path:
        raise ValueError("use a canonical absolute local path")
    return path


def hash_file(path, bound=256*1024*1024):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or not 0 < info.st_size <= bound):
        raise ValueError("expected a bounded regular single-link file")
    with path.open("rb") as source:
        current = os.fstat(source.fileno())
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("file changed before verification")
        digest = sha256()
        count = 0
        while chunk := source.read(65536):
            count += len(chunk)
            if count > bound:
                raise ValueError("file exceeded verification limit")
            digest.update(chunk)
        if count != info.st_size:
            raise ValueError("file changed during verification")
        return digest.hexdigest()


def private_dir(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("expected a local owner-only directory")


def fresh_target(path):
    private_dir(path.parent)
    # mkdir, not exists + rename/replacement: can only create an empty private path.
    path.mkdir(mode=0o700, exist_ok=False)
    private_dir(path)


def verified_binary(options):
    verifier, package = map(plain_path, (options.verifier, options.package))
    private_dir(package)
    bin_dir=package/"bin"
    inner=bin_dir.lstat()
    if not stat.S_ISDIR(inner.st_mode) or inner.st_uid != os.geteuid() or inner.st_mode & 0o022:
        raise ValueError("expected owner-controlled package binary directory")
    if hash_file(verifier) != options.verifier_sha256:
        raise ValueError("trusted verifier digest mismatch; no package code executed")
    command = [verifier,"verify-portable-core",package,options.manifest_sha256]
    checked = subprocess.run(command,capture_output=True,timeout=30,check=True)
    verdict = json.loads(checked.stdout)
    if verdict != {"target":options.target, "source_revision":options.source_revision,
                   "manifest_sha256":options.manifest_sha256, "binary_sha256":options.binary_sha256,
                   "files":2}:
        raise ValueError("portable verification result did not match operator's independent pins")
    binary = package / "bin/local-message-hub-rs"
    if hash_file(binary) != options.binary_sha256:
        raise ValueError("packaged binary digest changed")
    return binary


def config():
    if sys.platform != "linux":
        raise ValueError("guarded Linux rehearsal only")
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("verifier","verifier-sha256","package","manifest-sha256","binary-sha256",
                 "source-revision","target","scratch-parent"):
        parser.add_argument("--"+name,required=True)
    options=parser.parse_args()
    for field, length in (("verifier_sha256",64),("manifest_sha256",64),("binary_sha256",64),
                          ("source_revision",40)):
        value=getattr(options,field)
        if len(value)!=length or any(c not in "0123456789abcdef" for c in value):
            parser.error("invalid lowercase pin: "+field)
    if options.target not in ("linux-x64","linux-arm64"):
        parser.error("requires the matching native Linux target")
    return options


def stock_fixture(binary):
    # The reused stock oracle accepts its binary as its single CLI argument.
    checkout=Path(__file__).resolve().parents[1]
    probe=importlib.util.spec_from_file_location("stock_fixture",checkout/"scripts/check-legacy-http.py")
    fixture=importlib.util.module_from_spec(probe)
    args=sys.argv
    try:
        sys.argv=[str(checkout/"scripts/check-legacy-http.py"),str(binary)]
        probe.loader.exec_module(fixture)
    finally:
        sys.argv=args
    return fixture

USER = "/api/messages?platform=wechat&account_id=alpha&conversation_id=room"
CONVS = "/api/conversations?platform=wechat&account_id=alpha"
SEARCH = "/api/search?platform=wechat&account_id=alpha&conversation_id=room&q=old"
CONTEXT = "/api/context?platform=wechat&account_id=alpha&conversation_id=room&message_id=m2&before=1&after=1"
OTHER = USER.replace("account_id=alpha","account_id=Alpha")
EXPECTED = {"conversations":1, "messages":2, "contacts":1,
            "conversation_metadata":1, "contact_identities":1}
BLOBS = {"store.py":"1065ba686034b9cc052652328d6a668a6b42bf86",
         "server.py":"9dff68b768542740706429cd1f920d7639577c8d",
         "account_safety.py":"21b27d8d12ed3f4dae92f3523c19b7331012496a",
         "capabilities.py":"cd02eda4025c3c6c6721dee76fbb02bd86cf3ee7"}


def pinned():
    for name,expected in BLOBS.items():
        data=(Path(hub.__file__).parent/name).read_bytes()
        assert sha1(f"blob {len(data)}\0".encode()+data).hexdigest()==expected, name


def tree_hash(directory):
    return {str(path.relative_to(directory)):sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def seed(store, account):
    store.ingest("wechat",account,[{"id":"room","title":"合成测试 "+account}],
        [{"id":rid,"conversation_id":"room","text":body+" "+account,"timestamp":stamp,
          "type":"text","sender_id":"same-user","sender_name":"合成联系人","is_self":sent,
          "media_url":"file:///discard-synthetic-only-media","file_name":"fixture.txt"}
         for rid,body,stamp,sent in [("m1","old one",10,True),("m2","old two",11,False)]],
        contacts=[{"id":"same-user","name":"合成联系人 "+account}],
        checkpoint={"conversation_id":"room","next_offset":9,"complete":False})
    store.update_conversation_metadata("wechat",account,"room",note="备注 "+account,
                                       tags=["第一","第二"],archived=True)
    store.set_contact_identity("wechat",account,"same-user","contact-"+account)


@contextmanager
def stock(store,directory):
    handler, source_info = fixture.stock_handler()
    settings={"auto_sync":False,"mcp_write_enabled":False,"auto_download_attachments":False}
    lock=fixture.AccountSafety(settings,lambda _:None)
    cookie=lock.setup(secrets.token_hex(24))
    inert=SimpleNamespace(store=store,settings=settings,storage=directory,lock=threading.RLock(),
                          account_safety=lock,capability_enabled=lambda key:key in {"chat","contacts","search"})
    inert.source_info=lambda:source_info(inert)
    with ThreadingHTTPServer(("127.0.0.1",0),handler) as server:
        server.hub=inert
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            yield server.server_port,{"Cookie":"lmh_session="+cookie}
        finally:
            server.shutdown()
            thread.join(timeout=3)
            assert not thread.is_alive()


def projected(result):
    status,page=result
    assert status==200, result
    return page["total"],[
        tuple(row[field] for field in fixture.CORE_MSG) for row in page["items"]]


def refuse(command, data):
    declined=subprocess.run([BIN,*command],input=fixture.raw(data),capture_output=True,timeout=15)
    assert declined.returncode != 0 and not declined.stdout


def view(port,headers,*,stock_projection=False):
    def items(path,fields,allow_wider_stock=False):
        result=fixture.request(port,path,headers)
        code,page=result
        assert code==200, (path,code)
        rows=page["items"]
        if stock_projection and allow_wider_stock:
            # Original conversations/search handlers may return a wider
            # account set. Compare the selected-account projection only.
            rows=[row for row in rows if row["account_id"]=="alpha"]
        assert rows and all(row["account_id"]=="alpha" for row in rows)
        return [[tuple(row[field] for field in fields) for row in rows],page]
    context_rows,context=items(CONTEXT,fixture.CORE_MSG)
    assert context_rows[context["target_index"]][0]=="m2"
    return {"list":items(CONVS,fixture.CORE_CONV,True)[0],
            "history":projected(fixture.request(port,USER,headers)),
            "search":items(SEARCH,fixture.CORE_MSG,True)[0],
            "context":(context_rows,context["target_index"])}


def detached_counts(file):
    # Inputs to this helper are generated by this script and already closed.
    with closing(sqlite3.connect(file.as_uri()+"?mode=ro&immutable=1",uri=True)) as db:
        return {table:db.execute("SELECT count(*) FROM "+table).fetchone()[0]
                for table in (*EXPECTED,"sync_checkpoints")}


def exercise(root):
    old=root/"old-active"
    old.mkdir(mode=0o700)
    old_db=old/"hub.sqlite3"
    (old/"separate-config-fixture.txt").write_bytes(b"synthetic untouched config")
    store=ChatStore(old_db)
    try:
        seed(store,"alpha")
        seed(store,"Alpha")
        with stock(store,old) as (port,headers):
            baseline=view(port,headers,stock_projection=True)
            first=baseline["history"]
            foreign=projected(fixture.request(port,OTHER,headers))
            assert first[0]==foreign[0]==2 and first!=foreign
        assert (old/"hub.sqlite3-wal").stat().st_size>0
        frozen=store.backup_to(root/"detached"/"index.sqlite3")
        active_fixture=root/"active-rejection"
        fresh_target(active_fixture)
        password=secrets.token_hex(24)
        pass_frame={"passphrase":password}
        fixture.cli(["init-guarded-live","--directory",active_fixture],pass_frame)
        active_before=tree_hash(old)
        refuse(["preview-guarded-v9","--source",old_db,"--directory",active_fixture,
                "--platform","wechat","--account","alpha"],pass_frame)
        assert tree_hash(old)==active_before
    finally:
        store.close()
    old_before=tree_hash(old)
    frozen_before=tree_hash(frozen.parent)

    new=root/"guarded-new"
    nonprivate=root/"nonprivate"
    nonprivate.mkdir(mode=0o755)
    try:
        fresh_target(nonprivate/"rejected")
    except ValueError:
        pass
    else:
        raise AssertionError("nonprivate target parent accepted")
    assert not (nonprivate/"rejected").exists()
    fresh_target(new)
    try:
        fresh_target(new)
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing cutover target accepted")
    fixture.cli(["init-guarded-live","--directory",new],pass_frame)
    args=["--source",frozen,"--directory",new,"--platform","wechat","--account","alpha"]
    sentinel=frozen.with_name(frozen.name+"-wal")
    sentinel.touch(exist_ok=False)
    try:
        refuse(["preview-guarded-v9",*args],pass_frame)
    finally:
        sentinel.unlink()
    preview=fixture.cli(["preview-guarded-v9",*args],pass_frame)
    inventory=detached_counts(frozen)
    losses={name:inventory[name]-preview["rows"].get(name,0) for name in inventory}
    assert losses["messages"]==2 and losses["sync_checkpoints"]==2
    print(json.dumps({"phase":"review_fixture_projection","preserved_exact_scope":preview["rows"],
        "omitted_other_account_rows":{name:losses[name] for name in EXPECTED},
        "omitted_state_rows":{"sync_checkpoints":losses["sync_checkpoints"]},
        "unmigrated_fields":["media paths","file names","provider state","credentials","write queues"]},
        sort_keys=True),flush=True)
    assert preview["rows"]==EXPECTED
    receipt=fixture.cli(["import-guarded-v9",*args,"--confirm",preview["confirmation"]],pass_frame)
    assert receipt=={"batch_id":preview["confirmation"],"rows":EXPECTED}

    token=secrets.token_hex(32)
    grants=root/"new-grants.json"
    grants.touch(mode=0o600)
    grants.write_text(json.dumps({"version":1,"grants":[{"id":"test.migrate-desktop",
        "kind":"desktop","account_scope":{"platform":"wechat","account_id":"alpha"},
        "permissions":["legacy.http.read","legacy.search.read","messages.read","conversations.read"],
        "token_sha256":sha256(token.encode()).hexdigest()}]}))
    backend=subprocess.Popen([BIN,"serve-guarded-legacy","--directory",new,"--grants",grants],
                             stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             bufsize=0)
    try:
        ready=fixture.proc_json(backend)
        port=int(ready["origin"].removeprefix("http://127.0.0.1:"))
        assert ready["locked"] and ready["mode"]=="guarded-legacy-read-only"
        token_header={"Authorization":"Bearer "+token}
        assert fixture.request(port,USER,token_header)[0]==423
        backend.stdin.write(fixture.raw({"command":"unlock","passphrase":password}))
        assert fixture.proc_json(backend)=={"event":"unlocked"}
        actual=view(port,token_header)
        assert actual==baseline, {key:(actual[key],baseline[key]) for key in actual if actual[key]!=baseline[key]}
        assert "discard-synthetic-only-media" not in str(fixture.request(port,USER,token_header)[1])
        rejected=fixture.request(port,OTHER,token_header)
        assert rejected[0]==403 and "old one" not in str(rejected[1]), rejected
        backend.stdin.write(fixture.raw({"command":"stop"}))
        assert fixture.proc_json(backend)=={"event":"stopped"}
        assert backend.wait(timeout=5)==0 and not backend.stderr.read()
    finally:
        if backend.poll() is None:
            backend.kill()
            backend.wait(timeout=5)
        for pipe in (backend.stdin,backend.stdout,backend.stderr):
            pipe.close()

    # The fallback is to reopen the untouched stock database, not a conversion
    # of Rust state and not an in-place undo. Verify bytes before fallback startup.
    assert tree_hash(old)==old_before and tree_hash(frozen.parent)==frozen_before
    new_before=tree_hash(new)
    reopened=ChatStore(old_db)
    try:
        with stock(reopened,old) as (port,headers):
            assert view(port,headers,stock_projection=True)==baseline
            assert projected(fixture.request(port,OTHER,headers))==foreign
            reopened.ingest("wechat","alpha",[{"id":"room","title":"合成测试 alpha"}],
                [{"id":"post-fallback","conversation_id":"room","text":"after fallback only",
                  "timestamp":30,"type":"text","sender_id":"same-user","is_self":True}])
            total,rows=projected(fixture.request(port,USER,headers))
            assert total==3 and rows[0][0]=="post-fallback"
            assert projected(fixture.request(port,OTHER,headers))==foreign
    finally:
        reopened.close()
    assert tree_hash(new)==new_before and tree_hash(frozen.parent)==frozen_before
    assert (old/"separate-config-fixture.txt").read_bytes()==b"synthetic untouched config"


if __name__ == "__main__":
    options=config()
    BIN=verified_binary(options)
    fixture=stock_fixture(BIN)
    pinned()
    parent=plain_path(options.scratch_parent)
    private_dir(parent)
    with tempfile.TemporaryDirectory(prefix="lh-v9-old-new-old-",dir=parent) as tmp:
        exercise(Path(tmp))
    print("PASS verified exact portable package; pinned Python → fresh scoped Linux guarded reader → old Python fallback")
