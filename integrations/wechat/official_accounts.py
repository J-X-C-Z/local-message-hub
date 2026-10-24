"""Bounded, read-only extraction of cached official-account article cards."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

from .provider import CONFIG, RUNTIME, LocalReader
from .vendor.db import WeChatDB

MAX_LIMIT = 500
MAX_XML_BYTES = 2 * 1024 * 1024
_GH_ID = re.compile(r"^gh_[0-9a-f]{12}$", re.I)
_URL_KEYS = {"__biz", "mid", "idx", "sn"}
_CACHE_LOCK = threading.Lock()


def _tag(node):
    return node.tag.rsplit("}", 1)[-1].casefold() if isinstance(node.tag, str) else ""


def _text(node, *names):
    accepted = {name.casefold() for name in names}
    for child in node.iter():
        if child is node or _tag(child) not in accepted:
            continue
        if child.text and child.text.strip():
            return unescape(child.text.strip())
    return ""


def _article_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        return ""
    try:
        parsed = urlsplit(unescape(value.strip()))
        if (parsed.scheme not in ("http", "https") or parsed.hostname != "mp.weixin.qq.com" or
                parsed.username or parsed.password or parsed.port not in (None, 80, 443) or
                not (parsed.path == "/s" or parsed.path.startswith("/s/"))):
            return ""
        query = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=False)
                 if key.casefold() in _URL_KEYS and len(val) <= 512]
        if parsed.path == "/s" and not {key for key, _ in query} >= {"__biz", "mid", "sn"}:
            return ""
        return urlunsplit(("https", "mp.weixin.qq.com", parsed.path, urlencode(query), ""))
    except ValueError:
        return ""


def _date(value):
    value = (value or "").strip()
    if not value:
        return ""
    try:
        if re.fullmatch(r"\d{10,13}", value):
            stamp = int(value)
            if len(value) == 13:
                stamp //= 1000
            return datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return ""


def _received_at(value):
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _parse_articles(payload):
    if isinstance(payload, bytes):
        if len(payload) > MAX_XML_BYTES:
            return [], 1
        payload = WeChatDB._friendly_content(payload, "文件/链接/卡片")
    if not isinstance(payload, str) or len(payload.encode("utf-8", errors="replace")) > MAX_XML_BYTES:
        return [], 1
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", payload, re.I):
        return [], 1
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return [], 1

    items = [node for node in root.iter() if _tag(node) == "item"]
    candidates = items or [root]
    result = []
    for node in candidates:
        title = _text(node, "title")
        url = _article_url(_text(node, "url", "shorturl", "short_url"))
        if not title or not url:
            continue
        summary = _text(node, "digest", "des", "description")
        published = _date(_text(node, "pub_time", "publish_time", "publishtime") or
                          _text(root, "pub_time", "publish_time", "publishtime"))
        result.append({"title": title[:500], "url": url, "published_at": published,
                       "summary": summary[:2000], "content_status": "summary_only"})
    return result, 0 if result else 1


def read_official_articles(reader, contact_id, limit=500, cursor=None):
    """Read only one verified gh_ identity from biz_message shards."""
    result = {"items": [], "total": 0, "invalid": 0, "next_cursor": dict(cursor or {}), "has_more": False}
    incremental = cursor is not None
    if incremental and (not isinstance(cursor, dict) or any(
            not isinstance(k, str) or not isinstance(v, list) or len(v) != 2 or
            any(type(n) is not int or n < 0 for n in v) for k, v in cursor.items())):
        raise ValueError('公众号增量游标无效')
    if not isinstance(contact_id, str) or not _GH_ID.fullmatch(contact_id):
        result["invalid"] = 1
        result["warning"] = "invalid_contact"
        return result
    try:
        limit = min(MAX_LIMIT, max(0, int(limit)))
    except (TypeError, ValueError, OverflowError):
        limit = MAX_LIMIT
    if limit == 0:
        return result

    conn = reader._contact_conn()
    if conn is None:
        result["invalid"] = 1
        result["warning"] = "contact_unavailable"
        return result
    try:
        columns = {row[1].casefold(): row[1] for row in conn.execute('PRAGMA table_info("contact")')}
        user_col, type_col = columns.get("username"), columns.get("local_type")
        if not user_col or not type_col:
            result["invalid"] = 1
            result["warning"] = "contact_unavailable"
            return result
        row = conn.execute(
            f'SELECT "{type_col}" FROM "contact" WHERE "{user_col}"=? LIMIT 1',
            (contact_id,)).fetchone()
        if row is None or row[0] != 1:
            result["invalid"] = 1
            result["warning"] = "contact_unverified"
            return result
    finally:
        conn.close()

    table = "Msg_" + hashlib.md5(contact_id.encode("utf-8")).hexdigest()
    rows = []
    for rel, path, _size in getattr(reader, "_db_files", ()):
        if not Path(path).name.startswith("biz_message_") or rel not in getattr(reader, "_keys", {}):
            continue
        db = reader._open(rel)
        try:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not exists:
                continue
            columns = {row[1].casefold(): row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
            required = {"local_id", "server_id", "local_type", "create_time", "message_content", "compress_content"}
            if not required <= set(columns):
                result["invalid"] += 1
                continue
            q = lambda name: '"' + columns[name] + '"'
            after = (cursor or {}).get(rel, [0, 0])
            predicate = (f' WHERE ({q("create_time")}>? OR ({q("create_time") }=? AND {q("local_id")}>?))' if incremental else '')
            direction = 'ASC' if incremental else 'DESC'
            query = (f'SELECT {q("local_id")},{q("server_id")},{q("local_type")},'
                     f'{q("create_time")},{q("message_content")},{q("compress_content")} '
                     f'FROM "{table}"{predicate} ORDER BY {q("create_time")} {direction},{q("local_id")} {direction} LIMIT ?')
            shard_rows = db.execute(query, (*([after[0], after[0], after[1]] if incremental else []), limit + 1)).fetchall()
            result['has_more'] |= len(shard_rows) > limit
            selected = shard_rows[:limit]
            if incremental and selected:
                result['next_cursor'][rel] = [int(selected[-1][3]), int(selected[-1][0])]
            rows.extend(selected)
        finally:
            db.close()

    rows.sort(key=lambda row: (row[3] or 0, row[0] or 0), reverse=True)
    seen = set()
    for local_id, server_id, local_type, create_time, content, compressed in (rows if incremental else rows[:limit]):
        payload = content
        if not payload or (isinstance(payload, str) and not payload.strip()):
            payload = compressed
        articles, invalid = _parse_articles(payload)
        result["invalid"] += invalid
        for article in articles:
            if article["url"] in seen:
                continue
            seen.add(article["url"])
            article.update({"source_message_id": str(server_id or local_id),
                            "received_at": _received_at(create_time),
                            "timestamp_kind": "received_at" if create_time else "unknown"})
            result["items"].append(article)
            if not incremental and len(result["items"]) >= limit:
                break
        if not incremental and len(result["items"]) >= limit:
            break
    result["total"] = len(result["items"])
    return result


def get_cached_official_articles(account_id, contact_id, limit=500, cursor=None):
    with _CACHE_LOCK:
        return _get_cached_official_articles(account_id, contact_id, limit, cursor)


def _get_cached_official_articles(account_id, contact_id, limit=500, cursor=None):
    """Create a reader from already cached keys without extracting or saving keys."""
    if (not isinstance(account_id, str) or not account_id or
            not isinstance(contact_id, str) or not _GH_ID.fullmatch(contact_id)):
        return {"items": [], "total": 0, "invalid": 1}
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        configured_account = config.get("account")
        if not isinstance(configured_account, str) or re.sub(r"_\w{4}$", "", configured_account) != account_id:
            return {"items": [], "total": 0, "invalid": 1, "warning": "account_mismatch"}
        reader = LocalReader.__new__(LocalReader)
        reader.db_dir = config["data_dir"]
        reader.account = config["account"]
        reader.account_dir = os.path.join(reader.db_dir, reader.account)
        reader.workdir = str(RUNTIME / "cache")
        reader.keys_file = str(CONFIG)
        reader._keys = {key: bytes.fromhex(value)
                        for key, value in config.get("database_keys", {}).items()}
        reader._db_files = WeChatDB._collect_db_files(reader)
        reader._build_locks = {}
        reader._build_locks_guard = threading.Lock()
        return read_official_articles(reader, contact_id, limit, cursor=cursor)
    except (OSError, ValueError, KeyError, TypeError):
        return {"items": [], "total": 0, "invalid": 1, "warning": "cached_reader_unavailable"}
