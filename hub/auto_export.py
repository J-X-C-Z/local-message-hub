"""Restart-aware local scheduled exports from the normalized Hub index."""

from __future__ import annotations

import os
import ctypes
from pathlib import Path
import secrets
import threading
import tempfile
import time

from .exports import resolve_chatlab_conversation_type, write_export
from .store import ChatStore


INTERVALS = (1, 3, 6, 12, 24, 168)
FORMATS = {
    "json": "json", "csv": "csv", "md": "md", "txt": "txt", "html": "html",
    "xlsx": "xlsx", "sql": "sql", "chatlab": "json", "chatlab-jsonl": "jsonl",
}
MAX_MESSAGES = 250_000
PAGE_SIZE = 500


def _is_mapped_network_drive(anchor):
    return (os.name == "nt" and bool(anchor) and
            ctypes.windll.kernel32.GetDriveTypeW(str(anchor)) == 4)


class AutoExportManager:
    def __init__(self, store, storage, settings, persist, enabled):
        self.store = store
        self.storage = Path(storage).resolve()
        self.settings = settings
        self.persist = persist
        self.enabled = enabled
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._cancel = None
        self._scheduler = None
        self._worker = None
        self._running = False
        self._state = "idle"
        self._last_error = ""

        default_dir = self.storage / "exports"
        config = self.settings.get("auto_export_config")
        if not isinstance(config, dict):
            config = {}
        self.config = {
            "enabled": False,
            "interval_hours": 24,
            "format": "json",
            "platform": "all",
            "output_dir": str(default_dir),
            "next_run_at": None,
            "last_run_at": None,
            "last_status": "never",
            "last_file": "",
            **config,
        }
        try:
            self._validate(self.config, create_dir=False)
        except (TypeError, ValueError):
            self.config = {
                "enabled": False, "interval_hours": 24, "format": "json", "platform": "all",
                "output_dir": str(default_dir), "next_run_at": None, "last_run_at": None,
                "last_status": "never", "last_file": "",
            }
        self.settings["auto_export_config"] = self.config

    def _validate(self, config, create_dir):
        if not isinstance(config.get("enabled"), bool):
            raise ValueError("自动导出开关无效")
        try:
            interval = int(config.get("interval_hours"))
        except (TypeError, ValueError):
            raise ValueError("无效的自动导出间隔") from None
        if interval not in INTERVALS:
            raise ValueError("自动导出间隔必须为 1、3、6、12、24 或 168 小时")
        format_id = str(config.get("format") or "")
        if format_id not in FORMATS:
            raise ValueError("不支持的自动导出格式")
        platform = str(config.get("platform") or "")
        if platform not in ("all", "wechat", "wecom", "qq", "telegram", "email"):
            raise ValueError("未知导出平台")
        raw_dir = str(config.get("output_dir") or "").strip()
        path = Path(raw_dir).expanduser()
        if not raw_dir or not path.is_absolute():
            raise ValueError("请选择本机绝对导出目录")
        if raw_dir.startswith(("\\\\", "//")):
            raise ValueError("自动导出仅支持本机磁盘目录")
        resolved_path = path.resolve()
        resolved_dir = str(resolved_path)
        if resolved_dir.startswith(("\\\\", "//")):
            raise ValueError("自动导出仅支持本机磁盘目录")
        if resolved_path.drive and _is_mapped_network_drive(resolved_path.anchor):
            raise ValueError("自动导出仅支持本机磁盘目录")
        if create_dir:
            path.mkdir(parents=True, exist_ok=True)
        if path.exists() and (not path.is_dir() or path.is_symlink()):
            raise ValueError("导出位置必须是本机目录，不能是符号链接")
        config["interval_hours"] = interval
        config["format"] = format_id
        config["platform"] = platform
        config["output_dir"] = resolved_dir

    def configure(self, changes):
        if not isinstance(changes, dict):
            raise ValueError("自动导出配置无效")
        allowed = {"enabled", "interval_hours", "format", "platform", "output_dir"}
        if not changes or set(changes) - allowed:
            raise ValueError("自动导出配置字段无效")
        with self._lock:
            updated = {**self.config, **changes}
            self._validate(updated, create_dir=True)
            if updated["enabled"] and not self.config["enabled"]:
                updated["next_run_at"] = int(time.time()) + updated["interval_hours"] * 3600
            elif updated["enabled"] and any(key in changes for key in ("interval_hours", "output_dir", "format", "platform")):
                updated["next_run_at"] = int(time.time()) + updated["interval_hours"] * 3600
            elif not updated["enabled"]:
                updated["next_run_at"] = None
            self.config = updated
            self.settings["auto_export_config"] = self.config
            self.persist(self.settings)
            if not updated["enabled"] and self._cancel:
                self._cancel.set()
            return self.status()

    def status(self):
        with self._lock:
            return {
                "config": {key: value for key, value in self.config.items()},
                "running": self._running,
                "state": self._state,
                "last_error": self._last_error,
                "max_messages": MAX_MESSAGES,
            }

    def start(self):
        with self._lock:
            if self._scheduler and self._scheduler.is_alive():
                return
            self._stop.clear()
            self._scheduler = threading.Thread(target=self._schedule, daemon=True,
                                               name="local-auto-export-scheduler")
            self._scheduler.start()

    def close(self):
        self._stop.set()
        self.cancel()
        worker = self._worker
        scheduler = self._scheduler
        if scheduler and scheduler is not threading.current_thread():
            scheduler.join(timeout=5)
        if worker and worker is not threading.current_thread():
            worker.join()

    def _schedule(self):
        while not self._stop.wait(5):
            try:
                self.run_due()
            except (OSError, ValueError, PermissionError):
                self._state = "failed"

    def run_due(self, now=None):
        now = int(time.time() if now is None else now)
        with self._lock:
            if not self.enabled() or not self.config["enabled"] or self._running:
                return False
            next_run = self.config.get("next_run_at")
            if next_run is None:
                self.config["next_run_at"] = now + self.config["interval_hours"] * 3600
                self.settings["auto_export_config"] = self.config
                self.persist(self.settings)
                return False
            due = int(next_run) <= now
            if due:
                # Keep schedule validation and worker startup atomic with configure().
                # Otherwise disabling the schedule can return in the gap before
                # run_now() marks the job active, yet the export still starts.
                self.run_now()
                return True
        return False

    def run_now(self):
        if not self.enabled():
            raise PermissionError("自动导出功能已在设置中关闭")
        with self._lock:
            if self._running:
                raise ValueError("自动导出任务已在运行")
            cancel = threading.Event()
            self._cancel = cancel
            self._running = True
            self._state = "running"
            self._last_error = ""
            self._worker = threading.Thread(target=self._run, args=(cancel,), daemon=True,
                                            name="local-auto-export")
            self._worker.start()
            return self.status()

    def cancel(self):
        with self._lock:
            if not self._running or not self._cancel:
                return self.status()
            self._cancel.set()
            self._state = "cancelling"
            return self.status()

    def _run(self, cancel):
        temp_path = None
        try:
            with self._lock:
                config = dict(self.config)
            self._validate(config, create_dir=True)
            output_dir = Path(config["output_dir"])
            with tempfile.TemporaryDirectory(prefix="lmh-auto-export-") as snapshot_dir:
                snapshot_path = Path(snapshot_dir) / "snapshot.sqlite3"
                self.store.backup_to(snapshot_path)
                snapshot = ChatStore(snapshot_path)
                try:
                    first = snapshot.export_page(config["platform"], limit=PAGE_SIZE)
                    total = first["total"]
                    if total > MAX_MESSAGES:
                        raise ValueError(f"索引超过单次自动导出的 {MAX_MESSAGES} 条上限")
                    conversation_type = None
                    if config["format"] in {"chatlab", "chatlab-jsonl"}:
                        conversation_type = resolve_chatlab_conversation_type(
                            snapshot.export_conversation_kinds(config["platform"])
                        )

                    def message_rows():
                        page = first
                        after = None
                        while page["items"]:
                            if cancel.is_set() or self._stop.is_set():
                                raise InterruptedError("cancelled")
                            yield from page["items"]
                            after = page.get("next_after")
                            if not after:
                                return
                            page = snapshot.export_page(config["platform"], after=after,
                                                        limit=PAGE_SIZE)

                    def member_rows():
                        for member in snapshot.iter_export_members(config["platform"], PAGE_SIZE):
                            if cancel.is_set() or self._stop.is_set():
                                raise InterruptedError("cancelled")
                            yield member

                    if cancel.is_set() or self._stop.is_set():
                        raise InterruptedError("cancelled")
                    output_dir.mkdir(parents=True, exist_ok=True)
                    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                    name = f"lmh-auto-{config['platform']}-{stamp}-{secrets.token_hex(3)}.{FORMATS[config['format']]}"
                    target = output_dir / name
                    temp_path = output_dir / ("." + name + "." + secrets.token_hex(4) + ".part")
                    with temp_path.open("xb") as stream:
                        write_export(
                            message_rows(), stream, config["format"], "本地消息档案", count=total,
                            source_platforms=snapshot.export_platforms(config["platform"]),
                            members=member_rows(),
                            conversation_type=conversation_type,
                        )
                        stream.flush()
                        os.fsync(stream.fileno())
                finally:
                    snapshot.close()
            if cancel.is_set() or self._stop.is_set():
                raise InterruptedError("cancelled")
            with self._lock:
                if cancel.is_set() or self._stop.is_set():
                    raise InterruptedError("cancelled")
                # Serialize cancellation with publication: after this commit point,
                # cancel() observes a completed job instead of claiming it cancelled.
                os.replace(temp_path, target)
                temp_path = None
                self._finish("completed", last_file=name, error="")
                self._running = False
                self._cancel = None
        except InterruptedError:
            self._finish("cancelled", last_file="", error="")
        except Exception as exc:
            # Persist only a sanitized error type; messages can contain paths or data.
            self._finish("failed", last_file="", error=type(exc).__name__)
        finally:
            if temp_path:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            with self._lock:
                self._running = False
                self._cancel = None

    def _finish(self, status, last_file, error):
        with self._lock:
            now = int(time.time())
            self.config["last_run_at"] = now
            self.config["last_status"] = status
            self.config["last_file"] = last_file
            self.config["next_run_at"] = (now + self.config["interval_hours"] * 3600
                                           if self.config["enabled"] else None)
            self.settings["auto_export_config"] = self.config
            self._state = status
            self._last_error = error
            self.persist(self.settings)
