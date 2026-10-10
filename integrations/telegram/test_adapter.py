from __future__ import annotations

import json
import unittest

from integrations.telegram import parse_telegram_export


def _payload(document):
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


class TelegramExportAdapterTest(unittest.TestCase):
    def test_parses_messages_chat_senders_dates_and_attachment_metadata(self):
        result = parse_telegram_export(_payload({
            "name": "Synthetic group",
            "type": "personal_chat",
            "id": 42,
            "messages": [
                {
                    "id": 7,
                    "type": "message",
                    "date": "2024-01-02T03:04:05",
                    "from": "Test sender",
                    "from_id": "user123",
                    "text": [
                        {"type": "plain", "text": "Hello "},
                        {"type": "bold", "text": "synthetic"},
                    ],
                    "photo": "photos/photo_1@02-01-2024_03-04-05.jpg",
                    "is_outgoing": False,
                },
                {
                    "id": 8,
                    "type": "message",
                    "date_unixtime": "1704164646",
                    "from": "Me",
                    "from_id": "user-self",
                    "text": "A document",
                    "file": "files/report.pdf",
                    "is_outgoing": True,
                },
                {
                    "id": 9,
                    "type": "message",
                    "date_unixtime": "1704164647",
                    "from": "Test sender",
                    "from_id": "user123",
                    "file": "video_files/clip.mp4",
                    "media_type": "video_file",
                },
            ],
            "personal_information": {"user_id": "account-synthetic", "phone_number": "+10000000000"},
        }), account_id="account-override")

        self.assertEqual((result["total"], result["valid"], result["invalid"]), (3, 3, 0))
        self.assertEqual(result["conversations"], [{"id": "42", "title": "Synthetic group", "kind": "private"}])
        image, file_message, video_message = result["messages"]
        self.assertEqual(image["platform"], "telegram")
        self.assertEqual(image["account_id"], "account-override")
        self.assertEqual(image["conversation_id"], "42")
        self.assertEqual(image["sender_id"], "user123")
        self.assertEqual(image["sender_name"], "Test sender")
        self.assertEqual(image["timestamp"], 1704135845)
        self.assertEqual(image["text"], "Hello synthetic")
        self.assertEqual(image["type"], "image")
        self.assertEqual(image["media_url"], "photos/photo_1@02-01-2024_03-04-05.jpg")
        self.assertEqual(image["file_name"], "photo_1@02-01-2024_03-04-05.jpg")
        self.assertEqual(file_message["timestamp"], 1704164646)
        self.assertEqual(file_message["type"], "file")
        self.assertEqual(file_message["media_url"], "files/report.pdf")
        self.assertTrue(file_message["is_self"])
        self.assertEqual(video_message["type"], "video")
        self.assertEqual(video_message["media_url"], "video_files/clip.mp4")
        self.assertNotIn("phone_number", repr(result))

    def test_service_messages_and_bad_rows_are_isolated(self):
        result = parse_telegram_export(_payload({
            "name": "Synthetic room",
            "type": "group",
            "id": "-1001",
            "messages": [
                {"id": 1, "type": "service", "date_unixtime": "1700000000", "action": "joined"},
                {"id": 2, "date": "not-a-date", "text": "bad timestamp"},
                None,
            ],
        }))
        self.assertEqual(result["conversations"][0]["kind"], "group")
        self.assertEqual(result["valid"], 1)
        self.assertEqual(result["invalid"], 2)
        self.assertEqual(result["messages"][0]["type"], "system")
        self.assertEqual(result["messages"][0]["text"], "joined")
        self.assertEqual([error["row"] for error in result["errors"]], [2, 3])

    def test_unsafe_attachment_paths_are_omitted_and_not_opened(self):
        result = parse_telegram_export(_payload({
            "name": "Synthetic room",
            "type": "private_chat",
            "id": "peer-1",
            "messages": [
                {"id": 1, "date_unixtime": "1700000000", "photo": "../../outside.jpg"},
                {"id": 2, "date_unixtime": "1700000001", "file": "C:\\private\\secret.exe"},
                {"id": 3, "date_unixtime": "1700000002", "file": "javascript:alert(1)"},
                {"id": 4, "date_unixtime": "1700000003", "photo": "photos/ok.jpg"},
            ],
        }))
        self.assertEqual(result["valid"], 4)
        self.assertEqual([message["media_url"] for message in result["messages"]], ["", "", "", "photos/ok.jpg"])
        self.assertEqual(len(result["warnings"]), 3)
        self.assertNotIn("secret.exe", repr(result))
        self.assertNotIn("javascript:", repr(result))

    def test_rejects_non_telegram_shape_invalid_json_and_oversize_payload(self):
        with self.assertRaisesRegex(ValueError, "包含 messages 数组"):
            parse_telegram_export(b"{}")
        with self.assertRaisesRegex(ValueError, "JSON 格式错误"):
            parse_telegram_export(b"{broken")
        with self.assertRaisesRegex(ValueError, "256 MiB"):
            parse_telegram_export(b" " * (256 * 1024 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()
