"""Decode exported mail into searchable text without opening files or URLs."""

from __future__ import annotations

import hashlib
import io
import re
from datetime import timezone
from email import policy
from email.errors import MessageError
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

_MAX_PAYLOAD_BYTES = 256 * 1024 * 1024
_MAX_MESSAGE_BYTES = 64 * 1024 * 1024
_MAX_MESSAGES = 100_000
_MAX_ERRORS = 100


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "object"}:
            self.hidden += 1
        if not self.hidden and tag in {"br", "p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "object"} and self.hidden:
            self.hidden -= 1
        if not self.hidden and tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _records(payload: bytes, file_format: str):
    if file_format == "eml":
        yield payload
        return
    # MBOX escapes body lines beginning with From as >From; separators remain
    # unescaped. Read bytes in memory so importing never creates a temporary file.
    current: list[bytes] | None = None
    for line in io.BytesIO(payload):
        if line.startswith(b"From "):
            if current is not None:
                yield b"".join(current)
            current = []
        elif current is not None:
            current.append(line[1:] if line.startswith(b">From ") else line)
        elif line.strip():
            raise ValueError("MBOX 文件缺少 From 分隔行")
    if current is not None:
        yield b"".join(current)


def _stable_id(account: str, identity: str) -> str:
    return "email-" + hashlib.sha256((account + "\0" + identity).encode("utf-8")).hexdigest()


def _message_ids(value: str) -> list[str]:
    return re.findall(r"<[^<>\s]+>", value)


def _body(message) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    raw = part.get_payload(decode=True) or b""
    try:
        text = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        parser = _HTMLText()
        parser.feed(text)
        text = "".join(parser.parts)
    return text.strip()


def parse_email_export(payload: bytes, file_format: str = "eml", account_id: str = "") -> dict[str, Any]:
    """Parse EML/MBOX bytes using stdlib MIME decoding and account-scoped IDs.

    From is required; Date, Subject and recipient headers may be absent in real
    archived mail. Missing/invalid dates use zero, never the current clock.
    Attachments contribute their names only and are never saved or opened.
    """
    if len(payload) > _MAX_PAYLOAD_BYTES:
        raise ValueError("导入文件超过 256 MiB 限制")
    file_format = str(file_format).strip().lower().lstrip(".")
    if file_format not in {"eml", "mbox"}:
        raise ValueError("邮件导入仅支持 EML 或 MBOX")
    account = str(account_id).strip() or "email-export"
    messages, errors, warnings = [], [], []
    conversations: dict[str, dict[str, str]] = {}
    total = 0
    for total, raw in enumerate(_records(payload, file_format), 1):
        if total > _MAX_MESSAGES:
            raise ValueError("单个文件最多导入 100,000 封邮件")
        try:
            if len(raw) > _MAX_MESSAGE_BYTES:
                raise ValueError("单封邮件超过 64 MiB 限制")
            message = BytesParser(policy=policy.default).parsebytes(raw)
            sender = str(message.get("From", "")).strip()
            addresses = getaddresses([sender])
            sender_id = next((address for _, address in addresses if "@" in address), "")
            if not sender_id:
                raise ValueError("邮件缺少有效 From 发件人")
            sender_name = next((name for name, address in addresses if address == sender_id), "") or sender_id
            subject = str(message.get("Subject", "")).strip() or "（无主题）"
            own_ids = _message_ids(str(message.get("Message-ID", "")))
            identity = own_ids[0] if own_ids else hashlib.sha256(raw).hexdigest()
            roots = _message_ids(" ".join(str(v) for v in message.get_all("References", [])))
            replies = _message_ids(str(message.get("In-Reply-To", "")))
            conversation_id = _stable_id(account, (roots or replies or [identity])[0])
            timestamp = 0
            try:
                date = parsedate_to_datetime(str(message.get("Date", "")))
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                timestamp = int(date.timestamp())
            except (ValueError, TypeError, OverflowError, OSError):
                if len(warnings) < _MAX_ERRORS:
                    warnings.append({"row": total, "warning": "邮件日期缺失或无效，时间设为 0"})
            names = [str(part.get_filename()) for part in message.walk() if part.get_filename()]
            to = ", ".join(str(v) for v in message.get_all("To", []))
            cc = ", ".join(str(v) for v in message.get_all("Cc", []))
            text = f"主题: {subject}\n发件人: {sender}\n收件人: {to}\n抄送: {cc}\n\n{_body(message)}"
            if names:
                text += "\n\n附件: " + ", ".join(names)
            conversations.setdefault(conversation_id, {"id": conversation_id, "title": subject, "kind": "private"})
            messages.append({
                "platform": "email", "account_id": account,
                "id": _stable_id(account, identity), "conversation_id": conversation_id,
                "sender_id": sender_id, "sender_name": sender_name, "timestamp": timestamp,
                "type": "text", "text": text, "is_self": sender_id.casefold() == account.casefold(),
                "media_url": "", "file_name": "", "raw_type": "email", "preview": "",
            })
        except (ValueError, TypeError, OverflowError, RecursionError, MessageError) as exc:
            if len(errors) < _MAX_ERRORS:
                errors.append({"row": total, "error": str(exc)[:240]})
    return {"total": total, "valid": len(messages), "invalid": total - len(messages),
            "errors": errors, "warnings": warnings, "conversations": list(conversations.values()), "messages": messages}
