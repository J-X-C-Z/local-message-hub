import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .bot_credentials import WeComBotCredentials


class WeComBotCredentialsTest(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows current-user DPAPI only")
    def test_real_current_user_dpapi_round_trip_uses_temp_storage(self):
        with tempfile.TemporaryDirectory() as root:
            vault = WeComBotCredentials(Path(root) / "synthetic.credentials")
            status = vault.set("synthetic_bot", "synthetic-secret-123")
            self.assertTrue(status["configured"])
            self.assertNotIn(b"synthetic-secret-123", vault.path.read_bytes())
            self.assertEqual(vault.get(), {
                "bot_id": "synthetic_bot", "secret": "synthetic-secret-123"})
            self.assertFalse(vault.delete()["configured"])

    def test_credentials_are_hidden_from_status_and_removable(self):
        def fake_crypt(value, protect):
            return bytes(byte ^ 0xA5 for byte in value) if protect else bytes(
                byte ^ 0xA5 for byte in value)

        with tempfile.TemporaryDirectory() as root, patch(
                "integrations.wecom.bot_credentials._crypt", side_effect=fake_crypt):
            vault = WeComBotCredentials(Path(root) / "bot.credentials")
            self.assertFalse(vault.status()["configured"])
            status = vault.set("bot_123", "synthetic-secret-123")
            self.assertTrue(status["configured"])
            self.assertNotIn("secret", status)
            self.assertEqual(vault.get(), {"bot_id": "bot_123", "secret": "synthetic-secret-123"})
            self.assertNotIn(b"synthetic-secret-123", vault.path.read_bytes())
            self.assertFalse(vault.delete()["configured"])

    def test_bot_id_validation_matches_send_queue_identity_contract(self):
        with tempfile.TemporaryDirectory() as root, patch(
                "integrations.wecom.bot_credentials._crypt",
                side_effect=lambda value, _protect: value):
            vault = WeComBotCredentials(Path(root) / "bot.credentials")
            for bot_id in ("bot/id", "bot:id", "bot id", "机器人", "x" * 129):
                with self.subTest(bot_id=bot_id), self.assertRaisesRegex(ValueError, "机器人 ID"):
                    vault.set(bot_id, "synthetic-secret-123")
            self.assertFalse(vault.path.exists())
            self.assertTrue(vault.set("bot_123@example.test", "synthetic-secret-123")["configured"])


if __name__ == "__main__":
    unittest.main()
