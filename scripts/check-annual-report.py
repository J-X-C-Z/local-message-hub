#!/usr/bin/env python3
"""Paired compatible annual aggregates and native revision behavior, synthetic only."""
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
OFFSET=timezone(timedelta(hours=8))
YEAR=2026
LOCAL=lambda month,day,h=0,m=0,s=0:int(datetime(YEAR,month,day,h,m,s,tzinfo=OFFSET).timestamp())
OLD="/api/reports/annual?year=2026&platform=qq&account_id=scope"
NATIVE=OLD.replace("/api/reports/annual","/api/v1/analytics/annual")
LOWER=LOCAL(1,1)
UPPER=int(datetime(2027,1,1,tzinfo=OFFSET).timestamp())
SYN_ROWS=[
    ("before","a",LOWER-1,True),
    ("edge","a",LOWER,True),
    ("j1h1","b",LOCAL(1,1,1),False),
    ("j2h0","a",LOCAL(1,2,0,10),False),
    ("j2h1","a",LOCAL(1,2,1),True),
    ("dec","b",LOCAL(12,30,22),False),
    ("last","b",UPPER-1,True),
    ("after","a",UPPER,False),
]


def fixture(owner,row):
    id,conversation,ts,own=row
    return {"id":id,"conversation_id":conversation,"type":"text","timestamp":ts,"is_self":own,
        "text":"hidden payload "+owner,"sender_name":"hidden sender "+owner,"sender_id":"opaque hidden id"}

def core(value):
    clone=json.loads(json.dumps(value))
    clone.pop("message_types",None)
    clone.pop("top_contacts",None)
    return clone

def raw(value):
    return (json.dumps(value,ensure_ascii=False)+"\n").encode()

def run(args, payload):
    out=subprocess.run([BIN,*args],input=raw(payload),capture_output=True,timeout=10)
    assert out.returncode==0,out.stderr.decode()
    return json.loads(out.stdout)

def request(port,path,token=None,method="GET",body=None,headers=None):
    conn=http.client.HTTPConnection("127.0.0.1",port,timeout=5)
    fields={**(headers or {})}
    if token:
        fields["Authorization"]="Bearer "+token
    if body:
        fields["Content-Type"]="application/json"
    conn.request(method,path,headers=fields,body=json.dumps(body) if body else None)
    r=conn.getresponse()
    result=(r.status,json.loads(r.read()))
    conn.close()
    return result

def stock_http(root,store,expected):
    fixture_file=Path(__file__).resolve().with_name("check-legacy-http.py")
    spec=importlib.util.spec_from_file_location("stock_fixture",fixture_file)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handler,_=module.stock_handler()
    settings={}
    safety=AccountSafety(settings,lambda _:None)
    legacy_auth=safety.setup("isolated fixture "+secrets.token_hex(14))
    http=ThreadingHTTPServer(("127.0.0.1",0),handler)
    http.hub=SimpleNamespace(store=store,account_safety=safety,analytics=AnalyticsService(store),
        capability_enabled=lambda cap:cap=="annual_reports",require_capability=lambda cap:None)
    thread=threading.Thread(target=http.serve_forever,daemon=True)
    thread.start()
    try:
        code,payload=request(http.server_port,OLD,headers={"Cookie":"lmh_session="+legacy_auth})
        assert code==200 and core(payload)==expected
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=3)

def start_guard(guarded,grants,mode="serve-guarded-legacy"):
    p=subprocess.Popen([BIN,mode,"--directory",guarded,"--grants",grants],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
    return p

def receive(p):
    assert select.select([p.stdout],[],[],5)[0]
    return json.loads(p.stdout.readline())

def stop(p,guarded):
    p.stdin.write(raw({"command":"stop"}) if guarded else b"stop\n")
    assert receive(p)["event"]=="stopped" and p.wait(timeout=5)==0 and not p.stderr.read()

def cleanup(p):
    if p.poll() is None:
        p.kill();p.wait(timeout=5)
    for pipe in (p.stdin,p.stdout,p.stderr):
        pipe.close()

def exercise(root):
    source=ChatStore(root/"legacy.sqlite3")
    for owner in ("scope","SCOPE"):
        source.ingest("qq",owner,[{"id":"a"},{"id":"b"}],[fixture(owner,row) for row in SYN_ROWS])
    service=AnalyticsService(source)
    expected=core(service.annual_report(YEAR,"qq","scope").to_dict())
    subsection=core(service.annual_report(YEAR,"qq","scope","a").to_dict())
    assert expected["total_messages"]==6 and expected["sent_messages"]==3
    assert expected["busiest_day"]=={"date":"2026-01-01","count":2}
    assert expected["busiest_hour"]=={"hour":0,"count":2}
    assert expected["longest_streak"]=={"days":2,"start_date":"2026-01-01","end_date":"2026-01-02"}
    assert expected["first_timestamp"]==LOWER and expected["last_timestamp"]==UPPER-1
    stock_http(root,source,expected)
    detached=source.backup_to(root/"detached"/"index.sqlite3")

    grants=root/"grants.json"
    grants.touch(mode=0o600)
    summary,legacy,restricted,producer=[secrets.token_hex(32) for _ in range(4)]
    digest=lambda token:hashlib.sha256(token.encode()).hexdigest()
    scope={"platform":"qq","account_id":"scope"}
    old_grants=["messages.read","conversations.read","legacy.http.read"]
    grants.write_text(json.dumps({"version":1,"grants":[
        {"id":"fixture.summary","kind":"desktop","account_scope":scope,
         "permissions":["analytics.annual.read"],"token_sha256":digest(summary)},
        {"id":"fixture.legacy","kind":"desktop","account_scope":scope,
         "permissions":old_grants+["analytics.annual.read"],"token_sha256":digest(legacy)},
        {"id":"fixture.restricted","kind":"desktop","account_scope":scope,
         "permissions":old_grants,"token_sha256":digest(restricted)},
        {"id":"fixture.producer","kind":"producer","permissions":["messages.ingest"],"token_sha256":digest(producer)},
    ]}))
    guarded=root/"guarded"
    guarded.mkdir(mode=0o700)
    passphrase="synthetic protected report "+secrets.token_hex(14)
    auth={"passphrase":passphrase}
    run(["init-guarded-live","--directory",guarded],auth)
    flags=["--source",detached,"--directory",guarded,"--platform","qq","--account","scope"]
    confirm=run(["preview-guarded-v9",*flags],auth)["confirmation"]
    run(["import-guarded-v9",*flags,"--confirm",confirm],auth)
    for guarded_mode in ["serve-guarded-legacy","serve-guarded"]:
        p=start_guard(guarded,grants,guarded_mode)
        try:
            ready=receive(p)
            port=int(ready["origin"].removeprefix("http://127.0.0.1:"))
            path=OLD if guarded_mode=="serve-guarded-legacy" else NATIVE
            assert request(port,path,legacy)[0]==423
            p.stdin.write(raw({"command":"unlock","passphrase":passphrase}))
            assert receive(p)=={"event":"unlocked"}
            assert request(port,path,legacy if path==OLD else summary)==(200,expected)
            assert request(port,path+"&conversation_id=a",legacy if path==OLD else summary)==(200,subsection)
            assert request(port,path.replace("account_id=scope","account_id=SCOPE"),legacy)[0]==403
            assert request(port,path.replace("2026","1900"),legacy)[0]==400
            assert request(port,path+"&token=not-accepted",legacy)[0]==400
            if path==OLD:
                assert request(port,path,restricted)[0]==403
                assert request(port,"/api/settings",legacy)[1]["capabilities"]["annual_reports"]["enabled"]
                assert not request(port,"/api/settings",restricted)[1]["capabilities"]["annual_reports"]["enabled"]
                assert request(port,"/api/reports/pair",legacy)[0]==403
            stop(p,True)
        finally:
            cleanup(p)

    snapshot=subprocess.Popen([BIN,"desktop-supervise","--snapshot",detached,"--grants",grants],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
    try:
        ready=receive(snapshot)
        assert ready["mode"]=="snapshot-read-only"
        port=int(ready["origin"].removeprefix("http://127.0.0.1:"))
        assert request(port,NATIVE,summary)==(200,expected)  # Foreign rows still exist in detached input.
        stop(snapshot,False)
    finally:
        cleanup(snapshot)

    native=root/"native"
    native.mkdir(mode=0o700)
    run(["init-live","--directory",native],{})
    p=subprocess.Popen([BIN,"desktop-supervise","--live",native,"--grants",grants],
          stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
    try:
        port=int(receive(p)["origin"].removeprefix("http://127.0.0.1:"))
        def ingest(batch, owner, rows):
            values=[{k:v for k,v in fixture(owner,row).items() if k not in ("id","type")}
                | {"platform":"qq","account_id":owner,"message_id":row[0]} for row in rows]
            result=request(port,"/api/v1/ingest",producer,"POST",{"idempotency_key":batch,"messages":values})
            assert result[0]==200
            return result[1]
        ingest("first","scope",SYN_ROWS)
        ingest("foreign","SCOPE",SYN_ROWS)
        assert request(port,NATIVE,summary)==(200,expected)
        assert request(port,NATIVE,restricted)[0]==403
        empty=request(port,NATIVE.replace("2026","2024"),summary)
        assert empty[0]==200 and empty[1]["total_messages"]==0 and empty[1]["busiest_day"] is None
        changed=("edge","a",LOWER-1,True)
        assert ingest("revision","scope",[changed])["revised"]==1
        source.ingest("qq","scope",[],[fixture("scope",changed)])
        assert request(port,NATIVE,summary)==(200,core(service.annual_report(YEAR,"qq","scope").to_dict()))
        assert request(port,NATIVE,summary)[1]["total_messages"]==5
        stop(p,False)
    finally:
        cleanup(p)
        source.close()
    print("Pinned stock AnalyticsService + actual legacy Handler HTTP match safe UTC+08 annual projections; scope, boundaries, tie choices, optional conversation, guarded 423/capability, and native revision validated")


with tempfile.TemporaryDirectory(prefix="hub-annual-report-") as d:
    exercise(Path(d))
