"""Bounded, cancellable exports of resolver-supported local attachments."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
import zipfile


PLATFORMS = {"wechat", "wecom", "qq"}
MAX_ITEMS = 100
MAX_ITEM_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_RETAINED_ARCHIVE_BYTES = MAX_TOTAL_BYTES + 2 * 1024 * 1024
MAX_RETAINED_JOBS = 32
MAX_TERMINAL_RECORDS = 64
_JOB_DIRECTORY = re.compile(r"^[a-f0-9]{20}$")


class ResourceJobManager:
    def __init__(self, storage, resolver):
        self.storage = Path(storage).resolve()
        resource_dir = self.storage / "resources"
        self.root = resource_dir / "jobs"
        if resource_dir.is_symlink():
            raise ValueError("资源导出目录无效")
        resource_dir.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError("资源导出目录无效")
        self.root.mkdir(exist_ok=True)
        self._remove_orphaned_jobs()
        self.resolver = resolver
        self._lock = threading.RLock()
        self._jobs = {}
        self._paused = False

    @staticmethod
    def _validate_items(items):
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
            raise ValueError(f"请选择 1 到 {MAX_ITEMS} 个附件")
        validated, seen = [], set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("附件身份参数无效")
            platform = str(item.get("platform") or "")
            account_id = str(item.get("account_id") or "")
            kind = item.get("resource_kind", "message")
            if kind == "moment":
                allowed = {"resource_kind", "platform", "account_id", "post_id", "media_index", "variant"}
                if set(item) - allowed or platform != "wechat" or not account_id:
                    raise ValueError("朋友圈附件身份参数无效")
                post_id = str(item.get("post_id") or "")
                media_index = item.get("media_index")
                variant = item.get("variant", "original")
                if not post_id or len(account_id) > 256 or len(post_id) > 512 or type(media_index) is not int or not 0 <= media_index < 50 or variant not in ("original", "live"):
                    raise ValueError("朋友圈附件身份参数无效")
                identity = (kind, account_id, post_id, media_index, variant)
                validated_item = {"resource_kind": kind, "platform": platform, "account_id": account_id,
                                  "post_id": post_id, "media_index": media_index, "variant": variant}
            elif kind == "qq_cache":
                allowed = {"resource_kind", "platform", "account_id", "resource_id"}
                resource_id = str(item.get("resource_id") or "")
                if (set(item) - allowed or platform != "qq" or not account_id
                        or not resource_id or len(resource_id) > 128):
                    raise ValueError("QQ 缓存附件身份参数无效")
                identity = (kind, account_id, resource_id)
                validated_item = {"resource_kind": kind, "platform": platform,
                                  "account_id": account_id, "resource_id": resource_id}
            elif kind == "message":
                allowed = {"resource_kind", "platform", "account_id", "message_id", "conversation_id", "resource_index", "image_tier"}
                if set(item) - allowed:
                    raise ValueError("附件身份参数无效")
                message_id = str(item.get("message_id") or "")
                conversation_id = str(item.get("conversation_id") or "")
                if platform not in PLATFORMS or not account_id or not message_id or not conversation_id:
                    raise ValueError("附件必须包含平台、账号和消息身份")
                if any(len(value) > 256 for value in (account_id, message_id, conversation_id)):
                    raise ValueError("附件身份参数过长")
                resource_index = item.get("resource_index")
                max_resource_index = 100 if platform == "qq" else 49
                if resource_index is not None and (
                    type(resource_index) is not int or not 0 <= resource_index <= max_resource_index
                ):
                    raise ValueError("附件序号无效")
                image_tier = item.get("image_tier")
                if image_tier is not None and (platform != "wechat" or resource_index is not None
                                               or image_tier not in ("best", "mid", "thumb", "original")):
                    raise ValueError("图片质量选项无效")
                identity = (kind, platform, account_id, message_id, resource_index, image_tier)
                validated_item = {"resource_kind": kind, "platform": platform, "account_id": account_id,
                                  "message_id": message_id, "conversation_id": conversation_id,
                                  "resource_index": resource_index}
                if image_tier is not None:
                    validated_item["image_tier"] = image_tier
            else:
                raise ValueError("附件身份参数无效")
            if identity in seen:
                continue
            seen.add(identity)
            validated.append(validated_item)
        if not validated:
            raise ValueError("没有可导出的附件")
        return validated

    def start(self, items):
        requests = self._validate_items(items)
        job_id = uuid.uuid4().hex[:20]
        job = {"id": job_id, "state": "queued", "total": len(requests), "completed": 0,
               "exported": 0, "failed": 0, "bytes": 0, "created_at": int(time.time()),
               "download_url": None, "error": "", "cancel": threading.Event(), "thread": None,
               "manifest": []}
        thread = threading.Thread(target=self._run, args=(job, requests), daemon=True,
                                  name="local-resource-export")
        job["thread"] = thread
        with self._lock:
            if self._paused:
                raise ValueError("本地数据正在清理；请稍后重试")
            if any(job["state"] in ("queued", "running", "cancelling")
                   for job in self._jobs.values()):
                raise ValueError("已有资源导出任务正在运行；请等待完成")
            self._jobs[job_id] = job
            thread.start()
        return self.status(job_id)

    def status(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return {key: job[key] for key in ("id", "state", "total", "completed", "exported",
                                                "failed", "bytes", "created_at", "download_url",
                                                "error") if key in job}

    def cancel(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job["state"] in ("queued", "running"):
                job["cancel"].set()
                job["state"] = "cancelling"
            return self.status(job_id)

    def download_path(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job["state"] != "completed":
                return None
            path = (self.root / job_id / "attachments.zip").resolve()
        if not path.is_relative_to(self.root) or not path.is_file() or path.is_symlink():
            return None
        return path

    def close(self):
        self.pause()

    def pause(self):
        with self._lock:
            self._paused = True
            jobs = list(self._jobs.values())
            for job in jobs:
                if job["state"] in ("queued", "running", "cancelling"):
                    job["cancel"].set()
        for job in jobs:
            thread = job.get("thread")
            if thread and thread is not threading.current_thread():
                thread.join()

    def resume(self):
        with self._lock:
            self._paused = False

    @staticmethod
    def _safe_name(value):
        name = Path(str(value or "attachment")).name
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:100]
        return name or "attachment"

    def _update(self, job, **values):
        with self._lock:
            job.update(values)

    def _remove_orphaned_jobs(self):
        for path in self.root.iterdir():
            if _JOB_DIRECTORY.fullmatch(path.name) and path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)

    def _prune_completed_jobs(self, keep_job_id):
        with self._lock:
            completed = []
            for job_id, job in self._jobs.items():
                if job["state"] != "completed":
                    continue
                archive = self.root / job_id / "attachments.zip"
                try:
                    size = archive.stat().st_size if archive.is_file() and not archive.is_symlink() else 0
                except OSError:
                    size = 0
                completed.append((job["created_at"], job_id, size))
            total = sum(size for _, _, size in completed)
            for _, job_id, size in completed:
                if (total <= MAX_RETAINED_ARCHIVE_BYTES and
                        len(completed) <= MAX_RETAINED_JOBS):
                    break
                if job_id == keep_job_id:
                    continue
                path = self.root / job_id
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path, ignore_errors=True)
                self._jobs.pop(job_id, None)
                total -= size
                completed = [entry for entry in completed if entry[1] != job_id]

    def _prune_terminal_records(self, keep_job_id):
        with self._lock:
            terminal = [(job["created_at"], job_id) for job_id, job in self._jobs.items()
                        if job["state"] in ("completed", "failed", "cancelled")]
            terminal.sort(key=lambda entry: entry[0])
            for _, job_id in terminal:
                if len(terminal) <= MAX_TERMINAL_RECORDS:
                    break
                if job_id == keep_job_id:
                    continue
                job = self._jobs.pop(job_id, None)
                if job and job["state"] == "completed":
                    path = self.root / job_id
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path, ignore_errors=True)
                terminal = [entry for entry in terminal if entry[1] != job_id]

    def _run(self, job, requests):
        job_id = job["id"]
        work = self.root / job_id
        staging = work / "files"
        archive_temp = work / ".attachments.zip.part"
        terminal_state = None
        try:
            work.mkdir(parents=True, exist_ok=False)
            staging.mkdir()
            self._update(job, state="running")
            manifest, total_bytes, sequence = [], 0, 0
            for index, request in enumerate(requests, 1):
                if job["cancel"].is_set():
                    raise InterruptedError
                entry = dict(request)
                try:
                    path, suggested_name = self.resolver(request)
                    source = Path(path)
                    if source.is_symlink() or not source.is_file():
                        raise FileNotFoundError
                    source_stat = source.stat()
                    size = source_stat.st_size
                    if size > MAX_ITEM_BYTES or total_bytes + size > MAX_TOTAL_BYTES:
                        raise OverflowError
                    if job["cancel"].is_set():
                        raise InterruptedError
                    sequence += 1
                    filename = f"{sequence:03d}-{self._safe_name(suggested_name or source.name)}"
                    target = staging / filename
                    copied = 0
                    digest = hashlib.sha256()
                    try:
                        with source.open("rb") as reader, target.open("xb") as writer:
                            opened_stat = os.fstat(reader.fileno())
                            if self._source_signature(opened_stat) != self._source_signature(source_stat):
                                raise OSError("source changed before read")
                            while True:
                                if job["cancel"].is_set():
                                    raise InterruptedError
                                block = reader.read(1024 * 1024)
                                if not block:
                                    break
                                copied += len(block)
                                if copied > MAX_ITEM_BYTES or total_bytes + copied > MAX_TOTAL_BYTES:
                                    raise OverflowError
                                writer.write(block)
                                digest.update(block)
                            if self._source_signature(os.fstat(reader.fileno())) != self._source_signature(opened_stat):
                                raise OSError("source changed during read")
                        if copied != size:
                            raise OverflowError
                    except Exception:
                        target.unlink(missing_ok=True)
                        raise
                    total_bytes += copied
                    entry.update({"status": "exported", "file": filename, "bytes": copied,
                                  "sha256": digest.hexdigest()})
                except InterruptedError:
                    raise
                except Exception as exc:
                    entry.update({"status": "failed", "error": type(exc).__name__})
                manifest.append(entry)
                exported = sum(1 for row in manifest if row["status"] == "exported")
                self._update(job, completed=index, exported=exported,
                             failed=index - exported, bytes=total_bytes, manifest=manifest)
            if job["cancel"].is_set():
                raise InterruptedError
            if sequence == 0:
                raise ValueError("no_resources")
            with zipfile.ZipFile(archive_temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(staging.iterdir()):
                    self._write_archive_file(archive, path, job["cancel"])
                archive.writestr("manifest.json", json.dumps({"items": manifest}, ensure_ascii=False, indent=2))
            if archive_temp.stat().st_size > MAX_RETAINED_ARCHIVE_BYTES:
                raise OverflowError
            with archive_temp.open("r+b") as archive:
                os.fsync(archive.fileno())
            if job["cancel"].is_set():
                raise InterruptedError
            os.replace(archive_temp, work / "attachments.zip")
            self._update(job, state="completed", download_url=f"/api/resource-jobs/{job_id}/download")
            self._prune_completed_jobs(job_id)
        except InterruptedError:
            terminal_state = "cancelled"
        except Exception as exc:
            terminal_state = "failed"
            self._update(job, error=type(exc).__name__)
        finally:
            archive_temp.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)
            if terminal_state:
                shutil.rmtree(work, ignore_errors=True)
                self._update(job, state=terminal_state)
            if terminal_state or job["state"] == "completed":
                self._prune_terminal_records(job_id)

    @staticmethod
    def _write_archive_file(archive, path, cancel):
        info = zipfile.ZipInfo("attachments/" + path.name)
        info.compress_type = zipfile.ZIP_DEFLATED
        with path.open("rb") as source, archive.open(info, "w") as target:
            while True:
                if cancel.is_set():
                    raise InterruptedError
                block = source.read(1024 * 1024)
                if not block:
                    return
                target.write(block)

    @staticmethod
    def _source_signature(stat_result):
        # shortcut: metadata catches ordinary edits; use immutable cache snapshots for hostile writers.
        return (stat_result.st_dev, stat_result.st_ino, stat_result.st_size,
                stat_result.st_mtime_ns)
