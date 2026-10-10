import json
import tempfile
import unittest

from hub.imports import parse_import
from hub.store import ChatStore


class ImportTest(unittest.TestCase):
    def test_json_import_normalizes_bilingual_rows_and_deduplicates(self):
        rows = [{
            "id": f"message-{index}", "platform": "wechat", "account_id": "synthetic",
            "conversation_id": "room", "conversation_title": "Synthetic room",
            "timestamp": 1_800_000_000 + index, "sender_name": "Tester",
            "type": "text", "text": f"测试 message {index}",
        } for index in range(1000)]
        payload = json.dumps(rows, ensure_ascii=False).encode("utf-8")
        parsed = parse_import(payload, "json", "all")
        self.assertEqual((parsed["total"], parsed["valid"], parsed["invalid"]), (1000, 1000, 0))
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(f"{directory}/index.sqlite3")
            try:
                for _ in range(2):
                    report = store.ingest("wechat", "synthetic", parsed["conversations"], parsed["messages"])
                self.assertEqual(report["messages"], 1000)
                self.assertEqual(store.count(), 1000)
                self.assertEqual(store.search("测试 message 999")["total"], 1)
            finally:
                store.close()

    def test_csv_aliases_bad_rows_and_platform_defaults(self):
        payload = (
            "时间,平台,账号,会话,发送者,发送者ID,类型,内容\n"
            "2026-10-10 12:00:00,qq,synthetic,Room,Tester,u1,text,你好\n"
            "2026-10-10 12:00:01,qq,synthetic,,,text,\n"
        ).encode("utf-8")
        result = parse_import(payload, "csv", "all")
        self.assertEqual(result["valid"], 1)
        self.assertEqual(result["invalid"], 1)
        self.assertEqual(result["messages"][0]["platform"], "qq")
        self.assertEqual(result["messages"][0]["timestamp"], 1791604800)

    def test_json_requires_an_array_and_rejects_unsupported_platform(self):
        with self.assertRaisesRegex(ValueError, "须为消息数组"):
            parse_import(b"{}", "json", "wechat")
        payload = json.dumps([{"platform": "unsupported", "account_id": "a", "conversation_id": "c"}]).encode()
        result = parse_import(payload, "json", "wechat")
        self.assertEqual(result["valid"], 0)
        self.assertEqual(result["invalid"], 1)

    def test_telegram_export_imports_messages_and_safe_attachment_metadata(self):
        document = {
            "id": 42, "name": "Synthetic room", "type": "personal_chat",
            "personal_information": {"user_id": "must-not-win"},
            "messages": [{
                "id": 7, "type": "message", "date_unixtime": "1791604800",
                "from_id": "user42", "from": "Tester", "text": ["你好", {"type": "bold", "text": " Telegram"}],
                "photo": "photos/image_1.jpg", "media_type": "photo",
            }, {
                "id": 8, "type": "message", "date_unixtime": "1791604801",
                "text": "unsafe", "file": "../../outside.txt",
            }],
        }
        parsed = parse_import(json.dumps(document).encode(), "telegram", "telegram", "synthetic-account")
        self.assertEqual(parsed["valid"], 2)
        self.assertEqual(parsed["messages"][0]["account_id"], "synthetic-account")
        self.assertEqual(parsed["messages"][0]["text"], "你好 Telegram")
        self.assertEqual(parsed["messages"][0]["media_url"], "photos/image_1.jpg")
        self.assertEqual(parsed["messages"][1]["media_url"], "")

        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(f"{directory}/index.sqlite3")
            try:
                report = store.ingest("telegram", "synthetic-account", parsed["conversations"], parsed["messages"])
                self.assertEqual(report["messages"], 2)
                self.assertEqual(store.search("Telegram", platform="all")["total"], 1)
                self.assertEqual(store.search("你好", platform="telegram")["total"], 1)
            finally:
                store.close()

    def test_telegram_format_requires_telegram_platform(self):
        with self.assertRaisesRegex(ValueError, "必须选择 Telegram"):
            parse_import(b"{}", "telegram", "wechat")


if __name__ == "__main__":
    unittest.main()
