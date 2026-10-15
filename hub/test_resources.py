import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from . import resources as resource_module
from .resources import MAX_ITEMS, ResourceJobManager


class ResourceJobTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.files = {}
        self.manager = ResourceJobManager(self.root, self.resolve)

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def resolve(self, request):
        key = request["message_id"]
        value = self.files.get(key)
        if isinstance(value, Exception):
            raise value
        return value, "fixture image.png"

    def item(self, message_id="message-1"):
        return {"platform": "wechat", "account_id": "synthetic", "message_id": message_id,
                "conversation_id": "room"}

    def wait(self, job_id, states=("completed", "failed", "cancelled")):
        deadline = time.monotonic() + 3
        state = self.manager.status(job_id)
        while state["state"] not in states and time.monotonic() < deadline:
            time.sleep(0.01)
            state = self.manager.status(job_id)
        return state

    def test_exports_local_files_to_zip_with_provenance_manifest(self):
        source = self.root / "synthetic.png"
        source.write_bytes(b"synthetic image bytes")
        self.files["message-1"] = source
        started = self.manager.start([self.item()])
        result = self.wait(started["id"])
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["exported"], 1)
        path = self.manager.download_path(result["id"])
        self.assertTrue(path.is_relative_to(self.root / "resources" / "jobs"))
        with ZipFile(path) as archive:
            self.assertEqual(archive.read("attachments/001-fixture_image.png"), b"synthetic image bytes")
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["items"][0]["platform"], "wechat")
        self.assertEqual(manifest["items"][0]["message_id"], "message-1")
        self.assertEqual(manifest["items"][0]["sha256"],
                         hashlib.sha256(b"synthetic image bytes").hexdigest())

    def test_moments_identity_is_strict_and_manifest_never_contains_cache_paths(self):
        moment = {"resource_kind": "moment", "platform": "wechat", "account_id": "synthetic",
                  "post_id": "post-1", "media_index": 2, "variant": "live"}
        self.assertEqual(self.manager._validate_items([moment]), [moment])
        with self.assertRaises(ValueError):
            self.manager._validate_items([{**moment, "path": "C:/private/cache.jpg"}])
        with self.assertRaises(ValueError):
            self.manager._validate_items([{**moment, "message_id": "mixed-identity"}])
        self.assertEqual(self.manager._validate_items([moment, moment]), [moment])

        source = self.root / "moments-live.mp4"
        source.write_bytes(b"synthetic video")
        manager = ResourceJobManager(self.root, lambda _request: (source, "live.mp4"))
        try:
            result = manager.start([moment])
            status = self.wait_manager(manager, result["id"])
            self.assertEqual((status["state"], status["exported"]), ("completed", 1))
            with ZipFile(manager.download_path(status["id"])) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["items"][0], {**moment, "status": "exported",
                                                     "file": "001-live.mp4", "bytes": len(b"synthetic video"),
                                                     "sha256": hashlib.sha256(b"synthetic video").hexdigest()})
            self.assertNotIn("cache", json.dumps(manifest).lower())
        finally:
            manager.close()

    def test_wechat_source_identity_is_composite_and_numeric_local_id_is_canonical(self):
        source = {"resource_kind": "wechat_source", "platform": "wechat",
                  "account_id": "synthetic", "conversation_id": "room",
                  "shard": "message_1.db", "local_id": "7", "image_tier": "best"}
        canonical = {**source, "local_id": 7}
        self.assertEqual(self.manager._validate_items([source]), [canonical])
        self.assertEqual(self.manager._validate_items([source, canonical]), [canonical])
        for invalid in ({**source, "shard": "../message_1.db"},
                        {**source, "local_id": -1}, {**source, "local_id": True},
                        {**source, "local_id": "1" * 21},
                        {**source, "local_id": "7", "path": "C:/private/cache.jpg"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.manager._validate_items([invalid])

    def test_job_reports_partial_resolver_failures_without_private_error_text(self):
        source = self.root / "good.bin"
        source.write_bytes(b"ok")
        self.files["good"] = source
        self.files["bad"] = RuntimeError("private path or content must not persist")
        started = self.manager.start([self.item("good"), self.item("bad")])
        result = self.wait(started["id"])
        self.assertEqual(result["state"], "completed")
        self.assertEqual((result["exported"], result["failed"]), (1, 1))
        self.assertEqual(result["error"], "")
        with ZipFile(self.manager.download_path(result["id"])) as archive:
            text = archive.read("manifest.json").decode()
        self.assertNotIn("private path", text)
        self.assertIn('"error": "RuntimeError"', text)

    def test_cancel_cleans_partial_resources(self):
        started_resolve = threading.Event()
        release = threading.Event()
        source = self.root / "slow.bin"
        source.write_bytes(b"fixture")

        def slow_resolver(_request):
            started_resolve.set()
            release.wait(2)
            return source, source.name

        manager = ResourceJobManager(self.root, slow_resolver)
        try:
            job = manager.start([self.item()])
            self.assertTrue(started_resolve.wait(1))
            manager.cancel(job["id"])
            release.set()
            result = self.wait_manager(manager, job["id"])
            self.assertEqual(result["state"], "cancelled")
            self.assertIsNone(manager.download_path(job["id"]))
            self.assertFalse((self.root / "resources" / "jobs" / job["id"]).exists())
        finally:
            manager.close()

    def test_copy_enforces_stream_size_even_if_source_grows_after_stat(self):
        source = self.root / "growing.bin"
        source.write_bytes(b"ok")
        original_open = Path.open

        def grow_after_stat(path, mode="r", *args, **kwargs):
            if path == source and mode == "rb":
                with original_open(path, "ab") as writer:
                    writer.write(b"too-large")
            return original_open(path, mode, *args, **kwargs)

        with patch.object(resource_module, "MAX_ITEM_BYTES", 4), \
                patch.object(resource_module.Path, "open", new=grow_after_stat):
            self.files["growing"] = source
            result = self.wait(self.manager.start([self.item("growing")])["id"])
        self.assertEqual(result["state"], "completed")
        self.assertEqual((result["exported"], result["failed"], result["bytes"]), (0, 1, 0))
        self.assertFalse(list((self.root / "resources" / "jobs" / result["id"] / "files").glob("*")))

    def test_cancel_is_checked_between_file_chunks(self):
        source = self.root / "large.bin"
        source.write_bytes(b"x" * (2 * 1024 * 1024))
        original_open = Path.open
        first_read = threading.Event()
        release_read = threading.Event()

        class PausedReader:
            def __init__(self, stream):
                self.stream = stream
                self.paused = False

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.stream.close()

            def read(self, size):
                block = self.stream.read(size)
                if block and not self.paused:
                    self.paused = True
                    first_read.set()
                    release_read.wait(2)
                return block

            def fileno(self):
                return self.stream.fileno()

        def pause_during_copy(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            if path == source and mode == "rb":
                return PausedReader(stream)
            return stream

        with patch.object(resource_module.Path, "open", new=pause_during_copy):
            self.files["large"] = source
            job_id = self.manager.start([self.item("large")])["id"]
            self.assertTrue(first_read.wait(1))
            self.manager.cancel(job_id)
            release_read.set()
            result = self.wait(job_id)
        self.assertEqual(result["state"], "cancelled")
        self.assertIsNone(self.manager.download_path(job_id))
        self.assertFalse((self.root / "resources" / "jobs" / job_id).exists())

    def test_copy_rejects_a_same_size_source_mutation_during_read(self):
        source = self.root / "changing.bin"
        source.write_bytes(b"x" * (2 * 1024 * 1024))
        original_open = Path.open
        first_read = threading.Event()
        release_read = threading.Event()

        class PausedReader:
            def __init__(self, stream):
                self.stream = stream
                self.paused = False

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.stream.close()

            def fileno(self):
                return self.stream.fileno()

            def read(self, size):
                block = self.stream.read(size)
                if block and not self.paused:
                    self.paused = True
                    first_read.set()
                    release_read.wait(2)
                return block

        def pause_during_copy(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            if path == source and mode == "rb":
                return PausedReader(stream)
            return stream

        with patch.object(resource_module.Path, "open", new=pause_during_copy):
            self.files["changing"] = source
            job_id = self.manager.start([self.item("changing")])["id"]
            self.assertTrue(first_read.wait(1))
            source.write_bytes(b"y" * (2 * 1024 * 1024))
            stat = source.stat()
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
            release_read.set()
            result = self.wait(job_id)

        self.assertEqual(result["state"], "completed")
        self.assertEqual((result["exported"], result["failed"], result["bytes"]), (0, 1, 0))
        self.assertEqual(list((self.root / "resources" / "jobs" / job_id / "files").glob("*")), [])

    def test_cancel_is_checked_between_zip_compression_chunks(self):
        source = self.root / "compress-large.bin"
        source.write_bytes(b"x" * (2 * 1024 * 1024))
        original_open = Path.open
        first_read = threading.Event()
        release_read = threading.Event()

        class PausedReader:
            def __init__(self, stream):
                self.stream = stream
                self.paused = False

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.stream.close()

            def read(self, size):
                block = self.stream.read(size)
                if block and not self.paused:
                    self.paused = True
                    first_read.set()
                    release_read.wait(2)
                return block

        def pause_during_archive(path, mode="r", *args, **kwargs):
            stream = original_open(path, mode, *args, **kwargs)
            if path.parent.name == "files" and mode == "rb":
                return PausedReader(stream)
            return stream

        with patch.object(resource_module.Path, "open", new=pause_during_archive):
            self.files["compress-large"] = source
            job_id = self.manager.start([self.item("compress-large")])["id"]
            self.assertTrue(first_read.wait(1))
            self.manager.cancel(job_id)
            release_read.set()
            result = self.wait(job_id)

        self.assertEqual(result["state"], "cancelled")
        self.assertIsNone(self.manager.download_path(job_id))
        self.assertFalse((self.root / "resources" / "jobs" / job_id).exists())

    def wait_manager(self, manager, job_id):
        deadline = time.monotonic() + 3
        state = manager.status(job_id)
        while state["state"] not in ("completed", "failed", "cancelled") and time.monotonic() < deadline:
            time.sleep(0.01)
            state = manager.status(job_id)
        return state

    def test_validates_bounds_and_deduplicates_requests(self):
        with self.assertRaises(ValueError):
            self.manager.start([])
        with self.assertRaises(ValueError):
            self.manager.start([self.item()] * (MAX_ITEMS + 1))
        with self.assertRaises(ValueError):
            self.manager.start([{**self.item(), "path": "C:/private/file.png"}])
        with self.assertRaises(ValueError):
            self.manager.start([{**self.item(), "platform": "telegram"}])
        image_tier = {**self.item(), "image_tier": "original"}
        normalized_tier = {"resource_kind": "message", **self.item(), "resource_index": None,
                           "image_tier": "original"}
        self.assertEqual(self.manager._validate_items([image_tier]), [normalized_tier])
        with self.assertRaises(ValueError):
            self.manager._validate_items([{**self.item(), "image_tier": "download"}])
        with self.assertRaises(ValueError):
            self.manager._validate_items([{**self.item(), "platform": "wecom", "image_tier": "mid"}])
        with self.assertRaises(ValueError):
            self.manager._validate_items([{**self.item(), "image_tier": "mid", "resource_index": 0}])
        source = self.root / "duplicate.txt"
        source.write_text("safe", encoding="utf-8")
        self.files["message-1"] = source
        started = self.manager.start([self.item(), self.item()])
        result = self.wait(started["id"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["exported"], 1)

    def test_qq_bulk_export_accepts_provider_supported_resource_index_100(self):
        source = self.root / "qq-resource-100.bin"
        source.write_bytes(b"synthetic QQ attachment")
        item = {"platform": "qq", "account_id": "synthetic", "message_id": "message-qq",
                "conversation_id": "room", "resource_index": 100}
        manager = ResourceJobManager(self.root, lambda _request: (source, source.name))
        try:
            self.assertEqual(manager._validate_items([item])[0]["resource_index"], 100)
            result = self.wait_manager(manager, manager.start([item])["id"])
            self.assertEqual((result["state"], result["exported"]), ("completed", 1))
        finally:
            manager.close()
        with self.assertRaisesRegex(ValueError, "附件序号无效"):
            self.manager._validate_items([{**item, "resource_index": 101}])

    def test_paused_manager_rejects_new_jobs_until_resumed(self):
        self.manager.pause()
        with self.assertRaisesRegex(ValueError, "正在清理"):
            self.manager.start([self.item()])
        self.manager.resume()
        self.assertEqual(self.manager.start([self.item()])["total"], 1)

    def test_startup_removes_only_orphaned_job_directories(self):
        jobs_root = self.root / "resources" / "jobs"
        stale = jobs_root / ("a" * 20)
        stale.mkdir()
        (stale / "attachments.zip").write_bytes(b"old export")
        unrelated = jobs_root / "keep-this"
        unrelated.mkdir()
        replacement = ResourceJobManager(self.root, self.resolve)
        try:
            self.assertFalse(stale.exists())
            self.assertTrue(unrelated.is_dir())
        finally:
            replacement.close()

    def test_completed_export_retention_is_bounded_by_archive_bytes(self):
        jobs = []
        for index, payload in enumerate((b"old1", b"old2", b"new!"), 1):
            job_id = f"{index:020x}"
            work = self.manager.root / job_id
            work.mkdir()
            (work / "attachments.zip").write_bytes(payload)
            self.manager._jobs[job_id] = {
                "id": job_id, "state": "completed", "created_at": index,
                "cancel": threading.Event(), "thread": None,
            }
            jobs.append(job_id)

        with patch.object(resource_module, "MAX_RETAINED_ARCHIVE_BYTES", 5):
            self.manager._prune_completed_jobs(jobs[-1])

        self.assertNotIn(jobs[0], self.manager._jobs)
        self.assertNotIn(jobs[1], self.manager._jobs)
        self.assertIn(jobs[-1], self.manager._jobs)
        self.assertFalse((self.manager.root / jobs[0]).exists())
        self.assertTrue((self.manager.root / jobs[-1] / "attachments.zip").is_file())

    def test_rejects_a_second_active_export(self):
        started_resolve = threading.Event()
        release = threading.Event()
        source = self.root / "concurrent.bin"
        source.write_bytes(b"fixture")

        def blocked_resolver(_request):
            started_resolve.set()
            release.wait(2)
            return source, source.name

        self.manager.resolver = blocked_resolver
        first = self.manager.start([self.item("first")])
        self.assertTrue(started_resolve.wait(1))
        with self.assertRaisesRegex(ValueError, "已有资源导出任务"):
            self.manager.start([self.item("second")])
        release.set()
        self.assertEqual(self.wait(first["id"])["state"], "completed")

    def test_terminal_metadata_is_bounded_without_evicting_active_or_current(self):
        records = []
        for index, state in enumerate(("completed", "failed", "cancelled", "running"), 1):
            job_id = f"{index:020x}"
            work = self.manager.root / job_id
            work.mkdir()
            if state == "completed":
                (work / "attachments.zip").write_bytes(b"old")
            self.manager._jobs[job_id] = {
                "id": job_id, "state": state, "created_at": index,
                "cancel": threading.Event(), "thread": None,
            }
            records.append(job_id)

        with patch.object(resource_module, "MAX_TERMINAL_RECORDS", 1):
            self.manager._prune_terminal_records(records[2])

        self.assertNotIn(records[0], self.manager._jobs)
        self.assertNotIn(records[1], self.manager._jobs)
        self.assertIn(records[2], self.manager._jobs)
        self.assertIn(records[3], self.manager._jobs)
        self.assertFalse((self.manager.root / records[0]).exists())

    def test_zip_over_retention_limit_fails_before_download_publication(self):
        source = self.root / "oversized-archive.bin"
        source.write_bytes(b"fixture")
        self.files["too-large"] = source
        with patch.object(resource_module, "MAX_RETAINED_ARCHIVE_BYTES", 1):
            result = self.wait(self.manager.start([self.item("too-large")])["id"])
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"], "OverflowError")
        self.assertIsNone(self.manager.download_path(result["id"]))
        self.assertFalse((self.manager.root / result["id"]).exists())


if __name__ == "__main__":
    unittest.main()
