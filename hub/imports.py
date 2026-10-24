"""Validated import adapters for Local Message Hub JSON and CSV exports."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone, timedelta

from .store import PLATFORMS

_LOCAL_TZ = timezone(timedelta(hours=8))
_ALIASES = {
    "timestamp": ("timestamp", "时间", "time", "timestamp_text"),
    "platform": ("platform", "平台"),
    "account_id": ("account_id", "账号", "account"),
    "conversation_id": ("conversation_id", "会话ID", "chat_id"),
    "conversation_title": ("conversation_title", "会话", "conversation", "chat_name"),
    "sender_id": ("sender_id", "发送者ID"),
    "sender_name": ("sender_name", "发送者", "sender"),
    "id": ("id", "message_id", "消息ID"),
    "type": ("type", "类型", "message_type"),
    "text": ("text", "内容", "content", "message"),
    "is_self": ("is_self", "自己发送"),
    "media_url": ("media_url", "媒体链接"),
    "file_name": ("file_name", "文件名"),
    "raw_type": ("raw_type",),
    "preview": ("preview",),
}
_ALIAS_LOOKUP = {alias: key for key, aliases in _ALIASES.items() for alias in aliases}


def _timestamp(value):
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)) or str(value).strip().lstrip("-").isdigit():
        stamp = int(float(value))
        return stamp // 1000 if abs(stamp) >= 100_000_000_000 else stamp
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_LOCAL_TZ)
    return int(parsed.timestamp())


def _canonical_row(row):
    return {_ALIAS_LOOKUP.get(str(key).strip(), str(key).strip()): value for key, value in row.items()}


def parse_import(payload: bytes, file_format: str, default_platform: str, default_account: str = ""):
    """Normalize records, isolating invalid rows; no file content is executed."""
    if default_platform not in (*PLATFORMS, "all"):
        raise ValueError("请选择有效平台")
    if len(payload) > 256 * 1024 * 1024:
        raise ValueError("导入文件超过 256 MiB 限制")
    if file_format in ("eml", "mbox"):
        if default_platform not in ("email", "all"):
            raise ValueError("邮件导入必须选择邮件或全部平台")
        from integrations.email.adapter import parse_email_export
        return parse_email_export(payload, file_format, account_id=default_account)
    if file_format == "telegram":
        if default_platform not in ("telegram", "all"):
            raise ValueError("Telegram Desktop 导出必须选择 Telegram 平台")
        from integrations.telegram.adapter import parse_telegram_export
        return parse_telegram_export(payload, account_id=default_account)
    try:
        if file_format == "json":
            document = json.loads(payload.decode("utf-8-sig"))
            rows = document if isinstance(document, list) else document.get("messages", document.get("items")) if isinstance(document, dict) else None
            if not isinstance(rows, list):
                raise ValueError("JSON 须为消息数组，或包含 messages/items 数组")
        elif file_format == "csv":
            reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""))
            if not reader.fieldnames:
                raise ValueError("CSV 缺少表头")
            rows = reader
        else:
            raise ValueError("仅支持 JSON 或 CSV")
    except UnicodeDecodeError as exc:
        raise ValueError("文件必须使用 UTF-8 编码") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 格式错误（第 {exc.lineno} 行）") from exc
    except csv.Error as exc:
        raise ValueError("CSV 格式错误") from exc

    conversations, messages, errors = {}, [], []
    total = 0
    for index, source in enumerate(rows, 1):
        total += 1
        if total > 1_000_000:
            raise ValueError("单个文件最多导入 1,000,000 条消息")
        try:
            if not isinstance(source, dict):
                raise ValueError("记录必须是对象")
            row = _canonical_row(source)
            platform = row.get("platform") or default_platform
            if platform == "all":
                raise ValueError("记录缺少平台字段")
            if platform not in PLATFORMS:
                raise ValueError("未知平台")
            account = str(row.get("account_id") or default_account).strip()
            if not account:
                raise ValueError("账号标识不能为空")
            title = str(row.get("conversation_title") or "").strip()
            conversation_id = str(row.get("conversation_id") or "").strip()
            if not conversation_id:
                conversation_id = "import-" + hashlib.sha256(title.encode("utf-8")).hexdigest()[:20] if title else ""
            if not conversation_id:
                raise ValueError("会话 ID 或会话名称不能为空")
            text = row.get("text")
            if text is not None and not isinstance(text, str):
                text = str(text)
            if len(text or "") > 10_000_000:
                raise ValueError("单条消息内容超过 10 MiB")
            stamp = _timestamp(row.get("timestamp"))
            message_id = str(row.get("id") or "").strip()
            if not message_id:
                stable = json.dumps([platform, account, conversation_id, stamp, row.get("sender_id"), row.get("sender_name"), text, row.get("type")], ensure_ascii=False, separators=(",", ":"))
                message_id = "import-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()
            conversations.setdefault((platform, account, conversation_id), {
                "id": conversation_id, "title": title or conversation_id,
                "kind": "group" if str(row.get("type") or "").lower() == "group" else "private",
            })
            messages.append({
                "platform": platform, "account_id": account,
                "id": message_id, "conversation_id": conversation_id,
                "sender_id": str(row.get("sender_id") or ""),
                "sender_name": str(row.get("sender_name") or ""),
                "timestamp": stamp, "type": str(row.get("type") or "text"),
                "text": text or "", "is_self": str(row.get("is_self") or "").lower() in ("1", "true", "yes", "是"),
                "media_url": str(row.get("media_url") or ""),
                "file_name": str(row.get("file_name") or ""),
                "raw_type": str(row.get("raw_type") or ""),
                "preview": str(row.get("preview") or ""),
            })
        except (ValueError, TypeError, OverflowError) as exc:
            if len(errors) < 100:
                errors.append({"row": index, "error": str(exc)[:240]})
    return {
        "total": total, "valid": len(messages), "invalid": len(errors) if total <= 100 else total - len(messages),
        "errors": errors, "conversations": list(conversations.values()), "messages": messages,
    }
