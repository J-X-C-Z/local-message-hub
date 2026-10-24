from __future__ import annotations

import unittest
from email.errors import HeaderParseError
from email.message import EmailMessage
from unittest.mock import patch

from integrations.email import parse_email_export


def _mail(**headers):
    message = EmailMessage()
    for key, value in {"From": "张三 <sender@example.com>", "To": "me@example.com", "Cc": "cc@example.com",
                       "Subject": "测试主题", "Message-ID": "<one@example.com>",
                       "Date": "Tue, 02 Jan 2024 03:04:05 +0000", **headers}.items():
        if value is not None:
            message[key] = value
    message.set_content("纯文本正文")
    return message


class EmailExportAdapterTest(unittest.TestCase):
    def test_mime_headers_body_attachments_and_schema(self):
        message = _mail()
        message.add_alternative("<p>duplicate HTML</p>", subtype="html")
        message.add_attachment(b"never execute", maintype="application", subtype="octet-stream", filename="报告.exe")
        result = parse_email_export(message.as_bytes(), "eml", "me@example.com")
        self.assertEqual((result["total"], result["valid"], result["invalid"]), (1, 1, 0))
        item = result["messages"][0]
        self.assertEqual(item["platform"], "email")
        self.assertEqual(item["sender_name"], "张三")
        self.assertEqual(item["sender_id"], "sender@example.com")
        self.assertEqual(item["timestamp"], 1704164645)
        for expected in ["测试主题", "纯文本正文", "报告.exe", "cc@example.com", "me@example.com"]:
            self.assertIn(expected, item["text"])
        self.assertNotIn("duplicate HTML", item["text"])
        self.assertEqual(item["media_url"], "")
        self.assertEqual(item["file_name"], "")
        self.assertFalse(item["is_self"])

    def test_html_is_text_without_scripts_links_or_remote_requests(self):
        message = _mail()
        message.set_content('<p>安全 &amp; 正文</p><script>evil()</script><style>bad</style><img src="https://tracker/secret"><a href="javascript:evil()">链接</a>', subtype="html")
        text = parse_email_export(message.as_bytes())["messages"][0]["text"]
        self.assertIn("安全 & 正文", text)
        self.assertIn("链接", text)
        for hidden in ["evil", "bad", "tracker", "<p>", "javascript:"]:
            self.assertNotIn(hidden, text)

    def test_mbox_invalid_rows_are_isolated_and_threads_use_root_reference(self):
        root = _mail()
        reply = _mail(**{"Message-ID": "<reply@example.com>", "References": "<one@example.com> <other@example.com>", "Subject": "Re: 测试主题"})
        payload = b"From nobody Tue Jan 2 03:04:05 2024\n" + root.as_bytes() + b"\nFrom nobody Tue Jan 2 03:04:05 2024\ninvalid\n\nbody\nFrom nobody Tue Jan 2 03:04:05 2024\n" + reply.as_bytes()
        result = parse_email_export(payload, "mbox", "test")
        self.assertEqual((result["total"], result["valid"], result["invalid"]), (3, 2, 1))
        self.assertEqual(result["errors"][0]["row"], 2)
        self.assertEqual(len(result["conversations"]), 1)
        self.assertEqual(result["messages"][0]["conversation_id"], result["messages"][1]["conversation_id"])

    def test_stable_account_scoped_ids_fallback_dates_and_reply_header(self):
        raw = _mail(**{"Date": None, "Message-ID": None}).as_bytes()
        first = parse_email_export(raw, account_id="a")
        again = parse_email_export(raw, account_id="a")
        other = parse_email_export(raw, account_id="b")
        self.assertEqual(first, again)
        self.assertNotEqual(first["messages"][0]["id"], other["messages"][0]["id"])
        self.assertEqual(first["messages"][0]["timestamp"], 0)
        self.assertEqual(len(first["warnings"]), 1)
        root = parse_email_export(_mail().as_bytes())["messages"][0]
        reply = parse_email_export(_mail(**{"In-Reply-To": "<one@example.com>", "Message-ID": "<second@example.com>"}).as_bytes())["messages"][0]
        self.assertEqual(root["conversation_id"], reply["conversation_id"])

    def test_legacy_charset_and_missing_optional_headers(self):
        raw = b"From: sender@example.com\nSubject: =?iso-8859-1?q?caf=E9?=\nContent-Type: text/plain; charset=iso-8859-1\n\ncaf\xe9"
        item = parse_email_export(raw)["messages"][0]
        self.assertIn("café", item["text"])
        self.assertEqual(item["timestamp"], 0)

    def test_limits_format_and_required_sender(self):
        self.assertEqual(parse_email_export(b"", "mbox")["total"], 0)
        self.assertEqual(parse_email_export(b"Subject: no sender\n\nbody")["invalid"], 1)
        with self.assertRaisesRegex(ValueError, "EML 或 MBOX"):
            parse_email_export(b"", "pst")
        with self.assertRaisesRegex(ValueError, "From 分隔行"):
            parse_email_export(b"not an mbox", "mbox")
        with patch("integrations.email.adapter._MAX_PAYLOAD_BYTES", 4):
            with self.assertRaisesRegex(ValueError, "256 MiB"):
                parse_email_export(b"12345")
        with patch("integrations.email.adapter._MAX_MESSAGE_BYTES", 4):
            self.assertEqual(parse_email_export(_mail().as_bytes())["invalid"], 1)

    def test_mime_parser_errors_are_isolated(self):
        with patch("integrations.email.adapter.BytesParser.parsebytes", side_effect=HeaderParseError("bad header")):
            result = parse_email_export(_mail().as_bytes())
        self.assertEqual(result["invalid"], 1)
        self.assertEqual(result["errors"][0]["row"], 1)


if __name__ == "__main__":
    unittest.main()
