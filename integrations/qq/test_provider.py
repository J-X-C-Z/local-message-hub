import json
import tempfile
import unittest
from io import BytesIO
from urllib.error import HTTPError
from pathlib import Path
from unittest.mock import patch

from integrations.qq.provider import QQProvider


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class QQProviderTests(unittest.TestCase):
    def test_message_pagination_uses_requested_page_and_offset(self):
        provider = QQProvider()
        calls = []

        def request(method, path, body=None, **_kwargs):
            calls.append((path, body))
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
        self.assertEqual([row["id"] for row in rows], ["peer:p2-b", "peer:p3-a"])
        self.assertEqual(rows[0]["timestamp"], 101)

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

    def test_qr_refresh_only_runs_while_waiting_for_scan(self):
        provider = QQProvider()
        calls = []

        def waiting(path):
            calls.append(path)
            if path.endswith("CheckLoginStatus"):
                return {"loginPhase": "waiting_qrcode", "coreReady": False}
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
                return {"loginPhase": "qrcode_scanned", "coreReady": False}
            return {"qrcode": "existing-fixture"}

        with patch.object(provider, "_napcat_request", side_effect=scanned):
            result = provider.login_qr(refresh=True)
        self.assertEqual(result["qr_url"], "existing-fixture")
        self.assertFalse(any(path.endswith("RefreshQRcode") for path in calls))

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
                 patch.object(provider, "_request_bytes", return_value=b"fixture-bytes"), \
                 patch.object(provider, "_media_cache_dir", return_value=Path(folder)):
                result = provider.media({"type": "image", "file_name": "image_fixture.png",
                                         "media": [{"file_name": "image_fixture.png", "md5": "f00"}]})
            local = Path(result["path"])
            self.assertTrue(result["available"])
            self.assertTrue(local.is_file())
            self.assertEqual(local.read_bytes(), b"fixture-bytes")

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
             patch.object(provider, "_request_bytes", return_value=b"exported"), \
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

    def test_normalized_message_keeps_chat_type_for_media_export(self):
        row = QQProvider._normalize_message(
            {"msgId": "fixture", "msgTime": 1, "elements": []}, "peer", 2,
        )
        self.assertEqual(row["chatType"], 2)


if __name__ == "__main__":
    unittest.main()
