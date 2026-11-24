#!/usr/bin/env python3
"""Extracted native local draft API; all text/account/tokens synthetic."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import tempfile

from jsonschema import Draft202012Validator


def module(name,file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file))
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


runtime=module("runtime","smoke-linux-runtime.py")
src=module("source","check-scoped-producer.py")
BIN=runtime.BIN
PATH="/api/v1/drafts"
QUERY=PATH+"?conversation_id=room"
BODY="First private synthetic draft 🐧\n<plain text only>"


def check(root):
    native=root/"native"
    native.mkdir(mode=0o700)
    subprocess.run([BIN,"init-live","--directory",native],capture_output=True,check=True,timeout=8)
    cfg=root/"grants.json"
    cfg.touch(mode=0o600)
    a,b,read,limited,producer,new_a=[secrets.token_hex(32) for _ in range(6)]
    conv=["conversations.read"]
    rw=conv+["drafts.read","drafts.write"]
    grant=lambda id,scope,perms,token:src.grant(id,"desktop",("qq",scope),perms,token)
    ga=grant("test.a","one",rw,a)
    gb=grant("test.b","two",rw,b)
    gr=grant("test.reader","one",conv+["drafts.read"],read)
    gn=grant("test.limited","one",conv,limited)
    gp=src.grant("test.producer","producer",None,["messages.ingest"],producer)
    schema=json.loads((BIN.parent/"plugin-grants.schema.json").read_text())
    validator=Draft202012Validator(schema)

    def configure(*grants):
        doc={"version":1,"grants":list(grants)}
        validator.validate(doc)
        cfg.write_text(json.dumps(doc))
    configure(ga,gb,gr,gn,gp)
    assert not validator.is_valid({"version":1,"grants":[ga|{"permissions":conv+["drafts.write"]}]})
    assert not validator.is_valid({"version":1,"grants":[ga|{"permissions":["drafts.read"]}]})

    dbpath=native/"native-v1.sqlite3"
    def sql(q,values=()):
        db=sqlite3.connect(dbpath)
        try:
            result=db.execute(q,values).fetchall()
            db.commit()
            return result
        finally:
            db.close()
    tasks=[]
    def start():
        task=runtime.start(native,cfg)
        tasks.append(task)
        return task,runtime.ready(task)[0]
    def change(expected,key,body,**target):
        return dict(expected_revision=expected,request_id=key,body=body,
                    target=dict(conversation_id="room",**target))
    def call(port,token,doc,status=200,code=None,path=PATH,headers=None):
        conn,response=runtime.dial(port,"POST",path,token,doc,headers)
        try:
            data=json.loads(response.read())
            assert response.status==status,(response.status,data)
            assert response.getheader("Cache-Control")=="no-store"
            if code:
                assert data=={"error":{"code":code}}
            return data
        finally:
            conn.close()

    guarded=root/"guarded"
    guarded.mkdir(mode=0o700)
    passphrase="synthetic guarded draft fixture "+secrets.token_hex(16)
    raw=lambda doc:(json.dumps(doc)+"\n").encode()
    proc=subprocess.run([BIN,"init-guarded-live","--directory",guarded],
        input=raw({"passphrase":passphrase}),capture_output=True,timeout=8)
    assert proc.returncode==0 and not proc.stderr
    guard_task=subprocess.Popen([BIN,"serve-guarded","--directory",guarded,"--grants",cfg],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
    tasks.append(guard_task)
    try:
        port_guard=int(runtime.jsonline(guard_task)["origin"].split(":")[-1])
        assert runtime.get(port_guard,QUERY,a,status=423)=={"error":{"code":"account_locked"}}
        call(port_guard,a,change(0,"disabled","draft"),405,"method_not_allowed")
        guard_task.stdin.write(raw({"command":"unlock","passphrase":passphrase}))
        assert runtime.jsonline(guard_task)=={"event":"unlocked"}
        runtime.get(port_guard,QUERY,a,status=404)
        call(port_guard,a,change(0,"disabled","draft"),405,"method_not_allowed")
        guard_task.stdin.close()
        assert guard_task.wait(timeout=5)==0
        proc,port=start()
        for acct in ("one","two"):
            src.post(port,producer,src.request("seed-"+acct,account=acct,id="same-id")|{
                "messages":[dict(platform="qq",account_id=acct,conversation_id="room",
                                 message_id="same-id",timestamp=1724123456,is_self=False,text="indexed identical ID")]
            })
        get=lambda token=a,query=QUERY,status=200:runtime.get(port,query,token,status=status)
        assert get()=={"revision":0,"body":None}
        assert sql("SELECT count(*) FROM sqlite_schema WHERE name LIKE 'native_draft%'")==[(0,)]
        assert get(read)=={"revision":0,"body":None}
        assert get(limited,status=403)=={"error":{"code":"permission_required"}}
        assert get(producer,status=403)=={"error":{"code":"permission_required"}}
        assert get(query=QUERY+"&account_id=two",status=404)==get(query=PATH+"?conversation_id=missing",status=404)
        call(port,read,change(0,"blocked",BODY),403,"permission_required")
        call(port,a,change(0,"wrong",BODY,account_id="two"),404,"not_found")
        call(port,a,change(0,"origin",BODY),403,"local_native_client_required",headers={"Origin":"https://example.invalid"})
        call(port,a,change(0,"no-send",BODY),405,"method_not_allowed",path=PATH+"/send")
        manifest=runtime.get(port,"/api/v1/manifest",a)
        assert manifest["local_drafts"]=={"url":PATH,"can_write":True}
        assert runtime.get(port,"/api/v1/manifest",read)["local_drafts"]["can_write"] is False
        assert "local_drafts" not in runtime.get(port,"/api/v1/manifest",limited)
        contract=runtime.get(port,"/api/v1/openapi.json",a)
        subschema=contract["paths"]["/api/v1/manifest"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        Draft202012Validator(subschema).validate(manifest)

        first=change(0,"initial",BODY)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(call,port,a,first) for _ in range(2)]
            accepted,repeat=[f.result(timeout=8) for f in futures]
        assert accepted==repeat=={"revision":1,"present":True}
        assert get(read)=={"revision":1,"body":BODY}
        assert get(b)=={"revision":0,"body":None}
        assert call(port,b,change(0,"initial","different account"))=={"revision":1,"present":True}
        call(port,a,change(0,"initial","different payload"),409,"draft_request_conflict")
        call(port,a,change(0,"stale","ignored"),409,"draft_revision_conflict")
        assert call(port,a,change(1,"no-op",BODY))==accepted
        assert sql("SELECT operation,revision FROM native_draft_audit WHERE account_id='one'")==[("create",1)]

        sql("CREATE TRIGGER fail_draft BEFORE INSERT ON native_draft_audit BEGIN SELECT RAISE(ABORT,'fixture'); END")
        call(port,a,change(1,"edit","replacement"),503,"draft_store_unavailable")
        assert get()=={"revision":1,"body":BODY}
        sql("DROP TRIGGER fail_draft")
        assert call(port,a,change(1,"edit","replacement"))=={"revision":2,"present":True}
        assert call(port,a,change(2,"clear",None))=={"revision":3,"present":False}
        assert get()=={"revision":3,"body":None}
        assert call(port,a,first)==accepted
        assert sql("SELECT operation,revision FROM native_draft_audit WHERE account_id='one' ORDER BY revision")==[
            ("create",1),("edit",2),("clear",3)]
        audit_cols={v[1] for v in sql("PRAGMA table_info(native_draft_audit)")}
        assert audit_cols=={"platform","account_id","conversation_id","revision","operation","changed_at"}
        assert sql("SELECT count(*) FROM native_event_journal")==[(2,)]
        runtime.finish(proc,port)

        configure(grant("test.a","one",rw,new_a),gb,gr,gn,gp)
        proc,port=start()
        runtime.get(port,QUERY,a,status=401)
        assert runtime.get(port,QUERY,new_a)=={"revision":3,"body":None}
        assert call(port,new_a,first)==accepted
        assert runtime.get(port,QUERY,b)=={"revision":1,"body":"different account"}
        call(port,new_a,change(0,"tombstone","must not resurrect"),409,"draft_revision_conflict")
        runtime.finish(proc,port)
    finally:
        for proc in tasks:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            for pipe in (proc.stdin,proc.stdout,proc.stderr):
                pipe.close()


if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="lmh-draft-") as path:
        check(Path(path))
    print("extracted Rust local drafts: guarded disabled, scoped read/write, idempotent concurrent receipt and no-op, conflict/rollback, redacted audit, tombstones, token rotation and restart; no delivery")
