#!/usr/bin/env python3
"""Extracted native MCP wait smoke and pinned stock behavioral differential."""
import asyncio
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import select
import subprocess
import sys
import tempfile
from types import ModuleType

BIN=Path(sys.argv[1]).resolve(strict=True)
TOOL="wait_for_new_messages"


def legacy_case(path):
    from hub.store import ChatStore
    dummy=ModuleType("hub.server")
    dummy.LOCAL=path
    sys.modules["hub.server"]=dummy
    from hub.mcp_server import MCPServer
    old=ChatStore(path/"hub.sqlite3")
    handler=MCPServer(old,allow_writes=False)
    seq=0

    def ingest(id, is_self=False, text="text", account="synthetic"):
        old.ingest("qq",account,[{"id":"room"}],[{"id":id,"conversation_id":"room",
            "timestamp":123,"is_self":is_self,"text":text,"type":"text"}])

    def call(**kwargs):
        nonlocal seq
        seq+=1
        result=handler.handle({"jsonrpc":"2.0","id":seq,"method":"tools/call",
            "params":{"name":TOOL,"arguments":kwargs}})["result"]
        assert not result.get("isError")
        return result["structuredContent"]

    ingest("history")
    init=call(platform="qq")
    assert init["initialized"] and not init["messages"] and not init["timed_out"]
    ingest("self",True)
    ingest("history",text="edited")
    ingest("new")
    latest=call(platform="qq",after_event_id=init["cursor"],limit=1,timeout_seconds=0)
    expected=[m["id"] for m in latest["messages"]]
    assert expected==["new"] and not latest["timed_out"]
    empty=call(platform="qq",after_event_id=latest["cursor"],timeout_seconds=0)
    assert empty["timed_out"] and not empty["messages"]
    future=call(platform="qq",after_event_id=latest["cursor"]+999,timeout_seconds=0)
    assert future["reset"]
    old.close()
    print("Pinned stock raw handle: initialization hides history, self/revision skipped, new event delivered, timeout; stock global integer reset differs")
    return expected


async def native_case(root, expected):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    target=root/"isolated-live"
    target.mkdir(mode=0o700)
    subprocess.run([BIN,"init-live","--directory",target],check=True,capture_output=True,timeout=5)
    reader,producer=secrets.token_hex(32),secrets.token_hex(32)
    grants=root/"live-grants.json"
    grants.touch(mode=0o600)
    digest=lambda token:hashlib.sha256(token.encode()).hexdigest()
    grants.write_text(json.dumps({"version":1,"grants":[
        {"id":"mcp.wait","kind":"scoped-tool","account_scope":{"platform":"qq","account_id":"synthetic"},
         "permissions":["mcp.incoming.read"],"token_sha256":digest(reader)},
        {"id":"mcp.producer","kind":"producer","permissions":["messages.ingest"],"token_sha256":digest(producer)}
    ]}))
    task=subprocess.Popen([BIN,"desktop-supervise","--live",target,"--grants",grants],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
    try:
        assert select.select([task.stdout],[],[],5)[0]
        port=int(json.loads(task.stdout.readline())["origin"].removeprefix("http://127.0.0.1:"))

        def ingest(id, batch, is_self=False,text="content",account="synthetic"):
            req={"idempotency_key":batch,"messages":[{"platform":"qq","account_id":account,"conversation_id":"room",
                "message_id":id,"timestamp":123,"is_self":is_self,"text":text}]}
            conn=http.client.HTTPConnection("127.0.0.1",port,timeout=4)
            conn.request("POST","/api/v1/ingest",json.dumps(req),
                {"Authorization":"Bearer "+producer,"Content-Type":"application/json"})
            response=conn.getresponse()
            assert response.status==200
            response.read()
            conn.close()

        params=StdioServerParameters(command=str(BIN),args=["mcp","--live",str(target),"--grants",str(grants),
            "--plugin-id","mcp.wait"],env={"LMH_PLUGIN_TOKEN":reader})
        async def session_case(initial=None):
            async with stdio_client(params) as (receive,send):
                async with ClientSession(receive,send) as session:
                    await session.initialize()
                    tools=(await session.list_tools()).tools
                    assert [tool.name for tool in tools]==[TOOL] and tools[0].annotations.readOnlyHint
                    async def call(**kwargs):
                        result=await session.call_tool(TOOL,kwargs)
                        assert not result.isError
                        return result.structuredContent
                    if initial is None:
                        value=await call()
                        assert value["initialized"] and value["messages"]==[] and value["cursor"].endswith(":1")
                        assert (await session.call_tool(TOOL,{"after_event_id":1})).isError
                        return value["cursor"]
                    value=await call(after_cursor=initial,timeout_seconds=0,limit=1)
                    assert not value["timed_out"]
                    assert [msg["id"] for msg in value["messages"]]==expected
                    assert "text" not in value["messages"][0] and value["cursor"].endswith(":2")
                    empty=await call(after_cursor=value["cursor"],timeout_seconds=0)
                    assert empty["timed_out"] and not empty["messages"]
                    assert empty["cursor"]==value["cursor"]
        ingest("history","history")
        initial=await session_case()
        ingest("new","foreign",account="private-foreign")
        ingest("history","revision",text="edited")
        ingest("self","self",is_self=True)
        ingest("new","new")
        await session_case(initial)
        assert task.stdin.write(b"stop\n")==5
        assert select.select([task.stdout],[],[],4)[0]
        assert json.loads(task.stdout.readline())=={"event":"stopped","forced":False}
        assert task.wait(timeout=4)==0 and not task.stderr.read()
    finally:
        if task.poll() is None:
            task.kill()
            task.wait(timeout=4)
        for pipe in (task.stdin,task.stdout,task.stderr):
            pipe.close()
    print("Extracted native live MCP via SDK: scoped cursor resumed after stdio restart, no foreign/self/revision gaps, no bodies, bounded poll and old integer denied")


if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="native-mcp-incoming-") as d:
        path=Path(d)
        comparison=legacy_case(path)
        asyncio.run(asyncio.wait_for(native_case(path,comparison),timeout=20))
