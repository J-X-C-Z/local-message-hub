#!/usr/bin/env python3
"""Private conversation activity: paired raw legacy HTTP and extracted native runtime."""
from datetime import datetime,timedelta,timezone
from http.server import ThreadingHTTPServer
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
import secrets
import select
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace

from hub.account_safety import AccountSafety
from hub.analytics import AnalyticsService
from hub.store import ChatStore

BIN=Path(sys.argv[1]).resolve(strict=True)
BASE=int(datetime(2026,1,1,tzinfo=timezone(timedelta(hours=8))).timestamp())
OLD="/api/reports/pair?platform=qq&account_id=account&conversation_id=direct&year=2026"
NATIVE=OLD.replace("/api/reports/pair","/api/v1/analytics/pair")
PRIVATE_FIELDS={"conversation_title","counterpart_id","counterpart_name","counterpart_messages","message_types"}

def msg(id,conv,seconds,own,sender="peer"):
    return {"id":id,"conversation_id":conv,"timestamp":BASE+seconds,"type":"text","is_self":own,
        "sender_id":sender if not own else "local-person","sender_name":"hidden sender name",
        "text":"hidden raw body"}
def raw(v):
    return (json.dumps(v,ensure_ascii=False)+"\n").encode()
def cli(args,v):
    p=subprocess.run([BIN,*args],input=raw(v),capture_output=True,timeout=7)
    assert p.returncode==0,p.stderr.decode()
    return json.loads(p.stdout)
def request(port,path,headers=None):
    conn=http.client.HTTPConnection("127.0.0.1",port,timeout=4)
    conn.request("GET",path,headers=headers or {})
    response=conn.getresponse()
    value=response.status,json.loads(response.read())
    conn.close()
    return value
def receive(p):
    assert select.select([p.stdout],[],[],6)[0]
    return json.loads(p.stdout.readline())
def core(record):
    return {k:v for k,v in json.loads(json.dumps(record)).items() if k not in PRIVATE_FIELDS}

def stock_http(store):
    src=Path(__file__).resolve().with_name("check-legacy-http.py")
    spec=importlib.util.spec_from_file_location("stock_fixture_pair",src)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handler,_=module.stock_handler()
    acct=AccountSafety({},lambda _:None)
    secret=acct.setup("synthetic pair stock "+secrets.token_hex(12))
    server=ThreadingHTTPServer(("127.0.0.1",0),handler)
    server.hub=SimpleNamespace(store=store,account_safety=acct,analytics=AnalyticsService(store),
        capability_enabled=lambda cap:cap=="annual_reports",require_capability=lambda cap:None)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        code,payload=request(server.server_port,OLD,{"Cookie":"lmh_session="+secret})
        assert code==200 and payload["counterpart_id"]=="peer"
        return core(payload)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

def exercise(root):
    legacy=ChatStore(root/"legacy.sqlite3")
    chats=[{"id":"direct","title":"hidden conversation title","kind":"private"},
           {"id":"group","title":"hidden group title","kind":"group"},
           {"id":"mixed","title":"hidden mixed title","kind":"direct"},
           {"id":"rounding","title":"hidden rounding title","kind":"friend"}]
    rows=[
        msg("before","direct",-1,False),msg("edge","direct",0,True),
        msg("r1","direct",3,False),msg("s1","direct",4,True),
        msg("r2","direct",5,False),msg("s2","direct",5,True),
        msg("r3","direct",10,False),msg("s3","direct",3610,True),
        msg("r4","direct",3611,False),msg("s4","direct",7212,True),
        msg("grp","group",10,False),
        msg("mix-a","mixed",3,False,sender="one"),
        msg("mix-b","mixed",4,True),
        msg("mix-c","mixed",6,False,sender="two"),
        msg("mix-d","mixed",8,True),
        msg("mix-extra","mixed",10,False,sender="one"),
        # Lexicographic ID order is intentionally opposite the source row order
        # for the tied first reply.
        msg("z-in-first","rounding",10,False),msg("a-self-second","rounding",10,True),
        msg("round-r2","rounding",20,False),msg("round-s2","rounding",21,True),
    ]
    legacy.ingest("qq","account",chats,rows)
    legacy.ingest("qq","ACCOUNT",chats,[msg("r1","direct",2,False),msg("s1","direct",3,True)])
    oracle=AnalyticsService(legacy)
    expected=core(oracle.pair_report("qq","account","direct",2026).to_dict())
    multipeer=core(oracle.pair_report("qq","account","mixed",2026).to_dict())
    half_even=core(oracle.pair_report("qq","account","rounding",2026).to_dict())
    assert (half_even["response_count"],half_even["response_fastest_seconds"],half_even["response_avg_seconds"])==(2,0,0)
    assert expected==stock_http(legacy)
    assert expected["response_count"]==3 and expected["response_fastest_seconds"]==0
    assert expected["response_avg_seconds"]==1200 and expected["total_messages"]==9
    assert multipeer["response_count"]==1
    index=legacy.backup_to(root/"detached"/"index.sqlite3")
    legacy.close()

    grants=root/"grants.json"
    grants.touch(mode=0o600)
    pair,old,annual=[secrets.token_hex(32) for _ in range(3)]
    digest=lambda t:hashlib.sha256(t.encode()).hexdigest()
    scope={"platform":"qq","account_id":"account"}
    grants.write_text(json.dumps({"version":1,"grants":[
        {"id":"fixture.pair","kind":"desktop","account_scope":scope,"permissions":["analytics.pair.read"],"token_sha256":digest(pair)},
        {"id":"fixture.legacy","kind":"desktop","account_scope":scope,
         "permissions":["legacy.http.read","messages.read","conversations.read","analytics.pair.read"],"token_sha256":digest(old)},
        {"id":"fixture.annual","kind":"desktop","account_scope":scope,
         "permissions":["legacy.http.read","messages.read","conversations.read","analytics.annual.read"],"token_sha256":digest(annual)},
    ]}))
    target=root/"guarded"
    target.mkdir(mode=0o700)
    password="synthetic pair guarded "+secrets.token_hex(15)
    cli(["init-guarded-live","--directory",target],{"passphrase":password})
    args=["--source",index,"--directory",target,"--platform","qq","--account","account"]
    confirmation=cli(["preview-guarded-v9",*args],{"passphrase":password})["confirmation"]
    cli(["import-guarded-v9",*args,"--confirm",confirmation],{"passphrase":password})

    for mode in ("snapshot","legacy"):
        parameters=["desktop-supervise","--snapshot",index] if mode=="snapshot" else ["serve-guarded-legacy","--directory",target]
        native=subprocess.Popen([BIN,*parameters,"--grants",grants],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        try:
            ready=receive(native)
            port=int(ready["origin"].removeprefix("http://127.0.0.1:"))
            path=NATIVE if mode=="snapshot" else OLD
            good={"Authorization":"Bearer "+(pair if mode=="snapshot" else old)}
            restricted={"Authorization":"Bearer "+annual}
            get=lambda resource:path.replace("conversation_id=direct","conversation_id="+resource)
            if mode=="legacy":
                assert request(port,path,good)[0]==423
                native.stdin.write(raw({"command":"unlock","passphrase":password}))
                assert receive(native)["event"]=="unlocked"
                settings=request(port,"/api/settings",good)[1]
                assert settings["pair_activity_enabled"] and not settings["annual_activity_enabled"]
            status,actual=request(port,path,good)
            assert status==200 and actual.pop("response_basis")=="adjacent_incoming_to_self"
            assert actual==expected
            assert not PRIVATE_FIELDS.intersection(actual) and "hidden" not in str(actual)
            status,rounded=request(port,get("rounding"),good)
            assert status==200
            rounded.pop("response_basis")
            assert rounded==half_even
            alternative=request(port,get("mixed"),good)[1]
            assert alternative["total_messages"]==multipeer["total_messages"] and alternative["response_count"]==2
            assert request(port,path,restricted)[0]==403
            assert request(port,path.replace("account_id=account","account_id=ACCOUNT"),good)[0]==403
            assert request(port,get("missing").replace("account_id=account","account_id=bogus"),good)[0]==403
            assert request(port,get("missing"),good)[0]==404
            assert request(port,get("group"),good)[0]==400
            assert request(port,path+"&counterpart_id=peer",good)[0]==400
            assert request(port,path.replace("&year=2026",""),good)[0]==400
            native.stdin.write(raw({"command":"stop"}) if mode=="legacy" else b"stop\n")
            assert receive(native)["event"]=="stopped" and native.wait(timeout=5)==0 and not native.stderr.read()
        finally:
            if native.poll() is None:
                native.kill()
                native.wait(timeout=5)
            for output in (native.stdin,native.stdout,native.stderr):
                output.close()

    print("Pinned PairReport and real stock Handler matched shared fields for a single peer; role-only mixed sender behavior, UTC+08 cutoff, ties/half-even/0s/3600s/3601s, privacy redaction and prelookup account rejection verified in extracted snapshot and guarded modes")


with tempfile.TemporaryDirectory(prefix="hub-pair-report-") as path:
    exercise(Path(path))
