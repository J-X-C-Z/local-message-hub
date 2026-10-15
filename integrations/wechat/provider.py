"""Local read-only provider for WeChat 4.x; no external HTTP service required."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .vendor.db import WeChatDB, auto_detect_db_dir
from .vendor.media import MediaDownloader
from .image_key_store import WeChatImageKeyStore
from .moments import read_timeline
from .sns_media import resolve_cached_media


ROOT = Path(__file__).resolve().parents[2]
_LOCAL_DATA = Path(os.environ.get("LMH_STORAGE_DIR", ROOT / ".local"))
CONFIG = Path(os.environ.get("WECHAT_CONFIG_FILE", _LOCAL_DATA / "wechat.json"))
RUNTIME = Path(os.environ.get("WECHAT_RUNTIME_DIR", _LOCAL_DATA / "integrations" / "wechat"))
AVATAR_URL_FIELDS = (
    "big_head_img_url", "bigHeadImgUrl", "bigHeadUrl", "big_head_url",
    "small_head_img_url", "smallHeadImgUrl", "smallHeadUrl", "small_head_url",
    "head_img_url", "headImgUrl", "avatar_url", "avatarUrl",
)
AVATAR_HOSTS = {"q.qlogo.cn", "thirdqq.qlogo.cn", "wx.qlogo.cn", "wework.qpic.cn"}
EMOJI_EXTENSIONS = (".gif", ".png", ".webp", ".jpg", ".jpeg")


def media_cache_identity(account_id: str, session_id: str, shard: str, local_id: int) -> str:
    identity = "\0".join((str(account_id), str(session_id), str(shard), str(int(local_id))))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def avatar_database_status(reader: Any) -> dict[str, str]:
    """Report head-image database availability without exposing row data."""
    databases = [rel for rel, path, _size in getattr(reader, "_db_files", ())
                 if Path(path).name.casefold() == "head_image.db"]
    if not databases:
        return {"state": "missing", "message": "未发现头像数据库"}
    unlocked = set(getattr(reader, "_keys", {}))
    if not any(rel in unlocked for rel in databases):
        return {"state": "locked", "message": "头像数据库存在，但当前没有可用密钥"}
    return {"state": "available", "message": "头像数据库密钥可用；同步时读取本机头像"}


def _safe_avatar_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 2048 or value != value.strip():
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname not in AVATAR_HOSTS or
                parsed.username or parsed.password or parsed.port not in (None, 443) or
                parsed.fragment or not parsed.path.startswith("/")):
            return None
    except ValueError:
        return None
    return value


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


class HubMediaDownloader(MediaDownloader):
    """Resolve cached media without scanning client memory or changing source files."""

    def _key_store(self):
        return str(RUNTIME / "cache" / "image_keys.json")

    def _load_persisted_key(self):
        account_id = str(getattr(self.db, "wxid", None) or self.db.account or "local")
        return WeChatImageKeyStore(RUNTIME / "cache" / "image_keys").get(account_id)

    def _persist_key(self, aes_key):
        account_id = str(getattr(self.db, "wxid", None) or self.db.account or "local")
        WeChatImageKeyStore(RUNTIME / "cache" / "image_keys").set(account_id, aes_key)

    def _scan_aes_key(self, *args, **kwargs):
        return None

    def download_image_original(self, *args, **kwargs):
        raise RuntimeError("This integration only reads media already present on disk")


class WeChatProvider:
    platform = "wechat"
    _SOURCE_MEDIA_TYPES = {
        "image": {3}, "voice": {34}, "video": {43},
        "file": {49, 34359738417, 103079215153, 25769803825},
    }

    def __init__(self, data_dir: str | None = None, account: str | None = None):
        try:
            config = json.loads(CONFIG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            config = {}
        data_dir = data_dir or config.get("data_dir") or auto_detect_db_dir()
        account = account or config.get("account")
        cache_dir = config.get('sns_cache_dir') or os.environ.get('WECHAT_SNS_CACHE_DIR')
        if not cache_dir:
            cache_dir = Path.home() / 'Documents' / 'WeFlow' / 'sns_cache'
        self.sns_cache_dir = Path(cache_dir).expanduser()
        emoji_cache_dir = config.get('emoji_cache_dir') or os.environ.get('WECHAT_EMOJI_CACHE_DIR')
        self.emoji_cache_dir = Path(emoji_cache_dir).expanduser() if emoji_cache_dir else (
            Path.home() / 'Documents' / 'WeFlow' / 'Emojis')
        self.reader = LocalReader(db_dir=data_dir, account=account, keys_file=str(CONFIG),
                                  workdir=str(RUNTIME / "cache"))
        self.image_key_store = WeChatImageKeyStore(RUNTIME / "cache" / "image_keys")
        required = [rel for rel, _, _ in self.reader._db_files
                    if Path(rel).name == "session.db" or Path(rel).name.startswith("message_")]
        if not any(self.reader._key_works(rel) for rel in required):
            raise RuntimeError("WeChat databases detected but no usable database keys found. Keep WeChat logged in.")

    def status(self) -> dict[str, Any]:
        reader = self.reader
        return {"platform": self.platform, "state": "connected" if not reader.unkeyed else "partial",
                "database_count": len(reader._db_files), "unlocked_count": len(reader._keys),
                "unavailable_databases": reader.unkeyed,
                "avatar_database": avatar_database_status(reader),
                "image_key_configured": self.image_key_store.has(
                    str(reader.wxid or reader.account or "local")),
                "sessions": len(reader.get_sessions(limit=100000)),
                "message_count": sum(self.message_counts().values())}

    def set_image_key(self, key: str) -> dict[str, bool]:
        account_id = str(self.reader.wxid or self.reader.account or "local")
        downloader = HubMediaDownloader(self.reader)
        if not downloader._validate_key(key):
            raise ValueError("图片密钥未通过本机缓存图片校验")
        return self.image_key_store.set(account_id, key)

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
            columns = {str(row[1]).casefold(): str(row[1])
                       for row in conn.execute("PRAGMA table_info(contact)")}
            username = columns.get("username")
            if not username:
                return []
            selected = {"username": username}
            for output, candidates in (
                ("nickname", ("nick_name", "nickname")),
                ("remark", ("remark",)),
                ("alias", ("alias",)),
                ("local_type", ("local_type",)),
            ):
                source = next((columns.get(candidate.casefold()) for candidate in candidates
                               if columns.get(candidate.casefold())), None)
                if source:
                    selected[output] = source
            avatar_fields = []
            seen_avatar_columns = set()
            for name in AVATAR_URL_FIELDS:
                column = columns.get(name.casefold())
                if column and column.casefold() not in seen_avatar_columns:
                    alias = f"avatar_url_{len(avatar_fields)}"
                    avatar_fields.append((alias, column))
                    selected[alias] = column
                    seen_avatar_columns.add(column.casefold())
            projection = ",".join(
                '"' + column.replace('"', '""') + '" AS "' + name + '"'
                for name, column in selected.items())
            biz_types = {}
            tables = {str(row[0]).casefold() for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "biz_info" in tables:
                biz_columns = {str(row[1]).casefold(): str(row[1])
                               for row in conn.execute('PRAGMA table_info("biz_info")')}
                biz_username = biz_columns.get("username")
                biz_type = biz_columns.get("type")
                if biz_username and biz_type:
                    quote = lambda name: '"' + name.replace('"', '""') + '"'
                    table = quote("biz_info")
                    biz_types = {str(row[0]): row[1] for row in conn.execute(
                        f"SELECT {quote(biz_username)}, {quote(biz_type)} FROM {table}")}
            contacts = []
            for row in conn.execute(f"SELECT {projection} FROM contact"):
                contact_id = row["username"]
                nickname = row["nickname"] if "nickname" in selected else None
                remark = row["remark"] if "remark" in selected else None
                item = {"id": contact_id, "platform": self.platform,
                        "name": remark or nickname or contact_id,
                        "nickname": nickname, "alias": row["alias"] if "alias" in selected else None,
                        "kind": "group" if str(contact_id).endswith("@chatroom") else "contact"}
                if (str(contact_id).startswith("gh_") and "local_type" in selected
                        and row["local_type"] == 1):
                    item["kind"] = "official"
                    biz_type = biz_types.get(str(contact_id))
                    item["official_account_kind"] = {
                        0: "subscription", 1: "service", 2: "enterprise", 3: "enterprise"
                    }.get(int(biz_type)) if str(biz_type).isdigit() else None
                avatar = None
                for field, _column in avatar_fields:
                    avatar = _safe_avatar_url(row[field])
                    if avatar:
                        break
                if avatar:
                    item["avatar_url"] = avatar
                if item.get("official_account_kind") is None:
                    item.pop("official_account_kind", None)
                contacts.append(item)
            return contacts
        finally:
            conn.close()

    def avatar_buffers(self, usernames: list[str]) -> dict[str, bytes]:
        """Read bounded JPEG avatars by exact contact username from the cached-key DB."""
        if not isinstance(usernames, list):
            return {}
        rel = next((rel for rel, path, _ in getattr(self.reader, "_db_files", ())
                    if Path(path).name.casefold() == "head_image.db"), None)
        if rel is None or rel not in getattr(self.reader, "_keys", {}):
            return {}
        unique = list(dict.fromkeys(name for name in usernames
                                    if isinstance(name, str) and name and len(name) <= 512))
        if len(unique) > 50_000:
            unique = unique[:50_000]
        conn = self.reader._open(rel)
        try:
            table = next((str(row[0]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND lower(name)='head_image'")), None)
            if table is None:
                return {}
            quote = lambda name: '"' + name.replace('"', '""') + '"'
            columns = {str(row[1]).casefold() for row in conn.execute(
                f"PRAGMA table_info({quote(table)})")}
            if not {"username", "md5", "image_buffer", "update_time"} <= columns:
                return {}
            result = {}
            total_bytes = 0
            for start in range(0, len(unique), 320):
                batch = unique[start:start + 320]
                if not batch:
                    continue
                placeholders = ",".join("?" for _ in batch)
                cursor = conn.execute(
                    f"SELECT username,image_buffer FROM {quote(table)} "
                    f"WHERE username IN ({placeholders}) AND length(image_buffer) BETWEEN 3 AND ? "
                    "ORDER BY update_time DESC",
                    (*batch, 2 * 1024 * 1024))
                while rows := cursor.fetchmany(4):
                    for row in rows:
                        if row["username"] in result:
                            continue
                        payload = row["image_buffer"]
                        if isinstance(payload, bytes) and payload.startswith(b"\xff\xd8\xff"):
                            if total_bytes + len(payload) > 32 * 1024 * 1024:
                                return result
                            result[row["username"]] = payload
                            total_bytes += len(payload)
                    if total_bytes >= 32 * 1024 * 1024:
                        return result
            return result
        finally:
            conn.close()

    def local_avatar_url(self, username: str) -> str:
        digest = hashlib.sha256(
            f"{self.reader.account or ''}\0{username}".encode("utf-8")).hexdigest()
        return f"hub-avatar://wechat/{digest}"

    def moments(self, *, limit=50, offset=0, usernames=None, keyword='', start_time=0, end_time=0):
        rel = next((rel for rel, _, _ in self.reader._db_files
                    if Path(rel).name.casefold() == 'sns.db' and
                    any(part.casefold() == 'sns' for part in Path(rel).parts)), None)
        if rel is None:
            return {'available': False, 'reason': 'moments_database_missing', 'items': [], 'total': 0}
        if not self.reader._key_works(rel):
            return {'available': False, 'reason': 'moments_database_locked', 'items': [], 'total': 0}
        conn = self.reader._open(rel)
        try:
            result = read_timeline(conn, limit=limit, offset=offset, usernames=usernames,
                                   keyword=keyword, start_time=start_time, end_time=end_time,
                                   nickname_for=self.reader.get_nickname)
        finally:
            conn.close()
        if result.get('available'):
            for post in result.get('items', []):
                for media in post.get('media', []):
                    raw_urls = (media.get('url'), media.get('thumb'))
                    cached = next((found for raw_url in raw_urls
                                   if (found := resolve_cached_media(self.sns_cache_dir, raw_url))), None)
                    media['type'] = cached[2] if cached else 'video' if media.get('type') == 'video' else 'image'
                    media['local_available'] = bool(cached)
                    if cached:
                        media['_local_path'] = str(cached[0])
                        media['mime_type'] = cached[1]
                    for field in ('url', 'thumb', 'token', 'key', 'encIdx', 'md5'):
                        media.pop(field, None)
                    live_photo = media.get('livePhoto')
                    if isinstance(live_photo, dict):
                        live_cached = resolve_cached_media(
                            self.sns_cache_dir, live_photo.get('url'), kind='video')
                        live_photo['type'] = 'video'
                        live_photo['local_available'] = bool(live_cached)
                        if live_cached:
                            live_photo['_local_path'] = str(live_cached[0])
                            live_photo['mime_type'] = live_cached[1]
                        for field in ('url', 'thumb', 'token', 'thumbToken', 'key', 'encIdx', 'md5'):
                            live_photo.pop(field, None)
        return result

    def messages(self, session_id: str, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        result = []
        for row in self.reader.get_messages(session_id, limit=limit, offset=offset):
            shard = row.get("shard", "")
            result.append({"id": f"{session_id}:{shard}:{row['local_id']}", "platform": self.platform,
                           "session_id": session_id, "sender_id": row.get("sender_username", ""),
                           "is_self": row["is_self"], "timestamp": row["create_time"],
                           "sort_seq": row["sort_seq"], "type": row["type"],
                           "content": row["content"], "shard": shard,
                           "local_id": row["local_id"], "local_type": row["local_type"],
                           "server_id": str(row.get("server_id") or "")})
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
            "server_id": str(row.get("server_id") or ""),
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

    def source_media(self, session_id: str, *, media_type: str = "", start_time=None,
                     end_time=None, offset=0, limit=60) -> dict[str, Any]:
        """Page unprojected source media metadata without materializing message content."""
        if not isinstance(session_id, str) or not session_id or len(session_id) > 512:
            raise ValueError("invalid WeChat conversation")
        if media_type and media_type not in self._SOURCE_MEDIA_TYPES:
            raise ValueError("unsupported WeChat source media type")
        types = (self._SOURCE_MEDIA_TYPES[media_type] if media_type else
                 set().union(*self._SOURCE_MEDIA_TYPES.values()))
        result = self.reader.get_media_rows(session_id, types, offset=offset, limit=limit,
                                            start_time=start_time, end_time=end_time)
        items = []
        for row in result["items"]:
            local_type = int(row["local_type"]) & 0xFFFFFFFF
            kind = {3: "image", 34: "voice", 43: "video", 49: "file"}.get(local_type)
            if kind is None:
                continue
            shard = row["shard"]
            sender_id = row.get("sender_id")
            sender = self._sender_names(shard).get(int(sender_id or 0), "")
            items.append({"id": f"{session_id}:{shard}:{row['local_id']}",
                          "platform": self.platform, "conversation_id": session_id,
                          "timestamp": row["create_time"], "sort_seq": row["sort_seq"],
                          "sender_id": sender_id, "sender_name": sender,
                          "type": kind, "text": "", "raw_type": str(local_type),
                          "extra": {"resource_kind": "wechat_source", "shard": shard,
                                    "local_id": row["local_id"],
                                    "local_type": row["local_type"]}})
        return {"items": items, "total": result["total"]}

    def emoji_cache_account_dir(self) -> Path:
        account_id = str(self.reader.account or '').strip()
        if account_id.lower().startswith('accountid_'):
            account_id = account_id.split('_', 1)[0] + '_' + account_id.split('_', 1)[1].split('_', 1)[0]
        else:
            account_id = re.sub(r'_[A-Za-z0-9]{4}$', '', account_id)
        account_dir = Path(self.reader.account_dir).resolve()
        normalized_path = str(account_dir).lower() if os.name == 'nt' else str(account_dir)
        scope = hashlib.sha256(f'{normalized_path}\0{account_id.strip().lower()}'.encode()).hexdigest()[:24]
        return self.emoji_cache_dir / 'accounts' / f'v2-{scope}'

    def resolve_cached_emoji(self, emoji_md5: str) -> Path | None:
        if not isinstance(emoji_md5, str) or not re.fullmatch(r'[a-fA-F0-9]{32}', emoji_md5):
            return None
        root = self.emoji_cache_dir.expanduser().resolve()
        account_dir = self.emoji_cache_account_dir()
        if account_dir.is_symlink():
            return None
        try:
            account_root = account_dir.resolve(strict=True)
            if not account_root.is_dir() or not account_root.is_relative_to(root):
                return None
        except (OSError, RuntimeError):
            return None
        for extension in EMOJI_EXTENSIONS:
            candidate = account_root / f'{emoji_md5}{extension}'
            try:
                if candidate.is_symlink():
                    continue
                resolved = candidate.resolve(strict=True)
                if resolved.is_file() and resolved.is_relative_to(account_root):
                    return resolved
            except (OSError, RuntimeError):
                continue
        return None

    def media(self, session_id: str, local_id: int, shard: str,
              image_tier: str = "mid", video_variant: str | None = None) -> dict[str, Any]:
        if not shard or Path(shard).name != shard:
            raise ValueError("A database shard name is required to identify media uniquely")
        if image_tier not in {"mid", "best", "thumb", "original"}:
            raise ValueError("Image tier must be best, mid, thumb, or original")
        if video_variant not in {None, "poster", "thumbnail"}:
            raise ValueError("Video variant must be poster or thumbnail")
        row = self.reader.get_message_row(session_id, local_id, shard=shard)
        if row is None:
            return {"available": False, "path": None, "reason": "message_not_found"}
        account_id = str(self.reader.wxid or self.reader.account or "local")
        cache_identity = media_cache_identity(account_id, session_id, shard, local_id)
        local_type = int(row["local_type"])
        image_key = None
        if local_type == 3:
            image_key_store = getattr(self, "image_key_store", None) or WeChatImageKeyStore(
                RUNTIME / "cache" / "image_keys")
            image_key = image_key_store.get(account_id)
        downloader = HubMediaDownloader(self.reader, save_dir=str(RUNTIME / "media" / cache_identity),
                                        image_key=image_key)
        if video_variant is not None and local_type != 43:
            raise ValueError("Video variants are only available for WeChat videos")
        if local_type == 43 and video_variant is not None:
            path = downloader.download_video_thumbnail(
                session_id, local_id, shard=shard, thumbnail=video_variant == "thumbnail")
            return {"available": bool(path), "path": path,
                    "reason": None if path else "not_cached_or_key_unavailable"}
        method = {3: "download_image", 34: "download_voice", 43: "download_video",
                  49: "download_file", 34359738417: "download_file",
                  103079215153: "download_file", 25769803825: "download_file"}.get(local_type)
        if not method:
            return {"available": False, "path": None, "reason": "unsupported_media_type"}
        media_args = {"shard": shard}
        if local_type == 3:
            media_args["tier"] = image_tier
        try:
            path = getattr(downloader, method)(session_id, local_id, **media_args)
        except RuntimeError as exc:
            if local_type != 3 or not str(exc).startswith("无法获取图片 AES 密钥"):
                raise
            return {"available": False, "path": None,
                    "reason": "image_key_unavailable",
                    "message": "缺少微信图片密钥，无法解密此图片"}
        if local_type == 34 and path:
            from .voice import voice_media_result

            return voice_media_result(path)
        if local_type == 34:
            status = self.voice_status(session_id, local_id, shard, downloader=downloader)
            return {"available": False, "path": None,
                    "reason": status["reason"], "bytes": status["bytes"]}
        return {"available": bool(path), "path": path, "reason": None if path else "not_cached_or_key_unavailable"}

    def voice_status(self, session_id: str, local_id: int, shard: str, *, downloader=None) -> dict:
        """Explain one indexed voice message without exposing source identifiers."""
        if not shard or Path(shard).name != shard:
            raise ValueError("A database shard name is required to identify voice media")
        rows = self.reader.get_voice_rows(session_id, limit=500, local_id=local_id)
        row = next((item for item in rows if item.get("shard") == shard), None)
        if row is None:
            return {"available": False, "reason": "voice_message_not_found", "bytes": 0}
        if downloader is None:
            account_id = str(self.reader.wxid or self.reader.account or "local")
            cache_identity = media_cache_identity(account_id, session_id, shard, local_id)
            downloader = HubMediaDownloader(
                self.reader, save_dir=str(RUNTIME / "media" / cache_identity))
        known, index = downloader._voice_index(session_id)
        server_id = str(row.get("server_id") or "")
        size = int(index.get(server_id, 0)) if server_id else 0
        reason = downloader._voice_reason(server_id, size, row.get("download_status"), known)
        return {"available": reason == "ok", "reason": reason, "bytes": size}


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
