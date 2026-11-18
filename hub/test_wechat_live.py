"""Synthetic tests for the low-overhead WeChat live receiver."""

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from .server import Hub
from .wechat_live import WeChatLiveReceiver


def source_row(local_id, timestamp, text, *, is_self=False):
    return {
        "id": f"friend:message_0.db:{local_id}",
        "sender_id": "self" if is_self else "friend",
        "sender_username": "self" if is_self else "friend",
        "is_self": is_self,
        "timestamp": timestamp,
        "sort_seq": timestamp,
        "shard": "message_0.db",
        "local_id": local_id,
        "type": "文本",
        "content": text,
        "local_type": 1,
        "server_id": str(1000 + local_id),
    }


class FakeProvider:
    def __init__(self):
        self.reader = SimpleNamespace(wxid="account", account="", get_nickname=self.nickname)
        self.snapshot = None
        self.after_pages = []
        self.after_calls = []
        self.initial_rows = {}

    @staticmethod
    def nickname(username):
        return {"friend": "Friend", "account": "Me"}.get(username, username)

    def contacts(self):
        return [
            {"id": "account", "name": "Me"},
            {"id": "friend", "name": "Friend"},
        ]

    def session_snapshot(self, _previous):
        return self.snapshot

    def messages(self, conversation_id, limit=200):
        return self.initial_rows.get(conversation_id, [])[:limit]

    def messages_after(self, _conversation_id, cursor=None, limit=500):
        self.after_calls.append(copy.deepcopy(cursor))
        if not self.after_pages:
            raise RuntimeError("unexpected message read")
        return self.after_pages.pop(0)


class WeChatLiveReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hub = Hub(Path(self.temp.name))
        self.provider = FakeProvider()
        self.receiver = WeChatLiveReceiver(
            self.hub, provider_factory=lambda: self.provider, interval=1)

    def tearDown(self):
        self.hub.close()
        self.temp.cleanup()

    def seed_conversation(self, timestamp=100):
        self.hub.store.ingest(
            "wechat", "account",
            [{"id": "friend", "title": "Friend", "kind": "direct",
              "last_timestamp": timestamp, "last_message": "old"}],
            [{
                "id": "friend:message_0.db:1", "conversation_id": "friend",
                "sender_id": "friend", "sender_name": "Friend", "timestamp": timestamp,
                "type": "text", "text": "old", "is_self": False, "raw_type": "1",
            }],
            checkpoint={
                "conversation_id": "friend", "next_offset": 0, "complete": True,
                "cursor": {"version": 1, "session_id": "friend", "sort_seq": timestamp,
                           "shard": "message_0.db", "local_id": 1},
            })

    def test_unchanged_baseline_does_not_read_message_shards(self):
        self.seed_conversation()
        self.provider.snapshot = {
            "changed": True,
            "signature": ["session-v1"],
            "sessions": [{
                "username": "friend", "unread": 0, "summary": "old", "last_time": 100,
            }],
        }

        self.assertEqual(self.receiver.poll_once(), 0)
        self.assertEqual(self.provider.after_calls, [])
        self.assertEqual(self.receiver.pending, {})
        self.assertEqual(self.hub.sources["wechat"]["live_receive"]["state"], "ready")

    def test_changed_session_is_incrementally_ingested_once(self):
        self.seed_conversation()
        initial = {
            "username": "friend", "unread": 0, "summary": "old", "last_time": 100,
        }
        changed = {
            "username": "friend", "unread": 1, "summary": "new", "last_time": 101,
        }
        self.provider.snapshot = {
            "changed": True, "signature": ["session-v1"], "sessions": [initial],
        }
        self.receiver.poll_once()
        self.provider.snapshot = {
            "changed": True, "signature": ["session-v2"], "sessions": [changed],
        }
        self.provider.after_pages.append({
            "messages": [source_row(2, 101, "new")],
            "next_cursor": {"version": 1, "session_id": "friend", "sort_seq": 101,
                            "shard": "message_0.db", "local_id": 2},
            "has_more": False,
        })

        self.assertEqual(self.receiver.poll_once(), 1)
        self.assertEqual(self.provider.after_calls, [{
            "version": 1, "session_id": "friend", "sort_seq": 100,
            "shard": "message_0.db", "local_id": 1,
        }])
        self.assertIsNotNone(
            self.hub.store.get_message("wechat", "account", "friend:message_0.db:2"))
        events = self.hub.store.incoming_message_events(0)
        self.assertEqual(events[-1]["conversation_id"], "friend")
        self.assertEqual(self.hub.store.sync_checkpoint(
            "wechat", "account", "friend")["cursor"]["sort_seq"], 101)

        self.assertEqual(self.receiver.poll_once(), 0)
        self.assertEqual(len(self.provider.after_calls), 1)

    def test_restart_resumes_from_durable_checkpoint(self):
        self.seed_conversation()
        first = {
            "username": "friend", "unread": 0, "summary": "old", "last_time": 100,
        }
        second = {
            "username": "friend", "unread": 1, "summary": "new", "last_time": 101,
        }
        self.provider.snapshot = {
            "changed": True, "signature": ["session-v1"], "sessions": [first],
        }
        self.receiver.poll_once()
        self.provider.snapshot = {
            "changed": True, "signature": ["session-v2"], "sessions": [second],
        }
        self.provider.after_pages.append({
            "messages": [source_row(2, 101, "new")],
            "next_cursor": {"version": 1, "session_id": "friend", "sort_seq": 101,
                            "shard": "message_0.db", "local_id": 2},
            "has_more": False,
        })
        self.receiver.poll_once()

        restarted_provider = FakeProvider()
        restarted_provider.snapshot = {
            "changed": True,
            "signature": ["session-v3"],
            "sessions": [{
                "username": "friend", "unread": 1, "summary": "newer",
                "last_time": 102,
            }],
        }
        restarted_provider.after_pages.append({
            "messages": [source_row(3, 102, "newer")],
            "next_cursor": {"version": 1, "session_id": "friend", "sort_seq": 102,
                            "shard": "message_0.db", "local_id": 3},
            "has_more": False,
        })
        restarted = WeChatLiveReceiver(
            self.hub, provider_factory=lambda: restarted_provider, interval=1)

        self.assertEqual(restarted.poll_once(), 1)
        self.assertEqual(restarted_provider.after_calls[0]["sort_seq"], 101)
        self.assertIsNotNone(
            self.hub.store.get_message("wechat", "account", "friend:message_0.db:3"))

    def test_failed_read_stays_pending_for_next_tick(self):
        self.seed_conversation()
        old = {
            "username": "friend", "unread": 0, "summary": "old", "last_time": 100,
        }
        changed = {
            "username": "friend", "unread": 1, "summary": "new", "last_time": 101,
        }
        self.provider.snapshot = {
            "changed": True, "signature": ["session-v1"], "sessions": [old],
        }
        self.receiver.poll_once()
        self.provider.snapshot = {
            "changed": True, "signature": ["session-v2"], "sessions": [changed],
        }

        self.assertEqual(self.receiver.poll_once(), 0)
        self.assertIn("friend", self.receiver.pending)
        self.assertEqual(self.hub.sources["wechat"]["live_receive"]["state"], "error")

        self.provider.snapshot = {
            "changed": False, "signature": ["session-v2"], "sessions": [],
        }
        self.provider.after_pages.append({
            "messages": [source_row(2, 101, "new")],
            "next_cursor": {"version": 1, "session_id": "friend", "sort_seq": 101,
                            "shard": "message_0.db", "local_id": 2},
            "has_more": False,
        })
        self.assertEqual(self.receiver.poll_once(), 1)
        self.assertEqual(self.receiver.pending, {})


if __name__ == "__main__":
    unittest.main()
