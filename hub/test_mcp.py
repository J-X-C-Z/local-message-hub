import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

from hub.store import ChatStore
from hub.mcp_server import MCPServer, serve
import hub.mcp_server as mcp_module


class MCPServerTest(unittest.TestCase):
    def test_every_mcp_tool_has_an_explicit_capability(self):
        self.assertEqual(set(mcp_module._TOOL_FIELDS), set(mcp_module._TOOL_CAPABILITY))

    def test_list_imports_paginates_beyond_first_hundred_and_reports_full_total(self):
        store = ChatStore(":memory:")
        for index in range(101):
            store.create_import_job({
                "id": f"job-{index:03d}", "fingerprint": f"{index:064x}",
                "filename": f"fixture-{index:03d}.json", "staging_file": "synthetic.json",
                "created_at": index, "total": 1, "valid": 1, "invalid": 0,
            })

        server = MCPServer(store)
        first = server.call_tool("list_imports", {"limit": 100, "offset": 0})
        second = server.call_tool("list_imports", {"limit": 100, "offset": 100})
        self.assertEqual(first["total"], 101)
        self.assertEqual(len(first["items"]), 100)
        self.assertEqual(second["total"], 101)
        self.assertEqual([item["filename"] for item in second["items"]], ["fixture-000.json"])
        self.assertEqual(server.call_tool("list_imports", {"limit": 10, "offset": 101}), {
            "items": [], "total": 101,
        })
        store.close()

    def test_group_members_tool_reuses_hub_scope_filter_and_paging(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from hub.server import Hub

        with tempfile.TemporaryDirectory() as storage:
            hub = Hub(storage, resume_imports=False)
            try:
                expected = {}
                for platform in ("wechat", "wecom", "qq"):
                    account_id, conversation_id = f"{platform}-account", f"{platform}-group"
                    hub.store.ingest(platform, account_id, [
                        {"id": conversation_id, "title": "Synthetic group", "kind": "group"},
                        {"id": f"{platform}-private", "title": "Private", "kind": "private"},
                    ], [])
                    if platform == "wechat":
                        reader = SimpleNamespace(account=account_id, get_group_members=lambda _cid: [
                            {"username": "member-1", "nick_name": "Alpha", "remark": ""},
                            {"username": "member-2", "nick_name": "Beta", "remark": ""},
                        ])
                        hub.providers[platform] = SimpleNamespace(reader=reader)
                    else:
                        provider = SimpleNamespace()
                        provider.members = Mock(side_effect=lambda _cid, account_id, limit, query: {
                            "items": [item for item in [
                                {"id": "member-1", "name": "Alpha"},
                                {"id": "member-2", "name": "Beta"},
                            ] if query.casefold() in item["name"].casefold()][:limit],
                            "total": 1 if query else 2,
                        })
                        hub.providers[platform] = provider
                    expected[platform] = (account_id, conversation_id)

                state = {"chat": True}
                server = MCPServer(hub.store, group_members_reader=hub.conversation_members,
                                   capability_reader=lambda: state)
                self.assertIn("list_group_members", {tool["name"] for tool in server.tools()})
                for platform, (account_id, conversation_id) in expected.items():
                    result = server.call_tool("list_group_members", {
                        "platform": platform, "account_id": account_id,
                        "conversation_id": conversation_id, "q": "Alpha", "limit": 1,
                    })
                    self.assertEqual(result["total"], 1)
                    self.assertEqual(len(result["items"]), 1)
                    self.assertEqual(result["items"][0]["name"], "Alpha")
                    if platform != "wechat":
                        hub.providers[platform].members.assert_called_once_with(
                            conversation_id, account_id=account_id, limit=1, query="Alpha")
                with self.assertRaisesRegex(ValueError, "群聊不存在或不属于指定账号"):
                    server.call_tool("list_group_members", {
                        "platform": "qq", "account_id": "wrong-account",
                        "conversation_id": expected["qq"][1],
                    })
                with self.assertRaisesRegex(ValueError, "群聊不存在或不属于指定账号"):
                    server.call_tool("list_group_members", {
                        "platform": "qq", "account_id": expected["qq"][0],
                        "conversation_id": "qq-private",
                    })
                state["chat"] = False
                self.assertNotIn("list_group_members", {tool["name"] for tool in server.tools()})
                denied = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                    "name": "list_group_members", "arguments": {
                        "platform": "qq", "account_id": expected["qq"][0],
                        "conversation_id": expected["qq"][1],
                    }}})["result"]
                self.assertTrue(denied["isError"])
            finally:
                hub.close()

    def test_group_members_lazily_opens_each_platform_without_sync(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from hub.server import Hub

        with tempfile.TemporaryDirectory() as storage:
            hub = Hub(storage, resume_imports=False)
            try:
                reader = SimpleNamespace(wxid='wechat-account', account='wechat-account_abcd',
                                         get_group_members=Mock(return_value=[
                                             {'username': 'member', 'nick_name': 'Member'}]))
                providers = {
                    'wechat': SimpleNamespace(reader=reader),
                    'wecom': SimpleNamespace(members=Mock(return_value={
                        'items': [{'id': 'member'}], 'total': 1})),
                    'qq': SimpleNamespace(members=Mock(return_value={
                        'items': [{'id': 'member'}], 'total': 1})),
                }
                targets = {
                    'wechat': 'integrations.wechat.provider.WeChatProvider',
                    'wecom': 'integrations.wecom.adapter.WeComAdapter',
                    'qq': 'integrations.qq.provider.QQProvider',
                }
                server = MCPServer(hub.store, group_members_reader=hub.conversation_members)
                for platform, target in targets.items():
                    account_id = f'{platform}-account'
                    hub.store.ingest(platform, account_id, [
                        {'id': 'group', 'title': 'Synthetic group', 'kind': 'group'}], [])
                    args = {'platform': platform, 'account_id': account_id,
                            'conversation_id': 'group', 'limit': 1}
                    module_name, class_name = target.rsplit('.', 1)
                    factory = Mock(return_value=providers[platform])
                    module = SimpleNamespace(**{class_name: factory})
                    with patch.dict(sys.modules, {module_name: module}), \
                            patch.object(hub, 'start_sync') as sync:
                        with self.assertRaises(ValueError):
                            server.call_tool('list_group_members', {**args, 'account_id': 'wrong'})
                        factory.assert_not_called()
                        for _ in range(2):
                            self.assertEqual(server.call_tool('list_group_members', args)['total'], 1)
                        factory.assert_called_once()
                        sync.assert_not_called()
                reader.wxid = 'other-account'
                with self.assertRaisesRegex(ValueError, '账号与会话不匹配'):
                    server.call_tool('list_group_members', {
                        'platform': 'wechat', 'account_id': 'wechat-account', 'conversation_id': 'group'})
                self.assertEqual(reader.get_group_members.call_count, 2)
            finally:
                hub.close()

    def test_analytics_tools_are_scoped_gated_and_content_free(self):
        store = ChatStore(":memory:")
        timestamp = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp())
        store.ingest("wechat", "acct-a", [
            {"id": "dm", "title": "Private", "kind": "private"},
            {"id": "group", "title": "Group", "kind": "group"},
        ], [
            {"id": "dm-1", "conversation_id": "dm", "timestamp": timestamp,
             "text": "PRIVATE_BODY_SHOULD_NOT_ESCAPE", "sender_id": "peer", "sender_name": "Peer"},
            {"id": "dm-2", "conversation_id": "dm", "timestamp": timestamp + 1,
             "text": "another private body", "is_self": True},
            {"id": "group-1", "conversation_id": "group", "timestamp": timestamp,
             "text": "GROUP_BODY_SHOULD_NOT_ESCAPE", "sender_id": "member", "sender_name": "Member"},
        ])
        store.ingest("wechat", "acct-b", [{"id": "dm", "title": "Other account", "kind": "private"}], [
            {"id": "other", "conversation_id": "dm", "timestamp": timestamp,
             "text": "OTHER_ACCOUNT_BODY", "sender_id": "other-peer"},
        ])
        state = {"advanced_analytics": True, "annual_reports": True}
        server = MCPServer(store, capability_reader=lambda: state)
        tool_names = {tool["name"] for tool in server.tools()}
        self.assertTrue({"list_private_chat_profiles", "list_group_chat_profiles",
                         "get_annual_report", "get_pair_report"} <= tool_names)

        private = server.call_tool("list_private_chat_profiles", {
            "platform": "wechat", "account_id": "acct-a"})["items"]
        self.assertEqual([(item["conversation_id"], item["message_count"]) for item in private], [("dm", 2)])
        group = server.call_tool("list_group_chat_profiles", {
            "platform": "wechat", "account_id": "acct-a", "conversation_id": "group"})["items"]
        self.assertEqual(group[0]["message_count"], 1)
        self.assertEqual(group[0]["members"][0]["name"], "Member")
        annual = server.call_tool("get_annual_report", {
            "platform": "wechat", "account_id": "acct-a", "conversation_id": "dm", "year": 2024})
        self.assertEqual(annual["total_messages"], 2)
        pair = server.call_tool("get_pair_report", {
            "platform": "wechat", "account_id": "acct-a", "conversation_id": "dm", "year": 2024})
        self.assertEqual(pair["counterpart_id"], "peer")
        encoded = json.dumps([private, group, annual, pair], ensure_ascii=False)
        self.assertNotIn("PRIVATE_BODY_SHOULD_NOT_ESCAPE", encoded)
        self.assertNotIn("GROUP_BODY_SHOULD_NOT_ESCAPE", encoded)
        self.assertNotIn("OTHER_ACCOUNT_BODY", encoded)
        with self.assertRaisesRegex(ValueError, "exact platform and account_id"):
            server.call_tool("get_annual_report", {"conversation_id": "dm"})
        with self.assertRaisesRegex(ValueError, "private conversations"):
            server.call_tool("get_pair_report", {
                "platform": "wechat", "account_id": "acct-a", "conversation_id": "group"})
        with self.assertRaisesRegex(ValueError, "member_limit"):
            server.call_tool("list_group_chat_profiles", {
                "platform": "wechat", "account_id": "acct-a", "member_limit": 51})

        state["advanced_analytics"] = False
        hidden = {tool["name"] for tool in server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
        self.assertNotIn("list_private_chat_profiles", hidden)
        denied = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "list_private_chat_profiles", "arguments": {}}})["result"]
        self.assertTrue(denied["isError"])
        self.assertIn("已在设置中关闭", denied["content"][0]["text"])
        store.close()

    def test_stats_capability_gates_mcp_sources_tool_and_resource_only(self):
        store = ChatStore(":memory:")
        store.ingest("wechat", "account", [{"id": "room", "title": "Room"}], [
            {"id": "message", "conversation_id": "room", "timestamp": 1, "text": "local"},
        ])
        state = {"mcp": True, "stats": False, "chat": True}
        server = MCPServer(store, capability_reader=lambda: state)

        tools = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertNotIn("list_sources", {tool["name"] for tool in tools})
        direct = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "list_sources", "arguments": {}}})["result"]
        self.assertTrue(direct["isError"])
        self.assertIn("已在设置中关闭", direct["content"][0]["text"])
        self.assertEqual(server.handle({"jsonrpc": "2.0", "id": 3,
            "method": "resources/list"})["result"]["resources"], [])
        source = server.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {
            "uri": "lmh://sources"}})
        self.assertEqual(source["error"]["message"], "Resource not found or invalid")

        templates = server.handle({"jsonrpc": "2.0", "id": 5,
            "method": "resources/templates/list"})["result"]["resourceTemplates"]
        self.assertEqual(len(templates), 1)
        chats = server.call_tool("list_conversations", {"platform": "wechat"})
        self.assertEqual(chats["items"][0]["id"], "room")
        store.close()

    def test_core_search_remains_available_when_chat_capability_is_disabled(self):
        store = ChatStore(":memory:")
        store.ingest("wechat", "account", [{"id": "room", "title": "Room"}], [
            {"id": "message", "conversation_id": "room", "timestamp": 1, "text": "private"},
        ])
        state = {"mcp": True, "chat": False}
        server = MCPServer(store, capability_reader=lambda: state)

        tools = server.handle({"jsonrpc": "2.0", "id": 1,
            "method": "tools/list"})["result"]["tools"]
        self.assertIn("search_messages", {tool["name"] for tool in tools})
        self.assertNotIn("list_conversations", {tool["name"] for tool in tools})
        result = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "search_messages", "arguments": {"query": "private"}}})["result"]
        self.assertFalse(result["isError"])
        self.assertIn("private", json.dumps(result))
        store.close()

    def test_wechat_quote_context_resolves_only_from_exact_indexed_reference(self):
        store = ChatStore(":memory:")
        store.ingest("wechat", "acct", [{"id": "room", "title": "Room"}], [
            {"id": "target", "conversation_id": "room", "timestamp": 1,
             "text": "quoted target", "_hub_wechat_server_id": "12345"},
            {"id": "reference", "conversation_id": "room", "timestamp": 2,
             "text": "reply", "wechat_quote": {"target_server_id": "12345"}},
            {"id": "elsewhere", "conversation_id": "other-room", "timestamp": 0,
             "text": "not this conversation", "_hub_wechat_server_id": "12345"},
        ])
        server = MCPServer(store)
        args = {"platform": "wechat", "account_id": "acct", "conversation_id": "room",
                "referencing_message_id": "reference"}
        result = server.call_tool("get_quoted_message_context", args)
        self.assertEqual(result["target_message_id"], "target")
        self.assertEqual(result["context"]["items"][0]["id"], "target")
        self.assertNotIn("12345", json.dumps(result))
        self.assertIn("get_quoted_message_context", {tool["name"] for tool in server.tools()})
        with self.assertRaisesRegex(ValueError, "Unexpected tool argument"):
            server.call_tool("get_quoted_message_context", {**args, "source_id": "12345"})
        with self.assertRaisesRegex(ValueError, "target not found"):
            server.call_tool("get_quoted_message_context", {**args, "conversation_id": "other-room"})

        store.ingest("wechat", "acct", [{"id": "room", "title": "Room"}], [
            {"id": "duplicate", "conversation_id": "room", "timestamp": 3,
             "text": "ambiguous", "_hub_wechat_server_id": "12345"},
        ])
        with self.assertRaisesRegex(ValueError, "target not found"):
            server.call_tool("get_quoted_message_context", args)
        store.close()

    def test_contact_avatar_returns_only_exactly_scoped_cached_image(self):
        import base64
        import hashlib

        from hub.server import Hub

        with tempfile.TemporaryDirectory() as storage:
            hub = Hub(storage, resume_imports=False)
            try:
                avatar_url = "hub-avatar://wechat/" + "a" * 64
                image = b"\xff\xd8\xffsynthetic-avatar"
                hub.store.ingest("wechat", "account-a", [], [], [{
                    "id": "contact-a", "name": "Synthetic", "avatar_url": avatar_url,
                }])
                hub.store.ingest("wechat", "account-b", [], [], [{
                    "id": "contact-a", "name": "Other account",
                    "avatar_url": "hub-avatar://wechat/" + "b" * 64,
                }])
                self.assertTrue(hub._cache_local_avatar(avatar_url, image))
                cache_path = Path(storage, "avatars", hashlib.sha256(
                    avatar_url.encode("utf-8")).hexdigest() + ".jpg")
                server = MCPServer(hub.store, avatar_reader=hub.read_contact_avatar)
                args = {"platform": "wechat", "account_id": "account-a", "contact_id": "contact-a"}
                result = server.handle({"jsonrpc": "2.0", "id": 41, "method": "tools/call",
                                        "params": {"name": "get_contact_avatar", "arguments": args}})["result"]
                self.assertFalse(result["isError"])
                self.assertEqual(result["content"][1], {
                    "type": "image", "data": base64.b64encode(image).decode("ascii"),
                    "mimeType": "image/jpeg",
                })
                self.assertEqual(result["structuredContent"], {
                    "platform": "wechat", "account_id": "account-a", "contact_id": "contact-a",
                    "mime_type": "image/jpeg", "bytes": len(image),
                })
                self.assertNotIn(str(cache_path), json.dumps(result))
                self.assertNotIn(avatar_url, json.dumps(result))
                with self.assertRaisesRegex(ValueError, "头像不在本机缓存中"):
                    hub.read_contact_avatar("wechat", "account-b", "contact-a")
                with self.assertRaisesRegex(ValueError, "联系人没有已缓存头像"):
                    hub.read_contact_avatar("wechat", "account-a", "missing")

                disabled = MCPServer(hub.store, capabilities={"contacts": False},
                                     avatar_reader=hub.read_contact_avatar)
                self.assertNotIn("get_contact_avatar", {tool["name"] for tool in disabled.tools()})
                denied = disabled.handle({"jsonrpc": "2.0", "id": 42, "method": "tools/call",
                                          "params": {"name": "get_contact_avatar", "arguments": args}})["result"]
                self.assertTrue(denied["isError"])
            finally:
                hub.close()

    def test_qq_cache_gallery_lists_opaque_ids_and_returns_explicit_image(self):
        import base64

        store = ChatStore(":memory:")
        png = b"\x89PNG\r\n\x1a\nfixture"
        calls = []
        server = MCPServer(
            store, capabilities={"media": True},
            qq_gallery_reader=lambda **filters: {
                "items": [{"resource_id": "opaque_fixture_id_1234", "name": "local.png",
                           "type": "image", "size": len(png)}],
                "total": 1, "has_more": False, "available": True, "account_id": "qq-acct"},
            media_reader=lambda message, _index: calls.append(message) or (png, "image/png"))
        listed = server.call_tool("list_qq_cache_resources", {"type": "image"})
        self.assertEqual(listed["items"][0]["resource_id"], "opaque_fixture_id_1234")
        self.assertNotIn("url", listed["items"][0])
        result = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "get_qq_cache_resource", "arguments": {
                "account_id": "qq-acct", "resource_id": "opaque_fixture_id_1234", "type": "image"}}})["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][1], {"type": "image", "data": base64.b64encode(png).decode("ascii"),
                                                "mimeType": "image/png"})
        self.assertEqual(calls[0]["resource_kind"], "qq_cache")
        store.close()

    def test_qq_cache_gallery_accepts_inclusive_utc8_date_bounds(self):
        store = ChatStore(":memory:")
        calls = []
        server = MCPServer(store, capabilities={"media": True},
                           qq_gallery_reader=lambda **filters: calls.append(filters) or {"items": []})
        tool = next(tool for tool in server.tools() if tool["name"] == "list_qq_cache_resources")
        self.assertIn("start", tool["inputSchema"]["properties"])
        self.assertIn("end", tool["inputSchema"]["properties"])

        server.call_tool("list_qq_cache_resources", {"start": "2026-10-01", "end": "2026-10-02"})
        local_tz = timezone(timedelta(hours=8))
        expected_start = int(datetime(2026, 10, 1, tzinfo=local_tz).timestamp()) * 1000
        expected_end = int(datetime(2026, 10, 3, tzinfo=local_tz).timestamp()) * 1000 - 1
        self.assertEqual(calls[0]["start_ms"], expected_start)
        self.assertEqual(calls[0]["end_ms"], expected_end)

        with self.assertRaises(ValueError):
            server.call_tool("list_qq_cache_resources", {"start": "2026-02-30"})
        with self.assertRaises(ValueError):
            server.call_tool("list_qq_cache_resources", {
                "start": "2026-10-03", "end": "2026-10-01"})
        store.close()

    def test_list_contacts_can_scope_to_account(self):
        store = ChatStore(":memory:")
        store.ingest("wechat", "acct-1", [], [], [{"id": "same", "name": "First"}])
        store.ingest("wechat", "acct-2", [], [], [{"id": "same", "name": "Second"}])
        server = MCPServer(store)
        result = server.call_tool("list_contacts", {
            "platform": "wechat", "account_id": "acct-2"})
        self.assertEqual([item["name"] for item in result["items"]], ["Second"])
        store.close()

    def test_list_media_can_scope_to_one_platform_account(self):
        store = ChatStore(":memory:")
        for account_id, message_id in (("acct-1", "one"), ("acct-2", "two")):
            store.ingest("wechat", account_id, [{"id": "room", "title": "Photos"}], [{
                "id": message_id, "conversation_id": "room", "timestamp": 1, "type": "image"}])
        server = MCPServer(store)
        result = server.call_tool("list_media", {
            "platform": "wechat", "account_id": "acct-2"})
        self.assertEqual([item["id"] for item in result["items"]], ["two"])
        with self.assertRaisesRegex(ValueError, "platform must be selected"):
            server.call_tool("list_media", {"platform": "all", "account_id": "acct-2"})
        store.close()

    def test_list_media_can_scope_to_exact_conversation_id(self):
        store = ChatStore(":memory:")
        conversations = [
            {"id": "room-1", "title": "Same title"},
            {"id": "room-2", "title": "Same title"},
        ]
        messages = [
            {"id": "image-1", "conversation_id": "room-1", "timestamp": 1, "type": "image"},
            {"id": "image-2", "conversation_id": "room-2", "timestamp": 2, "type": "image"},
        ]
        store.ingest("wechat", "acct", conversations, messages)
        server = MCPServer(store)
        tool = next(tool for tool in server.tools() if tool["name"] == "list_media")
        self.assertIn("conversation_id", tool["inputSchema"]["properties"])
        fuzzy = server.call_tool("list_media", {
            "platform": "wechat", "account_id": "acct", "query": "Same title"})
        self.assertEqual(fuzzy["total"], 2)
        exact = server.call_tool("list_media", {
            "platform": "wechat", "account_id": "acct", "conversation_id": "room-2"})
        self.assertEqual([item["id"] for item in exact["items"]], ["image-2"])
        for args in (
                {"conversation_id": "room-2"},
                {"platform": "wechat", "conversation_id": "room-2"},
                {"platform": "wechat", "account_id": "acct", "conversation_id": " "},
        ):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, "conversation_id"):
                server.call_tool("list_media", args)
        store.close()

    def test_wechat_source_media_mcp_is_conversation_scoped_and_reads_exact_cached_item(self):
        store = ChatStore(":memory:")
        png = b"\x89PNG\r\n\x1a\nsynthetic-image"
        list_calls, read_calls = [], []

        def list_source(params):
            list_calls.append(params)
            return {"items": [
                {"id": "room-a:message_0.db:7", "platform": "wechat",
                 "account_id": "acct-a", "conversation_id": "room-a", "timestamp": 12,
                 "sender_name": "Synthetic sender", "type": "image", "raw_type": "3",
                 "text": "PRIVATE BODY MUST NOT ESCAPE", "extra": {
                     "shard": "message_0.db", "local_id": 7}},
                {"id": "room-b:message_0.db:8", "platform": "wechat",
                 "account_id": "acct-a", "conversation_id": "room-b", "type": "image",
                 "extra": {"shard": "message_0.db", "local_id": 8}},
            ], "total": 1, "limit": 1, "next_cursor": "next-page"}

        def read_source(identity):
            read_calls.append(identity)
            return png, "image/png"

        server = MCPServer(store, capabilities={"media": True},
                           wechat_source_media_lister=list_source,
                           wechat_source_media_reader=read_source)
        names = {tool["name"] for tool in server.tools()}
        self.assertIn("list_wechat_source_media", names)
        self.assertIn("get_wechat_source_media", names)

        listing = server.call_tool("list_wechat_source_media", {
            "platform": "wechat", "account_id": "acct-a", "conversation_id": "room-a",
            "limit": 1, "cursor": "prior-page", "type": "image",
        })
        self.assertEqual(list_calls[0]["cursor"], "prior-page")
        self.assertEqual(list_calls[0]["conversation_id"], "room-a")
        self.assertEqual(listing["next_cursor"], "next-page")
        self.assertEqual(len(listing["items"]), 1)
        item = listing["items"][0]
        self.assertEqual((item["account_id"], item["conversation_id"], item["shard"],
                          item["local_id"]),
                         ("acct-a", "room-a", "message_0.db", "7"))
        self.assertNotIn("text", item)
        self.assertNotIn("PRIVATE BODY", json.dumps(listing))
        self.assertNotIn("path", json.dumps(listing))

        request = {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {
            "name": "get_wechat_source_media", "arguments": {
                "platform": "wechat", "account_id": "acct-a", "conversation_id": "room-a",
                "shard": "message_0.db", "local_id": "7", "type": "image",
            }}}
        response = server.handle(request)["result"]
        self.assertFalse(response["isError"])
        self.assertEqual(response["content"][1], {
            "type": "image", "data": __import__("base64").b64encode(png).decode("ascii"),
            "mimeType": "image/png"})
        self.assertEqual(read_calls, [{"platform": "wechat", "account_id": "acct-a",
                                       "conversation_id": "room-a", "shard": "message_0.db",
                                       "local_id": "7", "type": "image"}])
        store.close()

    def test_list_media_expands_wecom_and_qq_text_attachments(self):
        store = ChatStore(":memory:")
        store.ingest("wecom", "wecom-account", [{"id": "team", "title": "Team"}], [{
            "id": "wecom-parent", "conversation_id": "team", "timestamp": 300,
            "sender_id": "member", "sender_name": "Member", "type": "text",
            "text": "a message with attachments",
            "attachments": [
                {"name": "photo.png", "type": "image", "p": "C:/private/photo.png"},
                {"name": "notes.pdf", "type": "file", "media_url": "https://private.invalid/file"},
            ],
        }])
        store.ingest("qq", "qq-account", [{"id": "group", "title": "Group"}], [{
            "id": "qq-parent", "conversation_id": "group", "timestamp": 200,
            "sender_id": "qq-member", "sender_name": "QQ Member", "type": "text",
            "text": "QQ roaming attachments",
            "roaming_resources": [
                {"name": "clip.png", "type": "image"},
                {"name": "clip.mp4", "type": "video"},
            ],
        }])
        store.ingest("wechat", "wx-account", [{"id": "room", "title": "Room"}], [{
            "id": "wechat-image", "conversation_id": "room", "timestamp": 100,
            "type": "image", "file_name": "ordinary.png",
        }, {
            "id": "wechat-sticker", "conversation_id": "room", "timestamp": 99,
            "type": "image", "raw_type": "47", "text": "[表情]",
        }])
        store.replace_moments("wx-account", [{
            "id": "moment-image", "username": "friend", "createTime": 400,
            "media": [{"type": "image"}],
        }], None)
        selected = []
        image = b"\x89PNG\r\n\x1a\nsynthetic"
        server = MCPServer(store, media_reader=lambda message, index: (
            selected.append((message["platform"], message["id"], index)) or (image, "image/png")))

        result = server.call_tool("list_media", {"platform": "all", "limit": 2})
        self.assertEqual(result["total"], 6)
        self.assertEqual([(item["platform"], item["id"], item["media_entry"]["resource_index"])
                          for item in result["items"]], [
                              ("wecom", "wecom-parent", 0),
                              ("wecom", "wecom-parent", 1),
                          ])
        first = result["items"][0]
        self.assertEqual(first["resource_uri"],
                         "lmh://message/wecom/wecom-account/wecom-parent")
        self.assertEqual(first["media_entry"]["resource"], {
            "name": "photo.png", "type": "image"})
        self.assertNotIn("attachments", first["extra"])
        self.assertNotIn("C:/private", json.dumps(result))
        self.assertNotIn("private.invalid", json.dumps(result))

        image_items = server.call_tool("list_media", {"platform": "all", "type": "image"})
        self.assertEqual(image_items["total"], 3)
        self.assertEqual([(item["platform"], item["media_entry"]["resource_index"])
                          for item in image_items["items"]], [
                              ("wecom", 0), ("qq", 0), ("wechat", None),
                          ])
        media_tool = next(tool for tool in server.tools() if tool["name"] == "list_media")
        self.assertIn("sticker", media_tool["inputSchema"]["properties"]["type"]["enum"])
        sticker_items = server.call_tool("list_media", {
            "platform": "wechat", "type": "sticker"})
        self.assertEqual([item["id"] for item in sticker_items["items"]], ["wechat-sticker"])
        self.assertEqual(sticker_items["items"][0]["media_entry"]["type"], "sticker")
        qq_video = server.call_tool("list_media", {"platform": "qq", "type": "video"})
        self.assertEqual([(item["id"], item["media_entry"]["resource_index"])
                          for item in qq_video["items"]], [("qq-parent", 1)])
        self.assertNotIn("roaming_resources", qq_video["items"][0]["extra"])

        wecom_items = server.call_tool("list_media", {
            "platform": "wecom", "account_id": "wecom-account", "conversation_id": "team"})
        self.assertEqual([item["media_entry"]["resource_index"] for item in wecom_items["items"]], [0, 1])
        selected_attachment = server.handle({"jsonrpc": "2.0", "id": 78, "method": "tools/call", "params": {
            "name": "get_attachment", "arguments": {
                "platform": "wecom", "account_id": "wecom-account",
                "message_id": "wecom-parent", "resource_index": 0}}})["result"]
        self.assertFalse(selected_attachment["isError"])
        self.assertEqual(selected_attachment["structuredContent"]["resource_index"], 0)

        for platform, account_id, message_id, resource_index in (
                ("wecom", "wecom-account", "wecom-parent", 1),
                ("qq", "qq-account", "qq-parent", 0)):
            attached = server.handle({"jsonrpc": "2.0", "id": 77, "method": "tools/call", "params": {
                "name": "get_attachment", "arguments": {
                    "platform": platform, "account_id": account_id,
                    "message_id": message_id, "resource_index": resource_index}}})["result"]
            self.assertFalse(attached["isError"])
            self.assertEqual(attached["structuredContent"]["resource_index"], resource_index)
            self.assertEqual(attached["content"][1]["type"], "image")
        self.assertEqual(selected, [
            ("wecom", "wecom-parent", 0), ("wecom", "wecom-parent", 1),
            ("qq", "qq-parent", 0),
        ])
        store.close()

    def test_list_media_accepts_inclusive_utc8_date_bounds(self):
        store = ChatStore(":memory:")
        local = timezone(timedelta(hours=8))
        start = datetime(2024, 3, 1, tzinfo=local).timestamp()
        end = datetime(2024, 3, 2, tzinfo=local).timestamp()
        rows = [
            {"id": "before", "conversation_id": "room", "timestamp": start - 1,
             "type": "image"},
            {"id": "start", "conversation_id": "room", "timestamp": start,
             "type": "image"},
            {"id": "end", "conversation_id": "room", "timestamp": end - 1,
             "type": "image"},
            {"id": "after", "conversation_id": "room", "timestamp": end,
             "type": "image"},
        ]
        store.ingest("wechat", "account", [{"id": "room", "title": "Photos"}], rows)
        server = MCPServer(store)
        media_tool = next(tool for tool in server.tools() if tool["name"] == "list_media")
        self.assertIn("start", media_tool["inputSchema"]["properties"])
        self.assertIn("end", media_tool["inputSchema"]["properties"])

        result = server.call_tool("list_media", {
            "platform": "wechat", "start": "2024-03-01", "end": "2024-03-01"})
        self.assertEqual([item["id"] for item in result["items"]], ["end", "start"])
        self.assertEqual(server.call_tool("list_media", {
            "platform": "wechat", "start": "2024-03-02"})["total"], 1)
        self.assertEqual(server.call_tool("list_media", {
            "platform": "wechat", "end": "2024-02-29"})["total"], 1)
        with self.assertRaises(ValueError):
            server.call_tool("list_media", {"platform": "wechat", "start": "2024-02-30"})
        store.close()

    def test_relationship_tools_are_capability_gated_and_return_content_free_local_metrics(self):
        store = ChatStore(":memory:")
        store.ingest("wechat", "acct", [{"id": "peer", "title": "Synthetic peer", "kind": "direct"}], [
            {"id": "one", "conversation_id": "peer", "timestamp": 1704067200,
             "text": "private marker must never escape", "type": "text", "is_self": True},
            {"id": "two", "conversation_id": "peer", "timestamp": 1704153600,
             "text": "another private marker", "type": "image", "is_self": False},
        ])
        state = {"relationship_footprint": False}
        server = MCPServer(store, capability_reader=lambda: state)
        names = {tool["name"] for tool in server.tools()}
        self.assertNotIn("list_relationship_footprint", names)
        with self.assertRaisesRegex(ValueError, "已在设置中关闭"):
            server.call_tool("list_relationship_footprint", {})

        state["relationship_footprint"] = True
        names = {tool["name"] for tool in server.tools()}
        self.assertTrue({"list_relationship_footprint", "get_relationship_timeline",
                         "get_relationship_achievements"}.issubset(names))
        footprint = server.call_tool("list_relationship_footprint", {
            "platform": "wechat", "account_id": "acct", "limit": 10,
        })["items"]
        self.assertEqual(len(footprint), 1)
        self.assertEqual(footprint[0]["message_count"], 2)
        timeline = server.call_tool("get_relationship_timeline", {
            "platform": "wechat", "account_id": "acct", "conversation_id": "peer",
        })["items"]
        self.assertEqual(len(timeline), 2)
        achievements = server.call_tool("get_relationship_achievements", {
            "platform": "wechat", "account_id": "acct", "conversation_id": "peer",
        })
        serialized = json.dumps([footprint, timeline, achievements], ensure_ascii=False)
        self.assertNotIn("private marker", serialized)
        self.assertNotIn("text", footprint[0])
        self.assertNotIn("text", timeline[0])
        self.assertNotIn("text", achievements)
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            server.call_tool("get_relationship_timeline", {
                "platform": "wechat", "account_id": "acct", "conversation_id": "peer",
                "start": "20240101",
            })

        state["relationship_footprint"] = False
        with self.assertRaisesRegex(ValueError, "已在设置中关闭"):
            server.call_tool("get_relationship_achievements", {
                "platform": "wechat", "account_id": "acct", "conversation_id": "peer",
            })
        store.close()

    def test_moments_tool_is_optional_capability_gated_and_local_reader_only(self):
        store = ChatStore(':memory:')
        state = {'moments': False}
        calls = []
        server = MCPServer(store, capability_reader=lambda: state,
                           moments_reader=lambda **filters: calls.append(filters) or {
                               'available': True, 'items': [], 'total': 0})
        self.assertNotIn('search_moments', {tool['name'] for tool in server.tools()})
        with self.assertRaisesRegex(ValueError, '已在设置中关闭'):
            server.call_tool('search_moments', {'query': 'hello'})
        self.assertEqual(calls, [])
        state['moments'] = True
        self.assertIn('search_moments', {tool['name'] for tool in server.tools()})
        result = server.call_tool('search_moments', {'query': 'hello', 'username': 'wxid',
                                                     'account_id': 'wx-account'})
        self.assertTrue(result['available'])
        self.assertEqual(calls[0]['keyword'], 'hello')
        self.assertEqual(calls[0]['account_id'], 'wx-account')
        store.close()

    def test_get_moment_media_returns_only_explicitly_requested_local_image(self):
        import base64

        png = b"\x89PNG\r\n\x1a\nlocal-moments-image"
        token = "/local-moment-media/" + "A" * 32
        store = ChatStore(':memory:')
        server = MCPServer(store, capabilities={"moments": True},
                           moments_media_reader=lambda media_url: (png, "image/png")
                           if media_url == token else (_ for _ in ()).throw(ValueError("not found")))
        self.assertIn("get_moment_media", {tool["name"] for tool in server.tools()})
        result = server.handle({"jsonrpc": "2.0", "id": 21, "method": "tools/call", "params": {
            "name": "get_moment_media", "arguments": {"media_url": token}}})["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][1], {"type": "image", "data": base64.b64encode(png).decode("ascii"),
                                                  "mimeType": "image/png"})
        self.assertEqual(result["structuredContent"], {"mime_type": "image/png", "bytes": len(png)})
        missing = server.handle({"jsonrpc": "2.0", "id": 23, "method": "tools/call", "params": {
            "name": "get_moment_media", "arguments": {}}})["result"]
        self.assertTrue(missing["isError"])
        self.assertIn("Missing required tool argument", missing["content"][0]["text"])
        with self.assertRaisesRegex(ValueError, "local Moments image"):
            server.call_tool("get_moment_media", {"media_url": "https://example.invalid/image.png"})
        store.close()

    def test_get_moment_media_returns_explicit_local_avif_and_heic_as_images(self):
        import base64

        token = "/local-moment-media/" + "A" * 32
        fixtures = {
            "image/avif": b"\x00\x00\x00\x14ftypavif\x00\x00\x00\x00avis",
            "image/heic": b"\x00\x00\x00\x14ftypheic\x00\x00\x00\x00mif1",
        }
        store = ChatStore(":memory:")
        server = MCPServer(store, capabilities={"moments": True},
                           moments_media_reader=lambda _media_url: (b"", "image/png"))
        for mime, payload in fixtures.items():
            with self.subTest(mime=mime):
                # The local opaque token is the only input accepted by this reader.
                server.moments_media_reader = lambda media_url, mime=mime: (payload, mime)
                result = server.handle({"jsonrpc": "2.0", "id": 24, "method": "tools/call", "params": {
                    "name": "get_moment_media", "arguments": {"media_url": token}}})["result"]
                self.assertFalse(result["isError"])
                self.assertEqual(result["content"][1], {
                    "type": "image", "data": base64.b64encode(payload).decode("ascii"), "mimeType": mime})
        store.close()

    def test_prompts_follow_chat_and_media_capabilities_for_listing_and_get(self):
        store = ChatStore(":memory:")
        state = {"chat": True, "media": False}
        server = MCPServer(store, capability_reader=lambda: state)

        listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})["result"]["prompts"]
        self.assertEqual([prompt["name"] for prompt in listed], ["cross_platform_search"])
        with self.assertRaisesRegex(ValueError, "Unknown prompt"):
            server.get_prompt("inspect_attachment", {
                "platform": "wechat", "account_id": "fixture", "message_id": "message"})

        state["chat"] = False
        listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "prompts/list"})["result"]["prompts"]
        self.assertEqual(listed, [])
        with self.assertRaisesRegex(ValueError, "Unknown prompt"):
            server.get_prompt("cross_platform_search", {"query": "fixture"})
        store.close()

    def test_get_moment_media_returns_explicit_local_video_as_embedded_resource(self):
        import base64

        video = b"\x00\x00\x00\x18ftypisomsynthetic-video"
        token = "/local-moment-media/" + "V" * 32
        store = ChatStore(":memory:")
        calls = []

        def read_media(media_url):
            calls.append(media_url)
            if media_url != token:
                raise ValueError("not found")
            return video, "video/mp4"

        server = MCPServer(store, capabilities={"moments": True}, moments_media_reader=read_media)
        result = server.handle({"jsonrpc": "2.0", "id": 22, "method": "tools/call", "params": {
            "name": "get_moment_media", "arguments": {"media_url": token}}})["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(calls, [token])
        self.assertEqual(result["content"][1], {"type": "resource", "resource": {
            "uri": "lmh://moments/cached-media", "mimeType": "video/mp4",
            "blob": base64.b64encode(video).decode("ascii")}})
        self.assertEqual(result["structuredContent"], {"mime_type": "video/mp4", "bytes": len(video)})
        with self.assertRaisesRegex(ValueError, "local Moments image or video"):
            server.call_tool("get_moment_media", {"media_url": "C:/private/video.mp4"})
        store.close()

    def test_qq_send_mcp_only_prepares_pending_request_and_reads_status(self):
        from hub.server import Hub

        with tempfile.TemporaryDirectory() as storage:
            deliveries = []

            class Adapter:
                def deliver(self, request):
                    deliveries.append(request)
                    return {"message_id": "should-not-run"}

            hub = Hub(storage, resume_imports=False, send_adapter=Adapter())
            try:
                hub.store.ingest("qq", "12345678", [
                    {"id": "87654321", "title": "Synthetic peer", "kind": "private"},
                    {"id": "group-123", "title": "Synthetic group", "kind": "group"},
                    {"id": "u_synthetic_peer", "title": "Synthetic local peer", "kind": "private",
                     "raw": {"peerUin": "12345001"}},
                ], [])
                server = MCPServer(
                    hub.store, send_service=hub.send_service, capabilities={"qq_send": True},
                    qq_send_target_resolver=hub.resolve_qq_send_target)
                tools = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
                names = {tool["name"] for tool in tools}
                self.assertIn("prepare_qq_send", names)
                self.assertIn("get_qq_send_status", names)
                self.assertFalse(names & {"approve_qq_send", "send_qq_message", "deliver_qq_send"})
                prepare_schema = next(tool["inputSchema"] for tool in tools
                                      if tool["name"] == "prepare_qq_send")
                self.assertIn("request_id", prepare_schema["required"])

                invalid_targets = (
                    {"account_id": "12345678", "conversation_type": "private",
                     "conversation_id": "87654322", "request_id": "missing_send_01"},
                    {"account_id": "12345679", "conversation_type": "private",
                     "conversation_id": "87654321", "request_id": "wrongacct_send_01"},
                    {"account_id": "12345678", "conversation_type": "group",
                     "conversation_id": "87654321", "request_id": "wrongtype_send_01"},
                )
                for target in invalid_targets:
                    with self.subTest(target=target), self.assertRaises(ValueError):
                        server.call_tool("prepare_qq_send", {
                            **target, "text": "synthetic test"})
                    self.assertIsNone(hub.send_repository.get(target["request_id"]))

                prepared = server.call_tool("prepare_qq_send", {
                    "account_id": "12345678", "conversation_type": "private",
                    "conversation_id": "87654321", "text": "synthetic test", "request_id": "test_send_01",
                })
                self.assertEqual(prepared["status"], "pending_approval")
                self.assertTrue(prepared["created"])
                self.assertEqual(deliveries, [])
                mapped = server.call_tool("prepare_qq_send", {
                    "account_id": "12345678", "conversation_type": "private",
                    "conversation_id": "u_synthetic_peer", "text": "synthetic UID target",
                    "request_id": "mapped_send_01"})
                self.assertEqual(mapped["conversation_id"], "12345001")
                self.assertEqual(mapped["source_conversation_id"], "u_synthetic_peer")
                status = server.call_tool("get_qq_send_status", {"request_id": "test_send_01"})
                self.assertEqual(status["status"], "pending_approval")
                self.assertEqual([event["action"] for event in status["audit"]], ["prepared"])
                with self.assertRaisesRegex(ValueError, "Unknown tool"):
                    server.call_tool("approve_qq_send", {"request_id": "test_send_01"})
                with self.assertRaisesRegex(ValueError, "Missing required tool argument"):
                    server.call_tool("prepare_qq_send", {
                        "account_id": "12345678", "conversation_type": "private",
                        "conversation_id": "87654321", "text": "retry-safe test",
                    })
            finally:
                hub.close()

    def test_wecom_bot_mcp_only_prepares_matching_bot_request_and_reads_status(self):
        from hub.sending import SendRepository, SendService

        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            deliveries = []

            class Adapter:
                def deliver(self, request):
                    deliveries.append(request)
                    return {"message_id": "should-not-run"}

            service = SendService(SendRepository(f"{storage}/hub.sqlite3"),
                                  {"qq": Adapter(), "wecom_bot": Adapter()})
            identity = {"configured": True, "bot_id": "bot_123"}
            server = MCPServer(store, send_service=service,
                               capabilities={"qq_send": True, "wecom_bot_send": True},
                               wecom_bot_identity_reader=lambda: identity)
            names = {tool["name"] for tool in server.tools()}
            self.assertIn("prepare_wecom_bot_send", names)
            self.assertIn("get_wecom_bot_send_status", names)
            self.assertFalse(names & {"approve_wecom_bot_send", "send_wecom_bot_message"})

            args = {"account_id": "bot_123", "conversation_type": "private",
                    "conversation_id": "user_1", "text": "synthetic test",
                    "request_id": "test_wecom_01"}
            with self.assertRaisesRegex(ValueError, "match the configured WeCom bot ID"):
                server.call_tool("prepare_wecom_bot_send", {**args, "account_id": "other_bot"})
            prepared = server.call_tool("prepare_wecom_bot_send", args)
            self.assertEqual(prepared["status"], "pending_approval")
            self.assertEqual(prepared["platform"], "wecom_bot")
            self.assertIn("非当前员工身份", prepared["identity_label"])
            self.assertEqual(deliveries, [])
            status = server.call_tool("get_wecom_bot_send_status", {"request_id": "test_wecom_01"})
            self.assertEqual(status["status"], "pending_approval")
            self.assertEqual(service.status("test_wecom_01", "qq"), None)
            with self.assertRaisesRegex(ValueError, "Send request not found"):
                server.call_tool("get_qq_send_status", {"request_id": "test_wecom_01"})
            service.repository.close()
            store.close()


    def test_wechat_mcp_only_prepares_pending_request_and_reads_status(self):
        from hub.sending import SendRepository, SendService

        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            deliveries = []
            readiness = {"state": "ready", "account_id": "wxid_self0001"}

            class Adapter:
                def deliver(self, request):
                    deliveries.append(request)
                    return {"message_id": "should-not-run"}

            service = SendService(SendRepository(f"{storage}/hub.sqlite3"),
                                  {"qq": Adapter(), "wechat": Adapter()})
            server = MCPServer(store, send_service=service,
                               capabilities={"qq_send": True, "wechat_send": True},
                               wechat_readiness_reader=lambda: readiness)
            names = {tool["name"] for tool in server.tools()}
            self.assertIn("prepare_wechat_send", names)
            self.assertIn("get_wechat_send_status", names)
            self.assertFalse(names & {"approve_wechat_send", "send_wechat_message",
                                      "deliver_wechat_send"})

            prepared = server.call_tool("prepare_wechat_send", {
                "account_id": "wxid_self0001", "conversation_type": "private",
                "conversation_id": "wxid_friend01", "text": "synthetic test",
                "request_id": "test_wechat_01"})
            self.assertEqual(prepared["status"], "pending_approval")
            self.assertEqual(prepared["platform"], "wechat")
            self.assertIn("GUI 拟人操作", prepared["identity_label"])
            with self.assertRaisesRegex(ValueError, "currently logged-in WeChat account"):
                server.call_tool("prepare_wechat_send", {
                    "account_id": "wxid_other0001", "conversation_type": "private",
                    "conversation_id": "wxid_friend01", "text": "synthetic mismatch",
                    "request_id": "test_wechat_mismatch"})
            self.assertIsNone(service.repository.get("test_wechat_mismatch"))
            self.assertEqual(deliveries, [])
            status = server.call_tool("get_wechat_send_status", {"request_id": "test_wechat_01"})
            self.assertEqual(status["status"], "pending_approval")
            self.assertEqual(service.status("test_wechat_01", "qq"), None)
            with self.assertRaisesRegex(ValueError, "Send request not found"):
                server.call_tool("get_qq_send_status", {"request_id": "test_wechat_01"})
            with self.assertRaisesRegex(ValueError, "Unknown tool"):
                server.call_tool("approve_wechat_send", {"request_id": "test_wechat_01"})
            service.repository.close()
            store.close()

    def test_wechat_mcp_tools_require_enabled_capability_and_ready_client(self):
        from hub.sending import SendRepository, SendService

        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            service = SendService(SendRepository(f"{storage}/hub.sqlite3"), object())
            state = {"wechat_send": False}
            readiness = {"state": "ready", "account_id": "wxid_self0001"}
            server = MCPServer(store, send_service=service,
                               capability_reader=lambda: state,
                               wechat_readiness_reader=lambda: readiness)
            self.assertNotIn("prepare_wechat_send", {tool["name"] for tool in server.tools()})
            with self.assertRaisesRegex(ValueError, "已在设置中关闭"):
                server.call_tool("prepare_wechat_send", {})
            state["wechat_send"] = True
            readiness["state"] = "window_not_found"
            self.assertNotIn("prepare_wechat_send", {tool["name"] for tool in server.tools()})
            with self.assertRaisesRegex(ValueError, "微信客户端不可用"):
                server.call_tool("prepare_wechat_send", {})
            self.assertEqual(service.list("all")["items"], [])
            service.repository.close()
            store.close()

    def test_wechat_mcp_status_remains_available_when_client_is_offline(self):
        from hub.sending import SendRepository, SendService

        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            deliveries = []

            class Adapter:
                def deliver(self, request):
                    deliveries.append(request)
                    return {"message_id": "should-not-run"}

            service = SendService(SendRepository(f"{storage}/hub.sqlite3"), {"wechat": Adapter()})
            readiness = {"state": "ready", "account_id": "wxid_self0001"}
            server = MCPServer(store, send_service=service,
                               capabilities={"wechat_send": True},
                               wechat_readiness_reader=lambda: readiness)
            service.prepare({"platform": "wechat", "account_id": "wxid_self0001",
                             "conversation_type": "private", "conversation_id": "wxid_friend01",
                             "text": "synthetic", "request_id": "offline_status_01"})

            readiness["state"] = "window_not_found"
            names = {tool["name"] for tool in server.tools()}
            self.assertIn("get_wechat_send_status", names)
            self.assertNotIn("prepare_wechat_send", names)
            status = server.call_tool("get_wechat_send_status", {
                "request_id": "offline_status_01"})
            self.assertEqual(status["status"], "pending_approval")
            self.assertEqual(deliveries, [])
            service.repository.close()
            store.close()

    def test_wechat_mcp_validates_conversation_type_and_ids(self):
        from hub.sending import SendRepository, SendService

        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            service = SendService(SendRepository(f"{storage}/hub.sqlite3"), object())
            server = MCPServer(store, send_service=service, capabilities={"wechat_send": True},
                               wechat_readiness_reader=lambda: {
                                   "state": "ready", "account_id": "wxid_self0001"})
            valid = {"account_id": "wxid_self0001", "conversation_type": "private",
                     "conversation_id": "wxid_friend01", "text": "synthetic",
                     "request_id": "test_wechat_02"}
            for invalid in (
                {**valid, "account_id": "wx id with spaces"},
                {**valid, "conversation_type": "channel"},
                {**valid, "conversation_id": "room id with spaces"},
                {**valid, "conversation_type": "group", "conversation_id": "wxid_friend01"},
                {**valid, "text": "  "},
                {**valid, "text": 42},
            ):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    server.call_tool("prepare_wechat_send", invalid)
            group = server.call_tool("prepare_wechat_send", {
                **valid, "conversation_type": "group",
                "conversation_id": "44766057488@chatroom", "request_id": "test_wechat_group"})
            self.assertEqual(group["conversation_id"], "44766057488@chatroom")
            self.assertEqual(group["status"], "pending_approval")
            pending = service.list("all")["items"]
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["conversation_id"], "44766057488@chatroom")
            self.assertEqual(pending[0]["status"], "pending_approval")
            tool = next(tool for tool in server.tools() if tool["name"] == "prepare_wechat_send")
            schema = tool["inputSchema"]["properties"]["conversation_id"]
            self.assertEqual(schema["maxLength"], 73)
            self.assertIn("@chatroom", schema["pattern"])
            service.repository.close()
            store.close()

    def test_wecom_bot_mcp_tools_require_enabled_capability_and_configured_identity(self):
        from hub.sending import SendRepository, SendService

        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            service = SendService(SendRepository(f"{storage}/hub.sqlite3"), object())
            state = {"wecom_bot_send": False}
            identity = {"configured": True, "bot_id": "bot_123"}
            server = MCPServer(store, send_service=service,
                               capability_reader=lambda: state,
                               wecom_bot_identity_reader=lambda: identity)
            self.assertNotIn("prepare_wecom_bot_send", {tool["name"] for tool in server.tools()})
            with self.assertRaisesRegex(ValueError, "已在设置中关闭"):
                server.call_tool("prepare_wecom_bot_send", {})
            state["wecom_bot_send"] = True
            identity["configured"] = False
            self.assertNotIn("prepare_wecom_bot_send", {tool["name"] for tool in server.tools()})
            with self.assertRaisesRegex(ValueError, "企微机器人未配置"):
                server.call_tool("prepare_wecom_bot_send", {})
            self.assertEqual(service.list("all")["items"], [])
            service.repository.close()
            store.close()

    def test_qq_send_tools_are_hidden_and_direct_calls_denied_when_disabled(self):
        from hub.sending import SendRepository, SendService

        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            service = SendService(SendRepository(f"{storage}/hub.sqlite3"), object())
            state = {"qq_send": False}
            server = MCPServer(store, send_service=service, capability_reader=lambda: state)
            self.assertNotIn("prepare_qq_send", {tool["name"] for tool in server.tools()})
            with self.assertRaisesRegex(ValueError, "已在设置中关闭"):
                server.call_tool("prepare_qq_send", {
                    "account_id": "12345678", "conversation_type": "private",
                    "conversation_id": "87654321", "text": "synthetic", "request_id": "test_send_02",
                })
            self.assertEqual(service.list("all")["items"], [])
            service.repository.close()
            store.close()

    def test_qq_send_prepare_is_hidden_without_local_target_resolver(self):
        from hub.sending import SendRepository, SendService

        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            service = SendService(SendRepository(f"{storage}/hub.sqlite3"), object())
            server = MCPServer(store, send_service=service, capabilities={"qq_send": True})
            self.assertNotIn("prepare_qq_send", {tool["name"] for tool in server.tools()})
            with self.assertRaisesRegex(ValueError, "QQ 本机目标校验暂不可用"):
                server.call_tool("prepare_qq_send", {
                    "account_id": "12345678", "conversation_type": "private",
                    "conversation_id": "87654321", "text": "synthetic",
                    "request_id": "no_resolver_01"})
            self.assertIsNone(service.repository.get("no_resolver_01"))
            service.repository.close()
            store.close()

    def test_qq_send_mcp_validates_text_type_and_ids(self):
        from hub.sending import SendRepository, SendService

        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            service = SendService(SendRepository(f"{storage}/hub.sqlite3"), object())
            server = MCPServer(store, send_service=service, capabilities={"qq_send": True})
            valid = {"account_id": "12345678", "conversation_type": "private",
                     "conversation_id": "87654321", "text": "synthetic", "request_id": "test_send_03"}
            for invalid in (
                {**valid, "account_id": "not-a-qq"},
                {**valid, "conversation_type": "channel"},
                {**valid, "conversation_id": "abc"},
                {**valid, "text": "  "},
                {**valid, "text": 42},
            ):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    server.call_tool("prepare_qq_send", invalid)
            self.assertEqual(service.list("all")["items"], [])
            service.repository.close()
            store.close()

    def test_disabled_mcp_modules_are_hidden_and_direct_calls_are_denied_without_data_changes(self):
        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            store.ingest("wechat", "account", [{"id": "room", "title": "Room"}], [{
                "id": "message", "conversation_id": "room", "timestamp": 1,
                "type": "image", "text": "photo marker",
            }])
            server = MCPServer(store, allow_writes=True,
                               capabilities={"media": False, "chat": False, "organization": False})
            names = {tool["name"] for tool in server.tools()}
            self.assertIn("search_messages", names)
            self.assertNotIn("list_conversations", names)
            self.assertNotIn("get_message", names)
            self.assertNotIn("list_media", names)
            self.assertNotIn("update_conversation_metadata", names)
            self.assertEqual(server.call_tool("search_messages", {"query": "photo"})["items"][0]["text"],
                             "photo marker")
            with self.assertRaisesRegex(ValueError, "已在设置中关闭"):
                server.call_tool("list_conversations", {"platform": "wechat"})
            with self.assertRaisesRegex(ValueError, "已在设置中关闭"):
                server.call_tool("get_message", {"platform": "wechat", "account_id": "account",
                                                  "message_id": "message"})
            with self.assertRaisesRegex(ValueError, "已在设置中关闭"):
                server.call_tool("list_media", {})
            with self.assertRaisesRegex(ValueError, "已在设置中关闭"):
                server.read_resource("lmh://message/wechat/account/message")
            self.assertEqual(store.count("wechat"), 1)
            self.assertEqual(store.conversations("wechat")["items"][0]["note"], "")
            store.close()

    def test_mcp_refreshes_capabilities_for_each_direct_tool_call(self):
        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            state = {"media": False}
            server = MCPServer(store, capability_reader=lambda: state)
            self.assertNotIn("list_media", {tool["name"] for tool in server.tools()})
            state["media"] = True
            self.assertIn("list_media", {tool["name"] for tool in server.tools()})
            self.assertEqual(server.call_tool("list_media", {})["total"], 0)
            store.close()

    def test_tools_hide_reader_backed_operations_without_their_local_reader(self):
        store = ChatStore(":memory:")
        capabilities = {"moments": True, "media": True}
        server = MCPServer(store, capabilities=capabilities)
        names = {tool["name"] for tool in server.tools()}
        for name in ("search_moments", "get_moment_media", "get_attachment",
                     "list_qq_cache_resources", "get_qq_cache_resource"):
            self.assertNotIn(name, names)

        configured = MCPServer(
            store, capabilities=capabilities,
            moments_reader=lambda **_kwargs: {"items": []},
            moments_media_reader=lambda _url: (b"", "image/png"),
            media_reader=lambda _message, _index: (b"", "image/png"),
            qq_gallery_reader=lambda **_filters: {"items": []})
        names = {tool["name"] for tool in configured.tools()}
        for name in ("search_moments", "get_moment_media", "get_attachment",
                     "list_qq_cache_resources", "get_qq_cache_resource"):
            self.assertIn(name, names)
        store.close()

    def test_mcp_resource_and_prompt_endpoints_refresh_revoked_capabilities(self):
        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            state = {"mcp": True, "chat": True}
            server = MCPServer(store, capability_reader=lambda: state)
            self.assertEqual(len(server.handle({"jsonrpc": "2.0", "id": 1,
                "method": "resources/templates/list"})["result"]["resourceTemplates"]), 1)

            state["chat"] = False
            self.assertEqual(server.handle({"jsonrpc": "2.0", "id": 2,
                "method": "resources/templates/list"})["result"]["resourceTemplates"], [])

            state["mcp"] = False
            for request_id, method in enumerate(("resources/list", "resources/templates/list",
                                                  "prompts/list", "prompts/get"), 3):
                response = server.handle({"jsonrpc": "2.0", "id": request_id,
                    "method": method, "params": {"name": "cross_platform_search",
                                                    "arguments": {"query": "fixture"}}})
                self.assertEqual(response["error"]["message"], "MCP is disabled")
            self.assertEqual(server.handle({"jsonrpc": "2.0", "id": 7,
                "method": "tools/list"})["result"]["tools"], [])
            store.close()

    def test_writes_are_hidden_by_default_and_explicit_opt_in_only_changes_metadata(self):
        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            store.ingest("wechat", "account", [{"id": "room", "title": "Room"}],
                         [{"id": "message", "conversation_id": "room", "timestamp": 1, "text": "unchanged"}],
                         [{"id": "person", "name": "Person"}])
            read_only = MCPServer(store)
            names = [tool["name"] for tool in read_only.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]]
            self.assertEqual(len(names), 14)
            denied = read_only.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "update_conversation_metadata", "arguments": {"platform": "wechat", "account_id": "account", "conversation_id": "room", "note": "x"}}})
            self.assertTrue(denied["result"]["isError"])
            self.assertEqual(store.conversations("wechat")["items"][0]["note"], "")
            writable = MCPServer(store, allow_writes=True)
            tools = writable.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]
            self.assertEqual(len(tools), 17)
            self.assertFalse(next(tool for tool in tools if tool["name"] == "update_conversation_metadata")["annotations"]["readOnlyHint"])
            updated = writable.call_tool("update_conversation_metadata", {
                "platform": "wechat", "account_id": "account", "conversation_id": "room", "note": "local", "tags": ["keep"], "archived": True})
            self.assertEqual(updated["note"], "local")
            writable.call_tool("set_contact_identity", {
                "platform": "wechat", "account_id": "account", "contact_id": "person", "identity_id": "unified"})
            self.assertEqual(store.get_message("wechat", "account", "message")["text"], "unchanged")
            self.assertEqual([entry["action"] for entry in store.operation_log()], [
                "contact_identity.link", "conversation_metadata.update"])
            store.close()

    def test_stdio_rejects_malformed_json_without_logging_content(self):
        with tempfile.TemporaryDirectory() as storage:
            output = io.BytesIO()
            serve(io.BytesIO(b'{"jsonrpc":\n'), output, storage)
            response = json.loads(output.getvalue().decode("utf-8"))
            self.assertEqual(response["error"]["code"], -32700)
            self.assertEqual(response["error"]["message"], "Parse error")

    def test_stdio_loads_explicit_local_metadata_write_opt_in(self):
        with tempfile.TemporaryDirectory() as storage:
            Path(storage, "settings.json").write_text('{"mcp_write_enabled": true}', encoding="utf-8")
            requests = [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                        {"jsonrpc": "2.0", "method": "exit"}]
            output = io.BytesIO()
            serve(io.BytesIO(("\n".join(json.dumps(item) for item in requests) + "\n").encode()), output, storage)
            tools = json.loads(output.getvalue().splitlines()[0])["result"]["tools"]
            tool_names = {tool["name"] for tool in tools}
            self.assertEqual(len(tools), 26)
            self.assertTrue({"list_wechat_source_media", "get_wechat_source_media"} <= tool_names)

    def test_stdio_queries_coexist_with_concurrent_wal_writes(self):
        with tempfile.TemporaryDirectory() as storage:
            database = f"{storage}/hub.sqlite3"
            setup = ChatStore(database)
            setup.ingest("qq", "synthetic", [{"id": "room", "title": "Concurrent"}], [])
            setup.close()
            process = subprocess.Popen(
                [sys.executable, "-m", "hub.mcp_server", "--storage", storage],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", bufsize=1,
                cwd=os.path.dirname(os.path.dirname(__file__)),
            )

            def exchange(request_id, method, params=None):
                message = {"jsonrpc": "2.0", "id": request_id, "method": method}
                if params is not None:
                    message["params"] = params
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()
                return json.loads(process.stdout.readline())

            def write_batches():
                writer = ChatStore(database)
                try:
                    for batch in range(8):
                        writer.ingest("qq", "synthetic", [{"id": "room", "title": "Concurrent"}], [
                            {"id": f"concurrent-{batch}-{index}", "conversation_id": "room",
                             "timestamp": batch * 10 + index, "type": "text", "text": "shared wal"}
                            for index in range(10)
                        ])
                        time.sleep(0.005)
                finally:
                    writer.close()

            try:
                self.assertEqual(exchange(1, "initialize", {"protocolVersion": "2024-11-05"})["id"], 1)
                writer_thread = threading.Thread(target=write_batches)
                writer_thread.start()
                for request_id in range(2, 12):
                    response = exchange(request_id, "tools/call", {"name": "search_messages",
                        "arguments": {"query": "shared wal", "platform": "qq", "limit": 100}})
                    result = response["result"]
                    self.assertFalse(result["isError"], result)
                    rows = json.loads(result["content"][0]["text"])
                    self.assertLessEqual(len(rows["items"]), 80)
                writer_thread.join(timeout=5)
                self.assertFalse(writer_thread.is_alive())
                final = exchange(12, "tools/call", {"name": "search_messages",
                    "arguments": {"query": "shared wal", "platform": "qq", "limit": 100}})
                self.assertEqual(json.loads(final["result"]["content"][0]["text"])["total"], 80)
                process.stdin.write('{"jsonrpc":"2.0","method":"exit"}\n')
                process.stdin.flush()
                process.stdin.close()
                self.assertEqual(process.wait(timeout=5), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()

    def test_stdio_handshake_lists_tools_and_reads_shared_index(self):
        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            store.ingest("wechat", "synthetic", [{"id": "room", "title": "Test room"}], [{
                "id": "message-1", "conversation_id": "room", "timestamp": 1800000000,
                "sender_name": "Tester", "type": "text", "text": "hello MCP",
            }, {"id": "message-2", "conversation_id": "room", "timestamp": 1799999999,
                "sender_name": "Tester", "type": "text", "text": "hello MCP again"}])
            store.create_import_job({"id": "mcp-job-fixture", "fingerprint": "b" * 64,
                "filename": "fixture.json", "staging_file": "private.json", "created_at": 1,
                "total": 2, "valid": 1, "invalid": 1,
                "errors": [{"row": 2, "error": "bad synthetic row"}]})
            mcp = MCPServer(store)
            first_page = mcp.call_tool("search_messages", {"query": "hello MCP", "limit": 1})
            second_page = mcp.call_tool("search_messages", {
                "query": "hello MCP", "limit": 1, "cursor": first_page["next_cursor"]})
            self.assertNotEqual(first_page["items"][0]["id"], second_page["items"][0]["id"])
            with self.assertRaisesRegex(ValueError, "cursor"):
                mcp.call_tool("search_messages", {"query": "different", "limit": 1,
                                                     "cursor": first_page["next_cursor"]})
            store.close()
            requests = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search_messages", "arguments": {"query": "MCP", "platform": "wechat"}}},
                {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "list_imports", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "search_messages", "arguments": {"query": "MCP", "limit": 1000}}},
                {"jsonrpc": "2.0", "method": "exit"},
            ]
            process = subprocess.run(
                [sys.executable, "-m", "hub.mcp_server", "--storage", storage],
                input=("\n".join(json.dumps(item) for item in requests) + "\n").encode(),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
                cwd=os.path.dirname(os.path.dirname(__file__)),
            )
            self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace"))
            self.assertNotIn("MCP tool failure", process.stdout.decode(errors="replace"))
            self.assertNotIn("hello MCP", process.stderr.decode(errors="replace"))
            output = [json.loads(line) for line in process.stdout.splitlines()]
            self.assertEqual(output[0]["result"]["protocolVersion"], "2024-11-05")
            tools = output[1]["result"]["tools"]
            tool_names = {tool["name"] for tool in tools}
            self.assertEqual(len(tools), 23)
            self.assertTrue({"list_wechat_source_media", "get_wechat_source_media"} <= tool_names)

            gated_store = ChatStore(":memory:")
            media_disabled = MCPServer(
                gated_store, capabilities={"media": False},
                wechat_source_media_lister=lambda **filters: {"items": []},
                wechat_source_media_reader=lambda identity: (b"", "application/octet-stream"),
            )
            gated_names = {tool["name"] for tool in media_disabled.tools()}
            self.assertFalse({"list_wechat_source_media", "get_wechat_source_media"} & gated_names)
            for tool_name in ("list_wechat_source_media", "get_wechat_source_media"):
                with self.assertRaisesRegex(ValueError, "已在设置中关闭"):
                    media_disabled.call_tool(tool_name, {})
            gated_store.close()
            result = output[2]["result"]
            self.assertFalse(result["isError"])
            payload = json.loads(result["content"][0]["text"])
            self.assertEqual(payload["items"][0]["text"], "hello MCP")
            self.assertEqual(result["structuredContent"], payload)
            imports = json.loads(output[3]["result"]["content"][0]["text"])
            self.assertEqual(imports["items"][0]["filename"], "fixture.json")
            self.assertTrue(output[4]["result"]["isError"])
            logged = ChatStore(f"{storage}/hub.sqlite3")
            try:
                operations = logged.mcp_operations()
                self.assertEqual([item["tool"] for item in operations], ["search_messages", "list_imports", "search_messages"])
                self.assertEqual([item["status"] for item in operations], ["error", "success", "success"])
                self.assertNotIn("hello MCP", json.dumps(operations))
            finally:
                logged.close()

    def test_media_message_resources_and_prompts_are_structured_and_metadata_only(self):
        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            store.ingest("wechat", "acc/one", [{"id": "room", "title": "Photos", "avatar_url": "avatar://room"}], [{
                "id": "image/1", "conversation_id": "room", "timestamp": 1800000000,
                "sender_name": "Tester", "type": "image", "text": "caption",
                "media_url": "data:image/png;base64,c2Vuc2l0aXZl", "file_name": "photo.jpg",
                "width": 640, "height": 480, "image_data_base64": "c2Vuc2l0aXZl",
            }], [{"id": "person", "name": "Tester", "avatar_url": "avatar://person"}])
            mcp = MCPServer(store)
            listed = mcp.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {
                "name": "list_media", "arguments": {"platform": "wechat", "type": "image"}}})["result"]
            item = listed["structuredContent"]["items"][0]
            self.assertEqual(item["file_name"], "photo.jpg")
            self.assertEqual(item["extra"]["width"], 640)
            self.assertEqual(item["resource_uri"], "lmh://message/wechat/acc%2Fone/image%2F1")
            self.assertNotIn("bytes", item)
            self.assertEqual(item["media_url"], "[inline media omitted]")
            self.assertEqual(item["extra"]["image_data_base64"], "[binary data omitted]")
            self.assertEqual(listed["content"][1]["type"], "resource_link")
            initialized = mcp.handle({"jsonrpc": "2.0", "id": 8, "method": "initialize", "params": {}})["result"]
            self.assertIn("resources", initialized["capabilities"])
            self.assertIn("prompts", initialized["capabilities"])
            self.assertIn("personal WeChat", initialized["instructions"])
            self.assertIn("MCP cannot approve or deliver", initialized["instructions"])
            self.assertEqual(len(mcp.handle({"jsonrpc": "2.0", "id": 7, "method": "resources/list"})["result"]["resources"]), 1)
            self.assertEqual(len(mcp.handle({"jsonrpc": "2.0", "id": 6, "method": "prompts/list"})["result"]["prompts"]), 2)
            retrieved = mcp.handle({"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {
                "name": "get_message", "arguments": {"platform": "wechat", "account_id": "acc/one", "message_id": "image/1"}}})["result"]
            self.assertEqual(retrieved["structuredContent"]["media_url"], "[inline media omitted]")
            resource = mcp.read_resource(item["resource_uri"])[0]
            self.assertEqual(json.loads(resource["text"])["file_name"], "photo.jpg")
            source_resource = mcp.read_resource("lmh://sources")[0]
            self.assertEqual(json.loads(source_resource["text"])["items"][0]["platform"], "wechat")
            self.assertEqual(len(mcp.prompts()), 2)
            prompt = mcp.get_prompt("inspect_attachment", {
                "platform": "wechat", "account_id": "acc/one", "message_id": "image/1"})
            self.assertIn("call get_attachment", prompt["messages"][0]["content"]["text"])
            self.assertIn("list_media", prompt["messages"][0]["content"]["text"])
            self.assertIn("resource_index", prompt["messages"][0]["content"]["text"])
            self.assertIn("avatar://room", mcp.call_tool("list_conversations", {"platform": "wechat"})["items"][0]["avatar_url"])
            self.assertIn("avatar://person", mcp.call_tool("list_contacts", {"platform": "wechat"})["items"][0]["avatar_url"])
            self.assertEqual(mcp.call_tool("list_contacts", {
                "platform": "wechat", "account_id": "acc/one"})["total"], 1)
            with self.assertRaisesRegex(ValueError, "Missing required"):
                mcp.call_tool("get_message", {"platform": "wechat", "account_id": "acc/one"})
            with self.assertRaises(ValueError):
                mcp.read_resource("lmh://message/wechat/acc%2Fone/missing")
            with self.assertRaises(ValueError):
                mcp.get_prompt("cross_platform_search", {"query": "x", "ignored": "y"})
            store.close()

    def test_get_attachment_returns_only_the_explicitly_requested_local_image_block(self):
        import base64

        png = b"\x89PNG\r\n\x1a\n" + b"synthetic-image"
        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            store.ingest("qq", "account", [{"id": "room", "title": "Photos"}], [{
                "id": "image", "conversation_id": "room", "timestamp": 1800000000,
                "type": "image", "file_name": "photo.png", "text": "caption",
                "extra": {"width": 12, "height": 10},
            }])
            store.ingest("wechat", "wx-account", [{"id": "room", "title": "Stickers"}], [{
                "id": "sticker", "conversation_id": "room", "timestamp": 1800000000,
                "type": "sticker", "raw_type": "47", "emoji_md5": "a" * 32,
            }])
            requested = []
            mcp = MCPServer(store, media_reader=lambda message, index: (
                requested.append((message["platform"], message["type"], index)) or
                (png, "image/png")))
            result = mcp.handle({"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {
                "name": "get_attachment", "arguments": {"platform": "qq", "account_id": "account", "message_id": "image"}}})["result"]
            self.assertFalse(result["isError"])
            self.assertEqual(result["content"][1], {"type": "image", "data": base64.b64encode(png).decode("ascii"), "mimeType": "image/png"})
            self.assertEqual(result["structuredContent"]["file_name"], "photo.png")
            self.assertNotIn("message", result["structuredContent"])
            sticker = mcp.handle({"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {
                "name": "get_attachment", "arguments": {
                    "platform": "wechat", "account_id": "wx-account", "message_id": "sticker"}}})["result"]
            self.assertFalse(sticker["isError"])
            self.assertEqual(sticker["content"][1], {
                "type": "image", "data": base64.b64encode(png).decode("ascii"),
                "mimeType": "image/png"})
            self.assertEqual(requested, [("qq", "image", None), ("wechat", "image", None)])
            self.assertIn("get_attachment", {tool["name"] for tool in mcp.tools()})
            store.close()

    def test_qq_forward_attachment_lists_then_returns_only_the_selected_media(self):
        import base64

        png = b"\x89PNG\r\n\x1a\n" + b"synthetic-forward-image"
        requested = []
        listing = {"items": [{"resource_index": 0, "resource_handle": "opaque_handle_1234567890",
                               "type": "image", "file_name": "forward.png", "size": len(png),
                               "mime_type": "image/png"}], "truncated": False}
        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            reader = lambda account, conversation, message, handle: (
                requested.append((account, conversation, message, handle)) or
                (png, "image/png", {"file_name": "forward.png"}))
            lister = lambda account, conversation, message: listing
            mcp = MCPServer(store, qq_forward_reader=reader, qq_forward_lister=lister)
            list_result = mcp.handle({"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {
                "name": "list_qq_forward_attachments", "arguments": {
                    "account_id": "account", "conversation_id": "room", "message_id": "room:parent"}}})["result"]
            self.assertFalse(list_result["isError"])
            self.assertEqual(list_result["structuredContent"], listing)
            request = {"jsonrpc": "2.0", "id": 14, "method": "tools/call", "params": {
                "name": "get_qq_forward_attachment", "arguments": {
                    "account_id": "account", "conversation_id": "room",
                    "message_id": "room:parent", "resource_handle": "opaque_handle_1234567890"}}}
            result = mcp.handle(request)["result"]
            self.assertFalse(result["isError"])
            self.assertEqual(result["content"][1], {
                "type": "image", "data": base64.b64encode(png).decode("ascii"), "mimeType": "image/png"})
            self.assertEqual(requested, [("account", "room", "room:parent", "opaque_handle_1234567890")])
            self.assertNotIn("payload", result["structuredContent"])
            tools = {tool["name"] for tool in mcp.tools()}
            self.assertIn("get_qq_forward_attachment", tools)
            self.assertIn("list_qq_forward_attachments", tools)
            disabled = MCPServer(store, qq_forward_reader=reader, qq_forward_lister=lister,
                                 capabilities={"media": False})
            self.assertNotIn("get_qq_forward_attachment", {tool["name"] for tool in disabled.tools()})
            store.close()

    def test_get_attachment_returns_cached_audio_as_mcp_audio_content(self):
        import base64

        wav = b"RIFF\x00\x00\x00\x00WAVE" + b"synthetic-audio"
        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            store.ingest("wechat", "account", [{"id": "room", "title": "Voice"}], [{
                "id": "voice", "conversation_id": "room", "timestamp": 1800000000,
                "type": "voice", "file_name": "voice.wav",
            }])
            mcp = MCPServer(store, media_reader=lambda message, index: (wav, "audio/wav"))
            result = mcp.handle({"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {
                "name": "get_attachment", "arguments": {"platform": "wechat", "account_id": "account", "message_id": "voice"}}})["result"]
            self.assertFalse(result["isError"])
            self.assertEqual(result["content"][1], {
                "type": "audio", "data": base64.b64encode(wav).decode("ascii"), "mimeType": "audio/wav"})
            store.close()

    def test_get_attachment_validates_wecom_indices_and_returns_files(self):
        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            store.ingest("wecom", "local", [{"id": "room", "title": "Photos"}], [{
                "id": "image", "conversation_id": "room", "type": "image",
                "file_name": "first.png", "attachments": [
                    {"name": "first.png", "type": "image"},
                    {"name": "second.png", "type": "图片"},
                    {"name": "report.pdf", "type": "file"},
                ],
            }])
            requested = []
            mcp = MCPServer(store, media_reader=lambda message, index: (
                requested.append(index) or b"%PDF-1.7 synthetic", "application/pdf"))
            try:
                result = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                    "name": "get_attachment", "arguments": {
                        "platform": "wecom", "account_id": "local", "message_id": "image",
                        "resource_index": 1,
                    }}})["result"]
                self.assertFalse(result["isError"])
                self.assertEqual(result["structuredContent"]["resource_index"], 1)
                self.assertEqual(requested, [1])
                result = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                    "name": "get_attachment", "arguments": {
                        "platform": "wecom", "account_id": "local", "message_id": "image",
                        "resource_index": 2,
                    }}})["result"]
                self.assertFalse(result["isError"])
                self.assertEqual(result["structuredContent"]["resource_index"], 2)
                self.assertEqual(result["content"][1]["type"], "resource")
                self.assertEqual(requested, [1, 2])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
