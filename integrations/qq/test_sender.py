import json
from http.client import IncompleteRead
import threading
import time
import tempfile
import unittest
from urllib.error import URLError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from integrations.qq import sender as sender_module
from integrations.qq.sender import (NapCatOneBotSender, QQAccountBoundSender, QQSendError,
                                    QQDeliveryUnknown, ensure_local_http_server)
from hub.sending import SendRepository, SendService


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.payload


class TrailingWhitespaceResponse(FakeResponse):
    def __init__(self, payload):
        super().__init__(payload)
        self.payload += b" " * (sender_module._MAX_SEND_RESPONSE_BYTES + 1)

    def read(self, limit):
        return self.payload[:limit]


class NapCatSenderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "onebot11.json"
        self.path.write_text(json.dumps({"network": {"httpServers": []}}), encoding="utf-8")
        self.endpoint = ensure_local_http_server(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def run_loopback_request(self, response_status=200, response_body=None, delay=0):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(inner_self):
                length = int(inner_self.headers.get("Content-Length", "0"))
                received.append({
                    "path": inner_self.path,
                    "authorization": inner_self.headers.get("Authorization"),
                    "content_type": inner_self.headers.get("Content-Type"),
                    "body": json.loads(inner_self.rfile.read(length)),
                })
                if delay:
                    time.sleep(delay)
                inner_self.send_response(response_status)
                inner_self.end_headers()
                try:
                    inner_self.wfile.write(json.dumps(response_body or {
                        "status": "ok", "retcode": 0, "data": {"message_id": 42}
                    }).encode("utf-8"))
                except OSError:
                    pass

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        config = json.loads(self.path.read_text(encoding="utf-8"))
        config["network"]["httpServers"][0]["port"] = server.server_address[1]
        self.path.write_text(json.dumps(config), encoding="utf-8")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, received

    def test_config_is_loopback_authenticated_and_idempotent(self):
        config = json.loads(self.path.read_text())
        server = config["network"]["httpServers"][0]
        self.assertEqual(server["host"], "127.0.0.1")
        self.assertFalse(server["enableCors"])
        self.assertGreaterEqual(len(server["token"]), 32)
        self.assertEqual(ensure_local_http_server(self.path), self.endpoint)
        self.assertEqual(len(json.loads(self.path.read_text())["network"]["httpServers"]), 1)

    def test_config_replaces_boolean_port(self):
        config = json.loads(self.path.read_text())
        config["network"]["httpServers"][0]["port"] = True
        self.path.write_text(json.dumps(config), encoding="utf-8")

        endpoint = ensure_local_http_server(self.path)

        self.assertIs(type(endpoint["port"]), int)
        stored = json.loads(self.path.read_text())["network"]["httpServers"][0]["port"]
        self.assertIs(type(stored), int)

    def test_endpoint_rejects_boolean_port(self):
        config = json.loads(self.path.read_text())
        config["network"]["httpServers"][0]["port"] = True
        self.path.write_text(json.dumps(config), encoding="utf-8")

        with self.assertRaisesRegex(QQSendError, "onebot_endpoint_invalid"):
            NapCatOneBotSender(self.path)._endpoint()

    def test_readiness_reads_only_local_login_identity(self):
        captured = {}

        def opener(request, timeout):
            captured.update(method=request.get_method(), url=request.full_url,
                            body=request.data, authorization=request.get_header("Authorization"))
            return FakeResponse({"status": "ok", "retcode": 0,
                                 "data": {"user_id": 12345678, "nickname": "Synthetic QQ"}})

        result = NapCatOneBotSender(self.path, opener=opener).readiness()
        self.assertEqual(result["state"], "online")
        self.assertEqual(result["account_id"], "12345678")
        self.assertEqual(result["nickname"], "Synthetic QQ")
        self.assertTrue(result["endpoint_reachable"])
        self.assertEqual(captured["method"], "POST")
        self.assertTrue(captured["url"].startswith("http://127.0.0.1:"))
        self.assertTrue(captured["url"].endswith("/get_login_info"))
        self.assertEqual(captured["body"], b"{}")
        self.assertTrue(captured["authorization"].startswith("Bearer "))

    def test_readiness_rejects_oversized_response_with_valid_json_prefix(self):
        def opener(*_args, **_kwargs):
            return TrailingWhitespaceResponse({"status": "ok", "retcode": 0,
                "data": {"user_id": 12345678, "nickname": "Synthetic QQ"}})

        result = NapCatOneBotSender(self.path, opener=opener).readiness()

        self.assertEqual(result["state"], "invalid_response")
        self.assertTrue(result["endpoint_reachable"])
        self.assertIsNone(result["account_id"])
        self.assertIsNone(result["account_online"])

    def test_readiness_rejects_non_integer_success_retcode(self):
        for retcode in (False, 0.0):
            with self.subTest(retcode=retcode):
                def opener(*_args, **_kwargs):
                    return FakeResponse({"status": "ok", "retcode": retcode,
                                         "data": {"user_id": 12345678}})

                result = NapCatOneBotSender(self.path, opener=opener).readiness()

                self.assertEqual(result["state"], "api_unavailable")
                self.assertIsNone(result["account_id"])
                self.assertIsNone(result["account_online"])

    def test_readiness_when_capability_disabled_does_not_contact_endpoint(self):
        calls = []
        result = NapCatOneBotSender(self.path, opener=lambda *_a, **_k: calls.append(True)).readiness(False)
        self.assertEqual(result["state"], "capability_disabled")
        self.assertFalse(result["capability_enabled"])
        self.assertEqual(calls, [])

    def test_readiness_distinguishes_unreachable_local_endpoint(self):
        result = NapCatOneBotSender(self.path, opener=lambda *_a, **_k: (_ for _ in ()).throw(
            URLError("synthetic"))).readiness()
        self.assertEqual(result["state"], "unreachable")
        self.assertTrue(result["endpoint_configured"])
        self.assertFalse(result["endpoint_reachable"])
        self.assertIsNone(result["account_online"])

    def test_atomic_config_write_failure_preserves_previous_config(self):
        original = self.path.read_text(encoding="utf-8")
        with patch.object(sender_module.os, "replace", side_effect=OSError("synthetic failure")):
            with self.assertRaisesRegex(QQSendError, "onebot_config_write_failed"):
                ensure_local_http_server(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_private_text_send_uses_explicit_target_and_auth_header(self):
        captured = {}

        def opener(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse({"status": "ok", "retcode": 0, "data": {"message_id": 17}})

        result = NapCatOneBotSender(self.path, opener=opener).deliver({
            "conversation_type": "private", "conversation_id": "12345678", "text": "hello"})
        self.assertEqual(result["message_id"], "17")
        request = captured["request"]
        self.assertEqual(request.full_url, f"http://127.0.0.1:{self.endpoint['port']}/send_private_msg")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + self.endpoint["token"])
        self.assertEqual(json.loads(request.data), {"user_id": 12345678,
                         "message": [{"type": "text", "data": {"text": "hello"}}]})

    def test_malformed_message_id_receipts_remain_unknown(self):
        for message_id in (True, "17", 17.0, {}, 2 ** 31, -(2 ** 31) - 1):
            with self.subTest(message_id=message_id):
                def opener(*_args, **_kwargs):
                    return FakeResponse({"status": "ok", "retcode": 0,
                                         "data": {"message_id": message_id}})

                with self.assertRaises(QQDeliveryUnknown) as result:
                    NapCatOneBotSender(self.path, opener=opener).deliver({
                        "conversation_type": "private", "conversation_id": "12345678", "text": "hello"})
                self.assertEqual(result.exception.code, "onebot_invalid_receipt")

    def test_malformed_retcode_receipts_remain_unknown(self):
        for status, retcode in (("ok", False), ("ok", 0.0), ("failed", True),
                                ("failed", 1.0)):
            with self.subTest(status=status, retcode=retcode):
                def opener(*_args, **_kwargs):
                    return FakeResponse({"status": status, "retcode": retcode,
                                         "data": {"message_id": 17}})

                with self.assertRaises(QQDeliveryUnknown) as result:
                    NapCatOneBotSender(self.path, opener=opener).deliver({
                        "conversation_type": "private", "conversation_id": "12345678",
                        "text": "hello"})
                self.assertEqual(result.exception.code, "onebot_invalid_receipt")

    def test_loopback_http_transport_sends_only_authenticated_bounded_text_contract(self):
        server, thread, received = self.run_loopback_request()
        try:
            result = NapCatOneBotSender(self.path).deliver({
                "conversation_type": "group", "conversation_id": "12345678", "text": "hello"})
            self.assertEqual(result["message_id"], "42")
            self.assertEqual(received, [{
                "path": "/send_group_msg",
                "authorization": "Bearer " + self.endpoint["token"],
                "content_type": "application/json",
                "body": {"group_id": 12345678,
                         "message": [{"type": "text", "data": {"text": "hello"}}]},
            }])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_loopback_http_401_and_timeout_do_not_retry(self):
        rejected, rejected_thread, rejected_requests = self.run_loopback_request(response_status=401)
        try:
            sender = NapCatOneBotSender(self.path)
            with self.assertRaises(QQSendError) as result:
                sender.deliver({"conversation_type": "private", "conversation_id": "12345678", "text": "x"})
            self.assertEqual(result.exception.code, "onebot_http_401")
            self.assertEqual(len(rejected_requests), 1)
        finally:
            rejected.shutdown()
            rejected.server_close()
            rejected_thread.join(timeout=2)

        delayed, delayed_thread, delayed_requests = self.run_loopback_request(delay=1.2)
        try:
            sender = NapCatOneBotSender(self.path, timeout=1)
            with self.assertRaises(TimeoutError):
                sender.deliver({"conversation_type": "private", "conversation_id": "12345678", "text": "x"})
            self.assertEqual(len(delayed_requests), 1)
        finally:
            delayed.shutdown()
            delayed.server_close()
            delayed_thread.join(timeout=2)

    def test_loopback_http_408_is_unknown_and_not_retried(self):
        server, thread, received = self.run_loopback_request(response_status=408)
        repository = SendRepository(Path(self.temp.name) / "hub.sqlite3")
        service = SendService(repository, NapCatOneBotSender(self.path))
        payload = {"platform": "qq", "account_id": "12345678", "conversation_type": "private",
                   "conversation_id": "87654321", "text": "synthetic", "request_id": "loopback-send-408"}
        try:
            request_id = service.prepare(payload)["request_id"]
            result = service.approve_and_deliver(request_id)
            again = service.approve_and_deliver(request_id)
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["error_code"], "onebot_http_408")
            self.assertEqual(again["status"], "unknown")
            self.assertEqual(len(received), 1)
        finally:
            repository.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_redirect_is_not_followed_and_delivery_remains_unknown(self):
        forwarded = []

        class TargetHandler(BaseHTTPRequestHandler):
            def do_POST(inner_self):
                forwarded.append(inner_self.headers.get("Authorization"))
                inner_self.send_response(200)
                inner_self.end_headers()

            def log_message(self, *_args):
                pass

        target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
        target.daemon_threads = True
        target_thread = threading.Thread(target=target.serve_forever, daemon=True)
        target_thread.start()

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(inner_self):
                inner_self.rfile.read(int(inner_self.headers.get("Content-Length", "0")))
                inner_self.send_response(307)
                inner_self.send_header("Location", f"http://127.0.0.1:{target.server_address[1]}/send")
                inner_self.end_headers()

            def log_message(self, *_args):
                pass

        redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        redirect.daemon_threads = True
        redirect_thread = threading.Thread(target=redirect.serve_forever, daemon=True)
        redirect_thread.start()
        config = json.loads(self.path.read_text(encoding="utf-8"))
        config["network"]["httpServers"][0]["port"] = redirect.server_address[1]
        self.path.write_text(json.dumps(config), encoding="utf-8")
        try:
            with self.assertRaises(QQDeliveryUnknown) as result:
                NapCatOneBotSender(self.path).deliver({
                    "conversation_type": "private", "conversation_id": "12345678", "text": "hello"})
            self.assertEqual(result.exception.code, "onebot_http_307")
            self.assertEqual(forwarded, [])
        finally:
            redirect.shutdown()
            redirect.server_close()
            redirect_thread.join(timeout=2)
            target.shutdown()
            target.server_close()
            target_thread.join(timeout=2)

    def test_success_without_receipt_stays_unknown_without_second_http_request(self):
        server, thread, received = self.run_loopback_request(response_body={
            "status": "ok", "retcode": 0, "data": {}})
        repository = SendRepository(Path(self.temp.name) / "hub.sqlite3")
        service = SendService(repository, NapCatOneBotSender(self.path))
        payload = {"platform": "qq", "account_id": "12345678", "conversation_type": "private",
                   "conversation_id": "87654321", "text": "synthetic", "request_id": "loopback-send-1"}
        try:
            prepared = service.prepare(payload)
            result = service.approve_and_deliver(prepared["request_id"])
            again = service.approve_and_deliver(prepared["request_id"])
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["error_code"], "onebot_receipt_missing")
            self.assertEqual(again["status"], "unknown")
            self.assertEqual(len(received), 1)
        finally:
            repository.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_incomplete_response_is_unknown_without_second_http_request(self):
        calls = []

        class InterruptedResponse(FakeResponse):
            def read(self, _limit):
                raise IncompleteRead(b'{"status":"ok"}')

        def opener(*_args, **_kwargs):
            calls.append(True)
            return InterruptedResponse({})

        repository = SendRepository(Path(self.temp.name) / "incomplete.sqlite3")
        service = SendService(repository, NapCatOneBotSender(self.path, opener=opener))
        payload = {"platform": "qq", "account_id": "12345678", "conversation_type": "private",
                   "conversation_id": "87654321", "text": "synthetic", "request_id": "loopback-send-2"}
        try:
            request_id = service.prepare(payload)["request_id"]
            result = service.approve_and_deliver(request_id)
            again = service.approve_and_deliver(request_id)
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["error_code"], "onebot_transport_unknown")
            self.assertEqual(again["status"], "unknown")
            self.assertEqual(len(calls), 1)
        finally:
            repository.close()

    def test_oversized_send_response_is_unknown_without_retry(self):
        calls = []

        def opener(*_args, **_kwargs):
            calls.append(True)
            return TrailingWhitespaceResponse({"status": "ok", "retcode": 0,
                                               "data": {"message_id": 42}})

        repository = SendRepository(Path(self.temp.name) / "oversized.sqlite3")
        service = SendService(repository, NapCatOneBotSender(self.path, opener=opener))
        payload = {"platform": "qq", "account_id": "12345678", "conversation_type": "private",
                   "conversation_id": "87654321", "text": "synthetic",
                   "request_id": "oversized-onebot-response"}
        try:
            request_id = service.prepare(payload)["request_id"]
            result = service.approve_and_deliver(request_id)
            again = service.approve_and_deliver(request_id)
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["error_code"], "onebot_response_too_large")
            self.assertEqual(again["status"], "unknown")
            self.assertEqual(len(calls), 1)
        finally:
            repository.close()

    def test_group_send_and_timeout_are_explicit(self):
        captured = {}

        def opener(request, timeout):
            captured["request"] = request
            raise TimeoutError()

        sender = NapCatOneBotSender(self.path, opener=opener)
        with self.assertRaises(TimeoutError):
            sender.deliver({"conversation_type": "group", "conversation_id": "12345678", "text": "hello"})
        self.assertTrue(captured["request"].full_url.endswith("/send_group_msg"))

    def test_rejects_non_loopback_endpoint_and_failed_receipt(self):
        config = json.loads(self.path.read_text())
        config["network"]["httpServers"][0]["host"] = "0.0.0.0"
        self.path.write_text(json.dumps(config), encoding="utf-8")
        sender = NapCatOneBotSender(self.path)
        with self.assertRaises(QQSendError):
            sender.deliver({"conversation_type": "private", "conversation_id": "12345678", "text": "hello"})

    def test_account_binding_rechecks_online_identity_before_delivery(self):
        delivered = []

        class Sender:
            def deliver(self, request):
                delivered.append(request)
                return {"message_id": "synthetic"}

        request = {"account_id": "12345678", "conversation_type": "private",
                   "conversation_id": "87654321", "text": "synthetic"}
        for status, code in (({"online": False, "account_id": "12345678"}, "qq_account_offline"),
                             ({"online": True, "account_id": "87654321"}, "qq_account_mismatch")):
            adapter = QQAccountBoundSender(Sender(), lambda status=status: status)
            with self.subTest(code=code), self.assertRaises(QQSendError) as result:
                adapter.deliver(request)
            self.assertEqual(result.exception.code, code)
        adapter = QQAccountBoundSender(Sender(), lambda: {"online": True, "account_id": "12345678"})
        self.assertEqual(adapter.deliver(request)["message_id"], "synthetic")
        self.assertEqual(len(delivered), 1)

        config = json.loads(self.path.read_text(encoding="utf-8"))
        config["network"]["httpServers"][0]["host"] = "127.0.0.1"
        self.path.write_text(json.dumps(config), encoding="utf-8")
        sender = NapCatOneBotSender(self.path, opener=lambda *_a, **_k: FakeResponse(
            {"status": "failed", "retcode": 1, "data": {}}))
        with self.assertRaises(QQSendError) as rejected:
            sender.deliver({"conversation_type": "private", "conversation_id": "12345678", "text": "hello"})
        self.assertNotIsInstance(rejected.exception, QQDeliveryUnknown)


if __name__ == "__main__":
    unittest.main()
