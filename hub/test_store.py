"""Focused unit tests for :mod:`hub.store` and :mod:`hub.exports`.

All fixtures are synthetic and generated in-memory / in temporary directories.
No real chat content is read or written.

Run with::

    python -m unittest hub.test_store -v
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

try:  # package-relative when imported as hub.test_store
    from .exports import render_export
    from .store import ChatStore, parse_bound
except ImportError:  # direct execution from inside the package directory
    from exports import render_export
    from store import ChatStore, parse_bound

CST = timezone(timedelta(hours=8))


def ts(year, month, day, hour=0, minute=0, second=0) -> int:
    """Unix seconds for a UTC+08:00 wall-clock time."""
    return int(datetime(year, month, day, hour, minute, second, tzinfo=CST).timestamp())


def msg(
    mid,
    cid,
    timestamp,
    text="",
    type="text",
    platform="wechat",
    account="acc1",
    sender="u1",
    sender_name="Alice",
    is_self=False,
    media_url=None,
    file_name=None,
    **extra
):
    record = {
        "id": mid,
        "platform": platform,
        "account_id": account,
        "conversation_id": cid,
        "sender_id": sender,
        "sender_name": sender_name,
        "timestamp": timestamp,
        "type": type,
        "text": text,
        "is_self": is_self,
        "media_url": media_url,
        "file_name": file_name,
        "raw_type": type,
    }
    record.update(extra)
    return record


def conv(cid, platform="wechat", account="acc1", title="Chat", **extra):
    record = {
        "id": cid,
        "platform": platform,
        "account_id": account,
        "title": title,
        "kind": "group",
        "avatar_url": None,
        "member_count": 3,
        "last_message": None,
        "last_timestamp": None,
        "message_count": 0,
    }
    record.update(extra)
    return record


def contact(cid, platform="wechat", account="acc1", name="Alice", **extra):
    record = {
        "id": cid,
        "platform": platform,
        "account_id": account,
        "name": name,
        "avatar_url": None,
        "kind": "friend",
    }
    record.update(extra)
    return record


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        self.store = ChatStore(self.path)

    def tearDown(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    # ------------------------------------------------------------------
    def test_cross_platform_id_collisions_are_isolated(self):
        self.store.ingest(
            "wechat",
            "acct",
            [conv("c1", platform="wechat", title="WeChat thread")],
            [msg("m1", "c1", ts(2024, 3, 1, 10), "hello from wechat", platform="wechat")],
        )
        self.store.ingest(
            "qq",
            "acct",
            [conv("c1", platform="qq", title="QQ thread")],
            [msg("m1", "c1", ts(2024, 3, 1, 11), "hello from qq", platform="qq")],
        )

        convs = self.store.conversations("all")
        self.assertEqual(convs["total"], 2)
        titles = sorted(c["title"] for c in convs["items"])
        self.assertEqual(titles, ["QQ thread", "WeChat thread"])

        wx = self.store.messages("wechat", "acct", "c1")
        qq = self.store.messages("qq", "acct", "c1")
        self.assertEqual(wx["total"], 1)
        self.assertEqual(qq["total"], 1)
        self.assertEqual(wx["items"][0]["text"], "hello from wechat")
        self.assertEqual(qq["items"][0]["text"], "hello from qq")

    def test_same_id_different_accounts_isolated(self):
        self.store.ingest("wechat", "a1", [conv("c1")], [msg("m1", "c1", ts(2024, 3, 1, 9), "one")])
        self.store.ingest("wechat", "a2", [conv("c1")], [msg("m1", "c1", ts(2024, 3, 1, 9), "two")])
        self.assertEqual(self.store.count("all"), 2)
        self.assertEqual(self.store.messages("wechat", "a1", "c1")["items"][0]["text"], "one")
        self.assertEqual(self.store.messages("wechat", "a2", "c1")["items"][0]["text"], "two")

    # ------------------------------------------------------------------
    def test_repeat_ingest_is_idempotent(self):
        conversations = [conv("c1")]
        messages = [
            msg("m1", "c1", ts(2024, 3, 1, 9), "first"),
            msg("m2", "c1", ts(2024, 3, 1, 10), "second"),
        ]
        first = self.store.ingest("wechat", "acc1", conversations, messages)
        second = self.store.ingest("wechat", "acc1", conversations, messages)
        self.assertEqual(first, {"conversations": 1, "messages": 2, "contacts": 0})
        self.assertEqual(second, first)
        self.assertEqual(self.store.count("all"), 2)
        self.assertEqual(self.store.conversations("all")["total"], 1)
        self.assertEqual(self.store.conversations("all")["items"][0]["message_count"], 2)

    def test_sync_checkpoint_commits_atomically_and_isolated_by_scope(self):
        checkpoint = {"conversation_id": "room", "next_offset": 10,
                      "cursor": {"sort_seq": 42, "shard": "message_1.db", "local_id": 9},
                      "complete": False}
        self.store.ingest("wechat", "a1", [conv("room")],
                          [msg("page-1", "room", 1, "first page")],
                          checkpoint=checkpoint)
        self.assertEqual(self.store.sync_checkpoint("wechat", "a1", "room")["next_offset"], 10)
        self.assertEqual(self.store.sync_checkpoint("wechat", "a1", "room")["cursor"], checkpoint["cursor"])
        self.assertIsNone(self.store.sync_checkpoint("wechat", "a2", "room"))
        self.assertIsNone(self.store.sync_checkpoint("qq", "a1", "room"))

        with patch.object(self.store, "_upsert_message", side_effect=RuntimeError("fixture")):
            with self.assertRaisesRegex(RuntimeError, "fixture"):
                self.store.ingest("wechat", "a1", [],
                                  [msg("page-2", "room", 2, "second page")],
                                  checkpoint={"conversation_id": "room", "next_offset": 20})
        self.assertEqual(self.store.sync_checkpoint("wechat", "a1", "room")["next_offset"], 10)
        self.assertEqual(self.store.count("wechat"), 1)

    def test_sync_checkpoint_survives_store_restart(self):
        self.store.ingest("qq", "account", [conv("peer", platform="qq")],
                          [msg("peer:1", "peer", 1, "page", platform="qq", account="account")],
                          checkpoint={"conversation_id": "peer", "next_offset": 500})
        self.store.close()
        self.store = ChatStore(self.path)
        checkpoint = self.store.sync_checkpoint("qq", "account", "peer")
        self.assertEqual(checkpoint["next_offset"], 500)
        self.assertIsNone(checkpoint["cursor"])
        self.assertFalse(checkpoint["complete"])
        self.assertEqual(self.store.count("qq"), 1)

    def test_duplicate_ids_within_one_batch_dedupe(self):
        result = self.store.ingest(
            "wechat",
            "acc1",
            [conv("c1")],
            [msg("m1", "c1", ts(2024, 3, 1, 9), "old"), msg("m1", "c1", ts(2024, 3, 1, 9), "new")],
        )
        self.assertEqual(result["messages"], 1)
        self.assertEqual(self.store.count("all"), 1)
        self.assertEqual(self.store.messages("wechat", "acc1", "c1")["items"][0]["text"], "new")

    def test_aggregates_refresh_on_edit(self):
        self.store.ingest("wechat", "acc1", [conv("c1")], [msg("m1", "c1", ts(2024, 3, 1, 9), "a")])
        self.store.ingest(
            "wechat",
            "acc1",
            [conv("c1")],
            [
                msg("m2", "c1", ts(2024, 3, 1, 10), "b"),
                msg("m3", "c1", ts(2024, 3, 1, 12), "c"),
            ],
        )
        conversation = self.store.conversations("all")["items"][0]
        self.assertEqual(conversation["message_count"], 3)
        self.assertEqual(conversation["last_timestamp"], ts(2024, 3, 1, 12))
        self.assertEqual(conversation["last_message"], "c")

        # Editing the newest message must move the preview and timestamp.
        self.store.ingest(
            "wechat", "acc1", [conv("c1")], [msg("m2", "c1", ts(2024, 3, 1, 20), "b-edited")]
        )
        conversation = self.store.conversations("all")["items"][0]
        self.assertEqual(conversation["message_count"], 3)
        self.assertEqual(conversation["last_timestamp"], ts(2024, 3, 1, 20))
        self.assertEqual(conversation["last_message"], "b-edited")

    def test_media_message_preview_label(self):
        self.store.ingest(
            "wechat", "acc1", [conv("c1")], [msg("m1", "c1", ts(2024, 3, 1, 9), "", type="image")]
        )
        self.assertEqual(self.store.conversations("all")["items"][0]["last_message"], "[图片]")

    def test_extra_fields_round_trip(self):
        self.store.ingest(
            "wechat",
            "acc1",
            [conv("c1", source_folder="F:/x")],
            [msg("m1", "c1", ts(2024, 3, 1, 9), "hi", source_seq=42)],
        )
        self.assertEqual(self.store.conversations("all")["items"][0]["extra"]["source_folder"], "F:/x")
        self.assertEqual(self.store.messages("wechat", "acc1", "c1")["items"][0]["extra"]["source_seq"], 42)

    # ------------------------------------------------------------------
    def test_message_text_and_type_filters(self):
        self.store.ingest(
            "wechat",
            "acc1",
            [conv("c1")],
            [
                msg("m1", "c1", ts(2024, 3, 1, 9), "hello world"),
                msg("m2", "c1", ts(2024, 3, 1, 10), "hello there", type="link"),
                msg("m3", "c1", ts(2024, 3, 1, 11), "再见 world"),
            ],
        )
        self.assertEqual(self.store.messages("wechat", "acc1", "c1", q="hello")["total"], 2)
        self.assertEqual(self.store.messages("wechat", "acc1", "c1", q="world")["total"], 2)
        self.assertEqual(self.store.messages("wechat", "acc1", "c1", type="link")["total"], 1)
        self.assertEqual(
            self.store.messages("wechat", "acc1", "c1", q="hello", type="text")["total"], 1
        )

    def test_message_ordering_newest_first(self):
        self.store.ingest(
            "wechat",
            "acc1",
            [conv("c1")],
            [msg("m1", "c1", ts(2024, 3, 1, 9), "old"), msg("m2", "c1", ts(2024, 3, 1, 10), "new")],
        )
        items = self.store.messages("wechat", "acc1", "c1")["items"]
        self.assertEqual([i["text"] for i in items], ["new", "old"])

    def test_iso_date_bounds_are_cst_inclusive(self):
        self.store.ingest(
            "wechat",
            "acc1",
            [conv("c1")],
            [
                msg("m_before", "c1", ts(2024, 2, 29, 23, 30), "before"),
                msg("m_in", "c1", ts(2024, 3, 1, 0, 30), "in"),
                msg("m_end", "c1", ts(2024, 3, 1, 23, 30), "end"),
                msg("m_after", "c1", ts(2024, 3, 2, 0, 30), "after"),
            ],
        )
        result = self.store.messages("wechat", "acc1", "c1", start="2024-03-01", end="2024-03-01")
        texts = sorted(i["text"] for i in result["items"])
        self.assertEqual(texts, ["end", "in"])
        self.assertEqual(result["total"], 2)
        # A start-only bound keeps everything from 2024-03-01 00:00 CST onward,
        # so the Feb-29-CST message is excluded while the other three remain.
        self.assertEqual(
            self.store.messages("wechat", "acc1", "c1", start="2024-03-01")["total"], 3
        )

    def test_unix_timestamp_bounds(self):
        base = ts(2024, 3, 1, 12)
        self.store.ingest(
            "wechat",
            "acc1",
            [conv("c1")],
            [
                msg("m1", "c1", base - 100, "early"),
                msg("m2", "c1", base, "middle"),
                msg("m3", "c1", base + 100, "late"),
            ],
        )
        result = self.store.messages(
            "wechat", "acc1", "c1", start=str(base - 50), end=str(base + 50)
        )
        self.assertEqual([i["text"] for i in result["items"]], ["middle"])

    def test_parse_bound_rejects_garbage(self):
        with self.assertRaises(ValueError):
            parse_bound("not-a-date")

    # ------------------------------------------------------------------
    def test_literal_percent_and_underscore_matching(self):
        self.store.ingest(
            "wechat",
            "acc1",
            [conv("c1")],
            [
                msg("m1", "c1", ts(2024, 3, 1, 9), "discount 100% off"),
                msg("m2", "c1", ts(2024, 3, 1, 10), "discount 100X off"),
                msg("m3", "c1", ts(2024, 3, 1, 11), "snake_case_name"),
                msg("m4", "c1", ts(2024, 3, 1, 12), "snakeXcaseXname"),
            ],
        )
        percent = self.store.messages("wechat", "acc1", "c1", q="100%")
        self.assertEqual(percent["total"], 1)
        self.assertEqual(percent["items"][0]["text"], "discount 100% off")

        underscore = self.store.messages("wechat", "acc1", "c1", q="_")
        self.assertEqual(underscore["total"], 1)
        self.assertEqual(underscore["items"][0]["text"], "snake_case_name")

        # 'snake_case' must not match 'snakeXcase' via the wildcard.
        self.assertEqual(self.store.messages("wechat", "acc1", "c1", q="snake_case")["total"], 1)

    # ------------------------------------------------------------------
    def test_pagination_and_has_more(self):
        self.store.ingest(
            "wechat",
            "acc1",
            [conv("c1")],
            [msg(f"m{i}", "c1", ts(2024, 3, 1, 9) + i, f"line-{i}") for i in range(25)],
        )
        page1 = self.store.messages("wechat", "acc1", "c1", limit=10, offset=0)
        self.assertEqual(len(page1["items"]), 10)
        self.assertEqual(page1["total"], 25)
        self.assertTrue(page1["has_more"])
        # newest first: line-24 .. line-15
        self.assertEqual(page1["items"][0]["text"], "line-24")
        self.assertEqual(page1["items"][-1]["text"], "line-15")

        page3 = self.store.messages("wechat", "acc1", "c1", limit=10, offset=20)
        self.assertEqual(len(page3["items"]), 5)
        self.assertFalse(page3["has_more"])

    def test_conversations_pagination_and_search(self):
        self.store.ingest(
            "wechat",
            "acc1",
            [
                conv("c1", title="Alpha", last_timestamp=ts(2024, 3, 1, 9)),
                conv("c2", title="Beta", last_timestamp=ts(2024, 3, 1, 10)),
                conv("c3", title="Alpha group", last_timestamp=ts(2024, 3, 1, 11)),
            ],
            [],
        )
        listed = self.store.conversations("all")
        self.assertEqual([c["title"] for c in listed["items"]], ["Alpha group", "Beta", "Alpha"])
        self.assertEqual(self.store.conversations("all", q="Alpha")["total"], 2)
        page = self.store.conversations("all", limit=2, offset=0)
        self.assertEqual(len(page["items"]), 2)
        self.assertEqual(page["total"], 3)

    # ------------------------------------------------------------------
    def test_search_joins_conversation_title(self):
        self.store.ingest(
            "wechat", "acc1", [conv("c1", title="Team Room", kind="group")], [msg("m1", "c1", ts(2024, 3, 1, 9), "ping")]
        )
        result = self.store.search("ping")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["conversation_title"], "Team Room")
        self.assertEqual(result["items"][0]["conversation_kind"], "group")

    def test_search_scopes_and_pagination(self):
        for platform in ("wechat", "qq"):
            self.store.ingest(
                platform,
                "acc1",
                [conv("c1", platform=platform, title=platform)],
                [msg(f"{platform}-{i}", "c1", ts(2024, 3, 1, 9) + i, f"token {i}", platform=platform) for i in range(5)],
            )
        self.assertEqual(self.store.search("token")["total"], 10)
        self.assertEqual(self.store.search("token", platform="wechat")["total"], 5)
        page = self.store.search("token", platform="qq", limit=2, offset=0)
        self.assertEqual(len(page["items"]), 2)
        self.assertTrue(page["has_more"])

    def test_search_cursor_keeps_tied_rows_stable_when_new_messages_arrive(self):
        self.store.ingest("wechat", "acc1", [conv("cursor-room")], [
            msg(f"cursor-{index}", "cursor-room", 100, "cursor token")
            for index in range(7)
        ])
        first = self.store.search("cursor token", limit=3)
        self.assertEqual(len(first["items"]), 3)
        self.assertTrue(first["has_more"])
        self.assertTrue(first["next_cursor"])
        self.store.ingest("wechat", "acc1", [conv("cursor-room")], [
            msg("cursor-newer", "cursor-room", 101, "cursor token")
        ])
        second = self.store.search("cursor token", limit=3, cursor=first["next_cursor"])
        self.assertEqual([item["id"] for item in second["items"]], [
            "cursor-3", "cursor-2", "cursor-1"
        ])
        self.assertFalse(set(item["id"] for item in first["items"]) &
                         set(item["id"] for item in second["items"]))
        self.assertEqual(second["total"], 8)
        with self.assertRaisesRegex(ValueError, "cursor"):
            self.store.search("cursor token", cursor="not-a-cursor")

    def test_conversation_message_cursor_uses_timestamp_and_rowid(self):
        self.store.ingest("qq", "acc1", [conv("cursor-room", platform="qq")], [
            msg(f"row-{index}", "cursor-room", 5, f"same timestamp {index}", platform="qq")
            for index in range(5)
        ])
        first = self.store.messages("qq", "acc1", "cursor-room", limit=2)
        self.assertEqual([item["id"] for item in first["items"]], ["row-4", "row-3"])
        second = self.store.messages("qq", "acc1", "cursor-room", limit=2, cursor=first["next_cursor"])
        self.assertEqual([item["id"] for item in second["items"]], ["row-2", "row-1"])
        self.assertTrue(second["has_more"])
        third = self.store.messages("qq", "acc1", "cursor-room", limit=2, cursor=second["next_cursor"])
        self.assertEqual([item["id"] for item in third["items"]], ["row-0"])
        self.assertFalse(third["has_more"])

    # ------------------------------------------------------------------
    def test_contacts(self):
        self.store.ingest(
            "wechat",
            "acc1",
            [],
            [],
            [contact("u1", name="Alice"), contact("u2", name="Bob"), contact("u3", name="Alicia")],
        )
        self.assertEqual(self.store.contacts("all")["total"], 3)
        self.assertEqual(self.store.contacts("all", q="Alic")["total"], 2)
        names = [c["name"] for c in self.store.contacts("all")["items"]]
        self.assertEqual(names, ["Alice", "Alicia", "Bob"])

    def test_media_filters_and_title(self):
        self.store.ingest(
            "wechat",
            "acc1",
            [conv("c1", title="Photos")],
            [
                msg("m1", "c1", ts(2024, 3, 1, 9), "text", type="text"),
                msg("m2", "c1", ts(2024, 3, 1, 10), "", type="image", media_url="http://x/1.jpg"),
                msg("m3", "c1", ts(2024, 3, 1, 11), "", type="voice"),
                msg("m4", "c1", ts(2024, 3, 1, 12), "", type="link"),
            ],
        )
        media = self.store.media("all")
        self.assertEqual(media["total"], 2)
        self.assertEqual(sorted(i["type"] for i in media["items"]), ["image", "voice"])
        self.assertEqual(media["items"][0]["conversation_title"], "Photos")
        self.assertEqual(self.store.media("all", type="image")["total"], 1)
        self.assertEqual(self.store.media("all", q="Photos")["total"], 2)

    # ------------------------------------------------------------------
    def test_stats(self):
        self.store.ingest(
            "wechat",
            "acc1",
            [conv("c1")],
            [
                msg("m1", "c1", ts(2024, 3, 1, 0, 30), "cst-boundary", type="text"),
                msg("m2", "c1", ts(2024, 3, 1, 9), "", type="image"),
                msg("m3", "c1", ts(2024, 2, 29, 22), "prev-day", type="text"),
            ],
            [contact("u1")],
        )
        self.store.ingest(
            "qq", "acc1", [conv("c1", platform="qq")], [msg("q1", "c1", ts(2024, 3, 2, 9), "qq", platform="qq")]
        )

        stats = self.store.stats("all")
        self.assertEqual(stats["message_count"], 4)
        self.assertEqual(stats["conversation_count"], 2)
        self.assertEqual(stats["contact_count"], 1)
        self.assertEqual(stats["media_count"], 1)
        self.assertEqual(stats["per_platform"]["wechat"]["messages"], 3)
        self.assertEqual(stats["per_platform"]["qq"]["messages"], 1)
        self.assertEqual(stats["per_type"]["text"], 3)
        self.assertEqual(stats["per_type"]["image"], 1)

        # 2024-03-01 00:30 CST is 2024-02-29 16:30 UTC: +08:00 grouping must win.
        daily = {entry["date"]: entry["count"] for entry in stats["daily"]}
        self.assertEqual(daily["2024-03-01"], 2)
        self.assertEqual(daily["2024-02-29"], 1)
        self.assertEqual(daily["2024-03-02"], 1)

        scoped = self.store.stats("wechat")
        self.assertEqual(scoped["message_count"], 3)
        self.assertEqual(set(scoped["per_platform"].keys()), {"wechat"})

    def test_count_scopes(self):
        self.store.ingest("wechat", "acc1", [conv("c1")], [msg("m1", "c1", ts(2024, 3, 1, 9), "a")])
        self.store.ingest("qq", "acc1", [conv("c1", platform="qq")], [msg("q1", "c1", ts(2024, 3, 1, 9), "b", platform="qq")])
        self.assertEqual(self.store.count("all"), 2)
        self.assertEqual(self.store.count("wechat"), 1)
        self.assertEqual(self.store.count("wecom"), 0)

    # ------------------------------------------------------------------
    def test_thread_safe_reads(self):
        self.store.ingest(
            "wechat",
            "acc1",
            [conv("c1")],
            [msg(f"m{i}", "c1", ts(2024, 3, 1, 9) + i, f"row {i}") for i in range(50)],
        )
        errors = []

        def worker():
            try:
                for _ in range(20):
                    self.assertEqual(self.store.count("all"), 50)
                    self.store.search("row")
            except Exception as exc:  # pragma: no cover - failure reporting
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])

    def test_close_is_idempotent(self):
        self.store.close()
        self.store.close()

    def test_online_backup_and_restore_preserve_index(self):
        source_message = msg("backup-message", "backup-room", ts(2026, 1, 2), "备份内容")
        self.store.ingest("wechat", "acc1", [conv("backup-room")], [source_message])
        with tempfile.TemporaryDirectory() as temp:
            backup_path = os.path.join(temp, "nested", "snapshot.sqlite3")
            self.store.backup_to(backup_path)

            self.store.ingest("qq", "acc2", [conv("qq-room", platform="qq", account="acc2")], [
                msg("new-message", "qq-room", ts(2026, 1, 3), "临时内容", platform="qq", account="acc2")
            ])
            self.store.restore_from(backup_path)

            self.assertEqual(self.store.count(), 1)
            result = self.store.search("备份内容")
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["items"][0]["id"], "backup-message")
            self.assertEqual(self.store.stats()["message_count"], 1)

    def test_restore_rejects_invalid_or_newer_database_without_mutation(self):
        self.store.ingest("wechat", "acc1", [conv("room")], [msg("keep", "room", 1, "保留")])
        with tempfile.TemporaryDirectory() as temp:
            invalid = os.path.join(temp, "invalid.sqlite3")
            with open(invalid, "wb") as handle:
                handle.write(b"not a database")
            with self.assertRaises(sqlite3.DatabaseError):
                self.store.restore_from(invalid)
            self.assertEqual(self.store.count(), 1)

            newer = os.path.join(temp, "newer.sqlite3")
            self.store.backup_to(newer)
            connection = sqlite3.connect(newer)
            connection.execute("PRAGMA user_version=999")
            connection.close()
            with self.assertRaisesRegex(ValueError, "更新版本"):
                self.store.restore_from(newer)
            self.assertEqual(self.store.count(), 1)

            incomplete = os.path.join(temp, "incomplete.sqlite3")
            connection = sqlite3.connect(incomplete)
            connection.executescript("""
                CREATE TABLE conversations (id TEXT);
                CREATE TABLE messages (id TEXT);
                CREATE TABLE contacts (id TEXT);
            """)
            connection.close()
            with self.assertRaisesRegex(ValueError, "结构与消息索引不兼容"):
                self.store.restore_from(incomplete)
            self.assertEqual(self.store.count(), 1)

    def test_fts_trigram_preserves_literal_substring_search_and_tracks_updates(self):
        self.assertTrue(self.store._fts_enabled)
        self.store.ingest("wechat", "acc1", [conv("fts-room")], [
            msg("fts-1", "fts-room", 10, '中文内容 hello "%_')
        ])
        for query in ("中文内", "hello", '"%_'):
            self.assertEqual(self.store.search(query)["total"], 1, query)
        self.assertEqual(self.store.search("文")["total"], 1)

        self.store.ingest("wechat", "acc1", [conv("fts-room")], [
            msg("fts-1", "fts-room", 10, "更新后的文字")
        ])
        self.assertEqual(self.store.search("中文内容")["total"], 0)
        self.assertEqual(self.store.search("更新后的")["total"], 1)

    def test_message_context_returns_stable_neighbourhood_and_bounds(self):
        self.store.ingest("qq", "acc1", [conv("context-room", platform="qq")], [
            msg(f"context-{index}", "context-room", 100 + index, f"row {index}", platform="qq")
            for index in range(20)
        ])
        result = self.store.message_context("qq", "acc1", "context-10", before=3, after=2)
        self.assertEqual([item["id"] for item in result["items"]], [
            "context-7", "context-8", "context-9", "context-10", "context-11", "context-12"
        ])
        self.assertEqual(result["target_index"], 3)
        self.assertTrue(result["has_more_before"])
        self.assertTrue(result["has_more_after"])
        bounded = self.store.message_context("qq", "acc1", "context-10", before=500, after=500)
        self.assertEqual(len(bounded["items"]), 20)
        self.assertIsNone(self.store.message_context("qq", "acc1", "missing"))

    def test_upgrade_rebuilds_fts_index_from_existing_messages(self):
        self.store.ingest("wechat", "acc1", [conv("legacy-room")], [
            msg("legacy-message", "legacy-room", 10, "迁移后仍能检索")
        ])
        self.store.close()
        connection = sqlite3.connect(self.path)
        connection.executescript("""
            DROP TRIGGER messages_fts_insert;
            DROP TRIGGER messages_fts_delete;
            DROP TRIGGER messages_fts_update;
            DROP TABLE messages_fts;
            PRAGMA user_version=1;
        """)
        connection.close()
        self.store = ChatStore(self.path)
        self.assertTrue(self.store._fts_enabled)
        self.assertEqual(self.store.search("迁移后仍")["total"], 1)

    def test_mcp_operation_log_is_bounded_and_content_free(self):
        self.store.record_mcp_operation("search_messages", "success")
        self.store.record_mcp_operation("list_contacts", "error")
        operations = self.store.mcp_operations()
        self.assertEqual([item["tool"] for item in operations], ["list_contacts", "search_messages"])
        self.assertEqual(operations[0]["status"], "error")
        self.assertNotIn("query", operations[0])

    def test_management_metadata_and_audit_tables_are_separate_from_messages(self):
        columns = lambda table: {row[1] for row in self.store._conn.execute(f'PRAGMA table_info("{table}")')}
        self.assertTrue({"note", "tags", "archived"}.issubset(columns("conversation_metadata")))
        self.assertIn("identity_id", columns("contact_identities"))
        self.assertIn("details", columns("operation_log"))
        self.assertNotIn("note", columns("messages"))
        self.assertNotIn("tags", columns("messages"))
        self.assertEqual(self.store._conn.execute("PRAGMA user_version").fetchone()[0], 7)

    def test_local_conversation_metadata_and_contact_identity_are_audited_separately(self):
        self.store.ingest("wechat", "acc1", [conv("managed", title="Managed")],
                          [msg("managed-message", "managed", 10, "source body")],
                          [contact("person", name="Person")])
        metadata = self.store.update_conversation_metadata(
            "wechat", "acc1", "managed", note="private note", tags=["work", "priority", "work"], archived=True)
        self.assertEqual(metadata, {"note": "private note", "tags": ["work", "priority"], "archived": True})
        listed = self.store.conversations("wechat")["items"][0]
        self.assertEqual((listed["note"], listed["tags"], listed["archived"]),
                         ("private note", ["work", "priority"], True))
        self.assertEqual(self.store.get_message("wechat", "acc1", "managed-message")["text"], "source body")
        self.store.set_contact_identity("wechat", "acc1", "person", "person-unified-1")
        self.assertEqual(self.store.contacts("wechat")["items"][0]["identity_id"], "person-unified-1")
        self.store.remove_contact_identity("wechat", "acc1", "person")
        self.assertIsNone(self.store.contacts("wechat")["items"][0]["identity_id"])
        operations = self.store.operation_log()
        self.assertEqual([item["action"] for item in operations], [
            "contact_identity.unlink", "contact_identity.link", "conversation_metadata.update"])
        self.assertNotIn("private note", str(operations))

    def test_local_metadata_rejects_invalid_or_missing_targets(self):
        with self.assertRaises(ValueError):
            self.store.update_conversation_metadata("wechat", "acc1", "missing", note="x")
        with self.assertRaises(ValueError):
            self.store.update_conversation_metadata("wechat", "acc1", "missing", tags="not-a-list")

    def test_invalid_platform_raises(self):
        with self.assertRaises(ValueError):
            self.store.ingest("unsupported", "acc1", [], [])


class ExportTestCase(unittest.TestCase):
    def setUp(self):
        self.messages = [
            {
                "id": "m1",
                "platform": "wechat",
                "account_id": "acc1",
                "conversation_id": "c1",
                "conversation_title": "团队",
                "sender_id": "u1",
                "sender_name": "张三",
                "timestamp": ts(2024, 3, 1, 9, 5, 0),
                "type": "text",
                "text": "你好，世界",
                "is_self": False,
                "media_url": None,
                "file_name": None,
                "raw_type": "text",
            },
            {
                "id": "m2",
                "platform": "qq",
                "account_id": "acc1",
                "conversation_id": "c1",
                "conversation_title": "团队",
                "sender_id": "u2",
                "sender_name": "=cmd|' /C calc'!A0",
                "timestamp": ts(2024, 3, 1, 9, 6, 0),
                "type": "image",
                "text": "<script>alert('x')</script>",
                "is_self": True,
                "media_url": "http://example/1.png",
                "file_name": "a.png",
                "raw_type": "image",
            },
        ]

    def test_json_preserves_unicode_and_provenance(self):
        payload, mime, filename = render_export(self.messages, "json")
        self.assertEqual(mime, "application/json")
        self.assertEqual(filename, "export.json")
        data = json.loads(payload.decode("utf-8"))
        self.assertEqual(data[0]["text"], "你好，世界")
        self.assertEqual(data[1]["platform"], "qq")
        self.assertEqual(data[0]["timestamp_text"], "2024-03-01 09:05:00")

    def test_csv_protects_formula_injection(self):
        payload, mime, filename = render_export(self.messages, "csv")
        self.assertTrue(mime.startswith("text/csv"))
        text = payload.decode("utf-8")
        self.assertNotIn(",=cmd", text)
        self.assertIn("'=cmd|' /C calc'!A0", text)
        self.assertIn("你好，世界", text)

    def test_csv_protects_plus_and_at(self):
        rows = [dict(self.messages[0], text="+1"), dict(self.messages[0], id="x", text="@x")]
        payload, _, _ = render_export(rows, "csv")
        text = payload.decode("utf-8")
        self.assertIn("'+1", text)
        self.assertIn("'@x", text)

    def test_html_escapes_all_user_values(self):
        payload, mime, filename = render_export(self.messages, "html", title="<b>Ex</b>")
        self.assertTrue(mime.startswith("text/html"))
        body = payload.decode("utf-8")
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertNotIn("<b>Ex</b>", body)
        self.assertIn("&lt;b&gt;Ex&lt;/b&gt;", body)
        self.assertIn("charset=\"utf-8\"", body)
        # self-contained: no remote resource references
        self.assertNotIn("http://example/1.png\"", body.replace("&quot;", '"'))

    def test_md_and_txt_contain_unicode(self):
        md, md_mime, _ = render_export(self.messages, "md")
        self.assertTrue(md_mime.startswith("text/markdown"))
        self.assertIn("你好，世界", md.decode("utf-8"))
        txt, txt_mime, _ = render_export(self.messages, "txt")
        self.assertTrue(txt_mime.startswith("text/plain"))
        self.assertIn("张三", txt.decode("utf-8"))

    def test_unknown_format_raises(self):
        with self.assertRaises(ValueError):
            render_export(self.messages, "pdf")
        with self.assertRaises(ValueError):
            render_export(self.messages, None)

    def test_format_is_case_insensitive(self):
        payload, _, _ = render_export(self.messages, " HTML ")
        self.assertIn(b"<!DOCTYPE html>", payload)

    def test_export_is_deterministic_bytes(self):
        first = render_export(self.messages, "json")
        second = render_export(self.messages, "json")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main(verbosity=2)
