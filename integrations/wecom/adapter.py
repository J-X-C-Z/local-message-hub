from __future__ import annotations

from collections.abc import Iterator
import ntpath
from pathlib import Path
from typing import Any

from .engine import OUT, SyncEngine, compute_stats, strip_internal


class WeComAdapter:
    """Read-only query facade over the local WeCom snapshot engine."""

    def __init__(self, interval: int = 30, data_root: str | None = None):
        self.engine = SyncEngine(interval=interval, full_root=data_root)
        self._loaded = False

    def refresh(self) -> dict[str, Any]:
        """Copy source databases into the ignored local snapshot area and parse them."""
        self.engine.run_once()
        self._loaded = True
        info = self.engine.info()
        if info.get("error"):
            raise RuntimeError("WeCom snapshot could not be parsed")
        return self.get_counts()

    def _messages(self) -> list[dict[str, Any]]:
        if not self._loaded:
            self.refresh()
        return [strip_internal(item) for item in self.engine.snapshot_messages()]

    def iter_message_batches(self, batch_size: int = 500) -> Iterator[list[dict[str, Any]]]:
        """Refresh once, then yield bounded batches for ingestion by the hub."""
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not self._loaded:
            self.refresh()
        snapshot = self.engine.snapshot_messages()
        for offset in range(0, len(snapshot), batch_size):
            yield [strip_internal(row) for row in snapshot[offset:offset + batch_size]]

    def list_conversations(self, limit: int = 200) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for message in self._messages():
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

    def contacts(self) -> list[dict[str, Any]]:
        """Return unique senders, keyed by provider sender ID."""
        grouped: dict[str, dict[str, Any]] = {}
        for message in self._messages():
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

    def get_messages(
        self,
        conversation_id: str,
        limit: int = 100,
        before_timestamp: float | None = None,
    ) -> list[dict[str, Any]]:
        conversation_id = str(conversation_id)
        rows = [
            message for message in self._messages()
            if str(message.get("conversation_id")) == conversation_id
            and (before_timestamp is None or (message.get("send_time") or 0) < before_timestamp)
        ]
        rows.sort(key=lambda item: item.get("send_time") or 0, reverse=True)
        return rows[:max(0, limit)]

    def search_messages(
        self,
        query: str,
        conversation_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        needle = query.casefold().strip()
        if not needle:
            return []
        rows = self._messages()
        if conversation_id is not None:
            rows = [m for m in rows if str(m.get("conversation_id")) == str(conversation_id)]
        matches = [m for m in rows if needle in str(m.get("text") or "").casefold()]
        matches.sort(key=lambda item: item.get("send_time") or 0, reverse=True)
        return matches[:max(0, limit)]

    def list_media(self, conversation_id: str | None = None) -> list[dict[str, Any]]:
        messages = self._messages()
        allowed = None
        if conversation_id is not None:
            allowed = {
                str(message.get("message_id")) for message in messages
                if str(message.get("conversation_id")) == str(conversation_id)
            }
        out = []
        for message in messages:
            if allowed is not None and str(message.get("message_id")) not in allowed:
                continue
            for media in message.get("media") or []:
                out.append({
                    **media,
                    "message_id": message.get("message_id"),
                    "conversation_id": message.get("conversation_id"),
                })
        return out

    def media(self, message_id: str) -> dict[str, Any]:
        """Resolve the first attachment only within the ignored local media root."""
        message_id = str(message_id)
        message = next((m for m in self._messages()
                       if str(m.get("message_id")) == message_id), None)
        if message is None:
            return {"available": False, "path": None, "reason": "message_not_found"}
        attachments = message.get("media") or []
        if not attachments:
            return {"available": False, "path": None, "reason": "no_attachment_metadata"}

        root = Path(OUT).resolve()
        for attachment in attachments:
            rel = str(attachment.get("p") or "").replace("\\", "/")
            if not rel or rel.startswith("/") or ntpath.isabs(rel):
                continue
            parts = rel.split("/")
            if any(part in ("", ".", "..") for part in parts):
                continue
            candidate = root.joinpath(*parts).resolve()
            try:
                safe_rel = candidate.relative_to(root).as_posix()
            except ValueError:
                continue
            if candidate.is_file():
                return {"available": True, "path": safe_rel, "reason": None}
        return {"available": False, "path": None, "reason": "media_file_missing_or_unsafe"}

    def get_counts(self) -> dict[str, Any]:
        messages = [strip_internal(item) for item in self.engine.snapshot_messages()]
        media = self.engine.snapshot_media()
        stats = compute_stats(messages, media)
        return {
            "messages": stats["total"],
            "conversations": stats["conversations"],
            "people": stats["people"],
            "media": stats["media_files"],
            "by_type": stats["by_type"],
        }

