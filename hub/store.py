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

#: UTC+08:00, the timezone used for all human-facing date interpretation.
CST = timezone(timedelta(hours=8))

_SCHEMA_VERSION = 7

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
        return text
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
_KNOWN_KEYS.update({"preview", "extra", "conversation_title"})


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
        keys = row.keys()
        if "conversation_title" in keys:
            data["conversation_title"] = row["conversation_title"]
        if "conversation_kind" in keys:
            data["conversation_kind"] = row["conversation_kind"]
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
        conversations = list(conversations or [])
        messages = list(messages or [])
        contacts = list(contacts or [])

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
                _dump_extra(contact),
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
                       COALESCE(cm.archived, 0) AS archived
                FROM conversations c
                LEFT JOIN conversation_metadata cm
                  ON cm.platform = c.platform AND cm.account_id = c.account_id
                 AND cm.conversation_id = c.id
                WHERE 1=1{self._scope_clause(platform, 'c.platform')[0]}
                  {"AND c.title LIKE ? ESCAPE '\\'" if q else ""}
                ORDER BY (c.last_timestamp IS NULL), c.last_timestamp DESC, c.id ASC
                LIMIT ? OFFSET ?
                """,
                self._scope_clause(platform, 'c.platform')[1] + ([ _like_pattern(q)] if q else []) + [limit, offset],
            ).fetchall()
        return {
            "items": [self._row_to_conversation(r) for r in rows],
            "total": int(total),
        }

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
            cursor_sql = " AND (timestamp < ? OR (timestamp = ? AND rowid < ?))"
            cursor_params = [decoded_cursor[0], decoded_cursor[0], decoded_cursor[1]]
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
        items = [self._row_to_message(r) for r in rows]
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
        return self._row_to_message(row) if row else None

    def message_context(self, platform, account_id, message_id, before=5, after=5):
        before, after = min(50, max(0, int(before))), min(50, max(0, int(after)))
        with self._lock:
            conn = self._require_connection()
            target = conn.execute(
                "SELECT rowid, * FROM messages WHERE platform=? AND account_id=? AND id=?",
                (_sid(platform), _sid(account_id), _sid(message_id)),
            ).fetchone()
            if not target:
                return None
            scope = (target["platform"], target["account_id"], target["conversation_id"])
            total = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE platform=? AND account_id=? AND conversation_id=?",
                scope,
            ).fetchone()[0]
            before_total = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE platform=? AND account_id=? AND conversation_id=? AND (timestamp < ? OR (timestamp = ? AND rowid < ?))",
                (*scope, target["timestamp"], target["timestamp"], target["rowid"]),
            ).fetchone()[0]
            base = """
                SELECT m.*, c.title AS conversation_title, c.kind AS conversation_kind
                FROM messages m LEFT JOIN conversations c
                  ON c.platform=m.platform AND c.account_id=m.account_id AND c.id=m.conversation_id
                WHERE m.platform=? AND m.account_id=? AND m.conversation_id=?
            """
            before_rows = conn.execute(
                base + " AND (m.timestamp < ? OR (m.timestamp = ? AND m.rowid < ?)) ORDER BY m.timestamp DESC, m.rowid DESC LIMIT ?",
                (*scope, target["timestamp"], target["timestamp"], target["rowid"], before),
            ).fetchall()
            after_rows = conn.execute(
                base + " AND (m.timestamp > ? OR (m.timestamp = ? AND m.rowid > ?)) ORDER BY m.timestamp ASC, m.rowid ASC LIMIT ?",
                (*scope, target["timestamp"], target["timestamp"], target["rowid"], after),
            ).fetchall()
            current = conn.execute(
                base + " AND m.rowid=?", (*scope, target["rowid"])
            ).fetchone()
        rows = list(reversed(before_rows)) + [current] + after_rows
        return {
            "items": [self._row_to_message(row) for row in rows],
            "total": int(total), "target_index": len(before_rows),
            "has_more_before": before_total > len(before_rows),
            "has_more_after": total - before_total - 1 > len(after_rows),
        }

    def search(self, q, platform="all", type="", start="", end="", limit=100, offset=0, cursor=None):
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
            cursor_sql = (" AND " if where_sql else " WHERE ") + "(m.timestamp < ? OR (m.timestamp = ? AND m.rowid < ?))"
            cursor_params = [decoded_cursor[0], decoded_cursor[0], decoded_cursor[1]]
        with self._lock:
            conn = self._require_connection()
            total = conn.execute(
                f"SELECT COUNT(*) FROM messages m {where_sql}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"""
                SELECT m.rowid AS _cursor_rowid, m.*, c.title AS conversation_title, c.kind AS conversation_kind
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
        items = [self._row_to_message(r) for r in rows]
        next_cursor = _encode_cursor(rows[-1]["timestamp"], rows[-1]["_cursor_rowid"],
                                     _cursor_scope([q, platform, type, start, end])) if has_more and rows else None
        return {
            "items": items,
            "total": total,
            "has_more": has_more if decoded_cursor or offset else offset + len(items) < total,
            "next_cursor": next_cursor,
        }

    def contacts(self, platform="all", q="", limit=200, offset=0):
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        where, params = ["1=1"], []
        scope, scope_params = self._scope_clause(platform, "c.platform")
        if scope:
            where.append(scope[5:])
            params += scope_params
        if q:
            where.append("c.name LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(q))
        where_sql = "WHERE " + " AND ".join(where)
        with self._lock:
            conn = self._require_connection()
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
                ORDER BY c.name COLLATE NOCASE ASC, c.id ASC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()
        return {
            "items": [self._row_to_contact(r) for r in rows],
            "total": int(total),
        }

    def media(self, platform="all", type="", q="", limit=100, offset=0):
        limit = max(0, int(limit))
        offset = max(0, int(offset))
        placeholders = ",".join("?" for _ in MEDIA_TYPES)
        where = [f"m.type IN ({placeholders})"]
        params = list(MEDIA_TYPES)
        scope, scope_params = self._scope_clause(platform, column="m.platform")
        if scope:
            where.append(scope[5:])
            params += scope_params
        if type:
            where.append("m.type = ?")
            params.append(type)
        if q:
            where.append("c.title LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(q))
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
                SELECT m.*, c.title AS conversation_title, c.kind AS conversation_kind
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
            "items": [self._row_to_message(r) for r in rows],
            "total": int(total),
        }

    def stats(self, platform="all"):
        with self._lock:
            conn = self._require_connection()
            scope, scope_params = self._scope_clause(platform)
            media_placeholders = ",".join("?" for _ in MEDIA_TYPES)

            message_count = conn.execute(
                f"SELECT COUNT(*) FROM messages WHERE 1=1{scope}", scope_params
            ).fetchone()[0]
            conversation_count = conn.execute(
                f"SELECT COUNT(*) FROM conversations WHERE 1=1{scope}", scope_params
            ).fetchone()[0]
            contact_count = conn.execute(
                f"SELECT COUNT(*) FROM contacts WHERE 1=1{scope}", scope_params
            ).fetchone()[0]
            media_count = conn.execute(
                f"SELECT COUNT(*) FROM messages WHERE type IN ({media_placeholders}){scope}",
                list(MEDIA_TYPES) + scope_params,
            ).fetchone()[0]

            per_type = {
                row["type"] or "unknown": int(row["c"])
                for row in conn.execute(
                    f"SELECT type, COUNT(*) AS c FROM messages WHERE 1=1{scope} GROUP BY type",
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
