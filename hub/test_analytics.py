"""Synthetic tests for read-only private/group analytics and reports."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

try:
    from .analytics import AnalyticsService
    from .store import ChatStore
except ImportError:
    from analytics import AnalyticsService
    from store import ChatStore

CST = timezone(timedelta(hours=8))


def ts(year, month, day, hour=0, minute=0):
    return int(datetime(year, month, day, hour, minute, tzinfo=CST).timestamp())


class AnalyticsTestCase(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        self.store = ChatStore(self.path)
        self.analytics = AnalyticsService(self.store)
        self.store.ingest("wechat", "acct", [
            {"id": "dm", "title": "Alice", "kind": "direct"},
            {"id": "group", "title": "Team", "kind": "group"},
        ], [
            self.message("d1", "dm", ts(2024, 1, 1, 9), "hi", "self", "Me", True),
            self.message("d2", "dm", ts(2024, 1, 1, 10), "hello", "alice", "Alice"),
            self.message("d3", "dm", ts(2024, 12, 31, 23, 30), "picture", "alice", "Alice", False, "image"),
            self.message("d4", "dm", ts(2025, 1, 1, 0), "new year", "self", "Me", True),
            self.message("g1", "group", ts(2024, 6, 2, 12), "one", "bob", "Bob"),
            self.message("g2", "group", ts(2024, 6, 2, 13), "two", "bob", "Bob"),
            self.message("g3", "group", ts(2024, 6, 3, 8), "three", "self", "Me", True),
        ], [
            {"id": "bob", "name": "Robert", "kind": "member"},
        ])

    def tearDown(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except FileNotFoundError:
                pass

    @staticmethod
    def message(mid, cid, timestamp, text, sender, sender_name, is_self=False, kind="text"):
        return {
            "id": mid, "conversation_id": cid, "timestamp": timestamp, "text": text,
            "type": kind, "sender_id": sender, "sender_name": sender_name, "is_self": is_self,
        }

    def test_private_profile_has_message_counts_and_media(self):
        self.store.ingest("wechat", "acct", [], [
            self.message("orphan", "missing-room", ts(2024, 1, 2), "unknown scope", "peer", "Peer"),
        ])
        profiles = self.analytics.private_profiles("wechat", "acct")
        self.assertEqual(len(profiles), 1)
        row = profiles[0].to_dict()
        self.assertEqual(row["conversation_id"], "dm")
        self.assertEqual((row["message_count"], row["sent_count"], row["received_count"]), (4, 2, 2))
        self.assertEqual(row["sent_ratio"], 0.5)
        self.assertEqual(row["media_count"], 1)
        self.assertEqual(row["first_timestamp"], ts(2024, 1, 1, 9))

    def test_self_sent_daily_distribution_uses_utc8_range_and_bounds_recent_days(self):
        distribution = self.analytics.self_sent_daily_distribution("wechat", "acct")
        self.assertEqual(distribution["total_messages"], 2)
        self.assertEqual(distribution["first_day"], "2024-01-01")
        self.assertEqual(distribution["last_day"], "2025-01-01")
        self.assertAlmostEqual(distribution["baseline"], 2 / 367)
        self.assertEqual(len(distribution["items"]), 37)
        self.assertEqual(distribution["items"][0], {"date": "2024-11-26", "count": 0})
        self.assertEqual(distribution["items"][-1], {"date": "2025-01-01", "count": 1})
        self.assertTrue(all("text" not in item for item in distribution["items"]))

    def test_self_sent_daily_distribution_scopes_platform_account_and_empty_period(self):
        self.store.ingest("wecom", "other", [{"id": "room", "title": "Peer", "kind": "direct"}], [
            self.message("other-self", "room", ts(2024, 1, 2), "private text", "self", "Me", True),
        ])
        self.store.ingest("wecom", "other", [], [
            self.message("orphan-self", "missing-room", ts(2024, 1, 2, 1), "unknown scope", "self", "Me", True),
        ])
        result = self.analytics.self_sent_daily_distribution("wecom", "other")
        self.assertEqual(result["items"], [{"date": "2024-01-02", "count": 1}])
        self.assertEqual(self.analytics.self_sent_daily_distribution("qq", "missing")["items"], [])

    def test_attachment_arrays_count_as_one_media_message_in_profiles_and_timeline(self):
        self.store.ingest("wecom", "fixture", [
            {"id": "wecom-dm", "title": "Work peer", "kind": "direct"},
        ], [{
            "id": "wecom-text-with-files", "conversation_id": "wecom-dm",
            "timestamp": ts(2024, 2, 1, 10), "type": "text", "text": "caption",
            "sender_id": "peer", "sender_name": "Peer",
            "attachments": [{"type": "image"}, {"type": "file"}],
        }])
        self.store.ingest("qq", "fixture", [
            {"id": "qq-peer", "title": "QQ peer", "kind": "direct"},
        ], [{
            "id": "qq-text-with-roaming", "conversation_id": "qq-peer",
            "timestamp": ts(2024, 2, 1, 11), "type": "text", "text": "caption",
            "sender_id": "peer", "sender_name": "Peer", "roaming_resources": [
                {"type": "image"}, {"type": "file"},
            ],
        }])

        wecom_profile = self.analytics.private_profiles("wecom", "fixture")[0].to_dict()
        self.assertEqual(wecom_profile["media_count"], 1)
        qq_rows = self.analytics.relationship_footprint("qq", "fixture")
        self.assertEqual(qq_rows[0]["media_count"], 1)
        self.assertEqual(self.analytics.relationship_timeline(
            "qq", "fixture", "qq-peer")[0]["media_count"], 1)
        with self.store._lock:
            self.store._conn.execute(
                "UPDATE messages SET extra='{' WHERE platform='qq' AND id='qq-text-with-roaming'")
        self.assertEqual(self.store.stats("qq")["media_count"], 0)

    def test_qq_media_alias_counts_in_media_analytics(self):
        self.store.ingest("qq", "fixture", [
            {"id": "qq-media-alias", "title": "QQ alias peer", "kind": "direct"},
        ], [{
            "id": "qq-media-alias-image", "conversation_id": "qq-media-alias",
            "timestamp": ts(2024, 2, 2, 11), "type": "text", "text": "caption",
            "sender_id": "peer", "sender_name": "Peer", "media": [
                {"name": "photo.png", "kind": "图片"},
                {"name": "voice.silk", "k": "audio"},
            ],
        }])

        self.assertEqual(self.store.stats("qq")["media_count"], 1)
        self.assertEqual(self.analytics.private_profiles("qq", "fixture")[0].media_count, 1)
        self.assertEqual(self.analytics.relationship_timeline(
            "qq", "fixture", "qq-media-alias")[0]["media_count"], 1)
        milestones = self.analytics.relationship_achievements(
            "qq", "fixture", "qq-media-alias")
        image_count = next(item["current"] for item in milestones["items"]
                           if item["id"] == "images_100")
        voice_count = next(item["current"] for item in milestones["items"]
                           if item["id"] == "voices_50")
        self.assertEqual((image_count, voice_count), (1, 1))

    def test_group_profile_ranks_members_and_uses_contact_display_name(self):
        groups = self.analytics.group_profiles("wechat", "acct")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["conversation_id"], "group")
        self.assertEqual(groups[0]["message_count"], 3)
        self.assertEqual(groups[0]["members"][0]["name"], "Robert")
        self.assertEqual(groups[0]["members"][0]["message_count"], 2)
        self.assertEqual(groups[0]["member_count"], 2)
        self.assertEqual(groups[0]["observed_member_count"], 2)
        self.assertAlmostEqual(groups[0]["sent_ratio"], 1 / 3, places=5)

    def test_group_member_aggregates_use_one_query_for_the_page(self):
        self.store.ingest("wechat", "acct", [{"id": "group-2", "title": "Another", "kind": "group"}], [
            self.message("g4", "group-2", ts(2024, 6, 4, 8), "four", "carol", "Carol"),
        ])
        statements = []
        self.store._conn.set_trace_callback(statements.append)
        try:
            groups = self.analytics.group_profiles("wechat", "acct")
        finally:
            self.store._conn.set_trace_callback(None)

        member_aggregates = [sql for sql in statements if "FROM messages m LEFT JOIN contacts c" in sql]
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(member_aggregates), 1)
        self.assertEqual(next(item for item in groups if item["conversation_id"] == "group-2")["members"][0]["name"], "Carol")

    def test_group_profile_member_limit_keeps_full_observed_count(self):
        group = self.analytics.group_profiles("wechat", "acct", member_limit=1)[0]
        self.assertEqual(len(group["members"]), 1)
        self.assertEqual(group["members"][0]["message_count"], 2)
        self.assertEqual(group["observed_member_count"], 2)
        selected = self.analytics.group_profiles(
            "wechat", "acct", member_limit=5000, conversation_id="group")
        self.assertEqual([item["conversation_id"] for item in selected], ["group"])

    def test_group_profile_zero_member_limit_keeps_observed_count(self):
        group = self.analytics.group_profiles("wechat", "acct", member_limit=0)[0]

        self.assertEqual(group["members"], [])
        self.assertEqual(group["observed_member_count"], 2)

    def test_group_profile_preserves_zero_roster_count_separately_from_observed_senders(self):
        self.store.ingest("wechat", "fixture", [{
            "id": "empty-roster-group", "title": "Empty roster", "kind": "group", "member_count": 0,
        }], [
            self.message("observed-sender", "empty-roster-group", ts(2024, 6, 5),
                         "synthetic only", "peer", "Peer"),
        ])

        group = self.analytics.group_profiles("wechat", "fixture", conversation_id="empty-roster-group")[0]

        self.assertEqual(group["member_count"], 0)
        self.assertEqual(group["observed_member_count"], 1)

    def test_annual_report_uses_inclusive_cst_calendar_year(self):
        report = self.analytics.annual_report(2024, "wechat", "acct", "dm").to_dict()
        self.assertEqual(report["total_messages"], 3)
        self.assertEqual(report["sent_messages"], 1)
        self.assertEqual(report["received_messages"], 2)
        self.assertAlmostEqual(report["sent_ratio"], 1 / 3, places=5)
        self.assertEqual(report["active_days"], 2)
        self.assertEqual(report["activity_heatmap"][1][9], 1)
        self.assertEqual(report["activity_heatmap"][1][10], 1)
        self.assertEqual(report["activity_heatmap"][2][23], 1)
        self.assertEqual(len(report["activity_heatmap"]), 7)
        self.assertTrue(all(len(day) == 24 for day in report["activity_heatmap"]))
        self.assertEqual(report["longest_streak"], {
            "days": 1, "start_date": "2024-01-01", "end_date": "2024-01-01",
        })
        self.assertEqual(report["monthly"], tuple(
            {"month": month, "count": count}
            for month, count in enumerate((2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1), start=1)
        ))
        self.assertEqual(report["busiest_hour"], {"hour": 9, "count": 1})
        self.assertEqual(report["top_contacts"], ({"platform": "wechat", "name": "Alice", "count": 2},))
        self.assertNotIn("account_id", report["top_contacts"][0])
        self.assertNotIn("sender_id", report["top_contacts"][0])
        self.assertNotIn("text", report["top_contacts"][0])
        self.assertNotIn({"month": 1, "count": 1}, report["monthly"])

    def test_annual_report_all_time_has_no_year_and_includes_multiple_years(self):
        report = self.analytics.annual_report(None, "wechat", "acct", "dm").to_dict()
        self.assertIsNone(report["year"])
        self.assertEqual(report["total_messages"], 4)
        self.assertEqual(report["monthly"], tuple(
            {"month": month, "count": count}
            for month, count in enumerate((3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1), start=1)
        ))
        self.assertEqual(report["longest_streak"], {
            "days": 2, "start_date": "2024-12-31", "end_date": "2025-01-01",
        })

    def test_annual_report_activity_heatmap_and_streak_follow_scope_and_timezone(self):
        self.store.ingest("qq", "fixture", [{"id": "qq-room", "title": "QQ peer", "kind": "direct"}], [
            self.message("q1", "qq-room", ts(2024, 2, 1, 0), "one", "peer", "Peer"),
            self.message("q2", "qq-room", ts(2024, 2, 2, 23), "two", "peer", "Peer"),
            self.message("q3", "qq-room", ts(2024, 2, 4, 12), "three", "peer", "Peer"),
        ])
        report = self.analytics.annual_report(2024, "qq", "fixture", "qq-room").to_dict()
        self.assertEqual(report["activity_heatmap"][4][0], 1)
        self.assertEqual(report["activity_heatmap"][5][23], 1)
        self.assertEqual(report["activity_heatmap"][0][12], 1)
        self.assertEqual(report["longest_streak"], {
            "days": 2, "start_date": "2024-02-01", "end_date": "2024-02-02",
        })

    def test_annual_top_contacts_are_scoped_by_year_and_platform_and_bounded(self):
        self.store.ingest("qq", "other-account", [{"id": "qq-room", "title": "QQ peer", "kind": "direct"}], [
            self.message("qq-inbound", "qq-room", ts(2024, 5, 1), "private body", "peer", "Peer"),
        ])
        report = self.analytics.annual_report(2024, "all").to_dict()
        self.assertEqual(report["longest_streak"], {
            "days": 2, "start_date": "2024-06-02", "end_date": "2024-06-03",
        })
        self.assertEqual(report["top_contacts"], (
            {"platform": "wechat", "name": "Alice", "count": 2},
            {"platform": "wechat", "name": "Robert", "count": 2},
            {"platform": "qq", "name": "Peer", "count": 1},
        ))
        self.assertTrue(all("account_id" not in item and "sender_id" not in item for item in report["top_contacts"]))

    def test_annual_contact_fallback_does_not_expose_sender_id(self):
        self.store.ingest("qq", "fixture", [{"id": "unknown-room", "title": "Room", "kind": "direct"}], [{
            "id": "unknown-contact-message", "conversation_id": "unknown-room",
            "timestamp": ts(2024, 1, 1), "type": "text", "sender_id": "private-contact-id",
        }])
        report = self.analytics.annual_report(2024, "qq", "fixture").to_dict()
        self.assertEqual(report["top_contacts"], ({"platform": "qq", "name": "未知联系人", "count": 1},))
        self.assertNotIn("private-contact-id", repr(report["top_contacts"]))

    def test_pair_report_selects_counterpart_and_can_filter_year(self):
        report = self.analytics.pair_report("wechat", "acct", "dm", year=2024).to_dict()
        self.assertEqual(report["counterpart_id"], "alice")
        self.assertEqual(report["counterpart_name"], "Alice")
        self.assertEqual(report["counterpart_messages"], 2)
        self.assertEqual(report["total_messages"], 3)
        self.assertAlmostEqual(report["sent_ratio"], 1 / 3, places=5)
        all_time = self.analytics.pair_report("wechat", "acct", "dm").to_dict()
        self.assertIsNone(all_time["year"])
        self.assertEqual(all_time["total_messages"], 4)

    def test_pair_report_measures_responses_to_selected_counterpart_within_session(self):
        self.store.ingest("wechat", "acct", [{"id": "reply-room", "title": "Reply peer", "kind": "private"}], [
            self.message("r1", "reply-room", ts(2024, 3, 1, 8), "private", "peer-a", "Peer A"),
            self.message("r2", "reply-room", ts(2024, 3, 1, 8, 10), "private", "self", "Me", True),
            self.message("r3", "reply-room", ts(2024, 3, 1, 8, 20), "private", "peer-a", "Peer A"),
            self.message("r4", "reply-room", ts(2024, 3, 1, 8, 40), "private", "self", "Me", True),
            self.message("r5", "reply-room", ts(2024, 3, 1, 9), "private", "peer-a", "Peer A"),
            self.message("r6", "reply-room", ts(2024, 3, 1, 10, 0) + 1, "private", "self", "Me", True),
            self.message("r7", "reply-room", ts(2024, 3, 1, 11), "private", "peer-b", "Peer B"),
            self.message("r8", "reply-room", ts(2024, 3, 1, 11, 5), "private", "self", "Me", True),
        ])

        report = self.analytics.pair_report("wechat", "acct", "reply-room", year=2024,
                                            counterpart_id="peer-a").to_dict()

        self.assertEqual(report["response_count"], 2)
        self.assertEqual(report["response_avg_seconds"], 900)
        self.assertEqual(report["response_fastest_seconds"], 600)

    def test_pair_report_counts_same_second_reply_as_zero_second_response(self):
        same_second = ts(2024, 5, 1, 12, 30)
        self.store.ingest("wechat", "acct", [{
            "id": "same-second-room", "title": "Fast reply", "kind": "private",
        }], [
            self.message("same-second-peer", "same-second-room", same_second,
                         "peer message", "alice", "Alice"),
            self.message("same-second-self", "same-second-room", same_second,
                         "self reply", "self", "Me", True),
        ])

        report = self.analytics.pair_report("wechat", "acct", "same-second-room",
                                            year=2024, counterpart_id="alice")

        self.assertEqual(report.response_count, 1)
        self.assertEqual(report.response_avg_seconds, 0)
        self.assertEqual(report.response_fastest_seconds, 0)

    def test_pair_report_year_range_uses_inclusive_utc8_calendar_boundary(self):
        report_2024 = self.analytics.pair_report("wechat", "acct", "dm", year=2024)
        report_2025 = self.analytics.pair_report("wechat", "acct", "dm", year=2025)
        cross_year_reply = self.analytics.pair_report(
            "wechat", "acct", "dm", year=2025, counterpart_id="alice")

        self.assertEqual(report_2024.total_messages, 3)
        self.assertEqual(report_2024.last_timestamp, ts(2024, 12, 31, 23, 30))
        self.assertEqual(report_2025.total_messages, 1)
        self.assertEqual(report_2025.first_timestamp, ts(2025, 1, 1, 0, 0))
        self.assertEqual(cross_year_reply.response_count, 0)

    def test_pair_report_rejects_group_conversations(self):
        with self.assertRaisesRegex(ValueError, "only for private conversations"):
            self.analytics.pair_report("wechat", "acct", "group")

    def test_relationship_footprint_is_content_free_and_uses_utc8_day_boundary(self):
        self.store.ingest("qq", "fixture", [{"id": "peer", "title": "Synthetic peer", "kind": "direct"}], [
            self.message("before-midnight-utc", "peer", ts(2024, 1, 1, 23, 59),
                         "must not appear", "self", "Me", True),
            self.message("midnight-cst", "peer", ts(2024, 1, 2, 0, 0),
                         "also private", "peer", "Peer", False, "image"),
        ])
        rows = self.analytics.relationship_footprint("qq", "fixture", "2024-01-02", "2024-01-02")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["conversation_id"], "peer")
        self.assertEqual((rows[0]["message_count"], rows[0]["sent_count"], rows[0]["received_count"]), (1, 0, 1))
        self.assertNotIn("text", rows[0])
        timeline = self.analytics.relationship_timeline("qq", "fixture", "peer", "2024-01-01", "2024-01-02")
        self.assertEqual(timeline, [
            {"date": "2024-01-02", "message_count": 1, "sent_count": 0, "received_count": 1, "media_count": 1},
            {"date": "2024-01-01", "message_count": 1, "sent_count": 1, "received_count": 0, "media_count": 0},
        ])

    def test_relationship_achievements_are_objective_and_group_scoped(self):
        self.store.ingest("wechat", "acct", [
            {"id": "achievement-room", "title": "Synthetic person", "kind": "direct"},
            {"id": "achievement-group", "title": "Synthetic group", "kind": "group"},
        ], [
            self.message("a1", "achievement-room", ts(2023, 12, 31, 23, 30), "private one", "self", "Me", True),
            self.message("a2", "achievement-room", ts(2024, 1, 1, 1), "private two", "peer", "Peer", False, "image"),
            self.message("a3", "achievement-room", ts(2024, 1, 2, 1), "private three", "peer", "Peer", False, "voice"),
            self.message("a4", "achievement-room", ts(2024, 1, 3, 1), "private four", "self", "Me", True),
            self.message("g1", "achievement-group", ts(2024, 1, 1), "group text", "peer", "Peer"),
        ])
        report = self.analytics.relationship_achievements("wechat", "acct", "achievement-room")
        items = {item["id"]: item for item in report["items"]}
        self.assertTrue(report["available"])
        self.assertTrue(items["first_message"]["unlocked"])
        self.assertEqual(items["messages_100"]["current"], 4)
        self.assertEqual(items["active_days_7"]["current"], 4)
        self.assertEqual(items["streak_3"]["current"], 4)
        self.assertEqual(items["images_100"]["current"], 1)
        self.assertTrue(items["active_years_2"]["unlocked"])
        self.assertTrue(items["span_365"]["unlocked"] is False)
        self.assertNotIn("text", report)
        group = self.analytics.relationship_achievements("wechat", "acct", "achievement-group")
        self.assertFalse(group["available"])
        self.assertEqual(group["items"], [])

    def test_relationship_achievements_fail_closed_for_unknown_conversation_kind(self):
        self.store.ingest("wechat", "acct", [
            {"id": "unknown-kind", "title": "Unclassified", "kind": "unknown"},
        ], [self.message("unknown-kind-message", "unknown-kind", ts(2024, 1, 1), "x", "peer", "Peer")])

        report = self.analytics.relationship_achievements("wechat", "acct", "unknown-kind")

        self.assertEqual(report, {
            "available": False, "reason": "关系成就仅适用于私聊会话", "items": [],
        })

    def test_wechat_call_milestone_uses_only_verified_local_type_code(self):
        messages = [
            {**self.message(f"call-{index}", "dm", ts(2024, 2, index + 1),
                            "synthetic call", "peer", "Peer", kind="system"),
             "raw_type": "50"}
            for index in range(5)
        ]
        messages.append({**self.message("not-call", "dm", ts(2024, 2, 8),
                                        "system text", "peer", "Peer", kind="system"),
                         "raw_type": "10000"})
        self.store.ingest("wechat", "acct", [], messages)

        report = self.analytics.relationship_achievements("wechat", "acct", "dm")
        calls = next(item for item in report["items"] if item["id"] == "calls_5")

        self.assertEqual(calls["current"], 5)
        self.assertTrue(calls["unlocked"])
        self.assertNotIn("synthetic call", str(report))

    def test_relationship_media_milestones_include_platform_attachments_once_per_message(self):
        self.store.ingest("wecom", "fixture", [
            {"id": "attachment-room", "title": "Synthetic work peer", "kind": "direct"},
        ], [{
            "id": "attachment-message", "conversation_id": "attachment-room",
            "timestamp": ts(2024, 2, 1), "type": "text", "text": "synthetic",
            "attachments": [
                {"type": "image"}, {"type": "picture"}, {"type": "audio"},
                {"type": "videos"}, {"type": "files"},
            ],
        }])

        report = self.analytics.relationship_achievements(
            "wecom", "fixture", "attachment-room")
        items = {item["id"]: item for item in report["items"]}
        self.assertEqual(items["images_100"]["current"], 1)
        self.assertEqual(items["voices_50"]["current"], 1)
        self.assertEqual(items["videos_20"]["current"], 1)
        self.assertEqual(items["files_10"]["current"], 1)
        self.assertNotIn("text", report)

        self.store.ingest("qq", "fixture", [
            {"id": "roaming-room", "title": "Synthetic QQ peer", "kind": "direct"},
        ], [{
            "id": "roaming-message", "conversation_id": "roaming-room",
            "timestamp": ts(2024, 2, 2), "type": "text", "text": "synthetic",
            "roaming_resources": [
                {"type": "image"}, {"type": "photo"}, {"type": "audio"},
                {"type": "video"}, {"type": "file"},
            ],
        }])
        qq_report = self.analytics.relationship_achievements(
            "qq", "fixture", "roaming-room")
        qq_items = {item["id"]: item for item in qq_report["items"]}
        self.assertEqual(qq_items["images_100"]["current"], 1)
        self.assertEqual(qq_items["voices_50"]["current"], 1)
        self.assertEqual(qq_items["videos_20"]["current"], 1)
        self.assertEqual(qq_items["files_10"]["current"], 1)

    def test_relationship_media_milestones_include_localized_wecom_attachment_types(self):
        self.store.ingest("wecom", "fixture", [
            {"id": "localized-room", "title": "Synthetic work peer", "kind": "direct"},
        ], [{
            "id": "localized-message", "conversation_id": "localized-room",
            "timestamp": ts(2024, 2, 3), "type": "text", "text": "synthetic",
            "attachments": [
                {"type": "图片"}, {"type": "语音"},
                {"type": "视频"}, {"type": "文件"},
            ],
        }])

        report = self.analytics.relationship_achievements(
            "wecom", "fixture", "localized-room")
        items = {item["id"]: item for item in report["items"]}
        self.assertEqual(items["images_100"]["current"], 1)
        self.assertEqual(items["voices_50"]["current"], 1)
        self.assertEqual(items["videos_20"]["current"], 1)
        self.assertEqual(items["files_10"]["current"], 1)

    def test_empty_and_invalid_report_inputs_are_explicit(self):
        empty = self.analytics.annual_report(2024, "wechat", "acct", "missing")
        self.assertEqual(empty.total_messages, 0)
        with self.assertRaises(ValueError):
            self.analytics.annual_report(1969)
        with self.assertRaises(ValueError):
            self.analytics.private_profiles(start="2025-01-02", end="2025-01-01")
        with self.assertRaises(KeyError):
            self.analytics.pair_report("wechat", "acct", "missing")


if __name__ == "__main__":
    unittest.main()
