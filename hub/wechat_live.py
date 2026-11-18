"""Low-overhead incremental receive path for the local WeChat database."""

from __future__ import annotations

import hashlib
import threading
import time


MAX_SESSIONS_PER_TICK = 16
MESSAGE_PAGE_SIZE = 500
NEW_CONVERSATION_SEED = 200
MAX_PAGES_PER_SESSION = 10
NAMES_REFRESH_SECONDS = 600


def _default_provider():
    from integrations.wechat.provider import WeChatProvider
    return WeChatProvider()


def _integer(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class WeChatLiveReceiver:
    """Index new WeChat rows without continuously scanning message shards."""

    def __init__(self, hub, provider_factory=None, interval=2.0):
        self.hub = hub
        self.provider_factory = provider_factory or _default_provider
        self.interval = min(max(float(interval), 1.0), 30.0)
        self.provider = None
        self.account_id = ""
        self.signature = None
        self.fingerprints = {}
        self.sessions = {}
        self.pending = {}
        self.names = {}
        self.names_loaded_at = 0.0
        self.ingested_total = 0
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.RLock()

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="wechat-live-receive", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread:
            thread.join(timeout=5)

    def _run(self):
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)

    def _publish(self, state, error="", changed=0):
        now = int(time.time())
        status = {
            "state": state,
            "account_id": self.account_id or None,
            "last_scan": now,
            "last_ingest": now if changed else None,
            "ingested_total": self.ingested_total,
            "changed_sessions": changed,
            "error": error,
        }
        with self.hub.lock:
            previous = dict(self.hub.sources["wechat"].get("live_receive") or {})
        if status["last_ingest"] is None:
            status["last_ingest"] = previous.get("last_ingest")
        self.hub.update("wechat", live_receive=status)

    def _enabled(self):
        return (self.hub.capability_enabled("wechat_live_receive")
                and self.hub.capability_enabled("sync")
                and self.hub.settings.get("auto_sync", True) is not False)

    def _ensure_provider(self):
        if self.provider is None:
            self.provider = self.provider_factory()
        account = str(
            getattr(self.provider.reader, "wxid", "")
            or getattr(self.provider.reader, "account", "")
            or "local")
        if account != self.account_id:
            self.account_id = account
            self.signature = None
            self.fingerprints.clear()
            self.sessions.clear()
            self.pending.clear()
            self.names.clear()
            self.names_loaded_at = 0.0
        return self.provider

    def _refresh_names(self, provider, changed):
        if not changed or (
                self.names
                and time.monotonic() - self.names_loaded_at < NAMES_REFRESH_SECONDS):
            return
        try:
            contacts = provider.contacts()
        except Exception:
            contacts = []
        self.names = {
            str(contact["id"]): str(contact.get("name") or contact["id"])
            for contact in contacts
            if isinstance(contact, dict) and contact.get("id")
        }
        self.names_loaded_at = time.monotonic()

    @staticmethod
    def _fingerprint(session):
        material = "\0".join((
            str(session.get("username") or ""),
            str(session.get("last_time") or ""),
            str(session.get("unread") or ""),
            str(session.get("summary") or ""),
        ))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _changed_sessions(self, sessions):
        current = {}
        changed = []
        first_snapshot = not self.fingerprints
        for session in sessions:
            username = str(session.get("username") or "")
            if not username:
                continue
            fingerprint = self._fingerprint(session)
            current[username] = fingerprint
            if first_snapshot:
                checkpoint = self.hub.store.sync_checkpoint(
                    "wechat", self.account_id, username)
                conversation = self.hub.store.get_conversation(
                    "wechat", self.account_id, username)
                cursor = checkpoint.get("cursor") if checkpoint else None
                session_time = _integer(session.get("last_time"))
                indexed_time = _integer(conversation.get("last_timestamp")) if conversation else 0
                if checkpoint is None or (cursor is not None and session_time > indexed_time):
                    changed.append(session)
            elif self.fingerprints.get(username) != fingerprint:
                changed.append(session)
        self.fingerprints = current
        self.sessions = {
            str(session["username"]): dict(session)
            for session in sessions if session.get("username")
        }
        return changed

    @staticmethod
    def _cursor_from_row(conversation_id, row):
        if not isinstance(row, dict) or not row.get("shard"):
            return None
        try:
            sort_seq = int(row["sort_seq"])
            local_id = int(row["local_id"])
        except (KeyError, TypeError, ValueError):
            return None
        return {
            "version": 1,
            "session_id": conversation_id,
            "sort_seq": sort_seq,
            "shard": str(row["shard"]),
            "local_id": local_id,
        }

    def _conversation(self, conversation_id, session):
        title = self.names.get(conversation_id) or conversation_id
        if title == conversation_id and self.provider is not None:
            try:
                resolved = self.provider.reader.get_nickname(conversation_id)
                if isinstance(resolved, str) and resolved.strip():
                    title = resolved.strip()
            except Exception:
                pass
        return {
            "id": conversation_id,
            "platform": "wechat",
            "account_id": self.account_id,
            "title": title,
            "kind": "group" if conversation_id.endswith("@chatroom") else "direct",
            "unread": _integer(session.get("unread")),
            "last_message": session.get("summary") or "",
            "last_timestamp": _integer(session.get("last_time")),
        }

    def _ingest(self, conversation, rows, cursor, complete):
        from .server import normalize_wechat_rows
        messages = normalize_wechat_rows(rows, conversation["id"], self.names)
        checkpoint = {
            "conversation_id": conversation["id"],
            "next_offset": 0,
            "cursor": cursor,
            "complete": bool(complete),
        }
        accepted = self.hub._sync_ingest(
            None, "wechat", self.account_id, [conversation], messages,
            checkpoint=checkpoint)
        return len(messages) if accepted else None

    def _process_session(self, conversation_id):
        session = self.sessions.get(conversation_id) or self.pending.get(conversation_id) or {}
        conversation = self._conversation(conversation_id, session)
        checkpoint = self.hub.store.sync_checkpoint(
            "wechat", self.account_id, conversation_id)
        cursor = checkpoint.get("cursor") if checkpoint else None
        if cursor is None:
            rows = self.provider.messages(
                conversation_id, limit=NEW_CONVERSATION_SEED)
            if not rows:
                return 0
            latest = rows[0]
            return self._ingest(
                conversation, rows,
                self._cursor_from_row(conversation_id, latest), True)

        ingested = 0
        for _page in range(MAX_PAGES_PER_SESSION):
            page = self.provider.messages_after(
                conversation_id, cursor=cursor, limit=MESSAGE_PAGE_SIZE)
            rows = page.get("messages") or []
            next_cursor = page.get("next_cursor")
            complete = not page.get("has_more", False)
            if not rows and not complete and next_cursor == cursor:
                raise RuntimeError("WeChat live source cursor did not advance")
            if rows:
                count = self._ingest(
                    conversation, rows, next_cursor, complete)
                if count is None:
                    return None
                ingested += count
            cursor = next_cursor
            if complete:
                break
        return ingested

    def poll_once(self):
        """Run one bounded incremental receive pass."""
        with self._lock:
            if not self._enabled():
                self._publish("disabled")
                return 0
            if "wechat" in self.hub.jobs:
                self._publish("waiting_sync")
                return 0
            try:
                provider = self._ensure_provider()
                snapshot_method = getattr(provider, "session_snapshot", None)
                if callable(snapshot_method):
                    snapshot = snapshot_method(self.signature)
                    if snapshot.get("changed"):
                        self.signature = snapshot.get("signature")
                        changed = self._changed_sessions(snapshot.get("sessions") or [])
                        for session in changed:
                            self.pending[str(session["username"])] = dict(session)
                else:
                    sessions = provider.sessions(include_counts=False)
                    changed = self._changed_sessions(sessions)
                    for session in changed:
                        self.pending[str(session["username"])] = dict(session)

                if not self.pending:
                    self._publish("ready")
                    return 0
                self._refresh_names(provider, True)
                ordered = sorted(
                    self.pending.items(),
                    key=lambda item: _integer(item[1].get("last_time")),
                    reverse=True)[:MAX_SESSIONS_PER_TICK]
                total = 0
                for conversation_id, session in ordered:
                    self.sessions[conversation_id] = dict(session)
                    count = self._process_session(conversation_id)
                    if count is None:
                        self._publish("disabled")
                        return total
                    self.pending.pop(conversation_id, None)
                    total += count
                self.ingested_total += total
                self._publish("ready", changed=len(ordered))
                return total
            except Exception:
                self._publish("error", error="本机微信增量接收暂不可用")
                return 0
