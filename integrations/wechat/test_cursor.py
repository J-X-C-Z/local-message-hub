"""Synthetic keyset-cursor tests; never open a user's WeChat databases."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from .provider import WeChatProvider
from .vendor.db import WeChatDB


class CursorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = []
        self.table = "Msg_" + ("a" * 32)
        fixture_rows = {
            "message_0.db": [(10, 1), (10, 2), (12, 1)],
            "message_1.db": [(10, 1), (10, 3), (11, 1), (12, 2)],
        }
        for filename, rows in fixture_rows.items():
            path = Path(self.temp.name) / ("cache__" + filename)
            conn = sqlite3.connect(path)
            conn.execute(
                f"CREATE TABLE {self.table} (local_id INTEGER, local_type INTEGER, "
                "real_sender_id INTEGER, create_time INTEGER, message_content TEXT, "
                "source BLOB, packed_info_data BLOB, compress_content BLOB, "
                "server_id TEXT, sort_seq INTEGER)"
            )
            conn.executemany(
                f"INSERT INTO {self.table} VALUES (?, 1, NULL, ?, ?, NULL, NULL, NULL, NULL, ?)",
                [(local_id, seq, f"{filename}:{local_id}", seq) for seq, local_id in rows],
            )
            conn.commit()
            conn.close()
            self.paths.append(path)

        self.reader = WeChatDB.__new__(WeChatDB)

        def open_shards(_user):
            result = []
            for path in sorted(self.paths):
                conn = sqlite3.connect(path)
                conn.row_factory = sqlite3.Row
                result.append((conn, self.table))
            return result

        self.reader._msg_conns = open_shards
        self.reader._shard_label = lambda conn: Path(
            conn.execute("PRAGMA database_list").fetchone()[2]
        ).name.rsplit("__", 1)[-1]

    def tearDown(self):
        self.temp.cleanup()

    def test_keyset_pages_cover_cross_shard_ties_once_in_stable_order(self):
        cursor = None
        received = []
        while True:
            page = self.reader.get_messages_after("synthetic-room", cursor, limit=2)
            if not page:
                break
            received.extend((row["sort_seq"], row["shard"], row["local_id"])
                            for row in page)
            last = page[-1]
            cursor = {"sort_seq": last["sort_seq"], "shard": last["shard"],
                      "local_id": last["local_id"]}

        self.assertEqual(received, [
            (10, "message_0.db", 1),
            (10, "message_0.db", 2),
            (10, "message_1.db", 1),
            (10, "message_1.db", 3),
            (11, "message_1.db", 1),
            (12, "message_0.db", 1),
            (12, "message_1.db", 2),
        ])
        self.assertEqual(len(received), len(set(received)))

    def test_cursor_resumes_after_an_inserted_later_key_without_rescanning(self):
        first = self.reader.get_messages_after("synthetic-room", None, limit=3)
        cursor = {"sort_seq": first[-1]["sort_seq"], "shard": first[-1]["shard"],
                  "local_id": first[-1]["local_id"]}

        conn = sqlite3.connect(self.paths[1])
        conn.execute(
            f"INSERT INTO {self.table} VALUES (4, 1, NULL, 13, 'new', NULL, NULL, NULL, NULL, 13)"
        )
        conn.commit()
        conn.close()

        resumed = self.reader.get_messages_after("synthetic-room", cursor, limit=20)
        keys = [(row["sort_seq"], row["shard"], row["local_id"]) for row in resumed]
        self.assertNotIn((10, "message_0.db", 1), keys)
        self.assertEqual(keys[-1], (13, "message_1.db", 4))

    def test_invalid_cursor_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cursor"):
            self.reader.get_messages_after("synthetic-room", {"sort_seq": "bad"})

    def test_provider_cursor_is_scoped_to_conversation_and_maps_rows(self):
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = Mock()
        rows = [{
            "shard": "message_0.db", "local_id": index, "sort_seq": index,
            "sender_username": "fixture", "is_self": False, "create_time": index,
            "type": "text", "content": "synthetic", "local_type": 1,
        } for index in (1, 2, 3, 4)]
        provider.reader.get_messages_after.side_effect = [rows[:3], rows[2:]]
        provider.platform = "wechat"

        first = provider.messages_after("synthetic-room", limit=2)
        self.assertEqual(len(first["messages"]), 2)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["messages"][0]["id"], "synthetic-room:message_0.db:1")
        second = provider.messages_after("synthetic-room", first["next_cursor"], limit=2)
        self.assertEqual(second["messages"][0]["sort_seq"], 3)
        with self.assertRaisesRegex(ValueError, "cursor"):
            provider.messages_after("another-room", first["next_cursor"], limit=2)


if __name__ == "__main__":
    unittest.main()
