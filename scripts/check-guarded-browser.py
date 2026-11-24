#!/usr/bin/env python3
"""Real extracted guarded loopback HTTP session; no browser runtime."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
from hub.store import ChatStore

spec=importlib.util.spec_from_file_location("runtime",Path(__file__).with_name("smoke-linux-runtime.py"))
runtime=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
BIN=runtime.BIN
BOOT="/_local/browser/bootstrap"
PASS_FIELD="passphrase"


def model(root):
    guarded=root/"guarded"
    guarded.mkdir(mode=0o700)
    cfg=root/"grants.json"
    cfg.touch(mode=0o600)
    password="synthetic private browser fixture "+secrets.token_hex(14)
    desktop,other,wechat=[secrets.token_hex(32) for _ in range(3)]
    permissions=["legacy.http.read","conversations.read","messages.read","contacts.read","legacy.search.read"]
    def grant(id,token,scope,p,platform="qq"):
        return dict(id=id,kind="desktop",account_scope={"platform":platform,"account_id":scope},
            permissions=p,token_sha256=hashlib.sha256(token.encode()).hexdigest())
    cfg.write_text(json.dumps({"version":1,"grants":[
        grant("browser.opt",desktop,"A",permissions+["legacy.browser.read","analytics.annual.read"]),
        grant("other.grant",other,"B",permissions),
        grant("wechat.opt",wechat,"A",permissions+["legacy.browser.read"],"wechat")]}))
    raw=lambda value:(json.dumps(value)+"\n").encode()
    def command(args,private=None):
        p=subprocess.run([BIN,*args],input=raw(private) if private else None,capture_output=True,timeout=8)
        assert p.returncode==0,p.stderr.decode()
        assert not p.stderr
        return json.loads(p.stdout)
    command(["init-guarded-live","--directory",guarded],{PASS_FIELD:password})
    for account in ("A","B"):
        source=root/f"synthetic-{account}.json"
        source.write_text(json.dumps([{"platform":"qq","account_id":account,"conversation_id":"room","id":"same-id",
            "timestamp":12345,"type":"text","is_self":False,"text":"invented-body-"+account}]))
        params=["--source",source,"--directory",guarded,"--platform","qq","--account",account]
        preview=command(["preview-guarded-legacy-json",*params],{PASS_FIELD:password})
        assert command(["import-guarded-legacy-json",*params,"--confirm",preview["confirmation"]],
                       {PASS_FIELD:password})["created"]==1

    def args(id,directory=guarded):
        return [BIN,"serve-guarded-browser","--directory",directory,"--grants",cfg,"--grant-id",id]
    result=subprocess.run(args("other.grant"),input=b"",capture_output=True,timeout=6)
    assert result.returncode!=0 and not result.stdout and b"opted-in" in result.stderr
    bad=root/"not-a-control-pipe"
    with bad.open("wb") as handle:
        p=subprocess.run(args("browser.opt"),input=b"",stdout=handle,stderr=subprocess.PIPE,timeout=6)
        assert p.returncode!=0 and b"private piped stdout" in p.stderr
    assert bad.read_bytes()==b""
    tasks=[]
    secrets_seen=[password,desktop,other,wechat]

    def start(id="browser.opt",directory=guarded):
        proc=subprocess.Popen(args(id,directory),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,bufsize=0)
        tasks.append(proc)
        ready=runtime.jsonline(proc)
        assert ready["mode"]=="guarded-browser-read-only" and ready["locked"] is True
        assert all(s.encode() not in Path(f"/proc/{proc.pid}/cmdline").read_bytes() for s in secrets_seen)
        return proc,int(ready["origin"].split(":")[-1])

    def control(proc, command, **fields):
        proc.stdin.write(raw(dict(command=command,**fields)))
        proc.stdin.flush()
        return runtime.jsonline(proc)
    def ticket(proc):
        value=control(proc,"issue_browser_ticket")
        assert value["event"]=="browser_ticket" and value["expires_in"]==30 and len(value["ticket"])==64
        secrets_seen.append(value["ticket"])
        return value["ticket"]

    def request(port,path,method="GET",headers=None,content=None):
        conn=http.client.HTTPConnection("127.0.0.1",port,timeout=5)
        try:
            conn.request(method,path,body=content,headers=headers or {})
            reply=conn.getresponse()
            body=reply.read()
            for k,v in {
                "Cache-Control":"no-store","X-Content-Type-Options":"nosniff","Referrer-Policy":"no-referrer",
                "X-Frame-Options":"DENY","Cross-Origin-Resource-Policy":"same-origin",
                "Cross-Origin-Opener-Policy":"same-origin",
            }.items():
                assert reply.getheader(k)==v,(reply.status,k)
            assert "frame-ancestors 'none'" in reply.getheader("Content-Security-Policy")
            assert reply.getheader("Access-Control-Allow-Origin") is None
            assert reply.getheader("WWW-Authenticate") is None
            assert all(secret.encode() not in body for secret in secrets_seen)
            return reply.status,body,reply.getheader("Set-Cookie")
        finally: conn.close()
    def status(port,path,code,**args):
        observed,body,cookie=request(port,path,**args)
        assert observed==code,(observed,code,body)
        if code>=400: assert cookie is None
        return body
    def exchange(port,proof,**headers):
        return request(port,BOOT,"POST",{"X-LMH-Bootstrap":proof}|headers)
    def auth(cookie,**headers):
        return {"Cookie":cookie}|headers

    try:
        proc,port=start()
        assert json.loads(status(port,"/healthz",200))=={"status":"ok"}
        locked=json.loads(status(port,"/api/messages",423))
        assert locked["code"]=="account_locked"
        assert control(proc,"issue_browser_ticket")=={"event":"browser_ticket_refused"}
        assert control(proc,"unlock",passphrase=password)=={"event":"unlocked"}
        origin=f"http://127.0.0.1:{port}"
        proof=ticket(proc)
        assert exchange(port,proof,Origin="null")[0]==403
        assert exchange(port,proof,Origin=origin)[0]==403
        assert exchange(port,proof,Host="localhost:"+str(port))[0]==403
        assert exchange(port,proof,Cookie="extra=x")[0]==400
        status(port,BOOT+"?extra=1",400,method="POST",headers={"X-LMH-Bootstrap":proof})
        status(port,BOOT,400,method="POST",headers={"X-LMH-Bootstrap":proof},content=b"x")
        status(port,BOOT,401,method="POST",headers={"Authorization":"Bearer "+desktop})
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies=[f.result(timeout=6) for f in [pool.submit(exchange,port,proof),pool.submit(exchange,port,proof)]]
        assert sorted(r[0] for r in replies)==[204,401]
        winner=next(r for r in replies if r[0]==204)
        assert winner[1]==b""
        cookie=winner[2].split(";",1)[0]
        assert cookie.startswith("lmh_guarded_browser=") and len(cookie.split("=")[1])==64
        assert winner[2].split("; ")[1:]==["Path=/api","Max-Age=300","HttpOnly","SameSite=Strict"]
        secrets_seen.append(cookie.split("=")[1])
        assert exchange(port,proof)[0]==401
        status(port,"/api/messages",401,headers={"Authorization":"Bearer "+desktop})
        status(port,"/api/messages",403,headers=auth(cookie,Origin="http://127.0.0.1:1"))
        status(port,"/api/messages",403,headers=auth(cookie,Referer="http://127.0.0.1:1/fake"))
        status(port,"/api/messages",403,headers=auth(cookie,**{"Sec-Fetch-Site":"same-site"}))
        status(port,"/api/messages",401,headers=auth(cookie+"; duplicate=1"))
        status(port,"/api/messages",401,headers=auth(cookie,Authorization="Bearer "+desktop))
        status(port,"/api/messages",405,headers=auth(cookie),method="POST")
        status(port,"/api/messages",405,headers=auth(cookie),method="OPTIONS")
        paths={
            "/api/sources":200,"/api/settings":200,
            "/api/conversations?platform=qq&account_id=A":200,
            "/api/messages?platform=qq&account_id=A&conversation_id=room":200,
            "/api/search?platform=qq&q=invented":200,"/api/contacts?platform=qq&account_id=A":200,
            "/api/context?platform=qq&account_id=A&conversation_id=room&message_id=same-id":200,
            "/api/quoted-message?platform=qq&account_id=A&conversation_id=room&reference_message_id=same-id":400,
            "/api/reports/annual":404,"/":404,
        }
        for path,code in paths.items():
            body=status(port,path,code,headers=auth(cookie,Origin=origin,**{"Sec-Fetch-Site":"same-origin"}))
            assert b"invented-body-B" not in body
        data=json.loads(status(port,"/api/settings",200,headers=auth(cookie)))
        assert data["annual_activity_enabled"] is False and data["pair_activity_enabled"] is False
        own=json.loads(status(port,"/api/messages?platform=qq&account_id=A&conversation_id=room",200,headers=auth(cookie)))
        assert [r["text"] for r in own["items"]]==["invented-body-A"]
        status(port,"/api/messages?platform=qq&account_id=B&conversation_id=room",403,headers=auth(cookie))
        assert control(proc,"lock")=={"event":"locked"}
        status(port,"/api/messages",423,headers=auth(cookie))
        assert control(proc,"unlock",passphrase=password)=={"event":"unlocked"}
        status(port,"/api/messages",401,headers=auth(cookie))
        assert exchange(port,proof)[0]==401
        revoke=ticket(proc)
        assert control(proc,"revoke_browser")=={"event":"browser_revoked"}
        assert exchange(port,revoke)[0]==401
        first=ticket(proc)
        cookie1=exchange(port,first)[2].split(";",1)[0]
        replacement=ticket(proc)
        cookie2=exchange(port,replacement)[2].split(";",1)[0]
        status(port,"/api/settings",401,headers=auth(cookie1))
        status(port,"/api/settings",200,headers=auth(cookie2))
        assert control(proc,"stop")=={"event":"stopped"}
        assert proc.wait(timeout=6)==0 and not proc.stderr.read()
        reopened,restarted=start()
        status(restarted,"/api/messages",423,headers=auth(cookie2))
        assert control(reopened,"unlock",passphrase=password)=={"event":"unlocked"}
        status(restarted,"/api/messages",401,headers=auth(cookie2))
        assert exchange(restarted,replacement)[0]==401
        assert control(reopened,"stop")=={"event":"stopped"} and reopened.wait(timeout=6)==0
        assert not reopened.stderr.read()

        # An actual stock v9 detached import provides canonical WeChat quotes
        # and contacts, rather than relying on direct Rust fixture injection.
        stock=ChatStore(root/"synthetic-old.sqlite3")
        for who in ("A","B"):
            stock.ingest("wechat",who,[{"id":"room","title":"fixture"}],[
                {"id":"target","conversation_id":"room","timestamp":20,"type":"text",
                 "text":"local-quote-"+who,"_hub_wechat_server_id":"123456789"},
                {"id":"ref","conversation_id":"room","timestamp":21,"type":"text",
                 "text":"reference","wechat_quote":{"target_server_id":"123456789","private":"excluded"}}],
                 contacts=[{"id":"peer","name":"contact-"+who}])
        detached=stock.backup_to(root/"detached"/"index.sqlite3")
        stock.close()
        quoted=root/"quote-guarded"
        quoted.mkdir(mode=0o700)
        command(["init-guarded-live","--directory",quoted],{PASS_FIELD:password})
        params=["--source",detached,"--directory",quoted,"--platform","wechat","--account","A"]
        preview=command(["preview-guarded-v9",*params],{PASS_FIELD:password})
        command(["import-guarded-v9",*params,"--confirm",preview["confirmation"]],{PASS_FIELD:password})
        quoteproc,quoteport=start("wechat.opt",quoted)
        assert control(quoteproc,"unlock",passphrase=password)=={"event":"unlocked"}
        quote_cookie=exchange(quoteport,ticket(quoteproc))[2].split(";",1)[0]
        path="/api/quoted-message?platform=wechat&account_id=A&conversation_id=room&reference_message_id=ref"
        quote=json.loads(status(quoteport,path,200,headers=auth(quote_cookie)))
        assert quote["target_message_id"]=="target" and "local-quote-A" in str(quote)
        assert "local-quote-B" not in str(quote) and "excluded" not in str(quote)
        assert control(quoteproc,"stop")=={"event":"stopped"} and quoteproc.wait(timeout=6)==0
        assert not quoteproc.stderr.read()
    finally:
        for p in tasks:
            if p.poll() is None:
                p.kill()
                p.wait(timeout=5)
            for io in (p.stdin,p.stdout,p.stderr):
                io.close()


if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="lmh-browser-http-") as path:
        model(Path(path))
    print("extracted guarded browser HTTP only: scoped opt-in, one-ticket concurrent exchange/replay, strict host/origin/cookie headers, eight read projections, relock/replacement/restart; no user browser")
