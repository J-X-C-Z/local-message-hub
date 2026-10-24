from __future__ import annotations

from collections.abc import Iterator
import hashlib
import heapq
from itertools import islice
import ntpath
from pathlib import Path
from typing import Any

from .engine import OUT, SyncEngine, compute_stats, strip_internal


class WeComSnapshotError(RuntimeError):
    MESSAGES = {
        "database_not_decrypted": "无法解密本机企业微信数据库；请核对密钥、账号槽位和客户端版本",
        "message_database_missing": "已读取企微本机目录，但未找到消息数据库；请核对账号槽位和数据版本",
        "snapshot_parse_failed": "企业微信本机快照解析失败；请检查数据目录、密钥和客户端版本",
    }

    def __init__(self, code: str):
        if code not in self.MESSAGES:
            code = "snapshot_parse_failed"
        self.code = code
        self.safe_message = self.MESSAGES[code]
        super().__init__("wecom_snapshot_" + code)


class WeComAdapter:
    """Read-only query facade over the local WeCom snapshot engine."""

    def __init__(self, interval: int = 30, data_root: str | None = None,
                 voice_cache_dir: str | Path | None = None):
        self.engine = SyncEngine(interval=interval, full_root=data_root)
        self.voice_cache_dir = Path(voice_cache_dir) if voice_cache_dir else Path(OUT).parent / "media" / "decoded-voice"
        self._loaded = False
        self._messages_released = False
        self._released_media = {}
        self._released_ids = set()
        self._last_counts = None

    def refresh(self) -> dict[str, Any]:
        """Copy source databases into the ignored local snapshot area and parse them."""
        self.engine.run_once()
        self._loaded = True
        self._messages_released = False
        self._released_media = {}
        self._released_ids = set()
        info = self.engine.info()
        if info.get("error"):
            code = {
                "未找到可解密的库（客户端可能未运行过）": "database_not_decrypted",
                "未找到可用 message.db": "message_database_missing",
            }.get(info["error"], "snapshot_parse_failed")
            raise WeComSnapshotError(code)
        self._last_counts = self.get_counts()
        return dict(self._last_counts)

    def _raw_messages(self) -> Iterator[dict[str, Any]]:
        if not self._loaded or getattr(self, "_messages_released", False):
            self.refresh()
        return self.engine.iter_messages()

    def _messages(self) -> Iterator[dict[str, Any]]:
        return (strip_internal(item) for item in self._raw_messages())

    def release_message_cache(self) -> dict[str, Any]:
        """Opt-in after Hub ingestion; native message queries reload on demand."""
        if not self._loaded or getattr(self, "_messages_released", False):
            return {"released": False, "messages": len(getattr(self, "_released_ids", ()))}
        rows = self.engine.iter_messages()
        ids = set()
        media = {}
        for row in rows:
            message_id = str(row.get("message_id"))
            if message_id in ids:
                continue
            ids.add(message_id)
            if row.get("media") and message_id not in media:
                media[message_id] = row["media"]
        summary = self.engine.release_messages()
        self._released_ids = ids
        self._released_media = media
        self._messages_released = True
        return {"released": True, **summary, "media_messages": len(media)}

    def restore_cached_media(self) -> bool:
        """Open a completed media snapshot without refreshing source databases."""
        media = self.engine.restore_cached_media_context()
        if media is None:
            return False
        self._released_media = media
        self._released_ids = set(media)
        self._messages_released = True
        self._loaded = True
        return True

    def iter_message_batches(self, batch_size: int = 500) -> Iterator[list[dict[str, Any]]]:
        """Refresh once, then yield bounded batches for ingestion by the hub."""
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not self._loaded:
            self.refresh()
        messages = self._messages()
        while batch := list(islice(messages, batch_size)):
            yield batch

    def list_conversations(self, limit: int = 200) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for message in self._raw_messages():
            conversation_id = str(message.get("conversation_id") or "")
            if not conversation_id:
                continue
            item = grouped.setdefault(conversation_id, {
                "conversation_id": conversation_id,
                "name": message.get("conversation") or conversation_id,
                "message_count": 0,
                "last_time": None,
                "participants": set(),
            })
            item["message_count"] += 1
            item["participants"].add(str(message.get("sender_id") or ""))
            sent_at = message.get("send_time")
            if sent_at is not None and (item["last_time"] is None or sent_at > item["last_time"]):
                item["last_time"] = sent_at
        conversations = [
            {**item, "participants": len(item["participants"])}
            for item in grouped.values()
        ]
        conversations.sort(key=lambda item: item["last_time"] or 0, reverse=True)
        return conversations[:max(0, limit)]

    def conversation_avatar_buffers(self, conversation_ids) -> dict[str, bytes]:
        if not self._loaded:
            self.refresh()
        reader = getattr(self.engine, "conversation_avatar_buffers", None)
        return reader(conversation_ids) if callable(reader) else {}

    @staticmethod
    def local_avatar_url(conversation_id: str) -> str:
        identity = "wecom\0local\0" + str(conversation_id)
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return "hub-avatar://wecom/" + digest

    def contacts(self) -> list[dict[str, Any]]:
        """Return unique senders, keyed by provider sender ID."""
        grouped: dict[str, dict[str, Any]] = {}
        for message in self._raw_messages():
            contact_id = str(message.get("sender_id") or "").strip()
            name = str(message.get("sender") or "").strip()
            if not contact_id:
                continue
            item = grouped.setdefault(contact_id, {
                "contact_id": contact_id,
                "name": name or contact_id,
                "message_count": 0,
                "conversations": set(),
                "last_time": None,
                "is_self": bool(message.get("me")),
            })
            item["message_count"] += 1
            conversation_id = str(message.get("conversation_id") or "")
            if conversation_id:
                item["conversations"].add(conversation_id)
            sent_at = message.get("send_time")
            if sent_at is not None and (item["last_time"] is None or sent_at > item["last_time"]):
                item["last_time"] = sent_at
        contacts = [
            {**item, "conversation_count": len(item.pop("conversations"))}
            for item in grouped.values()
        ]
        contacts.sort(key=lambda item: item["name"].casefold())
        return contacts

    def members(self, conversation_id: str, account_id: str | None = None,
                limit: int = 1000, query: str = "") -> dict[str, Any]:
        """Return a bounded roster from the configured local WeCom snapshot."""
        conversation_id = str(conversation_id or "").strip()
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        # The hub deliberately identifies this single configured local account as
        # "local"; the provider slot ID is an implementation detail, not an API.
        if account_id is not None and str(account_id).strip() != "local":
            raise ValueError("account_id does not match the configured WeCom account")
        if not self._loaded:
            self.refresh()

        content = self.engine.get_content()
        group_members = content.get("group_members", {}) if isinstance(content, dict) else {}
        rows = group_members.get(conversation_id, []) if isinstance(group_members, dict) else []
        directory_avatars = {
            str(member.get("user_id") or ""): member.get("avatar")
            for member in content.get("members", []) if isinstance(member, dict)
            and member.get("user_id") and member.get("avatar")
        } if isinstance(content, dict) else {}
        needle = str(query or "").casefold().strip()
        normalized = []
        seen: set[str] = set()
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or str(row.get("conversation_id") or "") != conversation_id:
                continue
            member_id = str(row.get("user_id") or "").strip()
            if not member_id or member_id in seen:
                continue
            name = str(row.get("name") or row.get("nick_name") or row.get("remark") or member_id).strip()
            remark = str(row.get("remark") or "")
            if needle and needle not in f"{member_id} {name} {remark}".casefold():
                continue
            seen.add(member_id)
            normalized.append({
                "id": member_id,
                "name": name,
                "avatar_url": row.get("avatar_url") or row.get("avatarUrl") or directory_avatars.get(member_id),
                "role": "member",
                "remark": str(row.get("remark") or ""),
                "join_time": row.get("join_time"),
                "account_id": "local",
            })
        normalized.sort(key=lambda item: (item["name"].casefold(), item["id"]))
        return {"items": normalized[:limit], "total": len(normalized)}

    def get_messages(
        self,
        conversation_id: str,
        limit: int = 100,
        before_timestamp: float | None = None,
    ) -> list[dict[str, Any]]:
        conversation_id = str(conversation_id)
        rows = (
            message for message in self._raw_messages()
            if str(message.get("conversation_id")) == conversation_id
            and (before_timestamp is None or (message.get("send_time") or 0) < before_timestamp)
        )
        return [strip_internal(row) for row in heapq.nlargest(
            max(0, limit), rows, key=lambda item: item.get("send_time") or 0)]

    def search_messages(
        self,
        query: str,
        conversation_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        needle = query.casefold().strip()
        if not needle:
            return []
        matches = (m for m in self._raw_messages()
                   if (conversation_id is None or str(m.get("conversation_id")) == str(conversation_id))
                   and needle in str(m.get("text") or "").casefold())
        return [strip_internal(row) for row in heapq.nlargest(
            max(0, limit), matches, key=lambda item: item.get("send_time") or 0)]

    def list_media(self, conversation_id: str | None = None) -> list[dict[str, Any]]:
        out = []
        for message in self._messages():
            if conversation_id is not None and str(message.get("conversation_id")) != str(conversation_id):
                continue
            for media in message.get("media") or []:
                out.append({
                    **media,
                    "message_id": message.get("message_id"),
                    "conversation_id": message.get("conversation_id"),
                })
        return out

    def media(self, message_id: str, resource_index: int | None = None) -> dict[str, Any]:
        """Resolve an attachment only within the ignored local media root."""
        message_id = str(message_id)
        if getattr(self, "_messages_released", False):
            message = ({"media": self._released_media.get(message_id, [])}
                       if message_id in self._released_ids else None)
        else:
            message = next((m for m in self._raw_messages()
                           if str(m.get("message_id")) == message_id), None)
        if message is None:
            return {"available": False, "path": None, "reason": "message_not_found"}
        attachments = message.get("media") or []
        if not attachments:
            return {"available": False, "path": None, "reason": "no_attachment_metadata"}

        if resource_index is not None:
            if type(resource_index) is not int or not 0 <= resource_index < 50:
                return {"available": False, "path": None, "reason": "invalid_resource_index"}
            if resource_index >= len(attachments):
                return {"available": False, "path": None, "reason": "resource_not_found"}
            attachments = [attachments[resource_index]]

        cache_root = Path(getattr(self.engine, 'cache_root', OUT)).resolve()
        roots = ((cache_root, '_local_cache_path'), (Path(OUT).resolve(), 'p'))
        for attachment in attachments:
            for root, key in roots:
                rel = str(attachment.get(key) or '').replace('\\', '/')
                if not rel or rel.startswith('/') or ntpath.isabs(rel):
                    continue
                parts = rel.split('/')
                if any(part in ('', '.', '..') for part in parts):
                    continue
                candidate = root.joinpath(*parts).resolve()
                try:
                    safe_rel = candidate.relative_to(root).as_posix()
                except ValueError:
                    continue
                if candidate.is_file():
                    with candidate.open('rb') as media_file:
                        header = media_file.read(7)
                    if header.startswith((b'#!SILK', b'\x02#!SILK')):
                        from integrations.wechat.voice import voice_media_result
                        return voice_media_result(str(candidate), self.voice_cache_dir)
                    path = str(candidate) if key == '_local_cache_path' else safe_rel
                    return {"available": True, "path": path,
                            "name": attachment.get("n") or candidate.name, "reason": None}
        return {"available": False, "path": None, "reason": "media_file_missing_or_unsafe"}

    def get_counts(self) -> dict[str, Any]:
        if getattr(self, "_messages_released", False) and self._last_counts is not None:
            return dict(self._last_counts)
        messages = self.engine.snapshot_messages()
        media = self.engine.snapshot_media()
        stats = compute_stats(messages, media)
        return {
            "messages": stats["total"],
            "conversations": stats["conversations"],
            "people": stats["people"],
            "media": stats["media_files"],
            "by_type": stats["by_type"],
        }

