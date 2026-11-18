"""Strict, account-bound downloads for indexed QQ file attachments."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any


MAX_FILE_BYTES = 32 * 1024 * 1024
_COPY_CHUNK_SIZE = 1024 * 1024
_MD5 = re.compile(r"[a-f0-9]{32}", re.IGNORECASE)


class QQDownloadError(RuntimeError):
    """A QQ attachment download failed a stable, caller-visible check."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.safe_message = message
        super().__init__(message)


def _unavailable() -> dict[str, Any]:
    return {"available": False, "path": None, "name": None, "mime_type": None}


def _merged(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise QQDownloadError("message_invalid", "QQ 消息元数据无效")
    extra = row.get("extra")
    return {**(extra if isinstance(extra, dict) else {}), **row}


def _attachments(row: dict[str, Any]) -> list[Any]:
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    for source in (extra, row):
        # Match the WebUI/MCP resource index order.
        for key in ("roaming_resources", "media"):
            if key not in source:
                continue
            value = source[key]
            if not isinstance(value, list):
                raise QQDownloadError("attachments_invalid", "QQ 附件元数据无效")
            return value
    return []


def _index(value: Any, count: int) -> int:
    if type(value) is not int or value < 0 or value >= count:
        raise QQDownloadError("attachment_index_invalid", "QQ 附件序号无效")
    return value


def _display_name(resource: dict[str, Any], index: int) -> str:
    raw = resource.get("file_name") or resource.get("name")
    if not isinstance(raw, str):
        return f"attachment-{index}.bin"
    normalized = raw.replace("\\", "/").rsplit("/", 1)[-1]
    normalized = re.sub(r"[\x00-\x1f]", "", normalized).strip()[:240]
    return normalized or f"attachment-{index}.bin"


def _select(row: dict[str, Any], resource_index: int) -> tuple[dict[str, Any], str, str, int, str]:
    attachments = _attachments(row)
    if not attachments:
        raise QQDownloadError("attachment_not_found", "QQ 消息没有附件")
    index = _index(resource_index, len(attachments))
    resource = attachments[index]
    if not isinstance(resource, dict):
        raise QQDownloadError("attachment_invalid", "QQ 附件元数据无效")

    kinds = {
        str(resource[key]).strip().lower()
        for key in ("kind", "type")
        if resource.get(key) is not None
    }
    if kinds != {"file"}:
        raise QQDownloadError("attachment_not_file", "主动下载仅支持 QQ 文件附件")

    digest = resource.get("md5")
    if not isinstance(digest, str) or _MD5.fullmatch(digest) is None:
        raise QQDownloadError("attachment_md5_invalid", "QQ 文件附件缺少有效的 MD5")
    digest = digest.lower()

    size = resource.get("size")
    if type(size) is not int or size < 0:
        raise QQDownloadError("attachment_size_invalid", "QQ 文件附件大小无效")
    if size > MAX_FILE_BYTES:
        raise QQDownloadError("attachment_too_large", "QQ 文件附件超过 32 MiB")

    name = _display_name(resource, index)
    selected = {
        **resource,
        "kind": "file",
        "type": "file",
        "file_name": name,
        "md5": digest,
        "size": size,
    }
    mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return selected, name, mime_type, size, digest


def _identity(row: dict[str, Any], resource_index: int) -> tuple[str, str, str, str, int]:
    merged = _merged(row)
    platform = str(merged.get("platform") or "qq")
    account_id = str(merged.get("account_id") or "")
    conversation_id = str(merged.get("conversation_id") or "")
    message_id = str(merged.get("id") or merged.get("message_id") or "")
    if platform != "qq" or not account_id or not conversation_id or not message_id:
        raise QQDownloadError("message_identity_invalid", "QQ 消息身份无效")
    if type(resource_index) is not int or resource_index < 0:
        raise QQDownloadError("attachment_index_invalid", "QQ 附件序号无效")
    return platform, account_id, conversation_id, message_id, resource_index


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


def _check_existing_chain(path: Path) -> None:
    for candidate in (path, *path.parents):
        if os.path.lexists(candidate) and _is_reparse_point(candidate):
            raise QQDownloadError("cache_path_unsafe", "QQ 下载缓存目录不安全")


def _safe_directory(path: Path, *, root: Path | None, create: bool) -> Path:
    try:
        if root is None:
            _check_existing_chain(path)
            if os.path.lexists(path):
                if not path.is_dir():
                    raise QQDownloadError("cache_path_unsafe", "QQ 下载缓存目录不安全")
                return path.resolve(strict=True)
            if not create:
                return path
            path.mkdir(parents=True)
            _check_existing_chain(path)
            return path.resolve(strict=True)

        if os.path.lexists(path):
            if _is_reparse_point(path) or not path.is_dir():
                raise QQDownloadError("cache_path_unsafe", "QQ 下载缓存目录不安全")
        elif create:
            path.mkdir()
        else:
            return path
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise QQDownloadError("cache_path_unsafe", "QQ 下载缓存目录越界")
        return resolved
    except QQDownloadError:
        raise
    except OSError:
        code = "cache_write_failed" if create else "cache_path_unsafe"
        raise QQDownloadError(code, "QQ 下载缓存目录不可用") from None


def _cache_target(
    storage: str | Path,
    identity: tuple[str, str, str, str, int],
    *,
    create: bool,
) -> Path:
    platform, account_id, conversation_id, message_id, resource_index = identity
    root = Path(os.path.abspath(Path(storage).expanduser()))
    root = _safe_directory(root, root=None, create=create)
    if not root.is_dir():
        raise QQDownloadError("cache_missing", "QQ 下载缓存目录不存在")

    account_hash = hashlib.sha256(f"{platform}\0{account_id}".encode("utf-8")).hexdigest()
    message_key = "\0".join((platform, account_id, conversation_id, message_id, str(resource_index)))
    message_hash = hashlib.sha256(message_key.encode("utf-8")).hexdigest()
    current = root
    for part in ("attachment-downloads", "qq", account_hash, message_hash):
        current = _safe_directory(current / part, root=root, create=create)
    target = current / str(resource_index)
    if os.path.lexists(target) and (_is_reparse_point(target) or not target.is_file()):
        raise QQDownloadError("cache_path_unsafe", "QQ 下载缓存文件不安全")
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
    """Return a verified cached QQ file, or a non-raising miss for ordinary rows."""
    try:
        identity = _identity(row, resource_index)
        _selected, name, mime_type, size, digest = _select(_merged(row), resource_index)
        target = _cache_target(storage, identity, create=False)
        if _valid_file(target, size, digest):
            return {
                "available": True,
                "path": str(target.resolve(strict=True)),
                "name": name,
                "mime_type": mime_type,
            }
    except (QQDownloadError, OSError, RuntimeError, ValueError, TypeError):
        pass
    return _unavailable()


def _copy_verified(source: Path, target: Path, size: int, digest: str) -> None:
    try:
        if (_is_reparse_point(source) or not source.is_file()
                or source.stat().st_size != size):
            raise QQDownloadError("download_size_mismatch", "QQ 下载文件大小不一致")
    except OSError:
        raise QQDownloadError("download_unavailable", "QQ 下载文件不可用") from None

    temporary: str | None = None
    actual = hashlib.md5()
    total = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=".qq-download-",
            suffix=".part",
            delete=False,
        ) as output:
            temporary = output.name
            with source.open("rb") as stream:
                while chunk := stream.read(_COPY_CHUNK_SIZE):
                    total += len(chunk)
                    if total > size:
                        raise QQDownloadError("download_size_mismatch", "QQ 下载文件大小不一致")
                    output.write(chunk)
                    actual.update(chunk)
            if total != size:
                raise QQDownloadError("download_size_mismatch", "QQ 下载文件大小不一致")
            if actual.hexdigest() != digest:
                raise QQDownloadError("download_md5_mismatch", "QQ 下载文件 MD5 不一致")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        temporary = None
    except QQDownloadError:
        raise
    except OSError:
        raise QQDownloadError("cache_write_failed", "QQ 下载缓存写入失败") from None
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def download_attachment(
    provider: Any,
    row: dict[str, Any],
    resource_index: int = 0,
    storage: str | Path = Path(".local"),
    conversation_kind: str = "private",
) -> dict[str, Any]:
    """Download one unambiguous QQ file attachment through the local QCE bridge."""
    cached = cached_attachment(row, resource_index, storage)
    if cached["available"]:
        return cached

    merged = _merged(row)
    identity = _identity(merged, resource_index)
    selected, name, mime_type, size, digest = _select(merged, resource_index)
    _platform, account_id, _conversation_id, _message_id, _index_value = identity

    if conversation_kind not in {"private", "group"}:
        raise QQDownloadError("conversation_kind_invalid", "QQ 会话类型无效")
    try:
        status = provider.status()
    except Exception:
        raise QQDownloadError("qq_status_failed", "QQ 登录状态不可用") from None
    if not isinstance(status, dict) or status.get("online") is not True:
        raise QQDownloadError("qq_offline", "QQ 当前不在线")
    if str(status.get("account_id") or "") != account_id:
        raise QQDownloadError("qq_account_mismatch", "QQ 当前账号与消息账号不一致")

    projected = {
        **merged,
        "type": "file",
        "file_name": name,
        "media": [selected],
        "chatType": 2 if conversation_kind == "group" else 1,
    }
    try:
        result = provider.media(projected)
    except Exception:
        raise QQDownloadError("qq_download_failed", "QQ 附件下载失败") from None
    source = result.get("path") if isinstance(result, dict) else None
    if (not isinstance(result, dict) or not result.get("available")
            or not isinstance(source, str) or not source):
        raise QQDownloadError("qq_download_unavailable", "QQ 附件当前不可下载")

    target = _cache_target(storage, identity, create=True)
    _copy_verified(Path(source), target, size, digest)
    return {
        "available": True,
        "path": str(target.resolve(strict=True)),
        "name": name,
        "mime_type": mime_type,
    }


__all__ = [
    "MAX_FILE_BYTES",
    "QQDownloadError",
    "cached_attachment",
    "download_attachment",
]
