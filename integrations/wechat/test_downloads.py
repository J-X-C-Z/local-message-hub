"""Synthetic tests for WeChat file cache resolution and proactive download."""

from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from .downloads import WeChatDownloadError, cached_attachment, download_attachment
from .vendor.media import MediaDownloader


class WeChatDownloadTests(unittest.TestCase):
    @staticmethod
    def content(name: str, payload: bytes, *, expires_at: int = 0) -> str:
        expiry = (f"<extcommoninfo><media_expire_at>{expires_at}</media_expire_at>"
                  "</extcommoninfo>" if expires_at else "")
        return (
            "<msg><appmsg><title>" + name + "</title><type>6</type><appattach>"
            f"<totallen>{len(payload)}</totallen><fileext>pdf</fileext>"
            f"</appattach><md5>{hashlib.md5(payload).hexdigest()}</md5></appmsg>"
            + expiry + "</msg>"
        )

    def row(self, payload: bytes, *, name: str = "report.pdf",
            local_id: int = 7, expires_at: int = 0, message: str | None = None) -> dict:
        conversation = "fixture@chatroom"
        return {
            "platform": "wechat",
            "account_id": "wxid_fixture",
            "conversation_id": conversation,
            "id": message or f"{conversation}:message_0.db:{local_id}",
            "type": "file",
            "raw_type": "49",
            "file_name": name,
            "timestamp": 1793000000,
            "extra": {
                "content": self.content(name, payload, expires_at=expires_at),
                "shard": "message_0.db",
                "local_id": local_id,
                "session_id": conversation,
            },
        }

    def provider(self, root: Path, source_row: dict):
        account_dir = root / "account"
        reader = SimpleNamespace(
            wxid="wxid_fixture",
            account="wxid_fixture",
            account_dir=str(account_dir),
            get_message_row=Mock(return_value=source_row),
        )
        return SimpleNamespace(reader=reader)

    def source_row(self, payload: bytes) -> dict:
        return {
            "create_time": 1793000000,
            "local_type": 49,
            "server_id": "123456",
            "content": self.content("report.pdf", payload),
            "shard": "message_0.db",
        }

    def test_resolves_current_packed_resource_filename(self):
        packed = bytes.fromhex(
            "080610023a4b0a4708001243323032362d32303237e5ada6e5b9b4e7acac"
            "e4b880e5ada6e69c9fe2809ce99885e8afbbe5ae9ee8b7b5e591a8e2809d"
            "e6b4bbe58aa8e5ae89e68e922839292e70646620005800"
        )
        self.assertEqual(
            MediaDownloader._resource_filename(packed),
            "2026-2027学年第一学期“阅读实践周”活动安排(9).pdf",
        )

    def test_cached_client_file_is_copied_and_verified_without_ui(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = b"%PDF-1.4 synthetic cached file"
            row = self.row(payload)
            provider = self.provider(root, self.source_row(payload))
            month = time.strftime("%Y-%m", time.localtime(1793000000))
            source = root / "account" / "msg" / "file" / month / "report.pdf"
            source.parent.mkdir(parents=True)
            source.write_bytes(payload)
            result = download_attachment(provider, row, storage=root / "hub")
            self.assertTrue(result["available"])
            self.assertEqual(Path(result["path"]).read_bytes(), payload)
            self.assertEqual(Path(result["path"]).name, "0")
            provider.reader.get_message_row.reset_mock()
            repeat = download_attachment(provider, row, storage=root / "hub")
            self.assertEqual(repeat, result)
            provider.reader.get_message_row.assert_not_called()

    def test_client_download_is_used_only_after_cache_miss(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = b"%PDF-1.4 proactive client download"
            row = self.row(payload)
            provider = self.provider(root, self.source_row(payload))
            source = root / "client-cache.pdf"

            def download(*_args, **_kwargs):
                source.write_bytes(payload)
                return source

            with patch("integrations.wechat.downloads._client_download",
                       side_effect=download) as client:
                result = download_attachment(
                    provider, row, storage=root / "hub",
                    conversation_title="Fixture group")
            self.assertTrue(result["available"])
            self.assertEqual(Path(result["path"]).read_bytes(), payload)
            client.assert_called_once()

    def test_duplicate_cache_name_and_md5_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = b"%PDF-1.4 renamed duplicate"
            row = self.row(payload)
            provider = self.provider(root, self.source_row(payload))
            month = time.strftime("%Y-%m", time.localtime(1793000000))
            directory = root / "account" / "msg" / "file" / month
            directory.mkdir(parents=True)
            (directory / "report (1).pdf").write_bytes(b"wrong bytes")
            (directory / "report (2).pdf").write_bytes(payload)
            with patch("integrations.wechat.downloads._source_filename",
                       return_value="report (2).pdf"):
                result = download_attachment(provider, row, storage=root / "hub")
            self.assertEqual(Path(result["path"]).read_bytes(), payload)

    def test_bad_client_bytes_never_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = b"%PDF-1.4 expected"
            row = self.row(payload)
            provider = self.provider(root, self.source_row(payload))
            source = root / "wrong.pdf"
            source.write_bytes(b"%PDF-1.4 wrong!!!")
            with patch("integrations.wechat.downloads._find_cached_source",
                       return_value=source):
                with self.assertRaises(WeChatDownloadError) as raised:
                    download_attachment(provider, row, storage=root / "hub")
            self.assertIn(raised.exception.code, {"download_size_mismatch",
                                                  "download_md5_mismatch"})
            self.assertFalse(cached_attachment(row, storage=root / "hub")["available"])

    def test_invalid_type_size_and_expiry_fail_before_client(self):
        payload = b"fixture"
        cases = []
        link = self.row(payload)
        link["type"] = "link"
        cases.append((link, "attachment_not_file"))
        expired = self.row(payload, expires_at=1)
        cases.append((expired, "attachment_expired"))
        large = self.row(payload)
        large["extra"]["content"] = (
            "<msg><appmsg><title>large.pdf</title><type>6</type><appattach>"
            f"<totallen>{32 * 1024 * 1024 + 1}</totallen></appattach>"
            f"<md5>{hashlib.md5(payload).hexdigest()}</md5></appmsg></msg>"
        )
        cases.append((large, "attachment_too_large"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            provider = self.provider(root, self.source_row(payload))
            with patch("integrations.wechat.downloads._client_download") as client:
                for row, code in cases:
                    with self.subTest(code=code):
                        with self.assertRaises(WeChatDownloadError) as raised:
                            download_attachment(provider, row, storage=root / "hub")
                        self.assertEqual(raised.exception.code, code)
                client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
