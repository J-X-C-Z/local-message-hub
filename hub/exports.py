"""Export rendering for normalised chat messages.

Only the Python standard library is used.  ``render_export`` turns an iterable
of message dictionaries (as returned by :class:`hub.store.ChatStore`) into a
byte payload plus a MIME type and a download filename.

Supported formats: ``json``, ``csv``, ``md``, ``txt`` and ``html``.  All human
readable dates are rendered in UTC+08:00.  CSV output is hardened against
spreadsheet formula injection and HTML output escapes every user supplied value
and is fully self-contained (inline CSS, no remote resources).
"""

from __future__ import annotations

import csv
import html
import io
import json
import zipfile
from xml.sax.saxutils import escape as xml_escape
from datetime import datetime, timedelta, timezone

__all__ = ["render_contact_export", "render_group_member_export", "render_export", "write_export",
           "message_resource_requests", "FORMATS"]

CST = timezone(timedelta(hours=8))

#: Canonical order of columns used by the delimited/document renderers.
_COLUMNS = (
    ("timestamp", "时间"),
    ("platform", "平台"),
    ("account_id", "账号"),
    ("conversation_title", "会话"),
    ("sender_name", "发送者"),
    ("sender_id", "发送者ID"),
    ("type", "类型"),
    ("text", "内容"),
    ("media_url", "媒体链接"),
    ("file_name", "文件名"),
    ("resources", "附件"),
    ("conversation_id", "会话ID"),
    ("id", "消息ID"),
)

#: Values that trigger formula-injection escaping in CSV cells.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

_FORMAT_META = {
    "json": ("application/json", "export.json"),
    "csv": ("text/csv; charset=utf-8", "export.csv"),
    "md": ("text/markdown; charset=utf-8", "export.md"),
    "txt": ("text/plain; charset=utf-8", "export.txt"),
    "html": ("text/html; charset=utf-8", "export.html"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "export.xlsx"),
    "sql": ("application/sql; charset=utf-8", "export.sql"),
    "chatlab": ("application/json; charset=utf-8", "export.chatlab.json"),
    "chatlab-jsonl": ("application/x-ndjson; charset=utf-8", "export.chatlab.jsonl"),
}

_CONTACT_FIELDS = (
    ("id", "联系人ID"),
    ("platform", "平台"),
    ("account_id", "账号"),
    ("name", "名称"),
    ("kind", "类型"),
    ("identity_id", "统一身份"),
    ("extra", "其他资料"),
)

_CONTACT_EXTRA_FIELDS = frozenset({
    "alias", "is_self", "last_time", "member_count", "message_count",
    "nickname", "remark", "uin", "conversation_count", "official_account_kind",
})

_GROUP_MEMBER_FIELDS = (
    ("platform", "平台"), ("account_id", "账号"), ("conversation_id", "群聊ID"),
    ("conversation_title", "群聊名称"), ("id", "成员ID"), ("name", "名称"),
    ("nickname", "昵称"), ("remark", "备注"), ("role", "角色"),
    ("is_owner", "是否群主"), ("join_time", "加入时间"),
)

FORMATS = tuple(_FORMAT_META)


def _format_time(value) -> str:
    """Render a Unix timestamp in UTC+08:00, or an empty string when absent."""
    if value is None or value == "":
        return ""
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return str(value)
    if ts == 0:
        return ""
    return datetime.fromtimestamp(ts, CST).strftime("%Y-%m-%d %H:%M:%S")


def _cell(message: dict, key: str) -> str:
    if key == "resources":
        return json.dumps(_message_resources(message), ensure_ascii=False, separators=(",", ":"))
    value = message.get(key)
    if value is None:
        return ""
    if key == "timestamp":
        return _format_time(value)
    if key == "is_self":
        return "1" if value else "0"
    return str(value)


def _message_resources(message):
    """Project stable attachment metadata without cache paths or ephemeral URLs."""
    extra = message.get("extra") if isinstance(message.get("extra"), dict) else {}
    resources = []
    emitted = {}
    for key in ("attachments", "roaming_resources", "media", "resources"):
        aliases = {}
        for source in (extra, message):
            items = source.get(key)
            if not isinstance(items, list):
                continue
            source_counts = {}
            source_resources = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                resource = {}
                for target, fields in (
                    ("name", ("name", "n", "file_name", "fileName")),
                    ("type", ("type", "kind", "k")),
                    ("size", ("size", "file_size", "fileSize", "sz")),
                    ("md5", ("md5", "md5HexStr", "fileMd5")),
                    ("width", ("width", "picWidth")),
                    ("height", ("height", "picHeight")),
                    ("duration", ("duration",)),
                ):
                    value = next((item[field] for field in fields if item.get(field) is not None), None)
                    if isinstance(value, (str, int, float)) and (not isinstance(value, str) or len(value) <= 2048):
                        resource[target] = value
                if isinstance(item.get("available"), bool):
                    resource["available"] = item["available"]
                if resource:
                    identity = json.dumps(resource, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":"))
                    source_counts[identity] = source_counts.get(identity, 0) + 1
                    source_resources[identity] = resource
            for identity, count in source_counts.items():
                if count > aliases.get(identity, (0,))[0]:
                    aliases[identity] = (count, source_resources[identity])

        for identity, (count, resource) in aliases.items():
            # The extra and top-level forms may mirror each other; keep the
            # larger multiplicity so genuinely repeated attachments survive.
            missing = count - emitted.get(identity, 0)
            resources.extend(dict(resource) for _ in range(max(0, missing)))
            emitted[identity] = max(emitted.get(identity, 0), count)
    return resources


def message_resource_requests(message):
    """Return resolver identities for attachments stored on one indexed message."""
    if not isinstance(message, dict):
        return []
    platform = str(message.get("platform") or "")
    account_id = str(message.get("account_id") or "")
    message_id = str(message.get("id") or "")
    conversation_id = str(message.get("conversation_id") or "")
    if platform not in {"wechat", "wecom", "qq"} or not all(
            (account_id, message_id, conversation_id)):
        return []

    extra = message.get("extra") if isinstance(message.get("extra"), dict) else {}
    resource_field = {"wecom": "attachments", "qq": "roaming_resources"}.get(platform)
    entries = extra.get(resource_field) if resource_field else None
    if not isinstance(entries, list) and resource_field:
        entries = message.get(resource_field)
    if platform == "qq" and not isinstance(entries, list):
        entries = extra.get("media")
    if platform == "qq" and not isinstance(entries, list):
        entries = message.get("media")
    if isinstance(entries, list) and entries:
        return [{"platform": platform, "account_id": account_id,
                 "message_id": message_id, "conversation_id": conversation_id,
                 "resource_index": index}
                for index, item in enumerate(entries) if isinstance(item, dict)]

    message_type = str(message.get("type") or "").lower()
    raw_type = str(message.get("raw_type") or "")
    if message_type in {"image", "video", "voice", "file", "sticker"} or (
            platform == "wechat" and raw_type in {"47", "11000"}):
        return [{"platform": platform, "account_id": account_id,
                 "message_id": message_id, "conversation_id": conversation_id}]
    return []


def _csv_safe(value: str) -> str:
    """Prefix values that could be interpreted as spreadsheet formulas."""
    if value and (value[0] in _FORMULA_PREFIXES
                  or value.lstrip(" \t\r\n\ufeff")[:1] in ("=", "+", "-", "@")):
        return "'" + value
    return value


def _contact_export_extra(value):
    """Export only normalized profile scalars, never raw provider payloads."""
    if not isinstance(value, dict):
        return {}
    safe = {}
    for key in _CONTACT_EXTRA_FIELDS:
        item = value.get(key)
        if not isinstance(item, (str, int, float, bool)):
            continue
        if key == "official_account_kind" and item not in ("subscription", "service", "enterprise"):
            continue
        if isinstance(item, str):
            lowered = item.casefold()
            if (len(item) > 2048 or "://" in lowered
                    or lowered.startswith(("data:", "file:", "blob:"))
                    or any(marker in lowered for marker in (
                        "token=", "access_token", "authorization=", "password=",
                        "cookie=", "secret=", "ticket=",
                    ))):
                continue
        safe[key] = item
    return {key: safe[key] for key in sorted(safe)}


def render_contact_export(contacts, format="csv"):
    """Render local contact records without ephemeral avatar/media URLs."""
    normalized_format = str(format or "").strip().lower()
    if normalized_format not in ("csv", "json"):
        raise ValueError(f"Unsupported contact export format: {format!r}")

    records = []
    for contact in contacts:
        record = {key: contact.get(key) for key, _ in _CONTACT_FIELDS}
        record["extra"] = _contact_export_extra(record.get("extra"))
        records.append(record)

    if normalized_format == "json":
        payload = json.dumps(records, ensure_ascii=False, indent=2).encode("utf-8")
        return payload, "application/json; charset=utf-8", "contacts.json"

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow([label for _, label in _CONTACT_FIELDS])
    for record in records:
        values = []
        for key, _ in _CONTACT_FIELDS:
            value = record.get(key)
            if key == "extra":
                value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            elif value is None:
                value = ""
            values.append(_csv_safe(str(value)))
        writer.writerow(values)
    return output.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", "contacts.csv"


def render_group_member_export(members, conversation, format="csv"):
    """Render a bounded local roster with validated conversation provenance."""
    normalized_format = str(format or "").strip().lower()
    if normalized_format not in ("csv", "json"):
        raise ValueError(f"Unsupported group member export format: {format!r}")
    provenance = {
        "platform": str(conversation.get("platform") or ""),
        "account_id": str(conversation.get("account_id") or ""),
        "conversation_id": str(conversation.get("id") or ""),
        "conversation_title": str(conversation.get("title") or ""),
    }
    records = []
    for member in members:
        record = dict(provenance)
        for key, _ in _GROUP_MEMBER_FIELDS[4:]:
            value = member.get(key)
            if key == "is_owner":
                value = bool(value or member.get("role") == "owner")
            elif value is not None and not isinstance(value, (str, int, float, bool)):
                value = str(value)
            record[key] = value
        records.append(record)
    if normalized_format == "json":
        payload = json.dumps(records, ensure_ascii=False, indent=2).encode("utf-8")
        return payload, "application/json; charset=utf-8", "group-members.json"

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow([label for _, label in _GROUP_MEMBER_FIELDS])
    for record in records:
        writer.writerow([_csv_safe("" if record.get(key) is None else str(record[key]))
                         for key, _ in _GROUP_MEMBER_FIELDS])
    return output.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", "group-members.csv"


def _render_json(messages, title):
    output = io.BytesIO()
    output.write(b"[")
    separator = b"\n"
    for message in messages:
        record = dict(message)
        record["timestamp_text"] = _format_time(message.get("timestamp"))
        resources = _message_resources(message)
        for key in ("attachments", "roaming_resources", "media", "resources"):
            record.pop(key, None)
        if resources:
            record["resources"] = resources
        extra = record.get("extra")
        if isinstance(extra, dict):
            record["extra"] = {key: value for key, value in extra.items()
                                if key not in {"attachments", "roaming_resources", "media", "resources"}}
        output.write(separator)
        rendered = json.dumps(record, ensure_ascii=False, indent=2)
        output.write(("  " + rendered.replace("\n", "\n  ")).encode("utf-8"))
        separator = b",\n"
    output.write(b"\n]" if separator == b",\n" else b"]")
    return output.getvalue()


def _encode_lines(lines, separator="\n", trailing=False):
    output = io.BytesIO()
    first = True
    delimiter = separator.encode("utf-8")
    for line in lines:
        if not first:
            output.write(delimiter)
        output.write(line.encode("utf-8"))
        first = False
    if trailing:
        output.write(delimiter)
    return output.getvalue()


def _render_csv(messages, title):
    buffer = io.BytesIO()
    text = io.TextIOWrapper(buffer, encoding="utf-8", newline="", write_through=True)
    writer = csv.writer(text, lineterminator="\r\n")
    writer.writerow([header for _, header in _COLUMNS])
    for message in messages:
        writer.writerow([_csv_safe(_cell(message, key)) for key, _ in _COLUMNS])
    text.flush()
    payload = buffer.getvalue()
    text.detach()
    return payload


def _render_md(messages, title):
    return _encode_lines(_md_lines(messages, title))


def _md_lines(messages, title):
    yield f"# {title}"
    yield ""
    for message in messages:
        sender = message.get("sender_name") or message.get("sender_id") or "未知"
        platform = message.get("platform") or ""
        stamp = _format_time(message.get("timestamp"))
        meta = " · ".join(part for part in (
            platform,
            f"账号: {message.get('account_id')}" if message.get("account_id") is not None else "",
            stamp,
            f"会话ID: {message.get('conversation_id')}" if message.get("conversation_id") is not None else "",
            f"消息ID: {message.get('id')}" if message.get("id") is not None else "",
        ) if part)
        header = f"**{sender}**"
        if meta:
            header += f" _({meta})_"
        text = message.get("text") or ""
        if not text:
            text = message.get("file_name") or message.get("media_url") or ""
        resource_names = [resource["name"] for resource in _message_resources(message) if resource.get("name")]
        if resource_names:
            text = "\n".join(filter(None, (text, "附件: " + ", ".join(resource_names))))
        yield header
        if text:
            yield f": {text}"
        yield ""


def _render_txt(messages, title):
    return _encode_lines(_txt_lines(messages, title))


def _txt_lines(messages, title):
    yield title
    yield ""
    for message in messages:
        sender = message.get("sender_name") or message.get("sender_id") or "未知"
        platform = message.get("platform") or ""
        stamp = _format_time(message.get("timestamp"))
        text = message.get("text") or ""
        if not text:
            text = message.get("file_name") or message.get("media_url") or ""
        resource_names = [resource["name"] for resource in _message_resources(message) if resource.get("name")]
        if resource_names:
            text = "\n".join(filter(None, (text, "附件: " + ", ".join(resource_names))))
        prefix = " · ".join(part for part in (
            stamp, platform,
            f"账号: {message.get('account_id')}" if message.get("account_id") is not None else "",
            sender,
            f"会话ID: {message.get('conversation_id')}" if message.get("conversation_id") is not None else "",
            f"消息ID: {message.get('id')}" if message.get("id") is not None else "",
        ) if part)
        yield f"[{prefix}] {text}" if prefix else text


def _render_html(messages, title):
    headers = "".join(f"<th>{html.escape(label)}</th>" for _, label in _COLUMNS)
    document = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
        margin: 2rem; color: #1f2933; }}
h1 {{ font-size: 1.4rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
th, td {{ border: 1px solid #d2d6dc; padding: 0.4rem 0.6rem; text-align: left;
          vertical-align: top; word-break: break-word; }}
th {{ background: #f5f7fa; }}
tr:nth-child(even) td {{ background: #fafbfc; }}
</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
<table>
<thead><tr>{headers}</tr></thead>
<tbody>
__MESSAGE_ROWS__
</tbody>
</table>
</body>
</html>
"""
    prefix, suffix = document.rsplit("__MESSAGE_ROWS__", 1)
    output = io.BytesIO()
    output.write(prefix.encode("utf-8"))
    for message in messages:
        cells = (html.escape(_cell(message, key), quote=True) for key, _ in _COLUMNS)
        row = "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"
        output.write(row.encode("utf-8"))
    output.write(suffix.encode("utf-8"))
    return output.getvalue()


def _sql_string(value):
    if value is None:
        return "NULL"
    return "'" + str(value).replace("\x00", "").replace("'", "''") + "'"


def _render_sql(messages, title):
    return _encode_lines(_sql_lines(messages), trailing=True)


def _sql_lines(messages):
    columns = (
        "platform", "account_id", "conversation_id", "conversation_title",
        "message_id", "timestamp", "sender_id", "sender_name", "type", "text",
        "is_self", "media_url", "file_name", "raw_type",
    )
    yield from (
        "-- Local Message Hub export; source messages are not modified.",
        "BEGIN;",
        "CREATE TABLE IF NOT EXISTS local_message_hub_messages (",
        "  platform TEXT NOT NULL, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL,",
        "  conversation_title TEXT, message_id TEXT, timestamp BIGINT, sender_id TEXT,",
        "  sender_name TEXT, type TEXT, text TEXT, is_self BOOLEAN, media_url TEXT,",
        "  file_name TEXT, raw_type TEXT",
        ");",
        "CREATE INDEX IF NOT EXISTS idx_lmh_messages_scope_time ON local_message_hub_messages (platform, account_id, conversation_id, timestamp);",
        "CREATE TABLE IF NOT EXISTS local_message_hub_resources (platform TEXT NOT NULL, account_id TEXT NOT NULL, conversation_id TEXT NOT NULL, message_id TEXT NOT NULL, resource_index INTEGER NOT NULL, resource_json TEXT NOT NULL);",
    )
    for message in messages:
        values = []
        for key in columns:
            value = message.get("id") if key == "message_id" else message.get(key)
            if key == "timestamp":
                try:
                    values.append(str(int(value)) if value not in (None, "") else "NULL")
                except (TypeError, ValueError):
                    values.append("NULL")
            elif key == "is_self":
                values.append("TRUE" if value else "FALSE")
            else:
                values.append(_sql_string(value))
        yield (
            "INSERT INTO local_message_hub_messages (" + ", ".join(columns) + ") VALUES ("
            + ", ".join(values) + ");"
        )
        for index, resource in enumerate(_message_resources(message)):
            values = [message.get("platform"), message.get("account_id"), message.get("conversation_id"),
                      message.get("id"), index, json.dumps(resource, ensure_ascii=False, separators=(",", ":"))]
            yield ("INSERT INTO local_message_hub_resources (platform, account_id, conversation_id, message_id, "
                   "resource_index, resource_json) VALUES (" + ", ".join(_sql_string(value) for value in values)
                   + ");")
    yield "COMMIT;"


_CHATLAB_TYPES = {
    "text": 0, "image": 1, "voice": 2, "video": 3, "file": 4,
    "emoji": 5, "link": 7, "location": 8, "system": 80,
}


def resolve_chatlab_conversation_type(kinds):
    kinds = list(kinds)
    if not kinds:
        return "private"
    normalized = {str(kind or "").lower() for kind in kinds}
    if not normalized <= {"group", "private", "direct"}:
        raise ValueError("ChatLab cannot represent unsupported conversation types")
    has_group = "group" in normalized
    has_private = bool(normalized & {"private", "direct"})
    if has_group and has_private:
        raise ValueError("ChatLab cannot represent mixed group and private conversations")
    return "group" if has_group else "private"


def _chatlab_conversation_type(messages):
    return resolve_chatlab_conversation_type(
        message.get("conversation_kind") for message in messages
    )


def _chatlab_header(messages, title):
    members = {}
    platforms = set()
    for message in messages:
        sender_id = str(message.get("sender_id") or "")
        sender_name = str(message.get("sender_name") or sender_id or "未知")
        platform = str(message.get("platform") or "")
        account_id = str(message.get("account_id") or "")
        platforms.add(platform)
        member_key = (platform, account_id, sender_id)
        if sender_id and member_key not in members:
            members[member_key] = {
                "platformId": sender_id,
                "accountName": sender_name,
                "platform": platform,
                "accountId": account_id,
            }
    return {
        "chatlab": {
            "version": "0.0.2",
            "exportedAt": int(datetime.now(timezone.utc).timestamp()),
            "generator": "Local Message Hub",
        },
        "meta": {
            "name": title,
            "platform": "im",
            "type": _chatlab_conversation_type(messages),
            "sourcePlatforms": sorted(platforms),
        },
        "members": list(members.values()),
    }


def _chatlab_message(message):
    sender_id = str(message.get("sender_id") or "")
    sender_name = str(message.get("sender_name") or sender_id or "未知")
    try:
        timestamp = int(message.get("timestamp") or 0)
    except (TypeError, ValueError):
        timestamp = 0
    record = {
        "sender": sender_id or sender_name,
        "accountName": sender_name,
        "timestamp": timestamp,
        "type": _CHATLAB_TYPES.get(str(message.get("type") or "").lower(), 99),
        "content": message.get("text") or message.get("file_name") or message.get("media_url") or None,
        "platform": str(message.get("platform") or ""),
        "accountId": str(message.get("account_id") or ""),
        "conversationId": str(message.get("conversation_id") or ""),
    }
    if message.get("id") is not None:
        record["platformMessageId"] = str(message["id"])
    if message.get("media_url"):
        record["mediaUrl"] = str(message["media_url"])
    resources = _message_resources(message)
    if resources:
        record["resources"] = resources
    return record


def _render_chatlab(messages, title):
    header = json.dumps(_chatlab_header(messages, title), ensure_ascii=False, indent=2)
    output = io.BytesIO()
    output.write((header[:-2] + ',\n  "messages": [').encode("utf-8"))
    separator = "\n"
    for message in messages:
        record = json.dumps(_chatlab_message(message), ensure_ascii=False, indent=2)
        output.write((separator + "    " + record.replace("\n", "\n    ")).encode("utf-8"))
        separator = ",\n"
    output.write(b"\n  ]\n}" if separator == ",\n" else b"]\n}")
    return output.getvalue()


def _render_chatlab_jsonl(messages, title):
    records = _chatlab_header(messages, title)
    def lines():
        yield json.dumps({"_type": "header", "chatlab": records["chatlab"], "meta": records["meta"]}, ensure_ascii=False)
        for member in records["members"]:
            yield json.dumps({"_type": "member", **member}, ensure_ascii=False)
        for message in messages:
            yield json.dumps({"_type": "message", **_chatlab_message(message)}, ensure_ascii=False)
    return _encode_lines(lines(), trailing=True)


def _xml_safe(value):
    text = str(value)
    text = "".join(
        char for char in text
        if char in "\t\n\r" or 0x20 <= ord(char) <= 0xD7FF
        or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF
    )
    return xml_escape(text)


def _excel_column(number):
    label = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        label = chr(65 + remainder) + label
    return label


def _render_xlsx(messages, title):
    def rows():
        yield [title]
        yield ["导出工具", "Local Message Hub"]
        yield ["消息数", str(len(messages))]
        yield [header for _, header in _COLUMNS]
        for message in messages:
            yield [_cell(message, key) for key, _ in _COLUMNS]
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="聊天记录" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
        with archive.open("xl/worksheets/sheet1.xml", "w") as sheet:
            sheet.write(b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>')
            for row_index, values in enumerate(rows(), 1):
                cells = []
                for column_index, value in enumerate(values, 1):
                    reference = f"{_excel_column(column_index)}{row_index}"
                    cells.append(f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{_xml_safe(value)}</t></is></c>')
                sheet.write((f'<row r="{row_index}">{"".join(cells)}</row>').encode("utf-8"))
            sheet.write(b'</sheetData></worksheet>')
    return buffer.getvalue()


_RENDERERS = {
    "json": _render_json,
    "csv": _render_csv,
    "md": _render_md,
    "txt": _render_txt,
    "html": _render_html,
    "xlsx": _render_xlsx,
    "sql": _render_sql,
    "chatlab": _render_chatlab,
    "chatlab-jsonl": _render_chatlab_jsonl,
}


def write_export(messages, stream, format, title="Chat export", *, count=0,
                 source_platforms=(), members=(), conversation_type=None):
    """Write an export incrementally to a binary stream without collecting messages."""
    if not isinstance(format, str):
        raise ValueError(f"unknown export format: {format!r}")
    key = format.strip().lower().lstrip(".")
    if key in {"excel", "xls"}:
        key = "xlsx"
    if key not in _RENDERERS:
        raise ValueError(f"unknown export format: {format!r}; expected one of {FORMATS}")

    if key == "json":
        stream.write(b"[")
        separator = b"\n"
        for message in messages:
            record = dict(message)
            record["timestamp_text"] = _format_time(message.get("timestamp"))
            resources = _message_resources(message)
            for field in ("attachments", "roaming_resources", "media", "resources"):
                record.pop(field, None)
            if resources:
                record["resources"] = resources
            extra = record.get("extra")
            if isinstance(extra, dict):
                record["extra"] = {k: v for k, v in extra.items()
                                    if k not in {"attachments", "roaming_resources", "media", "resources"}}
            rendered = json.dumps(record, ensure_ascii=False, indent=2)
            stream.write(separator + ("  " + rendered.replace("\n", "\n  ")).encode("utf-8"))
            separator = b",\n"
        stream.write(b"\n]" if separator == b",\n" else b"]")
        return

    if key == "csv":
        text = io.TextIOWrapper(stream, encoding="utf-8", newline="", write_through=True)
        try:
            writer = csv.writer(text, lineterminator="\r\n")
            writer.writerow([header for _, header in _COLUMNS])
            for message in messages:
                writer.writerow([_csv_safe(_cell(message, field)) for field, _ in _COLUMNS])
            text.flush()
        finally:
            text.detach()
        return

    if key in {"md", "txt"}:
        lines = _md_lines(messages, title) if key == "md" else _txt_lines(messages, title)
        first = True
        for line in lines:
            if not first:
                stream.write(b"\n")
            stream.write(line.encode("utf-8"))
            first = False
        return

    if key == "html":
        headers = "".join(f"<th>{html.escape(label)}</th>" for _, label in _COLUMNS)
        document = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
        margin: 2rem; color: #1f2933; }}
h1 {{ font-size: 1.4rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; }}
th, td {{ border: 1px solid #d2d6dc; padding: 0.4rem 0.6rem; text-align: left;
          vertical-align: top; word-break: break-word; }}
th {{ background: #f5f7fa; }}
tr:nth-child(even) td {{ background: #fafbfc; }}
</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
<table>
<thead><tr>{headers}</tr></thead>
<tbody>
__MESSAGE_ROWS__
</tbody>
</table>
</body>
</html>
"""
        prefix, suffix = document.rsplit("__MESSAGE_ROWS__", 1)
        stream.write(prefix.encode("utf-8"))
        for message in messages:
            cells = (html.escape(_cell(message, field), quote=True) for field, _ in _COLUMNS)
            stream.write(("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>").encode("utf-8"))
        stream.write(suffix.encode("utf-8"))
        return

    if key == "sql":
        for index, line in enumerate(_sql_lines(messages)):
            if index:
                stream.write(b"\n")
            stream.write(line.encode("utf-8"))
        stream.write(b"\n")
        return

    if key in {"chatlab", "chatlab-jsonl"}:
        exported_at = int(datetime.now(timezone.utc).timestamp())
        chatlab = {"version": "0.0.2", "exportedAt": exported_at,
                   "generator": "Local Message Hub"}
        if conversation_type is None:
            conversation_type = (_chatlab_conversation_type(messages)
                                 if isinstance(messages, (list, tuple)) else "private")
        if conversation_type not in {"group", "private"}:
            raise ValueError("ChatLab export requires one conversation type")
        meta = {"name": title, "platform": "im", "type": conversation_type,
                "sourcePlatforms": list(source_platforms)}
        if key == "chatlab-jsonl":
            stream.write(json.dumps({"_type": "header", "chatlab": chatlab, "meta": meta},
                                    ensure_ascii=False).encode("utf-8") + b"\n")
            for member in members:
                stream.write(json.dumps({"_type": "member", **member}, ensure_ascii=False).encode("utf-8") + b"\n")
            for message in messages:
                stream.write(json.dumps({"_type": "message", **_chatlab_message(message)},
                                        ensure_ascii=False).encode("utf-8") + b"\n")
            return
        stream.write(("{\n  \"chatlab\": " + json.dumps(chatlab, ensure_ascii=False, indent=2).replace("\n", "\n  ")
                      + ",\n  \"meta\": " + json.dumps(meta, ensure_ascii=False, indent=2).replace("\n", "\n  ")
                      + ",\n  \"members\": [").encode("utf-8"))
        separator = "\n"
        for member in members:
            rendered = json.dumps(member, ensure_ascii=False, indent=2)
            stream.write((separator + "    " + rendered.replace("\n", "\n    ")).encode("utf-8"))
            separator = ",\n"
        stream.write(b"\n  ],\n  \"messages\": [")
        separator = "\n"
        for message in messages:
            rendered = json.dumps(_chatlab_message(message), ensure_ascii=False, indent=2)
            stream.write((separator + "    " + rendered.replace("\n", "\n    ")).encode("utf-8"))
            separator = ",\n"
        stream.write(b"\n  ]\n}")
        return

    if key == "xlsx":
        workbook = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="聊天记录" sheetId="1" r:id="rId1"/></sheets></workbook>'
        )
        files = {
            "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
            "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
            "xl/workbook.xml": workbook,
            "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        }
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            for path, content in files.items():
                archive.writestr(path, content)
            with archive.open("xl/worksheets/sheet1.xml", "w") as sheet:
                sheet.write(b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>')
                rows = ([title], ["导出工具", "Local Message Hub"], ["消息数", str(int(count))],
                        [header for _, header in _COLUMNS])
                row_index = 0
                for values in rows:
                    row_index += 1
                    _write_xlsx_row(sheet, row_index, values)
                for message in messages:
                    row_index += 1
                    _write_xlsx_row(sheet, row_index, [_cell(message, field) for field, _ in _COLUMNS])
                sheet.write(b"</sheetData></worksheet>")
        return


def _write_xlsx_row(sheet, row_index, values):
    cells = []
    for column_index, value in enumerate(values, 1):
        reference = f"{_excel_column(column_index)}{row_index}"
        cells.append(f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{_xml_safe(value)}</t></is></c>')
    sheet.write((f'<row r="{row_index}">{"".join(cells)}</row>').encode("utf-8"))


def render_export(messages, format, title="Chat export"):
    """Render ``messages`` into ``(bytes, mime_type, filename)``.

    ``format`` is a case-insensitive extension name.  An unknown format raises
    :class:`ValueError`.
    """
    if not isinstance(format, str):
        raise ValueError(f"unknown export format: {format!r}")
    key = format.strip().lower().lstrip(".")
    if key in {"excel", "xls"}:
        key = "xlsx"
    if key not in _RENDERERS:
        raise ValueError(
            f"unknown export format: {format!r}; expected one of {FORMATS}"
        )
    messages = messages if messages is not None else ()
    if key in {"xlsx", "chatlab", "chatlab-jsonl"} and not isinstance(messages, (list, tuple)):
        messages = list(messages)
    mime, filename = _FORMAT_META[key]
    payload = _RENDERERS[key](messages, title)
    return payload, mime, filename
