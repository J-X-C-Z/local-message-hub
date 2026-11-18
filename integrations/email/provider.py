"""Read-only TLS IMAP import; credentials stay in current-user DPAPI storage."""
from __future__ import annotations

import hashlib
import imaplib
import json
import os
from email import policy
from email.parser import BytesParser
from pathlib import Path
import re
import shutil
import ssl
import tempfile
import threading

from integrations.wecom.bot_credentials import _crypt
from .adapter import _attachment_parts, parse_email_export


_PRESETS = {"gmail": "imap.gmail.com", "outlook": "outlook.office365.com"}
_KEYS = {"provider", "email", "host", "port", "username", "auth", "password", "access_token", "folder"}
_MAX_MESSAGE = 64 * 1024 * 1024
_MAX_ATTACHMENT = 32 * 1024 * 1024


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


def _account_key(email):
    return hashlib.sha256(email.strip().casefold().encode("utf-8")).hexdigest()[:24]


def _message_key(message_id):
    return hashlib.sha256(str(message_id).encode("utf-8")).hexdigest()


def _row_extra(row):
    extra = row.get("extra") if hasattr(row, "get") else None
    return extra if isinstance(extra, dict) else {}


def _row_attachments(row):
    attachments = row.get("attachments") if hasattr(row, "get") else None
    if not isinstance(attachments, list):
        attachments = _row_extra(row).get("attachments")
    return attachments if isinstance(attachments, list) else []


def _resource_index(value):
    if type(value) is not int or value < 0:
        raise EmailProviderError("邮件附件序号无效")
    return value


class EmailProvider:
    def __init__(self, storage):
        self.storage = Path(storage)
        self.config_path = self.storage / "email.credentials"
        self.cursor_path = self.storage / "email.cursor.json"
        self.accounts_path = self.storage / "email.accounts"
        self.attachments_path = self.storage / "email-attachments"
        self._lock = threading.RLock()

    def _read_config(self, path):
        try:
            value = json.loads(_crypt(path.read_bytes(), False).decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeError):
            raise EmailProviderError("邮件配置不可用，请重新配置") from None

    def _configs(self):
        configs = {}
        for path in self.accounts_path.glob("*.credentials"):
            try:
                config = self._read_config(path)
            except EmailProviderError:
                continue
            if config:
                configs[_account_key(config.get("email", ""))] = config
        try:
            legacy = self._read_config(self.config_path)
        except EmailProviderError:
            legacy = None
        if legacy:
            configs.setdefault(_account_key(legacy.get("email", "")), legacy)
        return configs

    def _config(self, account_id=None):
        configs = self._configs()
        if account_id:
            return configs.get(account_id)
        return next(iter(configs.values()), None)

    def _cursor_path(self, config):
        try:
            legacy = self._read_config(self.config_path)
        except EmailProviderError:
            legacy = None
        if legacy and _account_key(legacy.get("email", "")) == _account_key(config["email"]):
            return self.cursor_path
        return self.storage / f"email.cursor.{_account_key(config['email'])}.json"

    def status(self):
        with self._lock:
            try:
                configs = self._configs()
            except EmailProviderError:
                return {"configured": False, "accounts": [], "error": "邮件配置不可用，请重新配置"}
            accounts = []
            for account_id, config in configs.items():
                cursor_path = self._cursor_path(config)
                try:
                    cursor = json.loads(cursor_path.read_text(encoding="utf-8")) if cursor_path.exists() else {}
                except (OSError, ValueError):
                    cursor = {}
                accounts.append({"id": account_id, **{key: value for key, value in config.items()
                                 if key not in {"password", "access_token"}},
                                 "synced_uid": cursor.get("uid", 0)})
            first = accounts[0] if accounts else {}
            return {"configured": bool(accounts), "accounts": accounts, **first}

    def reset_checkpoint(self, account_id=None):
        with self._lock:
            if account_id:
                config = self._config(account_id)
                if config:
                    self._cursor_path(config).unlink(missing_ok=True)
            else:
                self.cursor_path.unlink(missing_ok=True)
                for path in self.storage.glob("email.cursor.*.json"):
                    path.unlink(missing_ok=True)

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
                previous = self._config(_account_key(config["email"]))
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
            account_id = _account_key(config["email"])
            legacy_corrupt = False
            try:
                legacy = self._read_config(self.config_path)
            except EmailProviderError:
                legacy, legacy_corrupt = None, True
            has_profiles = any(self.accounts_path.glob("*.credentials"))
            destination = (self.config_path if legacy_corrupt or
                           (legacy and _account_key(legacy.get("email", "")) == account_id) or
                           (not legacy and not has_profiles) else
                           self.accounts_path / f"{account_id}.credentials")
            _write(destination, _crypt(json.dumps(config, ensure_ascii=True).encode(), True))
            if not same:
                self._cursor_path(config).unlink(missing_ok=True)
            return self.status()

    def remove(self, account_id):
        if not isinstance(account_id, str) or not re.fullmatch(r"[a-f0-9]{24}", account_id):
            raise ValueError("邮箱账号标识无效")
        with self._lock:
            config = self._config(account_id)
            if not config:
                raise ValueError("邮箱账号不存在")
            legacy = self._read_config(self.config_path)
            cursor_path = self._cursor_path(config)
            if legacy and _account_key(legacy.get("email", "")) == account_id:
                self.config_path.unlink(missing_ok=True)
            else:
                (self.accounts_path / f"{account_id}.credentials").unlink(missing_ok=True)
            cursor_path.unlink(missing_ok=True)
            return self.status()

    def _cursor(self, config):
        path = self._cursor_path(config)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(value, dict) or type(value.get("uid")) is not int
                    or value["uid"] < 0):
                raise ValueError()
            return value
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise EmailProviderError("邮件同步进度不可用") from None

    @staticmethod
    def _folder(config):
        return '"' + config["folder"].replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _connect(self, config):
        client = imaplib.IMAP4_SSL(config["host"], config["port"],
                                  ssl_context=ssl.create_default_context(), timeout=30)
        try:
            if config["auth"] == "oauth2":
                client.authenticate("XOAUTH2", lambda challenge: b"" if challenge else (
                    f'user={config["username"]}\x01auth=Bearer {config["access_token"]}\x01\x01').encode())
            else:
                client.login(config["username"], config["password"])
            if client.select(self._folder(config), readonly=True)[0] != "OK":
                raise EmailProviderError("无法只读打开邮件文件夹")
            _, validity_data = client.response("UIDVALIDITY")
            validity = validity_data[0].decode("ascii") if validity_data and validity_data[0] else ""
            if not validity.isdigit():
                raise EmailProviderError("邮件服务未提供有效 UIDVALIDITY")
            return client, validity
        except (imaplib.IMAP4.error, OSError, UnicodeError, EmailProviderError):
            try:
                client.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
            raise

    @staticmethod
    def _fetch_message_size(client, uid):
        status, size_data = client.uid("FETCH", str(uid), "(RFC822.SIZE)")
        metadata = b" ".join(item for item in (size_data or []) if isinstance(item, bytes))
        size = re.search(rb"RFC822.SIZE\s+(\d+)", metadata, re.I)
        if status != "OK" or not size:
            raise EmailProviderError("邮件大小读取失败，进度未提交")
        return int(size.group(1))

    @staticmethod
    def _fetch_message(client, uid, expected_size=None):
        status, body_data = client.uid(
            "FETCH", str(uid), f"(BODY.PEEK[]<0.{_MAX_MESSAGE + 1}>)")
        bodies = [item for item in (body_data or []) if isinstance(item, tuple)
                  and len(item) == 2 and isinstance(item[1], bytes)]
        if status != "OK" or len(bodies) != 1 or len(bodies[0][1]) > _MAX_MESSAGE:
            raise EmailProviderError("邮件正文读取失败，进度未提交")
        header, payload = bodies[0]
        literal = re.search(rb"\{(\d+)\}", header if isinstance(header, bytes) else b"")
        if ((literal and int(literal.group(1)) != len(payload))
                or (expected_size is not None and len(payload) != expected_size)):
            raise EmailProviderError("邮件正文被截断，进度未提交")
        return payload

    def _cache_sync_attachments(self, config, message, parts, cancel):
        attachments = message.get("attachments")
        if not isinstance(attachments, list) or not attachments:
            return
        account_dir = self.attachments_path / _account_key(config["email"])
        selected = []
        for index, (metadata, payload) in enumerate(parts):
            if cancel and cancel():
                raise EmailProviderError("邮件同步已取消，进度未提交")
            if index >= len(attachments):
                raise EmailProviderError("邮件附件结构无效，进度未提交")
            attachment = attachments[index]
            if len(payload) > _MAX_ATTACHMENT:
                attachment.update({"available": False, "too_large": True})
                continue
            selected.append((index, payload))
        if not selected:
            return
        account_dir = self._safe_walk(account_dir)
        if account_dir is None:
            raise EmailProviderError("邮件附件缓存路径无效")
        account_dir.mkdir(parents=True, exist_ok=True)
        account_dir = self._safe_walk(account_dir)
        if account_dir is None or not account_dir.is_dir():
            raise EmailProviderError("邮件附件缓存路径无效")
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=account_dir))
        try:
            staging = self._safe_walk(staging)
            if staging is None or not staging.is_dir():
                raise EmailProviderError("邮件附件缓存路径无效")
            for index, payload in selected:
                if cancel and cancel():
                    raise EmailProviderError("邮件同步已取消，进度未提交")
                _write(staging / str(index), payload)
            destination = account_dir / _message_key(message["id"])
            destination = self._safe_walk(destination)
            if destination is None:
                raise EmailProviderError("邮件附件缓存路径无效")
            destination.mkdir(parents=True, exist_ok=True)
            destination = self._safe_walk(destination)
            if destination is None or not destination.is_dir():
                raise EmailProviderError("邮件附件缓存路径无效")
            for index, _ in selected:
                target = self._safe_walk(destination / str(index))
                if target is None:
                    raise EmailProviderError("邮件附件缓存路径无效")
                os.replace(staging / str(index), target)
                attachments[index]["available"] = True
        finally:
            if isinstance(staging, Path):
                shutil.rmtree(staging, ignore_errors=True)

    def commit_checkpoint(self, checkpoint, account_id=None):
        """Call only after all returned records are durably ingested."""
        with self._lock:
            config = self._config(account_id)
            if (not config or not isinstance(checkpoint, dict)
                    or checkpoint.get("identity") != _identity(config)
                    or type(checkpoint.get("uid")) is not int or checkpoint["uid"] < 0
                    or not re.fullmatch(r"[0-9]+", str(checkpoint.get("uidvalidity", "")))):
                raise EmailProviderError("邮件配置已变化，请重新同步")
            path = self._cursor_path(config)
            current = self._cursor(config)
            if (current.get("identity") == checkpoint["identity"]
                    and current.get("uidvalidity") == checkpoint["uidvalidity"]
                    and current.get("uid", 0) > checkpoint["uid"]):
                return
            _write(path, json.dumps(checkpoint).encode())

    def sync(self, limit=50, cancel=None, account_id=None, download_attachments=False):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("每次同步数量须为 1–50")
        if type(download_attachments) is not bool:
            raise ValueError("附件下载选项必须为布尔值")
        with self._lock:
            config = self._config(account_id)
            cursor = self._cursor(config) if config else {}
        if not config:
            raise EmailProviderError("请先配置邮箱")
        client = None
        try:
            client, validity = self._connect(config)
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
                expected_size = self._fetch_message_size(client, uid)
                if expected_size > _MAX_MESSAGE:
                    raise EmailProviderError("邮件超过 64 MiB，同步停止且进度未提交；可先导出该邮件归档")
                raw = self._fetch_message(client, uid, expected_size)
                parsed = parse_email_export(raw, "eml", config["email"])
                if parsed["invalid"] or not parsed["messages"]:
                    raise EmailProviderError("邮件格式无效，进度未提交")
                message = parsed["messages"][0]
                message["email_locator"] = {
                    "account_id": _account_key(config["email"]),
                    "identity": identity,
                    "uid": uid,
                    "uidvalidity": validity,
                }
                if download_attachments:
                    parsed_mime = BytesParser(policy=policy.default).parsebytes(raw)
                    self._cache_sync_attachments(
                        config, message, _attachment_parts(parsed_mime), cancel)
                for index, attachment in enumerate(message.get("attachments") or []):
                    if not attachment.get("available"):
                        attachment["available"] = self.cached_attachment(message, index)["available"]
                for key in ("total", "valid", "invalid"):
                    report[key] += parsed[key]
                for key in ("messages", "conversations", "errors", "warnings"):
                    report[key].extend(parsed.get(key, []))
                last_uid = uid
            return {"account_id": _account_key(config["email"]),
                    "records": report, "fetched": len(report["messages"]),
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

    def _safe_walk(self, target):
        raw = Path(os.path.abspath(target))
        for node in (raw, *raw.parents):
            if node.is_symlink():
                return None
            is_junction = getattr(node, "is_junction", None)
            if is_junction and is_junction():
                return None
        try:
            resolved = raw.resolve(strict=False)
            resolved.relative_to(self.storage.resolve(strict=False))
        except (OSError, ValueError):
            return None
        return resolved

    def _cached_location(self, row, resource_index):
        index = _resource_index(resource_index)
        if not hasattr(row, "get") or row.get("platform", "email") != "email":
            return None, index, {}, {}
        email = row.get("account_id")
        row_id = row.get("id")
        if not isinstance(email, str) or not isinstance(row_id, str) or not email or not row_id:
            return None, index, {}, {}
        profile_id = _account_key(email)
        try:
            config = self._config(profile_id)
        except EmailProviderError:
            config = None
        if config and config.get("email", "").casefold() != email.casefold():
            config = None
        locator = row.get("email_locator")
        if not isinstance(locator, dict):
            locator = _row_extra(row).get("email_locator")
        if not isinstance(locator, dict) or locator.get("account_id") != profile_id:
            return None, index, {}, config
        attachments = _row_attachments(row)
        if index >= len(attachments) or not isinstance(attachments[index], dict):
            return None, index, {}, config
        metadata = attachments[index]
        target = self.attachments_path / profile_id / _message_key(row_id) / str(index)
        return target, index, metadata, config

    def cached_attachment(self, row, resource_index=0):
        target, index, metadata, _ = self._cached_location(row, resource_index)
        name = metadata.get("name") if isinstance(metadata.get("name"), str) else ""
        mime_type = metadata.get("mime_type") if isinstance(metadata.get("mime_type"), str) else ""
        unavailable = {"available": False, "path": "", "name": name, "mime_type": mime_type}
        if target is None:
            return unavailable
        safe = self._safe_walk(target)
        if safe is None or not safe.is_file():
            return unavailable
        return {"available": True, "path": str(safe), "name": name, "mime_type": mime_type}

    def download_attachment(self, row, resource_index=0):
        target, index, metadata, config = self._cached_location(row, resource_index)
        already = self.cached_attachment(row, index)
        if already.get("available"):
            return already
        if target is None or not config:
            raise EmailProviderError("邮件记录或附件序号无效")
        size = metadata.get("size")
        if metadata.get("too_large") is True or type(size) is not int or size > _MAX_ATTACHMENT:
            raise EmailProviderError("附件超过 32 MiB，无法缓存")
        locator = row.get("email_locator")
        if not isinstance(locator, dict):
            locator = _row_extra(row).get("email_locator")
        uid = locator.get("uid") if isinstance(locator, dict) else None
        uidvalidity = locator.get("uidvalidity") if isinstance(locator, dict) else None
        if (type(uid) is not int or uid < 1 or not re.fullmatch(r"[0-9]+", str(uidvalidity or ""))
                or locator.get("identity") != _identity(config)):
            raise EmailProviderError("邮件附件定位信息无效")
        client = None
        try:
            client, validity = self._connect(config)
            if validity != str(uidvalidity):
                raise EmailProviderError("邮件 UIDVALIDITY 已变化，请重新同步")
            expected_size = self._fetch_message_size(client, uid)
            if expected_size > _MAX_MESSAGE:
                raise EmailProviderError("邮件超过 64 MiB，无法读取附件")
            raw = self._fetch_message(client, uid, expected_size)
            parsed = parse_email_export(raw, "eml", config["email"])
            if parsed["invalid"] or not parsed["messages"]:
                raise EmailProviderError("邮件格式无效")
            message = parsed["messages"][0]
            if message.get("id") != row.get("id"):
                raise EmailProviderError("邮件 Message-ID 与索引记录不一致")
            parts = _attachment_parts(
                BytesParser(policy=policy.default).parsebytes(raw))
            if index >= len(parts):
                raise EmailProviderError("邮件附件不存在")
            attachment, payload = parts[index]
            if (metadata.get("name") != attachment.get("name")
                    or metadata.get("size") != attachment.get("size")):
                raise EmailProviderError("邮件附件内容与索引记录不一致")
            if len(payload) > _MAX_ATTACHMENT:
                raise EmailProviderError("附件超过 32 MiB，无法缓存")
            safe = self._safe_walk(target)
            if safe is None:
                raise EmailProviderError("邮件附件缓存路径无效")
            _write(safe, payload)
            return self.cached_attachment(row, index)
        except EmailProviderError:
            raise
        except (imaplib.IMAP4.error, OSError, ValueError, UnicodeError):
            raise EmailProviderError("邮件连接或认证失败，请检查 TLS、主机和凭据") from None
        finally:
            if client is not None:
                try:
                    client.logout()
                except (imaplib.IMAP4.error, OSError):
                    pass
