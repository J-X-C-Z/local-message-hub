"""Strict, account-bound downloads for indexed WeChat file messages."""

from __future__ import annotations

import ctypes
import hashlib
import mimetypes
import os
import re
import stat
import sys
import tempfile
import threading
import time
import types
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


MAX_FILE_BYTES = 32 * 1024 * 1024
_COPY_CHUNK_SIZE = 1024 * 1024
_MD5 = re.compile(r"[a-f0-9]{32}", re.IGNORECASE)
_FILE_LOCAL_TYPES = {49, 34359738417, 103079215153, 25769803825}
_UI_LOCK = threading.Lock()


class WeChatDownloadError(RuntimeError):
    """A WeChat attachment download failed a stable, caller-visible check."""

    def __init__(self, code: str, message: str):
        self.code = str(code)
        self.safe_message = str(message)
        super().__init__(self.safe_message)


def _merged(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise WeChatDownloadError("message_invalid", "微信消息元数据无效")
    extra = row.get("extra")
    return {**(extra if isinstance(extra, dict) else {}), **row}


def _safe_name(value: Any, fallback: str = "wechat-file") -> str:
    if not isinstance(value, str):
        return fallback
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]+", "", name).strip()[:240]
    return fallback if name in ("", ".", "..") else name


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    merged = _merged(row)
    if str(merged.get("platform") or "") != "wechat":
        raise WeChatDownloadError("platform_invalid", "这不是微信消息")
    if str(merged.get("type") or "") != "file":
        raise WeChatDownloadError("attachment_not_file", "主动下载仅支持微信文件消息")
    raw_type = int(merged.get("raw_type") or 0) & 0xFFFFFFFF
    if raw_type not in _FILE_LOCAL_TYPES:
        raise WeChatDownloadError("attachment_not_file", "微信消息类型不是可下载文件")

    content = merged.get("content")
    if not isinstance(content, str) or "<" not in content:
        raise WeChatDownloadError("attachment_metadata_invalid", "微信文件消息缺少下载元数据")
    content = content[content.find("<"):].strip()
    if len(content) > 1024 * 1024 or "<!DOCTYPE" in content.upper() or "<!ENTITY" in content.upper():
        raise WeChatDownloadError("attachment_metadata_invalid", "微信文件消息元数据无效")
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise WeChatDownloadError("attachment_metadata_invalid", "微信文件消息元数据无效") from None
    app = root.find(".//appmsg")
    if app is None or (app.findtext("type") or "").strip() != "6":
        raise WeChatDownloadError("attachment_not_file", "微信消息不是普通文件附件")
    name = _safe_name(app.findtext("title") or merged.get("file_name"))
    attach = app.find(".//appattach")
    if attach is None:
        raise WeChatDownloadError("attachment_metadata_invalid", "微信文件消息缺少附件信息")
    try:
        size = int((attach.findtext("totallen") or "").strip())
    except ValueError:
        raise WeChatDownloadError("attachment_size_invalid", "微信文件大小无效") from None
    if size < 0:
        raise WeChatDownloadError("attachment_size_invalid", "微信文件大小无效")
    if size > MAX_FILE_BYTES:
        raise WeChatDownloadError("attachment_too_large", "微信文件超过 32 MiB")
    digest = (app.findtext("md5") or "").strip().lower()
    if _MD5.fullmatch(digest) is None:
        raise WeChatDownloadError("attachment_md5_invalid", "微信文件缺少有效的 MD5")
    expire_text = (root.findtext(".//extcommoninfo/media_expire_at") or "").strip()
    expires_at = int(expire_text) if expire_text.isdecimal() else 0
    if expires_at and expires_at <= int(time.time()):
        raise WeChatDownloadError("attachment_expired", "微信文件已过期，客户端可能无法补下载")
    return {"name": name, "size": size, "md5": digest, "expires_at": expires_at}


def _identity(row: dict[str, Any], resource_index: Any) -> tuple[str, str, str, str, int]:
    merged = _merged(row)
    account_id = str(merged.get("account_id") or "")
    conversation_id = str(merged.get("conversation_id") or "")
    message_id = str(merged.get("id") or merged.get("message_id") or "")
    if not account_id or not conversation_id or not message_id:
        raise WeChatDownloadError("message_identity_invalid", "微信消息身份无效")
    if resource_index is None:
        resource_index = 0
    if type(resource_index) is not int or resource_index != 0:
        raise WeChatDownloadError("attachment_index_invalid", "微信文件消息只有一个附件")
    return "wechat", account_id, conversation_id, message_id, resource_index


def _is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        checker = getattr(path, "is_junction", None)
        if callable(checker) and checker():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return bool(flag and attributes & flag)
    except OSError:
        return False


def _safe_directory(path: Path, *, root: Path | None, create: bool) -> Path:
    try:
        if root is None:
            for candidate in (path, *path.parents):
                if os.path.lexists(candidate) and _is_reparse_point(candidate):
                    raise WeChatDownloadError("cache_path_unsafe", "微信下载缓存目录不安全")
            if os.path.lexists(path):
                if not path.is_dir():
                    raise WeChatDownloadError("cache_path_unsafe", "微信下载缓存目录不安全")
                return path.resolve(strict=True)
            if create:
                path.mkdir(parents=True)
            return path.resolve(strict=create)

        if os.path.lexists(path):
            if _is_reparse_point(path) or not path.is_dir():
                raise WeChatDownloadError("cache_path_unsafe", "微信下载缓存目录不安全")
        elif create:
            path.mkdir()
        else:
            return path
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise WeChatDownloadError("cache_path_unsafe", "微信下载缓存目录越界")
        return resolved
    except WeChatDownloadError:
        raise
    except OSError:
        code = "cache_write_failed" if create else "cache_path_unsafe"
        raise WeChatDownloadError(code, "微信下载缓存目录不可用") from None


def _cache_target(
    storage: str | Path,
    identity: tuple[str, str, str, str, int],
    *,
    create: bool,
) -> Path:
    platform, account_id, conversation_id, message_id, resource_index = identity
    root = _safe_directory(Path(os.path.abspath(Path(storage).expanduser())), root=None,
                           create=create)
    if not root.is_dir():
        raise WeChatDownloadError("cache_missing", "微信下载缓存目录不存在")
    account_hash = hashlib.sha256(f"{platform}\0{account_id}".encode("utf-8")).hexdigest()
    message_key = "\0".join((platform, account_id, conversation_id, message_id,
                             str(resource_index)))
    message_hash = hashlib.sha256(message_key.encode("utf-8")).hexdigest()
    current = root
    for part in ("attachment-downloads", "wechat", account_hash, message_hash):
        current = _safe_directory(current / part, root=root, create=create)
    target = current / str(resource_index)
    if os.path.lexists(target) and (_is_reparse_point(target) or not target.is_file()):
        raise WeChatDownloadError("cache_path_unsafe", "微信下载缓存文件不安全")
    return target


def _valid_file(path: Path, size: int, digest: str) -> bool:
    try:
        if not os.path.lexists(path) or _is_reparse_point(path) or not path.is_file():
            return False
        if path.stat().st_size != size:
            return False
        actual = hashlib.md5()
        total = 0
        with path.open("rb") as stream:
            while chunk := stream.read(_COPY_CHUNK_SIZE):
                total += len(chunk)
                if total > size:
                    return False
                actual.update(chunk)
        return total == size and actual.hexdigest() == digest
    except OSError:
        return False


def cached_attachment(
    row: dict[str, Any],
    resource_index: int = 0,
    storage: str | Path = Path(".local"),
) -> dict[str, Any]:
    """Return a verified Hub-owned WeChat file after an explicit download."""
    unavailable = {"available": False, "path": None, "name": None, "mime_type": None}
    try:
        metadata = _metadata(row)
        identity = _identity(row, resource_index)
        target = _cache_target(storage, identity, create=False)
        if _valid_file(target, metadata["size"], metadata["md5"]):
            return {
                "available": True,
                "path": str(target.resolve(strict=True)),
                "name": metadata["name"],
                "mime_type": mimetypes.guess_type(metadata["name"])[0]
                or "application/octet-stream",
            }
    except (OSError, RuntimeError, TypeError, ValueError):
        pass
    return unavailable


def _copy_verified(source: Path, target: Path, size: int, digest: str) -> None:
    try:
        if (_is_reparse_point(source) or not source.is_file()
                or source.stat().st_size != size):
            raise WeChatDownloadError("download_size_mismatch", "微信下载文件大小不一致")
    except OSError:
        raise WeChatDownloadError("download_unavailable", "微信下载文件不可用") from None

    temporary: str | None = None
    actual = hashlib.md5()
    total = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=".wechat-download-",
            suffix=".part",
            delete=False,
        ) as output:
            temporary = output.name
            with source.open("rb") as stream:
                while chunk := stream.read(_COPY_CHUNK_SIZE):
                    total += len(chunk)
                    if total > size:
                        raise WeChatDownloadError(
                            "download_size_mismatch", "微信下载文件大小不一致")
                    output.write(chunk)
                    actual.update(chunk)
            if total != size:
                raise WeChatDownloadError("download_size_mismatch", "微信下载文件大小不一致")
            if actual.hexdigest() != digest:
                raise WeChatDownloadError("download_md5_mismatch", "微信下载文件 MD5 不一致")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        temporary = None
    except WeChatDownloadError:
        raise
    except OSError:
        raise WeChatDownloadError("cache_write_failed", "微信下载缓存写入失败") from None
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _source_filename(provider: Any, source_row: dict[str, Any], title: str) -> str:
    try:
        from .vendor.media import MediaDownloader

        name = MediaDownloader(provider.reader)._file_name(source_row)
    except Exception:
        name = None
    return _safe_name(name or title)


def _cache_candidates(directory: Path, names: list[str]) -> list[Path]:
    result = []
    seen = set()
    for name in names:
        path = directory / name
        if path not in seen:
            result.append(path)
            seen.add(path)
    for name in names:
        path = Path(name)
        stem, suffix = path.stem, path.suffix
        pattern = re.compile(rf"^{re.escape(stem)} \(\d+\){re.escape(suffix)}$",
                             re.IGNORECASE)
        try:
            children = list(directory.iterdir())
        except OSError:
            children = []
        for child in children:
            if child not in seen and pattern.fullmatch(child.name):
                result.append(child)
                seen.add(child)
    return result


def _find_cached_source(
    provider: Any,
    source_row: dict[str, Any],
    metadata: dict[str, Any],
    resource_name: str,
) -> Path | None:
    reader = getattr(provider, "reader", None)
    account_dir = getattr(reader, "account_dir", None)
    create_time = source_row.get("create_time")
    if not account_dir or type(create_time) is not int or create_time <= 0:
        return None
    try:
        month = time.strftime("%Y-%m", time.localtime(create_time))
    except (OverflowError, OSError, ValueError):
        return None
    base = Path(account_dir) / "msg" / "file"
    directories = [base / month]
    names = list(dict.fromkeys((resource_name, metadata["name"])))
    candidates = []
    for directory in directories:
        candidates.extend(_cache_candidates(directory, names))
    if not candidates:
        try:
            for child in base.rglob("*"):
                if child.name in names:
                    candidates.append(child)
                elif child.suffix:
                    stem = Path(names[0]).stem
                    if re.fullmatch(rf"{re.escape(stem)} \(\d+\){re.escape(child.suffix)}",
                                    child.name, re.IGNORECASE):
                        candidates.append(child)
        except OSError:
            pass
    for candidate in candidates:
        try:
            if (candidate.is_symlink() or not candidate.is_file()
                    or candidate.stat().st_size != metadata["size"]):
                continue
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(base.resolve()):
                continue
            if _valid_file(resolved, metadata["size"], metadata["md5"]):
                return resolved
        except (OSError, RuntimeError):
            continue
    return None


def _load_uia():
    try:
        import win32gui  # noqa: F401
        import win32process  # noqa: F401
        import uiautomation  # noqa: F401
    except Exception:
        raise WeChatDownloadError(
            "client_ui_dependencies_missing",
            "微信客户端下载组件未安装；请重新运行启动器安装运行依赖",
        ) from None
    upstream = Path(__file__).resolve().parent / "runtime" / "upstream"
    package = Path(upstream) / "wechatauto"
    if not (package / "uia_driver.py").is_file():
        raise WeChatDownloadError("client_ui_unavailable", "微信客户端自动化组件缺失")
    parent = sys.modules.get("wechatauto")
    if parent is None:
        parent = types.ModuleType("wechatauto")
        parent.__path__ = [str(package)]
        sys.modules["wechatauto"] = parent
    try:
        from wechatauto.uia_driver import WeChatUIA
    except Exception:
        raise WeChatDownloadError(
            "client_ui_unavailable", "微信客户端控件接口不可用") from None
    return WeChatUIA


def _visible_bubbles(uia: Any, target: str):
    message_list = uia._message_list()
    if message_list is None:
        return []
    found = []
    for child in message_list.GetChildren():
        try:
            if ((child.ClassName or "") == "mmui::ChatBubbleItemView"
                    and _bubble_name_matches(child.Name or "", target)):
                found.append(child)
        except Exception:
            continue
    return found


def _bubble_name_matches(text: str, target: str) -> bool:
    if not text or not target:
        return False
    return target in text or (len(target) > 16 and target[:16] in text)


def _find_menu_item(uia: Any, name: str):
    found = []

    def walk(control, depth=0):
        if depth > 12:
            return
        try:
            children = control.GetChildren()
        except Exception:
            return
        for child in children:
            try:
                if ((child.ClassName or "") == "mmui::XMenuView"
                        and (child.Name or "").strip() == name):
                    found.append(child)
            except Exception:
                pass
            walk(child, depth + 1)

    walk(uia._win)
    return found[0] if found else None


def _login_window_present(uia: Any, win32gui: Any, win32process: Any) -> bool:
    found = []

    def visit(hwnd, _):
        try:
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            title = win32gui.GetWindowText(hwnd)
            if not pid or title not in ("微信", "Weixin"):
                return True
            control = uia._anchor(hwnd)
            if control is not None and (control.ClassName or "") == "mmui::LoginWindow":
                found.append(hwnd)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(visit, None)
    return bool(found)


def _click_menu_item(uia: Any, control: Any) -> None:
    rect = control.BoundingRectangle
    x = (rect.left + rect.right) // 2
    y = (rect.top + rect.bottom) // 2
    uia._set_cursor(x, y)
    time.sleep(0.2)
    user32 = ctypes.windll.user32
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)


def _client_download(
    provider: Any,
    source_row: dict[str, Any],
    conversation_title: str,
    resource_name: str,
    metadata: dict[str, Any],
    wait_seconds: float,
) -> Path:
    if not conversation_title:
        raise WeChatDownloadError("conversation_title_missing", "缺少微信会话标题，请重新同步")
    if not _UI_LOCK.acquire(timeout=1):
        raise WeChatDownloadError("client_busy", "另一个微信客户端任务正在运行")
    hidden = []
    win32gui = None
    try:
        WeChatUIA = _load_uia()
        try:
            import win32gui as win32gui_module
            import win32process
        except Exception:
            raise WeChatDownloadError("client_ui_unavailable", "微信窗口组件不可用") from None
        win32gui = win32gui_module

        def collect(hwnd, _):
            try:
                if (win32process.GetWindowThreadProcessId(hwnd)[1]
                        and win32gui.GetWindowText(hwnd) in ("微信", "Weixin")
                        and not win32gui.IsWindowVisible(hwnd)):
                    hidden.append(hwnd)
            except Exception:
                pass
            return True

        win32gui.EnumWindows(collect, None)
        uia = WeChatUIA(timeout=10)
        if _login_window_present(uia, win32gui, win32process):
            raise WeChatDownloadError(
                "client_signed_out",
                "微信客户端当前已退出登录；请在本机重新登录后再下载",
            )
        if not uia.ensure_window(wake=True, timeout=12):
            raise WeChatDownloadError("client_window_unavailable", "未找到已登录的微信主窗口")
        if not uia.open_chat(conversation_title):
            raise WeChatDownloadError("conversation_unavailable", "无法在微信中唯一定位目标会话")

        candidates = list(dict.fromkeys((metadata["name"], resource_name)))
        target = None
        for candidate_index, name in enumerate(candidates):
            if candidate_index and not uia.open_chat(conversation_title):
                break
            if uia.find_in_message_list(
                    lambda cls, text, expected=name:
                    cls == "mmui::ChatBubbleItemView"
                    and _bubble_name_matches(text, expected),
                    max_scrolls=180):
                bubbles = _visible_bubbles(uia, name)
                if bubbles:
                    target = bubbles[-1]
                    break
        if target is None:
            raise WeChatDownloadError(
                "message_not_visible", "微信历史窗口中未定位到目标文件，请先打开该会话")
        uia._click_ctrl(target, right=True)
        time.sleep(0.8)
        download = _find_menu_item(uia, "下载")
        if download is None:
            raise WeChatDownloadError(
                "client_download_action_missing",
                "微信文件菜单未提供“下载”；文件可能已缓存或客户端版本不受支持",
            )
        _click_menu_item(uia, download)
        deadline = time.time() + max(20.0, float(wait_seconds))
        stable = None
        while time.time() < deadline:
            time.sleep(1)
            found = _find_cached_source(provider, source_row, metadata, resource_name)
            if found is not None:
                return found
            reader = getattr(provider, "reader", None)
            month = time.strftime("%Y-%m", time.localtime(int(source_row["create_time"])))
            directory = Path(reader.account_dir) / "msg" / "file" / month
            matches = []
            for candidate in _cache_candidates(directory, [resource_name, metadata["name"]]):
                try:
                    if candidate.is_file() and candidate.stat().st_size == metadata["size"]:
                        matches.append(candidate)
                except OSError:
                    pass
            signature = tuple(sorted((str(path), path.stat().st_size)
                                     for path in matches if path.exists()))
            if signature and signature == stable:
                found = _find_cached_source(provider, source_row, metadata, resource_name)
                if found is not None:
                    return found
            stable = signature
        raise WeChatDownloadError(
            "client_download_timeout", "微信未在限时内完成文件接收")
    finally:
        try:
            for hwnd in hidden:
                if win32gui is not None:
                    win32gui.ShowWindow(hwnd, 0)
        except Exception:
            pass
        _UI_LOCK.release()


def download_attachment(
    provider: Any,
    row: dict[str, Any],
    resource_index: int = 0,
    storage: str | Path = Path(".local"),
    conversation_title: str = "",
) -> dict[str, Any]:
    """Download one exact WeChat file, using the logged-in client when needed."""
    cached = cached_attachment(row, resource_index, storage)
    if cached["available"]:
        return cached

    metadata = _metadata(row)
    identity = _identity(row, resource_index)
    _platform, account_id, _conversation_id, _message_id, _resource_index = identity
    reader = getattr(provider, "reader", None)
    current_account = str(getattr(reader, "wxid", None) or
                          getattr(reader, "account", None) or "")
    if not reader or current_account != account_id:
        raise WeChatDownloadError("wechat_account_mismatch", "当前微信账号与消息账号不一致")
    merged = _merged(row)
    shard = str(merged.get("shard") or "")
    local_id = merged.get("local_id")
    if (type(local_id) is not int or local_id < 0 or not shard
            or Path(shard).name != shard):
        raise WeChatDownloadError("message_identity_invalid", "微信文件消息缺少分片定位信息")
    source_row = reader.get_message_row(_conversation_id, local_id, shard=shard)
    if source_row is None or int(source_row.get("local_type") or 0) not in _FILE_LOCAL_TYPES:
        raise WeChatDownloadError("message_not_found", "微信文件消息已不可用，请重新同步")
    resource_name = _source_filename(provider, source_row, metadata["name"])
    source = _find_cached_source(provider, source_row, metadata, resource_name)
    if source is None:
        source = _client_download(
            provider, source_row, conversation_title, resource_name, metadata,
            wait_seconds=max(60, min(300, metadata["size"] // (256 * 1024) + 60)),
        )
    target = _cache_target(storage, identity, create=True)
    _copy_verified(source, target, metadata["size"], metadata["md5"])
    return {
        "available": True,
        "path": str(target.resolve(strict=True)),
        "name": metadata["name"],
        "mime_type": mimetypes.guess_type(metadata["name"])[0]
        or "application/octet-stream",
    }


__all__ = [
    "MAX_FILE_BYTES",
    "WeChatDownloadError",
    "cached_attachment",
    "download_attachment",
]
