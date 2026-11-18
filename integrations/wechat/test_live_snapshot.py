"""Synthetic tests for the WeChat session-change probe."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from .provider import WeChatProvider


class SessionSnapshotTests(unittest.TestCase):
    def test_unchanged_session_file_skips_decryption_and_query(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "session.db"
            wal = Path(directory) / "session.db-wal"
            database.write_bytes(b"database")
            wal.write_bytes(b"")
            reader = SimpleNamespace(
                _db_files=[("session/session.db", str(database), 8)],
                get_sessions=Mock(return_value=[{
                    "username": "friend", "unread": 1, "summary": "new",
                    "last_time": 101, "last_sender": "Friend",
                }]),
            )
            provider = WeChatProvider.__new__(WeChatProvider)
            provider.reader = reader

            first = provider.session_snapshot()
            second = provider.session_snapshot(first["signature"])

            self.assertTrue(first["changed"])
            self.assertEqual(first["sessions"][0]["username"], "friend")
            self.assertFalse(second["changed"])
            reader.get_sessions.assert_called_once_with(limit=100000)

            wal.write_bytes(b"new transaction")
            third = provider.session_snapshot(second["signature"])
            self.assertTrue(third["changed"])
            self.assertEqual(reader.get_sessions.call_count, 2)


if __name__ == "__main__":
    unittest.main()
