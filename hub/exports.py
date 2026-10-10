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
from datetime import datetime, timedelta, timezone

__all__ = ["render_export", "FORMATS"]

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
)

#: Values that trigger formula-injection escaping in CSV cells.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

_FORMAT_META = {
    "json": ("application/json", "export.json"),
    "csv": ("text/csv; charset=utf-8", "export.csv"),
    "md": ("text/markdown; charset=utf-8", "export.md"),
    "txt": ("text/plain; charset=utf-8", "export.txt"),
    "html": ("text/html; charset=utf-8", "export.html"),
}

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
    value = message.get(key)
    if value is None:
        return ""
    if key == "timestamp":
        return _format_time(value)
    if key == "is_self":
        return "1" if value else "0"
    return str(value)


def _csv_safe(value: str) -> str:
    """Prefix values that could be interpreted as spreadsheet formulas."""
    if value and value[0] in _FORMULA_PREFIXES:
        return "'" + value
    return value


def _render_json(messages, title):
    payload = []
    for message in messages:
        record = dict(message)
        record["timestamp_text"] = _format_time(message.get("timestamp"))
        payload.append(record)
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    return data.encode("utf-8")


def _render_csv(messages, title):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([header for _, header in _COLUMNS])
    for message in messages:
        writer.writerow([_csv_safe(_cell(message, key)) for key, _ in _COLUMNS])
    return buffer.getvalue().encode("utf-8")


def _render_md(messages, title):
    lines = [f"# {title}", ""]
    for message in messages:
        sender = message.get("sender_name") or message.get("sender_id") or "未知"
        platform = message.get("platform") or ""
        stamp = _format_time(message.get("timestamp"))
        meta = " · ".join(part for part in (platform, stamp) if part)
        header = f"**{sender}**"
        if meta:
            header += f" _({meta})_"
        text = message.get("text") or ""
        if not text:
            text = message.get("file_name") or message.get("media_url") or ""
        lines.append(header)
        if text:
            lines.append(f": {text}")
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def _render_txt(messages, title):
    lines = [title, ""]
    for message in messages:
        sender = message.get("sender_name") or message.get("sender_id") or "未知"
        platform = message.get("platform") or ""
        stamp = _format_time(message.get("timestamp"))
        text = message.get("text") or ""
        if not text:
            text = message.get("file_name") or message.get("media_url") or ""
        prefix = " · ".join(part for part in (stamp, platform, sender) if part)
        lines.append(f"[{prefix}] {text}" if prefix else text)
    return "\n".join(lines).encode("utf-8")


def _render_html(messages, title):
    rows = []
    for message in messages:
        cells = [
            html.escape(_cell(message, key), quote=True) for key, _ in _COLUMNS
        ]
        rows.append(
            "<tr>"
            + "".join(f"<td>{cell}</td>" for cell in cells)
            + "</tr>"
        )
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
{''.join(rows)}
</tbody>
</table>
</body>
</html>
"""
    return document.encode("utf-8")


_RENDERERS = {
    "json": _render_json,
    "csv": _render_csv,
    "md": _render_md,
    "txt": _render_txt,
    "html": _render_html,
}


def render_export(messages, format, title="Chat export"):
    """Render ``messages`` into ``(bytes, mime_type, filename)``.

    ``format`` is a case-insensitive extension name.  An unknown format raises
    :class:`ValueError`.
    """
    if not isinstance(format, str):
        raise ValueError(f"unknown export format: {format!r}")
    key = format.strip().lower().lstrip(".")
    if key not in _RENDERERS:
        raise ValueError(
            f"unknown export format: {format!r}; expected one of {FORMATS}"
        )
    messages = list(messages or [])
    mime, filename = _FORMAT_META[key]
    payload = _RENDERERS[key](messages, title)
    return payload, mime, filename
