import imaplib
import os
import ssl
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from .provider import EmailProvider, EmailProviderError, _MAX_MESSAGE


_MAIL = b"From: Sender <sender@example.test>\r\nTo: me@example.test\r\nSubject: Test\r\nMessage-ID: <one@example.test>\r\nDate: Fri, 23 Oct 2026 12:00:00 +0800\r\n\r\nhello"


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
            return "OK", [f"1 (UID {args[0]} RFC822.SIZE {len(_MAIL)})".encode()]
        return "OK", [(b"1 (BODY[] {123}", _MAIL), b")"]

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
        for metadata, body in ((str(_MAX_MESSAGE + 1), _MAIL), (str(len(_MAIL) + 1), _MAIL)):
            self.client.uid.side_effect = lambda command, *args: (("OK", [b"1"]) if command == "SEARCH" else
                ("OK", [f"1 (RFC822.SIZE {metadata})".encode()]) if args[1] == "(RFC822.SIZE)" else
                ("OK", [(b"BODY[]", body)]))
            with self.assertRaises(EmailProviderError):
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
