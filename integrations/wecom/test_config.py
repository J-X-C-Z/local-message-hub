from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from . import config


class WeComConfigTest(unittest.TestCase):
    def test_first_run_configuration_is_created_in_selected_local_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "source"
            local_dir = root / "local"
            data_dir.mkdir()
            with patch.multiple(config, LOCAL_DIR=local_dir, CONFIG_PATH=local_dir / "wecom.json"):
                result = config.configure(str(data_dir), "0123456789abcdef0123456789abcdef", "slot-1")
                saved = json.loads((local_dir / "wecom.json").read_text(encoding="utf-8"))
            self.assertEqual(result, {"configured": "true"})
            self.assertEqual(saved["database_root"], str(data_dir.resolve()))
            self.assertEqual(saved["key_hex"], "0123456789abcdef0123456789abcdef")
            self.assertEqual(saved["user_slot_id"], "slot-1")

    def test_first_run_requires_all_source_identity_fields_without_writing_partial_config(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "source"
            local_dir = root / "local"
            data_dir.mkdir()
            config_path = local_dir / "wecom.json"
            with patch.multiple(config, LOCAL_DIR=local_dir, CONFIG_PATH=config_path):
                with self.assertRaisesRegex(ValueError, "账号槽位标识"):
                    config.configure(str(data_dir), "0123456789abcdef0123456789abcdef", "")
            self.assertFalse(config_path.exists())

    def test_user_slot_identifier_cannot_escape_database_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "source"
            local_dir = root / "local"
            data_dir.mkdir()
            with patch.multiple(config, LOCAL_DIR=local_dir, CONFIG_PATH=local_dir / "wecom.json"):
                with self.assertRaisesRegex(ValueError, "账号槽位标识"):
                    config.configure(str(data_dir), "0123456789abcdef0123456789abcdef", "../outside")


if __name__ == "__main__":
    unittest.main()
