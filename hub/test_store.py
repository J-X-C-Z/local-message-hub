"""Focused unit tests for :mod:`hub.store` and :mod:`hub.exports`.

All fixtures are synthetic and generated in-memory / in temporary directories.
No real chat content is read or written.

Run with::

    python -m unittest hub.test_store -v
"""

from __future__ import annotations

import json
import csv
import io
import os
import sqlite3
import zipfile
import xml.etree.ElementTree as ET
import tempfile
import threading
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

try:  # package-relative when imported as hub.test_store
    from .exports import render_contact_export, render_group_member_export, render_export
    from .store import ChatStore, parse_bound
except ImportError:  # direct execution from inside the package directory
    from exports import render_contact_export, render_group_member_export, render_export
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

    def test_moments_catalog_migrates_v7_and_pages_sanitized_account_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = os.path.join(temp, "sns-cache")
            os.mkdir(root)
            with open(os.path.join(root, "photo.dat"), "wb") as handle:
                handle.write(b"image")
            self.store._conn.execute("PRAGMA user_version=7")
            self.store.close()
            self.store = ChatStore(self.path)
            self.assertEqual(self.store._conn.execute("PRAGMA user_version").fetchone()[0], 9)
            self.assertIn("idx_msg_platform_ts", {
                row[1] for row in self.store._conn.execute("PRAGMA index_list(messages)")
            })
            posts = [
                {"id": "a", "username": "alice", "nickname": "Alice", "createTime": 20,
                 "contentDesc": "needle post", "type": 1, "likes": ["bob"],
                 "comments": [{"username": "bob", "content": "nice", "secret": "drop"}],
                 "location": {"city": "HK", "secret": "drop"},
                 "remote_url": "https://private.invalid/path", "token": "secret-token",
                 "media": [{"type": "image", "md5": "abc", "width": 20,
                            "_local_path": os.path.join(root, "photo.dat"),
                            "url": "https://private.invalid/image", "key": "secret-key"}]},
                {"id": "b", "username": "bob", "createTime": 30, "contentDesc": "other",
                 "media": [{"type": "video", "_local_path": "missing.mp4"}]},
            ]
            self.assertEqual(self.store.replace_moments("acc1", posts, root)["total"], 2)
            self.store.replace_moments("acc2", [], root)
            self.assertEqual(self.store.moment_accounts(), ["acc1", "acc2"])
            self.assertEqual(self.store.moments("acc2")["available"], True)
            self.assertEqual(self.store.moments("absent")["available"], False)
            page = self.store.moments("acc1", keyword="needle", limit=1)
            self.assertEqual(page["total"], 1)
            self.assertEqual(page["items"][0]["id"], "a")
            self.assertEqual(self.store.moments("acc1", keyword="nice")["total"], 1)
            self.assertEqual(self.store.moments("acc1", keyword="private.invalid")["total"], 0)
            self.assertEqual(self.store.moments("acc1", keyword="bob")["total"], 0)
            self.assertEqual(page["items"][0]["media"][0]["_cache_relpath"], "photo.dat")
            self.assertTrue(page["items"][0]["media"][0]["local_available"])
            serialized = json.dumps(page, ensure_ascii=False)
            for forbidden in ("private.invalid", "secret-token", "secret-key", root, "drop"):
                self.assertNotIn(forbidden, serialized)
            self.assertEqual(self.store.moments("acc1", username="bob")["total"], 1)
            self.assertEqual(self.store.moments("acc1", start=20, end=20)["total"], 1)
            self.assertEqual(self.store.moments("acc2")["total"], 0)

    def test_moments_catalog_replace_rolls_back_and_rejects_escaped_or_symlink_cache_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = os.path.join(temp, "cache")
            os.mkdir(root)
            outside = os.path.join(temp, "outside")
            with open(outside, "wb") as handle:
                handle.write(b"outside")
            symlink = os.path.join(root, "linked")
            try:
                os.symlink(outside, symlink)
            except (OSError, NotImplementedError):
                symlink = None
            self.store.replace_moments("acc1", [{"id": "keep", "createTime": 1, "contentDesc": "old"}], root)
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.replace_moments("acc1", [{"id": "duplicate"}, {"id": "duplicate"}], root)
            self.assertEqual(self.store.moments("acc1")["items"][0]["id"], "keep")
            media = [{"type": "image", "_local_path": "../outside"}]
            if symlink:
                media.append({"type": "image", "_local_path": symlink})
            self.store.replace_moments("acc1", [{"id": "safe", "media": media}], root)
            result = self.store.moments("acc1")["items"][0]
            self.assertTrue(all(not item.get("local_available") and "_cache_relpath" not in item
                                for item in result["media"]))

    def test_moments_catalog_can_index_text_without_an_available_media_cache(self):
        result = self.store.replace_moments("account", [{
            "id": "text-only", "contentDesc": "still searchable",
            "media": [{"_local_path": "not-present.jpg", "type": "image"}],
        }], None)
        self.assertEqual(result["total"], 1)
        page = self.store.moments("account", keyword="searchable")
        self.assertTrue(page["catalogued"])
        self.assertFalse(page["items"][0]["media"][0]["local_available"])

    def test_moments_page_loads_media_with_one_query_and_preserves_order(self):
        self.store.replace_moments("account", [
            {"id": post_id, "createTime": stamp,
             "media": [{"width": stamp * 10}, {"width": stamp * 10 + 1}]}
            for stamp, post_id in enumerate(("a", "b", "c"), 1)
        ], None)
        media_queries = []
        self.store._conn.set_trace_callback(
            lambda sql: media_queries.append(sql) if "FROM moments_media" in sql else None)
        try:
            page = self.store.moments("account", limit=3)
        finally:
            self.store._conn.set_trace_callback(None)
        self.assertEqual(len(media_queries), 1)
        self.assertEqual([item["id"] for item in page["items"]], ["c", "b", "a"])
        self.assertEqual([media["width"] for media in page["items"][0]["media"]], [30, 31])

    def test_moments_catalog_refreshes_text_when_cache_root_disappears(self):
        with tempfile.TemporaryDirectory() as temp:
            root = os.path.join(temp, "sns-cache")
            os.mkdir(root)
            os.rmdir(root)
            result = self.store.replace_moments("account", [{
                "id": "offline-cache", "contentDesc": "text remains searchable",
                "media": [{"_local_path": os.path.join(root, "photo.jpg"), "type": "image"}],
            }], root)
        self.assertEqual(result["total"], 1)
        page = self.store.moments("account", keyword="searchable")
        self.assertEqual([item["id"] for item in page["items"]], ["offline-cache"])
        self.assertFalse(page["items"][0]["media"][0]["local_available"])

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

    def test_contact_avatars_are_attached_to_conversations_and_messages(self):
        qq_avatar = "https://q.qlogo.cn/g?b=qq&nk=10001&s=100"
        sender_avatar = "https://thirdqq.qlogo.cn/g?b=qq&nk=10002&s=100"
        self.store.ingest(
            "qq", "account", [conv("peer", platform="qq", account="account", title="Peer")],
            [msg("m1", "peer", ts(2024, 3, 1, 9), "hello avatar", platform="qq",
                 account="account", sender="10002")],
            [contact("peer", platform="qq", account="account", avatar_url=qq_avatar),
             contact("10002", platform="qq", account="account", avatar_url=sender_avatar)],
        )
        conversation = self.store.conversations("qq")["items"][0]
        self.assertEqual(conversation["avatar_url"], qq_avatar)
        message = self.store.messages("qq", "account", "peer")["items"][0]
        self.assertEqual(message["sender_avatar_url"], sender_avatar)
        self.assertEqual(self.store.search("hello avatar", "qq")["items"][0]["sender_avatar_url"], sender_avatar)

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

    def test_conversation_preview_hides_structured_content_and_keeps_message(self):
        body = '<msg><url>https://login.invalid/?access_token=synthetic-secret</url></msg>'
        self.store.ingest("wechat", "acc1", [conv("structured-room")], [
            msg("structured-message", "structured-room", 1, body)
        ])

        self.assertEqual(self.store.conversations("all")["items"][0]["last_message"], "[结构化消息]")
        self.assertEqual(self.store.messages("wechat", "acc1", "structured-room")["items"][0]["text"], body)

    def test_conversation_preview_sanitizes_legacy_values_and_bounds_plain_text(self):
        self.store.ingest("wechat", "acc1", [conv("legacy-room")], [
            msg("legacy-message", "legacy-room", 1, "initial")
        ])
        self.store._conn.execute(
            "UPDATE conversations SET last_message=? WHERE platform='wechat' AND account_id='acc1' AND id='legacy-room'",
            ('Sign in https://login.invalid/path?token=synthetic-secret',),
        )
        self.assertEqual(self.store.get_conversation("wechat", "acc1", "legacy-room")["last_message"],
                         "Sign in [链接]")

        self.store.ingest("wechat", "acc1", [conv("long-room")], [
            msg("long-message", "long-room", 2, "x" * 400)
        ])
        preview = self.store.conversations("all")["items"]
        long_preview = next(row["last_message"] for row in preview if row["id"] == "long-room")
        self.assertEqual(len(long_preview), 241)
        self.assertTrue(long_preview.endswith("…"))

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

    def test_export_keyset_pages_preserve_chronological_tie_order(self):
        self.store.ingest("wechat", "acc1", [conv("c1")], [
            msg("b", "c1", 100, "wechat-b"),
            msg("a", "c1", 100, "wechat-a"),
        ])
        self.store.ingest("qq", "acc2", [conv("c1")], [
            msg("a", "c1", 100, "qq-a"),
        ])

        after = None
        rows = []
        while True:
            page = self.store.export_page("all", after=after, limit=1)
            if after is None:
                self.assertEqual(page["total"], 3)
            else:
                self.assertIsNone(page["total"])
            rows.extend(page["items"])
            after = page["next_after"]
            if after is None:
                break

        self.assertEqual([(row["id"], row["platform"], row["text"]) for row in rows], [
            ("a", "qq", "qq-a"), ("a", "wechat", "wechat-a"), ("b", "wechat", "wechat-b"),
        ])

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

    def test_conversations_null_timestamps_follow_ties_across_pages(self):
        self.store.ingest("qq", "acc1", [
            conv("new", last_timestamp=30), conv("tie-b", last_timestamp=20),
            conv("tie-a", last_timestamp=20), conv("zero", last_timestamp=0),
            conv("empty-b"), conv("empty-a"),
        ], [])
        self.store.ingest("wechat", "acc2", [conv("other", last_timestamp=25)], [])
        for platform, expected in (
            ("qq", ["new", "tie-a", "tie-b", "zero", "empty-a", "empty-b"]),
            ("all", ["new", "other", "tie-a", "tie-b", "zero", "empty-a", "empty-b"]),
        ):
            with self.subTest(platform=platform):
                pages = [self.store.conversations(platform, limit=2, offset=offset)
                         for offset in range(0, len(expected), 2)]
                self.assertEqual([item["id"] for page in pages for item in page["items"]], expected)
                self.assertTrue(all(page["total"] == len(expected) for page in pages))

    # ------------------------------------------------------------------
    def test_search_joins_conversation_title(self):
        self.store.ingest(
            "wechat", "acc1", [conv("c1", title="Team Room", kind="group")], [msg("m1", "c1", ts(2024, 3, 1, 9), "ping")]
        )
        result = self.store.search("ping")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["conversation_title"], "Team Room")
        self.assertEqual(result["items"][0]["conversation_kind"], "group")

    def test_search_uses_exact_account_contact_avatar_when_conversation_avatar_is_missing(self):
        fallback = "https://wx.qlogo.cn/account-one/avatar"
        other_account = "https://wx.qlogo.cn/account-two/avatar"
        self.store.ingest(
            "wechat", "acc1", [conv("peer", title="Alice")],
            [msg("m1", "peer", 1, "avatar search")],
            [contact("peer", account="acc1", avatar_url=fallback)],
        )
        self.store.ingest("wechat", "acc2", [conv("peer")], [],
                          [contact("peer", account="acc2", avatar_url=other_account)])

        result = self.store.search("avatar search")

        self.assertEqual(result["items"][0]["conversation_avatar_url"], fallback)

    def test_search_uses_contact_avatar_when_conversation_avatar_is_empty(self):
        fallback = "https://wx.qlogo.cn/contact/avatar"
        self.store.ingest(
            "wechat", "acc1", [conv("peer", title="Alice", avatar_url="")],
            [msg("m1", "peer", 1, "empty avatar search")],
            [contact("peer", account="acc1", avatar_url=fallback)],
        )

        self.assertEqual(self.store.search("empty avatar search")["items"][0]["conversation_avatar_url"], fallback)

    def test_search_prefers_conversation_avatar_over_contact_avatar(self):
        self.store.ingest(
            "wechat", "acc1", [conv("peer", avatar_url="local-conversation-avatar")],
            [msg("m1", "peer", 1, "avatar priority")],
            [contact("peer", avatar_url="contact-avatar")],
        )

        self.assertEqual(self.store.search("avatar priority")["items"][0]["conversation_avatar_url"],
                         "local-conversation-avatar")

    def test_search_preview_bounds_returned_text_without_changing_full_search(self):
        body = "synthetic match " + "x" * (2 * 1024 * 1024)
        self.store.ingest("wechat", "acc1", [conv("preview-room")], [
            msg("preview-message", "preview-room", 1, body)
        ])

        preview = self.store.search("synthetic match", preview_chars=1200)
        full = self.store.search("synthetic match")

        self.assertEqual(preview["total"], 1)
        self.assertEqual(len(preview["items"][0]["text"]), 1200)
        self.assertEqual(full["items"][0]["text"], body)

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

    def test_platform_search_order_uses_timestamp_index_without_temp_sort(self):
        self.store._conn.executemany(
            "INSERT INTO messages(platform,account_id,id,conversation_id,timestamp,type,text) "
            "VALUES(?,?,?,?,?,?,?)",
            ((platform, "account", f"{platform}-{index}", "room", index, "text", "body")
             for platform in ("wechat", "wecom", "qq") for index in range(10000)),
        )
        self.store._conn.execute("ANALYZE")
        plan = self.store._conn.execute(
            "EXPLAIN QUERY PLAN SELECT rowid,* FROM messages WHERE platform=? "
            "ORDER BY timestamp DESC,rowid DESC LIMIT 101", ("wechat",)
        ).fetchall()
        details = " ".join(row[3] for row in plan)
        self.assertIn("idx_msg_platform_ts", details)
        self.assertNotIn("TEMP B-TREE", details.upper())

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
            [contact("u1", name="Alice", nickname="Ally", alias="a-1", remark="A"),
             contact("u2", name="Bob"), contact("u3", name="Alicia")],
        )
        self.store.ingest("wechat", "acc2", [], [],
                          [contact("u1", account="acc2", name="Different account")])
        self.store.ingest("wechat", "acc1", [], [], [
            contact("gh-official", name="News", kind="official",
                    official_account_kind="subscription", local_type=1,
                    raw_biz_type=0),
        ])
        self.assertEqual(self.store.contacts("all")["total"], 5)
        self.assertEqual(self.store.contacts("all", q="Alic")["total"], 2)
        self.assertEqual(self.store.contacts("all", q="Ally")["items"][0]["id"], "u1")
        self.assertEqual(self.store.contacts("all", q="a-1")["items"][0]["id"], "u1")
        self.assertEqual(self.store.contacts("all", q="u2")["items"][0]["id"], "u2")
        self.assertEqual(self.store.contacts("wechat", account_id="acc2")["items"][0]["name"],
                         "Different account")
        self.assertEqual(self.store.contacts("wechat", account_id="acc1")["total"], 4)
        officials = self.store.contacts("wechat", account_id="acc1", kind="official")
        self.assertEqual([row["id"] for row in officials["items"]], ["gh-official"])
        self.assertEqual(officials["items"][0]["extra"]["official_account_kind"], "subscription")
        self.assertEqual(len(self.store.contacts("all")["accounts"]), 2)

    def test_contact_pagination_has_stable_cross_account_tie_breakers(self):
        rows = [
            contact("same-id", platform="wechat", account="z", name="Shared"),
            contact("same-id", platform="wechat", account="a", name="Shared"),
            contact("same-id", platform="qq", account="a", name="Shared"),
        ]
        self.store.ingest("wechat", "z", [], [], [rows[0]])
        self.store.ingest("wechat", "a", [], [], [rows[1]])
        self.store.ingest("qq", "a", [], [], [rows[2]])

        first = self.store.contacts("all", limit=1)
        second = self.store.contacts("all", limit=1, offset=1)
        third = self.store.contacts("all", limit=1, offset=2)
        page_keys = [
            (item["platform"], item["account_id"], item["id"])
            for page in (first, second, third)
            for item in page["items"]
        ]
        self.assertEqual(page_keys, [
            ("qq", "a", "same-id"),
            ("wechat", "a", "same-id"),
            ("wechat", "z", "same-id"),
        ])
        self.assertEqual(
            page_keys,
            [(item["platform"], item["account_id"], item["id"])
             for item in self.store.contacts("all", limit=3)["items"]],
        )

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
        self.assertEqual(self.store.media("all", start="2024-03-01", end="2024-03-01")["total"], 2)
        self.assertEqual(self.store.media("all", start="2024-03-01", end="2024-03-01", type="image", q="Photos")["total"], 1)
        self.assertEqual(self.store.media("all", start="2024-03-02")["total"], 0)

    def test_media_attachment_projection_filters_and_pages_by_resource(self):
        with self.store._lock:
            self.assertEqual(self.store._require_connection().execute("SELECT json_valid('{}')").fetchone()[0], 1)
        self.store.ingest("wecom", "acc1", [conv("wecom-room", platform="wecom", title="Mixed")], [
            msg("mixed", "wecom-room", 300, "invoice-keyphrase", type="image", platform="wecom",
                sender="wecom-user-7", sender_name="Morgan Sender",
                attachments=[{"name": "photo.png", "type": "image", "p": "private/cache/path"},
                            {"name": "notes.pdf", "type": "file"}]),
        ])
        self.store.ingest("qq", "acc1", [conv("qq-room", platform="qq", title="Roaming")], [
            msg("roaming", "qq-room", 200, "", type="text", platform="qq",
                roaming_resources=[{"name": "clip.png", "type": "image"},
                                   {"name": "clip.mp4", "type": "video"}]),
        ])
        self.store.ingest("wechat", "acc1", [conv("wx-room", title="Photo")], [
            msg("plain-image", "wx-room", 100, "", type="image"),
        ])

        page = self.store.media("all", limit=2, expand_attachments=True)
        self.assertEqual(page["total"], 5)
        self.assertEqual([(item["id"], item["media_entry"]["resource_index"])
                          for item in page["items"]], [("mixed", 0), ("mixed", 1)])
        next_page = self.store.media("all", limit=2, offset=2, expand_attachments=True)
        self.assertEqual([(item["id"], item["media_entry"]["resource_index"])
                          for item in next_page["items"]], [("roaming", 0), ("roaming", 1)])
        final_page = self.store.media("all", limit=2, offset=4, expand_attachments=True)
        self.assertEqual([(item["id"], item["media_entry"]["resource_index"])
                          for item in final_page["items"]], [("plain-image", None)])
        images = self.store.media("all", type="image", expand_attachments=True)
        self.assertEqual(images["total"], 3)
        self.assertEqual({item["id"] for item in images["items"]}, {"mixed", "roaming", "plain-image"})
        files = self.store.media("all", type="file", expand_attachments=True)
        self.assertEqual([(item["id"], item["media_entry"]["resource"]["name"])
                          for item in files["items"]], [("mixed", "notes.pdf")])
        named = self.store.media("all", q="photo.png", expand_attachments=True)
        self.assertEqual(named["total"], 1)
        self.assertEqual(named["items"][0]["media_entry"]["resource"]["name"], "photo.png")
        roaming_name = self.store.media("all", q="clip.mp4", expand_attachments=True)
        self.assertEqual([(item["id"], item["media_entry"]["type"])
                          for item in roaming_name["items"]], [("roaming", "video")])
        self.assertEqual(self.store.media("all", q="invoice-keyphrase", source="chat",
                                          expand_attachments=True)["total"], 2)
        sender_name = self.store.media("all", q="Morgan Sender", source="all",
                                       expand_attachments=True)
        self.assertEqual([(item["id"], item["media_entry"]["resource_index"])
                          for item in sender_name["items"]], [("mixed", 0), ("mixed", 1)])
        sender_id = self.store.media("all", q="wecom-user-7", source="chat",
                                     expand_attachments=True)
        self.assertEqual(sender_id["total"], 2)
        self.assertNotIn("p", page["items"][0]["media_entry"]["resource"])
        self.assertEqual(self.store.media("wecom", type="video", expand_attachments=True)["total"], 0)

    def test_qq_media_alias_is_visible_in_chat_and_unified_media_queries(self):
        self.store.ingest("qq", "acc1", [conv("qq-alias-room", platform="qq", title="QQ Alias")], [
            msg("qq-alias", "qq-alias-room", 100, "", type="text", platform="qq",
                media=[{"name": "alias-photo.png", "type": "image"},
                       {"name": "alias-voice.silk", "type": "audio"}]),
            msg("qq-empty-primary", "qq-alias-room", 90, "", type="text", platform="qq",
                roaming_resources=[], media=[{"name": "must-not-fallback.png", "type": "image"}]),
        ])

        for source in ("chat", "all"):
            with self.subTest(source=source):
                page = self.store.media("qq", source=source, expand_attachments=True)
                self.assertEqual(page["total"], 2)
                self.assertEqual([(item["media_entry"]["resource_index"],
                                   item["media_entry"]["type"])
                                  for item in page["items"]], [(0, "image"), (1, "voice")])
                self.assertEqual(self.store.media("qq", type="image", source=source,
                                                  expand_attachments=True)["total"], 1)
                self.assertEqual(self.store.media("qq", q="alias-voice.silk", source=source,
                                                  expand_attachments=True)["total"], 1)
                self.assertEqual(self.store.media("qq", q="must-not-fallback.png", source=source,
                                                  expand_attachments=True)["total"], 0)

    def test_localized_wecom_attachment_types_filter_as_canonical_media(self):
        self.store.ingest("wecom", "acc1", [conv("localized-room", platform="wecom", title="Localized")], [
            msg("localized", "localized-room", 100, "", type="text", platform="wecom",
                attachments=[{"name": "photo.png", "type": "图片"},
                            {"name": "voice.amr", "type": "语音"},
                            {"name": "clip.mp4", "type": "视频"},
                            {"name": "notes.dat", "type": "文件"}]),
        ])
        for kind, filename in (("image", "photo.png"), ("voice", "voice.amr"),
                               ("video", "clip.mp4"), ("file", "notes.dat")):
            with self.subTest(kind=kind):
                result = self.store.media("wecom", type=kind, expand_attachments=True)
                self.assertEqual(result["total"], 1)
                self.assertEqual(result["items"][0]["media_entry"]["type"], kind)
                self.assertEqual(result["items"][0]["media_entry"]["resource"]["name"], filename)

    def test_media_account_scope_filters_before_count_and_page(self):
        for account_id, message_id in (("acc1", "first"), ("acc2", "second")):
            self.store.ingest("wechat", account_id, [conv("room", title="Photos")], [
                msg(message_id, "room", 100, "", type="image")])
        result = self.store.media("wechat", expand_attachments=True, account_id="acc2")
        self.assertEqual(result["total"], 1)
        self.assertEqual([item["id"] for item in result["items"]], ["second"])
        self.assertEqual(self.store.media("wechat", account_id="acc1")["total"], 1)

    def test_media_conversation_scope_filters_plain_and_expanded_resources(self):
        self.store.ingest("wecom", "acc1", [
            conv("room-a", platform="wecom", title="Same title"),
            conv("room-b", platform="wecom", title="Same title"),
        ], [
            msg("a-image", "room-a", 200, "same body", type="image", platform="wecom"),
            msg("b-image", "room-b", 100, "same body", type="image", platform="wecom"),
        ])

        plain = self.store.media("wecom", conversation_id="room-a")
        self.assertEqual(plain["total"], 1)
        self.assertEqual([item["id"] for item in plain["items"]], ["a-image"])
        expanded = self.store.media("wecom", source="all", conversation_id="room-a",
                                    expand_attachments=True)
        self.assertEqual(expanded["total"], 1)
        self.assertEqual([item["conversation_id"] for item in expanded["items"]], ["room-a"])
        self.assertEqual(self.store.media("wecom", source="moments", conversation_id="room-a",
                                          expand_attachments=True)["total"], 0)

    def test_unified_media_pages_chat_and_moments_in_one_ordered_result(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = os.path.join(temp, "sns")
            os.mkdir(cache)
            photo = os.path.join(cache, "cached.jpg")
            with open(photo, "wb") as handle:
                handle.write(b"fixture")
            self.store.ingest("wechat", "acc1", [conv("room", title="Chat Photos")], [
                msg("chat-image", "room", 200, "", type="image")])
            self.store.replace_moments("acc1", [{"id": "post-1", "username": "alice",
                "nickname": "Alice", "createTime": 300, "contentDesc": "sunset needle",
                "media": [{"type": "image", "_local_path": photo}]}], cache)
            page = self.store.media("all", limit=1, expand_attachments=True)
            self.assertEqual(page["total"], 2)
            self.assertEqual((page["items"][0]["resource_kind"], page["items"][0]["post_id"]),
                             ("moment", "post-1"))
            self.assertNotIn("_cache_relpath", page["items"][0]["media_entry"])
            next_page = self.store.media("all", limit=1, offset=1, expand_attachments=True)
            self.assertEqual(next_page["items"][0]["id"], "chat-image")
            self.assertEqual(self.store.media("all", source="moments", q="needle",
                                               expand_attachments=True)["total"], 1)
            self.assertEqual(self.store.media("all", source="chat", expand_attachments=True)["total"], 1)
            self.assertEqual(self.store.media("qq", source="moments", expand_attachments=True)["total"], 0)
            self.assertEqual(self.store.media("all", start="1970-01-02",
                                               source="moments", expand_attachments=True)["total"], 0)

    def test_wechat_stickers_are_not_counted_or_filtered_as_images(self):
        self.store.ingest("wechat", "acc1", [conv("room")], [
            msg("photo", "room", 200, "", type="image", raw_type="3"),
            msg("sticker", "room", 100, "[表情]", type="image", raw_type="47"),
            msg("animated-sticker", "room", 90, "[表情]", type="image", raw_type="11000"),
        ])

        all_media = self.store.media("wechat", expand_attachments=True)
        images = self.store.media("wechat", type="image", expand_attachments=True)
        chat_media = self.store.media("wechat", source="chat", expand_attachments=True)
        chat_images = self.store.media("wechat", type="image", source="chat", expand_attachments=True)
        chat_stickers = self.store.media("wechat", type="sticker", source="chat", expand_attachments=True)
        self.assertEqual(all_media["total"], 3)
        self.assertEqual({item["id"] for item in images["items"]}, {"photo"})
        self.assertEqual(chat_media["total"], 3)
        self.assertEqual({item["id"] for item in chat_images["items"]}, {"photo"})
        self.assertEqual({item["id"] for item in chat_stickers["items"]}, {"sticker", "animated-sticker"})
        sticker = next(item for item in all_media["items"] if item["id"] == "sticker")
        self.assertEqual(sticker["media_entry"]["type"], "sticker")
        self.assertEqual(self.store.media("wechat")["total"], 1)
        stats = self.store.stats("wechat")
        self.assertEqual(stats["media_count"], 1)
        self.assertEqual(stats["per_type"]["sticker"], 2)
        self.assertEqual(stats["per_type"]["image"], 1)

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
            "qq", "acc1", [conv("c1", platform="qq")], [
                msg("q1", "c1", ts(2024, 3, 2, 9), "qq", platform="qq"),
                msg("q2", "c1", ts(2024, 3, 2, 10), "qq attachment", platform="qq",
                    roaming_resources=[{"type": "image"}, {"type": "file"}]),
            ]
        )

        stats = self.store.stats("all")
        self.assertEqual(stats["message_count"], 5)
        self.assertEqual(stats["conversation_count"], 2)
        self.assertEqual(stats["contact_count"], 1)
        self.assertEqual(stats["media_count"], 2)
        self.assertEqual(stats["per_platform"]["wechat"]["messages"], 3)
        self.assertEqual(stats["per_platform"]["qq"]["messages"], 2)
        self.assertEqual(stats["per_type"]["text"], 4)
        self.assertEqual(stats["per_type"]["image"], 1)

        # 2024-03-01 00:30 CST is 2024-02-29 16:30 UTC: +08:00 grouping must win.
        daily = {entry["date"]: entry["count"] for entry in stats["daily"]}
        self.assertEqual(daily["2024-03-01"], 2)
        self.assertEqual(daily["2024-02-29"], 1)
        self.assertEqual(daily["2024-03-02"], 2)

        scoped = self.store.stats("wechat")
        self.assertEqual(scoped["message_count"], 3)
        self.assertEqual(set(scoped["per_platform"].keys()), {"wechat"})

    def test_source_counts_are_live_and_skip_report_aggregates(self):
        self.assertEqual(self.store.source_counts(), {})
        self.store.ingest("wechat", "acc1", [conv("c1")],
                          (msg(f"m{i}", "c1", i, "text") for i in range(3)))
        self.store.ingest("qq", "acc1", [conv("c1", platform="qq")], [])
        self.assertEqual(self.store.source_counts(), {
            "wechat": {"messages": 3, "conversations": 1},
            "qq": {"messages": 0, "conversations": 1},
        })
        with self.store._lock:
            conn = self.store._require_connection()
            conn.execute("DELETE FROM messages WHERE id='m0'")
            conn.execute("DELETE FROM conversations WHERE platform='qq'")
        statements = []
        conn.set_trace_callback(statements.append)
        try:
            self.assertEqual(self.store.source_counts(), {
                "wechat": {"messages": 2, "conversations": 1},
            })
        finally:
            conn.set_trace_callback(None)
        self.assertEqual(len(statements), 1)
        self.assertNotIn("date(", statements[0].lower())
        self.assertNotIn("contacts", statements[0].lower())

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

    def test_message_context_tied_timestamps_zero_bounds_and_scope_isolation(self):
        ordered = ["first", "tie-a", "tie-b", "tie-c", "last"]
        for platform, account, room in (
            ("qq", "acc1", "room"), ("qq", "acc2", "room"),
            ("wechat", "acc1", "room"), ("qq", "acc1", "other"),
        ):
            self.store.ingest(platform, account, [conv(room, platform=platform, account=account)], [
                msg(mid if room == "room" else f"other-{mid}", room, timestamp,
                    platform=platform, account=account)
                for mid, timestamp in zip(ordered, [10, 20, 20, 20, 30])
            ])
        for target_index, target in enumerate(ordered):
            for before, after in ((0, 0), (1, 2), (5, 5)):
                with self.subTest(target=target, before=before, after=after):
                    result = self.store.message_context(
                        "qq", "acc1", target, before, after, conversation_id="room")
                    lower = max(0, target_index - before)
                    upper = min(len(ordered), target_index + after + 1)
                    self.assertEqual([item["id"] for item in result["items"]], ordered[lower:upper])
                    self.assertTrue(all(item["platform"] == "qq" and item["account_id"] == "acc1"
                                        and item["conversation_id"] == "room" for item in result["items"]))
                    self.assertEqual(result["total"], len(ordered))
                    self.assertEqual(result["target_index"], target_index - lower)
                    self.assertEqual(result["has_more_before"], lower > 0)
                    self.assertEqual(result["has_more_after"], upper < len(ordered))
        self.assertIsNone(self.store.message_context("qq", "acc1", "tie-b", conversation_id="other"))

    def test_incoming_message_events_are_ordered_and_body_free(self):
        self.store.ingest('wechat', 'acc1', [conv('events', title='Event room')], [
            msg('event-old', 'events', 10, 'old private body'),
            msg('event-self', 'events', 11, 'outgoing private body', is_self=True),
        ])
        cursor = self.store.message_rowid()
        self.store.ingest('qq', 'acc2', [conv('events', title='QQ room', platform='qq')], [
            msg('event-new', 'events', 12, 'new private body', platform='qq', account='acc2'),
        ])
        rows = self.store.incoming_message_events(cursor)
        self.assertEqual(len(rows), 1)
        self.assertGreater(rows[0]['event_id'], cursor)
        self.assertEqual(rows[0]['conversation_title'], 'QQ room')
        self.assertNotIn('text', rows[0])
        self.assertNotIn('preview', rows[0])
        self.assertEqual(self.store.incoming_message_events(self.store.message_rowid()), [])

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
        self.assertEqual(self.store._conn.execute("PRAGMA user_version").fetchone()[0], 9)

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

    def test_contact_export_preserves_provenance_and_omits_ephemeral_avatar_urls(self):
        contacts = [{
            "id": "contact-1", "platform": "qq", "account_id": "account-1",
            "name": "=formula", "kind": "group", "identity_id": "shared-1",
            "extra": {
                "member_count": 8, "nickname": "群友", "uin": "10001",
                "raw": {"avatarUrl": "https://qlogo.invalid/a?token=temporary-secret",
                        "access_token": "credential-secret", "nested": {"url": "file:///private"}},
                "official_account_kind": "subscription", "raw_biz_type": 0,
                "unknown_private_field": "must-not-export",
                "alias": "https://example.invalid/?access_token=embedded-secret",
            },
            "avatar_url": "/api/avatar?token=temporary-secret",
        }, {"id": "invalid-kind", "platform": "wechat", "account_id": "account-1",
            "name": "Invalid", "kind": "official",
            "extra": {"official_account_kind": 0}}]
        payload, mime, filename = render_contact_export(contacts, "json")
        self.assertEqual((mime, filename), ("application/json; charset=utf-8", "contacts.json"))
        record = json.loads(payload)[0]
        self.assertEqual((record["platform"], record["account_id"], record["identity_id"]),
                         ("qq", "account-1", "shared-1"))
        self.assertEqual(record["extra"], {"member_count": 8, "nickname": "群友", "uin": "10001",
                                           "official_account_kind": "subscription"})
        self.assertNotIn("raw_biz_type", payload.decode())
        self.assertEqual(json.loads(payload)[1]["extra"], {})
        self.assertNotIn("avatar_url", record)
        self.assertNotIn(b"temporary-secret", payload)
        self.assertNotIn(b"credential-secret", payload)
        self.assertNotIn(b"must-not-export", payload)
        self.assertNotIn(b"embedded-secret", payload)

        csv_payload, csv_mime, csv_filename = render_contact_export(contacts, "csv")
        self.assertTrue(csv_mime.startswith("text/csv"))
        self.assertEqual(csv_filename, "contacts.csv")
        rows = list(csv.reader(io.StringIO(csv_payload.decode("utf-8-sig"))))
        self.assertEqual(rows[1][3], "'=formula")
        self.assertEqual(rows[1][5], "shared-1")
        self.assertNotIn("avatarUrl", csv_payload.decode("utf-8-sig"))

    def test_contact_export_rejects_unknown_format(self):
        with self.assertRaises(ValueError):
            render_contact_export([], "xlsx")

    def test_group_member_export_keeps_scope_and_omits_ephemeral_fields(self):
        members = [{"id": "wx-1", "name": "=formula", "nickname": "昵称", "role": "owner",
                    "avatar_url": "/api/avatar?token=temporary-secret", "extra": {"private": True}}]
        conversation = {"platform": "wechat", "account_id": "local-account", "id": "room-1",
                        "title": "本地群聊"}
        payload, mime, filename = render_group_member_export(members, conversation, "json")
        record = json.loads(payload)[0]
        self.assertEqual((mime, filename), ("application/json; charset=utf-8", "group-members.json"))
        self.assertEqual((record["platform"], record["account_id"], record["conversation_id"]),
                         ("wechat", "local-account", "room-1"))
        self.assertTrue(record["is_owner"])
        self.assertNotIn("avatar_url", record)
        self.assertNotIn("extra", record)
        self.assertNotIn(b"temporary-secret", payload)

        csv_payload, _, _ = render_group_member_export(members, conversation, "csv")
        rows = list(csv.reader(io.StringIO(csv_payload.decode("utf-8-sig"))))
        self.assertEqual(rows[1][4], "wx-1")
        self.assertEqual(rows[1][5], "'=formula")
        self.assertEqual(rows[1][6], "昵称")

    def test_json_preserves_unicode_and_provenance(self):
        payload, mime, filename = render_export(self.messages, "json")
        self.assertEqual(mime, "application/json")
        self.assertEqual(filename, "export.json")
        data = json.loads(payload.decode("utf-8"))
        self.assertEqual(data[0]["text"], "你好，世界")
        self.assertEqual(data[1]["platform"], "qq")
        self.assertEqual(data[0]["timestamp_text"], "2024-03-01 09:05:00")

    def test_human_readable_exports_preserve_exact_message_scope(self):
        messages = [dict(self.messages[0], conversation_id="room-42", id="message-99")]
        csv_payload, _, _ = render_export(messages, "csv")
        csv_rows = list(csv.reader(io.StringIO(csv_payload.decode("utf-8"))))
        columns = {name: index for index, name in enumerate(csv_rows[0])}
        self.assertEqual(csv_rows[1][columns["会话ID"]], "room-42")
        self.assertEqual(csv_rows[1][columns["消息ID"]], "message-99")

        html_payload, _, _ = render_export(messages, "html")
        self.assertIn("<td>room-42</td><td>message-99</td>", html_payload.decode("utf-8"))
        for format in ("md", "txt"):
            with self.subTest(format=format):
                payload, _, _ = render_export(messages, format)
                self.assertIn("会话ID: room-42", payload.decode("utf-8"))
                self.assertIn("消息ID: message-99", payload.decode("utf-8"))

        xlsx_payload, _, _ = render_export(messages, "xlsx")
        with zipfile.ZipFile(io.BytesIO(xlsx_payload)) as workbook:
            sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        values = [node.text for node in sheet.findall(".//x:t", namespace)]
        self.assertIn("会话ID", values)
        self.assertIn("消息ID", values)
        self.assertIn("room-42", values)
        self.assertIn("message-99", values)

    def test_markdown_and_text_exports_preserve_account_scope(self):
        messages = [
            dict(self.messages[0], account_id="account-a", conversation_id="same-room", id="same-message"),
            dict(self.messages[0], account_id="account-b", conversation_id="same-room", id="same-message"),
        ]
        for format in ("md", "txt"):
            with self.subTest(format=format):
                payload, _, _ = render_export(messages, format)
                text = payload.decode("utf-8")
                self.assertIn("账号: account-a", text)
                self.assertIn("账号: account-b", text)

    def test_csv_protects_formula_injection(self):
        payload, mime, filename = render_export(self.messages, "csv")
        self.assertTrue(mime.startswith("text/csv"))
        text = payload.decode("utf-8")
        self.assertNotIn(",=cmd", text)
        self.assertIn("'=cmd|' /C calc'!A0", text)
        self.assertIn("你好，世界", text)

    def test_csv_protects_plus_and_at(self):
        rows = [dict(self.messages[0], text="+1"), dict(self.messages[0], id="x", text="@x"),
                dict(self.messages[0], id="y", text="  =1+1"),
                dict(self.messages[0], id="z", text="\t-1+1")]
        payload, _, _ = render_export(rows, "csv")
        text = payload.decode("utf-8")
        self.assertIn("'+1", text)
        self.assertIn("'@x", text)
        self.assertIn("'  =1+1", text)
        self.assertIn("'\t-1+1", text)

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

    def test_single_pass_exports_keep_existing_document_bytes(self):
        for format in ("json", "csv", "md", "txt", "html", "sql"):
            with self.subTest(format=format):
                expected = render_export(self.messages, format, title="__MESSAGE_ROWS__")[0]
                actual = render_export((dict(row) for row in self.messages), format,
                                       title="__MESSAGE_ROWS__")[0]
                self.assertEqual(actual, expected)
        # Preserve the prior indented JSON wire representation, not just its values.
        payload = render_export(iter(self.messages), "json")[0]
        expected = json.loads(payload)
        self.assertEqual(payload,
                         json.dumps(expected, ensure_ascii=False, indent=2).encode("utf-8"))
        self.assertEqual(render_export(iter(()), "json")[0], b"[]")

    def test_xlsx_accepts_one_shot_rows_and_preserves_count(self):
        payload = render_export((dict(row) for row in self.messages), "xlsx")[0]
        with zipfile.ZipFile(io.BytesIO(payload)) as workbook:
            sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        rows = sheet.findall(".//x:row", namespace)
        self.assertEqual(len(rows), len(self.messages) + 4)
        self.assertEqual(rows[2].findall(".//x:t", namespace)[1].text, str(len(self.messages)))

    def test_xlsx_is_valid_workbook_and_uses_literal_cells(self):
        payload, mime, filename = render_export(self.messages, "excel")
        self.assertEqual(filename, "export.xlsx")
        self.assertIn("spreadsheetml.sheet", mime)
        with zipfile.ZipFile(io.BytesIO(payload)) as workbook:
            self.assertIn("xl/workbook.xml", workbook.namelist())
            sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        values = [node.text for node in sheet.findall(".//x:t", namespace)]
        self.assertIn("平台", values)
        self.assertIn("=cmd|' /C calc'!A0", values)
        self.assertEqual(sheet.findall(".//x:f", namespace), [])
        self.assertEqual(self.messages[1]["sender_name"], "=cmd|' /C calc'!A0")

    def test_sql_export_escapes_values_and_executes_without_mutating_messages(self):
        messages = [dict(self.messages[0], text="O'Reilly\x00 says hi", timestamp="bad")]
        payload, mime, filename = render_export(messages, "sql")
        script = payload.decode("utf-8")
        self.assertEqual(filename, "export.sql")
        self.assertTrue(mime.startswith("application/sql"))
        self.assertIn("O''Reilly says hi", script)
        self.assertIn("BEGIN;", script)
        self.assertTrue(script.rstrip().endswith("COMMIT;"))
        database = sqlite3.connect(":memory:")
        database.executescript(script)
        row = database.execute("SELECT message_id, text, timestamp FROM local_message_hub_messages").fetchone()
        self.assertEqual(row, (str(messages[0]["id"]), "O'Reilly says hi", None))
        self.assertEqual(messages[0]["text"], "O'Reilly\x00 says hi")

    def test_chatlab_json_and_jsonl_preserve_platform_and_account_provenance(self):
        payload, _, filename = render_export(self.messages, "chatlab")
        self.assertEqual(filename, "export.chatlab.json")
        document = json.loads(payload.decode("utf-8"))
        self.assertEqual(document["chatlab"]["generator"], "Local Message Hub")
        self.assertEqual(document["meta"]["sourcePlatforms"], ["qq", "wechat"])
        self.assertEqual(document["messages"][0]["type"], 0)
        self.assertEqual(document["messages"][0]["platform"], "wechat")
        self.assertEqual(document["messages"][1]["mediaUrl"], "http://example/1.png")
        lines, mime, _ = render_export(self.messages, "chatlab-jsonl")
        self.assertTrue(mime.startswith("application/x-ndjson"))
        records = [json.loads(line) for line in lines.decode("utf-8").splitlines()]
        self.assertEqual(records[0]["_type"], "header")
        self.assertEqual([record["_type"] for record in records[1:]], ["member", "member", "message", "message"])

    def test_exports_preserve_all_attachment_metadata_without_cache_paths_or_urls(self):
        resources = [
            {"fileName": "photo-1.png", "kind": "image", "fileSize": 123,
             "md5HexStr": "abc123", "path": "C:/private/photo.png",
             "url": "/local-media/secret-token"},
            {"n": "report.pdf", "type": "file", "size": 456,
             "local_path": "C:/private/report.pdf", "media_url": "https://example.invalid/?token=secret"},
        ]
        qq = dict(self.messages[0], platform="qq", account_id="qq-account",
                  extra={"roaming_resources": resources})
        wecom = dict(self.messages[0], platform="wecom", account_id="wecom-account",
                     extra={"attachments": [{"name": "voice.amr", "type": "voice",
                                              "duration": 8, "available": True}]})
        payload = render_export([qq, wecom], "json")[0]
        exported = json.loads(payload)
        self.assertEqual(exported[0]["resources"], [
            {"name": "photo-1.png", "type": "image", "size": 123, "md5": "abc123"},
            {"name": "report.pdf", "type": "file", "size": 456},
        ])
        self.assertEqual(exported[1]["resources"], [
            {"name": "voice.amr", "type": "voice", "duration": 8, "available": True},
        ])

        csv_payload = render_export([qq], "csv")[0].decode("utf-8")
        self.assertIn("photo-1.png", csv_payload)
        self.assertIn("report.pdf", csv_payload)
        html_payload = render_export([qq], "html")[0].decode("utf-8")
        self.assertIn("photo-1.png", html_payload)
        xlsx_payload = render_export([qq], "xlsx")[0]
        with zipfile.ZipFile(io.BytesIO(xlsx_payload)) as workbook:
            self.assertIn("photo-1.png", workbook.read("xl/worksheets/sheet1.xml").decode("utf-8"))

        sql = render_export([qq], "sql")[0].decode("utf-8")
        database = sqlite3.connect(":memory:")
        database.execute("""CREATE TABLE local_message_hub_messages (
            platform TEXT NOT NULL, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
            conversation_title TEXT, message_id TEXT, timestamp BIGINT, sender_id TEXT,
            sender_name TEXT, type TEXT, text TEXT, is_self BOOLEAN, media_url TEXT,
            file_name TEXT, raw_type TEXT)""")
        database.executescript(sql)
        sql_resources = [json.loads(row[0]) for row in database.execute(
            "SELECT resource_json FROM local_message_hub_resources ORDER BY resource_index")]
        self.assertEqual(sql_resources, exported[0]["resources"])

        chatlab = json.loads(render_export([qq], "chatlab")[0])
        self.assertEqual(chatlab["messages"][0]["resources"], exported[0]["resources"])
        for format in ("md", "txt"):
            with self.subTest(format=format):
                text = render_export([qq], format)[0].decode("utf-8")
                self.assertIn("photo-1.png", text)
                self.assertIn("report.pdf", text)
        for secret in ("C:/private", "secret-token", "token=secret"):
            self.assertNotIn(secret, payload.decode("utf-8"))
        jsonl_records = [json.loads(line) for line in render_export([qq], "chatlab-jsonl")[0].decode().splitlines()]
        self.assertEqual(jsonl_records[-1]["resources"], exported[0]["resources"])

    def test_json_export_sanitizes_top_level_attachment_aliases(self):
        message = dict(self.messages[0], attachments=[{
            "name": "safe.png", "type": "image", "path": "C:/private/safe.png",
            "url": "https://example.invalid/?token=secret",
        }], resources=[{
            "local_path": "C:/private/secret.bin", "media_url": "/local-media/opaque-token",
        }])

        exported = json.loads(render_export([message], "json")[0])[0]

        self.assertEqual(exported["resources"], [{"name": "safe.png", "type": "image"}])
        self.assertNotIn("attachments", exported)
        text = json.dumps(exported)
        for forbidden in ("C:/private", "example.invalid", "secret", "opaque-token"):
            self.assertNotIn(forbidden, text)

    def test_exports_merge_distinct_attachment_collections_and_dedupe_aliases(self):
        repeated = {"name": "same.png", "type": "image", "path": "C:/private/same.png"}
        message = dict(self.messages[0], attachments=[repeated], resources=[{
            "name": "top-level.pdf", "type": "file", "url": "https://example.invalid/token=x",
        }], extra={
            "attachments": [dict(repeated)],
            "roaming_resources": [{"fileName": "qq.bin", "kind": "file", "fileSize": 9}],
            "media": [],
            "resources": [{"name": "voice.amr", "type": "voice", "duration": 3}],
        })

        expected = [
            {"name": "same.png", "type": "image"},
            {"name": "qq.bin", "type": "file", "size": 9},
            {"name": "voice.amr", "type": "voice", "duration": 3},
            {"name": "top-level.pdf", "type": "file"},
        ]
        rendered = json.loads(render_export([message], "json")[0])[0]
        self.assertEqual(rendered["resources"], expected)
        self.assertEqual(json.loads(render_export([message], "chatlab")[0])["messages"][0]["resources"], expected)
        jsonl = [json.loads(line) for line in render_export([message], "chatlab-jsonl")[0].decode().splitlines()]
        self.assertEqual(jsonl[-1]["resources"], expected)
        self.assertIn("top-level.pdf", render_export([message], "csv")[0].decode("utf-8"))
        serialized = json.dumps(rendered)
        for forbidden in ("C:/private", "example.invalid", "token=x"):
            self.assertNotIn(forbidden, serialized)

    def test_exports_preserve_repeated_identical_attachments_in_one_collection(self):
        duplicate = {"name": "same.png", "type": "image", "size": 12}
        message = dict(self.messages[0], id="duplicate-attachments", attachments=[
            dict(duplicate), dict(duplicate),
        ])

        exported = json.loads(render_export([message], "json")[0])[0]
        self.assertEqual(exported["resources"], [duplicate, duplicate])

        chatlab = json.loads(render_export([message], "chatlab")[0])
        self.assertEqual(chatlab["messages"][0]["resources"], [duplicate, duplicate])

        sql = render_export([message], "sql")[0].decode("utf-8")
        database = sqlite3.connect(":memory:")
        database.executescript(sql)
        self.assertEqual(database.execute(
            "SELECT COUNT(*) FROM local_message_hub_resources"
        ).fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
