"""Read-only TLS IMAP import; credentials stay in current-user DPAPI storage."""
from __future__ import annotations

import hashlib
import imaplib
import json
import os
from pathlib import Path
import re
import ssl
import tempfile
import threading

from integrations.wecom.bot_credentials import _crypt
from .adapter import parse_email_export


_PRESETS = {"gmail": "imap.gmail.com", "outlook": "outlook.office365.com"}
_KEYS = {"provider", "email", "host", "port", "username", "auth", "password", "access_token", "folder"}
_MAX_MESSAGE = 20 * 1024 * 1024


class EmailProviderError(RuntimeError):
    pass


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _identity(config):
    return hashlib.sha256(json.dumps([config.get(key) for key in (
        "email", "host", "port", "username", "folder")], ensure_ascii=True).encode()).hexdigest()


class EmailProvider:
    def __init__(self, storage):
        self.storage = Path(storage)
        self.config_path = self.storage / "email.credentials"
        self.cursor_path = self.storage / "email.cursor.json"
        self._lock = threading.RLock()

    def _config(self):
        try:
            value = json.loads(_crypt(self.config_path.read_bytes(), False).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeError):
            raise EmailProviderError("邮件配置不可用，请重新配置") from None

    def status(self):
        with self._lock:
            try:
                config = self._config()
            except EmailProviderError:
                return {"configured": False, "error": "邮件配置不可用，请重新配置"}
            return {"configured": bool(config), **({key: value for key, value in config.items()
                    if key not in {"password", "access_token"}} if config else {})}

    def reset_checkpoint(self):
        with self._lock:
            self.cursor_path.unlink(missing_ok=True)

    def configure(self, values):
        if not isinstance(values, dict) or set(values) - _KEYS:
            raise ValueError("邮件配置字段无效")
        config = dict(values)
        provider = config.setdefault("provider", "imap")
        if provider not in {"imap", "gmail", "outlook"}:
            raise ValueError("请选择 IMAP、Gmail 或 Outlook")
        config.setdefault("host", _PRESETS.get(provider, ""))
        config.setdefault("port", 993)
        config.setdefault("username", config.get("email", ""))
        config.setdefault("auth", "password")
        config.setdefault("folder", "INBOX")
        for key in ("email", "host", "username", "folder", "auth"):
            if (not isinstance(config.get(key), str) or not config[key]
                    or len(config[key]) > 320 or any(ord(c) < 32 or ord(c) == 127 for c in config[key])):
                raise ValueError("邮件配置包含无效文本")
        if (not re.fullmatch(r"[^\s@/\\:]+@[^\s@/\\:]+", config["email"])
                or not re.fullmatch(r"[A-Za-z0-9.-]+", config["host"])
                or not config["folder"].isascii()):
            raise ValueError("邮箱、IMAP 主机或文件夹无效（文件夹请使用 ASCII 名称）")
        if type(config["port"]) is not int or not 1 <= config["port"] <= 65535:
            raise ValueError("IMAP 端口无效")
        if config["auth"] not in {"password", "oauth2"}:
            raise ValueError("邮件认证方式无效")
        secret_key = "password" if config["auth"] == "password" else "access_token"
        with self._lock:
            try:
                previous = self._config()
            except EmailProviderError:
                previous = None
            same = previous is not None and _identity(previous) == _identity(config)
            secret = config.get(secret_key)
            if not secret and same and previous.get("auth") == config["auth"]:
                secret = previous.get(secret_key)
            if (not isinstance(secret, str) or not 1 <= len(secret) <= 16384
                    or any(ord(c) < 32 or ord(c) == 127 for c in secret)):
                raise ValueError("请提供有效的应用密码或 OAuth 访问令牌")
            config.pop("password", None)
            config.pop("access_token", None)
            config[secret_key] = secret
            _write(self.config_path, _crypt(json.dumps(config, ensure_ascii=True).encode(), True))
            if not same:
                self.cursor_path.unlink(missing_ok=True)
            return self.status()

    def _cursor(self):
        try:
            value = json.loads(self.cursor_path.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or type(value.get("uid")) is not int
                    or value["uid"] < 0):
                raise ValueError()
            return value
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise EmailProviderError("邮件同步进度不可用") from None

    def commit_checkpoint(self, checkpoint):
        """Call only after all returned records are durably ingested."""
        with self._lock:
            config = self._config()
            if (not config or not isinstance(checkpoint, dict)
                    or checkpoint.get("identity") != _identity(config)
                    or type(checkpoint.get("uid")) is not int or checkpoint["uid"] < 0
                    or not re.fullmatch(r"[0-9]+", str(checkpoint.get("uidvalidity", "")))):
                raise EmailProviderError("邮件配置已变化，请重新同步")
            current = self._cursor()
            if (current.get("identity") == checkpoint["identity"]
                    and current.get("uidvalidity") == checkpoint["uidvalidity"]
                    and current.get("uid", 0) > checkpoint["uid"]):
                return
            _write(self.cursor_path, json.dumps(checkpoint).encode())

    def sync(self, limit=50, cancel=None):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("每次同步数量须为 1–50")
        with self._lock:
            config = self._config()
            cursor = self._cursor()
        if not config:
            raise EmailProviderError("请先配置邮箱")
        client = None
        try:
            client = imaplib.IMAP4_SSL(config["host"], config["port"],
                                      ssl_context=ssl.create_default_context(), timeout=30)
            if config["auth"] == "oauth2":
                client.authenticate("XOAUTH2", lambda challenge: b"" if challenge else (
                    f'user={config["username"]}\x01auth=Bearer {config["access_token"]}\x01\x01').encode())
            else:
                client.login(config["username"], config["password"])
            folder = '"' + config["folder"].replace("\\", "\\\\").replace('"', '\\"') + '"'
            if client.select(folder, readonly=True)[0] != "OK":
                raise EmailProviderError("无法只读打开邮件文件夹")
            _, validity_data = client.response("UIDVALIDITY")
            validity = validity_data[0].decode("ascii") if validity_data and validity_data[0] else ""
            if not validity.isdigit():
                raise EmailProviderError("邮件服务未提供有效 UIDVALIDITY")
            identity = _identity(config)
            after = cursor.get("uid", 0) if (cursor.get("identity") == identity
                    and cursor.get("uidvalidity") == validity) else 0
            status, data = client.uid("SEARCH", None, "UID", f"{after + 1}:*")
            if status != "OK" or not data or not isinstance(data[0], bytes):
                raise EmailProviderError("邮件索引读取失败")
            uids = sorted({int(uid) for uid in data[0].split() if int(uid) > after})
            report = {"total": 0, "valid": 0, "invalid": 0, "errors": [],
                      "warnings": [], "messages": [], "conversations": []}
            last_uid = after
            for uid in uids[:limit]:
                if cancel and cancel():
                    raise EmailProviderError("邮件同步已取消，进度未提交")
                status, size_data = client.uid("FETCH", str(uid), "(RFC822.SIZE)")
                metadata = b" ".join(item for item in (size_data or []) if isinstance(item, bytes))
                size = re.search(rb"RFC822.SIZE\s+(\d+)", metadata, re.I)
                if status != "OK" or not size:
                    raise EmailProviderError("邮件大小读取失败，进度未提交")
                if int(size.group(1)) > _MAX_MESSAGE:
                    raise EmailProviderError("邮件超过 20 MiB，同步停止且进度未提交；可先导出该邮件归档")
                status, body_data = client.uid("FETCH", str(uid), f"(BODY.PEEK[]<0.{_MAX_MESSAGE + 1}>)")
                bodies = [item[1] for item in (body_data or []) if isinstance(item, tuple)
                          and len(item) == 2 and isinstance(item[1], bytes)]
                if (status != "OK" or len(bodies) != 1 or len(bodies[0]) > _MAX_MESSAGE
                        or len(bodies[0]) != int(size.group(1))):
                    raise EmailProviderError("邮件正文读取失败，进度未提交")
                parsed = parse_email_export(bodies[0], "eml", config["email"])
                if parsed["invalid"] or not parsed["messages"]:
                    raise EmailProviderError("邮件格式无效，进度未提交")
                for key in ("total", "valid", "invalid"):
                    report[key] += parsed[key]
                for key in ("messages", "conversations", "errors", "warnings"):
                    report[key].extend(parsed.get(key, []))
                last_uid = uid
            return {"records": report, "fetched": len(report["messages"]),
                    "has_more": len(uids) > limit,
                    "checkpoint": {"identity": identity, "uidvalidity": validity, "uid": last_uid}}
        except EmailProviderError:
            raise
        except (imaplib.IMAP4.error, OSError, ValueError, UnicodeError):
            # Server exception text can echo credentials or private message content.
            raise EmailProviderError("邮件连接或认证失败，请检查 TLS、主机和凭据") from None
        finally:
            if client is not None:
                try:
                    client.logout()
                except (imaplib.IMAP4.error, OSError):
                    pass
