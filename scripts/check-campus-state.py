#!/usr/bin/env python3
"""Extracted Rust local Campus bookmarks/digest; compares selected old handlers."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import tempfile
import threading
from types import SimpleNamespace
from urllib.parse import urlencode

from hub.account_safety import AccountSafety
from hub.campus import CampusService
import hub.campus as legacy_campus
from jsonschema import Draft202012Validator

path=Path(__file__).with_name("check-campus-offline.py")
spec=importlib.util.spec_from_file_location("offline",path)
off=importlib.util.module_from_spec(spec)
spec.loader.exec_module(off)
runtime=off.runtime
BIN=runtime.BIN
BOOK="/api/v1/campus/bookmarks"
DIGEST="/api/v1/campus/digest"
DAY="2022-01-02"

def ID(item):
    return hashlib.sha256(item["url"].encode()).hexdigest()

def old(root,articles):
    original=legacy_campus.fetch_source
    def never(*_,**__): raise AssertionError("unexpected legacy fetch")
    legacy_campus.fetch_source=never
    service=CampusService(root/"pinned")
    for row in articles:
        service._store_result({"id":row["source_id"],"name":row["source_name"],"category":row["category"]},
                              {"items":[row]},count_status=False)
    handler,_=off.legacy.stock_handler()
    acct=AccountSafety({},lambda _:None)
    session=acct.setup("synthetic campus bookmarks "+secrets.token_hex(16))
    server=ThreadingHTTPServer(("127.0.0.1",0),handler)
    server.hub=SimpleNamespace(store=object(),account_safety=acct,campus_service=lambda:service,
        capability_enabled=lambda cap:cap=="campus",require_capability=lambda _:None)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    bookmark_id=ID(articles[0])
    headers={"Cookie":"lmh_session="+session}

    def write(value):
        conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=5)
        try:
            conn.request("POST","/api/campus/items/"+bookmark_id+"/state",
                json.dumps({"bookmarked":value}),headers=headers|{"Content-Type":"application/json"})
            response=conn.getresponse()
            result=json.loads(response.read())
            assert response.status==200,result
            return result
        finally:conn.close()

    try:
        assert write(True)["bookmarked"] is True
        status,data=off.legacy.request(server.server_port,"/api/campus/items?bookmarked=1",headers)
        assert status==200 and [v["id"] for v in data["items"]]==[bookmark_id]
        assert write(False)["bookmarked"] is False
        status,data=off.legacy.request(server.server_port,"/api/campus/briefing?"+urlencode(
            {"section":"daily","date":DAY}),headers)
        assert status==200 and data["date"]==DAY and data["pipeline"]["mode"]=="local_rules"
        # Only common material identity/date projection: the old briefing
        # also runs local rule ranking/grouping which Rust does not emulate.
        return {(r["id"],r["title"],r["published_at"]) for e in data["items"] for r in e["reports"]}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        service.close()
        legacy_campus.fetch_source=original

def model(root):
    a=[off.article(2201,DAY,"Synthetic dated outline",summary="Stored notice"),
       off.article(2202,DAY,"合成活动",summary="")|{"category":"activity"},
       off.article(2203,"2022-01-03","Next day"),
       off.article(2204,"","Undated")]
    peer_only=off.article(9999,DAY,"Peer isolated")
    pinned=old(root,a)
    archive=root/"archive"
    archive.mkdir(mode=0o700)
    def cli(*args):
        run=subprocess.run([BIN,*args],capture_output=True,timeout=6)
        assert run.returncode==0,run.stderr.decode()
        return json.loads(run.stdout)
    cli("init-live","--directory",archive)
    source=root/"items.json"
    for profile,rows in [("a",a),("b",[a[0]|{"title":"Peer shares URL"},peer_only])]:
        source.write_text(json.dumps({"version":1,"profile":profile,"articles":rows},ensure_ascii=False))
        args=["--source",source,"--directory",archive,"--profile",profile]
        p=cli("preview-campus-json",*args)
        assert cli("import-campus-json",*args,"--confirm",p["confirmation"])["created"]==len(rows)
    cfg=root/"grants.json"
    cfg.touch(mode=0o600)
    def grant(id,profile,editor):
        token=secrets.token_hex(32)
        permissions=["campus.read"]+["campus.bookmarks.write"]*editor
        return token,{"id":id,"kind":"campus-editor" if editor else "campus-reader","campus_profile":profile,
                      "permissions":permissions,"token_sha256":hashlib.sha256(token.encode()).hexdigest()}
    aedit,g1=grant("profile.a","a",True)
    bread,g2=grant("viewer.b","b",False)
    aread,g3=grant("viewer.a","a",False)
    bedit,g4=grant("editor.b","b",True)
    schema=Draft202012Validator(json.loads((BIN.parent/"plugin-grants.schema.json").read_text()))
    config={"version":1,"grants":[g1,g2,g3,g4]}
    schema.validate(config)
    assert not schema.is_valid({"version":1,"grants":[g2|{"permissions":g1["permissions"]}]})
    assert not schema.is_valid({"version":1,"grants":[g1|{"permissions":g2["permissions"]}]})
    cfg.write_text(json.dumps(config))
    db_path=archive/"native-v1.sqlite3"
    def sql(command):
        with sqlite3.connect(db_path) as db:
            return db.execute(command).fetchall()
    id=ID(a[0])
    path=BOOK+"/"+id
    retained=[]
    def start():
        proc=runtime.start(archive,cfg)
        retained.append(proc)
        return proc,runtime.ready(proc)[0]
    def post(port,token,path,body,code=200,expected=None,headers=None):
        conn,response=runtime.dial(port,"POST",path,token,body,headers)
        try:
            result=json.loads(response.read())
            assert response.status==code,(response.status,result)
            assert response.getheader("Cache-Control")=="no-store"
            if expected: assert result=={"error":{"code":expected}}
            return result
        finally:conn.close()

    missing={"error":{"code":"not_found"}}
    try:
        proc,port=start()
        manifest=runtime.get(port,"/api/v1/manifest",aread)
        assert manifest["campus_offline"]["bookmarks"] is False  # Frozen old object.
        assert manifest["campus_local_state"]=={
            "digest":DIGEST,"bookmarks":BOOK,"can_write_bookmarks":False}
        assert runtime.get(port,"/api/v1/manifest",aedit)["campus_local_state"]["can_write_bookmarks"]
        assert runtime.get(port,path,aread)=={"revision":0,"bookmarked":False}
        assert runtime.get(port,BOOK,aread)=={"items":[],"total":0}
        assert sql("SELECT count(*) FROM sqlite_schema WHERE name LIKE 'native_campus_bookmark%'")==[(0,)]
        update={"expected_revision":0,"bookmarked":True}
        post(port,aread,path,update,403,"permission_required")
        post(port,aedit,path,update|{"profile":"b"},404,"not_found")
        post(port,aedit,BOOK+"/"+ID(peer_only),update,404,"not_found")
        post(port,aedit,path,update,403,"local_native_client_required",headers={"Origin":"https://example.invalid"})
        post(port,aedit,path,update|{"autofetch":True},400,"bad_campus_bookmark")
        post(port,aedit,DIGEST,update,405,"method_not_allowed")
        runtime.get(port,"/api/v1/messages",aedit,status=403)
        assert runtime.get(port,DIGEST+"?date="+DAY+"&profile=b",aread,status=404)==missing

        with ThreadPoolExecutor(max_workers=2) as pool:
            # Competing revision-zero writers are serialized; one updates it.
            def writer(payload):
                conn,resp=runtime.dial(port,"POST",path,aedit,payload)
                try: return resp.status,json.loads(resp.read())
                finally:conn.close()
            futures=[pool.submit(writer,update) for _ in range(2)]
            replies=[f.result(timeout=8) for f in futures]
        assert sorted(status for status,_ in replies)==[200,409]
        assert runtime.get(port,path,aread)=={"revision":1,"bookmarked":True}
        assert runtime.get(port,path,bread)=={"revision":0,"bookmarked":False}
        assert post(port,aedit,path,{"expected_revision":1,"bookmarked":True})=={
            "revision":1,"bookmarked":True}
        assert [x["id"] for x in runtime.get(port,BOOK,aread)["items"]]==[id]
        assert "content" not in runtime.get(port,BOOK,aread)["items"][0]
        assert post(port,bedit,path,update)=={"revision":1,"bookmarked":True}

        sql("CREATE TRIGGER fail_bookmarks BEFORE INSERT ON native_campus_bookmark_audit "
            "BEGIN SELECT RAISE(ABORT,'fixture'); END")
        clear={"expected_revision":1,"bookmarked":False}
        post(port,aedit,path,clear,503,"campus_store_unavailable")
        assert runtime.get(port,path,aread)=={"revision":1,"bookmarked":True}
        sql("DROP TRIGGER fail_bookmarks")
        assert post(port,aedit,path,clear)=={"revision":2,"bookmarked":False}
        assert runtime.get(port,BOOK,aread)=={"items":[],"total":0}
        assert {r[1] for r in sql("PRAGMA table_info(native_campus_bookmark_audit)")}=={
            "profile","id","revision","bookmarked","changed_at"}
        assert sql("SELECT revision,bookmarked FROM native_campus_bookmark_audit WHERE profile='a' ORDER BY revision")==[(1,1),(2,0)]

        result=runtime.get(port,DIGEST+"?date="+DAY,aread)
        assert {(i["id"],i["title"],i["published_at"]) for i in result["items"]}==pinned
        assert result["total"]==2 and [i["id"] for i in result["items"]]==sorted(map(ID,a[:2]))
        assert result["category_counts"]==[{"category":"activity","count":1},{"category":"teaching","count":1}]
        assert all("content" not in v for v in result["items"])
        assert next(v for v in result["items"] if v["id"]==ID(a[1]))["summary"]==""
        assert len(runtime.get(port,DIGEST+"?date="+DAY+"&limit=1",aread)["items"])==1
        assert runtime.get(port,DIGEST+"?date=2022-12-31",aread)["total"]==0
        for q in ("","?date=2022-02-30","?date=0000-01-01","?date="+DAY+"&limit=51"):
            runtime.get(port,DIGEST+q,aread,status=400)
        runtime.finish(proc,port)
        proc,port=start()
        assert runtime.get(port,path,aread)=={"revision":2,"bookmarked":False}
        assert runtime.get(port,path,bread)=={"revision":1,"bookmarked":True}
        assert runtime.get(port,DIGEST+"?date="+DAY,aread)==result
        assert sql("SELECT count(*) FROM native_event_journal")==[(0,)]
        runtime.finish(proc,port)
    finally:
        for p in retained:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5)
            for channel in (p.stdin,p.stdout,p.stderr):
                channel.close()


if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="lmh-campus-state-") as folder:
        model(Path(folder))
    print("extracted Rust offline Campus state: explicit editor, CAS race/rollback, profile/same-ID isolation, redacted audit, exact day/counts/restart; pinned old bookmark and daily article projection")
