import imaplib
import os
import ssl
import tempfile
import unittest
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

from .provider import EmailProvider, EmailProviderError, _MAX_MESSAGE


_MAIL = b"From: Sender <sender@example.test>\r\nTo: me@example.test\r\nSubject: Test\r\nMessage-ID: <one@example.test>\r\nDate: Fri, 23 Oct 2026 12:00:00 +0800\r\n\r\nhello"


def _mail_with_attachment(filename="report.pdf", payload=b"attachment bytes"):
    message = EmailMessage()
    message["From"] = "Sender <sender@example.test>"
    message["To"] = "me@example.test"
    message["Subject"] = "Attachment"
    message["Message-ID"] = "<one@example.test>"
    message["Date"] = "Fri, 23 Oct 2026 12:00:00 +0800"
    message.set_content("hello")
    message.add_attachment(payload, maintype="application", subtype="octet-stream",
                           filename=filename)
    return message.as_bytes()


class EmailProviderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        crypt = patch("integrations.email.provider._crypt", side_effect=lambda value, _: bytes(b ^ 0xA5 for b in value))
        crypt.start()
        self.addCleanup(crypt.stop)
        self.provider = EmailProvider(self.temp.name)
        self.values = {"provider": "gmail", "email": "me@example.test", "password": "synthetic-secret"}
        self.provider.configure(self.values)
        self.mail = _MAIL
        self.client = MagicMock()
        self.client.select.return_value = ("OK", [b"2"])
        self.client.response.return_value = ("UIDVALIDITY", [b"123"])
        self.client.uid.side_effect = self.uid
        connection = patch("integrations.email.provider.imaplib.IMAP4_SSL", return_value=self.client)
        self.connection = connection.start()
        self.addCleanup(connection.stop)

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [b"1 2"]
        if args[1] == "(RFC822.SIZE)":
            return "OK", [f"1 (UID {args[0]} RFC822.SIZE {len(self.mail)})".encode()]
        return "OK", [(f"1 (BODY[] {{{len(self.mail)}}}".encode(), self.mail), b")"]

    def test_readonly_verified_tls_bounded_sync_and_explicit_checkpoint(self):
        result = self.provider.sync(limit=1)
        self.assertEqual(result["fetched"], 1)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["records"]["messages"][0]["platform"], "email")
        self.assertFalse(self.provider.cursor_path.exists())
        self.client.select.assert_called_once_with('"INBOX"', readonly=True)
        context = self.connection.call_args.kwargs["ssl_context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.client.uid.assert_any_call("FETCH", "1", f"(BODY.PEEK[]<0.{_MAX_MESSAGE + 1}>)")
        self.client.close.assert_not_called()
        self.client.logout.assert_called_once()
        self.provider.commit_checkpoint(result["checkpoint"])
        self.provider.sync()
        self.client.uid.assert_any_call("SEARCH", None, "UID", "2:*")
        self.provider.reset_checkpoint()
        self.assertFalse(self.provider.cursor_path.exists())

    def test_credentials_encrypted_redacted_and_only_preserved_for_same_identity(self):
        self.assertNotIn(b"synthetic-secret", self.provider.config_path.read_bytes())
        self.assertNotIn("password", self.provider.status())
        self.provider.configure({"provider": "gmail", "email": "me@example.test"})
        with self.assertRaises(ValueError):
            self.provider.configure({"provider": "gmail", "email": "other@example.test"})
        self.assertEqual(self.provider.status()["email"], "me@example.test")
        self.assertEqual(len(self.provider.status()["accounts"]), 1)
        for key, value in (("host", "evil\r\nINBOX"), ("port", True),
                           ("password", "bad\x01secret"), ("folder", "a\nBAD")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.provider.configure({**self.values, key: value})

    def test_oauth2_and_quoted_folder(self):
        self.provider.configure({"provider": "outlook", "email": "me@example.test",
                                 "auth": "oauth2", "access_token": "synthetic-token", "folder": 'Sent "Mail"'})
        self.provider.sync()
        self.client.login.assert_not_called()
        mechanism, callback = self.client.authenticate.call_args.args
        self.assertEqual(mechanism, "XOAUTH2")
        self.assertEqual(callback(b""), b"user=me@example.test\x01auth=Bearer synthetic-token\x01\x01")
        self.assertEqual(callback(b'{"status":"401"}'), b"")
        self.client.select.assert_called_once_with('"Sent \\"Mail\\""', readonly=True)
        self.assertNotIn("access_token", self.provider.status())

    def test_uidvalidity_reset_and_filter_uid_search_star_old_uid(self):
        first = self.provider.sync()
        self.provider.commit_checkpoint(first["checkpoint"])
        self.client.uid.side_effect = lambda command, *args: ("OK", [b"2"]) if command == "SEARCH" else self.uid(command, *args)
        self.assertEqual(self.provider.sync()["fetched"], 0)
        self.client.response.return_value = ("UIDVALIDITY", [b"456"])
        self.assertEqual(self.provider.sync()["fetched"], 1)
        self.client.uid.assert_any_call("SEARCH", None, "UID", "1:*")

    def test_error_cancel_oversize_and_truncation_do_not_write_checkpoint(self):
        with self.assertRaises(EmailProviderError):
            self.provider.sync(cancel=lambda: True)
        self.assertFalse(self.provider.cursor_path.exists())
        metadata = str(_MAX_MESSAGE + 1)
        self.client.uid.side_effect = lambda command, *args: (("OK", [b"1"]) if command == "SEARCH" else
            ("OK", [f"1 (RFC822.SIZE {metadata})".encode()]) if args[1] == "(RFC822.SIZE)" else
            ("OK", [(b"BODY[]", _MAIL)]))
        with self.assertRaises(EmailProviderError):
            self.provider.sync()
        self.assertFalse(self.provider.cursor_path.exists())

    def test_truncated_body_does_not_write_checkpoint(self):
        full = self.mail

        def truncated(command, *args):
            if command == "SEARCH":
                return "OK", [b"1"]
            if args[1] == "(RFC822.SIZE)":
                return "OK", [f"1 (RFC822.SIZE {len(full)})".encode()]
            return "OK", [(f"1 (BODY[] {{{len(full)}}}".encode(), full[:10]), b")"]

        self.client.uid.side_effect = truncated
        with self.assertRaisesRegex(EmailProviderError, "截断"):
            self.provider.sync()
        self.assertFalse(self.provider.cursor_path.exists())

    def test_server_errors_are_redacted_and_always_logout(self):
        self.client.login.side_effect = imaplib.IMAP4.error("private synthetic-secret reply")
        with self.assertRaises(EmailProviderError) as raised:
            self.provider.sync()
        self.assertNotIn("synthetic-secret", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.client.logout.assert_called_once()

    def test_config_change_resets_cursor_and_rejects_stale_checkpoint(self):
        checkpoint = self.provider.sync()["checkpoint"]
        self.provider.commit_checkpoint(checkpoint)
        self.provider.configure({**self.values, "folder": "Archive"})
        self.assertFalse(self.provider.cursor_path.exists())
        with self.assertRaises(EmailProviderError):
            self.provider.commit_checkpoint(checkpoint)

    def test_corrupt_config_status_is_safe(self):
        self.provider.config_path.write_bytes(b"corrupt")
        self.assertFalse(self.provider.status()["configured"])
        self.assertTrue(self.provider.configure(self.values)["configured"])

    def test_multiple_accounts_keep_independent_encrypted_credentials_and_checkpoints(self):
        first = self.provider.sync(limit=1)
        self.provider.commit_checkpoint(first["checkpoint"], first["account_id"])
        qq_cursor = self.provider.cursor_path.read_bytes()
        self.provider.configure({"provider": "imap", "email": "campus@example.test",
                                 "host": "imap.example.test", "password": "campus-secret"})
        status = self.provider.status()
        self.assertEqual({account["email"] for account in status["accounts"]},
                         {"me@example.test", "campus@example.test"})
        campus = next(account for account in status["accounts"] if account["email"] == "campus@example.test")
        self.assertNotIn(b"campus-secret", (self.provider.accounts_path / f"{campus['id']}.credentials").read_bytes())
        self.assertEqual(self.provider.cursor_path.read_bytes(), qq_cursor)
        self.assertFalse(self.provider._cursor_path({"email": "campus@example.test"}).exists())
        self.provider.remove(campus["id"])
        self.assertEqual([account["email"] for account in self.provider.status()["accounts"]], ["me@example.test"])
        self.assertEqual(self.provider.cursor_path.read_bytes(), qq_cursor)

    def test_attachment_switch_metadata_content_retry_and_safe_path(self):
        self.mail = _mail_with_attachment("../../报告.exe", b"first")
        disabled = self.provider.sync(download_attachments=False)["records"]["messages"][0]
        self.assertEqual(disabled["attachments"], [{
            "type": "file", "name": "../../报告.exe", "size": 5,
            "mime_type": "application/octet-stream", "available": False,
        }])
        self.assertFalse(self.provider.attachments_path.exists())

        enabled = self.provider.sync(download_attachments=True)["records"]["messages"][0]
        cached = self.provider.cached_attachment(enabled)
        self.assertTrue(cached["available"])
        self.assertEqual(os.path.basename(cached["path"]), "0")
        self.assertEqual((cached["name"], cached["mime_type"]),
                         ("../../报告.exe", "application/octet-stream"))
        self.assertEqual(self.provider.cached_attachment(enabled)["path"], cached["path"])
        self.assertEqual(self.temp.name in cached["path"], True)
        self.assertFalse((self.provider.storage.parent / "报告.exe").exists())

        self.mail = _mail_with_attachment("../../报告.exe", b"second")
        retried = self.provider.sync(download_attachments=True)["records"]["messages"][0]
        self.assertEqual(self.provider.cached_attachment(retried)["path"], cached["path"])
        with open(cached["path"], "rb") as stream:
            self.assertEqual(stream.read(), b"second")

    def test_attachment_limit_uses_small_mocked_cap(self):
        self.mail = _mail_with_attachment(payload=b"12345")
        with patch("integrations.email.provider._MAX_ATTACHMENT", 4):
            item = self.provider.sync(download_attachments=True)["records"]["messages"][0]
        self.assertEqual(item["attachments"][0]["size"], 5)
        self.assertTrue(item["attachments"][0]["too_large"])
        self.assertFalse(self.provider.cached_attachment(item)["available"])

    def test_attachment_cancel_stops_before_commit(self):
        self.mail = _mail_with_attachment()
        calls = iter((False, False, True))
        with self.assertRaises(EmailProviderError):
            self.provider.sync(download_attachments=True, cancel=lambda: next(calls))
        self.assertFalse(any(path.is_file() for path in self.provider.attachments_path.rglob("*"))
                         if self.provider.attachments_path.exists() else False)

    def test_download_rejects_uidvalidity_and_message_id_mismatch(self):
        self.mail = _mail_with_attachment()
        item = self.provider.sync()["records"]["messages"][0]
        self.client.response.return_value = ("UIDVALIDITY", [b"456"])
        with self.assertRaisesRegex(EmailProviderError, "UIDVALIDITY"):
            self.provider.download_attachment(item)

        self.client.response.return_value = ("UIDVALIDITY", [b"123"])
        wrong = dict(item, id="email-wrong")
        with self.assertRaisesRegex(EmailProviderError, "Message-ID"):
            self.provider.download_attachment(wrong)

    def test_cached_attachment_is_reused_offline_and_after_account_removal(self):
        self.mail = _mail_with_attachment()
        item = self.provider.sync(download_attachments=True)["records"]["messages"][0]
        cached = self.provider.cached_attachment(item)
        self.connection.reset_mock()
        self.assertEqual(self.provider.download_attachment(item), cached)
        self.connection.assert_not_called()

        account_id = self.provider.status()["accounts"][0]["id"]
        self.provider.remove(account_id)
        self.assertTrue(self.provider.cached_attachment(item)["available"])

    def test_cached_attachment_remains_available_after_a_later_sync(self):
        self.mail = _mail_with_attachment()
        self.provider.sync(download_attachments=True)
        self.provider.reset_checkpoint()
        item = self.provider.sync(download_attachments=False)["records"]["messages"][0]
        self.assertTrue(item["attachments"][0]["available"])

    def test_oversized_attachment_fails_before_connecting(self):
        self.mail = _mail_with_attachment(payload=b"12345")
        with patch("integrations.email.provider._MAX_ATTACHMENT", 4):
            item = self.provider.sync(download_attachments=True)["records"]["messages"][0]
        self.connection.reset_mock()
        with self.assertRaisesRegex(EmailProviderError, "32 MiB"):
            self.provider.download_attachment(item)
        self.connection.assert_not_called()

    def test_removing_the_legacy_account_deletes_its_checkpoint(self):
        result = self.provider.sync()
        self.provider.commit_checkpoint(result["checkpoint"])
        self.assertTrue(self.provider.cursor_path.exists())
        account_id = self.provider.status()["accounts"][0]["id"]
        self.provider.remove(account_id)
        self.assertFalse(self.provider.cursor_path.exists())

    def test_corrupt_secondary_profile_does_not_hide_valid_accounts(self):
        self.provider.accounts_path.mkdir(exist_ok=True)
        (self.provider.accounts_path / f"{'a' * 24}.credentials").write_bytes(b"corrupt")
        status = self.provider.status()
        self.assertTrue(status["configured"])
        self.assertEqual([account["email"] for account in status["accounts"]],
                         ["me@example.test"])

    @unittest.skipUnless(os.name == "nt", "Windows current-user DPAPI only")
    def test_real_dpapi_roundtrip_in_temporary_storage(self):
        from integrations.wecom.bot_credentials import _crypt
        with patch("integrations.email.provider._crypt", side_effect=_crypt):
            self.provider.config_path.unlink()
            self.provider.configure(self.values)
            self.assertNotIn(b"synthetic-secret", self.provider.config_path.read_bytes())
            self.provider.sync(limit=1)
            self.client.login.assert_called_once_with("me@example.test", "synthetic-secret")


if __name__ == "__main__":
    unittest.main()
