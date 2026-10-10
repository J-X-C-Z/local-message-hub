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

from hub.store import ChatStore
from hub.mcp_server import MCPServer, serve


class MCPServerTest(unittest.TestCase):
    def test_writes_are_hidden_by_default_and_explicit_opt_in_only_changes_metadata(self):
        with tempfile.TemporaryDirectory() as storage:
            store = ChatStore(f"{storage}/hub.sqlite3")
            store.ingest("wechat", "account", [{"id": "room", "title": "Room"}],
                         [{"id": "message", "conversation_id": "room", "timestamp": 1, "text": "unchanged"}],
                         [{"id": "person", "name": "Person"}])
            read_only = MCPServer(store)
            names = [tool["name"] for tool in read_only.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]]
            self.assertEqual(len(names), 7)
            denied = read_only.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "update_conversation_metadata", "arguments": {"platform": "wechat", "account_id": "account", "conversation_id": "room", "note": "x"}}})
            self.assertTrue(denied["result"]["isError"])
            self.assertEqual(store.conversations("wechat")["items"][0]["note"], "")
            writable = MCPServer(store, allow_writes=True)
            tools = writable.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]
            self.assertEqual(len(tools), 10)
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
            self.assertEqual(len(tools), 10)

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
            self.assertEqual(len(output[1]["result"]["tools"]), 7)
            result = output[2]["result"]
            self.assertFalse(result["isError"])
            payload = json.loads(result["content"][0]["text"])
            self.assertEqual(payload["items"][0]["text"], "hello MCP")
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


if __name__ == "__main__":
    unittest.main()
