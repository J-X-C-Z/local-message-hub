import json
import hashlib
import tempfile
import time
from types import SimpleNamespace
import unittest
from io import BytesIO
from urllib.error import HTTPError
from pathlib import Path
from unittest.mock import Mock, call, patch

from integrations.qq.provider import (
    QQProvider,
    QQProviderError,
    _safe_launch_error,
)


def _cached_download_fixture(cache_dir, payload):
    def download(_url, name, _expected_size=None):
        cache = Path(cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / ("fixture-" + Path(name).name)
        target.write_bytes(payload)
        return target
    return download


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self.payload if size < 0 else self.payload[:size]


class QQProviderTests(unittest.TestCase):
    def test_runtime_prepares_authenticated_loopback_sender_config(self):
        from integrations.qq.sender import NapCatOneBotSender

        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder)
            qq_exe = bundle / "QQ.exe"
            app = qq_exe.parent / "resources" / "app"
            config = bundle / "config"
            app.mkdir(parents=True)
            config.mkdir()
            (app / "package.json").write_text(
                json.dumps({"name": "fixture", "version": "1.0", "private": True}),
                encoding="utf-8",
            )
            (config / "webui.json").write_text("{}", encoding="utf-8")
            (config / "napcat.json").write_text("{}", encoding="utf-8")
            onebot_path = config / "onebot11.json"
            onebot_path.write_text(json.dumps({"network": {"httpServers": []}}),
                                   encoding="utf-8")
            provider = QQProvider(bundle_dir=bundle)
            with patch("integrations.qq.provider.sys.platform", "win32"):
                provider._prepare_runtime(qq_exe)

            endpoint = NapCatOneBotSender(onebot_path)._endpoint()
            self.assertEqual(endpoint["host"], "127.0.0.1")
            self.assertTrue(1 <= endpoint["port"] <= 65535)
            self.assertGreaterEqual(len(endpoint["token"]), 32)
            server = json.loads(onebot_path.read_text(encoding="utf-8"))["network"]["httpServers"][0]
            self.assertFalse(server["enableCors"])
            self.assertFalse(server["enableWebsocket"])
            self.assertTrue(server["enable"])

    def test_qce_json_response_is_bounded(self):
        provider = QQProvider()
        response = _Response({"data": "x" * 32})
        with patch("integrations.qq.provider._MAX_QCE_RESPONSE_BYTES", 16), \
                patch.object(provider, "_token", return_value="fixture"), \
                patch("integrations.qq.provider.urlopen", return_value=response):
            with self.assertRaisesRegex(QQProviderError, "response exceeds the size limit"):
                provider._request("GET", "/api/messages/fetch")
        self.assertEqual(response.read_sizes, [17])

    def test_custom_qce_binary_rejects_unapproved_hash_even_with_sidecar(self):
        payload = b"vendored-qce-build-fixture"
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder)
            binary = bundle / "qce-server.exe"
            binary.write_bytes(payload)
            provider = QQProvider(bundle_dir=bundle)
            (bundle / "qce-server.exe.sha256").write_text(hashlib.sha256(payload).hexdigest(), encoding="ascii")
            with self.assertRaisesRegex(QQProviderError, "not an approved"):
                provider._validate_qce_binary()

    def test_qce_binary_hashes_in_chunks_and_detects_changed_payload(self):
        payload = b"synthetic-qce-build" * 20000
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "qce-server.exe"
            binary.write_bytes(payload)
            provider = QQProvider(bundle_dir=folder)
            with patch("integrations.qq.provider.APPROVED_QCE_SHA256",
                       {hashlib.sha256(payload).hexdigest()}), \
                    patch.object(Path, "read_bytes", side_effect=AssertionError("whole file read")):
                provider._validate_qce_binary()
                with binary.open("ab") as stream:
                    stream.write(b"changed")
                with self.assertRaisesRegex(QQProviderError, "not an approved"):
                    provider._validate_qce_binary()

    def test_status_stays_available_for_napcat_login_when_qce_is_down(self):
        provider = QQProvider()
        with patch.object(provider, "_request", side_effect=QQProviderError("QCE unavailable")), \
                patch.object(provider, "login_status", return_value={
                    "loginPhase": "waiting_qrcode", "qr_url": "private-qr",
                    "token": "private-token",
                }):
            status = provider.status()

        self.assertEqual(status, {
            "provider": "qq", "available": True, "online": False,
            "authenticated": False, "mode": "napcat_waiting_login",
            "login_phase": "waiting_qrcode", "account_id": None,
        })
        self.assertNotIn("private-qr", json.dumps(status))
        self.assertNotIn("private-token", json.dumps(status))

    def test_status_is_unavailable_when_qce_and_napcat_are_down(self):
        provider = QQProvider()
        with patch.object(provider, "_request", side_effect=QQProviderError("QCE unavailable")), \
                patch.object(provider, "login_status",
                             side_effect=QQProviderError("NapCat unavailable")):
            status = provider.status()

        self.assertEqual(status, {
            "provider": "qq", "available": False, "online": False,
            "authenticated": False, "login_phase": None,
        })

    def test_forward_expansion_keeps_qce_locator_behind_opaque_handle(self):
        provider = QQProvider()
        row = {"platform": "qq", "account_id": "10001", "conversation_id": "peer",
               "id": "peer:parent", "timestamp": 1000,
               "extra": {"chatType": 1, "roaming_content": {"elements": [{"type": "forward"}]}}}
        raw = {"msgId": "parent", "peerUid": "peer", "elements": [{
            "multiForwardMsgElement": {"resId": "secret-res"}}]}
        with patch.object(provider, "status", return_value={"account_id": "10001"}), \
                patch.object(provider, "_request", return_value={"messages": [raw]}), \
                patch.object(provider, "_rpc", return_value={"msgList": [{
                    "msgId": "nested-secret", "chatType": 1, "peerUid": "peer",
                    "elements": [{"picElement": {"elementId": "element-secret",
                        "sourcePath": "private-source", "fileName": "photo.png", "fileSize": 8}}]}]}) as rpc:
            result = provider.expand_forward_resources(row, "10001", Path(tempfile.gettempdir()))
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(item["type"], "image")
        self.assertEqual(item["name"], "photo.png")
        self.assertEqual(rpc.call_args.args[0], "MsgService.getMultiMsg")

    def test_forward_download_writes_only_validated_media_inside_hub_cache(self):
        provider = QQProvider()
        handle = "opaque_handle"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider._forward_handles[handle] = {
                "expires": time.monotonic() + 60, "account_id": "10001", "peer": "peer",
                "parent_id": "parent", "msg_id": "nested", "chat_type": 1,
                "element_id": "element", "source_path": "opaque", "kind": "image", "size": 8,
            }
            with self.assertRaises(PermissionError):
                provider.download_forward_resource(handle, root, account_id="10002")
            with self.assertRaises(ValueError):
                provider.download_forward_resource(handle, root, max_bytes=4)
            def rpc(method, params):
                self.assertEqual(method, "FileApi.downloadMedia")
                self.assertEqual(params[2:4], ["peer", "element"])
                Path(params[4]).write_bytes(b"\x89PNG\r\n\x1a\n")
                return "ignored"
            with patch.object(provider, "status", return_value={"account_id": "10001"}), \
                    patch.object(provider, "_rpc", side_effect=rpc):
                path, mime = provider.download_forward_resource(
                    handle, root, account_id="10001", peer="peer", parent_id="parent", max_bytes=8)
            self.assertTrue(path.is_relative_to(root.resolve()))
            self.assertEqual(mime, "image/png")

    def test_messages_rejects_non_integer_pagination_before_request(self):
        provider = QQProvider()
        request = Mock()
        with patch.object(provider, "_request", request):
            for limit, offset in ((True, 0), (1.5, 0), (1, True), (1, 0.5)):
                with self.subTest(limit=limit, offset=offset):
                    with self.assertRaises(ValueError):
                        provider.messages("peer", limit=limit, offset=offset)
        request.assert_not_called()

    def test_message_pagination_uses_requested_page_and_offset(self):
        provider = QQProvider()
        calls = []

        def request(method, path, body=None, **kwargs):
            calls.append((path, body, kwargs))
            if path.startswith("/api/messages/fetch"):
                page = body["page"]
                return {"messages": [
                    {"msgId": f"p{page}-a", "msgTime": 100, "senderUid": "a", "elements": []},
                    {"msgId": f"p{page}-b", "msgTime": 101, "senderUid": "b", "elements": []},
                ]}
            return {"napcat": {"selfInfo": {"uid": "self"}}}

        with patch.object(provider, "_request", side_effect=request):
            rows = provider.messages({"id": "peer", "chatType": 2}, limit=2, offset=3)

        fetch = calls[0][1]
        self.assertEqual(fetch["page"], 2)
        self.assertEqual(fetch["peer"], {"chatType": 2, "peerUid": "peer"})
        self.assertEqual(calls[0][2], {"request_timeout": 30.0})
        self.assertEqual(calls[-1][2], {})
        self.assertEqual([row["id"] for row in rows], ["peer:p2-b", "peer:p3-a"])
        self.assertEqual(rows[0]["timestamp"], 101)

    def test_messages_retries_same_page_once_after_transient_read_failure(self):
        provider = QQProvider()
        calls = []

        def request(method, path, body=None, **kwargs):
            calls.append((method, path, body, kwargs))
            if path == "/api/messages/fetch":
                if sum(item[1] == path for item in calls) == 1:
                    raise QQProviderError("QCE service is unavailable: TimeoutError")
                return {"messages": [{"msgId": "retry-ok", "msgTime": 100}]}
            return {"napcat": {"selfInfo": {}}}

        with patch.object(provider, "_request", side_effect=request), \
                patch("integrations.qq.provider.time.sleep") as sleep:
            rows = provider.messages("peer", limit=1)

        fetches = [item for item in calls if item[1] == "/api/messages/fetch"]
        self.assertEqual(len(fetches), 2)
        self.assertEqual(fetches[0][2:], fetches[1][2:])
        sleep.assert_called_once_with(0.1)
        self.assertEqual([row["id"] for row in rows], ["peer:retry-ok"])

    def test_messages_retries_http_5xx_but_not_auth_or_client_errors(self):
        provider = QQProvider()
        for error, expected_calls in (
            ("QCE HTTP request failed (503)", 2),
            ("QCE rejected the local access token", 1),
            ("QCE HTTP request failed (404)", 1),
            ("QCE service is unavailable: JSONDecodeError", 1),
            ("QCE request failed (502)", 1),
        ):
            with self.subTest(error=error):
                request = Mock(side_effect=QQProviderError(error))
                with patch.object(provider, "_request", request), \
                        patch("integrations.qq.provider.time.sleep") as sleep:
                    with self.assertRaises(QQProviderError):
                        provider.messages("peer", limit=1)
                self.assertEqual(request.call_count, expected_calls)
                if expected_calls == 2:
                    self.assertEqual(request.call_args_list[0], request.call_args_list[1])
                    sleep.assert_called_once_with(0.1)
                else:
                    sleep.assert_not_called()

    def test_large_message_page_bounds_qce_prefetch_and_underlying_batch(self):
        provider = QQProvider()
        calls = []

        def request(method, path, body=None, **_kwargs):
            calls.append((path, body))
            if path == "/api/messages/fetch":
                page = body["page"]
                return {"messages": [{"msgId": f"message-{page}-{index}",
                                       "msgTime": page * 100 + index}
                                      for index in range(100)], "hasNext": True}
            return {"napcat": {"selfInfo": {}}}

        with patch.object(provider, "_request", side_effect=request):
            rows = provider.messages("peer", limit=500)

        fetches = [body for path, body in calls if path == "/api/messages/fetch"]
        self.assertEqual(len(rows), 500)
        self.assertEqual(fetches[0]["limit"], 100)
        self.assertEqual(fetches[0]["batchSize"], 100)
        self.assertEqual(fetches[-1]["page"], 5)

    def test_message_pagination_follows_has_next_for_short_page(self):
        provider = QQProvider()
        pages = []

        def request(method, path, body=None, **_kwargs):
            if path.startswith("/api/messages/fetch"):
                pages.append(body["page"])
                if body["page"] == 1:
                    return {"messages": [{"msgId": "first", "msgTime": 100}], "hasNext": True}
                return {"messages": [{"msgId": "second", "msgTime": 101}], "hasNext": False}
            return {"napcat": {"selfInfo": {}}}

        with patch.object(provider, "_request", side_effect=request):
            rows = provider.messages({"id": "peer", "chatType": 1}, limit=2)

        self.assertEqual(pages, [1, 2])
        self.assertEqual([row["id"] for row in rows], ["peer:first", "peer:second"])

    def test_message_pagination_stops_on_invalid_batch(self):
        provider = QQProvider()
        pages = []

        def request(method, path, body=None, **_kwargs):
            if path == "/api/messages/fetch":
                pages.append(body["page"])
                return {"messages": {"unexpected": "shape"}, "hasNext": True}
            return {"napcat": {"selfInfo": {}}}

        with patch.object(provider, "_request", side_effect=request):
            rows = provider.messages("peer", limit=200)

        self.assertEqual(pages, [1])
        self.assertEqual(rows, [])

    def test_message_pagination_stops_when_qce_repeats_the_same_page(self):
        provider = QQProvider()
        pages = []
        repeated_page = [{"msgId": "same-1", "msgTime": 100},
                         {"msgId": "same-2", "msgTime": 101}]

        def request(method, path, body=None, **_kwargs):
            if path.startswith("/api/messages/fetch"):
                pages.append(body["page"])
                return {"messages": repeated_page, "hasNext": True}
            return {"napcat": {"selfInfo": {}}}

        with patch.object(provider, "_request", side_effect=request):
            rows = provider.messages("peer", limit=4)

        self.assertEqual(pages, [1, 2])
        self.assertEqual([row["id"] for row in rows], ["peer:same-1", "peer:same-2"])

    def test_messages_routes_normalized_group_conversation_as_group(self):
        provider = QQProvider()
        calls = []

        def request(method, path, body=None, **_kwargs):
            calls.append((method, path, body))
            if path == "/api/messages/fetch":
                return {"messages": [{"msgId": "group-message", "elements": []}]}
            return {"napcat": {"selfInfo": {}}}

        with patch.object(provider, "_request", side_effect=request):
            rows = provider.messages({"id": "group-peer", "kind": "group"}, limit=1)

        self.assertEqual(calls[0][2]["peer"], {"chatType": 2, "peerUid": "group-peer"})
        self.assertEqual(rows[0]["chatType"], 2)

    def test_group_members_uses_bundled_endpoint_and_returns_filtered_bounded_roster(self):
        provider = QQProvider()
        calls = []

        def request(method, path, body=None, **_kwargs):
            calls.append((method, path, body))
            return {"value": [
                {"uid": "member-b", "uin": "200", "nick": "Beta", "role": "admin"},
                {"uid": "member-a", "uin": "100", "nick": "Alpha", "role": "owner",
                 "avatarUrl": "https://avatar.invalid/synthetic"},
                {"uid": "member-a", "nick": "Duplicate"},
            ]}

        with patch.object(provider, "_request", side_effect=request):
            result = provider.members("group-123", account_id="local", limit=1, query="alp")

        self.assertEqual(calls, [("GET", "/api/groups/group-123/members", None)])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"], [{
            "id": "member-a", "name": "Alpha", "avatar_url": "https://avatar.invalid/synthetic",
            "role": "owner", "remark": "",
        }])

    def test_group_members_validates_account_before_reading_roster(self):
        provider = QQProvider()
        request = Mock()
        request.side_effect = lambda method, path, body=None, **_kwargs: (
            {"napcat": {"selfInfo": {"uid": "active-uid", "uin": "123"}}}
            if path == "/api/system/info" else {"value": []}
        )
        with patch.object(provider, "_request", request):
            with self.assertRaises(ValueError):
                provider.members("group-123", account_id="other-uid")

        self.assertEqual(request.call_args_list[0].args[:2], ("GET", "/api/system/info"))
        self.assertEqual(request.call_count, 1)

    def test_group_members_rejects_bad_group_code_without_qce_request(self):
        provider = QQProvider()
        with patch.object(provider, "_request") as request:
            with self.assertRaises(ValueError):
                provider.members("../other")
        request.assert_not_called()

    def test_roaming_export_uses_bounded_private_chat_route_and_downloads_resources(self):
        provider = QQProvider()
        calls = []
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(provider, "_request", side_effect=lambda method, path, body=None, **kwargs: (
                 calls.append((method, path, body)) or {"taskId": "roam-fixture"})):
            result = provider.start_roaming_export(
                {"id": "peer-uid", "kind": "private", "title": "Peer"},
                100, 200, folder, max_messages=123, max_sequence_queries=456)

        self.assertEqual(result["taskId"], "roam-fixture")
        method, path, body = calls[0]
        self.assertEqual((method, path), ("POST", "/api/messages/roaming/export"))
        self.assertEqual(body["peer"], {"chatType": 1, "peerUid": "peer-uid", "guildId": ""})
        self.assertEqual(body["filter"], {"startTime": 100, "endTime": 200})
        self.assertEqual(body["roaming"], {"maxMessages": 123, "maxSequenceQueries": 456})
        self.assertTrue(body["options"]["includeResourceLinks"])
        self.assertNotIn("skipDownloadResourceTypes", body["options"])

    def test_roaming_export_rejects_group_and_unbounded_peer(self):
        provider = QQProvider()
        with patch.object(provider, "_request") as request:
            with self.assertRaises(ValueError):
                provider.start_roaming_export({"id": "group", "kind": "group"}, 1, 2, ".")
            with self.assertRaises(ValueError):
                provider.start_roaming_export({"id": "123456", "kind": "private"}, 1, 2, ".")
        request.assert_not_called()

    def test_roaming_export_rejects_boolean_and_fractional_bounds_without_request(self):
        provider = QQProvider()
        invalid = [
            ((True, 200), {}),
            ((100, False), {}),
            ((100.5, 200), {}),
            ((100, 200.5), {}),
            ((100, 200), {"max_messages": True}),
            ((100, 200), {"max_sequence_queries": 1.5}),
        ]
        with patch.object(provider, "_request") as request:
            for bounds, options in invalid:
                with self.subTest(bounds=bounds, options=options):
                    with self.assertRaises(ValueError):
                        provider.start_roaming_export(
                            {"id": "peer-uid", "kind": "private"},
                            bounds[0], bounds[1], ".", **options)
        request.assert_not_called()

    def test_roaming_task_ids_are_validated_before_requests(self):
        provider = QQProvider()
        with patch.object(provider, "_request") as request:
            with self.assertRaises(ValueError):
                provider.cancel_roaming_task("../other")
        request.assert_not_called()

    def test_napcat_credential_is_cached_and_never_put_in_url(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder)
            config = bundle / "config"
            config.mkdir()
            (config / "webui.json").write_text(json.dumps({
                "host": "127.0.0.1", "port": 6099, "token": "fixture-token",
            }))
            provider = QQProvider(bundle_dir=bundle)
            calls = []

            def open_request(request, timeout):
                calls.append(request)
                if request.full_url.endswith("/api/auth/login"):
                    return _Response({"code": 0, "data": {"Credential": "fixture-credential"}})
                return _Response({"code": 0, "data": {"loginPhase": "waiting_qrcode"}})

            with patch("integrations.qq.provider.urlopen", side_effect=open_request):
                first = provider._napcat_request("QQLogin/CheckLoginStatus")
                second = provider._napcat_request("QQLogin/CheckLoginStatus")

            self.assertEqual(first, second)
            self.assertEqual(sum(req.full_url.endswith("/api/auth/login") for req in calls), 1)
            self.assertTrue(all("fixture-credential" not in req.full_url for req in calls))
            self.assertEqual(calls[1].headers.get("Authorization"), "Bearer fixture-credential")

    def test_napcat_401_invalidates_cached_credential_once(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder)
            config = bundle / "config"
            config.mkdir()
            (config / "webui.json").write_text(json.dumps({
                "host": "127.0.0.1", "port": 6099, "token": "fixture-token-401",
            }))
            provider = QQProvider(bundle_dir=bundle)
            counts = {"login": 0, "api": 0}

            def open_request(request, timeout):
                if request.full_url.endswith("/api/auth/login"):
                    counts["login"] += 1
                    return _Response({"code": 0, "data": {
                        "Credential": f"credential-{counts['login']}"}})
                counts["api"] += 1
                if counts["api"] == 1:
                    raise HTTPError(request.full_url, 401, "expired", None, BytesIO())
                return _Response({"code": 0, "data": {"loginPhase": "waiting_qrcode"}})

            with patch("integrations.qq.provider.urlopen", side_effect=open_request):
                result = provider._napcat_request("QQLogin/CheckLoginStatus")

            self.assertEqual(result["loginPhase"], "waiting_qrcode")
            self.assertEqual(counts, {"login": 2, "api": 2})

    def test_launch_returns_napcat_qr_without_waiting_for_qce(self):
        provider = QQProvider()
        expected = {"started": True, "qr_url": "fixture-only", "login_phase": "waiting_qrcode",
                    "message": "Scan the QQ QR code in the app to connect."}
        with patch("integrations.qq.provider.sys.platform", "win32"), \
             patch.object(provider, "_launch_result", return_value=expected), \
             patch.object(provider, "_service_ready", side_effect=AssertionError("QCE must not gate QR")):
            self.assertEqual(provider.launch(), expected)

    def test_launch_refresh_failure_reuses_existing_qr_before_starting_qq(self):
        provider = QQProvider()
        expected = {"started": True, "qr_url": "fixture-only", "login_phase": "waiting_qrcode"}
        with patch("integrations.qq.provider.sys.platform", "win32"), \
             patch.object(provider, "_launch_result", side_effect=[QQProviderError("refresh failed"), expected]) as launch_result, \
             patch.object(provider, "_validate_qce_binary", side_effect=AssertionError("must not start QQ")):
            self.assertEqual(provider.launch(refresh=True), expected)
        self.assertEqual(launch_result.call_args_list, [call(refresh=True), call()])

    def test_launch_timeout_preserves_only_sanitized_last_readiness_error(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder)
            (bundle / "NapCatWinBootMain.exe").touch()
            (bundle / "NapCatWinBootHook.dll").touch()
            provider = QQProvider(bundle_dir=bundle, timeout=1)
            provider._launch_result = Mock(side_effect=[
                QQProviderError("QCE access token is unavailable; start/login to the local QCE runtime first"),
                QQProviderError("NapCat WebUI authentication failed"),
            ])
            fake_process = Mock(pid=123)
            clock = Mock(side_effect=[0, 0, 2])
            with patch("integrations.qq.provider.sys.platform", "win32"), \
                 patch.object(provider, "_validate_qce_binary"), \
                 patch.object(provider, "_find_qq_exe", return_value=Path("QQ.exe")), \
                 patch.object(provider, "_qq_process_running", return_value=False), \
                 patch.object(provider, "_prepare_runtime"), \
                 patch("integrations.qq.provider.subprocess.STARTUPINFO",
                       side_effect=lambda: SimpleNamespace(dwFlags=0, wShowWindow=0), create=True), \
                 patch("integrations.qq.provider.subprocess.STARTF_USESHOWWINDOW", 1, create=True), \
                 patch("integrations.qq.provider.subprocess.SW_HIDE", 0, create=True), \
                 patch("integrations.qq.provider.subprocess.Popen", return_value=fake_process), \
                 patch("integrations.qq.provider.time.monotonic", clock), \
                 patch("integrations.qq.provider.time.sleep"):
                with self.assertRaises(QQProviderError) as caught:
                    provider.launch()

        self.assertIn("NapCat WebUI authentication failed", str(caught.exception))
        self.assertNotIn("QCE access token is unavailable", str(caught.exception))
        self.assertEqual(_safe_launch_error(QQProviderError(
            "QR https://example.invalid/login?token=secret bearer secret")), "QQProviderError")
        self.assertEqual(_safe_launch_error(QQProviderError(
            "QQ.exe is already running; refusing to start a second client instance")),
            "QQ.exe is already running; refusing to start a second client instance")

    def test_qr_refresh_only_runs_while_waiting_for_scan(self):
        provider = QQProvider()
        calls = []

        def waiting(path):
            calls.append(path)
            if path.endswith("CheckLoginStatus"):
                return {"loginPhase": "waiting_qrcode", "coreReady": True, "isLogin": False}
            if path.endswith("RefreshQRcode"):
                return {"qrcodeurl": "fresh-fixture"}
            raise AssertionError("unexpected route")

        with patch.object(provider, "_napcat_request", side_effect=waiting):
            result = provider.login_qr(refresh=True)
        self.assertEqual(result["qr_url"], "fresh-fixture")
        self.assertTrue(calls[-1].endswith("RefreshQRcode"))

        calls.clear()

        def scanned(path):
            calls.append(path)
            if path.endswith("CheckLoginStatus"):
                return {"loginPhase": "qrcode_scanned", "coreReady": True, "isLogin": False}
            return {"qrcode": "existing-fixture"}

        with patch.object(provider, "_napcat_request", side_effect=scanned):
            result = provider.login_qr(refresh=True)
        self.assertEqual(result["qr_url"], "existing-fixture")
        self.assertFalse(any(path.endswith("RefreshQRcode") for path in calls))

        calls.clear()

        def logged_in(path):
            calls.append(path)
            if path.endswith("CheckLoginStatus"):
                return {"loginPhase": "logged_in", "coreReady": True, "isLogin": True}
            raise AssertionError("logged-in accounts do not need another QR request")

        with patch.object(provider, "_napcat_request", side_effect=logged_in):
            result = provider.login_qr()
        self.assertIsNone(result["qr_url"])
        self.assertEqual(len(calls), 1)

    def test_malformed_napcat_login_flag_does_not_suppress_qr(self):
        provider = QQProvider()
        for login_flag in ("false", 1, None):
            with self.subTest(login_flag=login_flag), patch.object(
                    provider, "_napcat_request", side_effect=[
                        {"isLogin": login_flag, "loginPhase": "waiting_qrcode"},
                        {"qrcode": "fixture-only"},
                    ]) as request:
                self.assertEqual(provider.login_status()["isLogin"], False)
                request.reset_mock()
                request.side_effect = [
                    {"isLogin": login_flag, "loginPhase": "waiting_qrcode"},
                    {"qrcode": "fixture-only"},
                ]
                result = provider.login_qr()

            self.assertEqual(result["qr_url"], "fixture-only")

    def test_runtime_config_replace_failure_preserves_original(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle = Path(folder)
            config = bundle / "config"
            config.mkdir()
            path = config / "napcat.json"
            original = '{"consoleLog": true, "keep": "fixture"}\n'
            path.write_text(original, encoding="utf-8")
            provider = QQProvider(bundle_dir=bundle)
            with patch("integrations.qq.provider.os.replace", side_effect=OSError("fixture")):
                with self.assertRaisesRegex(QQProviderError, "Could not write bundled napcat.json"):
                    provider._set_runtime_config("napcat.json", {"consoleLog": False})

            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(list(config.glob("napcat.json.*.tmp")), [])

    def test_media_export_poll_resolves_original_resource_endpoint(self):
        provider = QQProvider()
        calls = []

        def request(method, path, body=None, **_kwargs):
            calls.append((method, path, body))
            if path == "/api/messages/export":
                return {"taskId": "task-fixture", "fileName": "fixture.html"}
            if path == "/api/tasks/task-fixture":
                return {"status": "completed", "fileName": "fixture.html"}
            return {"resources": [{
                "fileName": "image_fixture.png",
                "relativePath": "/api/exports/files/fixture.html/resources/images/image_fixture.png",
            }]}

        with patch.object(provider, "_request", side_effect=request):
            item = provider._export_message_media({
                "conversation_id": "peer", "timestamp": 1234,
                "type": "image", "raw": {"chatType": 2, "peerUid": "peer"},
            }, "fixture.png", "")

        self.assertEqual(item["relativePath"],
                         "/api/exports/files/fixture.html/resources/images/image_fixture.png")
        self.assertEqual(calls[0][2]["filter"], {"startTime": 1232, "endTime": 1236})
        self.assertEqual(calls[0][2]["options"]["skipDownloadResourceTypes"], [])

    def test_media_returns_local_path_after_authenticated_resource_download(self):
        provider = QQProvider()
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(provider, "_request", return_value={"files": [{
                "fileName": "f00_image_fixture.png", "url": "/resources/images/f00_image_fixture.png",
            }]}), \
                 patch.object(provider, "_download_to_cache",
                              side_effect=_cached_download_fixture(folder, b"fixture-bytes")), \
                 patch.object(provider, "_media_cache_dir", return_value=Path(folder)):
                result = provider.media({"type": "image", "file_name": "image_fixture.png",
                                         "media": [{"file_name": "image_fixture.png", "md5": "f00"}]})
            local = Path(result["path"])
            self.assertTrue(result["available"])
            self.assertTrue(local.is_file())
            self.assertEqual(local.read_bytes(), b"fixture-bytes")

    def test_media_cache_write_is_atomic_and_leaves_no_partial_file(self):
        provider = QQProvider()
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(provider, "_request", return_value={"files": [{
                    "fileName": "fixture_image.png", "url": "/resources/images/fixture_image.png",
                }]}), \
                 patch.object(provider, "_download_to_cache",
                              side_effect=_cached_download_fixture(folder, b"fixture-bytes")), \
                patch.object(provider, "_media_cache_dir", return_value=Path(folder)):
            result = provider.media({"type": "image", "file_name": "image.png",
                                     "media": [{"file_name": "image.png", "md5": "fixture"}]})
            files = list(Path(folder).iterdir())
            contents = files[0].read_bytes()

        self.assertTrue(result["available"])
        self.assertEqual(files, [Path(result["path"])])
        self.assertEqual(contents, b"fixture-bytes")

    def test_media_prefers_digest_and_exports_current_group_on_collision(self):
        provider = QQProvider()
        request_calls = []

        def request(_method, path, body=None, **_kwargs):
            request_calls.append((path, body))
            if path.startswith("/api/resources/files"):
                return {"files": [{"fileName": "report.xlsx", "url": "/resources/files/report.xlsx"}]}
            if path == "/api/messages/export":
                return {"taskId": "task-fixture", "fileName": "fixture.html"}
            if path == "/api/tasks/task-fixture":
                return {"status": "completed", "fileName": "fixture.html"}
            return {"resources": [{
                "fileName": "abc123_report.xlsx",
                "relativePath": "/api/exports/files/fixture.html/resources/files/abc123_report.xlsx",
            }]}

        row = {
            "conversation_id": "group-peer", "chatType": 2,
            "timestamp": 1000, "type": "file", "file_name": "report.xlsx",
            "media": [{"kind": "file", "file_name": "report.xlsx", "md5": "abc123"}],
            "raw": {"msgTime": 1000, "peerUid": "group-peer"},
        }
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(provider, "_request", side_effect=request), \
             patch.object(provider, "_download_to_cache",
                          side_effect=_cached_download_fixture(folder, b"exported")), \
             patch.object(provider, "_media_cache_dir", return_value=Path(folder)):
            result = provider.media(row)

        self.assertTrue(result["available"])
        self.assertEqual(request_calls[0][0],
                         "/api/resources/files?type=files&limit=200&nameSearch=abc123")
        export_body = next(body for path, body in request_calls if path == "/api/messages/export")
        self.assertEqual(export_body["peer"]["chatType"], 2)

    def test_media_export_failure_degrades_to_unavailable(self):
        provider = QQProvider()
        with patch.object(provider, "_request", return_value={"files": []}), \
             patch.object(provider, "_export_message_media", side_effect=RuntimeError("fixture")):
            result = provider.media({"file_name": "missing.png", "type": "image"})
        self.assertFalse(result["available"])
        self.assertIsNone(result["path"])

    def test_forward_resource_signature_accepts_bmp(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "fixture.bmp"
            image.write_bytes(b"BM" + b"synthetic-bmp")
            self.assertEqual(QQProvider._media_signature(image, "image"),
                             (True, "image/bmp"))

    def test_forward_resource_signature_accepts_classic_and_big_tiff(self):
        with tempfile.TemporaryDirectory() as folder:
            for index, header in enumerate((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")):
                image = Path(folder) / f"fixture-{index}.tiff"
                image.write_bytes(header + b"synthetic-tiff")
                self.assertEqual(QQProvider._media_signature(image, "image"),
                                 (True, "image/tiff"))

    def test_forward_resource_signature_accepts_avif_heic_and_rejects_video_brands(self):
        fixtures = ((b"avif", (True, "image/avif")),
                    (b"avis", (True, "image/avif")),
                    (b"heic", (True, "image/heic")),
                    (b"heix", (True, "image/heic")),
                    (b"isom", (False, "application/octet-stream")))
        with tempfile.TemporaryDirectory() as folder:
            for index, (brand, expected) in enumerate(fixtures):
                image = Path(folder) / f"fixture-{index}.image"
                image.write_bytes(b"\x00\x00\x00\x14ftyp" + brand + b"\x00\x00\x00\x00" + brand)
                self.assertEqual(QQProvider._media_signature(image, "image"), expected)

    def test_forward_resource_signature_requires_exact_well_formed_bmff_brands(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "image.bin"
            lookalikes = (
                b"\x00\x00\x00\x14ftypzzzzheic",  # minor version is not a brand
                b"\x00\x00\x00\x14ftypheic\x00\x00\x00\x00",  # declared box is truncated
                b"\x00\x00\x00\x13ftypheic\x00\x00\x00",  # misaligned box size
            )
            for payload in lookalikes:
                image.write_bytes(payload)
                self.assertEqual(QQProvider._media_signature(image, "image"),
                                 (False, "application/octet-stream"))

    def test_forward_resource_signature_accepts_qce_silk_voice_variants(self):
        with tempfile.TemporaryDirectory() as folder:
            for index, header in enumerate((b"#!SILK_V3", b"\x02#!SILK_V3")):
                voice = Path(folder) / f"fixture-{index}.silk"
                voice.write_bytes(header + b"synthetic-silk")
                self.assertEqual(QQProvider._media_signature(voice, "voice"),
                                 (True, "audio/silk"))

    def test_forward_resource_signature_accepts_exporter_audio_formats_not_generic_video(self):
        fixtures = ((b"#!AMR\n", (True, "audio/amr")),
                    (b"#!AMR-WB\n", (True, "audio/amr")),
                    (b"\xff\xf1\x50\x80", (True, "audio/aac")),
                    (b"\x00\x00\x00\x18ftypM4A ", (True, "audio/mp4")),
                    (bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c"),
                     (True, "audio/x-ms-wma")),
                    (bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6d"),
                     (False, "application/octet-stream")),
                    (b"\x00\x00\x00\x18ftypisom", (False, "application/octet-stream")))
        with tempfile.TemporaryDirectory() as folder:
            for index, (header, expected) in enumerate(fixtures):
                voice = Path(folder) / f"fixture-{index}.audio"
                voice.write_bytes(header + b"synthetic-audio")
                self.assertEqual(QQProvider._media_signature(voice, "voice"), expected)

    def test_cached_forward_wma_is_returned_as_audio_without_qce_request(self):
        provider = QQProvider()
        with tempfile.TemporaryDirectory() as folder:
            cached = Path(folder) / "voice.wma"
            cached.write_bytes(bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c") + b"synthetic-audio")
            provider._forward_handles["opaque_wma"] = {
                "expires": time.monotonic() + 60, "account_id": "10001", "peer": "peer",
                "parent_id": "parent", "kind": "voice", "size": cached.stat().st_size,
                "cached_path": str(cached),
            }
            with patch.object(provider, "status", side_effect=AssertionError("QCE status")), \
                    patch.object(provider, "_rpc", side_effect=AssertionError("QCE download")):
                path, mime = provider.download_forward_resource("opaque_wma", Path(folder))
        self.assertEqual(path.name, "voice.wma")
        self.assertEqual(mime, "audio/x-ms-wma")

    def test_cached_media_reads_existing_image_without_contacting_qce(self):
        provider = QQProvider()
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "qq" / "media-cache"
            cache.mkdir(parents=True)
            image = cache / "0123456789ab-photo.png"
            image.write_bytes(b"synthetic image")
            row = {"type": "image", "file_name": "photo.png", "extra": {}}
            with patch.object(provider, "_media_cache_dir", return_value=cache), \
                 patch.object(provider, "_request", side_effect=AssertionError("QCE request")), \
                patch.object(provider, "_download_to_cache", side_effect=AssertionError("QCE download")), \
                 patch.object(provider, "_export_message_media", side_effect=AssertionError("QCE export")):
                result = provider.cached_media(row)

        self.assertTrue(result["available"])
        self.assertEqual(Path(result["path"]).name, image.name)

    def test_cached_media_reads_existing_video_voice_and_file_without_qce(self):
        provider = QQProvider()
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "qq" / "media-cache"
            cache.mkdir(parents=True)
            cases = (("video", "clip.mp4"), ("voice", "voice.ogg"), ("file", "notes.pdf"))
            for kind, name in cases:
                (cache / f"fixture-{name}").write_bytes(b"synthetic " + kind.encode())
            with patch.object(provider, "_media_cache_dir", return_value=cache), \
                 patch.object(provider, "_request", side_effect=AssertionError("QCE request")), \
                 patch.object(provider, "_download_to_cache", side_effect=AssertionError("QCE download")), \
                 patch.object(provider, "_export_message_media", side_effect=AssertionError("QCE export")):
                results = [provider.cached_media({"type": kind, "extra": {
                    "media": [{"type": kind, "name": name}],
                }}) for kind, name in cases]

        self.assertTrue(all(result["available"] for result in results))
        self.assertEqual([Path(result["path"]).name for result in results],
                         [f"fixture-{name}" for _kind, name in cases])

    def test_cached_media_miss_never_contacts_qce(self):
        provider = QQProvider()
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / "qq" / "media-cache"
            cache.mkdir(parents=True)
            row = {"type": "image", "file_name": "missing.png", "extra": {}}
            with patch.object(provider, "_media_cache_dir", return_value=cache), \
                 patch.object(provider, "_request", side_effect=AssertionError("QCE request")), \
                patch.object(provider, "_download_to_cache", side_effect=AssertionError("QCE download")), \
                 patch.object(provider, "_export_message_media", side_effect=AssertionError("QCE export")):
                result = provider.cached_media(row)

        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "cached_image_missing")

    def test_cached_media_index_selects_only_a_contained_image_resource(self):
        provider = QQProvider()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = root / "qq" / "images" / "selected.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"synthetic image")
            cache = root / "qq" / "media-cache"
            cache.mkdir()
            row = {"type": "image", "extra": {"roaming_resources": [
                {"type": "file", "path": "qq/images/other.png"},
                {"type": "image", "path": "qq/images/selected.png"},
            ]}}
            with patch.object(provider, "_media_cache_dir", return_value=cache):
                selected = provider.cached_media(row, resource_index=1)
                wrong_type = provider.cached_media(row, resource_index=0)

        self.assertTrue(selected["available"])
        self.assertEqual(Path(selected["path"]), image.resolve())
        self.assertFalse(wrong_type["available"])
        self.assertEqual(wrong_type["reason"], "resource_is_not_image")

    def test_cached_media_rejects_unsafe_roaming_paths_and_resolves_default_image(self):
        provider = QQProvider()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache = root / "qq" / "media-cache"
            cache.mkdir(parents=True)
            image = root / "qq" / "roaming" / "photo.png"
            image.parent.mkdir()
            image.write_bytes(b"synthetic image")
            row = {"type": "image", "file_name": "ignored.png", "extra": {
                "roaming_resources": [{"type": "file", "path": "qq/roaming/other.png"},
                                       {"type": "image", "path": "qq/roaming/photo.png"}],
            }}
            with patch.object(provider, "_media_cache_dir", return_value=cache):
                selected = provider.cached_media(row)
                traversal = provider.cached_media({"type": "image", "extra": {
                    "roaming_resources": [{"type": "image", "path": "../outside.png"}],
                }}, resource_index=0)
                absolute = provider.cached_media({"type": "image", "extra": {
                    "roaming_resources": [{"type": "image", "path": str(image)}],
                }}, resource_index=0)

        self.assertTrue(selected["available"])
        self.assertEqual(Path(selected["path"]), image.resolve())
        self.assertFalse(traversal["available"])
        self.assertFalse(absolute["available"])

    def test_cached_media_rejects_roaming_symlink_even_when_target_is_inside_storage(self):
        provider = QQProvider()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache = root / "qq" / "media-cache"
            cache.mkdir(parents=True)
            target = root / "qq" / "real.png"
            target.write_bytes(b"synthetic image")
            link = root / "qq" / "alias.png"
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            row = {"type": "image", "extra": {"roaming_resources": [
                {"type": "image", "path": "qq/alias.png"},
            ]}}
            with patch.object(provider, "_media_cache_dir", return_value=cache):
                result = provider.cached_media(row, resource_index=0)

        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "cached_resource_missing_or_unsafe")

    def test_cached_resource_gallery_uses_opaque_ids_and_encoded_local_paths(self):
        provider = QQProvider()
        rows = [{"fileName": "photo 1.png", "url": "/resources/images/photo%201.png",
                 "type": "image", "size": len(b"image-bytes"), "mimeType": "image/png",
                 "modifyTime": "2026-10-01T00:00:00Z"}]
        with patch.object(provider, "status", return_value={"online": True, "account_id": "qq-1"}), \
                patch.object(provider, "_request", return_value={"files": rows, "total": 1,
                                                                   "hasMore": False}) as request:
            result = provider.cached_resources(kind="image", query="photo", offset=60, limit=60)
        request.assert_called_once_with("GET", "/api/resources/files?type=images&page=2&limit=60&nameSearch=photo")
        self.assertEqual(result["account_id"], "qq-1")
        self.assertEqual(result["items"][0]["name"], "photo 1.png")
        self.assertNotIn("url", result["items"][0])
        self.assertNotIn("/resources", json.dumps(result))
        resource_id = result["items"][0]["resource_id"]
        with patch.object(provider, "status", return_value={"online": True, "account_id": "qq-1"}):
            self.assertEqual(provider.cached_resource_metadata(resource_id, "qq-1"),
                             {"type": "image", "name": "photo 1.png"})
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(provider, "status", return_value={"online": True, "account_id": "qq-1"}), \
                patch.object(provider, "_download_to_cache",
                             side_effect=_cached_download_fixture(folder, b"image-bytes")) as download, \
                patch.object(provider, "_media_cache_dir", return_value=Path(folder)):
            path, name = provider.resolve_cached_resource(resource_id, "qq-1")
            self.assertEqual(path.read_bytes(), b"image-bytes")
        download.assert_called_once_with("/resources/images/photo%201.png", "photo 1.png", 11)
        self.assertEqual(name, "photo 1.png")

    def test_cached_resource_gallery_honors_non_page_aligned_offset(self):
        provider = QQProvider()
        def row(name):
            return {"fileName": name, "url": f"/resources/images/{name}",
                    "type": "image", "size": 1, "mimeType": "image/png"}

        pages = [
            {"files": [row("a.png")], "total": 4, "hasMore": True},
            {"files": [row("b.png")], "total": 4, "hasMore": True},
            {"files": [row("c.png"), row("d.png")], "total": 4, "hasMore": False},
        ]
        with patch.object(provider, "status", return_value={"online": True, "account_id": "qq-1"}), \
                patch.object(provider, "_request", side_effect=pages) as request:
            result = provider.cached_resources(offset=1, limit=2)

        self.assertEqual([item["name"] for item in result["items"]], ["b.png", "c.png"])
        self.assertEqual(result["total"], 4)
        self.assertTrue(result["has_more"])
        self.assertEqual([call.args[1] for call in request.call_args_list], [
            "/api/resources/files?type=all&page=1&limit=2",
            "/api/resources/files?type=all&page=2&limit=2",
            "/api/resources/files?type=all&page=3&limit=2",
        ])

    def test_cached_resource_gallery_bounds_short_page_followups(self):
        provider = QQProvider()
        pages = [{"files": [], "total": 100, "hasMore": True} for _ in range(4)]
        with patch.object(provider, "status", return_value={"online": True, "account_id": "qq-1"}), \
                patch.object(provider, "_request", side_effect=pages) as request:
            result = provider.cached_resources(offset=0, limit=2)

        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 100)
        self.assertTrue(result["has_more"])
        self.assertEqual(request.call_count, 4)

    def test_cached_resource_gallery_passes_date_bounds_and_rejects_invalid_ranges(self):
        provider = QQProvider()
        with patch.object(provider, "status", return_value={"online": True, "account_id": "qq-1"}), \
                patch.object(provider, "_request", return_value={"files": [], "total": 0,
                                                                   "hasMore": False,
                                                                   "dateFilterSupported": True}) as request:
            provider.cached_resources(start_ms=1_000, end_ms=2_999)
            self.assertEqual(request.call_args.args[1],
                             "/api/resources/files?type=all&page=1&limit=60&startTime=1000&endTime=2999")
            for bounds in ({"start_ms": True}, {"end_ms": -1},
                           {"start_ms": 2000, "end_ms": 1000}):
                with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                    provider.cached_resources(**bounds)

    def test_cached_resource_date_filter_fails_closed_with_old_qce(self):
        provider = QQProvider()
        with patch.object(provider, "status", return_value={"online": True, "account_id": "qq-1"}), \
                patch.object(provider, "_request", return_value={"files": [], "total": 9,
                                                                   "hasMore": True}):
            with self.assertRaisesRegex(QQProviderError, "does not support.*date filtering"):
                provider.cached_resources(start_ms=1_000)

    def test_media_download_streams_bounded_chunks_to_atomic_cache_file(self):
        provider = QQProvider()
        payload = b"a" * (2 * 1024 * 1024 + 17)

        class Response:
            def __init__(self):
                self.offset = 0
                self.read_sizes = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size):
                self.read_sizes.append(size)
                chunk = payload[self.offset:self.offset + size]
                self.offset += len(chunk)
                return chunk

        response = Response()

        class Opener:
            def open(self, _request, timeout):
                self.timeout = timeout
                return response

        opener = Opener()
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(provider, "_token", return_value="local-token"), \
                patch.object(provider, "_media_cache_dir", return_value=Path(folder)), \
                patch("integrations.qq.provider.build_opener", return_value=opener):
            target = provider._download_to_cache("/resources/images/picture.png", "picture.png",
                                                 expected_size=len(payload))
            self.assertEqual(target.read_bytes(), payload)
            self.assertLessEqual(max(response.read_sizes), 1024 * 1024)
            self.assertEqual(list(Path(folder).iterdir()), [target])

    def test_cached_resource_gallery_rejects_mismatched_paths_and_account_changes(self):
        provider = QQProvider()
        rows = [
            {"fileName": "photo.png", "url": "/resources/files/photo.png", "type": "image", "size": 1},
            {"fileName": "../secret.png", "url": "/resources/images/secret.png", "type": "image", "size": 1},
            {"fileName": "ok.png", "url": "https://example.invalid/ok.png", "type": "image", "size": 1},
        ]
        with patch.object(provider, "status", return_value={"online": True, "account_id": "qq-1"}), \
                patch.object(provider, "_request", return_value={"files": rows, "total": 3}):
            result = provider.cached_resources()
        self.assertEqual(result["items"], [])
        with self.assertRaisesRegex(FileNotFoundError, "no longer available"):
            provider.resolve_cached_resource("missing-id", "qq-1")

    def test_normalized_message_keeps_chat_type_for_media_export(self):
        row = QQProvider._normalize_message(
            {"msgId": "fixture", "msgTime": 1, "elements": [
                {"textElement": {"content": "hello"}},
                {"arkElement": {"title": "A card", "description": "Details"}},
                {"fileElement": {"fileName": "report.xlsx", "fileSize": 12}},
            ]}, "peer", 2,
        )
        self.assertEqual(row["chatType"], 2)
        self.assertEqual(row["roaming_content"]["elements"][0],
                         {"type": "text", "data": {"text": "hello"}})
        self.assertEqual(row["roaming_content"]["elements"][1]["type"], "json")
        self.assertNotIn("raw", row)
        provider = QQProvider()
        with patch.object(provider, "_request", side_effect=[
                {"taskId": "task", "fileName": "export.html"},
                {"status": "completed", "fileName": "export.html"},
                {"resources": [{"fileName": "report.xlsx"}]},
        ]) as request:
            provider._export_message_media(row, "report.xlsx", "")
        self.assertEqual(request.call_args_list[0].args[2]["peer"]["chatType"], 2)

    def test_normalized_message_preserves_native_face_and_reply_preview(self):
        row = QQProvider._normalize_message({"msgId": "fixture", "msgTime": 1, "elements": [
            {"faceElement": {"faceIndex": 358, "faceText": "/骰子", "privatePayload": "drop"}},
            {"replyElement": {"referencedMessageId": "quoted-1", "senderNick": "Peer",
                              "sourceMsgText": "quoted text", "privatePayload": "drop"}},
        ]}, "peer", 1)

        self.assertEqual(row["roaming_content"]["elements"], [
            {"type": "face", "data": {"id": "358", "name": "/骰子"}},
            {"type": "reply", "data": {
                "messageId": "quoted-1", "senderName": "Peer", "content": "quoted text"}},
        ])
        self.assertNotIn("privatePayload", str(row))

    def test_normalized_face_preserves_zero_index(self):
        row = QQProvider._normalize_message({"msgId": "zero-face", "elements": [
            {"faceElement": {"faceIndex": 0}},
        ]}, "peer", 1)

        self.assertEqual(row["roaming_content"]["elements"], [
            {"type": "face", "data": {"id": "0", "name": "表情0"}},
        ])

    def test_normalized_mentions_preserve_qce_all_and_member_at_elements(self):
        row = QQProvider._normalize_message({"msgId": "mentions", "elements": [
            {"textElement": {"content": "@全体成员", "atType": 1, "privatePayload": "drop"}},
            {"textElement": {"content": "@Alice", "atType": 2, "atNtUid": "uid-1",
                              "atUid": "10001", "privatePayload": "drop"}},
        ]}, "peer", 2)

        self.assertEqual(row["text"], "@全体成员@Alice")
        self.assertEqual(row["roaming_content"]["elements"], [
            {"type": "at", "data": {"uid": "all", "uin": "0", "name": "全体成员", "atType": 1}},
            {"type": "at", "data": {"uid": "uid-1", "uin": "10001", "name": "Alice", "atType": 2}},
        ])
        self.assertNotIn("privatePayload", str(row))

    def test_boolean_at_type_is_not_treated_as_qce_numeric_mention(self):
        row = QQProvider._normalize_message({"msgId": "boolean-mention", "elements": [
            {"textElement": {"content": "@text", "atType": True}},
        ]}, "peer", 2)

        self.assertEqual(row["roaming_content"]["elements"], [
            {"type": "text", "data": {"text": "@text"}},
        ])

    def test_normalized_markdown_is_searchable_bounded_and_drops_raw_payload(self):
        source = "**hello**" * 600
        row = QQProvider._normalize_message({"msgId": "markdown", "elements": [{
            "markdownElement": {"content": source, "privatePayload": "drop"},
        }]}, "peer", 1)

        self.assertEqual(row["type"], "text")
        self.assertEqual(row["text"], source[:4096])
        self.assertEqual(row["roaming_content"]["elements"], [{
            "type": "markdown", "data": {"content": source[:4096]},
        }])
        self.assertNotIn("privatePayload", str(row))

    def test_normalized_wallet_element_keeps_only_bounded_display_summary(self):
        row = QQProvider._normalize_message({"msgId": "wallet", "msgTime": 1, "elements": [{
            "walletElement": {"title": "恭喜发财", "channel": 1,
                              "amount": "private-amount", "token": "private-token"},
        }]}, "peer", 2)

        self.assertEqual(row["roaming_content"]["elements"], [{
            "type": "wallet", "data": {"summary": "恭喜发财"},
        }])
        self.assertNotIn("private-amount", str(row))
        self.assertNotIn("private-token", str(row))

    def test_normalized_live_gift_keeps_only_bounded_qce_summary(self):
        row = QQProvider._normalize_message({"msgId": "gift", "elements": [{
            "liveGiftElement": {"giftName": "鲜花", "recipient": "private-recipient",
                                "payload": "private-payload"},
        }]}, "peer", 2)

        self.assertEqual(row["roaming_content"]["elements"], [{
            "type": "live_gift", "data": {"summary": "鲜花"},
        }])
        self.assertNotIn("private-recipient", str(row))
        self.assertNotIn("private-payload", str(row))

    def test_normalized_tofu_record_preserves_bounded_description_only(self):
        description = "状态描述" * 100
        row = QQProvider._normalize_message({"msgId": "tofu", "elements": [{
            "tofuRecordElement": {"descriptionContent": description,
                                   "payload": "private-payload", "type": 7},
        }]}, "peer", 2)

        self.assertEqual(row["roaming_content"]["elements"], [{
            "type": "tofu_record", "data": {"summary": description[:256]},
        }])
        self.assertNotIn("private-payload", str(row))

    def test_normalized_giphy_keeps_only_offline_summary(self):
        row = QQProvider._normalize_message({"msgId": "giphy", "elements": [{
            "giphyElement": {"id": "private-id", "url": "https://private.invalid/gif",
                             "width": 640, "height": 480},
        }]}, "peer", 2)

        self.assertEqual(row["roaming_content"]["elements"], [{
            "type": "giphy", "data": {"summary": "Giphy 动图"},
        }])
        self.assertNotIn("private-id", str(row))
        self.assertNotIn("private.invalid", str(row))

    def test_normalized_shared_location_keeps_qce_display_summary_only(self):
        row = QQProvider._normalize_message({"msgId": "location", "elements": [{
            "shareLocationElement": {"latitude": 22.3, "longitude": 114.2,
                                      "privatePayload": "drop"},
        }]}, "peer", 2)

        self.assertEqual(row["roaming_content"]["elements"], [{
            "type": "location", "data": {"title": "位置消息", "summary": "分享了位置"},
        }])
        self.assertNotIn("22.3", str(row))
        self.assertNotIn("privatePayload", str(row))

    def test_normalized_struct_long_message_keeps_only_offline_summary(self):
        row = QQProvider._normalize_message({"msgId": "long-message", "elements": [{
            "structLongMsgElement": {"resId": "private-resource-id",
                                      "xmlContent": "<private>content</private>"},
        }]}, "peer", 2)

        self.assertEqual(row["roaming_content"]["elements"], [{
            "type": "long_message", "data": {"summary": "长消息"},
        }])
        self.assertNotIn("private-resource-id", str(row))
        self.assertNotIn("private>content", str(row))

    def test_normalized_message_bounds_nested_forward_preview_and_redacts_locators(self):
        rows = [{"senderUin": str(index), "elements": [
            {"textElement": {"content": f"message-{index}"}},
            {"picElement": {"fileName": "photo.png", "fileSize": 123,
                             "md5HexStr": "a" * 32, "picWidth": 640,
                             "originImageUrl": "/private/path"}},
        ]} for index in range(12)]
        rows[0]["elements"].append({"multiForwardMsgElement": {"resId": "private-id"}})
        rows[0]["records"] = [{"sendNickName": "Nested", "elements": [
            {"textElement": {"content": "nested-text"}},
        ]}]
        normalized = QQProvider._normalize_message({"msgId": "outer", "elements": [
            {"multiForwardMsgElement": {"resId": "private-id"}},
        ], "records": rows}, "peer", 2)

        forward = normalized["roaming_content"]["elements"][0]["data"]
        self.assertEqual(len(forward["messages"]), 10)
        self.assertIn("nested-text", forward["messages"][0]["content"]["text"])
        self.assertIn("[图片: photo.png]", forward["messages"][0]["content"]["text"])
        self.assertEqual(forward["messages"][0]["content"]["resources"], [{
            "type": "image", "available": False, "name": "photo.png", "size": 123,
            "md5": "a" * 32, "width": 640,
        }])
        self.assertNotIn("/private/path", str(forward))
        self.assertNotIn("private-id", str(forward))
        self.assertNotIn("records", str(forward))
        self.assertLessEqual(sum(len(item["content"]["text"].encode())
                                for item in forward["messages"]), 16_384)

    def test_forward_preview_bounds_nested_resource_metadata(self):
        records = [{"elements": [{"picElement": {"fileName": f"image-{index}-{part}.png"}}
                                  for part in range(10)]} for index in range(10)]
        preview = QQProvider._forward_preview(records)
        self.assertLessEqual(sum(len(item["content"].get("resources", []))
                                 for item in preview), 50)

    def test_normalized_message_keeps_bounded_call_record_summary(self):
        row = QQProvider._normalize_message({"msgId": "call", "msgType": 19, "elements": [{
            "avRecordElement": {"type": 2, "text": "missed", "time": "private-time",
                                "payload": "private-payload"},
        }]}, "peer", 1)

        self.assertEqual(row["type"], "system")
        self.assertEqual(row["roaming_content"]["elements"], [{
            "type": "system", "data": {"text": "视频通话 - missed"},
        }])
        self.assertNotIn("private", str(row))


if __name__ == "__main__":
    unittest.main()
