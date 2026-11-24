#!/usr/bin/env python3
"""Synthetic detached Campus index + exact stock legacy read Handler comparison."""
import hashlib
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

from jsonschema import Draft202012Validator
from hub.account_safety import AccountSafety
import hub.campus as old_campus
from hub.campus import CampusService

def module(name,file):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(file))
    built=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(built)
    return built

runtime=module("runtime","smoke-linux-runtime.py")
legacy=module("legacy","check-legacy-http.py")
BIN=runtime.BIN
PATH="/api/v1/campus/items"
FIELD=("id","url","title","published_at","summary","source_id","source_name","category")

def article(key,day="2026-11-17",title="Synthetic campus",summary="合成摘要"):
    return {"url":f"https://www.sysu.edu.cn/info/9999/{key}.htm","title":title,
            "published_at":day,"summary":summary,"content":"Synthetic campus OFFICE HOURS %_",
            "source_id":"synthetic-source","source_name":"Synthetic catalog","category":"teaching"}

def old_http(root,published,filters,selected):
    # No old start_updates/refresh path is enabled or invoked. Fail loudly
    # if the local store/Handler unexpectedly tries its collector seam.
    saved=old_campus.fetch_source
    def never(*_,**__): raise AssertionError("unexpected collector")
    old_campus.fetch_source=never
    service=CampusService(root/"pinned-old")
    first=published[0]
    service._store_result({"id":first["source_id"],"name":first["source_name"],"category":first["category"]},
        {"items":published},count_status=False)
    handler,_=legacy.stock_handler()
    account=AccountSafety({},lambda _:None)
    session=account.setup("synthetic offline campus scope "+secrets.token_hex(16))
    server=ThreadingHTTPServer(("127.0.0.1",0),handler)
    server.hub=SimpleNamespace(store=object(),account_safety=account,campus_service=lambda:service,
        capability_enabled=lambda capability:capability=="campus",require_capability=lambda _:None)
    tasks=threading.Thread(target=server.serve_forever,daemon=True)
    tasks.start()
    try:
        headers={"Cookie":"lmh_session="+session}
        cases={}
        for params in filters:
            q=urlencode(params)
            status,data=legacy.request(server.server_port,"/api/campus/items?"+q,headers)
            assert status==200 and "profile" in data and all(
                row["scoring"]=={"status":"disabled"} for row in data["items"])
            cases[q]={"items":[{k:row[k] for k in FIELD} for row in data["items"]],"total":data["total"]}
        status,details=legacy.request(server.server_port,"/api/campus/items/"+selected,headers)
        assert status==200
        return cases,{k:details[k] for k in (*FIELD,"content")}
    finally:
        server.shutdown()
        server.server_close()
        tasks.join(timeout=4)
        service.close()
        old_campus.fetch_source=saved

def model(root):
    def command(args,success=True):
        proc=subprocess.run([BIN,*args],capture_output=True,timeout=8)
        if not success:
            assert proc.returncode!=0 and not proc.stdout,proc
            assert b"discarded-private-profile" not in proc.stderr
            return None
        assert proc.returncode==0,proc.stderr.decode()
        return json.loads(proc.stdout) if proc.stdout else None

    native=root/"native"
    native.mkdir(mode=0o700)
    command(["init-live","--directory",native])
    dbpath=native/"native-v1.sqlite3"
    csv=root/"detached.json"
    schema=json.loads((BIN.parent/"campus-detached.schema.json").read_text())
    form=Draft202012Validator(schema)

    def write(profile,articles):
        doc={"version":1,"profile":profile,"articles":articles}
        form.validate(doc)
        csv.write_text(json.dumps(doc,ensure_ascii=False))
    def preview(profile="a",ok=True):
        return command(["preview-campus-json","--source",csv,"--directory",native,"--profile",profile],ok)
    def commit(digest,profile="a",ok=True):
        return command(["import-campus-json","--source",csv,"--directory",native,
                        "--profile",profile,"--confirm",digest],ok)
    def sql(query):
        db=sqlite3.connect(dbpath.as_uri()+"?mode=ro",uri=True)
        try:
            return db.execute(query).fetchall()
        finally:
            db.close()

    articles=[
        article(100,"2026-11-17","合成甲"),
        article(101,"2026-11-17","合成乙"),
        article(102,"","没有日期",""),
    ]
    title=articles[0]; selector=hashlib.sha256(title["url"].encode()).hexdigest()
    fixtures=[{},{"q":"office Hours"},{"q":"%_","limit":1,"offset":1},{"q":"合成乙"},
        {"category":"teaching","source_id":"synthetic-source"},{"category":"student"}]
    pinned,pin_detail=old_http(root,articles,fixtures,selector)

    write("a",articles)
    first=csv.read_bytes()
    old_bytes=dbpath.read_bytes()
    check=preview()
    assert check["rows"]==3 and before_untouched(dbpath,old_bytes)
    preview("b",False)
    csv.write_bytes(first+b"\n")
    commit(check["confirmation"],ok=False)
    # Same canonical identity with a fragment is rejected within a single batch.
    write("a",[title,title|{"url":title["url"]+"#different"}])
    preview(ok=False)
    for url in ("http://sysu.edu.cn/info/1","https://sysu.edu.cn.fake.invalid/1",
                "https://discarded-private-profile@sysu.edu.cn/info/1",
                "https://sysu.edu.cn/info/1?secret=discarded-private-profile"):
        write("a",[title|{"url":url}])
        preview(ok=False)
    csv.write_bytes(first)
    receipt=commit(check["confirmation"])
    assert (receipt["created"],receipt["unchanged"])==(3,0)
    assert commit(check["confirmation"])==receipt and csv.read_bytes()==first
    write("a",[article(404),title|{"title":"conflicting immutable metadata"}])
    commit(preview()["confirmation"],ok=False)
    assert sql("SELECT count(*) FROM native_campus_articles")==[(3,)]
    foreign=article(999,title="synthetic foreign-only")
    write("b",[title|{"title":"Private to separate local profile"},foreign])
    assert commit(preview("b")["confirmation"],"b")["created"]==2

    a,b,none,chat=[secrets.token_hex(32) for _ in range(4)]
    cfg=root/"grants.json"
    cfg.touch(mode=0o600)
    def grant(id,token,profile,permissions=None):
        return {"id":id,"kind":"campus-reader","campus_profile":profile,"permissions":permissions or ["campus.read"],
                "token_sha256":hashlib.sha256(token.encode()).hexdigest()}
    grants=[grant("campus.a",a,"a"),grant("campus.b",b,"b"),
            {"id":"chat.desktop","kind":"desktop","permissions":["messages.read"],
             "token_sha256":hashlib.sha256(chat.encode()).hexdigest()}]
    grant_schema=Draft202012Validator(json.loads((BIN.parent/"plugin-grants.schema.json").read_text()))
    grant_schema.validate({"version":1,"grants":grants})
    assert not grant_schema.is_valid({"version":1,"grants":[grant("missed",none,"c",["campus.read","messages.read"])]})
    assert not grant_schema.is_valid({"version":1,"grants":[grant("missed",none,None)]})
    cfg.write_text(json.dumps({"version":1,"grants":grants}))
    for repeat in range(2):
        proc=runtime.start(native,cfg)
        try:
            port,_=runtime.ready(proc)
            detail=runtime.get(port,PATH+"/"+selector,a)
            assert detail==pin_detail
            assert runtime.get(port,PATH+"/"+selector,b)["title"]=="Private to separate local profile"
            foreignid=hashlib.sha256(foreign["url"].encode()).hexdigest()
            missing={"error":{"code":"not_found"}}
            assert runtime.get(port,PATH+"/"+foreignid,a,status=404)==missing
            assert runtime.get(port,PATH+"?profile=b",a,status=404)==missing
            assert runtime.get(port,PATH+"/"+selector+"?profile=b",a,status=404)==missing
            for q,expected in pinned.items():
                assert runtime.get(port,PATH+"?"+q,a)==expected
            runtime.get(port,PATH,chat,status=403)
            runtime.get(port,"/api/v1/messages",a,status=403)
            runtime.get(port,"/api/v1/status",a,status=403)
            runtime.get(port,"/api/v1/campus/sources",a,status=403)
            runtime.get(port,PATH+"?limit=51",a,status=400)
            manifest=runtime.get(port,"/api/v1/manifest",a)
            assert manifest["campus_offline"]=={
               "items":PATH,"offline":True,"bookmarks":False,"fetch":False,"models":False,"autosync":False}
            assert all(v is None for v in manifest["resources"].values())
            assert "campus_offline" not in runtime.get(port,"/api/v1/manifest",chat)
            contract=runtime.get(port,"/api/v1/openapi.json",a)
            Draft202012Validator(contract["paths"]["/api/v1/manifest"]["get"]["responses"]["200"]
                ["content"]["application/json"]["schema"]).validate(manifest)
            runtime.finish(proc,port)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            for p in (proc.stdin,proc.stdout,proc.stderr):
                p.close()
    assert sql("SELECT count(*) FROM native_event_journal")==[(0,)]


def before_untouched(db,old):
    return db.read_bytes()==old


if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="lmh-campus-detached-") as directory:
        model(Path(directory))
    print("extracted Rust offline Campus: pinned Handler/store common list/detail/search fields, URL/profile scoping, no collector, explicit capability flags, immutable rollback and restarted readback")
