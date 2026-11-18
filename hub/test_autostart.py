from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hub import autostart


class WindowsAutostartTests(unittest.TestCase):
    def test_source_startup_command_uses_hidden_pythonw_and_background_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pythonw = root / "integrations/wechat/runtime/venv/Scripts/pythonw.exe"
            pythonw.parent.mkdir(parents=True)
            pythonw.write_bytes(b"")
            with patch.object(autostart.sys, "frozen", False, create=True):
                command = autostart.startup_command(root)
        self.assertEqual(
            command,
            subprocess.list2cmdline([
                str(pythonw.resolve()),
                str(root.resolve() / "start_hub.py"),
                "--background",
            ]),
        )

    def test_status_compares_the_expected_command(self):
        expected = r'"C:\LocalMessageHub.exe" --background'
        with patch.object(autostart, "startup_command", return_value=expected), \
                patch.object(autostart, "_supported", return_value=True), \
                patch.object(autostart, "_registered_command", return_value=expected):
            self.assertTrue(autostart.status("C:/ignored")["installed"])
        with patch.object(autostart, "startup_command", return_value=expected), \
                patch.object(autostart, "_supported", return_value=True), \
                patch.object(autostart, "_registered_command", return_value=None):
            self.assertFalse(autostart.status("C:/ignored")["installed"])

    def test_set_enabled_rejects_non_boolean_and_non_windows(self):
        with patch.object(autostart, "_supported", return_value=True), \
                patch.object(autostart, "status", return_value={}):
            with self.assertRaisesRegex(ValueError, "无效"):
                autostart.set_enabled("C:/ignored", "true")
        with patch.object(autostart, "_supported", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Windows"):
                autostart.set_enabled("C:/ignored", True)


if __name__ == "__main__":
    unittest.main()
