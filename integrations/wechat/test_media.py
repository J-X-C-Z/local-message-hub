"""Offline media contract tests without reading the user's chats."""

import math
import io
import logging
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from .provider import WeChatProvider
from .voice import voice_media_result
from .vendor import db as vendor_db
from .vendor.diagnostics import diagnostic_print


class MediaTests(unittest.TestCase):
    def provider(self, row):
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = Mock()
        provider.reader.get_message_row.return_value = row
        return provider

    def test_missing_message_has_no_path(self):
        result = self.provider(None).media("fixture", 7, "message_0.db")
        self.assertFalse(result["available"])
        self.assertIsNone(result["path"])
        self.assertEqual(result["reason"], "message_not_found")

    def test_message_ids_are_unique_across_conversations(self):
        provider = self.provider(None)
        provider.reader.get_messages.return_value = [{
            "shard": "message_0.db", "local_id": 1, "sender_username": "fixture",
            "is_self": False, "create_time": 1, "sort_seq": 1,
            "type": "text", "content": "fixture", "local_type": 1,
        }]
        first = provider.messages("conversation-a")[0]
        second = provider.messages("conversation-b")[0]
        self.assertEqual(first["shard"], second["shard"])
        self.assertEqual(first["local_id"], second["local_id"])
        self.assertNotEqual(first["id"], second["id"])
        store = {message["id"]: message for message in [first, second]}
        self.assertEqual(len(store), 2)

    def test_missing_media_diagnostic_does_not_emit_private_ids(self):
        reader = vendor_db.WeChatDB.__new__(vendor_db.WeChatDB)
        reader._run_msg_query = Mock(return_value=[])
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        vendor_db.wxlog.addHandler(handler)
        try:
            result = reader.get_message_row("sensitive_session", 91827364,
                                            shard="sensitive_shard.db")
        finally:
            vendor_db.wxlog.removeHandler(handler)
        self.assertIsNone(result)
        text = output.getvalue()
        self.assertIn("WeChat local reader diagnostic", text)
        for secret in ("sensitive_session", "sensitive_shard.db", "91827364"):
            self.assertNotIn(secret, text)

    def test_print_diagnostic_does_not_emit_account_or_key_path(self):
        output = io.StringIO()
        with patch("integrations.wechat.vendor.diagnostics.sys.stderr", output):
            diagnostic_print("sensitive_account", "sensitive_key_path")
        self.assertIn("Local reader diagnostic", output.getvalue())
        self.assertNotIn("sensitive_account", output.getvalue())
        self.assertNotIn("sensitive_key_path", output.getvalue())

    def test_text_is_not_media(self):
        result = self.provider({"local_type": 1}).media("fixture", 7, "message_0.db")
        self.assertFalse(result["available"])
        self.assertIsNone(result["path"])
        self.assertEqual(result["reason"], "unsupported_media_type")

    def test_uncached_voice_does_not_attempt_decode(self):
        with patch("integrations.wechat.vendor.media.MediaDownloader.download_voice", return_value=None):
            result = self.provider({"local_type": 34}).media("fixture", 7, "message_0.db")
        self.assertFalse(result["available"])
        self.assertIsNone(result["path"])
        self.assertEqual(result["reason"], "not_cached_or_key_unavailable")

    def test_shard_is_required(self):
        with self.assertRaises(ValueError):
            self.provider(None).media("fixture", 7, "")
        with self.assertRaises(ValueError):
            self.provider(None).media("fixture", 7, "../message_0.db")

    def test_decoder_missing_retains_silk(self):
        with patch.dict("sys.modules", {"pilk_nogil": None}):
            result = voice_media_result("fixture.silk")
        self.assertTrue(result["available"])
        self.assertFalse(result["playable"])
        self.assertEqual(result["path"], "fixture.silk")
        self.assertEqual(result["reason"], "voice_decoder_unavailable")

    def test_corrupt_voice_retains_silk(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "fixture.silk"
            source.write_bytes(b"invalid fixture")
            result = voice_media_result(str(source))
            self.assertFalse(result["playable"])
            self.assertEqual(result["reason"], "voice_decode_failed")
            self.assertEqual(result["path"], str(source))
            self.assertTrue(source.exists())

    def test_voice_becomes_valid_wav(self):
        import pilk_nogil

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "fixture.silk"
            pcm = b"".join(struct.pack("<h", int(2000 * math.sin(i * math.tau * 440 / 24000)))
                           for i in range(4800))
            source.write_bytes(pilk_nogil.encode(pcm, None, pcm_rate=24000, tencent=True))
            with patch("integrations.wechat.vendor.media.MediaDownloader.download_voice", return_value=str(source)):
                result = self.provider({"local_type": 34}).media("fixture", 7, "message_0.db")
            self.assertTrue(result["available"])
            self.assertTrue(result["playable"])
            self.assertEqual(result["mime_type"], "audio/wav")
            self.assertEqual(result["original_path"], str(source))
            self.assertIsNone(result["reason"])
            with wave.open(result["path"], "rb") as output:
                self.assertEqual(output.getframerate(), 24000)
                self.assertEqual(output.getnchannels(), 1)
                self.assertEqual(output.getsampwidth(), 2)
                self.assertGreater(output.getnframes(), 0)


if __name__ == "__main__":
    unittest.main()
