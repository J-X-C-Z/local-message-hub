#!/usr/bin/env python3
"""Optional scoped MCP SDK acceptance plus pinned Python raw differential."""
import asyncio
from contextlib import AsyncExitStack
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

BIN=Path(sys.argv[1]).resolve(strict=True)
MODE=sys.argv[2]
ROOT=Path(__file__).resolve().parent.parent
ACCOUNT="账/户%"
ID="id/消息%"
URI="lmh://message/wechat/%E8%B4%A6%2F%E6%88%B7%25/id%2F%E6%B6%88%E6%81%AF%25"
TEMPLATE="lmh://message/{platform}/{account_id}/{message_id}"


def fixture(path):
    from hub.store import ChatStore
    legacy=ChatStore(path/"hub.sqlite3")
    legacy.ingest("wechat",ACCOUNT,[{"id":"room"}],[{
        "id":ID,"conversation_id":"room","text":"synthetic example","type":"text","timestamp":3,
        "sender_name":"测试","media_url":"file://exclude","extra":{"private":"legacy"}},
    ])
    legacy.backup_to(path/"index.sqlite3")
    legacy.close()
    # Existing Rust snapshot checks refuse any colocated running-profile marker.
    (path/"hub.sqlite3").unlink()
    token=secrets.token_hex(32)
    grants=path/"plugins.json"
    grants.write_text(json.dumps({"version":1,"grants":[{
        "id":"fixture.scoped","kind":"scoped-tool","account_scope":{"platform":"wechat","account_id":ACCOUNT},
        "permissions":["messages.read","mcp.sources.read","mcp.prompts.read"],
        "token_sha256":hashlib.sha256(token.encode()).hexdigest(),
    }]}))
    grants.chmod(0o600)
    argv=["mcp","--snapshot",str(path/"index.sqlite3"),"--grants",str(grants),"--plugin-id","fixture.scoped"]
    return argv, token


async def sdk(argv,token):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from importlib.metadata import version
    async with AsyncExitStack() as stack:
        reader,writer=await stack.enter_async_context(stdio_client(StdioServerParameters(
            command=str(BIN),args=argv,env={"LMH_PLUGIN_TOKEN":token})))
        session=await stack.enter_async_context(ClientSession(reader,writer))
        hello=await session.initialize()
        assert hello.capabilities.resources is not None and hello.capabilities.prompts is not None
        found=await session.call_tool("search_messages",{"query":"synthetic"})
        assert not found.isError and found.structuredContent["items"][0]["resource_uri"]==URI
        items=(await session.list_resources()).resources
        assert str(items[0].uri)=="lmh://sources"
        template=(await session.list_resource_templates()).resourceTemplates[0].uriTemplate
        assert template==TEMPLATE
        message=(await session.read_resource(URI)).contents[0]
        assert json.loads(message.text)["id"]==ID
        assert "file://exclude" not in message.text
        assert {p.name for p in (await session.list_prompts()).prompts}=={"cross_platform_search","inspect_attachment"}
        prompt=await session.get_prompt("inspect_attachment",{"platform":"wechat","account_id":ACCOUNT,"message_id":ID})
        assert URI in prompt.messages[0].content.text
        assert not (await session.call_tool("get_source_status",{})).isError
    print("Python MCP SDK",version("mcp"),"negotiated",hello.protocolVersion,"and called validated scoped resources, tools and prompts")


def oracle(argv,token,path):
    import importlib
    from types import ModuleType
    from hub.store import ChatStore
    # Isolate the historical server's LOCAL constant; no providers or web
    # runtime imported. MCPServer.handle, analytics, caps and store are stock.
    server=ModuleType("hub.server")
    server.LOCAL=path
    sys.modules["hub.server"]=server
    old=importlib.import_module("hub.mcp_server")
    store=ChatStore(path/"index.sqlite3")
    handler=old.MCPServer(store,allow_writes=False)
    messages = [
        {"id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"1"}}},
        {"method":"notifications/initialized"},
        {"id":2,"method":"tools/list","params":{}},
        {"id":3,"method":"resources/list","params":{}},
        {"id":4,"method":"resources/templates/list","params":{}},
        {"id":5,"method":"resources/read","params":{"uri":URI}},
        {"id":6,"method":"prompts/list","params":{}},
        {"id":7,"method":"tools/call","params":{"name":"get_source_status","arguments":{"platform":"wechat"}}},
        {"id":8,"method":"resources/read","params":{"uri":"lmh://sources"}},
    ]
    messages=[{"jsonrpc":"2.0",**frame} for frame in messages]
    try:
        old_results={frame["id"]:out["result"] for frame in messages
            if (out:=handler.handle(frame)) is not None}
    finally:
        store.close()
    completed=subprocess.run([BIN,*argv],input="".join(json.dumps(frame)+"\n" for frame in messages),
        text=True,capture_output=True,env={**os.environ,"LMH_PLUGIN_TOKEN":token},timeout=10,check=True)
    assert not completed.stderr
    new={v["id"]:v["result"] for line in completed.stdout.splitlines() if (v:=json.loads(line))}
    assert new[1]["protocolVersion"]==old_results[1]["protocolVersion"]
    assert set(new[1]["capabilities"])==set(old_results[1]["capabilities"])
    assert {x["name"] for x in new[2]["tools"]}<={x["name"] for x in old_results[2]["tools"]}
    assert new[3]["resources"][0]["uri"]==old_results[3]["resources"][0]["uri"]=="lmh://sources"
    assert new[4]["resourceTemplates"][0]["uriTemplate"]==old_results[4]["resourceTemplates"][0]["uriTemplate"]==TEMPLATE
    a,b=(json.loads(r[5]["contents"][0]["text"]) for r in (new,old_results))
    for k in ["id","platform","account_id","conversation_id","text","timestamp","resource_uri"]:
        assert a[k]==b[k]
    assert {x["name"] for x in new[6]["prompts"]}=={x["name"] for x in old_results[6]["prompts"]}
    a,b=[r[7]["structuredContent"]["items"][0] for r in (new, old_results)]
    assert a==b
    a=json.loads(new[8]["contents"][0]["text"])["items"][0]
    b=next(v for v in json.loads(old_results[8]["contents"][0]["text"])["items"] if v["platform"]=="wechat")
    for k in ["message_count","conversation_count","contact_count"]:
        assert a[k]==b[k]
    from jsonschema import Draft202012Validator
    checker=Draft202012Validator(json.loads((ROOT/"plugin-grants.schema.json").read_text()))
    original=json.loads((path/"plugins.json").read_text())
    checker.validate(original)
    for bad in ["producer","tool","desktop"]:
        clone=json.loads(json.dumps(original))
        clone["grants"][0]["kind"]=bad
        assert not checker.is_valid(clone)
    clone=json.loads(json.dumps(original))
    clone["grants"][0]["permissions"]=["mcp.sources.read","mcp.prompts.read"]
    assert not checker.is_valid(clone)
    print("Pinned stock raw handler: protocol/capability and resource/prompt/tool names, Unicode URI, scoped count shape; grant Draft 2020-12 validated")


if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="scoped-hub-mcp-") as root:
        path=Path(root)
        args,token=fixture(path)
        if MODE=="sdk":
            asyncio.run(asyncio.wait_for(sdk(args,token),timeout=15))
        elif MODE=="oracle":
            oracle(args,token,path)
        else:
            raise SystemExit("expected sdk or oracle")
