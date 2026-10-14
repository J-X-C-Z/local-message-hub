"""Governed, auditable outbound text messaging."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Protocol


_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_QQ_RE = re.compile(r"^\d{5,20}$")
_WECOM_RE = re.compile(r"^[A-Za-z0-9_@.-]{1,128}$")
_WECHAT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_WECHAT_CONVERSATION_RE = re.compile(r"^(?:[A-Za-z0-9_-]{1,64}|[A-Za-z0-9_-]{1,64}@chatroom)$")
_MAX_TEXT = 4000
_MAX_RECEIPT_ID = 200
_PLATFORMS = ("qq", "wecom_bot", "wechat")
_PATTERNS = {"qq": _QQ_RE, "wecom_bot": _WECOM_RE, "wechat": _WECHAT_RE}
_PLATFORM_LABELS = {"qq": "QQ", "wecom_bot": "企微机器人", "wechat": "微信"}


class DeliveryAdapter(Protocol):
    def deliver(self, request: dict) -> dict: ...


class SendRepository:
    """Persist pending sends in the Hub SQLite file, included in normal backups."""

    def __init__(self, path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, timeout=30, isolation_level=None,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS send_requests (
                request_id TEXT PRIMARY KEY,
                payload_hash TEXT NOT NULL,
                platform TEXT NOT NULL,
                account_id TEXT NOT NULL,
                conversation_type TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                text TEXT NOT NULL,
                attachment_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL,
                message_id TEXT NOT NULL DEFAULT '',
                error_code TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS send_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                FOREIGN KEY(request_id) REFERENCES send_requests(request_id)
            );
            CREATE INDEX IF NOT EXISTS idx_send_audit_time ON send_audit(timestamp DESC);
        """)
        if "attachment_json" not in {row[1] for row in self._conn.execute("PRAGMA table_info(send_requests)")}:
            self._conn.execute("ALTER TABLE send_requests ADD COLUMN attachment_json TEXT NOT NULL DEFAULT '{}' ")
        self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_send_attachment_once ON send_requests(json_extract(attachment_json, '$.attachment_id'))")

    def prepare(self, payload):
        now = int(time.time())
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT * FROM send_requests WHERE request_id=?", (payload["request_id"],)
                ).fetchone()
                if row:
                    if row["payload_hash"] != payload["payload_hash"]:
                        raise ValueError("request_id 已用于不同消息")
                    self._conn.execute("COMMIT")
                    return dict(row), False
                attachment_id = (payload.get("attachment") or {}).get("attachment_id")
                if attachment_id and self._conn.execute(
                        "SELECT 1 FROM send_requests WHERE json_extract(attachment_json, '$.attachment_id')=?",
                        (attachment_id,)).fetchone():
                    raise ValueError("附件已关联其他发送请求，请重新选择")
                self._conn.execute("""INSERT INTO send_requests
                    (request_id,payload_hash,platform,account_id,conversation_type,conversation_id,text,attachment_json,status,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,'pending_approval',?,?)""",
                    (payload["request_id"], payload["payload_hash"], payload["platform"],
                     payload["account_id"], payload["conversation_type"],
                     payload["conversation_id"], payload["text"],
                     json.dumps(payload.get("attachment") or {}, ensure_ascii=False,
                                sort_keys=True, separators=(",", ":")), now, now))
                self._audit(payload["request_id"], "prepared", "pending_approval", now)
                row = self._conn.execute("SELECT * FROM send_requests WHERE request_id=?",
                                         (payload["request_id"],)).fetchone()
                self._conn.execute("COMMIT")
                return self.get(payload["request_id"]), True
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def get(self, request_id):
        with self._lock:
            row = self._conn.execute("SELECT * FROM send_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["audit"] = [dict(item) for item in self._conn.execute(
                "SELECT action,status,timestamp FROM send_audit WHERE request_id=? ORDER BY id",
                (request_id,)).fetchall()]
            return result

    def list(self, status="pending_approval", limit=50, platform=None):
        if status not in ("pending_approval", "sending", "sent", "failed", "unknown", "rejected", "all"):
            raise ValueError("发送状态无效")
        if platform not in (None,) + _PLATFORMS:
            raise ValueError("发送平台无效")
        limit = min(max(int(limit), 1), 100)
        with self._lock:
            clauses, values = [], []
            if status != "all":
                clauses.append("status=?")
                values.append(status)
            if platform:
                clauses.append("platform=?")
                values.append(platform)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = self._conn.execute(
                "SELECT * FROM send_requests" + where + " ORDER BY created_at DESC LIMIT ?",
                (*values, limit)).fetchall()
            return [dict(row) for row in rows]

    def recover_inflight(self):
        now = int(time.time())
        with self._lock:
            rows = self._conn.execute(
                "SELECT request_id FROM send_requests WHERE status='sending'").fetchall()
            if not rows:
                return 0
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                for row in rows:
                    self._conn.execute(
                        "UPDATE send_requests SET status='unknown',error_code='interrupted',updated_at=? WHERE request_id=? AND status='sending'",
                        (now, row["request_id"]))
                    self._audit(row["request_id"], "recovered_unknown", "unknown", now)
                self._conn.execute("COMMIT")
                return len(rows)
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def attachment_ids(self):
        with self._lock:
            return {row[0] for row in self._conn.execute(
                "SELECT json_extract(attachment_json, '$.attachment_id') FROM send_requests "
                "WHERE status IN ('pending_approval','sending')"
            ) if isinstance(row[0], str)}

    def claim(self, request_id):
        return self._transition(request_id, "pending_approval", "sending", "approved")

    def reject(self, request_id):
        return self._transition(request_id, "pending_approval", "rejected", "rejected")

    def finish(self, request_id, status, message_id="", error_code=""):
        if status not in ("sent", "failed", "unknown"):
            raise ValueError("发送状态无效")
        return self._transition(request_id, "sending", status, status,
                                 message_id=message_id, error_code=error_code)

    def _transition(self, request_id, expected, status, action, **values):
        now = int(time.time())
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                updates = {"status": status, "updated_at": now, **values}
                assignments = ",".join(f"{key}=?" for key in updates)
                cur = self._conn.execute(
                    f"UPDATE send_requests SET {assignments} WHERE request_id=? AND status=?",
                    (*updates.values(), request_id, expected))
                if cur.rowcount != 1:
                    row = self._conn.execute("SELECT status FROM send_requests WHERE request_id=?",
                                             (request_id,)).fetchone()
                    self._conn.execute("COMMIT")
                    if row is None:
                        raise ValueError("发送请求不存在")
                    return dict(self.get(request_id)), False
                self._audit(request_id, action, status, now)
                row = self._conn.execute("SELECT * FROM send_requests WHERE request_id=?",
                                         (request_id,)).fetchone()
                self._conn.execute("COMMIT")
                return dict(row), True
            except Exception:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def _audit(self, request_id, action, status, now):
        self._conn.execute("INSERT INTO send_audit(request_id,action,status,timestamp) VALUES (?,?,?,?)",
                           (request_id, action, status, now))

    def close(self):
        with self._lock:
            self._conn.close()


class SendService:
    def __init__(self, repository: SendRepository, adapter: DeliveryAdapter | dict,
                 recover_inflight=False):
        self.repository = repository
        self.adapters = adapter if isinstance(adapter, dict) else {"qq": adapter}
        if recover_inflight:
            self.repository.recover_inflight()

    @staticmethod
    def validate_request(payload):
        if not isinstance(payload, dict):
            raise ValueError("发送请求无效")
        platform = payload.get("platform")
        account_id = payload.get("account_id")
        conversation_type = payload.get("conversation_type")
        conversation_id = payload.get("conversation_id")
        text = payload.get("text")
        request_id = payload.get("request_id")
        if platform not in _PLATFORMS:
            raise ValueError("当前平台不支持发送")
        pattern = _PATTERNS[platform]
        label = _PLATFORM_LABELS[platform]
        if not isinstance(account_id, str) or not pattern.fullmatch(account_id):
            raise ValueError(f"{label}账号标识无效")
        if conversation_type not in ("private", "group"):
            raise ValueError("会话类型无效")
        conversation_pattern = _WECHAT_CONVERSATION_RE if platform == "wechat" else pattern
        if not isinstance(conversation_id, str) or not conversation_pattern.fullmatch(conversation_id):
            raise ValueError(f"{label}会话标识无效")
        if platform == "wechat":
            is_chatroom = conversation_id.endswith("@chatroom")
            if (conversation_type == "group") != is_chatroom:
                raise ValueError("会话类型与微信会话标识不匹配")
        attachment = payload.get("attachment")
        if attachment is not None:
            if (platform != "wecom_bot" or not isinstance(attachment, dict) or
                    set(attachment) != {"attachment_id", "filename", "media_type", "size", "sha256"} or
                    not isinstance(attachment.get("attachment_id"), str) or
                    not re.fullmatch(r"[a-f0-9]{48}", attachment["attachment_id"]) or
                    attachment.get("media_type") not in ("image", "file", "voice", "video") or
                    not isinstance(attachment.get("filename"), str) or
                    not isinstance(attachment.get("size"), int) or isinstance(attachment.get("size"), bool) or
                    not 1 <= attachment["size"] <= 50 * 1024 * 1024 or
                    not isinstance(attachment.get("sha256"), str) or
                    not re.fullmatch(r"[a-f0-9]{64}", attachment["sha256"])):
                raise ValueError("企微机器人附件信息无效")
        if not isinstance(text, str) or len(text) > _MAX_TEXT or (attachment is None and not text.strip()):
            raise ValueError("文本不能为空且不得超过 4000 个字符")
        if attachment is not None and text.strip():
            raise ValueError("媒体请求不能附带文本")
        if not isinstance(request_id, str) or not _ID_RE.fullmatch(request_id):
            raise ValueError("request_id 格式无效")
        normalized = {"platform": platform, "account_id": account_id,
                      "conversation_type": conversation_type,
                      "conversation_id": conversation_id, "text": text,
                      "request_id": request_id, "attachment": attachment}
        digest = hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        return {**normalized, "payload_hash": digest}

    def prepare(self, payload):
        # A source spelling stays outside the durable request so a replayed
        # prepare cannot be mistaken for a different target.
        source_id = payload.get("source_conversation_id")
        record, created = self.repository.prepare(self.validate_request(payload))
        result = self.public_record(record, created=created)
        if result is not None and isinstance(source_id, str):
            result["source_conversation_id"] = source_id
        return result

    def list(self, status="pending_approval", limit=50, platform=None):
        return {"items": [self.public_record(item) for item in
                           self.repository.list(status, limit, platform)]}

    def approve_and_deliver(self, request_id, platform=None):
        if not isinstance(request_id, str) or not _ID_RE.fullmatch(request_id):
            raise ValueError("request_id 格式无效")
        if platform and self.status(request_id, platform) is None:
            raise ValueError("发送请求不存在")
        record, claimed = self.repository.claim(request_id)
        if not claimed:
            return self.public_record(record)
        try:
            adapter = self.adapters.get(record["platform"])
            if adapter is None:
                raise RuntimeError("delivery_adapter_unavailable")
            delivery_record = dict(record)
            try:
                delivery_record["attachment"] = json.loads(record.get("attachment_json") or "{}")
            except (ValueError, TypeError):
                delivery_record["attachment"] = {}
            result = adapter.deliver(delivery_record)
            message_id = result.get("message_id") if isinstance(result, dict) else None
            if (not isinstance(message_id, str) or not message_id.strip() or
                    len(message_id) > _MAX_RECEIPT_ID):
                record, _ = self.repository.finish(request_id, "unknown", error_code="invalid_receipt")
            else:
                record, _ = self.repository.finish(request_id, "sent", message_id)
        except TimeoutError:
            record, _ = self.repository.finish(request_id, "unknown", error_code="timeout")
        except Exception as exc:
            code = getattr(exc, "code", "delivery_error")
            status = "unknown" if getattr(exc, "delivery_unknown", False) else "failed"
            record, _ = self.repository.finish(request_id, status, error_code=str(code)[:64])
        return self.public_record(record)

    def status(self, request_id, platform=None):
        record = self.repository.get(request_id)
        if record and platform and record["platform"] != platform:
            return None
        return self.public_record(record) if record else None

    def reject(self, request_id, platform=None):
        if not isinstance(request_id, str) or not _ID_RE.fullmatch(request_id):
            raise ValueError("request_id 格式无效")
        if platform and self.status(request_id, platform) is None:
            raise ValueError("发送请求不存在")
        record, _ = self.repository.reject(request_id)
        return self.public_record(record)

    @staticmethod
    def public_record(record, created=None):
        if record is None:
            return None
        result = {key: record[key] for key in (
            "request_id", "platform", "account_id", "conversation_type",
            "conversation_id", "text", "status", "message_id", "error_code",
            "created_at", "updated_at") if key in record}
        try:
            attachment = json.loads(record.get("attachment_json") or "{}")
        except (ValueError, TypeError):
            attachment = {}
        if attachment:
            result["attachment"] = {key: attachment[key] for key in
                                     ("filename", "media_type", "size") if key in attachment}
        if result.get("platform") == "wecom_bot":
            result["identity_label"] = "企业微信智能机器人（非当前员工身份）"
        if result.get("platform") == "wechat":
            result["identity_label"] = "当前登录的本机微信账号（GUI 拟人操作）"
        if "audit" in record:
            result["audit"] = record["audit"]
        if created is not None:
            result["created"] = created
        return result
