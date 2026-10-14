import json
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import image_key_store
from .image_key_store import WeChatImageKeyStore


class ImageKeyStoreTests(unittest.TestCase):
    def test_keys_are_encrypted_account_scoped_and_never_returned_by_status(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WeChatImageKeyStore(directory)

            def fake_crypt(data, protect):
                return base64.b64encode(data) if protect else base64.b64decode(data)

            with patch.object(image_key_store, "_crypt", side_effect=fake_crypt):
                self.assertEqual(store.set("wxid-one", "0123456789abcdef"), {"configured": True})
                self.assertEqual(store.get("wxid-one"), "0123456789abcdef")
                self.assertIsNone(store.get("wxid-two"))
                self.assertTrue(store.has("wxid-one"))
                self.assertFalse(store.has("wxid-two"))

            files = list(Path(directory).glob("*.dpapi"))
            self.assertEqual(len(files), 1)
            self.assertNotIn(b"0123456789abcdef", files[0].read_bytes())

    def test_rejects_invalid_keys_and_account_binding_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WeChatImageKeyStore(directory)
            with patch.object(image_key_store, "_crypt", side_effect=lambda data, protect: data):
                for value in ("", "short", "0123456789abcde\u2603", "0123456789abcde\n"):
                    with self.subTest(value=value), self.assertRaises(ValueError):
                        store.set("wxid-one", value)
                path = store._path("wxid-one")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"account_id": "wxid-two", "key": "0123456789abcdef"}),
                                encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "账号绑定"):
                    store.get("wxid-one")

    @unittest.skipUnless(__import__("os").name == "nt", "Windows current-user DPAPI only")
    def test_windows_dpapi_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WeChatImageKeyStore(directory)
            store.set("wxid-one", "0123456789abcdef")
            self.assertEqual(store.get("wxid-one"), "0123456789abcdef")
            self.assertNotIn(b"0123456789abcdef", store._path("wxid-one").read_bytes())


if __name__ == "__main__":
    unittest.main()
