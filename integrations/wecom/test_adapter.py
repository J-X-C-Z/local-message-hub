import importlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch


class WeComMediaResolverTest(unittest.TestCase):
    def setUp(self):
        # Keep this resolver unit test independent of the optional decryption runtime.
        engine = types.ModuleType("integrations.wecom.engine")
        engine.OUT = ""
        engine.cache_root = ""
        engine.SyncEngine = object
        engine.compute_stats = lambda *_args: {}
        engine.strip_internal = lambda row: row
        self.engine_patch = patch.dict(sys.modules, {engine.__name__: engine})
        self.engine_patch.start()
        self.addCleanup(self.engine_patch.stop)
        self.adapter = importlib.import_module(".adapter", package="integrations.wecom")

    def test_snapshot_failures_map_to_safe_diagnostic_codes(self):
        cases = (
            ("未找到可解密的库（客户端可能未运行过）", "database_not_decrypted"),
            ("未找到可用 message.db", "message_database_missing"),
            ("secret path and SQL detail", "snapshot_parse_failed"),
        )
        for engine_error, expected_code in cases:
            with self.subTest(code=expected_code):
                engine = types.SimpleNamespace(
                    run_once=lambda: None,
                    info=lambda: {"error": engine_error},
                )
                with patch.object(self.adapter, "SyncEngine", return_value=engine):
                    with self.assertRaises(self.adapter.WeComSnapshotError) as caught:
                        self.adapter.WeComAdapter().refresh()
                self.assertEqual(caught.exception.code, expected_code)
                self.assertNotIn("secret path", str(caught.exception))
                self.assertNotIn("SQL detail", caught.exception.safe_message)

    def test_resource_index_selects_exact_cached_attachment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "images" / "first.png"
            second = root / "files" / "second.pdf"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"first fixture")
            second.write_bytes(b"second fixture")

            adapter = object.__new__(self.adapter.WeComAdapter)
            adapter.engine = types.SimpleNamespace(cache_root=str(root))
            adapter._raw_messages = lambda: [{
                "message_id": "synthetic-message",
                "media": [
                    {"p": "images/first.png", "n": "first.png"},
                    {"p": "files/second.pdf", "n": "second.pdf"},
                ],
            }]
            with patch.object(self.adapter, "OUT", str(root)):
                result = adapter.media("synthetic-message", resource_index=1)
                missing = adapter.media("synthetic-message", resource_index=2)

            self.assertEqual(result, {
                "available": True, "path": "files/second.pdf", "name": "second.pdf", "reason": None,
            })
            self.assertEqual(missing["reason"], "resource_not_found")

    def test_explicit_unsafe_or_missing_entry_does_not_fall_back_to_another_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / "safe.png"
            image.write_bytes(b"fixture")
            adapter = object.__new__(self.adapter.WeComAdapter)
            adapter.engine = types.SimpleNamespace(cache_root=str(root))
            adapter._raw_messages = lambda: [{
                "message_id": "synthetic-message",
                "media": [
                    {"p": "../outside.png", "n": "outside.png"},
                    {"p": "safe.png", "n": "safe.png"},
                ],
            }]
            with patch.object(self.adapter, "OUT", str(root)):
                result = adapter.media("synthetic-message", resource_index=0)

            self.assertEqual(result["reason"], "media_file_missing_or_unsafe")

    def test_local_cache_mapping_resolves_only_inside_configured_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cached = root / 'Cache' / 'Image' / 'local.png'
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b'local fixture')
            adapter = object.__new__(self.adapter.WeComAdapter)
            adapter.engine = types.SimpleNamespace(cache_root=str(root))
            adapter._raw_messages = lambda: [{'message_id': 'local-message', 'media': [
                {'_local_cache_path': 'Cache/Image/local.png', 'n': 'local.png'}]}]

            result = adapter.media('local-message')

            self.assertTrue(result['available'])
            self.assertEqual(Path(result['path']), cached.resolve())

    def test_cached_silk_attachment_uses_shared_voice_decoder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            voice = root / "voice.silk"
            payload = b"#!SILK" + b"fixture"
            voice.write_bytes(payload)
            output_dir = root / "hub-voice-cache"
            adapter = object.__new__(self.adapter.WeComAdapter)
            adapter.engine = types.SimpleNamespace(cache_root=str(root))
            adapter.voice_cache_dir = output_dir
            adapter._raw_messages = lambda: [{"message_id": "voice-message", "media": [
                {"p": "voice.silk", "n": "voice.silk"},
            ]}]
            decoder = types.SimpleNamespace(decode=Mock(return_value=b"1234"))
            with patch.object(self.adapter, "OUT", str(root)), \
                    patch.dict(sys.modules, {"pilk_nogil": decoder}):
                result = adapter.media("voice-message")

            self.assertTrue(result["playable"])
            self.assertEqual(Path(result["path"]).parent, output_dir)
            self.assertEqual(voice.read_bytes(), payload)
            self.assertEqual({path.name for path in root.iterdir()}, {"voice.silk", "hub-voice-cache"})
            self.assertEqual(list(output_dir.glob("*.wav.tmp")), [])

    def test_restart_restores_media_without_running_source_sync(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / 'Cache' / 'Image' / 'cached.png'
            image.parent.mkdir(parents=True)
            image.write_bytes(b'cached fixture')
            restored = {'same-message': [{'_local_cache_path': 'Cache/Image/cached.png', 'n': 'cached.png'}]}
            engine = types.SimpleNamespace(
                cache_root=str(root), restore_cached_media_context=Mock(return_value=restored),
                run_once=Mock(side_effect=AssertionError('source sync must not run')))
            with patch.object(self.adapter, 'SyncEngine', return_value=engine):
                adapter = self.adapter.WeComAdapter()
            self.assertTrue(adapter.restore_cached_media())
            self.assertEqual(Path(adapter.media('same-message')['path']), image.resolve())
            self.assertEqual(adapter.media('other-message')['reason'], 'message_not_found')
            engine.run_once.assert_not_called()

            engine.restore_cached_media_context.return_value = None
            with patch.object(self.adapter, 'SyncEngine', return_value=engine):
                missing = self.adapter.WeComAdapter()
            self.assertFalse(missing.restore_cached_media())
            self.assertFalse(missing._loaded)
            engine.run_once.assert_not_called()

    def test_media_keeps_internal_path_while_public_rows_are_redacted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / 'Cache' / 'Image' / 'cached.png'
            image.parent.mkdir(parents=True)
            image.write_bytes(b'cached fixture')
            adapter = object.__new__(self.adapter.WeComAdapter)
            adapter._loaded = True
            adapter._messages_released = False
            adapter.engine = types.SimpleNamespace(cache_root=str(root), iter_messages=lambda: iter([
                {'message_id': 'same-message', 'media': [{'_local_cache_path': 'Cache/Image/cached.png'}]}]))
            with patch.object(self.adapter, 'strip_internal', side_effect=lambda row: {
                    **row, 'media': [{}]}):
                self.assertEqual(list(adapter._messages())[0]['media'], [{}])
                self.assertTrue(adapter.media('same-message')['available'])


class WeComGroupRosterTest(unittest.TestCase):
    def setUp(self):
        engine = types.ModuleType("integrations.wecom.engine")
        engine.OUT = ""
        engine.SyncEngine = object
        engine.compute_stats = lambda *_args: {}
        engine.strip_internal = lambda row: row
        self.engine_patch = patch.dict(sys.modules, {engine.__name__: engine})
        self.engine_patch.start()
        self.addCleanup(self.engine_patch.stop)
        self.adapter_module = importlib.import_module(".adapter", package="integrations.wecom")

    def test_members_returns_filtered_bounded_exact_conversation_roster(self):
        adapter = object.__new__(self.adapter_module.WeComAdapter)
        adapter._loaded = True
        adapter.engine = types.SimpleNamespace(get_content=lambda: {"group_members": {
            "room-a": [
                {"conversation_id": "room-a", "user_id": "u-2", "name": "Bob"},
                {"conversation_id": "room-a", "user_id": "u-1", "nick_name": "Alice"},
                {"conversation_id": "room-a", "user_id": "u-1", "name": "Duplicate"},
            ],
            "room-b": [{"conversation_id": "room-b", "user_id": "u-x", "name": "Other"}],
        }, "members": [{"user_id": "u-1", "avatar": "https://wework.qpic.cn/member.png"}]})

        result = adapter.members("room-a", account_id="local", limit=1, query="ALICE")

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"], [{
            "id": "u-1", "name": "Alice", "avatar_url": "https://wework.qpic.cn/member.png", "role": "member",
            "remark": "", "join_time": None, "account_id": "local",
        }])

    def test_members_rejects_account_mismatch_and_invalid_bounds(self):
        adapter = object.__new__(self.adapter_module.WeComAdapter)
        adapter._loaded = True
        adapter.engine = types.SimpleNamespace(get_content=lambda: {"group_members": {}})
        with self.assertRaises(ValueError):
            adapter.members("room-a", account_id="another-account")
        with self.assertRaises(ValueError):
            adapter.members("room-a", limit=1001)


class WeComBoundedQueriesTest(unittest.TestCase):
    def setUp(self):
        engine = types.ModuleType("integrations.wecom.engine")
        engine.OUT = ""
        engine.SyncEngine = object
        engine.compute_stats = lambda *_args: {}
        engine.strip_internal = lambda row: {k: v for k, v in row.items() if not k.startswith("_")}
        with patch.dict(sys.modules, {engine.__name__: engine}):
            self.module = importlib.import_module(".adapter", package="integrations.wecom")
        self.strip_patch = patch.object(self.module, "strip_internal", engine.strip_internal)
        self.strip_patch.start()
        self.addCleanup(self.strip_patch.stop)
        self.rows = [{"message_id": str(i), "conversation_id": "room-a" if i % 2 else "room-b",
                      "send_time": i // 2, "text": "Search fixture", "_k": str(i)}
                     for i in range(120)]
        self.adapter = object.__new__(self.module.WeComAdapter)
        self.adapter._loaded = True
        self.adapter.engine = types.SimpleNamespace(iter_messages=lambda: iter(self.rows))

    def test_query_top_k_matches_stable_full_sort_and_omits_internal_keys(self):
        expected = sorted(self.rows, key=lambda row: row["send_time"], reverse=True)[:5]
        actual = self.adapter.search_messages("SEARCH", limit=5)
        self.assertEqual([r["message_id"] for r in actual], [r["message_id"] for r in expected])
        self.assertTrue(all("_k" not in r for r in actual))
        expected = sorted((r for r in self.rows if r["conversation_id"] == "room-a"
                           and r["send_time"] < 30), key=lambda row: row["send_time"], reverse=True)[:3]
        actual = self.adapter.get_messages("room-a", limit=3, before_timestamp=30)
        self.assertEqual([r["message_id"] for r in actual], [r["message_id"] for r in expected])
        self.assertEqual(self.adapter.search_messages("SEARCH", limit=0), [])

    def test_ingestion_clones_only_the_requested_batch(self):
        calls = []
        strip = self.module.strip_internal
        with patch.object(self.module, "strip_internal", side_effect=lambda row: (calls.append(row), strip(row))[1]):
            batches = self.adapter.iter_message_batches(batch_size=7)
            self.assertEqual(len(next(batches)), 7)
            self.assertEqual(len(calls), 7)
            remainder = list(batches)
        self.assertEqual(sum(map(len, remainder)) + 7, len(self.rows))
        self.assertTrue(all(len(batch) <= 7 for batch in remainder))

    def test_media_listing_does_not_exhaust_iterator_before_filtering(self):
        self.rows[1]["media"] = [{"p": "fixture.png"}]
        self.rows[0]["media"] = [{"p": "other.png"}]
        result = self.adapter.list_media("room-a")
        self.assertEqual([r["p"] for r in result], ["fixture.png"])

    def test_release_retains_media_members_and_counts_without_refresh(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "fixture.png"
            image.write_bytes(b"fixture")
            self.rows[1]["media"] = [{"p": "fixture.png"}]
            self.adapter._last_counts = {"messages": len(self.rows)}
            self.adapter.engine.release_messages = lambda: {"messages": len(self.rows)}
            self.adapter.engine.get_content = lambda: {"group_members": {}}
            with patch.object(self.adapter, "refresh", side_effect=AssertionError("unnecessary reload")), \
                    patch.object(self.module, "OUT", temp):
                result = self.adapter.release_message_cache()
                self.assertTrue(result["released"])
                self.assertTrue(self.adapter.media("1")["available"])
                self.assertEqual(self.adapter.media("missing")["reason"], "message_not_found")
                self.assertEqual(self.adapter.media("0")["reason"], "no_attachment_metadata")
                self.assertEqual(self.adapter.members("room-a")["items"], [])
                self.assertEqual(self.adapter.get_counts(), {"messages": len(self.rows)})
                self.assertFalse(self.adapter.release_message_cache()["released"])

    def test_native_query_reloads_released_messages(self):
        self.adapter._messages_released = True
        def reload():
            self.adapter._messages_released = False
        with patch.object(self.adapter, "refresh", side_effect=reload) as refresh:
            result = self.adapter.search_messages("Search", limit=2)
        self.assertEqual(len(result), 2)
        refresh.assert_called_once()


class WeComEngineMemoryTest(unittest.TestCase):
    def setUp(self):
        config = types.ModuleType("integrations.wecom.config")
        config.CONFIG_LOCK = threading.RLock()
        config.load_local_config = lambda: {"key_hex": "00" * 16,
                                           "database_root": "fixture", "user_slot_id": "fixture"}
        config.local_data_dir = lambda: "fixture"
        decrypt = types.ModuleType("integrations.wecom.decrypt_all")
        decrypt.decrypt_db = lambda *_args: None
        decrypt.PAGE_CONST = 4096
        extract = types.ModuleType("integrations.wecom.extract_chat2")
        extract.best_text = lambda *_args: ""
        extract.CTYPE = {}
        extract.parse_fields = lambda *_args: []
        spec = importlib.util.spec_from_file_location("integrations.wecom._engine_memory_test",
                                                     Path(__file__).with_name("engine.py"))
        self.engine = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {m.__name__: m for m in (config, decrypt, extract)}):
            spec.loader.exec_module(self.engine)

    def test_media_index_skips_malformed_rows_without_discarding_valid_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            index = Path(temp) / "media.json"
            index.write_text(json.dumps([
                None,
                "malformed",
                {"MD5": 123, "保存路径": "提取结果/ignored.png"},
                {"MD5": "AbCd", "保存路径": "提取结果/image.png", "文件名": "image.png"},
            ]), encoding="utf-8")
            with patch.object(self.engine, "MEDIA_INDEX", str(index)), \
                    patch.object(self.engine.glob, "glob", return_value=[]):
                media, by_mid = self.engine.build_media_context(temp)

        self.assertEqual(len(media), 2)
        self.assertEqual(media[1]["rel"], "image.png")
        self.assertEqual(by_mid, {})

    def test_non_list_media_index_is_treated_as_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            index = Path(temp) / "media.json"
            index.write_text(json.dumps({"MD5": "fixture"}), encoding="utf-8")
            with patch.object(self.engine, "MEDIA_INDEX", str(index)), \
                    patch.object(self.engine.glob, "glob", return_value=[]):
                media, by_mid = self.engine.build_media_context(temp)

        self.assertEqual(media, [])
        self.assertEqual(by_mid, {})

    def test_cache_mapping_links_local_media_without_export_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cache_root = root / 'account'
            entries = [('image', 'Image', 'fixture.png', 'image-md5'),
                       ('video', 'Video', 'fixture.mp4', 'video-md5'),
                       ('voice', 'Voice', 'fixture.silk', 'voice-md5'),
                       ('file', 'File', 'fixture.bin', 'file-md5')]
            for _kind, directory, filename, _md5 in entries:
                cached = cache_root / 'Cache' / directory / filename
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(b'fixture')
            snapshot = root / 'snapshot'
            mapping_db = snapshot / 'CacheMapping' / 'mapping.db'
            mapping_db.parent.mkdir(parents=True)
            with sqlite3.connect(mapping_db) as db:
                db.execute('CREATE TABLE mapping(type, key, file_name, last_modify_time, file_md5)')
                db.executemany('INSERT INTO mapping VALUES(?, ?, ?, ?, ?)',
                               [(kind, 'opaque', filename, 1, md5)
                                for kind, _directory, filename, md5 in entries])
            db.close()
            decrypted_file_db = root / 'decrypted' / 'file.db'
            decrypted_file_db.parent.mkdir(parents=True)
            with sqlite3.connect(decrypted_file_db) as db:
                db.execute('CREATE TABLE file_table4(message_id, md5)')
                db.executemany('INSERT INTO file_table4 VALUES(?, ?)',
                               [(f'message-{kind}', md5) for kind, _directory, _filename, md5 in entries])
                db.execute('INSERT INTO file_table4 VALUES(?, ?)',
                           ('message-image-shared', 'image-md5'))
            db.close()

            with patch.object(self.engine, 'MEDIA_INDEX', str(root / 'missing-index.json')):
                media, by_mid = self.engine.build_media_context(
                    str(cache_root), str(snapshot), {'Data/file.db': str(decrypted_file_db)})

            self.assertEqual(len(media), 4)
            for kind, directory, filename, _md5 in entries:
                with self.subTest(kind=kind):
                    attachment = by_mid[f'message-{kind}'][0]
                    self.assertEqual(attachment['_local_cache_path'], f'Cache/{directory}/{filename}')
                    self.assertEqual(attachment['k'], kind)
                    self.assertEqual(attachment['sz'], len(b'fixture'))
                    self.assertNotIn('p', attachment)
            self.assertEqual(by_mid['message-image-shared'][0]['_local_cache_path'],
                             'Cache/Image/fixture.png')

    def test_strip_internal_removes_nested_cache_paths(self):
        result = self.engine.strip_internal({'media': [{'n': 'image', '_local_cache_path': 'private'}]})
        self.assertEqual(result, {'media': [{'n': 'image'}]})

    def test_streaming_sql_does_not_preload_and_closes_when_consumer_stops(self):
        consumed = []
        def rows():
            for i in range(20):
                consumed.append(i)
                yield (i,)
        connection = types.SimpleNamespace(execute=lambda *_args: rows(), close=lambda: consumed.append("closed"))
        with patch.object(self.engine.sqlite3, "connect", return_value=connection):
            stream = self.engine.iter_read_messages("fixture.db")
            self.assertEqual(consumed, [])
            self.assertEqual(next(stream), (0,))
            self.assertEqual(consumed, [0])
            stream.close()
            self.assertEqual(consumed, [0, "closed"])

    def test_snapshot_iterator_remains_consistent_when_sync_replaces_messages(self):
        engine = object.__new__(self.engine.SyncEngine)
        engine.lock = threading.Lock()
        engine.messages = [{"message_id": "before"}]
        stream = engine.iter_messages()
        engine.messages = [{"message_id": "after"}]
        self.assertEqual(list(stream), [{"message_id": "before"}])
        self.assertEqual(engine.snapshot_messages(), [{"message_id": "after"}])

    def test_new_event_payload_is_only_materialized_with_subscribers(self):
        engine = object.__new__(self.engine.SyncEngine)
        engine.lock = threading.Lock()
        engine.subscribers = []
        built = []
        def event():
            built.append(True)
            return {"type": "new", "messages": [{"message_id": "fixture"}]}
        engine._emit(event)
        self.assertEqual(built, [])
        events = []
        engine.subscribe(events.append)
        engine._emit(event)
        self.assertEqual(len(built), 1)
        self.assertEqual(events, [{"type": "new", "messages": [{"message_id": "fixture"}]}])

    def test_release_removes_text_preserving_notices_and_reported_count(self):
        engine = object.__new__(self.engine.SyncEngine)
        engine.lock = threading.Lock()
        engine._released_summary = None
        engine.messages = [{"message_id": "normal", "conversation": "room", "content_type": 1,
                            "text": "ordinary chat"},
                           {"message_id": "notice", "conversation": "room", "content_type": 1022,
                            "text": "synthetic group notice"}]
        summary = engine.release_messages()
        self.assertEqual(summary, {"messages": 2, "conversations": 1})
        self.assertEqual(engine.messages, [])
        self.assertEqual(engine._released_notices[0]["message_id"], "notice")
        self.assertEqual(engine.release_messages(), summary)


if __name__ == "__main__":
    unittest.main()
