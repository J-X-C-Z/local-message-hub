"""Synthetic tests for QQ active-download isolation and validation."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from .downloads import MAX_FILE_BYTES, QQDownloadError, cached_attachment, download_attachment


class QQDownloadTests(unittest.TestCase):
    @staticmethod
    def row(
        payload: bytes = b"synthetic QQ file",
        *,
        account: str = "10001",
        conversation: str = "u-private",
        message: str = "message-1",
        index: int = 0,
        kind: str = "file",
        digest: str | None = None,
        size: int | None = None,
        extra_key: str = "media",
    ) -> dict:
        resource = {
            "kind": kind,
            "type": kind,
            "file_name": "report.txt",
            "md5": hashlib.md5(payload).hexdigest() if digest is None else digest,
            "size": len(payload) if size is None else size,
        }
        return {
            "platform": "qq",
            "account_id": account,
            "id": f"{conversation}:{message}",
            "conversation_id": conversation,
            "timestamp": 1234,
            "type": kind,
            "extra": {extra_key: [resource]},
        }

    def provider(self, source: Path, *, account: str = "10001") -> Mock:
        provider = Mock()
        provider.status.return_value = {"online": True, "account_id": account}
        provider.media.return_value = {"available": True, "path": str(source)}
        return provider

    def test_cached_query_does_not_raise_for_ordinary_non_file_or_no_md5(self):
        with tempfile.TemporaryDirectory() as temp:
            storage = Path(temp)
            self.assertFalse(cached_attachment(
                self.row(kind="image"), storage=storage)["available"])
            self.assertFalse(cached_attachment(
                self.row(digest=""), storage=storage)["available"])
            self.assertFalse(cached_attachment(
                self.row(index=5), resource_index=5, storage=storage)["available"])

    def test_download_projects_private_and_group_chat_type(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = b"synthetic private and group files"
            source = root / "source.bin"
            source.write_bytes(payload)
            for kind, expected in (("private", 1), ("group", 2)):
                with self.subTest(kind=kind):
                    row = self.row(payload, conversation=f"conversation-{kind}",
                                   message=f"message-{kind}")
                    provider = self.provider(source)
                    result = download_attachment(
                        provider, row, storage=root / kind, conversation_kind=kind)
                    self.assertTrue(result["available"])
                    projected = provider.media.call_args.args[0]
                    self.assertEqual(projected["chatType"], expected)
                    self.assertEqual(projected["type"], "file")
                    self.assertEqual(len(projected["media"]), 1)
                    self.assertEqual(Path(result["path"]).name, "0")
                    self.assertEqual(Path(result["path"]).read_bytes(), payload)

    def test_account_and_message_cache_keys_are_isolated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload_one = b"account one"
            payload_two = b"account two"
            payload_three = b"message two"
            source_one = root / "one.bin"
            source_two = root / "two.bin"
            source_three = root / "three.bin"
            source_one.write_bytes(payload_one)
            source_two.write_bytes(payload_two)
            source_three.write_bytes(payload_three)
            first = download_attachment(
                self.provider(source_one, account="10001"),
                self.row(payload_one, account="10001"),
                storage=root / "storage",
            )
            second = download_attachment(
                self.provider(source_two, account="10002"),
                self.row(payload_two, account="10002"),
                storage=root / "storage",
            )
            third = download_attachment(
                self.provider(source_three, account="10001"),
                self.row(payload_three, account="10001", message="message-2"),
                storage=root / "storage",
            )
            self.assertNotEqual(first["path"], second["path"])
            self.assertNotEqual(first["path"], third["path"])
            self.assertEqual(Path(first["path"]).read_bytes(), payload_one)
            self.assertEqual(Path(second["path"]).read_bytes(), payload_two)
            self.assertEqual(Path(third["path"]).read_bytes(), payload_three)

    def test_repeat_request_uses_verified_cache_without_provider_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = b"cache once"
            source = root / "source.bin"
            source.write_bytes(payload)
            provider = self.provider(source)
            row = self.row(payload)
            first = download_attachment(provider, row, storage=root)
            provider.status.side_effect = AssertionError("network/status must not run")
            provider.media.side_effect = AssertionError("download must not run")
            second = download_attachment(provider, row, storage=root)
            self.assertEqual(first, second)
            provider.status.assert_called_once_with()
            provider.media.assert_called_once()

    def test_cached_digest_mismatch_is_a_miss_and_can_be_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = b"correct bytes"
            source = root / "source.bin"
            source.write_bytes(payload)
            row = self.row(payload)
            first = download_attachment(self.provider(source), row, storage=root)
            Path(first["path"]).write_bytes(b"corrupt bytes")
            self.assertFalse(cached_attachment(row, storage=root)["available"])
            provider = self.provider(source)
            repaired = download_attachment(provider, row, storage=root)
            self.assertEqual(Path(repaired["path"]).read_bytes(), payload)
            provider.media.assert_called_once()

    def test_account_mismatch_fails_closed_before_download(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.bin"
            source.write_bytes(b"fixture")
            provider = self.provider(source, account="20002")
            with self.assertRaises(QQDownloadError) as raised:
                download_attachment(provider, self.row(b"fixture"), storage=temp)
            self.assertEqual(raised.exception.code, "qq_account_mismatch")
            provider.media.assert_not_called()

    def test_download_digest_mismatch_does_not_publish_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.bin"
            source.write_bytes(b"different-byte")
            row = self.row(b"expected bytes")
            with self.assertRaises(QQDownloadError) as raised:
                download_attachment(self.provider(source), row, storage=root)
            self.assertEqual(raised.exception.code, "download_md5_mismatch")
            self.assertFalse(cached_attachment(row, storage=root)["available"])
            self.assertFalse(list(root.rglob("*.part")))

    def test_oversized_and_non_file_attachments_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.bin"
            source.write_bytes(b"fixture")
            for row, code in (
                (self.row(size=MAX_FILE_BYTES + 1), "attachment_too_large"),
                (self.row(kind="image"), "attachment_not_file"),
            ):
                with self.subTest(code=code):
                    provider = self.provider(source)
                    with self.assertRaises(QQDownloadError) as raised:
                        download_attachment(provider, row, storage=temp)
                    self.assertEqual(raised.exception.code, code)
                    provider.status.assert_not_called()

    def test_roaming_resources_and_top_level_media_are_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = b"alternate shape"
            source = root / "source.bin"
            source.write_bytes(payload)
            roaming = self.row(payload, extra_key="roaming_resources")
            top_level = self.row(payload, conversation="u-top", message="top")
            resource = top_level["extra"].pop("media")[0]
            top_level["media"] = [resource]
            for row in (roaming, top_level):
                result = download_attachment(self.provider(source), row, storage=root)
                self.assertEqual(Path(result["path"]).read_bytes(), payload)

    def test_roaming_resources_win_when_both_resource_arrays_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            roaming_payload = b"roaming resource"
            legacy_payload = b"legacy media"
            source = root / "source.bin"
            source.write_bytes(roaming_payload)
            row = self.row(legacy_payload)
            row["extra"]["roaming_resources"] = [{
                "kind": "file",
                "type": "file",
                "file_name": "roaming.bin",
                "md5": hashlib.md5(roaming_payload).hexdigest(),
                "size": len(roaming_payload),
            }]
            result = download_attachment(self.provider(source), row, storage=root)
            self.assertEqual(Path(result["path"]).read_bytes(), roaming_payload)
            self.assertEqual(result["name"], "roaming.bin")

    def test_resource_index_selects_one_of_same_named_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = b"second same-named file"
            source = root / "source.bin"
            source.write_bytes(payload)
            row = self.row(b"first same-named file")
            row["extra"]["media"].append({
                "kind": "file",
                "type": "file",
                "file_name": "report.txt",
                "md5": hashlib.md5(payload).hexdigest(),
                "size": len(payload),
            })
            provider = self.provider(source)
            result = download_attachment(
                provider, row, resource_index=1, storage=root)
            self.assertEqual(Path(result["path"]).name, "1")
            self.assertEqual(Path(result["path"]).read_bytes(), payload)
            self.assertEqual(
                provider.media.call_args.args[0]["media"][0]["md5"],
                hashlib.md5(payload).hexdigest(),
            )

    def test_symlink_or_junction_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            storage = root / "storage"
            storage.mkdir()
            outside = root / "outside"
            outside.mkdir()
            link = storage / "attachment-downloads"
            created = False
            if os.name == "nt":
                result = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                created = result.returncode == 0
            if not created:
                try:
                    link.symlink_to(outside, target_is_directory=True)
                    created = True
                except OSError:
                    self.skipTest("directory links are unavailable")
            source = root / "source.bin"
            source.write_bytes(b"fixture")
            row = self.row(b"fixture")
            self.assertFalse(cached_attachment(row, storage=storage)["available"])
            with self.assertRaises(QQDownloadError) as raised:
                download_attachment(self.provider(source), row, storage=storage)
            self.assertEqual(raised.exception.code, "cache_path_unsafe")
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
