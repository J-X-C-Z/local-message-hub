import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from integrations.wecom.avatar_store import read_conversation_avatars


class WeComAvatarStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "avatar_store_v3.db"
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("CREATE TABLE conversation_avatar_table "
                               "(conversation_id TEXT, hash TEXT, buffer BLOB)")
            connection.executemany("INSERT INTO conversation_avatar_table VALUES (?, ?, ?)", [
                ("room-a", "hash-a", b"\x89PNG\r\n\x1a\nimage-a"),
                ("room-b", "hash-b", b"\xff\xd8\xffimage-b"),
                ("room-too-large", "hash-c", b"x" * (2 * 1024 * 1024 + 1)),
            ])
            connection.commit()
        finally:
            connection.close()
        self.files = {"Data/avatar_store_v3.db": str(self.path)}

    def test_reads_only_exact_requested_ids_and_known_snapshot_path(self):
        self.assertEqual(read_conversation_avatars(self.files, ["room-a", "room-b", "missing"]), {
            "room-a": b"\x89PNG\r\n\x1a\nimage-a",
            "room-b": b"\xff\xd8\xffimage-b",
        })
        self.assertEqual(read_conversation_avatars(
            {"Data.old/avatar_store_v3.db": str(self.path)}, ["room-a"]), {})

    def test_reads_without_modifying_source_database(self):
        before = hashlib.sha256(self.path.read_bytes()).digest()
        read_conversation_avatars(self.files, ["room-a"])
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).digest(), before)

    def test_rejects_unknown_schema(self):
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("DROP TABLE conversation_avatar_table")
            connection.execute("CREATE TABLE conversation_avatar_table "
                               "(conversation_id TEXT, buffer BLOB)")
        finally:
            connection.close()
        self.assertEqual(read_conversation_avatars(self.files, ["room-a"]), {})
