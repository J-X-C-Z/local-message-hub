"""Offline media contract tests without reading the user's chats."""

import math
import io
import json
import logging
import os
import sys
import types
from pathlib import Path
import struct
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from .provider import WeChatProvider
from . import provider as provider_module
from . import image_key_store
from .provider import HubMediaDownloader
from .voice import voice_media_result
from .vendor import db as vendor_db
from .vendor.media import MediaDownloader, V1_MAGIC, V2_MAGIC, _jpeg_like, aligned_aes_block_size
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

    def test_provider_dispatches_weflow_file_local_type_variants(self):
        for local_type in (49, 34359738417, 103079215153, 25769803825):
            with self.subTest(local_type=local_type):
                provider = self.provider({"local_type": local_type})
                downloader = Mock()
                downloader.download_file.return_value = "cached-file.bin"
                with patch.object(provider_module, "HubMediaDownloader", return_value=downloader):
                    result = provider.media("fixture", 7, "message_0.db")
                self.assertEqual(result, {
                    "available": True, "path": "cached-file.bin", "reason": None,
                })
                downloader.download_file.assert_called_once_with(
                    "fixture", 7, shard="message_0.db")

    def test_provider_passes_best_cached_image_tier_only_when_requested(self):
        provider = self.provider({"local_type": 3})
        downloader = Mock()
        downloader.download_image.return_value = "cached.jpg"
        with patch.object(provider_module, "HubMediaDownloader", return_value=downloader):
            result = provider.media("fixture", 7, "message_0.db", image_tier="best")
        self.assertTrue(result["available"])
        downloader.download_image.assert_called_once_with(
            "fixture", 7, shard="message_0.db", tier="best")
        with patch.object(provider_module, "HubMediaDownloader", return_value=downloader):
            provider.media("fixture", 7, "message_0.db", image_tier="original")
        downloader.download_image.assert_any_call(
            "fixture", 7, shard="message_0.db", tier="original")
        with self.assertRaisesRegex(ValueError, "Image tier"):
            provider.media("fixture", 7, "message_0.db", image_tier="remote")

    def test_best_cached_image_prefers_complete_original_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "image_h.dat"
            mid = root / "image.dat"
            thumb = root / "image_t.dat"
            original.write_bytes(b"x" * 2048)
            mid.write_bytes(b"synthetic-mid")
            thumb.write_bytes(b"synthetic-thumbnail")
            payloads = {str(original): b"\xff\xd8\xfforiginal",
                        str(mid): b"\xff\xd8\xffmid",
                        str(thumb): b"\xff\xd8\xffthumb"}
            reader = Mock()
            reader.get_message_row.return_value = {
                "local_type": 3, "packed_info": b"a" * 32,
            }
            downloader = MediaDownloader(reader, save_dir=str(root / "hub-cache"))
            with patch.object(downloader, "_image_files", return_value={
                    "original": (str(original), original.stat().st_size),
                    "mid": (str(mid), mid.stat().st_size),
                    "thumb": (str(thumb), thumb.stat().st_size)}), \
                 patch.object(downloader, "decrypt_image", side_effect=lambda path, *_: payloads[path]):
                result = downloader.download_image("fixture", 7, tier="best")
            with patch.object(downloader, "_image_files", return_value={
                    "original": (str(original), 10), "mid": (str(mid), mid.stat().st_size),
                    "thumb": (str(thumb), thumb.stat().st_size)}), \
                 patch.object(downloader, "decrypt_image", side_effect=lambda path, *_: payloads[path]):
                fallback = downloader.download_image("fixture", 7, tier="best")
            self.assertTrue(result.endswith("fixture_7_h.jpg"))
            self.assertTrue(fallback.endswith("fixture_7.jpg"))
            self.assertEqual(Path(result).read_bytes(), payloads[str(original)])
            self.assertEqual(Path(fallback).read_bytes(), payloads[str(mid)])
            self.assertEqual(original.read_bytes(), b"x" * 2048)
            self.assertEqual(mid.read_bytes(), b"synthetic-mid")
            self.assertEqual(thumb.read_bytes(), b"synthetic-thumbnail")

    def test_image_cache_resolver_rejects_symlink_outside_attachment_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = root / "account"
            md5 = "a" * 32
            cache = account / "msg" / "attach" / MediaDownloader(Mock())._chat_md5("fixture")
            image_dir = cache / "2026-10" / "Img"
            image_dir.mkdir(parents=True)
            outside = root / f"{md5}.dat"
            outside.write_bytes(b"private synthetic file")
            linked = image_dir / f"{md5}.dat"
            try:
                linked.symlink_to(outside)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")

            reader = Mock()
            reader.account_dir = str(account)
            downloader = MediaDownloader(reader, save_dir=str(root / "hub-cache"))

            self.assertEqual(downloader._image_files("fixture", md5), {})
            self.assertEqual(outside.read_bytes(), b"private synthetic file")

    def test_image_cache_resolver_accepts_weflow_dat_tier_suffixes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = root / "account"
            reader = Mock()
            reader.account_dir = str(account)
            downloader = MediaDownloader(reader, save_dir=str(root / "hub-cache"))
            md5 = "a" * 32
            image_dir = (account / "msg" / "attach" / downloader._chat_md5("fixture") /
                         "2026-10" / "Img")
            image_dir.mkdir(parents=True)
            variants = {
                "original": ("_h.dat", ".h.dat", "_hd.dat", ".hd.dat"),
                "thumb": ("_t.dat", ".t.dat", "_thumb.dat"),
            }
            for tier, suffixes in variants.items():
                for suffix in suffixes:
                    for candidate in image_dir.glob(f"{md5}*"):
                        candidate.unlink()
                    path = image_dir / f"{md5}{suffix}"
                    path.write_bytes(b"x" * 2048)
                    self.assertEqual(Path(downloader._image_files("fixture", md5)[tier][0]),
                                     path.resolve())
            for candidate in image_dir.glob(f"{md5}*"):
                candidate.unlink()
            for tier, suffixes in variants.items():
                sizes = (2046, 2045, 2048, 2047) if tier == "original" else (100, 102, 101)
                for suffix, size in zip(suffixes, sizes):
                    (image_dir / f"{md5}{suffix}").write_bytes(b"x" * size)
            (image_dir / f"{md5}.dat").write_bytes(b"mid")

            found = downloader._image_files("fixture", md5)

            self.assertEqual(Path(found["original"][0]).name, f"{md5}_hd.dat")
            self.assertEqual(Path(found["thumb"][0]).name, f"{md5}.t.dat")
            self.assertEqual(Path(found["mid"][0]).name, f"{md5}.dat")

            reader.get_message_row.return_value = {
                "local_type": 3, "packed_info": md5.encode("ascii"),
            }
            decoded = b"\xff\xd8\xffsynthetic-image\xff\xd9"
            with patch.object(downloader, "decrypt_image", return_value=decoded) as decrypt:
                best = downloader.download_image("fixture", 7, tier="best")
                decrypt.assert_called_with(str((image_dir / f"{md5}_hd.dat").resolve()), None, None)
                thumb = downloader.download_image("fixture", 7, tier="thumb")
                decrypt.assert_called_with(str((image_dir / f"{md5}.t.dat").resolve()), None, None)
            self.assertTrue(best.endswith("fixture_7_h.jpg"))
            self.assertTrue(thumb.endswith("fixture_7_thumb.jpg"))

            for candidate in image_dir.glob(f"{md5}*"):
                candidate.unlink()
            for suffix in ("_b.dat", ".b.dat", "_w.dat", ".w.dat", "_c.dat", ".c.dat"):
                for candidate in image_dir.glob(f"{md5}*"):
                    candidate.unlink()
                (image_dir / f"{md5}{suffix}").write_bytes(b"x" * 2048)
                self.assertNotIn("thumb", downloader._image_files("fixture", md5))
                with patch.object(downloader, "decrypt_image") as decrypt:
                    self.assertIsNone(downloader.download_image("fixture", 7, tier="thumb"))
                    self.assertIsNone(downloader.download_image("fixture", 7, tier="best"))
                    decrypt.assert_not_called()

    def test_uncached_voice_does_not_attempt_decode(self):
        provider = self.provider({"local_type": 34})
        provider.reader.get_voice_rows.return_value = []
        with patch("integrations.wechat.vendor.media.MediaDownloader.download_voice", return_value=None):
            result = provider.media("fixture", 7, "message_0.db")
        self.assertFalse(result["available"])
        self.assertIsNone(result["path"])
        self.assertEqual(result["reason"], "voice_message_not_found")

    def test_voice_status_is_on_demand_shard_bound_and_redacted(self):
        provider = self.provider({"local_type": 34})
        provider.reader.get_voice_rows.return_value = [
            {"local_id": 7, "shard": "other.db", "server_id": "private-other",
             "download_status": 1},
            {"local_id": 7, "shard": "message_0.db", "server_id": "private-server",
             "download_status": 0},
        ]
        downloader = Mock()
        downloader.download_voice.return_value = None
        downloader._voice_index.return_value = (True, {})
        downloader._voice_reason.return_value = "audio_not_downloaded"
        with patch.object(provider_module, "HubMediaDownloader", return_value=downloader):
            result = provider.media("fixture", 7, "message_0.db")

        self.assertEqual(result, {"available": False, "path": None,
                                  "reason": "audio_not_downloaded", "bytes": 0})
        provider.reader.get_voice_rows.assert_called_once_with("fixture", limit=500, local_id=7)
        downloader._voice_reason.assert_called_once_with("private-server", 0, 0, True)
        self.assertNotIn("server_id", result)
        self.assertNotIn("path", {k: v for k, v in result.items() if k != "path"})

    def test_voice_status_rejects_unscoped_shard(self):
        provider = self.provider(None)
        with self.assertRaisesRegex(ValueError, "shard"):
            provider.voice_status("fixture", 7, "../message_0.db")
        provider.reader.get_voice_rows.assert_not_called()

    def test_voice_row_query_handles_zero_id_and_legacy_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "message_0.db"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE MSG (local_id INTEGER, server_id TEXT, "
                         "real_sender_id TEXT, create_time INTEGER, sort_seq INTEGER, "
                         "local_type INTEGER)")
            conn.executemany("INSERT INTO MSG VALUES (?, ?, ?, ?, ?, ?)", [
                (0, "voice-zero", "sender", 1, 1, 34),
                (1, "voice-one", "sender", 2, 2, 34),
                (2, "text", "sender", 3, 3, 1),
            ])
            conn.commit()
            reader = vendor_db.WeChatDB.__new__(vendor_db.WeChatDB)
            reader._run_msg_query = lambda _user, build: build([(conn, "MSG")])
            try:
                rows = reader.get_voice_rows("fixture", local_id=0)
            finally:
                conn.close()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["local_id"], 0)
        self.assertEqual(rows[0]["server_id"], "voice-zero")
        self.assertIsNone(rows[0]["download_status"])
        self.assertEqual(rows[0]["shard"], "message_0.db")

    def test_shard_is_required(self):
        with self.assertRaises(ValueError):
            self.provider(None).media("fixture", 7, "")
        with self.assertRaises(ValueError):
            self.provider(None).media("fixture", 7, "../message_0.db")

    def _file_media_downloader(self, account_dir, output_dir, row):
        reader = Mock()
        reader.account_dir = str(account_dir)
        reader.get_message_row.return_value = row
        reader._db_files = []
        return MediaDownloader(reader, save_dir=str(output_dir))

    def test_video_copy_is_chunked_atomic_and_keeps_source_unchanged(self):
        message_id = "a" * 32
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = root / "account"
            video_dir = account / "msg" / "video" / "2026-10"
            video_dir.mkdir(parents=True)
            source = video_dir / f"{message_id}.mp4"
            payload = b"synthetic-video" * 200_000
            source.write_bytes(payload)
            output = root / "hub-cache"
            downloader = self._file_media_downloader(
                account, output, {"local_type": 43, "packed_info": message_id.encode(),
                                  "create_time": 1790899200})
            read_sizes = []
            real_open = open

            class TrackedReader:
                def __init__(self, stream):
                    self.stream = stream

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.stream.close()

                def read(self, size):
                    read_sizes.append(size)
                    return self.stream.read(size)

            def tracked_open(path, mode="r", *args, **kwargs):
                stream = real_open(path, mode, *args, **kwargs)
                return TrackedReader(stream) if path == str(source) and mode == "rb" else stream

            with patch("integrations.wechat.vendor.media.open", side_effect=tracked_open, create=True):
                result = downloader.download_video("wxid_fixture", 7)

            self.assertIsNotNone(result)
            self.assertEqual(Path(result).read_bytes(), payload)
            self.assertEqual(source.read_bytes(), payload)
            self.assertTrue(read_sizes)
            self.assertEqual(set(read_sizes), {1024 * 1024})
            self.assertFalse(list(output.glob("*.part")))

    def test_video_resolver_uses_weflow_raw_mp4_fallback(self):
        message_id = "d" * 32
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = root / "account"
            video_dir = account / "msg" / "video" / "2026-10"
            video_dir.mkdir(parents=True)
            source = video_dir / f"{message_id}_raw.mp4"
            payload = b"synthetic-raw-video"
            source.write_bytes(payload)
            downloader = self._file_media_downloader(
                account, root / "hub-cache", {"local_type": 43,
                    "packed_info": message_id.encode(), "create_time": 1790899200})

            result = downloader.download_video("wxid_fixture", 7)

            self.assertIsNotNone(result)
            self.assertEqual(Path(result).read_bytes(), payload)
            self.assertEqual(source.read_bytes(), payload)

    def test_cached_attachment_size_limit_preserves_existing_complete_file(self):
        message_id = "b" * 32
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = root / "account"
            video_dir = account / "msg" / "video" / "2026-10"
            video_dir.mkdir(parents=True)
            source = video_dir / f"{message_id}.mp4"
            source.write_bytes(b"x" * 10)
            output = root / "hub-cache"
            downloader = self._file_media_downloader(
                account, output, {"local_type": 43, "packed_info": message_id.encode(),
                                  "create_time": 1790899200})
            expected = output / "wxid_fixture_7.mp4"
            expected.parent.mkdir()
            expected.write_bytes(b"previous-complete-file")

            with patch("integrations.wechat.vendor.media.MAX_CACHED_ATTACHMENT_BYTES", 9):
                result = downloader.download_video("wxid_fixture", 7)

            self.assertIsNone(result)
            self.assertEqual(expected.read_bytes(), b"previous-complete-file")
            self.assertEqual(list(output.iterdir()), [expected])

    def test_cached_attachment_growth_during_copy_is_rejected_and_cleaned(self):
        message_id = "c" * 32
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = root / "account"
            video_dir = account / "msg" / "video" / "2026-10"
            video_dir.mkdir(parents=True)
            source = video_dir / f"{message_id}.mp4"
            source.write_bytes(b"x" * 10)
            output = root / "hub-cache"
            downloader = self._file_media_downloader(
                account, output, {"local_type": 43, "packed_info": message_id.encode(),
                                  "create_time": 1790899200})
            real_getsize = os.path.getsize

            def stale_size(path):
                return 8 if os.fspath(path) == os.fspath(source) else real_getsize(path)

            with patch("integrations.wechat.vendor.media.MAX_CACHED_ATTACHMENT_BYTES", 9), \
                 patch("integrations.wechat.vendor.media.os.path.getsize", side_effect=stale_size):
                result = downloader.download_video("wxid_fixture", 7)

            self.assertIsNone(result)
            self.assertEqual(list(output.iterdir()), [])

    def test_video_cached_cover_and_thumbnail_are_resolved_without_source_changes(self):
        message_id = "a" * 32
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = root / "account"
            video_dir = account / "msg" / "video" / "2026-10"
            video_dir.mkdir(parents=True)
            cover = video_dir / f"{message_id}.jpg"
            thumbnail = video_dir / f"{message_id}_thumb.jpg"
            cover_bytes = b"\xff\xd8\xffsynthetic-cover"
            thumbnail_bytes = b"\xff\xd8\xffsynthetic-thumbnail"
            cover.write_bytes(cover_bytes)
            thumbnail.write_bytes(thumbnail_bytes)
            output = root / "hub-cache"
            downloader = self._file_media_downloader(
                account, output, {"local_type": 43, "packed_info": message_id.encode(),
                                  "create_time": 1790899200})

            cover_copy = downloader.download_video_thumbnail(
                "wxid_fixture", 7, thumbnail=False)
            thumbnail_copy = downloader.download_video_thumbnail(
                "wxid_fixture", 7, thumbnail=True)

            self.assertEqual(Path(cover_copy).read_bytes(), cover_bytes)
            self.assertEqual(Path(thumbnail_copy).read_bytes(), thumbnail_bytes)
            self.assertEqual(cover.read_bytes(), cover_bytes)
            self.assertEqual(thumbnail.read_bytes(), thumbnail_bytes)

    def test_video_cached_poster_rejects_non_jpeg_and_invalid_rows(self):
        message_id = "a" * 32
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = root / "account"
            video_dir = account / "msg" / "video" / "2026-10"
            video_dir.mkdir(parents=True)
            (video_dir / f"{message_id}.jpg").write_bytes(b"not-an-image")
            output = root / "hub-cache"
            row = {"local_type": 43, "packed_info": message_id.encode(),
                   "create_time": 1790899200}
            downloader = self._file_media_downloader(account, output, row)
            self.assertIsNone(downloader.download_video_thumbnail(
                "wxid_fixture", 7, thumbnail=False))
            downloader.db.get_message_row.return_value = {
                **row, "local_type": 1,
            }
            self.assertIsNone(downloader.download_video_thumbnail(
                "wxid_fixture", 7, thumbnail=False))

    def test_voice_cache_write_is_atomic_and_preserves_previous_file_on_failure(self):
        payload = b"synthetic-silk-payload"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "media_0.db"
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE Name2Id (user_name TEXT);
                INSERT INTO Name2Id VALUES ('fixture');
                CREATE TABLE VoiceInfo (
                    chat_name_id INTEGER, svr_id TEXT, create_time INTEGER, voice_data BLOB
                );
                INSERT INTO VoiceInfo VALUES (1, 'server-fixture', 1, X'73796e7468657469632d73696c6b2d7061796c6f6164');
            """)
            conn.commit()
            conn.close()

            reader = Mock()
            reader.account_dir = str(root / "account")
            reader.get_message_row.return_value = {
                "local_type": 34, "server_id": "server-fixture",
            }
            reader._db_files = [("media_0.db", str(db_path), db_path.stat().st_size)]
            def open_media_db(_rel):
                connection = sqlite3.connect(db_path)
                connection.row_factory = sqlite3.Row
                return connection
            reader._open.side_effect = open_media_db
            output = root / "hub-cache"
            downloader = MediaDownloader(reader, save_dir=str(output))
            expected = output / "fixture_7.silk"
            expected.parent.mkdir()
            expected.write_bytes(b"previous-complete-file")

            result = downloader.download_voice("fixture", 7, shard="message_0.db")

            self.assertEqual(Path(result), expected)
            self.assertEqual(expected.read_bytes(), payload)
            self.assertEqual(list(output.iterdir()), [expected])

            with patch("integrations.wechat.vendor.media.os.replace", side_effect=OSError):
                failed = downloader.download_voice("fixture", 7, shard="message_0.db")

            self.assertIsNone(failed)
            self.assertEqual(expected.read_bytes(), payload)
            self.assertEqual(list(output.iterdir()), [expected])

    def test_voice_cache_streams_sqlite_blob_in_bounded_chunks(self):
        payload = b"synthetic-silk-" * 100_000
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "media_0.db"
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE Name2Id (user_name TEXT);
                INSERT INTO Name2Id VALUES ('fixture');
                CREATE TABLE VoiceInfo (
                    chat_name_id INTEGER, svr_id TEXT, create_time INTEGER, voice_data BLOB
                );
            """)
            conn.execute("INSERT INTO VoiceInfo VALUES (1, 'server-fixture', 1, ?)",
                         (payload,))
            conn.commit()
            conn.close()

            reader = Mock()
            reader.account_dir = str(root / "account")
            reader.get_message_row.return_value = {
                "local_type": 34, "server_id": "server-fixture",
            }
            reader._db_files = [("media_0.db", str(db_path), db_path.stat().st_size)]
            def open_media_db(_rel):
                connection = sqlite3.connect(db_path)
                connection.row_factory = sqlite3.Row
                return connection
            reader._open.side_effect = open_media_db
            output = root / "hub-cache"
            downloader = MediaDownloader(reader, save_dir=str(output))

            with patch("integrations.wechat.vendor.media.MEDIA_COPY_CHUNK_SIZE", 1024):
                result = downloader.download_voice("fixture", 7, shard="message_0.db")

            self.assertEqual(Path(result).read_bytes(), payload)
            self.assertFalse(list(output.glob("*.part")))

    def test_file_resolver_rejects_symlinks_outside_account_media_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = root / "account"
            file_dir = account / "msg" / "file" / "2026-10"
            file_dir.mkdir(parents=True)
            outside = root / "private.txt"
            outside.write_bytes(b"outside-account")
            link = file_dir / "fixture.txt"
            try:
                os.symlink(outside, link)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink creation unavailable: {exc}")
            downloader = self._file_media_downloader(
                account, root / "hub-cache", {"local_type": 49, "server_id": "fixture",
                                               "create_time": 1790899200})
            with patch.object(downloader, "_file_name", return_value="fixture.txt"):
                result = downloader.download_file("wxid_fixture", 9)
            self.assertIsNone(result)
            self.assertEqual(outside.read_bytes(), b"outside-account")
            self.assertFalse((root / "hub-cache").exists())

    def test_file_resolver_copies_only_the_matching_account_cache_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = root / "account"
            file_dir = account / "msg" / "file" / "2026-10"
            file_dir.mkdir(parents=True)
            source = file_dir / "fixture.txt"
            source.write_bytes(b"synthetic-account-file")
            output = root / "hub-cache"
            downloader = self._file_media_downloader(
                account, output, {"local_type": 49, "server_id": "fixture",
                                  "create_time": 1790899200})
            with patch.object(downloader, "_file_name", return_value="fixture.txt"):
                result = downloader.download_file("wxid_fixture", 9)
            self.assertIsNotNone(result)
            self.assertEqual(Path(result).read_bytes(), b"synthetic-account-file")
            self.assertEqual(source.read_bytes(), b"synthetic-account-file")

    def test_file_resolver_does_not_fall_back_to_another_month(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = root / "account"
            wrong_month = account / "msg" / "file" / "2026-09"
            wrong_month.mkdir(parents=True)
            (wrong_month / "fixture.txt").write_bytes(b"other-message-file")
            downloader = self._file_media_downloader(
                account, root / "hub-cache", {"local_type": 49, "server_id": "fixture",
                                               "create_time": 1790899200})
            with patch.object(downloader, "_file_name", return_value="fixture.txt"), \
                 patch("integrations.wechat.vendor.media.os.walk",
                       side_effect=AssertionError("cache traversal must stay bounded")):
                result = downloader.download_file("wxid_fixture", 9)

            self.assertIsNone(result)
            self.assertFalse((root / "hub-cache").exists())

    def test_file_resolver_accepts_weflow_file_local_type_variants_only(self):
        file_local_types = (49, 34359738417, 103079215153, 25769803825)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account = root / "account"
            file_dir = account / "msg" / "file" / "2026-10"
            file_dir.mkdir(parents=True)
            source = file_dir / "fixture.txt"
            source.write_bytes(b"synthetic-account-file")
            for local_type in file_local_types:
                with self.subTest(local_type=local_type):
                    row = {"local_type": local_type, "server_id": "fixture"}
                    downloader = self._file_media_downloader(
                        account, root / f"hub-cache-{local_type}",
                        {**row, "create_time": 1790899200})
                    with patch.object(downloader, "_file_name", return_value="fixture.txt"):
                        result = downloader.download_file(
                            "wxid_fixture", 9, shard="message_0.db")
                    self.assertIsNotNone(result)
                    self.assertEqual(Path(result).read_bytes(), b"synthetic-account-file")
                    downloader.db.get_message_row.assert_called_once_with(
                        "wxid_fixture", 9, shard="message_0.db")

            unsupported = self._file_media_downloader(
                account, root / "unsupported-cache", {"local_type": 8589934592049,
                                                         "server_id": "fixture"})
            with patch.object(unsupported, "_file_name", return_value="fixture.txt"):
                self.assertIsNone(unsupported.download_file(
                    "wxid_fixture", 9, shard="message_0.db"))
            unsupported.db.get_message_row.assert_called_once_with(
                "wxid_fixture", 9, shard="message_0.db")

    def test_output_name_cannot_escape_media_cache_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloader = MediaDownloader(Mock(), save_dir=str(root / "cache"))
            output = Path(downloader._out(None, "../outside.txt"))
            self.assertEqual(output.parent, (root / "cache").resolve())
            self.assertEqual(output.name, "outside.txt")

    def test_v1_image_decrypts_with_explicit_xor_key(self):
        from .vendor.media import MediaDownloader

        image = b"\xff\xd8\xffsynthetic-jpeg\xff\xd9"
        key = 0x5A
        encrypted = V1_MAGIC + (b"0" * 16) + bytes(byte ^ key for byte in image)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "image.dat"
            source.write_bytes(encrypted)
            result = MediaDownloader(Mock(), save_dir=directory).decrypt_image(
                str(source), xor_key=key)
        self.assertEqual(result, image)

    def test_v1_image_rejects_wrong_xor_key(self):
        image = b"\xff\xd8\xffsynthetic-jpeg\xff\xd9"
        encrypted = V1_MAGIC + (b"0" * 16) + bytes(byte ^ 0x5A for byte in image)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "image.dat"
            source.write_bytes(encrypted)
            with self.assertRaisesRegex(ValueError, "V1 图片解密结果"):
                MediaDownloader(Mock(), save_dir=directory).decrypt_image(
                    str(source), xor_key=0x00)

    def test_image_decryption_rejects_oversized_cache_before_reading(self):
        from .vendor import media as media_module

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "oversized.dat"
            source.write_bytes(b"x" * 17)
            downloader = MediaDownloader(Mock(), save_dir=directory)
            with patch.object(media_module, "MAX_ENCRYPTED_IMAGE_BYTES", 16):
                with self.assertRaisesRegex(ValueError, "解密上限"):
                    downloader.decrypt_image(str(source))

    def test_image_decryption_bounds_read_if_cache_grows_after_stat(self):
        from .vendor import media as media_module

        class GrowingFile:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, limit=-1):
                self.limit = limit
                return b"x" * limit

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "growing.dat"
            source.write_bytes(b"x")
            downloader = MediaDownloader(Mock(), save_dir=directory)
            fake_open = GrowingFile()
            with patch.object(media_module, "MAX_ENCRYPTED_IMAGE_BYTES", 16), \
                    patch.object(media_module.os.path, "getsize", return_value=1), \
                    patch("builtins.open", return_value=fake_open):
                with self.assertRaisesRegex(ValueError, "解密上限"):
                    downloader.decrypt_image(str(source))
            self.assertEqual(fake_open.limit, 17)

    def test_wechat_image_formats_keep_previewable_extensions(self):
        fixtures = {
            "webp": b"RIFF\x04\x00\x00\x00WEBPVP8 " + b"fixture",
            "bmp": b"BM" + b"\x00" * 14 + b"fixture",
        }
        with tempfile.TemporaryDirectory() as directory:
            downloader = MediaDownloader(Mock(), save_dir=directory)
            for extension, payload in fixtures.items():
                with self.subTest(extension=extension):
                    path = downloader._write_decrypted(payload, "fixture", None)
                    self.assertEqual(Path(path).suffix, "." + extension)
                    self.assertEqual(Path(path).read_bytes(), payload)

    def test_original_completeness_checks_webp_and_bmp_container_lengths(self):
        webp_body = b"WEBPVP8 " + b"fixture"
        webp = b"RIFF" + struct.pack("<I", len(webp_body)) + webp_body
        bmp = b"BM" + struct.pack("<I", 22) + b"\x00" * 12 + b"data"

        self.assertTrue(MediaDownloader._image_complete(webp))
        self.assertFalse(MediaDownloader._image_complete(webp[:-1]))
        self.assertTrue(MediaDownloader._image_complete(bmp))
        self.assertFalse(MediaDownloader._image_complete(bmp[:-1]))

    def test_original_completeness_allows_only_known_image_footers(self):
        jpeg = b"\xff\xd8\xff" + b"synthetic-jpeg" + b"\xff\xd9"
        png_end = b"\x49\x45\x4e\x44\xae\x42\x60\x82"
        png = b"\x89PNG\r\n\x1a\n" + b"synthetic-png" + png_end

        self.assertTrue(MediaDownloader._image_complete(jpeg + b"footer"))
        self.assertFalse(MediaDownloader._image_complete(jpeg + b"x" * 33))
        self.assertTrue(MediaDownloader._image_complete(png + b"footer"))
        self.assertFalse(MediaDownloader._image_complete(
            b"\x89PNG\r\n\x1a\n" + b"synthetic-pngIEND" + b"x" * 8))

    def test_original_gif_completeness_requires_trailer_before_zero_padding(self):
        valid = b"GIF89a" + b"\x00" * 10 + b"\x3b" + b"\x00" * 4
        truncated_with_semicolon = b"GIF89a" + b"\x00" * 10 + b"\x3b" + b"x" * 4

        self.assertTrue(MediaDownloader._image_complete(valid))
        self.assertFalse(MediaDownloader._image_complete(truncated_with_semicolon))

    def test_legacy_xor_image_recognizes_webp_signature(self):
        image = b"RIFF\x04\x00\x00\x00WEBPVP8 " + b"fixture"
        key = 0x88
        encrypted = bytes(value ^ key for value in image)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "image.dat"
            source.write_bytes(encrypted)
            result = MediaDownloader(Mock(), save_dir=directory).decrypt_image(str(source))
        self.assertEqual(result, image)

    def test_legacy_xor_image_recognizes_bmp_signature(self):
        image = b"BM" + struct.pack("<I", 22) + b"\x00" * 12 + b"fixture"
        key = 0x88
        encrypted = bytes(value ^ key for value in image)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "image.dat"
            source.write_bytes(encrypted)
            result = MediaDownloader(Mock(), save_dir=directory).decrypt_image(str(source))
        self.assertEqual(result, image)

    def test_legacy_xor_image_uses_account_cfg_dword_key(self):
        image = b"\xff\xd8\xffsynthetic-jpeg\xff\xd9"
        key = 0x5A
        encrypted = bytes(value ^ key for value in image)
        db = Mock()
        db.cfg_dword = 0x12345600 | key
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "image.dat"
            source.write_bytes(encrypted)
            result = MediaDownloader(db, save_dir=directory).decrypt_image(str(source))
        self.assertEqual(result, image)

    def test_legacy_xor_image_rejects_non_webp_riff_payload(self):
        self.assertFalse(_jpeg_like(b"RIFF\x00\x00\x00\x00WAVE"))

    def test_v2_image_decrypts_with_explicit_keys_and_strips_footer(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from .vendor.media import MediaDownloader

        image = b"\xff\xd8\xff" + b"synthetic-jpeg-data" * 3 + b"\xff\xd9"
        aes_key = b"0123456789abcdef"
        xor_key = 0x37
        aes_size = 16
        aes_plain = image[:aes_size]
        padded = aes_plain + bytes([16]) * 16
        encryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).encryptor()
        encrypted_aes = encryptor.update(padded) + encryptor.finalize()
        raw = image[aes_size:-2]
        tail = image[-2:] + b"synthetic-footer"
        encrypted_tail = bytes(byte ^ xor_key for byte in tail)
        payload = (V2_MAGIC + struct.pack("<LL", aes_size, len(encrypted_tail)) + b"\x00" +
                   encrypted_aes + raw + encrypted_tail)

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "image.dat"
            source.write_bytes(payload)
            result = MediaDownloader(Mock(), save_dir=directory).decrypt_image(
                str(source), aes_key=aes_key.decode(), xor_key=xor_key)
        self.assertEqual(result, image)

    def test_v2_image_rejects_wrong_key_instead_of_caching_garbage(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        image = b"\xff\xd8\xff" + b"synthetic-jpeg-payload" * 2 + b"\xff\xd9"
        key = b"0123456789abcdef"
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        ciphertext = encryptor.update(image[:16] + bytes([16]) * 16) + encryptor.finalize()
        payload = V2_MAGIC + struct.pack("<LL", 16, 0) + b"\x00" + ciphertext + image[16:]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "image.dat"
            source.write_bytes(payload)
            with self.assertRaisesRegex(ValueError, "不是受支持的图片格式"):
                MediaDownloader(Mock(), save_dir=directory).decrypt_image(
                    str(source), aes_key="fedcba9876543210", xor_key=0)

    def test_v2_image_rejects_section_lengths_outside_container(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        key = b"0123456789abcdef"
        encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        ciphertext = encryptor.update(b"A" * 16 + b"\x10" * 16) + encryptor.finalize()
        malformed = (
            V2_MAGIC + struct.pack("<LL", 16, 1) + b"\x00" + ciphertext,
            V2_MAGIC + struct.pack("<LL", 16, 100) + b"\x00" + ciphertext,
        )
        with tempfile.TemporaryDirectory() as directory:
            downloader = MediaDownloader(Mock(), save_dir=directory)
            for index, payload in enumerate(malformed):
                with self.subTest(index=index):
                    source = Path(directory) / f"malformed-{index}.dat"
                    source.write_bytes(payload)
                    with self.assertRaisesRegex(ValueError, "V2 图片容器"):
                        downloader.decrypt_image(str(source), aes_key=key.decode(), xor_key=0)

    def test_hub_media_key_scanning_is_disabled_and_cache_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            account_dir = root / "account"
            image_dir = account_dir / "msg" / "attach" / "chat" / "2026-10" / "Img"
            image_dir.mkdir(parents=True)
            probe = (V2_MAGIC + struct.pack("<LL", 16, 0) + b"\x00" + b"X" * 32)
            (image_dir / "fixture.dat").write_bytes(probe)
            reader = Mock()
            reader.account = "synthetic-account"
            reader.wxid = "synthetic-wxid"
            reader.cfg_dword = None
            reader.account_dir = str(account_dir)
            reader.workdir = str(root / "work")
            with patch.object(provider_module, "RUNTIME", root / "runtime"), \
                    patch.object(provider_module, "CONFIG", root / "wechat.json"):
                downloader = HubMediaDownloader(reader, save_dir=str(root / "media"))
                with patch.object(image_key_store, "_crypt", side_effect=lambda data, protect:
                                  __import__("base64").b64encode(data) if protect else
                                  __import__("base64").b64decode(data)):
                    downloader._persist_key("0123456789abcdef")
                    cache_path = next((root / "runtime" / "cache" / "image_keys").glob("*.dpapi"))
                    self.assertNotIn(b"0123456789abcdef", cache_path.read_bytes())
                    self.assertEqual(downloader._load_persisted_key(), "0123456789abcdef")
                    cache_path.unlink()
                self.assertFalse((root / "wechat.json").exists())
                with patch.object(downloader, "_dbg_last_dat", return_value=None, create=True):
                    self.assertIsNone(downloader.detect_image_key())

    def test_v2_block_size_uses_pkcs7_full_block(self):
        self.assertEqual(aligned_aes_block_size(16), 32)

    def test_wxgf_conversion_uses_optional_ffmpeg_wrapper_fallback(self):
        downloader = MediaDownloader.__new__(MediaDownloader)
        ffmpeg = types.SimpleNamespace(get_ffmpeg_exe=lambda: "local-ffmpeg.exe")

        def convert(command, **_kwargs):
            Path(command[-1]).write_bytes(bytes((255, 216, 255)) + b"synthetic-jpeg")
            return types.SimpleNamespace(returncode=0)

        wxgf = b"wxgf" + bytes((0, 0, 0, 1)) + b"synthetic-hevc"
        with patch("integrations.wechat.vendor.media.shutil.which", return_value=None), patch.dict(
                sys.modules, {"imageio_ffmpeg": ffmpeg}), patch(
                "subprocess.run", side_effect=convert) as run:
            result = downloader._wxgf_to_jpg(wxgf)

        self.assertEqual(result, bytes((255, 216, 255)) + b"synthetic-jpeg")
        self.assertEqual(run.call_args.args[0][0], "local-ffmpeg.exe")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-fs") + 1], str(64 * 1024 * 1024 + 1))

    def test_ffmpeg_path_prefers_existing_bundled_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            bundled = Path(directory) / "ffmpeg.exe"
            bundled.write_bytes(b"synthetic executable")
            with patch.dict(os.environ, {"LMH_FFMPEG_PATH": str(bundled)}), patch(
                    "integrations.wechat.vendor.media.shutil.which", return_value="system-ffmpeg"):
                self.assertEqual(MediaDownloader._ffmpeg_exe(), str(bundled))

    def test_ffmpeg_path_ignores_missing_bundled_binary(self):
        with patch.dict(os.environ, {"LMH_FFMPEG_PATH": "missing-ffmpeg.exe"}), patch(
                "integrations.wechat.vendor.media.shutil.which", return_value="system-ffmpeg"):
            self.assertEqual(MediaDownloader._ffmpeg_exe(), "system-ffmpeg")

    def test_wxgf_decode_writes_previewable_jpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            downloader = MediaDownloader.__new__(MediaDownloader)
            with patch.object(downloader, "_wxgf_to_jpg", return_value=b"\xff\xd8\xfffixture"):
                path = downloader._write_decrypted(
                    b"wxgf" + bytes((0, 0, 0, 1)) + b"synthetic-hevc", "fixture", directory)
            self.assertEqual(Path(path).suffix, ".jpg")
            self.assertEqual(Path(path).read_bytes(), b"\xff\xd8\xfffixture")

    def test_wxgf_decode_failure_keeps_original_downloadable(self):
        source = b"wxgf" + bytes((0, 0, 0, 1)) + b"synthetic-hevc"
        with tempfile.TemporaryDirectory() as directory:
            downloader = MediaDownloader.__new__(MediaDownloader)
            with patch.object(downloader, "_wxgf_to_jpg", return_value=None):
                path = downloader._write_decrypted(source, "fixture", directory)
            self.assertEqual(Path(path).suffix, ".wxgf")
            self.assertEqual(Path(path).read_bytes(), source)

    def test_wxgf_extract_hevc_accepts_three_and_four_byte_annex_b_prefixes(self):
        downloader = MediaDownloader.__new__(MediaDownloader)
        for prefix in (b"\x00\x00\x01", b"\x00\x00\x00\x01"):
            with self.subTest(prefix_length=len(prefix)):
                stream = b"wxgf" + prefix + b"synthetic-hevc"
                self.assertEqual(downloader._extract_hevc(stream), stream[4:])

    def test_wxgf_conversion_retries_vps_group_candidates(self):
        downloader = MediaDownloader.__new__(MediaDownloader)
        ffmpeg = types.SimpleNamespace(get_ffmpeg_exe=lambda: "local-ffmpeg.exe")
        prefix = b"\x00\x00\x00\x01"
        vps1 = b"\x40\x01" + b"a" * 64
        frame1 = b"\x26\x01" + b"b" * 64
        vps2 = b"\x40\x01" + b"c" * 64
        frame2 = b"\x26\x01" + b"d" * 64
        wxgf = b"wxgf" + prefix + vps1 + prefix + frame1 + prefix + vps2 + prefix + frame2
        tried = []

        def convert(command, **_kwargs):
            tried.append(Path(command[command.index("-i") + 1]).read_bytes())
            if len(tried) == 2:
                Path(command[-1]).write_bytes(bytes((255, 216, 255)) + b"synthetic-jpeg")
                return types.SimpleNamespace(returncode=0)
            return types.SimpleNamespace(returncode=1)

        with patch("integrations.wechat.vendor.media.shutil.which", return_value=None), patch.dict(
                sys.modules, {"imageio_ffmpeg": ffmpeg}), patch(
                "subprocess.run", side_effect=convert):
            result = downloader._wxgf_to_jpg(wxgf)

        self.assertEqual(result, bytes((255, 216, 255)) + b"synthetic-jpeg")
        self.assertEqual(len(tried), 2)
        self.assertEqual(tried[0], prefix + vps1 + prefix + frame1)
        self.assertEqual(tried[1], prefix + vps2 + prefix + frame2)

    def test_wxgf_prefers_embedded_jpeg_and_png_signatures(self):
        downloader = MediaDownloader.__new__(MediaDownloader)
        with tempfile.TemporaryDirectory() as directory:
            for stem, image, extension in (
                    ("jpeg", b"\xff\xd8\xffsynthetic-jpeg", ".jpg"),
                    ("png", b"\x89PNG\r\n\x1a\nsynthetic-png", ".png")):
                with self.subTest(extension=extension):
                    path = downloader._write_decrypted(
                        b"wxgf" + b"container-data" + image, stem, directory)
                    self.assertTrue(path.endswith(extension))
                    self.assertEqual(Path(path).read_bytes(), image)

    def test_wxgf_extracts_embedded_gif_webp_and_bmp_signatures(self):
        downloader = MediaDownloader.__new__(MediaDownloader)
        with tempfile.TemporaryDirectory() as directory:
            fixtures = (
                ("gif", b"GIF89a" + b"synthetic-gif", ".gif"),
                ("webp", b"RIFF\x04\x00\x00\x00WEBPsynthetic-webp", ".webp"),
                ("bmp", b"BM" + b"\x00" * 14 + b"synthetic-bmp", ".bmp"),
            )
            for stem, image, extension in fixtures:
                with self.subTest(extension=extension):
                    path = downloader._write_decrypted(
                        b"wxgf" + b"container-data" + image, stem, directory)
                    self.assertTrue(path.endswith(extension))
                    self.assertEqual(Path(path).read_bytes(), image)

    def test_wxgf_conversion_rejects_oversized_output(self):
        downloader = MediaDownloader.__new__(MediaDownloader)
        ffmpeg = types.SimpleNamespace(get_ffmpeg_exe=lambda: "local-ffmpeg.exe")

        def convert(command, **_kwargs):
            Path(command[-1]).write_bytes(bytes((255, 216, 255)) + b"x" * 32)
            return types.SimpleNamespace(returncode=0)

        wxgf = b"wxgf" + bytes((0, 0, 0, 1)) + b"synthetic-hevc"
        with patch("integrations.wechat.vendor.media.shutil.which", return_value=None), patch.dict(
                sys.modules, {"imageio_ffmpeg": ffmpeg}), patch(
                "subprocess.run", side_effect=convert), patch(
                "integrations.wechat.vendor.media.MAX_DECODED_IMAGE_BYTES", 16):
            self.assertIsNone(downloader._wxgf_to_jpg(wxgf))

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

    def test_oversized_silk_is_not_read_into_decoder(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "fixture.silk"
            source.write_bytes(b"123")
            decoder = types.SimpleNamespace(decode=Mock(side_effect=AssertionError("must not decode")))
            with patch.dict(sys.modules, {"pilk_nogil": decoder}), \
                    patch("integrations.wechat.voice.MAX_SILK_BYTES", 2):
                result = voice_media_result(str(source))

        self.assertEqual(result["reason"], "voice_decode_limit_exceeded")
        self.assertFalse(result["playable"])
        decoder.decode.assert_not_called()

    def test_oversized_decoded_pcm_retains_original_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "fixture.silk"
            source.write_bytes(b"synthetic silk")
            decoder = types.SimpleNamespace(decode=Mock(return_value=b"1234"))
            with patch.dict(sys.modules, {"pilk_nogil": decoder}), \
                    patch("integrations.wechat.voice.MAX_PCM_BYTES", 2):
                result = voice_media_result(str(source))

            self.assertEqual(result["reason"], "voice_decode_limit_exceeded")
            self.assertEqual(result["path"], str(source))
            self.assertFalse(Path(directory, "fixture.wav").exists())
            self.assertFalse(Path(directory, "fixture.wav.tmp").exists())

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
