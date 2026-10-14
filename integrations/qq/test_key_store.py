import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import key_store
from .key_store import QQKeyProtectionError, load_key, migrate_legacy_key, persist_key, protect_key


class QQKeyStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "qq-local.json"
        self.key = "SyntheticKey1234"

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def fake_crypt(data, protect):
        return base64.b64encode(data) if protect else base64.b64decode(data)

    def test_protected_key_round_trip_and_atomic_config_persistence(self):
        with patch.object(key_store, "_crypt", side_effect=self.fake_crypt):
            protected = protect_key(self.key)
            self.assertTrue(protected.startswith("dpapi:v1:"))
            self.assertEqual(load_key(protected), (self.key, False))
            persist_key(self.path, {"data_dir": "fixture"}, self.key)
            raw = self.path.read_bytes()
            config = json.loads(raw)

        self.assertNotIn(self.key.encode(), raw)
        self.assertEqual(config["data_dir"], "fixture")
        self.assertTrue(config["key"].startswith("dpapi:v1:"))

    def test_legacy_plaintext_migrates_only_when_verified_value_is_unchanged(self):
        self.path.write_text(json.dumps({"data_dir": "fixture", "key": self.key}), encoding="utf-8")
        with patch.object(key_store, "_crypt", side_effect=self.fake_crypt):
            self.assertFalse(migrate_legacy_key(self.path, "DifferentFixtureKey"))
            self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["key"], self.key)
            self.assertTrue(migrate_legacy_key(self.path, self.key))
            saved = self.path.read_bytes()
            self.assertNotIn(self.key.encode(), saved)
            self.assertEqual(load_key(json.loads(saved)["key"]), (self.key, False))

    def test_protected_ciphertext_fails_closed_on_non_windows_or_foreign_user(self):
        protected = "dpapi:v1:Zm9yZWlnbi1jaXBoZXJ0ZXh0"
        with patch.object(key_store.os, "name", "posix"):
            with self.assertRaises(QQKeyProtectionError):
                load_key(protected)
            with self.assertRaises(QQKeyProtectionError):
                protect_key(self.key)

        with patch.object(key_store, "_crypt", side_effect=QQKeyProtectionError("foreign user")):
            with self.assertRaises(QQKeyProtectionError):
                load_key(protected)

    @unittest.skipUnless(os.name == "nt", "Windows current-user DPAPI only")
    def test_windows_current_user_dpapi_round_trip(self):
        protected = protect_key(self.key)
        self.assertEqual(load_key(protected), (self.key, False))
        self.assertNotIn(self.key, protected)


if __name__ == "__main__":
    unittest.main()
