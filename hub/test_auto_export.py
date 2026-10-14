import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from hub.auto_export import AutoExportManager, MAX_MESSAGES, _is_mapped_network_drive
from hub.server import save_json
from hub.store import ChatStore


class AutoExportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = {"capabilities": {"auto_export": True}}
        self.store = ChatStore(self.root / "hub.sqlite3")
        self.store.ingest("wechat", "synthetic", [
            {"id": "room", "title": "Fixture", "kind": "private"},
        ], [
            {"id": "one", "conversation_id": "room", "timestamp": 20,
             "type": "text", "text": "synthetic export record", "is_self": True},
        ])
        self.manager = AutoExportManager(self.store, self.root, self.settings,
                                        lambda value: save_json(self.root / "settings.json", value),
                                        lambda: self.settings["capabilities"]["auto_export"])

    def tearDown(self):
        self.manager.close()
        self.store.close()
        self.temp.cleanup()

    def wait_finished(self):
        deadline = time.monotonic() + 5
        while self.manager.status()["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.manager.status()["running"])

    def test_scheduled_configuration_persists_and_runs_to_local_directory(self):
        output_dir = self.root / "exports"
        status = self.manager.configure({"enabled": True, "interval_hours": 1,
                                         "format": "json", "platform": "wechat",
                                         "output_dir": str(output_dir)})
        self.assertEqual(status["config"]["last_status"], "never")
        self.assertTrue(output_dir.is_dir())
        self.assertTrue((self.root / "settings.json").is_file())

        self.manager.run_now()
        self.wait_finished()
        status = self.manager.status()
        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["config"]["last_status"], "completed")
        exported = output_dir / status["config"]["last_file"]
        content = json.loads(exported.read_text(encoding="utf-8"))
        self.assertEqual(content[0]["text"], "synthetic export record")
        persisted = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(persisted["auto_export_config"]["last_status"], "completed")

    def test_due_schedule_resumes_after_reopening_the_manager(self):
        output_dir = self.root / "resumed"
        self.manager.configure({"enabled": True, "output_dir": str(output_dir)})
        saved = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
        saved["auto_export_config"]["next_run_at"] = int(time.time()) - 1
        resumed = AutoExportManager(self.store, self.root, saved,
                                    lambda value: save_json(self.root / "settings.json", value),
                                    lambda: saved["capabilities"]["auto_export"])
        try:
            self.assertTrue(resumed.run_due())
            deadline = time.monotonic() + 5
            while resumed.status()["running"] and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(resumed.status()["state"], "completed")
            self.assertTrue((output_dir / resumed.status()["config"]["last_file"]).is_file())
        finally:
            resumed.close()

    def test_disabling_schedule_cannot_race_between_due_check_and_worker_start(self):
        self.manager.configure({"enabled": True})
        self.manager.config["next_run_at"] = int(time.time()) - 1
        entered, release, disabled = threading.Event(), threading.Event(), threading.Event()
        original_run_now = self.manager.run_now
        result = []

        def paused_run_now():
            entered.set()
            self.assertTrue(release.wait(2))
            return original_run_now()

        self.manager.run_now = paused_run_now
        due_thread = threading.Thread(target=lambda: result.append(self.manager.run_due()))
        due_thread.start()
        self.assertTrue(entered.wait(2))

        def disable_schedule():
            self.manager.configure({"enabled": False})
            disabled.set()

        disable_thread = threading.Thread(target=disable_schedule)
        disable_thread.start()
        try:
            self.assertFalse(disabled.wait(0.05))
        finally:
            release.set()
        due_thread.join(2)
        disable_thread.join(2)
        self.assertFalse(due_thread.is_alive())
        self.assertFalse(disable_thread.is_alive())
        self.assertTrue(disabled.is_set())
        self.assertEqual(result, [True])
        self.assertFalse(self.manager.status()["config"]["enabled"])
        self.wait_finished()

    def test_capability_is_checked_before_starting_a_job(self):
        self.settings["capabilities"]["auto_export"] = False
        with self.assertRaises(PermissionError):
            self.manager.run_now()

    def test_invalid_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.configure({"interval_hours": 2})
        with self.assertRaises(ValueError):
            self.manager.configure({"output_dir": "relative/path"})
        with self.assertRaisesRegex(ValueError, "本机磁盘目录"):
            self.manager.configure({"output_dir": r"\\server\share\exports"})
        with self.assertRaises(ValueError):
            self.manager.configure({"format": "exe"})

    def test_windows_network_drive_type_is_rejected_but_local_drive_is_allowed(self):
        with patch("hub.auto_export.os.name", "nt"), \
                patch("hub.auto_export.ctypes.windll", create=True) as windll:
            windll.kernel32.GetDriveTypeW.return_value = 4
            self.assertTrue(_is_mapped_network_drive("Z:\\"))
            windll.kernel32.GetDriveTypeW.return_value = 3
            self.assertFalse(_is_mapped_network_drive("C:\\"))

    def test_resolved_network_target_is_rejected_before_creating_output_dir(self):
        redirected = self.root / "redirected-exports"
        cases = ((Path(r"\\server\share\exports"), False),
                 (Path(r"Z:\exports"), True))
        for resolved, mapped in cases:
            with self.subTest(target=str(resolved)), \
                    patch("hub.auto_export.Path.resolve", return_value=resolved), \
                    patch("hub.auto_export._is_mapped_network_drive", return_value=mapped):
                with self.assertRaisesRegex(ValueError, "本机磁盘目录"):
                    self.manager.configure({"output_dir": str(redirected)})
                self.assertFalse(redirected.exists())

    def test_message_limit_fails_without_creating_an_export(self):
        output_dir = self.root / "limited"
        self.manager.configure({"output_dir": str(output_dir)})
        with patch("hub.auto_export.MAX_MESSAGES", 0):
            self.manager.run_now()
            self.wait_finished()
        self.assertEqual(self.manager.status()["state"], "failed")
        self.assertEqual(self.manager.status()["last_error"], "ValueError")
        self.assertEqual(list(output_dir.iterdir()), [])

    def test_concurrent_ingest_does_not_shift_auto_export_pages(self):
        self.store.ingest("wechat", "synthetic", [], [
            {"id": "older", "conversation_id": "room", "timestamp": 10,
             "type": "text", "text": "snapshot older record"},
        ])
        original = ChatStore.export_page
        inserted = False

        def ingest_after_first_page(snapshot, *args, **kwargs):
            nonlocal inserted
            page = original(snapshot, *args, **kwargs)
            if not inserted and kwargs.get("after") is None:
                inserted = True
                self.store.ingest("wechat", "synthetic", [], [{
                    "id": "newer-during-export", "conversation_id": "room", "timestamp": 30,
                    "type": "text", "text": "arrived during export",
                }])
            return page

        output_dir = self.root / "snapshot"
        self.manager.configure({"output_dir": str(output_dir)})
        with patch.object(ChatStore, "export_page", new=ingest_after_first_page), \
                patch("hub.auto_export.PAGE_SIZE", 1):
            self.manager.run_now()
            self.wait_finished()

        self.assertTrue(inserted)
        self.assertEqual(self.manager.status()["state"], "completed")
        exported = output_dir / self.manager.status()["config"]["last_file"]
        rows = json.loads(exported.read_text(encoding="utf-8"))
        self.assertEqual([row["text"] for row in rows], [
            "snapshot older record", "synthetic export record",
        ])

    def test_cancelled_export_leaves_no_partial_file(self):
        entered, release = threading.Event(), threading.Event()
        original = ChatStore.export_page

        def blocked_search(snapshot, *args, **kwargs):
            entered.set()
            release.wait(2)
            return original(snapshot, *args, **kwargs)

        output_dir = self.root / "cancelled"
        self.manager.configure({"output_dir": str(output_dir)})
        with patch.object(ChatStore, "export_page", new=blocked_search):
            self.manager.run_now()
            self.assertTrue(entered.wait(2))
            self.manager.cancel()
            release.set()
            self.wait_finished()
        self.assertEqual(self.manager.status()["state"], "cancelled")
        self.assertEqual(list(output_dir.iterdir()), [])

    def test_every_format_streams_to_a_valid_file(self):
        import csv
        import io
        import zipfile

        for format_id, extension in (("json", "json"), ("csv", "csv"), ("md", "md"),
                                     ("txt", "txt"), ("html", "html"), ("xlsx", "xlsx"),
                                     ("sql", "sql"), ("chatlab", "json"),
                                     ("chatlab-jsonl", "jsonl")):
            with self.subTest(format=format_id):
                output_dir = self.root / ("format-" + format_id)
                self.manager.configure({"format": format_id, "output_dir": str(output_dir)})
                self.manager.run_now()
                self.wait_finished()
                self.assertEqual(self.manager.status()["state"], "completed")
                path = output_dir / self.manager.status()["config"]["last_file"]
                data = path.read_bytes()
                self.assertGreater(len(data), 0)
                if format_id == "json":
                    self.assertEqual(json.loads(data)[0]["text"], "synthetic export record")
                elif format_id == "csv":
                    self.assertIn("synthetic export record", data.decode("utf-8"))
                elif format_id in ("chatlab", "chatlab-jsonl"):
                    if format_id == "chatlab":
                        self.assertEqual(json.loads(data)["messages"][0]["content"],
                                         "synthetic export record")
                    else:
                        records = [json.loads(line) for line in data.splitlines()]
                        self.assertEqual(next(row for row in records if row["_type"] == "message")["content"],
                                         "synthetic export record")
                elif format_id == "xlsx":
                    with zipfile.ZipFile(io.BytesIO(data)) as archive:
                        self.assertIn(b"synthetic export record",
                                      archive.read("xl/worksheets/sheet1.xml"))

    def test_chatlab_auto_export_rejects_mixed_conversation_types(self):
        self.store.ingest("wechat", "synthetic", [
            {"id": "room", "title": "Fixture", "kind": "private"},
            {"id": "group-room", "title": "Synthetic group", "kind": "group"},
        ], [{"id": "group-message", "conversation_id": "group-room", "timestamp": 30,
             "type": "text", "text": "synthetic group record"}])
        output_dir = self.root / "mixed-chatlab"
        self.manager.configure({"format": "chatlab-jsonl", "platform": "wechat",
                                "output_dir": str(output_dir)})

        self.manager.run_now()
        self.wait_finished()

        self.assertEqual(self.manager.status()["state"], "failed")
        self.assertEqual(self.manager.status()["last_error"], "ValueError")
        self.assertEqual(list(output_dir.iterdir()), [])

    def test_cancel_after_temp_file_fsync_leaves_no_partial_or_final_file(self):
        original_fsync = os.fsync
        output_dir = self.root / "cancelled-after-write"
        self.manager.configure({"output_dir": str(output_dir)})

        def cancel_after_fsync(fd):
            original_fsync(fd)
            self.manager.cancel()

        with patch("hub.auto_export.os.fsync", new=cancel_after_fsync):
            self.manager.run_now()
            self.wait_finished()

        self.assertEqual(self.manager.status()["state"], "cancelled")
        self.assertEqual(list(output_dir.iterdir()), [])

    def test_cancel_serializes_with_final_file_publication(self):
        output_dir = self.root / "publish-race"
        self.manager.configure({"output_dir": str(output_dir)})
        replace_entered, release_replace, cancel_entered, cancel_done = (
            threading.Event(), threading.Event(), threading.Event(), threading.Event())
        original_replace = os.replace
        cancel_results = []

        def pause_replace(source, target):
            if Path(target).parent == output_dir:
                replace_entered.set()
                self.assertTrue(release_replace.wait(2))
            return original_replace(source, target)

        def request_cancel():
            cancel_entered.set()
            cancel_results.append(self.manager.cancel())
            cancel_done.set()

        with patch("hub.auto_export.os.replace", new=pause_replace):
            self.manager.run_now()
            self.assertTrue(replace_entered.wait(2))
            lock_free = self.manager._lock.acquire(blocking=False)
            if lock_free:
                self.manager._lock.release()
            self.assertFalse(lock_free, "the worker must hold the manager lock during publication")
            cancel_thread = threading.Thread(target=request_cancel)
            cancel_thread.start()
            self.assertTrue(cancel_entered.wait(2))
            self.assertFalse(cancel_done.wait(0.05),
                             "cancellation waits while atomic publication commits")
            release_replace.set()
            cancel_thread.join(2)
            self.assertFalse(cancel_thread.is_alive())
            self.wait_finished()

        self.assertEqual(cancel_results[0]["state"], "completed")
        self.assertEqual(self.manager.status()["state"], "completed")
        self.assertEqual(len(list(output_dir.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
