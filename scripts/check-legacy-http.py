#!/usr/bin/env python3
"""Run the pinned stock HTTP handler and new guarded wire adapter on synthetic data."""
from __future__ import annotations
import ast
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import http.client
import json
from pathlib import Path
import re
import secrets
import select
import sqlite3
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from hub.account_safety import AccountSafety
from hub.capabilities import describe_capabilities
from hub.exports import write_export
from hub.store import ChatStore, PLATFORMS
import hub

BIN=Path(sys.argv[1]).resolve(strict=True)
SCOPE="/api/messages?platform=qq&account_id=alpha&conversation_id=room"
CORE_MSG=("id","platform","account_id","conversation_id","sender_id","sender_name","type","timestamp","is_self","text")
SEARCH="/api/search?platform=qq&q=Current"
CONTACTS="/api/contacts?platform=qq&account_id=alpha"
CONTEXT="/api/context?platform=qq&account_id=alpha&conversation_id=room&message_id=same-id&before=1&after=1"
WECHAT_CONTEXT="/api/context?platform=wechat&account_id=alpha&conversation_id=wroom&message_id=tgt&before=1&after=1"
QUOTE="/api/quoted-message?platform=wechat&account_id=alpha&conversation_id=wroom&reference_message_id=ref&before=1&after=1"
CORE_CONTACT=("id","platform","account_id","name","kind")
CORE_CONV=("id","platform","account_id","title","kind","message_count","last_message","last_timestamp","note","tags","archived")

def stock_handler():
    path=next(Path(p)/"server.py" for p in hub.__path__ if (Path(p)/"server.py").is_file())
    module=ast.parse(path.read_text())
    handler=next(node for node in module.body if isinstance(node,ast.ClassDef) and node.name=="Handler")
    source=next(node for node in module.body if isinstance(node,ast.ClassDef) and node.name=="Hub")
    source_fn=next(node for node in source.body if isinstance(node,ast.FunctionDef) and node.name=="source_info")
    namespace={
        "BaseHTTPRequestHandler":BaseHTTPRequestHandler,"SimpleCookie":SimpleCookie,
        "parse_qs":parse_qs,"urlsplit":urlsplit,"PLATFORMS":PLATFORMS,"re":re,"json":json,
        "describe_capabilities":describe_capabilities,
        "windows_autostart":SimpleNamespace(status=lambda _root:{"synthetic":"inert"}),
        "ROOT":Path("."),"QQProviderError":type("DisabledSyntheticProvider",(Exception,),{}),
    }
    exec(compile(ast.Module(body=[ast.parse("from __future__ import annotations").body[0],
        handler,source_fn],type_ignores=[]),str(path),"exec"),namespace)
    return namespace["Handler"],namespace["source_info"]


def populate(store, account):
    store.ingest("qq",account,[{"id":"room","title":"中文 Synthetic room"}],[
        {"id":id,"conversation_id":"room","type":"text","text":text+account,
         "timestamp":stamp,"sender_id":"u","sender_name":"同学","is_self":self_flag,
         "media_url":"file:///discard-synthetic-provider-reference"}
        for id,stamp,text,self_flag in [("same-id",31,"Current %literal_ ",False),("older",12,"Earlier ",True)]
    ])


def populate_wechat(store,account):
    store.ingest("wechat",account,[{"id":"wroom","title":"quote fixture"},{"id":"separate","title":"foreign conversation"}],[
        {"id":"tgt","conversation_id":"wroom","type":"text","text":"equal canonical target "+account,"timestamp":70},
        {"id":"ref","conversation_id":"wroom","type":"text","text":"equal canonical reference "+account,"timestamp":70},
        {"id":"other-room","conversation_id":"separate","type":"text","text":"different conversation "+account,"timestamp":70},
    ])

def canonical_quote(db,account):
    db.execute("UPDATE messages SET extra=? WHERE platform='wechat' AND account_id=? AND id IN ('tgt','other-room')",
        (json.dumps({"_hub_wechat_server_id":"712345678901"}),account))
    db.execute("UPDATE messages SET extra=? WHERE platform='wechat' AND account_id=? AND id='ref'",
        (json.dumps({"wechat_quote":{"target_server_id":"712345678901","raw":"fixture-private-quote"}}),account))

def seed_contact(db,account):
    db.execute("""INSERT INTO contacts(platform,account_id,id,name,avatar_url,kind,extra)
        VALUES('qq',?,'same-contact',?,'file:///fixture-contact-avatar','friend','{"alias":"hidden-alias-fixture"}')""",
        (account,"Shared Contact "+account))

def request(port,path,headers=None,method="GET"):
    conn=http.client.HTTPConnection("127.0.0.1",port,timeout=4)
    conn.request(method,path,headers=headers or {})
    response=conn.getresponse()
    assert response.getheader("Cache-Control")=="no-store"
    assert response.getheader("X-Content-Type-Options")=="nosniff"
    result = response.status,json.loads(response.read())
    conn.close()
    return result

def raw(obj):
    return (json.dumps(obj,ensure_ascii=False)+"\n").encode()

def cli(args,data):
    checked=subprocess.run([BIN,*args],input=raw(data),capture_output=True,timeout=6,check=True)
    assert not checked.stderr
    return json.loads(checked.stdout)

def proc_json(proc):
    assert select.select([proc.stdout],[],[],6)[0]
    return json.loads(proc.stdout.readline())


def exercise(root):
    stock=ChatStore(root/"old.sqlite3")
    populate(stock,"alpha")
    populate_wechat(stock,"alpha")
    with sqlite3.connect(root/"old.sqlite3") as db:
        seed_contact(db,"alpha")
        canonical_quote(db,"alpha")
    legacy_handler,legacy_sources=stock_handler()
    password="synthetic guarded fixture "+secrets.token_hex(16)
    settings={"auto_sync":False,"mcp_write_enabled":False,"auto_download_attachments":False,
        "private_fixture":"do not expose this settings field",
        "capabilities":{"media":False}}
    lock=AccountSafety(settings,lambda _:None)
    old_cookie=lock.setup(password)
    fake=SimpleNamespace(store=stock,settings=settings,storage=root,lock=threading.RLock(),
        sources={name:{"id":name,"state":"disconnected","version":"","last_sync":None,"error":"","progress":""}
                 for name in ("qq","wechat")},auto_exports=SimpleNamespace(status=lambda:{"enabled":False}),
        account_safety=lock,capability_enabled=lambda x:x in {"chat","search","contacts"})
    fake.source_info=lambda:legacy_sources(fake)
    old=ThreadingHTTPServer(("127.0.0.1",0),legacy_handler)
    old.hub=fake
    thread=threading.Thread(target=old.serve_forever,daemon=True)
    thread.start()
    tasks=[]
    try:
        old_headers={"Cookie":"lmh_session="+old_cookie}
        assert request(old.server_port,"/api/settings")==(423,{"error":"应用已锁定","code":"account_locked"})
        # Run the supported stock HTTP paths using the authenticated cookie,
        # but only an inert synthetic store/runtime wiring and no providers.
        old_result={}
        for url in ("/api/sources","/api/settings","/api/conversations?platform=qq",SCOPE,SEARCH,CONTACTS,CONTEXT,WECHAT_CONTEXT,QUOTE):
            status,payload=request(old.server_port,url,old_headers)
            assert status==200,(url,status)
            old_result[url]=payload

        newer=ChatStore(root/"newer-source.sqlite3")
        profile=root/"native"
        profile.mkdir(mode=0o700)
        cli(["init-guarded-live","--directory",profile],{"passphrase":password})
        for account in ("alpha","other"):
            populate(newer,account)
            populate_wechat(newer,account)
            for platform in ("qq","wechat"):
                source=root/f"stock-{platform}-{account}.json"
                with source.open("wb") as output:
                    write_export([row for row in newer.export_page(platform)["items"] if row["account_id"]==account],output,"json")
                args=["--source",source,"--directory",profile,"--platform",platform,"--account",account]
                preview=cli(["preview-guarded-legacy-json",*args],{"passphrase":password})
                assert cli(["import-guarded-legacy-json",*args,"--confirm",preview["confirmation"]],{"passphrase":password})["created"]==(2 if platform=="qq" else 3)
        newer.close()
        # Fixture-only direct SQL ensures the adapter drops prohibited fields
        # even if present. Normal production guarded CLI never imports these.
        with sqlite3.connect(profile/"native-v1.sqlite3") as db:
            db.execute("""UPDATE messages SET media_url='file:///raw-fixture-url',
                extra='{"private":"raw-message-extra"}', raw_type='raw-provider-type',file_name='fixture-private-name'""")
            db.execute("""UPDATE conversations SET avatar_url='file:///raw-fixture-avatar',
                extra='{"private":"raw-conversation-extra"}'""")

            for account in ("alpha","other"):
                seed_contact(db,account)
                canonical_quote(db,account)
            for id in ("amb-ref","amb-a","amb-b"):
                extra={"wechat_quote":{"target_server_id":"812345678902"}} if id=="amb-ref" else {"_hub_wechat_server_id":"812345678902"}
                db.execute("""INSERT INTO messages(platform,account_id,id,conversation_id,timestamp,type,text,extra)
                    VALUES('wechat','alpha',?,'ambiguous',70,'text','synthetic ambiguous metadata',?)""",(id,json.dumps(extra)))

        token,no_opt,limited,wechat=[secrets.token_hex(32) for _ in range(4)]
        grants=root/"native-private-grants.json"
        grants.touch(mode=0o600)
        hash_token=lambda t:hashlib.sha256(t.encode()).hexdigest()
        permissions=["messages.read","conversations.read"]
        grants.write_text(json.dumps({"version":1,"grants":[
            {"id":"fixture.legacy","kind":"desktop","account_scope":{"platform":"qq","account_id":"alpha"},
             "permissions":permissions+["legacy.http.read","legacy.search.read","contacts.read"],"token_sha256":hash_token(token)},
            {"id":"fixture.no-opt","kind":"desktop","account_scope":{"platform":"qq","account_id":"alpha"},
             "permissions":permissions,"token_sha256":hash_token(no_opt)},
            {"id":"fixture.limited","kind":"desktop","account_scope":{"platform":"qq","account_id":"alpha"},
             "permissions":permissions+["legacy.http.read"],"token_sha256":hash_token(limited)},
            {"id":"fixture.wechat","kind":"desktop","account_scope":{"platform":"wechat","account_id":"alpha"},
             "permissions":permissions+["legacy.http.read","legacy.search.read"],"token_sha256":hash_token(wechat)}
        ]}))
        new=subprocess.Popen([BIN,"serve-guarded-legacy","--directory",profile,"--grants",grants],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        tasks.append(new)
        ready=proc_json(new)
        assert ready["mode"]=="guarded-legacy-read-only" and ready["locked"]
        port=int(ready["origin"].removeprefix("http://127.0.0.1:"))
        auth={"Authorization":"Bearer "+token}
        wx_auth={"Authorization":"Bearer "+wechat}
        restrictive={"Authorization":"Bearer "+limited}
        assert request(port,"/api/settings",auth)==(423,{"error":"应用已锁定","code":"account_locked"})
        new.stdin.write(raw({"command":"unlock","passphrase":password}))
        assert proc_json(new)=={"event":"unlocked"}
        assert request(port,"/api/settings")[0]==401
        assert request(port,"/api/settings",{"Authorization":"Bearer "+no_opt})[0]==401
        assert request(port,"/api/settings",{**auth,"Cookie":"lmh_session="+old_cookie})[0]==403
        assert request(port,"/api/settings",{**auth,"Origin":ready["origin"]})[0]==403
        assert request(port,"/api/settings",{**auth,"Host":f"localhost:{port}"})[0]==403
        rejected_secret=request(port,"/api/messages?token="+token,auth)
        assert rejected_secret[0]==400 and token not in str(rejected_secret[1])
        assert request(port,"/api/%6dessages",auth)[0]==404
        assert request(port,"/api/v1/messages",auth)[0]==404
        assert request(port,"/api/attachments/download",auth)[0]==404
        assert request(port,"/api/settings",auth,"POST")[0]==405

        new_result={}
        for url,expected in old_result.items():
            status,value=request(port,url,wx_auth if url in (WECHAT_CONTEXT,QUOTE) else auth)
            assert status==200
            new_result[url]=value
        key="/api/sources"
        stock_source=next(x for x in old_result[key]["sources"] if x["id"]=="qq")
        assert new_result[key]["sources"]==[stock_source]
        key="/api/settings"
        assert set(new_result[key]["capabilities"])==set(old_result[key]["capabilities"])
        # The native adapter adds a bounded indexed-account stats route for
        # Desktop grants holding message, conversation and contact read.
        # Preserve pinned stock settings defaults; the Rust adapter must not
        # advertise unrelated stock capabilities that the grant cannot use.
        enabled=lambda report:{x for x,v in report["capabilities"].items() if v["enabled"]}
        assert old_result[key]["capabilities"]==describe_capabilities(settings["capabilities"])
        assert enabled(new_result[key])=={"chat","search","contacts","stats"}
        assert request(port,"/api/stats",auth)==(200,{
            "message_count":2,"conversation_count":1,"contact_count":1,"media_count":0,
            "per_type":{"text":2},"per_platform":{"qq":{"messages":2,"conversations":1,"contacts":1}},
            "daily":[{"date":"1970-01-01","count":2}],"scope":"indexed_account",
            "media_count_basis":"normalized_message_kind_only",
        })
        assert request(port,"/api/stats?platform=qq&account_id=other",auth)[0]==404
        assert request(port,"/api/stats",restrictive)[0]==403
        assert new_result[key]["account_lock_enabled"] and "storage_dir" not in new_result[key]
        for url,fields in (("/api/conversations?platform=qq",CORE_CONV),(SCOPE,CORE_MSG)):
            before=old_result[url]
            after=new_result[url]
            assert before["total"]==after["total"]
            assert [{k:x[k] for k in fields} for x in before["items"]]==[{k:x[k] for k in fields} for x in after["items"]]
            assert not any(x in str(after) for x in ("raw-fixture","raw-message-extra","raw-provider-type","fixture-private-name","raw-conversation-extra","Current other"))
        for url,fields in [(SEARCH,CORE_MSG),(CONTACTS,CORE_CONTACT),(CONTEXT,CORE_MSG),(WECHAT_CONTEXT,CORE_MSG),(QUOTE,CORE_MSG)]:
            before=new_result[url]
            after=old_result[url]
            assert before["total"]==after["total"], url
            # Context ties follow local insertion order, so compare canonical
            # identity/body and selected ID separately instead of assuming
            # the original Python row ID survives normalized JSON import.
            get_items=lambda value: sorted([{k:x[k] for k in fields} for x in value["items"]],key=lambda item:item["id"])
            assert get_items(before)==get_items(after), url
            assert not any(secret in str(before) for secret in ["raw-fixture","fixture-contact-avatar","hidden-alias-fixture",
                "fixture-private-quote","712345678901","Current other","target other","reference other"])
            if "target_index" in before:
                assert before["items"][before["target_index"]]["id"]==after["items"][after["target_index"]]["id"]
        assert new_result[QUOTE]["target_message_id"]=="tgt"
        assert [x["id"] for x in new_result[WECHAT_CONTEXT]["items"]]==["ref","tgt"]
        assert [x["id"] for x in old_result[WECHAT_CONTEXT]["items"]]==["tgt","ref"]
        ambiguity=QUOTE.replace("conversation_id=wroom","conversation_id=ambiguous").replace("reference_message_id=ref","reference_message_id=amb-ref")
        assert request(port,ambiguity,wx_auth)[0]==404
        assert new_result[CONTACTS]["accounts"]==[{"platform":"qq","account_id":"alpha"}]
        assert request(port,CONTACTS+"&q=hidden-alias-fixture",auth)[1]["total"]==0
        assert request(port,CONTACTS.replace("alpha","other"),auth)[0]==403
        assert request(port,SEARCH.replace("Current","%25literal_"),auth)[1]["total"]==1
        assert request(port,SEARCH.replace("Current","%25missing_"),auth)[1]["total"]==0
        assert request(port,"/api/search?platform=wechat&q=equal&limit=1",wx_auth)[1]["items"][0]["id"]=="tgt"
        assert request(port,CONTEXT.replace("account_id=alpha","account_id=other"),auth)[0]==404
        assert request(port,CONTEXT+"&source_message_id=712345678901",auth)[0]==400
        assert request(port,QUOTE.replace("alpha","other"),wx_auth)[0]==404
        assert request(port,QUOTE.replace("conversation_id=wroom","conversation_id=separate"),wx_auth)[0]==404
        assert request(port,QUOTE+"&source_message_id=712345678901",wx_auth)[0]==400
        assert request(port,SEARCH+"&offset=999999",auth)[0]==400
        assert request(port,CONTACTS,restrictive)[0]==403 and request(port,SEARCH,restrictive)[0]==403
        restricted=request(port,"/api/settings",restrictive)[1]["capabilities"]
        assert {x for x,v in restricted.items() if v["enabled"]}=={"chat"}
        left=request(port,SCOPE+"&limit=1",auth)[1]
        assert left["has_more"] and left["items"][0]["id"]=="same-id"
        cursor=left["next_cursor"]
        assert request(port,SCOPE+"&limit=1&cursor="+cursor,auth)[1]["items"][0]["id"]=="older"
        stock_cursor=request(old.server_port,SCOPE+"&limit=1",old_headers)[1]["next_cursor"]
        assert request(port,SCOPE+"&limit=1&cursor="+stock_cursor,auth)[0]==400
        assert request(port,SCOPE.replace("alpha","other"),auth)[0]==403
        assert request(port,SCOPE+"&q=%ff",auth)[0]==400
        assert request(port,SCOPE+"&account_id=other",auth)[0]==400
        assert request(port,"/api/messages",auth)==request(old.server_port,"/api/messages",old_headers)==(
            200,{"items":[],"total":0,"has_more":False,"next_cursor":None})
        def parallel_read():
            with ThreadPoolExecutor(max_workers=3) as executor:
                return list(executor.map(lambda _:request(port,SCOPE,auth),range(9)))
        authorized=request(port,SCOPE,auth)
        assert authorized[0]==200 and parallel_read()==[authorized]*9
        new.stdin.write(raw({"command":"lock"}))
        assert proc_json(new)=={"event":"locked"}
        assert parallel_read()==[(423,{"error":"应用已锁定","code":"account_locked"})]*9
        new.stdin.write(raw({"command":"unlock","passphrase":password}))
        assert proc_json(new)=={"event":"unlocked"}
        assert parallel_read()==[authorized]*9
        new.stdin.write(raw({"command":"stop"}))
        assert proc_json(new)=={"event":"stopped"} and new.wait(timeout=5)==0
        assert not new.stderr.read()
        print("Pinned stock Handler over live synthetic loopback: 423 and eight old read-path shared wire shapes (fixture-only contacts and quotes); Rust scopes sources/settings/rows and rejects cookies, origin, foreign scope, old cursors and writes")
    finally:
        for process in tasks:
            if process.poll() is None:
                process.kill(); process.wait(timeout=5)
            for pipe in (process.stdin,process.stdout,process.stderr):
                pipe.close()
        old.shutdown()
        old.server_close()
        thread.join(timeout=3)
        stock.close()

if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="hub-legacy-http-") as directory:
        exercise(Path(directory))
