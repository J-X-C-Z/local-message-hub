#!/usr/bin/env python3
"""HTTP exact assets and synthetic Node protocol harness; not a browser."""
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile

spec=importlib.util.spec_from_file_location("runtime",Path(__file__).with_name("smoke-linux-runtime.py"))
runtime=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
BIN=runtime.BIN
PAGE="/api/_reader/"
BOOT="/_local/browser/bootstrap"
ASSETS={
    PAGE:("index.html","text/html; charset=utf-8"),
    PAGE+"reader.css":("reader.css","text/css; charset=utf-8"),
    PAGE+"reader.js":("reader.js","text/javascript; charset=utf-8"),
}


def model(root):
    archive=root/"archive"
    archive.mkdir(mode=0o700)
    grants=root/"grants.json"
    grants.touch(mode=0o600)
    token=secrets.token_hex(32)
    password="synthetic protected reader "+secrets.token_hex(18)
    grants.write_text(json.dumps({"version":1,"grants":[{
        "id":"reader.account","kind":"desktop","account_scope":{"platform":"qq","account_id":"fixture-a"},
        "permissions":["legacy.http.read","legacy.browser.read","conversations.read","messages.read"],
        "token_sha256":hashlib.sha256(token.encode()).hexdigest()}]}))
    def raw(obj):
        return (json.dumps(obj,ensure_ascii=False)+"\n").encode()
    def cli(args):
        proc=subprocess.run([BIN,*args],input=raw({"passphrase":password}),capture_output=True,timeout=9)
        assert proc.returncode==0 and not proc.stderr,proc.stderr.decode()
        return json.loads(proc.stdout)
    cli(["init-guarded-live","--directory",archive])
    for acct in ("fixture-a","fixture-b"):
        source=root/(acct+".json")
        source.write_text(json.dumps([{
            "platform":"qq","account_id":acct,"conversation_id":"same-room","id":"same-id",
            "timestamp":123,"type":"text","is_self":False,
            "conversation_title":"<em>合成标题</em>" if acct=="fixture-a" else "foreign title",
            "text":"<b>合成正文</b>" if acct=="fixture-a" else "archive-b-private-body",
        }],ensure_ascii=False))
        args=["--source",source,"--directory",archive,"--platform","qq","--account",acct]
        proof=cli(["preview-guarded-legacy-json",*args])["confirmation"]
        assert cli(["import-guarded-legacy-json",*args,"--confirm",proof])["created"]==1

    tasks=[]
    def start(opt):
        proc=subprocess.Popen([BIN,"serve-guarded-browser","--directory",archive,"--grants",grants,
            "--grant-id","reader.account",*(["--reader"] if opt else [])],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        tasks.append(proc)
        ready=runtime.jsonline(proc)
        assert ready["locked"] is True
        if opt:
            assert ready["mode"]=="guarded-browser-reader-read-only" and ready["reader_path"]==PAGE
        else:
            assert ready["mode"]=="guarded-browser-read-only" and "reader_path" not in ready
        return proc,ready["origin"]
    def control(proc,cmd,**values):
        proc.stdin.write(raw(dict(command=cmd,**values)))
        proc.stdin.flush()
        return runtime.jsonline(proc)
    def req(origin,path,cookie=None,method="GET",headers=None):
        conn=http.client.HTTPConnection("127.0.0.1",int(origin.split(":")[-1]),timeout=5)
        values=dict(headers or {})
        if cookie:
            values["Cookie"]=cookie
        try:
            conn.request(method,path,headers=values)
            response=conn.getresponse()
            data=response.read()
            assert token.encode() not in data and password.encode() not in data
            assert b"archive-b-private-body" not in data
            assert response.getheader("Cache-Control")=="no-store"
            assert response.getheader("X-Content-Type-Options")=="nosniff"
            assert response.getheader("Access-Control-Allow-Origin") is None
            return response.status,data,{k.lower():v for k,v in response.getheaders()}
        finally:
            conn.close()
    def cookie_for(proc,origin):
        assert control(proc,"unlock",passphrase=password)=={"event":"unlocked"}
        ticket=control(proc,"issue_browser_ticket")["ticket"]
        status,_,headers=req(origin,BOOT,method="POST",headers={"X-LMH-Bootstrap":ticket})
        assert status==204 and "HttpOnly" in headers["set-cookie"]
        return headers["set-cookie"].split(";",1)[0]

    try:
        proc,origin=start(False)
        cookie=cookie_for(proc,origin)
        assert req(origin,PAGE,cookie)[0]==404
        assert control(proc,"stop")=={"event":"stopped"} and proc.wait(timeout=6)==0
        proc,origin=start(True)
        for path,(file,kind) in ASSETS.items():
            assert req(origin,path)[0]==423
        cookie=cookie_for(proc,origin)
        for path,(file,kind) in ASSETS.items():
            assert req(origin,path)[0]==401
            status,body,headers=req(origin,path,cookie)
            assert status==200 and headers["content-type"]==kind
            checked=(BIN.parent/"guarded-reader"/file).read_bytes()
            assert body==checked
            assert req(origin,path+"?unknown=1",cookie)[0]==400
            assert req(origin,path,cookie,method="POST")[0]==405
        _,page,headers=req(origin,PAGE,cookie)
        csp=headers["content-security-policy"]
        assert "default-src 'none'" in csp and "connect-src 'self'" in csp and "style-src 'self'" in csp
        assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "form-action 'none'" in csp
        assert "'unsafe-inline'" not in csp and "https:" not in csp
        assert b"/api/_reader/reader.css" in page and b"/api/_reader/reader.js" in page
        for path in ("/","/index.html","/app.js","/vendor/lucide.js",PAGE+"index.html",
                     PAGE+"hidden.js",PAGE+"%72eader.js",PAGE+"../reader.js"):
            assert req(origin,path,cookie)[0]==404
        assert req(origin,PAGE,cookie,headers={"Origin":"http://127.0.0.1:1"})[0]==403
        assert req(origin,PAGE,cookie,headers={"Sec-Fetch-Site":"same-site"})[0]==403
        assert req(origin,"/api/messages?platform=qq&account_id=fixture-b&conversation_id=same-room",cookie)[0]==403
        assert req(origin,"/api/messages?platform=qq&account_id=fixture-a&conversation_id=same-room",cookie)[0]==200
        node=shutil.which("node")
        assert node, "node required for the optional protocol fixture"
        fixture=Path(__file__).with_name("check-guarded-reader-js.cjs")
        checked=subprocess.run([node,fixture],input=raw({"origin":origin,"cookie":cookie}),
            capture_output=True,timeout=15)
        assert checked.returncode==0 and not checked.stderr,checked.stderr.decode()
        print(checked.stdout.decode().strip())
        assert control(proc,"lock")=={"event":"locked"}
        for path in ASSETS:
            assert req(origin,path,cookie)[0]==423
        assert control(proc,"unlock",passphrase=password)=={"event":"unlocked"}
        assert req(origin,PAGE,cookie)[0]==401
        assert control(proc,"stop")=={"event":"stopped"} and proc.wait(timeout=6)==0
        assert not proc.stderr.read()
    finally:
        for proc in tasks:
            if proc.poll() is None:
                proc.kill();proc.wait(timeout=5)
            for fd in (proc.stdin,proc.stdout,proc.stderr):
                fd.close()


if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="lmh-reader-http-") as path:
        model(Path(path))
    print("extracted HTTP exact reader assets, opt-in/private scope, literal MIME, header and traversal denial, lock/relock; no browser runtime")
