"""Local read-only provider for WeChat 4.x; no external HTTP service required."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .vendor.db import WeChatDB, auto_detect_db_dir


ROOT = Path(__file__).resolve().parents[2]
_LOCAL_DATA = Path(os.environ.get("LMH_STORAGE_DIR", ROOT / ".local"))
CONFIG = Path(os.environ.get("WECHAT_CONFIG_FILE", _LOCAL_DATA / "wechat.json"))
RUNTIME = Path(os.environ.get("WECHAT_RUNTIME_DIR", _LOCAL_DATA / "integrations" / "wechat"))


class LocalReader(WeChatDB):
    def _shard_label(self, conn):
        cache_name = super()._shard_label(conn)
        return cache_name.rsplit("__", 1)[-1]

    def _sender_names(self, shard):
        if not hasattr(self, "_local_sender_cache"):
            self._local_sender_cache = {}
        if shard in self._local_sender_cache:
            return self._local_sender_cache[shard]
        rel = next((rel for rel, path, _ in self._db_files if Path(path).name == shard), None)
        if not rel:
            return {}
        conn = self._open(rel)
        try:
            table = conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                 "AND name LIKE 'Name2Id%' ORDER BY name DESC LIMIT 1").fetchone()
            if table:
                identifier = table[0].replace('"', '""')
                names = dict(conn.execute(f'SELECT rowid,user_name FROM "{identifier}"'))
            else:
                names = {}
        finally:
            conn.close()
        self._local_sender_cache[shard] = names
        return names

    def _msg_row_to_dict(self, row):
        result = super()._msg_row_to_dict(row)
        sender = self._sender_names(result["shard"]).get(int(result["sender_id"] or 0), "")
        result["sender_username"] = sender
        result["is_self"] = sender == self.wxid if sender else None
        result["local_type"] = int(row["local_type"])
        return result

    def _stable_key_dirs(self):
        return []

    def _load_key_cache(self, path):
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            return {k: bytes.fromhex(v) for k, v in payload.get("database_keys", {}).items()}
        except (OSError, ValueError, TypeError):
            return {}

    def _save_keys(self):
        if not self._keys:
            return
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        try:
            payload = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        payload.update({"data_dir": self.db_dir, "account": self.account,
                        "database_keys": {k: v.hex() for k, v in self._keys.items()}})
        temporary = CONFIG.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, CONFIG)


class WeChatProvider:
    platform = "wechat"

    def __init__(self, data_dir: str | None = None, account: str | None = None):
        try:
            config = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            config = {}
        data_dir = data_dir or config.get("data_dir") or auto_detect_db_dir()
        account = account or config.get("account")
        self.reader = LocalReader(db_dir=data_dir, account=account, keys_file=str(CONFIG),
                                  workdir=str(RUNTIME / "cache"))
        required = [rel for rel, _, _ in self.reader._db_files
                    if Path(rel).name == "session.db" or Path(rel).name.startswith("message_")]
        if not any(self.reader._key_works(rel) for rel in required):
            raise RuntimeError("WeChat databases detected but no usable database keys found. Keep WeChat logged in.")

    def status(self) -> dict[str, Any]:
        reader = self.reader
        return {"platform": self.platform, "state": "connected" if not reader.unkeyed else "partial",
                "database_count": len(reader._db_files), "unlocked_count": len(reader._keys),
                "unavailable_databases": reader.unkeyed,
                "sessions": len(reader.get_sessions(limit=100000)),
                "message_count": sum(self.message_counts().values())}

    def sessions(self, limit: int = 100000, include_counts: bool = True) -> list[dict[str, Any]]:
        result = []
        counts = self.message_counts() if include_counts else {}
        for row in self.reader.get_sessions(limit=limit):
            username = row["username"]
            result.append({"id": username, "platform": self.platform,
                           "title": self.reader.get_nickname(username),
                           "kind": "group" if username.endswith("@chatroom") else "direct",
                           "unread": row["unread"], "last_message": row["summary"],
                           "last_timestamp": row["last_time"],
                           "message_count": counts.get(hashlib.md5(username.encode()).hexdigest(), 0)})
        return result

    def message_counts(self) -> dict[str, int]:
        counts = {}
        for rel in self.reader._message_dbs():
            conn = self.reader._open(rel)
            try:
                tables = [row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")]
                for table in tables:
                    if len(table) != 36 or any(c not in "0123456789abcdefABCDEF" for c in table[4:]):
                        continue
                    count = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                    counts[table[4:]] = counts.get(table[4:], 0) + count
            finally:
                conn.close()
        return counts

    def contacts(self) -> list[dict[str, Any]]:
        conn = self.reader._contact_conn()
        if conn is None:
            return []
        try:
            rows = conn.execute("SELECT username,nick_name,remark,alias FROM contact").fetchall()
            return [{"id": row["username"], "platform": self.platform,
                     "name": row["remark"] or row["nick_name"] or row["username"],
                     "nickname": row["nick_name"], "alias": row["alias"]} for row in rows]
        finally:
            conn.close()

    def messages(self, session_id: str, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        result = []
        for row in self.reader.get_messages(session_id, limit=limit, offset=offset):
            shard = row.get("shard", "")
            result.append({"id": f"{session_id}:{shard}:{row['local_id']}", "platform": self.platform,
                           "session_id": session_id, "sender_id": row.get("sender_username", ""),
                           "is_self": row["is_self"], "timestamp": row["create_time"],
                           "sort_seq": row["sort_seq"], "type": row["type"],
                           "content": row["content"], "shard": shard,
                           "local_id": row["local_id"], "local_type": row["local_type"]})
        return result

    def messages_after(self, session_id: str, cursor: dict[str, Any] | None = None,
                       limit: int = 200) -> dict[str, Any]:
        """Read an ascending page after an opaque, conversation-scoped cursor.

        Unlike ``messages``' display-oriented offset API, this source API is
        suitable for forward synchronization and interruption recovery.
        """
        if limit <= 0:
            return {"messages": [], "next_cursor": cursor, "has_more": False}
        source_cursor = None
        if cursor is not None:
            if (not isinstance(cursor, dict) or cursor.get("version") != 1 or
                    cursor.get("session_id") != session_id):
                raise ValueError("invalid WeChat message cursor")
            source_cursor = {key: cursor.get(key) for key in ("sort_seq", "shard", "local_id")}

        rows = self.reader.get_messages_after(session_id, source_cursor, limit + 1)
        has_more = len(rows) > limit
        rows = rows[:limit]
        messages = [{
            "id": f"{session_id}:{row.get('shard', '')}:{row['local_id']}",
            "platform": self.platform,
            "session_id": session_id,
            "sender_id": row.get("sender_username", ""),
            "is_self": row.get("is_self"),
            "timestamp": row["create_time"],
            "sort_seq": row["sort_seq"],
            "type": row["type"],
            "content": row["content"],
            "shard": row.get("shard", ""),
            "local_id": row["local_id"],
            "local_type": row["local_type"],
        } for row in rows]
        next_cursor = cursor
        if rows:
            last = rows[-1]
            next_cursor = {
                "version": 1,
                "session_id": session_id,
                "sort_seq": last["sort_seq"],
                "shard": last["shard"],
                "local_id": last["local_id"],
            }
        return {"messages": messages, "next_cursor": next_cursor, "has_more": has_more}

    def message_count(self, session_id: str) -> int:
        tables = self.reader._msg_conns(session_id)
        try:
            return sum(conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                       for conn, table in tables)
        finally:
            for conn, _ in tables:
                conn.close()

    def media(self, session_id: str, local_id: int, shard: str) -> dict[str, Any]:
        from .vendor.media import MediaDownloader

        class LocalMedia(MediaDownloader):
            def _key_store(self):
                return str(CONFIG)

            def _load_persisted_key(self):
                try:
                    key = json.loads(CONFIG.read_text(encoding="utf-8")).get("image_key")
                    return key if key and self._validate_key(key) else None
                except (OSError, ValueError):
                    return None

            def _persist_key(self, aes_key):
                try:
                    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    payload = {}
                payload["image_key"] = aes_key
                temporary = CONFIG.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                os.replace(temporary, CONFIG)

            def download_image_original(self, *args, **kwargs):
                raise RuntimeError("This integration only reads media already present on disk")

        if not shard or Path(shard).name != shard:
            raise ValueError("A database shard name is required to identify media uniquely")
        row = self.reader.get_message_row(session_id, local_id, shard=shard)
        if row is None:
            return {"available": False, "path": None, "reason": "message_not_found"}
        cache_identity = hashlib.sha256(f"{session_id}:{shard}:{local_id}".encode()).hexdigest()
        downloader = LocalMedia(self.reader, save_dir=str(RUNTIME / "media" / cache_identity))
        local_type = int(row["local_type"]) & 0xFFFFFFFF
        method = {3: "download_image", 34: "download_voice", 43: "download_video",
                  49: "download_file"}.get(local_type)
        if not method:
            return {"available": False, "path": None, "reason": "unsupported_media_type"}
        path = getattr(downloader, method)(session_id, local_id, shard=shard)
        if local_type == 34 and path:
            from .voice import voice_media_result

            return voice_media_result(path)
        return {"available": bool(path), "path": path, "reason": None if path else "not_cached_or_key_unavailable"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["status", "sessions", "contacts", "messages"])
    parser.add_argument("--session")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    provider = WeChatProvider()
    if args.command == "messages":
        if not args.session:
            parser.error("--session is required")
        result = provider.messages(args.session, args.limit, args.offset)
    elif args.command == "sessions":
        result = provider.sessions(args.limit)
    else:
        result = getattr(provider, args.command)()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
