"""Regression checks for write elision and bounded search joins."""
import tempfile
from pathlib import Path
import unittest

from hub.store import ChatStore


class StorePerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ChatStore(Path(self.temp.name) / "index.db")
        self.rows = [{"id": str(i), "conversation_id": "room", "timestamp": i // 3,
                      "sender_id": "sender", "text": f"original needle {i}"} for i in range(12)]
        self.store.ingest("email", "a", [], self.rows)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_repeat_does_not_write_and_checkpoint_still_advances(self):
        changes = self.store._conn.total_changes
        counts = self.store.ingest("email", "a", [], self.rows)
        self.assertEqual(counts["messages"], 12)
        self.assertEqual(self.store._conn.total_changes, changes)
        self.store.ingest("email", "a", [], self.rows, checkpoint={
            "conversation_id": "room", "next_offset": 12, "cursor": {"uid": 12}, "complete": True})
        checkpoint = self.store.sync_checkpoint("email", "a", "room")
        self.assertEqual(checkpoint["cursor"], {"uid": 12})
        self.assertTrue(checkpoint["complete"])
        self.assertEqual(self.store._conn.total_changes - changes, 1)

    def test_changed_body_and_null_values_update_search_and_preview(self):
        row = {**self.rows[-1], "text": "changed body", "sender_name": "Name"}
        self.store.ingest("email", "a", [], [row])
        self.assertEqual(self.store.search("original needle")["total"], 11)
        self.assertEqual(self.store.search("changed body")["total"], 1)
        conversation = self.store.get_conversation("email", "a", "room")
        self.assertEqual(conversation["message_count"], 12)
        self.assertEqual(conversation["last_message"], "changed body")
        row["text"] = None
        row["sender_name"] = None
        self.store.ingest("email", "a", [], [row])
        self.assertEqual(self.store.search("changed body")["total"], 0)
        self.assertIsNone(self.store.get_message("email", "a", row["id"])["sender_name"])

    def test_metadata_change_does_not_rewrite_fts(self):
        changes = self.store._conn.total_changes
        self.store.ingest("email", "a", [], [{**self.rows[0], "sender_name": "Renamed"}])
        self.assertEqual(self.store._conn.total_changes - changes, 2)
        self.assertEqual(self.store.search("original needle")["total"], 12)

    def test_unchanged_messages_recreate_missing_parent_with_aggregates(self):
        self.store._conn.execute("DELETE FROM conversations")
        self.store.ingest("email", "a", [], self.rows)
        conversation = self.store.get_conversation("email", "a", "room")
        self.assertEqual(conversation["message_count"], 12)
        self.assertEqual(conversation["last_message"], self.rows[-1]["text"])

    def test_pagination_scopes_isolate_accounts_and_conversations(self):
        self.store.ingest("email", "b", [], self.rows)
        self.store.ingest("email", "a", [], [{"id": "other", "conversation_id": "other",
                                               "timestamp": 100, "text": "original needle"}])
        kwargs = {"q": "needle", "platform": "email", "account_id": "a", "conversation_id": "room"}
        first = self.store.search(**kwargs, limit=5)
        second = self.store.search(**kwargs, limit=5, cursor=first["next_cursor"])
        third = self.store.search(**kwargs, limit=5, cursor=second["next_cursor"])
        items = first["items"] + second["items"] + third["items"]
        self.assertEqual(len({m["id"] for m in items}), 12)
        self.assertEqual(first["total"], 12)
        self.assertTrue(all(m["account_id"] == "a" and m["conversation_id"] == "room" for m in items))
        self.assertFalse(third["has_more"])
        for scope in ({"account_id": "b"}, {"conversation_id": "other"}):
            with self.assertRaises(ValueError):
                self.store.search(**{**kwargs, **scope}, cursor=first["next_cursor"])
        expected = self.store.search(**kwargs, limit=3, offset=5)
        self.assertEqual([m["id"] for m in expected["items"]], [m["id"] for m in second["items"][:3]])

    def test_legacy_fts_trigger_is_upgraded_on_reopen(self):
        self.store._conn.executescript("""
            DROP TRIGGER messages_fts_update;
            CREATE TRIGGER messages_fts_update AFTER UPDATE OF text ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
                INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text);
            END;
        """)
        self.store.close()
        self.store = ChatStore(Path(self.temp.name) / "index.db")
        changes = self.store._conn.total_changes
        self.store._conn.execute("UPDATE messages SET text=text WHERE id='0'")
        self.assertEqual(self.store._conn.total_changes - changes, 1)
        self.assertEqual(self.store.search("needle")["total"], 12)


if __name__ == "__main__":
    unittest.main()
