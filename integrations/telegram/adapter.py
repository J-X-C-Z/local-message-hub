"""Read-only parser for Telegram Desktop JSON exports.

The parser only decodes JSON bytes and returns normalized metadata. It never
opens attachment paths or executes content embedded in exported records.
"""

from __future__ import annotations

import json
import ntpath
import posixpath
import re
from datetime import datetime, timezone, timedelta
from pathlib import PurePosixPath
from typing import Any


_LOCAL_TZ = timezone(timedelta(hours=8))
_MAX_PAYLOAD_BYTES = 256 * 1024 * 1024
_MAX_MESSAGES = 1_000_000
_MAX_ERRORS = 100

_MEDIA_FIELDS = ("photo", "file", "video", "audio", "voice_message", "sticker")
_MEDIA_TYPES = {
    "photo": "image",
    "video_file": "video",
    "video_message": "video",
    "audio_file": "voice",
    "voice_message": "voice",
    "sticker": "image",
    "file": "file",
}


def _timestamp(message: dict[str, Any]) -> int:
    value = message.get("date_unixtime")
    if value is not None and str(value).strip():
        stamp = int(float(value))
        return stamp // 1000 if abs(stamp) >= 100_000_000_000 else stamp

    value = message.get("date")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("消息缺少有效日期")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_LOCAL_TZ)
    return int(parsed.timestamp())


def _text(value: Any) -> str:
    """Flatten Telegram's text/entity arrays without interpreting entities."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _is_outgoing(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes"}
    return value is True or (isinstance(value, (int, float)) and value == 1)


def _safe_relative_path(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ntpath.isabs(value) or "\x00" in raw:
        return ""
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return ""
    # Reject drive-like and URI-like first segments, even when not absolute.
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", parts[0]):
        return ""
    normalized = posixpath.normpath(raw)
    if normalized in ("", ".") or normalized.startswith("../"):
        return ""
    return PurePosixPath(normalized).as_posix()


def _attachment(message: dict[str, Any]) -> tuple[str, str, str]:
    declared_type = str(message.get("media_type") or "").casefold()
    for field in _MEDIA_FIELDS:
        candidate = message.get(field)
        if isinstance(candidate, str) and candidate.strip():
            path = _safe_relative_path(candidate)
            if path:
                name = path.rsplit("/", 1)[-1]
                return _MEDIA_TYPES.get(declared_type, _MEDIA_TYPES.get(field, "file")), path, name
            return _MEDIA_TYPES.get(declared_type, _MEDIA_TYPES.get(field, "file")), "", ""
    return "", "", ""


def parse_telegram_export(payload: bytes, account_id: str = "") -> dict[str, Any]:
    """Parse one Telegram Desktop chat export into normalized hub records.

    ``account_id`` should be supplied by the caller where available. Telegram's
    personal-information section is deliberately not copied into the result.
    Invalid messages are isolated and reported; unsafe attachment paths are
    omitted with a warning while preserving the message itself.
    """
    if len(payload) > _MAX_PAYLOAD_BYTES:
        raise ValueError("导入文件超过 256 MiB 限制")
    try:
        document = json.loads(payload.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ValueError("文件必须使用 UTF-8 编码") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 格式错误（第 {exc.lineno} 行）") from exc
    if not isinstance(document, dict) or not isinstance(document.get("messages"), list):
        raise ValueError("Telegram 导出须为包含 messages 数组的会话 JSON")

    chat_id = str(document.get("id") or "").strip()
    chat_title = str(document.get("name") or chat_id).strip()
    if not chat_id:
        raise ValueError("Telegram 导出缺少会话 ID")
    if not chat_title:
        chat_title = chat_id
    chat_type = str(document.get("type") or "").casefold()
    conversation = {
        "id": chat_id,
        "title": chat_title,
        "kind": "group" if "group" in chat_type or "supergroup" in chat_type else "private",
    }
    account = str(account_id).strip()
    if not account:
        personal = document.get("personal_information")
        account = str(personal.get("user_id") or "telegram-export") if isinstance(personal, dict) else "telegram-export"

    messages: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    rows = document["messages"]
    if len(rows) > _MAX_MESSAGES:
        raise ValueError("单个文件最多导入 1,000,000 条消息")

    for index, source in enumerate(rows, 1):
        try:
            if not isinstance(source, dict):
                raise ValueError("记录必须是对象")
            message_id = str(source.get("id") or "").strip()
            if not message_id:
                raise ValueError("消息缺少 ID")
            timestamp = _timestamp(source)
            sender_id = str(source.get("from_id") or "").strip()
            sender_name = str(source.get("from") or "").strip()
            raw_type = str(source.get("type") or "message").strip()
            kind, media_path, file_name = _attachment(source)
            if not kind:
                kind = "system" if raw_type == "service" else "text"
            elif kind == "image" and "sticker" in source:
                raw_type = "sticker"
            text = _text(source.get("text"))
            if raw_type == "service" and not text:
                text = str(source.get("action") or "")
            if any(isinstance(source.get(field), str) and source.get(field).strip()
                   for field in _MEDIA_FIELDS) and not media_path:
                if len(warnings) < _MAX_ERRORS:
                    warnings.append({"row": index, "warning": "不安全或无效的附件路径已忽略"})
            messages.append({
                "platform": "telegram",
                "account_id": account,
                "id": f"telegram-{chat_id}-{message_id}",
                "conversation_id": chat_id,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "timestamp": timestamp,
                "type": kind,
                "text": text,
                "is_self": _is_outgoing(source.get("is_outgoing")),
                "media_url": media_path,
                "file_name": file_name,
                "raw_type": raw_type,
                "preview": "",
            })
        except (ValueError, TypeError, OverflowError) as exc:
            if len(errors) < _MAX_ERRORS:
                errors.append({"row": index, "error": str(exc)[:240]})

    return {
        "total": len(rows),
        "valid": len(messages),
        "invalid": len(rows) - len(messages),
        "errors": errors,
        "warnings": warnings,
        "conversations": [conversation],
        "messages": messages,
    }
