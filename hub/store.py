"""Unified SQLite index for cross-platform chat history.

This module implements :class:`ChatStore`, a small, dependency-free store built
only on the Python 3.12 standard library.  It normalises conversations,
messages and contacts coming from multiple platforms (``wechat``, ``wecom``,
``qq``) and multiple accounts into a single local SQLite database.

Design notes
------------
* Source identifiers may collide across platforms and accounts, so every table
  uses the composite primary key ``(platform, account_id, id)``.
* A single connection guarded by a re-entrant lock is shared across threads.
  This keeps ``:memory:`` databases usable while remaining safe for the HTTP
  worker threads that will read the store concurrently.
* Writes are transactional and idempotent; conversation aggregates are
  recomputed from the message table so repeated ingests never double count.
* Timestamps are integer Unix seconds.  Date bounds supplied as ISO strings are
  interpreted in UTC+08:00, with ``end`` inclusive through that whole day.
* The store only ever writes to its own file; source databases are never
  touched.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

__all__ = ["ChatStore", "PLATFORMS", "MESSAGE_TYPES", "MEDIA_TYPES"]

#: Platforms understood by the store.  ``all`` is a query scope, not a platform.
PLATFORMS = ("wechat", "wecom", "qq", "telegram")

#: Canonical message types.
MESSAGE_TYPES = ("text", "image", "video", "voice", "file", "link", "system")

#: Message types that are considered media.
MEDIA_TYPES = ("image", "video", "voice", "file")


def media_message_predicate(alias="m"):
    """SQL predicate for message rows that carry media, including indexed attachments."""
    return f"""(({alias}.type IN ('image','video','voice','file') AND NOT (
        {alias}.platform='wechat' AND COALESCE({alias}.raw_type,'') IN ('47','11000')
    )) OR (
        {alias}.platform='wecom' AND CASE WHEN json_valid({alias}.extra) THEN CASE
            WHEN json_type({alias}.extra,'$.attachments')='array'
            THEN json_array_length({alias}.extra,'$.attachments') ELSE 0 END ELSE 0 END > 0
    ) OR (
        {alias}.platform='qq' AND CASE WHEN json_valid({alias}.extra) THEN CASE
            WHEN json_type({alias}.extra,'$.roaming_resources')='array'
            THEN json_array_length({alias}.extra,'$.roaming_resources')
            WHEN json_type({alias}.extra,'$.media')='array'
            THEN json_array_length({alias}.extra,'$.media') ELSE 0 END ELSE 0 END > 0
    ))"""

#: UTC+08:00, the timezone used for all human-facing date interpretation.
CST = timezone(timedelta(hours=8))

_SCHEMA_VERSION = 9

_MOMENT_POST_FIELDS = ("id", "username", "nickname", "createTime", "contentDesc",
                       "type", "likes", "comments", "location")
_MOMENT_MEDIA_FIELDS = ("type", "mime_type", "width", "height", "size", "file_size",
                        "duration", "orientation", "local_available")

_CONVERSATION_FIELDS = (
    "id",
    "platform",
    "account_id",
    "title",
    "kind",
    "avatar_url",
    "member_count",
    "last_message",
    "last_timestamp",
    "message_count",
)

_MESSAGE_FIELDS = (
    "id",
    "platform",
    "account_id",
    "conversation_id",
    "sender_id",
    "sender_name",
    "timestamp",
    "type",
    "text",
    "is_self",
    "media_url",
    "file_name",
    "raw_type",
)

_CONTACT_FIELDS = (
    "id",
    "platform",
    "account_id",
    "name",
    "avatar_url",
    "kind",
)

# Human readable previews used when a message carries no text body.
_PREVIEW_LABELS = {
    "image": "[图片]",
    "video": "[视频]",
    "voice": "[语音]",
    "file": "[文件]",
    "link": "[链接]",
    "system": "[系统消息]",
    "text": "",
}
_PREVIEW_MARKUP_RE = re.compile(r"<[^>]{1,512}>")
_PREVIEW_CREDENTIAL_RE = re.compile(
    r"(?i)\b(?:access[_-]?token|refresh[_-]?token|authorization|password|passwd|"
    r"client[_-]?secret|session[_-]?(?:key|token)|api[_-]?key|token|secret|cookie)\b\s*[:=]"
)
_PREVIEW_URL_RE = re.compile(r"(?i)\bhttps?://[^\s<>]+")
_PREVIEW_LIMIT = 240

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_DATETIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
)


def _sid(value) -> str:
    """Coerce an identifier to the canonical TEXT form used in the schema."""
    if value is None:
        return ""
    return str(value)


def _as_int(value, default=None):
    """Best-effort integer coercion that never raises for junk input."""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def _as_bool_int(value) -> int:
    """Normalise a truthy value to 0/1 without treating the string "0" oddly."""
    if isinstance(value, str):
        return 0 if value.strip().lower() in ("", "0", "false", "no") else 1
    return 1 if value else 0


def _preview(message) -> str:
    """Return the short preview string stored on the conversation row."""
    text = (message.get("text") or "").strip()
    if text:
        text = _PREVIEW_URL_RE.sub("[链接]", text)
        if _PREVIEW_MARKUP_RE.search(text) or _PREVIEW_CREDENTIAL_RE.search(text):
            return "[结构化消息]"
        return text[:_PREVIEW_LIMIT] + ("…" if len(text) > _PREVIEW_LIMIT else "")
    mtype = (message.get("type") or "text").lower()
    return _PREVIEW_LABELS.get(mtype, "")


def _like_pattern(q: str) -> str:
    """Build a ``%...%`` LIKE pattern with ``\\`` escaping for ``%`` and ``_``."""
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fts_phrase(q: str) -> str:
    return '"' + q.replace('"', '""') + '"'


def _cursor_scope(values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _encode_cursor(timestamp, rowid, scope):
    payload = json.dumps([int(timestamp), int(rowid), scope], separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value, scope):
    if not value:
        return None
    if not isinstance(value, str) or len(value) > 128:
        raise ValueError("invalid page cursor")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        timestamp, rowid, encoded_scope = json.loads(raw)
        if (not isinstance(timestamp, int) or isinstance(timestamp, bool) or
                not isinstance(rowid, int) or isinstance(rowid, bool) or rowid < 1 or
                encoded_scope != scope):
            raise ValueError
        return timestamp, rowid
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
        raise ValueError("invalid page cursor") from exc


def parse_bound(value, end: bool = False):
    """Convert a query bound to an integer Unix timestamp.

    Accepts ``None``/empty (returns ``None``), plain integers, numeric strings
    (Unix seconds) and ISO-8601 strings.  ISO values without an explicit offset
    are interpreted in UTC+08:00.  When ``end`` is true and only a date was
    given, the bound is the last second of that day so the whole day is
    included.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"invalid date bound: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=CST)
        return int(dt.timestamp())
    if isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=CST)
        if end:
            dt = dt + timedelta(days=1) - timedelta(seconds=1)
        return int(dt.timestamp())

    s = str(value).strip()
    if not s:
        return None
    if _INT_RE.match(s):
        return int(s)

    m = _ISO_DATE_RE.match(s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        dt = datetime(y, mo, d, tzinfo=CST)
        if end:
            dt = dt + timedelta(days=1) - timedelta(seconds=1)
        return int(dt.timestamp())

    for fmt in _DATETIME_FORMATS:
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=CST)
            return int(dt.timestamp())
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"invalid date bound: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return int(dt.timestamp())


def _load_extra(raw):
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _dump_extra(source: dict):
    """Collect keys not part of the normalised model into a JSON string."""
    if not source:
        return None
    known = set(source)
    extra = {k: v for k, v in source.items() if k not in _KNOWN_KEYS}
    if not extra:
        return None
    return json.dumps(extra, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


_KNOWN_KEYS = set(_CONVERSATION_FIELDS) | set(_MESSAGE_FIELDS) | set(_CONTACT_FIELDS)
_KNOWN_KEYS.update({"preview", "extra", "conversation_title", "server_id"})


class ChatStore:
    """A thread-safe, idempotent index over normalised chat data."""

    def __init__(self, path):
        self.path = path if isinstance(path, str) else str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path,
            check_same_thread=False,
            timeout=30.0,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._fts_enabled = False
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._migrate()

    # ------------------------------------------------------------------
    # schema management
    # ------------------------------------------------------------------
    def _migrate(self) -> None:
        conn = self._conn
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < _SCHEMA_VERSION:
            conn.execute("BEGIN")
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS conversations (
                        platform TEXT NOT NULL, account_id TEXT NOT NULL, id TEXT NOT NULL,
                        title TEXT, kind TEXT, avatar_url TEXT, member_count INTEGER,
                        last_message TEXT, last_timestamp INTEGER,
                        message_count INTEGER NOT NULL DEFAULT 0, extra TEXT,
                        PRIMARY KEY (platform, account_id, id)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS messages (
                        platform TEXT NOT NULL, account_id TEXT NOT NULL, id TEXT NOT NULL,
                        conversation_id TEXT NOT NULL, sender_id TEXT, sender_name TEXT,
                        timestamp INTEGER NOT NULL DEFAULT 0, type TEXT, text TEXT,
                        is_self INTEGER NOT NULL DEFAULT 0, media_url TEXT, file_name TEXT,
                        raw_type TEXT, preview TEXT, extra TEXT,
                        PRIMARY KEY (platform, account_id, id)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS contacts (
                        platform TEXT NOT NULL, account_id TEXT NOT NULL, id TEXT NOT NULL,
                        name TEXT, avatar_url TEXT, kind TEXT, extra TEXT,
                        PRIMARY KEY (platform, account_id, id)
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_conv_ts ON messages(platform, account_id, conversation_id, timestamp)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(timestamp)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_platform_ts ON messages(platform, timestamp)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_platform_type ON messages(platform, type)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_platform_ts ON conversations(platform, last_timestamp)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_contact_platform ON contacts(platform)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_contact_name ON contacts(name)")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sync_checkpoints (
                        platform TEXT NOT NULL, account_id TEXT NOT NULL,
                        conversation_id TEXT NOT NULL, next_offset INTEGER NOT NULL,
                        cursor_json TEXT NOT NULL DEFAULT '',
                        complete INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL,
                        PRIMARY KEY (platform, account_id, conversation_id)
                    )
                """)
                checkpoint_columns = {row[1] for row in conn.execute(
                    "PRAGMA table_info(sync_checkpoints)")}
                if "cursor_json" not in checkpoint_columns:
                    conn.execute("ALTER TABLE sync_checkpoints ADD COLUMN cursor_json TEXT NOT NULL DEFAULT ''")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS import_jobs (
                        id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, filename TEXT NOT NULL,
                        staging_file TEXT NOT NULL, state TEXT NOT NULL, created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL, total INTEGER NOT NULL, valid INTEGER NOT NULL,
                        invalid INTEGER NOT NULL, processed INTEGER NOT NULL DEFAULT 0,
                        messages INTEGER NOT NULL DEFAULT 0, conversations INTEGER NOT NULL DEFAULT 0,
                        errors TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT ''
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_import_jobs_created ON import_jobs(created_at DESC)")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS mcp_operations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tool TEXT NOT NULL, status TEXT NOT NULL,
                        timestamp INTEGER NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_mcp_operations_timestamp ON mcp_operations(timestamp DESC)")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS conversation_metadata (
                        platform TEXT NOT NULL, account_id TEXT NOT NULL,
                        conversation_id TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                        tags TEXT NOT NULL DEFAULT '[]', archived INTEGER NOT NULL DEFAULT 0,
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY (platform, account_id, conversation_id)
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS contact_identities (
                        platform TEXT NOT NULL, account_id TEXT NOT NULL,
                        contact_id TEXT NOT NULL, identity_id TEXT NOT NULL,
                        updated_at INTEGER NOT NULL,
                        PRIMARY KEY (platform, account_id, contact_id)
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_contact_identity_group ON contact_identities(identity_id)")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS operation_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        action TEXT NOT NULL, entity_type TEXT NOT NULL,
                        entity_key TEXT NOT NULL, status TEXT NOT NULL,
                        details TEXT NOT NULL DEFAULT '{}', timestamp INTEGER NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_operation_log_timestamp ON operation_log(timestamp DESC)")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS moments_catalog (
                        account_id TEXT PRIMARY KEY, catalogued_at INTEGER NOT NULL,
                        post_count INTEGER NOT NULL DEFAULT 0
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS moments_posts (
                        account_id TEXT NOT NULL, id TEXT NOT NULL, timestamp INTEGER NOT NULL,
                        username TEXT NOT NULL DEFAULT '', payload TEXT NOT NULL,
                        PRIMARY KEY (account_id, id),
                        FOREIGN KEY (account_id) REFERENCES moments_catalog(account_id) ON DELETE CASCADE
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_moments_posts_page ON moments_posts(account_id, timestamp DESC, id DESC)")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS moments_media (
                        account_id TEXT NOT NULL, post_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                        payload TEXT NOT NULL, cache_locator TEXT,
                        PRIMARY KEY (account_id, post_id, ordinal),
                        FOREIGN KEY (account_id, post_id) REFERENCES moments_posts(account_id, id) ON DELETE CASCADE
                    )
                """)
                conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        self._setup_fts()

    def _setup_fts(self):
        conn = self._conn
        existed = conn.execute("SELECT 1 FROM sqlite_master WHERE name='messages_fts'").fetchone()
        try:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    text, content='messages', content_rowid='rowid', tokenize='trigram'
                )
            """)
            conn.executescript("""
                CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text);
                END;
                CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
                END;
                CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE OF text ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, text) VALUES ('delete', old.rowid, old.text);
                    INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text);
                END;
            """)
            if not existed:
                conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
            self._fts_enabled = True
        except sqlite3.OperationalError:
            self._fts_enabled = False

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _require_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("ChatStore is closed")
        return self._conn

    @staticmethod
    def _validate_platform(platform: str) -> str:
        if platform not in PLATFORMS:
            raise ValueError(
                f"unknown platform {platform!r}; expected one of {PLATFORMS}"
            )
        return platform

    @staticmethod
    def _scope_clause(platform, column="platform"):
        """Return ``(sql, params)`` restricting rows to a platform scope."""
        if platform and platform != "all":
            return f" AND {column} = ?", [platform]
        return "", []

    # -- row -> dict converters -----------------------------------------
    @staticmethod
    def _row_to_conversation(row) -> dict:
        data = {f: row[f] for f in _CONVERSATION_FIELDS}
        data["last_message"] = _preview({"text": data["last_message"]})
        if "display_avatar_url" in row.keys():
            data["avatar_url"] = row["display_avatar_url"] or data["avatar_url"]
        data["member_count"] = (
            None if row["member_count"] is None else int(row["member_count"])
        )
        data["message_count"] = int(row["message_count"] or 0)
        data["last_timestamp"] = (
            None if row["last_timestamp"] is None else int(row["last_timestamp"])
        )
        data["extra"] = _load_extra(row["extra"])
        if "note" in row.keys():
            data["note"] = row["note"] or ""
            data["tags"] = json.loads(row["tags"] or "[]")
            data["archived"] = bool(row["archived"])
        return data

    @staticmethod
    def _row_to_message(row) -> dict:
        data = {f: row[f] for f in _MESSAGE_FIELDS}
        data["timestamp"] = int(row["timestamp"] or 0)
        data["is_self"] = bool(row["is_self"])
        data["extra"] = _load_extra(row["extra"])
        data["extra"].pop("_hub_wechat_server_id", None)
        quote = data["extra"].get("wechat_quote")
        if isinstance(quote, dict):
            quote.pop("target_server_id", None)
        keys = row.keys()
        if "conversation_title" in keys:
            data["conversation_title"] = row["conversation_title"]
        if "conversation_kind" in keys:
            data["conversation_kind"] = row["conversation_kind"]
        if "conversation_avatar_url" in keys:
            data["conversation_avatar_url"] = row["conversation_avatar_url"]
        return data

    @staticmethod
    def _row_to_contact(row) -> dict:
        data = {f: row[f] for f in _CONTACT_FIELDS}
        data["extra"] = _load_extra(row["extra"])
        if "identity_id" in row.keys():
            data["identity_id"] = row["identity_id"]
        return data

    # ------------------------------------------------------------------
    # ingest
    # ------------------------------------------------------------------
    def ingest(self, platform, account_id, conversations, messages, contacts=None,
               checkpoint=None):
        """Transactionally upsert a batch of normalised records.

        Returns ``{"conversations": int, "messages": int, "contacts": int}`` with
        the number of distinct rows written for each table.
        """
        platform = self._validate_platform(platform)
        account_id = _sid(account_id)
        conversations = conversations if conversations is not None else ()
        messages = messages if messages is not None else ()
        contacts = contacts if contacts is not None else ()

        with self._lock:
            conn = self._require_connection()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conv_rows = self._dedupe(conversations, key="id")
                msg_rows = self._dedupe(messages, key="id")
                contact_rows = self._dedupe(contacts, key="id")

                for conv in conv_rows.values():
                    self._upsert_conversation(conn, platform, account_id, conv)
                for msg in msg_rows.values():
                    self._upsert_message(conn, platform, account_id, msg)
                for contact in contact_rows.values():
                    self._upsert_contact(conn, platform, account_id, contact)

                if checkpoint is not None:
                    next_offset = max(0, int(checkpoint["next_offset"]))
                    complete = bool(checkpoint.get("complete", False))
                    conversation_id = _sid(checkpoint["conversation_id"])
                    cursor = checkpoint.get("cursor")
                    if cursor is not None and not isinstance(cursor, dict):
                        raise ValueError("sync cursor must be an object")
                    cursor_json = json.dumps(cursor, ensure_ascii=False, separators=(",", ":"),
                                             sort_keys=True) if cursor is not None else ""
                    if len(cursor_json) > 8192:
                        raise ValueError("sync cursor is too large")
                    conn.execute("""
                        INSERT INTO sync_checkpoints
                            (platform, account_id, conversation_id, next_offset, cursor_json, complete, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, strftime('%s', 'now'))
                        ON CONFLICT(platform, account_id, conversation_id) DO UPDATE SET
                            next_offset=excluded.next_offset,
                            cursor_json=excluded.cursor_json,
                            complete=excluded.complete,
                            updated_at=excluded.updated_at
                    """, (platform, account_id, conversation_id, next_offset, cursor_json, int(complete)))

                # Refresh aggregates for every conversation touched this batch.
                affected = {
                    _sid(m.get("conversation_id")) for m in msg_rows.values()
                }
                affected.update(conv_rows.keys())
                for cid in affected:
                    self._refresh_conversation(conn, platform, account_id, cid)

                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        return {
            "conversations": len(conv_rows),
            "messages": len(msg_rows),
            "contacts": len(contact_rows),
        }

    def sync_checkpoint(self, platform, account_id, conversation_id):
        platform, account_id, conversation_id = self._management_key(
            platform, account_id, conversation_id)
        with self._lock:
            row = self._require_connection().execute("""
                SELECT next_offset, cursor_json, complete, updated_at FROM sync_checkpoints
                WHERE platform=? AND account_id=? AND conversation_id=?
            """, (platform, account_id, conversation_id)).fetchone()
            try:
                cursor = json.loads(row["cursor_json"]) if row["cursor_json"] else None
            except (TypeError, ValueError):
                cursor = None
            return ({"next_offset": int(row["next_offset"]),
                     "cursor": cursor,
                     "complete": bool(row["complete"]),
                     "updated_at": int(row["updated_at"])} if row else None)

    @staticmethod
    def _dedupe(records, key):
        """Dedupe a batch by composite key, keeping the last occurrence."""
        out = {}
        for record in records:
            out[_sid(record.get(key))] = record
        return out

    def _upsert_conversation(self, conn, platform, account_id, conv) -> None:
        conn.execute(
            """
            INSERT INTO conversations
                (platform, account_id, id, title, kind, avatar_url,
                 member_count, last_message, last_timestamp, message_count, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, account_id, id) DO UPDATE SET
                title          = excluded.title,
                kind           = excluded.kind,
                avatar_url     = excluded.avatar_url,
                member_count   = excluded.member_count,
                last_message   = COALESCE(excluded.last_message, conversations.last_message),
                last_timestamp = COALESCE(excluded.last_timestamp, conversations.last_timestamp),
                message_count  = COALESCE(excluded.message_count, conversations.message_count),
                extra          = excluded.extra
            """,
            (
                platform,
                account_id,
                _sid(conv.get("id")),
                conv.get("title"),
                conv.get("kind"),
                conv.get("avatar_url"),
                _as_int(conv.get("member_count")),
                conv.get("last_message"),
                _as_int(conv.get("last_timestamp")),
                _as_int(conv.get("message_count"), 0),
                _dump_extra(conv),
            ),
        )

    def _upsert_message(self, conn, platform, account_id, msg) -> None:
        cid = _sid(msg.get("conversation_id"))
        # Guarantee the parent conversation row exists so joins never lose rows.
        conn.execute(
            """
            INSERT INTO conversations (platform, account_id, id, message_count)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(platform, account_id, id) DO NOTHING
            """,
            (platform, account_id, cid),
        )
        conn.execute(
            """
            INSERT INTO messages
                (platform, account_id, id, conversation_id, sender_id, sender_name,
                 timestamp, type, text, is_self, media_url, file_name, raw_type,
                 preview, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, account_id, id) DO UPDATE SET
                conversation_id = excluded.conversation_id,
                sender_id       = excluded.sender_id,
                sender_name     = excluded.sender_name,
                timestamp       = excluded.timestamp,
                type            = excluded.type,
                text            = excluded.text,
                is_self         = excluded.is_self,
                media_url       = excluded.media_url,
                file_name       = excluded.file_name,
                raw_type        = excluded.raw_type,
                preview         = excluded.preview,
                extra           = excluded.extra
            """,
            (
                platform,
                account_id,
                _sid(msg.get("id")),
                cid,
                _sid(msg.get("sender_id")),
                msg.get("sender_name"),
                _as_int(msg.get("timestamp"), 0),
                (msg.get("type") or "text"),
                msg.get("text"),
                _as_bool_int(msg.get("is_self")),
                msg.get("media_url"),
                msg.get("file_name"),
                msg.get("raw_type"),
                _preview(msg),
                _dump_extra(msg),
            ),
        )

    def _upsert_contact(self, conn, platform, account_id, contact) -> None:
        extra = contact.get("extra") if isinstance(contact.get("extra"), dict) else {}
        inferred_extra = _load_extra(_dump_extra(contact))
        if inferred_extra:
            extra = {**inferred_extra, **extra}
        conn.execute(
            """
            INSERT INTO contacts
                (platform, account_id, id, name, avatar_url, kind, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, account_id, id) DO UPDATE SET
                name       = excluded.name,
                avatar_url = excluded.avatar_url,
                kind       = excluded.kind,
                extra      = excluded.extra
            """,
            (
                platform,
                account_id,
                _sid(contact.get("id")),
                contact.get("name"),
                contact.get("avatar_url"),
                contact.get("kind"),
                json.dumps(extra, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                if extra else None,
            ),
        )

    def _refresh_conversation(self, conn, platform, account_id, cid) -> None:
        """Recompute aggregate counters from the message table."""
        row = conn.execute(
            """
            SELECT COUNT(*) AS c, MAX(timestamp) AS ts
            FROM messages
            WHERE platform = ? AND account_id = ? AND conversation_id = ?
            """,
            (platform, account_id, cid),
        ).fetchone()
        count = int(row["c"] or 0)
        if count == 0:
            # Nothing to aggregate; keep any provided metadata untouched.
            return
        latest = conn.execute(
            """
            SELECT preview, text
            FROM messages
            WHERE platform = ? AND account_id = ? AND conversation_id = ?
            ORDER BY timestamp DESC, rowid DESC
            LIMIT 1
            """,
            (platform, account_id, cid),
        ).fetchone()
        conn.execute(
            """
            UPDATE conversations
            SET message_count = ?, last_timestamp = ?, last_message = ?
            WHERE platform = ? AND account_id = ? AND id = ?
            """,
            (
                count,
                int(row["ts"]) if row["ts"] is not None else None,
                latest["preview"] if latest else None,
                platform,
                account_id,
                cid,
            ),
        )

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    def conversations(self, platform="all", q="", limit=100, offset=0):
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        with self._lock:
            conn = self._require_connection()
            where, params = "WHERE 1=1", []
            scope, scope_params = self._scope_clause(platform)
            where += scope
            params += scope_params
            if q:
                where += " AND title LIKE ? ESCAPE '\\'"
                params.append(_like_pattern(q))
            total = conn.execute(
                f"SELECT COUNT(*) FROM conversations {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT c.*, COALESCE(cm.note, '') AS note,
                       COALESCE(cm.tags, '[]') AS tags,
                       COALESCE(cm.archived, 0) AS archived,
                       (SELECT p.avatar_url FROM contacts p
                        WHERE p.platform=c.platform AND p.account_id=c.account_id
                          AND p.id=c.id LIMIT 1) AS display_avatar_url
                FROM conversations c
                LEFT JOIN conversation_metadata cm
                  ON cm.platform = c.platform AND cm.account_id = c.account_id
                 AND cm.conversation_id = c.id
                WHERE 1=1{self._scope_clause(platform, 'c.platform')[0]}
                  {"AND c.title LIKE ? ESCAPE '\\'" if q else ""}
                ORDER BY c.last_timestamp DESC, c.id ASC
                LIMIT ? OFFSET ?
                """,
                self._scope_clause(platform, 'c.platform')[1] + ([ _like_pattern(q)] if q else []) + [limit, offset],
            ).fetchall()
        return {
            "items": [self._row_to_conversation(r) for r in rows],
            "total": int(total),
        }

    def get_conversation(self, platform, account_id, conversation_id):
        platform = self._validate_platform(platform)
        with self._lock:
            row = self._require_connection().execute(
                "SELECT * FROM conversations WHERE platform=? AND account_id=? AND id=?",
                (platform, _sid(account_id), _sid(conversation_id)),
            ).fetchone()
        return self._row_to_conversation(row) if row else None

    def wechat_source_media_ids(self, account_id, conversation_id, message_ids):
        """Return source identities already represented in the indexed message table."""
        ids = list(dict.fromkeys(str(value) for value in message_ids if value))
        found = set()
        with self._lock:
            conn = self._require_connection()
            for start in range(0, len(ids), 400):
                batch = ids[start:start + 400]
                placeholders = ",".join("?" for _ in batch)
                rows = conn.execute(
                    f"SELECT id FROM messages WHERE platform='wechat' AND account_id=? "
                    f"AND conversation_id=? AND id IN ({placeholders})",
                    [str(account_id), str(conversation_id), *batch],
                )
                found.update(str(row["id"]) for row in rows)
        return found

    def wechat_indexed_source_media_count(self, account_id, conversation_id, raw_types,
                                         start_time=None, end_time=None):
        types = list(dict.fromkeys(str(value) for value in raw_types))
        if not types:
            return 0
        where = ["platform='wechat'", "account_id=?", "conversation_id=?",
                 "raw_type IN (" + ",".join("?" for _ in types) + ")"]
        params = [str(account_id), str(conversation_id), *types]
        if start_time is not None:
            where.append("timestamp >= ?")
            params.append(int(start_time))
        if end_time is not None:
            where.append("timestamp <= ?")
            params.append(int(end_time))
        with self._lock:
            return int(self._require_connection().execute(
                "SELECT count(*) FROM messages WHERE " + " AND ".join(where), params).fetchone()[0])

    def messages(
        self,
        platform,
        account_id,
        conversation_id,
        q="",
        type="",
        start="",
        end="",
        limit=100,
        offset=0,
        cursor=None,
    ):
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        where = ["platform = ?", "account_id = ?", "conversation_id = ?"]
        params = [_sid(platform), _sid(account_id), _sid(conversation_id)]
        if q:
            if self._fts_enabled and len(q) >= 3:
                where.append("rowid IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)")
                params.append(_fts_phrase(q))
            where.append("text LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(q))
        if type:
            where.append("type = ?")
            params.append(type)
        self._append_time_bounds(where, params, start, end)
        where_sql = "WHERE " + " AND ".join(where)
        decoded_cursor = _decode_cursor(cursor, _cursor_scope(
            [platform, account_id, conversation_id, q, type, start, end]))
        cursor_sql = ""
        cursor_params = []
        if decoded_cursor:
            cursor_sql = " AND (timestamp, rowid) < (?, ?)"
            cursor_params = [decoded_cursor[0], decoded_cursor[1]]
        with self._lock:
            conn = self._require_connection()
            total = conn.execute(
                f"SELECT COUNT(*) FROM messages {where_sql}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT rowid AS _cursor_rowid, * FROM messages {where_sql}
                {cursor_sql}
                ORDER BY timestamp DESC, rowid DESC
                LIMIT ? OFFSET ?
                """,
                params + cursor_params + [limit + 1, 0 if decoded_cursor else offset],
            ).fetchall()
        total = int(total)
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = self._attach_sender_avatars([self._row_to_message(r) for r in rows])
        next_cursor = _encode_cursor(rows[-1]["timestamp"], rows[-1]["_cursor_rowid"],
                                     _cursor_scope([platform, account_id, conversation_id, q, type, start, end])) if has_more and rows else None
        return {
            "items": items,
            "total": total,
            "has_more": has_more if decoded_cursor or offset else offset + len(items) < total,
            "next_cursor": next_cursor,
        }

    def get_message(self, platform, account_id, message_id):
        """Look up one media/message identity without materialising its entire conversation."""
        with self._lock:
            conn = self._require_connection()
            row = conn.execute(
                'SELECT * FROM messages WHERE platform=? AND account_id=? AND id=?',
                (_sid(platform), _sid(account_id), _sid(message_id)),
            ).fetchone()
        return self._attach_sender_avatars([self._row_to_message(row)])[0] if row else None

    def quoted_message_target(self, platform, account_id, conversation_id,
                              referencing_message_id, source_id):
        source_id = str(source_id or '')
        if platform != 'wechat' or not re.fullmatch(r'[1-9][0-9]{0,19}', source_id):
            return None
        with self._lock:
            conn = self._require_connection()
            reference = conn.execute("""
                SELECT extra FROM messages
                WHERE platform='wechat' AND account_id=? AND conversation_id=? AND id=?
            """, (_sid(account_id), _sid(conversation_id),
                  _sid(referencing_message_id))).fetchone()
            if not reference:
                return None
            quote = _load_extra(reference['extra']).get('wechat_quote')
            if not isinstance(quote, dict) or quote.get('target_server_id') != source_id:
                return None
            rows = conn.execute("""
                SELECT id FROM messages
                WHERE platform='wechat' AND account_id=? AND conversation_id=?
                  AND json_valid(extra)
                  AND json_extract(extra, '$._hub_wechat_server_id')=?
                LIMIT 2
            """, (_sid(account_id), _sid(conversation_id), source_id)).fetchall()
        return rows[0]['id'] if len(rows) == 1 else None

    def quoted_message_target_for_reference(self, platform, account_id, conversation_id,
                                             referencing_message_id):
        """Resolve a quote using only the canonical identity of its reference row."""
        if platform != 'wechat':
            return None
        with self._lock:
            conn = self._require_connection()
            reference = conn.execute("""
                SELECT extra FROM messages
                WHERE platform='wechat' AND account_id=? AND conversation_id=? AND id=?
            """, (_sid(account_id), _sid(conversation_id),
                  _sid(referencing_message_id))).fetchone()
            quote = _load_extra(reference['extra']).get('wechat_quote') if reference else None
            source_id = quote.get('target_server_id') if isinstance(quote, dict) else None
            if not isinstance(source_id, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', source_id):
                return None
            rows = conn.execute("""
                SELECT id FROM messages
                WHERE platform='wechat' AND account_id=? AND conversation_id=?
                  AND json_valid(extra)
                  AND json_extract(extra, '$._hub_wechat_server_id')=?
                LIMIT 2
            """, (_sid(account_id), _sid(conversation_id), source_id)).fetchall()
        return rows[0]['id'] if len(rows) == 1 else None

    def attach_quoted_message_previews(self, items):
        """Batch-enrich one already-paged message result; never query per quote."""
        refs = [item for item in items if item.get('platform') == 'wechat'
                and isinstance(item.get('extra', {}).get('wechat_quote'), dict)]
        if not refs:
            return items
        scopes = {(item['account_id'], item['conversation_id']) for item in refs}
        with self._lock:
            conn = self._require_connection()
            source_by_ref = {}
            for account, conversation in scopes:
                ids = [item['id'] for item in refs if item['account_id'] == account
                       and item['conversation_id'] == conversation]
                if not ids:
                    continue
                slots = ','.join('?' for _ in ids)
                rows = conn.execute(f"""
                    SELECT id, extra FROM messages WHERE platform='wechat'
                      AND account_id=? AND conversation_id=? AND id IN ({slots})
                """, [account, conversation, *ids]).fetchall()
                for row in rows:
                    quote = _load_extra(row['extra']).get('wechat_quote')
                    source = quote.get('target_server_id') if isinstance(quote, dict) else None
                    if isinstance(source, str) and re.fullmatch(r'[1-9][0-9]{0,19}', source):
                        source_by_ref[(account, conversation, row['id'])] = source
            candidates = {}
            for account, conversation in scopes:
                sources = list({source for (a, c, _), source in source_by_ref.items()
                                if a == account and c == conversation})
                if not sources:
                    continue
                slots = ','.join('?' for _ in sources)
                rows = conn.execute(f"""
                    SELECT id, account_id, conversation_id, extra
                    FROM messages WHERE platform='wechat' AND account_id=? AND conversation_id=?
                      AND json_valid(extra)
                      AND json_extract(extra, '$._hub_wechat_server_id') IN ({slots})
                """, [account, conversation, *sources]).fetchall()
                for row in rows:
                    key = (row['account_id'], row['conversation_id'],
                           _load_extra(row['extra']).get('_hub_wechat_server_id'))
                    candidates.setdefault(key, []).append(row['id'])
        for item in refs:
            quote = item['extra']['wechat_quote']
            source = source_by_ref.get((item['account_id'], item['conversation_id'], item['id']))
            matches = candidates.get((item['account_id'], item['conversation_id'], source), [])
            if source and len(matches) == 1:
                quote['target_message_id'] = matches[0]
        return items

    def message_rowid(self):
        with self._lock:
            row = self._require_connection().execute(
                "SELECT COALESCE(MAX(rowid), 0) FROM messages").fetchone()
            return int(row[0])

    def incoming_message_events(self, after_rowid, limit=100):
        """Return body-free metadata for newly indexed incoming messages."""
        after_rowid = max(0, int(after_rowid))
        limit = min(max(int(limit), 1), 100)
        with self._lock:
            rows = self._require_connection().execute("""
                SELECT m.rowid AS event_id, m.platform, m.account_id,
                       m.conversation_id, c.title AS conversation_title,
                       c.kind AS conversation_kind, m.sender_name, m.timestamp,
                       m.type, m.is_self
                FROM messages AS m
                LEFT JOIN conversations AS c
                  ON c.platform=m.platform AND c.account_id=m.account_id
                 AND c.id=m.conversation_id
                WHERE m.rowid>? AND m.is_self=0
                ORDER BY m.rowid LIMIT ?
            """, (after_rowid, limit)).fetchall()
            return [dict(row) for row in rows]

    def _attach_sender_avatars(self, items):
        """Attach locally indexed contact avatars to message senders in bounded queries."""
        groups = {}
        for item in items:
            sender_id = item.get("sender_id")
            if sender_id:
                groups.setdefault((item["platform"], item["account_id"]), set()).add(sender_id)
        if not groups:
            return items
        avatars = {}
        with self._lock:
            conn = self._require_connection()
            for (platform, account_id), ids in groups.items():
                values = list(ids)
                for offset in range(0, len(values), 800):
                    batch = values[offset:offset + 800]
                    placeholders = ",".join("?" for _ in batch)
                    rows = conn.execute(
                        f"SELECT id, avatar_url FROM contacts WHERE platform=? AND account_id=? AND id IN ({placeholders})",
                        [platform, account_id, *batch],
                    ).fetchall()
                    avatars.update({(platform, account_id, row["id"]): row["avatar_url"] for row in rows})
        for item in items:
            item["sender_avatar_url"] = avatars.get((item["platform"], item["account_id"], item.get("sender_id")))
        return items

    def message_context(self, platform, account_id, message_id, before=5, after=5,
                        conversation_id=None):
        before, after = min(50, max(0, int(before))), min(50, max(0, int(after)))
        with self._lock:
            conn = self._require_connection()
            target = conn.execute(
                "SELECT rowid, * FROM messages WHERE platform=? AND account_id=? AND id=? "
                "AND (? IS NULL OR conversation_id=?)",
                (_sid(platform), _sid(account_id), _sid(message_id),
                 _sid(conversation_id) if conversation_id is not None else None,
                 _sid(conversation_id) if conversation_id is not None else None),
            ).fetchone()
            if not target:
                return None
            scope = (target["platform"], target["account_id"], target["conversation_id"])
            total = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE platform=? AND account_id=? AND conversation_id=?",
                scope,
            ).fetchone()[0]
            base = """
                SELECT m.*, c.title AS conversation_title, c.kind AS conversation_kind,
                       c.avatar_url AS conversation_avatar_url
                FROM messages m LEFT JOIN conversations c
                  ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.conversation_id
                WHERE m.platform=? AND m.account_id=? AND m.conversation_id=?
            """
            before_rows = conn.execute(
                base + " AND (m.timestamp, m.rowid) < (?, ?) ORDER BY m.timestamp DESC, m.rowid DESC LIMIT ?",
                (*scope, target["timestamp"], target["rowid"], before + 1),
            ).fetchall()
            after_rows = conn.execute(
                base + " AND (m.timestamp, m.rowid) > (?, ?) ORDER BY m.timestamp ASC, m.rowid ASC LIMIT ?",
                (*scope, target["timestamp"], target["rowid"], after + 1),
            ).fetchall()
            current = conn.execute(
                base + " AND m.rowid=?", (*scope, target["rowid"])
            ).fetchone()
        rows = list(reversed(before_rows[:before])) + [current] + after_rows[:after]
        return {
            "items": self._attach_sender_avatars([self._row_to_message(row) for row in rows]),
            "total": int(total), "target_index": min(before, len(before_rows)),
            "has_more_before": len(before_rows) > before,
            "has_more_after": len(after_rows) > after,
        }

    def search(self, q, platform="all", type="", start="", end="", limit=100, offset=0,
               cursor=None, preview_chars=None):
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        where = []
        params = []
        scope, scope_params = self._scope_clause(platform, column="m.platform")
        if scope:
            where.append(scope[5:])  # strip leading " AND "
            params += scope_params
        if q:
            if self._fts_enabled and len(q) >= 3:
                where.append("m.rowid IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)")
                params.append(_fts_phrase(q))
            where.append("m.text LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(q))
        if type:
            where.append("m.type = ?")
            params.append(type)
        self._append_time_bounds(where, params, start, end, alias="m")
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        decoded_cursor = _decode_cursor(cursor, _cursor_scope([q, platform, type, start, end]))
        cursor_sql = ""
        cursor_params = []
        if decoded_cursor:
            cursor_sql = (" AND " if where_sql else " WHERE ") + "(m.timestamp, m.rowid) < (?, ?)"
            cursor_params = [decoded_cursor[0], decoded_cursor[1]]
        with self._lock:
            conn = self._require_connection()
            total = conn.execute(
                f"SELECT COUNT(*) FROM messages m {where_sql}", params
            ).fetchone()[0]
            if preview_chars is None:
                message_projection = ", ".join(f"m.{field}" for field in _MESSAGE_FIELDS)
            else:
                preview_chars = min(max(int(preview_chars), 1), 4096)
                message_projection = ", ".join(
                    f"substr(m.text, 1, {preview_chars}) AS text" if field == "text"
                    else f"m.{field}"
                    for field in _MESSAGE_FIELDS
                )
            rows = conn.execute(
                f"""
                SELECT m.rowid AS _cursor_rowid, {message_projection}, m.extra,
                       c.title AS conversation_title, c.kind AS conversation_kind,
                       COALESCE(NULLIF(c.avatar_url, ''), (
                           SELECT p.avatar_url FROM contacts p
                           WHERE p.platform=m.platform AND p.account_id=m.account_id
                             AND p.id=m.conversation_id LIMIT 1
                       )) AS conversation_avatar_url
                FROM messages m
                LEFT JOIN conversations c
                  ON c.platform = m.platform
                 AND c.account_id = m.account_id
                 AND c.id = m.conversation_id
                {where_sql}
                {cursor_sql}
                ORDER BY m.timestamp DESC, m.rowid DESC
                LIMIT ? OFFSET ?
                """,
                params + cursor_params + [limit + 1, 0 if decoded_cursor else offset],
            ).fetchall()
        total = int(total)
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = self._attach_sender_avatars([self._row_to_message(r) for r in rows])
        next_cursor = _encode_cursor(rows[-1]["timestamp"], rows[-1]["_cursor_rowid"],
                                     _cursor_scope([q, platform, type, start, end])) if has_more and rows else None
        return {
            "items": items,
            "total": total,
            "has_more": has_more if decoded_cursor or offset else offset + len(items) < total,
            "next_cursor": next_cursor,
        }

    def export_page(self, platform="all", after=None, limit=500):
        """Read an ascending, keyset-paginated batch for snapshot exports."""
        limit = min(max(int(limit), 1), 2000)
        where = []
        params = []
        scope, scope_params = self._scope_clause(platform, "m.platform")
        if scope:
            where.append(scope[5:])
            params.extend(scope_params)
        if after is not None:
            timestamp, message_id, rowid = after
            where.append("(m.timestamp > ? OR (m.timestamp = ? AND m.id > ?) OR "
                         "(m.timestamp = ? AND m.id = ? AND m.rowid < ?))")
            params.extend((timestamp, timestamp, message_id, timestamp, message_id, rowid))
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        with self._lock:
            conn = self._require_connection()
            if after is None:
                scope_where = "WHERE " + scope[5:] if scope else ""
                total = int(conn.execute(
                    f"SELECT COUNT(*) FROM messages m {scope_where}", scope_params
                ).fetchone()[0])
            else:
                total = None
            rows = conn.execute(f"""
                SELECT m.rowid AS _cursor_rowid, {", ".join(f"m.{field}" for field in _MESSAGE_FIELDS)},
                       m.extra, c.title AS conversation_title, c.kind AS conversation_kind,
                       COALESCE(NULLIF(c.avatar_url, ''), (
                           SELECT p.avatar_url FROM contacts p
                           WHERE p.platform=m.platform AND p.account_id=m.account_id
                             AND p.id=m.conversation_id LIMIT 1
                       )) AS conversation_avatar_url
                FROM messages m LEFT JOIN conversations c
                  ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.conversation_id
                {where_sql}
                ORDER BY m.timestamp ASC, m.id ASC, m.rowid DESC
                LIMIT ?
            """, [*params, limit + 1]).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = self._attach_sender_avatars([self._row_to_message(row) for row in rows])
        next_after = None
        if has_more and rows:
            last = rows[-1]
            next_after = (int(last["timestamp"] or 0), str(last["id"] or ""),
                          int(last["_cursor_rowid"]))
        return {"items": items, "total": total, "next_after": next_after}

    def export_platforms(self, platform="all"):
        scope, params = self._scope_clause(platform)
        with self._lock:
            rows = self._require_connection().execute(
                f"SELECT DISTINCT platform FROM messages WHERE 1=1{scope} ORDER BY platform", params
            ).fetchall()
        return [row[0] for row in rows]

    def export_conversation_kinds(self, platform="all"):
        scope, params = self._scope_clause(platform, "m.platform")
        with self._lock:
            rows = self._require_connection().execute(f"""
                SELECT DISTINCT c.kind
                FROM messages m LEFT JOIN conversations c
                  ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.conversation_id
                WHERE 1=1{scope}
            """, params).fetchall()
        return [row[0] for row in rows]

    def iter_export_members(self, platform="all", batch_size=500):
        scope, params = self._scope_clause(platform, "m.platform")
        where = "WHERE 1=1" + scope
        query = f"""
            SELECT platform, account_id, sender_id, sender_name FROM (
                SELECT m.platform, m.account_id, m.sender_id,
                       COALESCE(NULLIF(m.sender_name, ''), m.sender_id, '未知') AS sender_name,
                       ROW_NUMBER() OVER (
                           PARTITION BY m.platform, m.account_id, m.sender_id
                           ORDER BY m.timestamp ASC, m.id ASC, m.rowid DESC
                       ) AS member_rank,
                       m.timestamp, m.id, m.rowid
                FROM messages m {where} AND m.sender_id IS NOT NULL AND m.sender_id != ''
            ) WHERE member_rank=1
            ORDER BY timestamp ASC, id ASC, rowid DESC
        """
        with self._lock:
            cursor = self._require_connection().execute(query, params)
            while True:
                rows = cursor.fetchmany(min(max(int(batch_size), 1), 2000))
                if not rows:
                    break
                batch = [dict(row) for row in rows]
                for row in batch:
                    yield {"platformId": row["sender_id"], "accountName": row["sender_name"],
                           "platform": row["platform"], "accountId": row["account_id"]}

    def account_scopes(self):
        with self._lock:
            rows = self._require_connection().execute("""
                SELECT platform, account_id FROM conversations
                UNION SELECT platform, account_id FROM contacts
                UNION SELECT platform, account_id FROM messages
                UNION SELECT 'wechat' AS platform, account_id FROM moments_posts
                ORDER BY platform, account_id
            """).fetchall()
        return [{"platform": row["platform"], "account_id": row["account_id"]} for row in rows]

    def contacts(self, platform="all", q="", limit=200, offset=0, account_id="", kind=""):
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        where, params = ["1=1"], []
        scope, scope_params = self._scope_clause(platform, "c.platform")
        if scope:
            where.append(scope[5:])
            params += scope_params
        if account_id:
            where.append("c.account_id = ?")
            params.append(str(account_id))
        if kind:
            where.append("c.kind = ?")
            params.append(str(kind))
        if q:
            pattern = _like_pattern(q)
            where.append("(c.name LIKE ? ESCAPE '\\' OR c.id LIKE ? ESCAPE '\\' OR "
                         "(json_valid(c.extra) AND ("
                         "json_extract(c.extra, '$.nickname') LIKE ? ESCAPE '\\' OR "
                         "json_extract(c.extra, '$.nick_name') LIKE ? ESCAPE '\\' OR "
                         "json_extract(c.extra, '$.alias') LIKE ? ESCAPE '\\' OR "
                         "json_extract(c.extra, '$.remark') LIKE ? ESCAPE '\\')))" )
            params.extend([pattern] * 6)
        where_sql = "WHERE " + " AND ".join(where)
        with self._lock:
            conn = self._require_connection()
            accounts = conn.execute(
                "SELECT DISTINCT platform, account_id FROM contacts "
                + "ORDER BY platform, account_id",
            ).fetchall()
            total = conn.execute(
                f"SELECT COUNT(*) FROM contacts c {where_sql}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT c.*, ci.identity_id
                FROM contacts c
                LEFT JOIN contact_identities ci
                  ON ci.platform = c.platform AND ci.account_id = c.account_id
                 AND ci.contact_id = c.id
                {where_sql}
                ORDER BY c.name COLLATE NOCASE ASC, c.id ASC,
                         c.platform ASC, c.account_id ASC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()
        return {
            "items": [self._row_to_contact(r) for r in rows],
            "total": int(total),
            "accounts": [{"platform": r["platform"], "account_id": r["account_id"]}
                         for r in accounts],
        }

    def contact_avatar(self, platform, account_id, contact_id):
        """Return one avatar URL only for the exact indexed platform/account/contact."""
        with self._lock:
            conn = self._require_connection()
            row = conn.execute(
                "SELECT avatar_url FROM contacts WHERE platform=? AND account_id=? AND id=?",
                (str(platform), str(account_id), str(contact_id)),
            ).fetchone()
        return row["avatar_url"] if row else None

    def contact_avatars(self, platform, account_id, contact_ids):
        """Return indexed avatar references for exact identities in one account."""
        ids = list(dict.fromkeys(str(value) for value in contact_ids if value))[:1000]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._require_connection().execute(
                f"SELECT id, avatar_url FROM contacts WHERE platform=? AND account_id=? "
                f"AND id IN ({placeholders})",
                [str(platform), str(account_id), *ids],
            ).fetchall()
        return {row["id"]: row["avatar_url"] for row in rows if row["avatar_url"]}

    @staticmethod
    def _safe_moment_text(value, max_length=200_000):
        if value is None or isinstance(value, (dict, list, tuple, set)):
            return ""
        return str(value)[:max_length]

    @classmethod
    def _safe_moment_post(cls, post):
        safe = {key: post.get(key) for key in _MOMENT_POST_FIELDS if key in post}
        safe["id"] = cls._safe_moment_text(post.get("id") or post.get("tid"), 512)
        safe["username"] = cls._safe_moment_text(post.get("username"), 512)
        safe["nickname"] = cls._safe_moment_text(post.get("nickname"), 2048)
        safe["contentDesc"] = cls._safe_moment_text(post.get("contentDesc"))
        safe["createTime"] = _as_int(post.get("createTime"), 0) or 0
        safe["type"] = _as_int(post.get("type"), 0) or 0
        safe["likes"] = [cls._safe_moment_text(x, 2048) for x in post.get("likes", [])
                         if not isinstance(x, (dict, list))][:10_000]
        safe["comments"] = []
        for item in post.get("comments", [])[:10_000] if isinstance(post.get("comments"), list) else []:
            if isinstance(item, dict):
                safe["comments"].append({key: cls._safe_moment_text(item.get(key), 20_000)
                                         for key in ("id", "nickname", "username", "content", "refCommentId")})
        location = post.get("location")
        if isinstance(location, dict):
            safe["location"] = {key: cls._safe_moment_text(location.get(key), 2048)
                                for key in ("city", "country", "poiName", "poiAddress") if key in location}
        else:
            safe["location"] = None
        return safe

    @staticmethod
    def _moment_cache_locator(value, cache_root):
        if not cache_root or not isinstance(value, (str, os.PathLike)) or not value:
            return None
        root = Path(cache_root).resolve(strict=True)
        if not root.is_dir():
            return None
        supplied = Path(value)
        candidate = supplied if supplied.is_absolute() else root / supplied
        try:
            lexical_relative = candidate.relative_to(root) if candidate.is_absolute() else None
            if lexical_relative is None:
                lexical_relative = Path(os.path.normpath(str(candidate))).relative_to(root)
            if any(part in ("", ".", "..") for part in lexical_relative.parts):
                return None
            cursor = root
            for part in lexical_relative.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    return None
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if not resolved.is_file():
                return None
            return resolved.relative_to(root).as_posix()
        except (OSError, ValueError, RuntimeError):
            return None

    def replace_moments(self, account_id, posts, cache_root):
        """Atomically replace one account's sanitized, locally cached Moments catalog."""
        account_id = str(account_id)
        try:
            root = Path(cache_root).resolve(strict=True) if cache_root else None
        except (OSError, RuntimeError, TypeError, ValueError):
            root = None
        if root is not None and not root.is_dir():
            root = None
        prepared = []
        for original in posts:
            if not isinstance(original, dict):
                continue
            post = self._safe_moment_post(original)
            if not post["id"]:
                continue
            media_rows = []
            for media in original.get("media", []) if isinstance(original.get("media"), list) else []:
                if not isinstance(media, dict):
                    continue
                safe_media = {key: media[key] for key in _MOMENT_MEDIA_FIELDS if key in media}
                safe_media["type"] = self._safe_moment_text(media.get("type") or "image", 64)
                locator = self._moment_cache_locator(media.get("_local_path") or media.get("_cache_relpath"), root)
                safe_media["local_available"] = bool(locator)
                live_photo = media.get("livePhoto")
                live_locator = None
                if isinstance(live_photo, dict):
                    safe_live = {key: live_photo[key] for key in _MOMENT_MEDIA_FIELDS if key in live_photo}
                    safe_live["type"] = self._safe_moment_text(live_photo.get("type") or "video", 64)
                    live_locator = self._moment_cache_locator(
                        live_photo.get("_local_path") or live_photo.get("_cache_relpath"), root)
                    safe_live["local_available"] = bool(live_locator)
                    safe_media["livePhoto"] = safe_live
                    safe_media["_live_cache_locator"] = live_locator
                media_rows.append((safe_media, locator))
            prepared.append((post, media_rows))
        with self._lock:
            conn = self._require_connection()
            conn.execute("BEGIN")
            try:
                conn.execute("""INSERT INTO moments_catalog(account_id,catalogued_at,post_count)
                    VALUES(?,?,?) ON CONFLICT(account_id) DO UPDATE SET
                    catalogued_at=excluded.catalogued_at,post_count=excluded.post_count""",
                    (account_id, int(datetime.now(timezone.utc).timestamp()), len(prepared)))
                conn.execute("DELETE FROM moments_posts WHERE account_id=?", (account_id,))
                for post, media_rows in prepared:
                    conn.execute("INSERT INTO moments_posts(account_id,id,timestamp,username,payload) VALUES(?,?,?,?,?)",
                                 (account_id, post["id"], post["createTime"], post["username"],
                                  json.dumps(post, ensure_ascii=False, separators=(",", ":"))))
                    for ordinal, (safe_media, locator) in enumerate(media_rows):
                        conn.execute("INSERT INTO moments_media(account_id,post_id,ordinal,payload,cache_locator) VALUES(?,?,?,?,?)",
                                     (account_id, post["id"], ordinal,
                                      json.dumps(safe_media, ensure_ascii=False, separators=(",", ":")), locator))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return {"available": True, "catalogued": True, "total": len(prepared)}

    def moments(self, account_id, *, username="", keyword="", start="", end="", limit=50, offset=0):
        """Page the persisted account catalog in stable timestamp/id order."""
        limit, offset = int(limit), int(offset)
        if limit < 1 or limit > 500 or offset < 0 or offset > 10_000_000:
            raise ValueError("invalid Moments page bounds")
        start_ts, end_ts = parse_bound(start), parse_bound(end, end=True)
        where = ["p.account_id=?"]
        params = [str(account_id)]
        if username:
            where.append("p.username=?")
            params.append(str(username))
        if start_ts is not None:
            where.append("p.timestamp>=?")
            params.append(start_ts)
        if end_ts is not None:
            where.append("p.timestamp<=?")
            params.append(end_ts)
        if keyword:
            pattern = _like_pattern(str(keyword))
            where.append("(json_extract(p.payload,'$.contentDesc') LIKE ? ESCAPE '\\' OR "
                         "EXISTS (SELECT 1 FROM json_each(p.payload,'$.comments') AS c "
                         "WHERE json_extract(c.value,'$.content') LIKE ? ESCAPE '\\'))")
            params.extend((pattern, pattern))
        clause = " AND ".join(where)
        with self._lock:
            conn = self._require_connection()
            catalog = conn.execute("SELECT 1 FROM moments_catalog WHERE account_id=?", (str(account_id),)).fetchone()
            total = conn.execute(f"SELECT COUNT(*) FROM moments_posts p WHERE {clause}", params).fetchone()[0]
            rows = conn.execute(f"SELECT p.id,p.payload FROM moments_posts p WHERE {clause} ORDER BY p.timestamp DESC,p.id DESC LIMIT ? OFFSET ?",
                                params + [limit, offset]).fetchall()
            items = []
            media_by_post = {}
            if rows:
                post_ids = [row["id"] for row in rows]
                placeholders = ",".join("?" for _ in post_ids)
                media_rows = conn.execute(
                    f"SELECT post_id,payload,cache_locator FROM moments_media "
                    f"WHERE account_id=? AND post_id IN ({placeholders}) ORDER BY post_id,ordinal",
                    [str(account_id), *post_ids],
                ).fetchall()
                for media_row in media_rows:
                    media_by_post.setdefault(media_row["post_id"], []).append(media_row)
            for row in rows:
                post = json.loads(row["payload"])
                post["media"] = []
                for media_row in media_by_post.get(row["id"], []):
                    media = json.loads(media_row["payload"])
                    if media_row["cache_locator"]:
                        media["_cache_relpath"] = media_row["cache_locator"]
                    live_locator = media.pop("_live_cache_locator", None)
                    if live_locator and isinstance(media.get("livePhoto"), dict):
                        media["livePhoto"]["_cache_relpath"] = live_locator
                    post["media"].append(media)
                items.append(post)
        return {"available": bool(catalog), "catalogued": bool(catalog), "items": items,
                "total": int(total), "limit": limit, "offset": offset}

    def moment_accounts(self):
        """Return account identities with a persisted Moments catalog, including empty catalogs."""
        with self._lock:
            rows = self._require_connection().execute(
                "SELECT account_id FROM moments_catalog ORDER BY account_id").fetchall()
        return [str(row["account_id"]) for row in rows]

    def moment_media_locator(self, account_id, post_id, ordinal, variant="original"):
        """Resolve an indexed Moments cache locator by stable catalog identity."""
        column = "cache_locator" if variant == "original" else "json_extract(payload,'$._live_cache_locator')"
        if variant not in ("original", "live"):
            return None
        with self._lock:
            row = self._require_connection().execute(
                f"SELECT {column} AS locator FROM moments_media WHERE account_id=? AND post_id=? AND ordinal=?",
                (str(account_id), str(post_id), int(ordinal))).fetchone()
        return row["locator"] if row else None

    def media(self, platform="all", type="", q="", limit=100, offset=0, start="", end="",
              expand_attachments=False, source="all", account_id="", conversation_id=""):
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        if expand_attachments:
            return self._media_attachment_page(platform, type, q, limit, offset, start, end, source, account_id, conversation_id)
        placeholders = ",".join("?" for _ in MEDIA_TYPES)
        where = [f"(m.type IN ({placeholders}) AND NOT (m.platform='wechat' AND COALESCE(m.raw_type,'') IN ('47','11000')))" ]
        params = list(MEDIA_TYPES)
        scope, scope_params = self._scope_clause(platform, column="m.platform")
        if scope:
            where.append(scope[5:])
            params += scope_params
        if account_id:
            where.append("m.account_id = ?")
            params.append(str(account_id))
        if conversation_id:
            where.append("m.conversation_id = ?")
            params.append(str(conversation_id))
        if type:
            where.append("m.type = ?")
            params.append(type)
        if q:
            where.append("c.title LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(q))
        self._append_time_bounds(where, params, start, end, alias="m")
        where_sql = "WHERE " + " AND ".join(where)
        with self._lock:
            conn = self._require_connection()
            total = conn.execute(
                f"""
                SELECT COUNT(*) FROM messages m
                LEFT JOIN conversations c
                  ON c.platform = m.platform
                 AND c.account_id = m.account_id
                 AND c.id = m.conversation_id
                {where_sql}
                """,
                params,
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT m.*, c.title AS conversation_title, c.kind AS conversation_kind,
                       c.avatar_url AS conversation_avatar_url
                FROM messages m
                LEFT JOIN conversations c
                  ON c.platform = m.platform
                 AND c.account_id = m.account_id
                 AND c.id = m.conversation_id
                {where_sql}
                ORDER BY m.timestamp DESC, m.rowid DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()
        return {
            "items": self._attach_sender_avatars([self._row_to_message(r) for r in rows]),
            "total": int(total),
        }

    def _media_attachment_page(self, platform, media_type, query, limit, offset, start, end, source="all", account_id="", conversation_id=""):
        """Page media at attachment granularity while retaining each parent message."""
        if source not in ("all", "chat", "moments"):
            raise ValueError("invalid media source")
        if source != "chat":
            return self._unified_media_attachment_page(platform, media_type, query, limit, offset,
                                                       start, end, source, account_id, conversation_id)
        placeholders = ",".join("?" for _ in MEDIA_TYPES)
        where = [f"((m.type IN ({placeholders}) OR (m.platform='wechat' AND m.raw_type IN ('47','11000'))) OR (m.platform IN ('wecom', 'qq') AND "
                 "CASE WHEN json_valid(m.extra) THEN CASE "
                 "WHEN m.platform='wecom' AND json_type(m.extra, '$.attachments')='array' "
                 "THEN json_array_length(m.extra, '$.attachments') "
                 "WHEN m.platform='qq' AND json_type(m.extra, '$.roaming_resources')='array' "
                 "THEN json_array_length(m.extra, '$.roaming_resources') "
                 "WHEN m.platform='qq' AND json_type(m.extra, '$.media')='array' "
                 "THEN json_array_length(m.extra, '$.media') ELSE 0 END "
                 "ELSE 0 END > 0))"]
        params = list(MEDIA_TYPES)
        scope, scope_params = self._scope_clause(platform, column="m.platform")
        if scope:
            where.append(scope[5:])
            params += scope_params
        if account_id:
            where.append("m.account_id = ?")
            params.append(str(account_id))
        if conversation_id:
            where.append("m.conversation_id = ?")
            params.append(str(conversation_id))
        self._append_time_bounds(where, params, start, end, alias="m")
        where_sql = " AND ".join(where)
        attachment_array = """CASE WHEN json_valid(c.extra) THEN CASE
            WHEN c.platform='wecom' AND json_type(c.extra, '$.attachments')='array'
                THEN json_extract(c.extra, '$.attachments')
            WHEN c.platform='qq' AND json_type(c.extra, '$.roaming_resources')='array'
                THEN json_extract(c.extra, '$.roaming_resources')
            WHEN c.platform='qq' AND json_type(c.extra, '$.media')='array'
                THEN json_extract(c.extra, '$.media')
            ELSE '[]' END ELSE '[]' END"""
        resource_type = "lower(COALESCE(json_extract(j.value, '$.type'), 'file'))"
        normalized_type = f"""CASE {resource_type}
            WHEN 'audio' THEN 'voice' WHEN 'photo' THEN 'image'
            WHEN 'picture' THEN 'image' WHEN 'images' THEN 'image'
            WHEN 'videos' THEN 'video' WHEN 'files' THEN 'file'
            WHEN '图片' THEN 'image' WHEN '语音' THEN 'voice'
            WHEN '视频' THEN 'video' WHEN '文件' THEN 'file'
            ELSE {resource_type} END"""
        ctes = f"""WITH candidates AS (
                SELECT m.*, c.title AS conversation_title, c.kind AS conversation_kind,
                       c.avatar_url AS conversation_avatar_url, m.rowid AS _media_rowid
                FROM messages m
                LEFT JOIN conversations c
                  ON c.platform=m.platform AND c.account_id=m.account_id
                 AND c.id=m.conversation_id
                WHERE {where_sql}
            ), media_entries AS (
                SELECT c.*, NULL AS _media_resource_index, NULL AS _media_resource,
                       CASE WHEN c.platform='wechat' AND c.raw_type IN ('47','11000') THEN 'sticker' ELSE c.type END AS _media_type
                FROM candidates c
                WHERE (c.type IN ({placeholders}) OR (c.platform='wechat' AND c.raw_type IN ('47','11000')))
                  AND json_array_length({attachment_array})=0
                UNION ALL
                SELECT c.*, CAST(j.key AS INTEGER) AS _media_resource_index,
                       j.value AS _media_resource, {normalized_type} AS _media_type
                FROM candidates c JOIN json_each({attachment_array}) j
            )"""
        query_filter = " WHERE (?='' OR _media_type=?) AND (?='' OR " \
                       "conversation_title LIKE ? ESCAPE '\\' OR " \
                       "file_name LIKE ? ESCAPE '\\' OR " \
                       "COALESCE(text,'') LIKE ? ESCAPE '\\' OR " \
                       "COALESCE(sender_name,'') LIKE ? ESCAPE '\\' OR " \
                       "COALESCE(sender_id,'') LIKE ? ESCAPE '\\' OR " \
                       "json_extract(_media_resource, '$.name') LIKE ? ESCAPE '\\' OR " \
                       "json_extract(_media_resource, '$.n') LIKE ? ESCAPE '\\' OR " \
                       "json_extract(_media_resource, '$.file_name') LIKE ? ESCAPE '\\')"
        filter_params = [media_type, media_type, query, _like_pattern(query),
                         _like_pattern(query), _like_pattern(query), _like_pattern(query),
                         _like_pattern(query), _like_pattern(query), _like_pattern(query),
                         _like_pattern(query)]
        with self._lock:
            conn = self._require_connection()
            total = conn.execute(
                f"{ctes} SELECT COUNT(*) FROM media_entries{query_filter}",
                params + list(MEDIA_TYPES) + filter_params,
            ).fetchone()[0]
            rows = conn.execute(
                f"""{ctes} SELECT * FROM media_entries{query_filter}
                    ORDER BY timestamp DESC, _media_rowid DESC,
                             COALESCE(_media_resource_index, -1) ASC
                    LIMIT ? OFFSET ?""",
                params + list(MEDIA_TYPES) + filter_params + [limit, offset],
            ).fetchall()
        items = []
        for row in rows:
            item = self._row_to_message(row)
            resource_index = row["_media_resource_index"]
            raw_resource = row["_media_resource"]
            resource = json.loads(raw_resource) if raw_resource is not None else None
            if isinstance(resource, dict):
                resource = {key: resource[key] for key in
                            ("name", "n", "type", "size", "media_url", "available")
                            if key in resource}
            else:
                resource = None
            item["media_entry"] = {
                "resource_index": int(resource_index) if resource_index is not None else None,
                "resource": resource,
                "type": row["_media_type"],
            }
            items.append(item)
        return {"items": self._attach_sender_avatars(items), "total": int(total)}

    def _unified_media_attachment_page(self, platform, media_type, query, limit, offset, start, end, source, account_id="", conversation_id=""):
        """Filter, count and page chat and Moments media in one SQLite query."""
        include_chat = source == "all" or source == "chat"
        include_moments = source == "all" or source == "moments"
        if conversation_id:
            include_moments = False
        if platform not in ("all", "wechat"):
            include_moments = False
        branches, params = [], []
        if include_chat:
            placeholders = ",".join("?" for _ in MEDIA_TYPES)
            where = [f"((m.type IN ({placeholders}) OR (m.platform='wechat' AND m.raw_type IN ('47','11000'))) OR (m.platform IN ('wecom','qq') AND CASE WHEN json_valid(m.extra) THEN CASE WHEN m.platform='wecom' AND json_type(m.extra,'$.attachments')='array' THEN json_array_length(m.extra,'$.attachments') WHEN m.platform='qq' AND json_type(m.extra,'$.roaming_resources')='array' THEN json_array_length(m.extra,'$.roaming_resources') WHEN m.platform='qq' AND json_type(m.extra,'$.media')='array' THEN json_array_length(m.extra,'$.media') ELSE 0 END ELSE 0 END > 0))"]
            params.extend(MEDIA_TYPES)
            scope, scope_params = self._scope_clause(platform, column="m.platform")
            if scope:
                where.append(scope[5:])
                params.extend(scope_params)
            if account_id:
                where.append("m.account_id = ?")
                params.append(str(account_id))
            if conversation_id:
                where.append("m.conversation_id = ?")
                params.append(str(conversation_id))
            self._append_time_bounds(where, params, start, end, alias="m")
            where_sql = " AND ".join(where)
            attachment_array = """CASE WHEN json_valid(m.extra) THEN CASE
                WHEN m.platform='wecom' AND json_type(m.extra,'$.attachments')='array' THEN json_extract(m.extra,'$.attachments')
                WHEN m.platform='qq' AND json_type(m.extra,'$.roaming_resources')='array' THEN json_extract(m.extra,'$.roaming_resources')
                WHEN m.platform='qq' AND json_type(m.extra,'$.media')='array' THEN json_extract(m.extra,'$.media') ELSE '[]' END ELSE '[]' END"""
            raw = "lower(COALESCE(json_extract(j.value,'$.type'),'file'))"
            normalized = f"CASE {raw} WHEN 'audio' THEN 'voice' WHEN 'photo' THEN 'image' WHEN 'picture' THEN 'image' WHEN 'images' THEN 'image' WHEN 'videos' THEN 'video' WHEN 'files' THEN 'file' WHEN '图片' THEN 'image' WHEN '语音' THEN 'voice' WHEN '视频' THEN 'video' WHEN '文件' THEN 'file' ELSE {raw} END"
            branches.append(f"""SELECT 'message' AS resource_kind,m.platform,m.account_id,m.id,m.conversation_id,
                c.title AS conversation_title,c.kind AS conversation_kind,c.avatar_url AS conversation_avatar_url,
                m.timestamp,m.sender_name,m.sender_id,m.is_self,m.type AS message_type,m.file_name,
                m.text,m.extra,m.rowid AS sort_id,NULL AS post_id,NULL AS media_index,NULL AS variant,
                NULL AS cache_locator,NULL AS post_payload,NULL AS resource_index,NULL AS resource_json,
                CASE WHEN m.platform='wechat' AND m.raw_type IN ('47','11000') THEN 'sticker' ELSE m.type END AS media_type
                FROM messages m LEFT JOIN conversations c ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.conversation_id
                WHERE {where_sql} AND m.type IN ({placeholders}) AND json_array_length({attachment_array})=0
                UNION ALL
                SELECT 'message',m.platform,m.account_id,m.id,m.conversation_id,c.title,c.kind,c.avatar_url,
                m.timestamp,m.sender_name,m.sender_id,m.is_self,m.type,m.file_name,m.text,m.extra,m.rowid,
                NULL,NULL,NULL,NULL,NULL,CAST(j.key AS INTEGER),j.value,{normalized}
                FROM messages m LEFT JOIN conversations c ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.conversation_id
                JOIN json_each({attachment_array}) j WHERE {where_sql}""")
            base_params = list(params)
            params.extend(MEDIA_TYPES)
            params.extend(base_params)
        if include_moments:
            moment_conditions = ["1=1"]
            moment_params = []
            if account_id:
                moment_conditions.append("p.account_id = ?")
                moment_params.append(str(account_id))
            self._append_time_bounds(moment_conditions, moment_params, start, end, alias="p")
            branches.append("""SELECT 'moment' AS resource_kind, 'wechat' AS platform,
                p.account_id AS account_id, p.id AS id, '' AS conversation_id,
                COALESCE(json_extract(p.payload,'$.nickname'),p.username) AS conversation_title,
                'moment' AS conversation_kind, NULL AS conversation_avatar_url,
                p.timestamp AS timestamp, p.username AS sender_name, p.username AS sender_id,
                0 AS is_self, 'image' AS message_type, NULL AS file_name, p.payload AS text,
                '{}' AS extra, 0 AS sort_id, p.id AS post_id, mm.ordinal AS media_index,
                'original' AS variant, mm.cache_locator AS cache_locator, p.payload AS post_payload,
                mm.ordinal AS resource_index, mm.payload AS resource_json,
                CASE lower(COALESCE(json_extract(mm.payload,'$.type'),'image'))
                  WHEN 'video' THEN 'video' WHEN 'voice' THEN 'voice' WHEN 'file' THEN 'file' ELSE 'image' END AS media_type
                FROM moments_posts p JOIN moments_media mm ON mm.account_id=p.account_id AND mm.post_id=p.id
                WHERE """ + " AND ".join(moment_conditions))
            params.extend(moment_params)
            # Include already-catalogued Live Photo video files as distinct resources.
            branches.append("""SELECT 'moment' AS resource_kind, 'wechat' AS platform,
                p.account_id AS account_id, p.id AS id, '' AS conversation_id,
                COALESCE(json_extract(p.payload,'$.nickname'),p.username) AS conversation_title,
                'moment' AS conversation_kind, NULL AS conversation_avatar_url,
                p.timestamp AS timestamp, p.username AS sender_name, p.username AS sender_id,
                0 AS is_self, 'video' AS message_type, NULL AS file_name, p.payload AS text,
                '{}' AS extra, 0 AS sort_id, p.id AS post_id, mm.ordinal AS media_index,
                'live' AS variant, json_extract(mm.payload,'$._live_cache_locator') AS cache_locator,
                p.payload AS post_payload, mm.ordinal AS resource_index,
                json_extract(mm.payload,'$.livePhoto') AS resource_json, 'video' AS media_type
                FROM moments_posts p JOIN moments_media mm ON mm.account_id=p.account_id AND mm.post_id=p.id
                WHERE json_extract(mm.payload,'$._live_cache_locator') IS NOT NULL AND """ + " AND ".join(moment_conditions))
            params.extend(moment_params)
        if not branches:
            return {"items": [], "total": 0}
        union = " UNION ALL ".join(branches)
        where = ["(?='' OR media_type=?)", "(?='' OR COALESCE(conversation_title,'') LIKE ? ESCAPE '\\' OR COALESCE(file_name,'') LIKE ? ESCAPE '\\' OR COALESCE(text,'') LIKE ? ESCAPE '\\' OR COALESCE(sender_name,'') LIKE ? ESCAPE '\\' OR COALESCE(sender_id,'') LIKE ? ESCAPE '\\' OR json_extract(resource_json,'$.name') LIKE ? ESCAPE '\\' OR json_extract(resource_json,'$.n') LIKE ? ESCAPE '\\' OR json_extract(resource_json,'$.file_name') LIKE ? ESCAPE '\\' OR COALESCE(post_payload,'') LIKE ? ESCAPE '\\')"]
        filter_params = [media_type, media_type, query, _like_pattern(query), _like_pattern(query),
                         _like_pattern(query), _like_pattern(query), _like_pattern(query),
                         _like_pattern(query), _like_pattern(query),
                         _like_pattern(query), _like_pattern(query)]
        sql = f"WITH unified AS ({union}) SELECT * FROM unified WHERE {' AND '.join(where)}"
        with self._lock:
            conn = self._require_connection()
            total = conn.execute(f"WITH unified AS ({union}) SELECT COUNT(*) FROM unified WHERE {' AND '.join(where)}",
                                 params + filter_params).fetchone()[0]
            rows = conn.execute(sql + " ORDER BY timestamp DESC, sort_id DESC, resource_kind, account_id, id, media_index, variant LIMIT ? OFFSET ?",
                                params + filter_params + [limit, offset]).fetchall()
        items = []
        for row in rows:
            if row["resource_kind"] == "moment":
                resource = json.loads(row["resource_json"]) if row["resource_json"] else {}
                item = {"resource_kind": "moment", "platform": "wechat", "account_id": row["account_id"],
                        "id": row["id"], "post_id": row["post_id"], "media_index": row["media_index"],
                        "variant": row["variant"], "conversation_id": "", "conversation_title": row["conversation_title"],
                        "timestamp": row["timestamp"], "sender_name": row["sender_name"], "type": row["media_type"],
                        "text": json.loads(row["post_payload"] or "{}").get("contentDesc", ""),
                        "media_entry": {"resource_kind": "moment", "post_id": row["post_id"],
                                        "media_index": row["media_index"], "variant": row["variant"],
                                        "resource": {key: resource[key] for key in ("name", "type", "mime_type", "local_available") if key in resource},
                                        "type": row["media_type"]}}
                if row["cache_locator"]:
                    item["media_entry"]["cache_available"] = True
                    item["_cache_relpath"] = row["cache_locator"]
            else:
                message = {key: row[key] for key in row.keys() if key in
                           ("platform", "account_id", "id", "conversation_id", "conversation_title", "conversation_kind",
                            "conversation_avatar_url", "timestamp", "sender_name", "sender_id", "is_self", "file_name", "text", "extra")}
                message["type"] = row["media_type"]
                resource = json.loads(row["resource_json"]) if row["resource_json"] else None
                if isinstance(resource, dict):
                    resource = {key: resource[key] for key in ("name", "n", "type", "size", "media_url", "available") if key in resource}
                message["media_entry"] = {"resource_index": row["resource_index"], "resource": resource,
                                           "type": row["media_type"]}
                item = message
            items.append(item)
        return {"items": items, "total": int(total)}

    def source_counts(self):
        """Live per-platform counters without report/date/media aggregation."""
        with self._lock:
            conn = self._require_connection()
            rows = conn.execute("""
                SELECT platform, SUM(messages) AS messages,
                       SUM(conversations) AS conversations
                FROM (
                    SELECT platform, COUNT(*) AS messages, 0 AS conversations
                    FROM messages GROUP BY platform
                    UNION ALL
                    SELECT platform, 0, COUNT(*)
                    FROM conversations GROUP BY platform
                ) GROUP BY platform
            """).fetchall()
        return {row["platform"]: {"messages": int(row["messages"]),
                                  "conversations": int(row["conversations"])}
                for row in rows}

    def stats(self, platform="all"):
        with self._lock:
            conn = self._require_connection()
            scope, scope_params = self._scope_clause(platform)
            message_count = conn.execute(
                f"SELECT COUNT(*) FROM messages WHERE 1=1{scope}", scope_params
            ).fetchone()[0]
            conversation_count = conn.execute(
                f"SELECT COUNT(*) FROM conversations WHERE 1=1{scope}", scope_params
            ).fetchone()[0]
            contact_count = conn.execute(
                f"SELECT COUNT(*) FROM contacts WHERE 1=1{scope}", scope_params
            ).fetchone()[0]
            media_scope = self._scope_clause(platform, column="m.platform")[0]
            media_count = conn.execute(
                f"SELECT COUNT(*) FROM messages m WHERE {media_message_predicate('m')}{media_scope}",
                scope_params,
            ).fetchone()[0]

            per_type = {
                row["type"] or "unknown": int(row["c"])
                for row in conn.execute(
                    f"SELECT CASE WHEN platform='wechat' AND raw_type IN ('47','11000') THEN 'sticker' ELSE type END AS type, COUNT(*) AS c FROM messages WHERE 1=1{scope} GROUP BY CASE WHEN platform='wechat' AND raw_type IN ('47','11000') THEN 'sticker' ELSE type END",
                    scope_params,
                ).fetchall()
            }

            per_platform = {}
            for row in conn.execute(
                """
                SELECT platform,
                       SUM(CASE WHEN kind = 'message' THEN n ELSE 0 END) AS messages,
                       SUM(CASE WHEN kind = 'conversation' THEN n ELSE 0 END) AS conversations,
                       SUM(CASE WHEN kind = 'contact' THEN n ELSE 0 END) AS contacts
                FROM (
                    SELECT platform, 'message' AS kind, COUNT(*) AS n FROM messages GROUP BY platform
                    UNION ALL
                    SELECT platform, 'conversation', COUNT(*) FROM conversations GROUP BY platform
                    UNION ALL
                    SELECT platform, 'contact', COUNT(*) FROM contacts GROUP BY platform
                )
                GROUP BY platform
                """,
                [],
            ).fetchall():
                per_platform[row["platform"]] = {
                    "messages": int(row["messages"] or 0),
                    "conversations": int(row["conversations"] or 0),
                    "contacts": int(row["contacts"] or 0),
                }
            if platform and platform != "all":
                per_platform = {
                    k: v for k, v in per_platform.items() if k == platform
                }

            daily_scope, daily_params = self._scope_clause(platform)
            daily = [
                {"date": row["d"], "count": int(row["c"])}
                for row in conn.execute(
                    f"""
                    SELECT date(timestamp, 'unixepoch', '+8 hours') AS d,
                           COUNT(*) AS c
                    FROM messages
                    WHERE 1=1{daily_scope}
                    GROUP BY d
                    ORDER BY d ASC
                    """,
                    daily_params,
                ).fetchall()
                if row["d"] is not None
            ]

        return {
            "message_count": int(message_count),
            "conversation_count": int(conversation_count),
            "contact_count": int(contact_count),
            "media_count": int(media_count),
            "per_platform": per_platform,
            "per_type": per_type,
            "daily": daily,
        }

    def count(self, platform="all"):
        with self._lock:
            conn = self._require_connection()
            scope, params = self._scope_clause(platform)
            row = conn.execute(
                f"SELECT COUNT(*) FROM messages WHERE 1=1{scope}", params
            ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------
    # import job provenance and checkpoints
    # ------------------------------------------------------------------
    @staticmethod
    def _import_job(row):
        if row is None:
            return None
        result = dict(row)
        result["errors"] = json.loads(result.pop("errors"))
        result.pop("staging_file", None)
        return result

    def create_import_job(self, job):
        with self._lock:
            conn = self._require_connection()
            conn.execute("""
                INSERT INTO import_jobs
                    (id, fingerprint, filename, staging_file, state, created_at, updated_at,
                     total, valid, invalid, errors)
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
            """, (job["id"], job["fingerprint"], job["filename"], job["staging_file"],
                  job["created_at"], job["created_at"], job["total"], job["valid"],
                  job["invalid"], json.dumps(job.get("errors", []), ensure_ascii=False)))
            return self._import_job(conn.execute("SELECT * FROM import_jobs WHERE id=?", (job["id"],)).fetchone())

    def import_job(self, job_id):
        with self._lock:
            return self._import_job(self._require_connection().execute(
                "SELECT * FROM import_jobs WHERE id=?", (job_id,)).fetchone())

    def import_jobs(self, limit=50):
        with self._lock:
            rows = self._require_connection().execute(
                "SELECT * FROM import_jobs ORDER BY created_at DESC LIMIT ?", (min(max(int(limit), 1), 100),)
            ).fetchall()
            return [self._import_job(row) for row in rows]

    def import_jobs_page(self, limit=50, offset=0):
        limit = min(max(int(limit), 1), 100)
        offset = max(int(offset), 0)
        with self._lock:
            conn = self._require_connection()
            total = int(conn.execute("SELECT COUNT(*) FROM import_jobs").fetchone()[0])
            rows = conn.execute(
                "SELECT * FROM import_jobs ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return {"items": [self._import_job(row) for row in rows], "total": total}

    def record_mcp_operation(self, tool, status):
        tool = str(tool)[:64]
        status = "success" if status == "success" else "error"
        with self._lock:
            conn = self._require_connection()
            conn.execute("INSERT INTO mcp_operations(tool,status,timestamp) VALUES(?,?,strftime('%s','now'))",
                         (tool, status))
            conn.execute("DELETE FROM mcp_operations WHERE id NOT IN (SELECT id FROM mcp_operations ORDER BY id DESC LIMIT 200)")

    def mcp_operations(self, limit=20):
        limit = min(max(int(limit), 1), 100)
        with self._lock:
            rows = self._require_connection().execute(
                "SELECT tool,status,timestamp FROM mcp_operations ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _management_key(platform, account_id, entity_id):
        platform = ChatStore._validate_platform(platform)
        account_id, entity_id = _sid(account_id), _sid(entity_id)
        if not account_id or not entity_id or len(account_id) > 256 or len(entity_id) > 512:
            raise ValueError("账号或记录标识无效")
        return platform, account_id, entity_id

    def update_conversation_metadata(self, platform, account_id, conversation_id,
                                     note=None, tags=None, archived=None):
        key = self._management_key(platform, account_id, conversation_id)
        if note is not None:
            if not isinstance(note, str) or len(note) > 4096:
                raise ValueError("备注必须是 4096 字以内的文本")
        if tags is not None:
            if not isinstance(tags, list) or len(tags) > 50 or any(
                    not isinstance(tag, str) or not tag.strip() or len(tag.strip()) > 64 for tag in tags):
                raise ValueError("标签最多 50 个，每个标签需为 1 至 64 字")
            tags = list(dict.fromkeys(tag.strip() for tag in tags))
        if archived is not None and not isinstance(archived, bool):
            raise ValueError("归档状态无效")
        with self._lock:
            conn = self._require_connection()
            if not conn.execute("SELECT 1 FROM conversations WHERE platform=? AND account_id=? AND id=?", key).fetchone():
                raise ValueError("会话不存在")
            existing = conn.execute("SELECT note,tags,archived FROM conversation_metadata WHERE platform=? AND account_id=? AND conversation_id=?", key).fetchone()
            values = (note if note is not None else (existing["note"] if existing else ""),
                      json.dumps(tags if tags is not None else (json.loads(existing["tags"]) if existing else []), ensure_ascii=False),
                      int(archived if archived is not None else (existing["archived"] if existing else 0)))
            conn.execute("""INSERT INTO conversation_metadata(platform,account_id,conversation_id,note,tags,archived,updated_at)
                VALUES(?,?,?,?,?,?,strftime('%s','now')) ON CONFLICT(platform,account_id,conversation_id)
                DO UPDATE SET note=excluded.note,tags=excluded.tags,archived=excluded.archived,updated_at=excluded.updated_at""",
                (*key, *values))
            action = "conversation_metadata.update"
            conn.execute("INSERT INTO operation_log(action,entity_type,entity_key,status,details,timestamp) VALUES(?, 'conversation', ?, 'success', '{}', strftime('%s','now'))",
                         (action, json.dumps(key, ensure_ascii=False)))
            conn.execute("DELETE FROM operation_log WHERE id NOT IN (SELECT id FROM operation_log ORDER BY id DESC LIMIT 2000)")
            return {"note": values[0], "tags": json.loads(values[1]), "archived": bool(values[2])}

    def set_contact_identity(self, platform, account_id, contact_id, identity_id):
        key = self._management_key(platform, account_id, contact_id)
        if not isinstance(identity_id, str) or not identity_id.strip() or len(identity_id.strip()) > 256:
            raise ValueError("统一身份标识必须为 1 至 256 字")
        identity_id = identity_id.strip()
        with self._lock:
            conn = self._require_connection()
            if not conn.execute("SELECT 1 FROM contacts WHERE platform=? AND account_id=? AND id=?", key).fetchone():
                raise ValueError("联系人不存在")
            conn.execute("""INSERT INTO contact_identities(platform,account_id,contact_id,identity_id,updated_at)
                VALUES(?,?,?,?,strftime('%s','now')) ON CONFLICT(platform,account_id,contact_id)
                DO UPDATE SET identity_id=excluded.identity_id,updated_at=excluded.updated_at""", (*key, identity_id))
            conn.execute("INSERT INTO operation_log(action,entity_type,entity_key,status,details,timestamp) VALUES('contact_identity.link','contact',?,'success','{}',strftime('%s','now'))",
                         (json.dumps(key, ensure_ascii=False),))
            conn.execute("DELETE FROM operation_log WHERE id NOT IN (SELECT id FROM operation_log ORDER BY id DESC LIMIT 2000)")
        return {"identity_id": identity_id}

    def remove_contact_identity(self, platform, account_id, contact_id):
        key = self._management_key(platform, account_id, contact_id)
        with self._lock:
            conn = self._require_connection()
            if not conn.execute("SELECT 1 FROM contacts WHERE platform=? AND account_id=? AND id=?", key).fetchone():
                raise ValueError("联系人不存在")
            conn.execute("DELETE FROM contact_identities WHERE platform=? AND account_id=? AND contact_id=?", key)
            conn.execute("INSERT INTO operation_log(action,entity_type,entity_key,status,details,timestamp) VALUES('contact_identity.unlink','contact',?,'success','{}',strftime('%s','now'))",
                         (json.dumps(key, ensure_ascii=False),))
            conn.execute("DELETE FROM operation_log WHERE id NOT IN (SELECT id FROM operation_log ORDER BY id DESC LIMIT 2000)")
        return {"identity_id": None}

    def operation_log(self, limit=50):
        limit = min(max(int(limit), 1), 200)
        with self._lock:
            rows = self._require_connection().execute(
                "SELECT action,entity_type,status,timestamp FROM operation_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def active_import_jobs(self):
        with self._lock:
            rows = self._require_connection().execute(
                "SELECT * FROM import_jobs WHERE state IN ('queued','running') ORDER BY created_at"
            ).fetchall()
            return [self._import_job(row) for row in rows]

    def update_import_job(self, job_id, **values):
        allowed = {"state", "updated_at", "processed", "messages", "conversations", "error"}
        if not values or set(values) - allowed:
            raise ValueError("invalid import job update")
        assignments = ", ".join(f"{key}=?" for key in values)
        with self._lock:
            conn = self._require_connection()
            conn.execute(f"UPDATE import_jobs SET {assignments} WHERE id=?", (*values.values(), job_id))
            return self._import_job(conn.execute("SELECT * FROM import_jobs WHERE id=?", (job_id,)).fetchone())

    def backup_to(self, destination) -> Path:
        """Write a transactionally consistent SQLite snapshot to ``destination``."""
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
        if temporary.exists():
            temporary.unlink()
        try:
            with self._lock:
                source = self._require_connection()
                backup = sqlite3.connect(temporary, timeout=30.0)
                try:
                    source.backup(backup, pages=256, sleep=0.05)
                    check = backup.execute("PRAGMA quick_check").fetchone()[0]
                    if check != "ok":
                        raise sqlite3.DatabaseError("backup integrity check failed")
                finally:
                    backup.close()
            os.replace(temporary, target)
            return target
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def restore_from(self, source_path) -> None:
        """Replace indexed data from a validated SQLite snapshot, without closing readers."""
        source_path = Path(source_path)
        uri = source_path.resolve().as_uri() + "?mode=ro"
        source = sqlite3.connect(uri, uri=True, timeout=30.0)
        try:
            source.row_factory = sqlite3.Row
            integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError("备份数据库完整性校验失败")
            version = int(source.execute("PRAGMA user_version").fetchone()[0])
            if version > _SCHEMA_VERSION:
                raise ValueError("备份来自更新版本，当前程序不能恢复")
            tables = {row[0] for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if not {"conversations", "messages", "contacts"}.issubset(tables):
                raise ValueError("备份缺少消息索引所需的数据表")
            required_columns = {
                "conversations": {"platform", "account_id", "id", "title", "kind", "avatar_url", "member_count", "last_message", "last_timestamp", "message_count", "extra"},
                "messages": {"platform", "account_id", "id", "conversation_id", "sender_id", "sender_name", "timestamp", "type", "text", "is_self", "media_url", "file_name", "raw_type", "preview", "extra"},
                "contacts": {"platform", "account_id", "id", "name", "avatar_url", "kind", "extra"},
            }
            for table, required in required_columns.items():
                columns = {row[1] for row in source.execute(f'PRAGMA table_info("{table}")')}
                if not required.issubset(columns):
                    raise ValueError("备份数据库结构与消息索引不兼容")
            with self._lock:
                destination = self._require_connection()
                source.backup(destination, pages=256, sleep=0.05)
                self._migrate()
                destination.execute("PRAGMA journal_mode=WAL")
                destination.execute("PRAGMA busy_timeout=30000")
                destination.execute("PRAGMA foreign_keys=ON")
                check = destination.execute("PRAGMA integrity_check").fetchone()[0]
                if check != "ok":
                    raise sqlite3.DatabaseError("restored database integrity check failed")
        finally:
            source.close()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                self._conn.close()
                self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------
    @staticmethod
    def _append_time_bounds(where, params, start, end, alias=""):
        prefix = f"{alias}." if alias else ""
        start_ts = parse_bound(start, end=False)
        end_ts = parse_bound(end, end=True)
        if start_ts is not None:
            where.append(f"{prefix}timestamp >= ?")
            params.append(start_ts)
        if end_ts is not None:
            where.append(f"{prefix}timestamp <= ?")
            params.append(end_ts)
