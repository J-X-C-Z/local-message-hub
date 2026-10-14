"""Read-only adapter for the local qq-chat-exporter/NapCat QCE service."""

from __future__ import annotations

import json
import hashlib
import os
import mimetypes
import re
import subprocess
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, unquote, urlencode
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:40653"
_MAX_QCE_RESPONSE_BYTES = 32 * 1024 * 1024
APPROVED_QCE_SHA256 = frozenset({
    "b928a9e556db6d1f8ba93b4213b70238190cd580743c4ce5dd7fcc0309ed1fcc",
    "066d9ff15c3e6cb7efe23acc564b5b7b34d6da2849fec77a7dcc890290bb7745",
})
DEFAULT_BUNDLE = (
    Path(__file__).resolve().parents[2]
    / ".runtime"
    / "qq"
    / "v6.3.2"
    / "shell"
    / "NapCat-QCE-Windows-x64"
)
_NAPCAT_CREDENTIALS: dict[str, str] = {}
_NAPCAT_CREDENTIALS_LOCK = threading.Lock()


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _is_image_resource(resource: dict[str, Any]) -> bool:
    return _is_media_resource(resource, "image")


def _is_media_resource(resource: dict[str, Any], expected: str) -> bool:
    kind = str(resource.get("type") or resource.get("kind") or resource.get("k") or "").strip().lower()
    aliases = {
        "image": {"image", "photo", "picture", "images", "图片"},
        "video": {"video", "videos", "视频"},
        "voice": {"voice", "audio", "audios", "语音"},
        "file": {"file", "files", "文件"},
    }
    return kind in aliases.get(expected, {expected})


class QQProviderError(RuntimeError):
    """Raised when the local QCE service cannot satisfy a read request."""


def _safe_launch_error(error: Exception) -> str:
    message = str(error)
    safe_messages = {
        "NapCat WebUI configuration is unavailable",
        "NapCat WebUI is not configured for authenticated loopback access",
        "NapCat WebUI authentication failed",
        "NapCat local API rejected the request",
        "NapCat has not produced a QQ login QR code",
        "QQ.exe is already running; refusing to start a second client instance",
        "QCE access token is unavailable; start/login to the local QCE runtime first",
        "QCE rejected the local access token",
        "QCE returned an invalid response",
    }
    if message in safe_messages:
        return message
    if re.fullmatch(r"NapCat local API request failed \(\d{3}\)", message):
        return message
    if re.fullmatch(r"QCE HTTP request failed \(\d{3}\)", message):
        return message
    match = re.fullmatch(r"(NapCat WebUI|QCE service) is unavailable: ([A-Za-z]+Error|JSONDecodeError)", message)
    if match:
        return message
    return type(error).__name__


class QQProvider:
    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 10.0,
        token_file: str | os.PathLike[str] | None = None,
        bundle_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("QCE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.token_file = Path(
            token_file
            or os.environ.get("QCE_SECURITY_FILE")
            or (Path.home() / ".qq-chat-exporter" / "security.json")
        )
        self.bundle_dir = Path(bundle_dir or os.environ.get("QCE_BUNDLE_DIR") or DEFAULT_BUNDLE)
        self._process: subprocess.Popen[bytes] | None = None
        self._gallery_lock = threading.RLock()
        self._gallery_resources: dict[str, dict[str, Any]] = {}
        self._forward_lock = threading.RLock()
        self._forward_handles: dict[str, dict[str, Any]] = {}
        self._validate_local_url()

    def _rpc(self, method: str, params: list[Any]) -> Any:
        """Call only the fixed QCE bridge methods used by lazy forward expansion."""
        if method not in {"MsgService.getMultiMsg", "FileApi.downloadMedia"}:
            raise ValueError("unsupported local bridge method")
        request = Request("http://127.0.0.1:40654/rpc",
                          data=json.dumps({"method": method, "params": params}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=min(self.timeout, 30)) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise QQProviderError("QQ forward response exceeds the size limit")
            result = json.loads(raw.decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise QQProviderError(f"Local QQ bridge request failed: {type(exc).__name__}") from None
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise QQProviderError("Local QQ bridge rejected the request")
        return result.get("result")

    @staticmethod
    def _media_signature(path: Path, kind: str) -> tuple[bool, str]:
        with path.open("rb") as file:
            head = file.read(64)
        signatures = {
            "image": ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"),
                      (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"),
                      (b"RIFF", "image/webp"), (b"BM", "image/bmp"),
                      (b"II*\x00", "image/tiff"), (b"MM\x00*", "image/tiff"),
                      (b"II+\x00", "image/tiff"), (b"MM\x00+", "image/tiff")),
            "video": ((b"\x00\x00\x00", "video/mp4"), (b"\x1aE\xdf\xa3", "video/webm")),
            "voice": ((b"OggS", "audio/ogg"), (b"RIFF", "audio/wav"),
                      (b"ID3", "audio/mpeg"), (b"fLaC", "audio/flac"),
                      (bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c"), "audio/x-ms-wma"),
                      (b"#!AMR\n", "audio/amr"), (b"#!AMR-WB\n", "audio/amr"),
                      (b"#!SILK_V3", "audio/silk"),
                      (b"\x02#!SILK_V3", "audio/silk")),
            "file": ((b"%PDF-", "application/pdf"), (b"PK\x03\x04", "application/zip"),
                     (b"Rar!", "application/vnd.rar"), (b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed")),
        }
        if kind == "voice":
            if (len(head) >= 12 and head[4:8] == b"ftyp"
                    and head[8:12] in {b"M4A ", b"M4B ", b"M4P ", b"F4A ", b"F4B "}):
                return True, "audio/mp4"
            if len(head) >= 2 and head[0] == 0xff and head[1] & 0xf6 == 0xf0:
                return True, "audio/aac"
        if kind == "image" and len(head) >= 16 and head[4:8] == b"ftyp":
            box_size = int.from_bytes(head[:4], "big")
            if box_size >= 16 and box_size % 4 == 0 and box_size <= path.stat().st_size:
                brands = {head[8:12]}
                brands.update(head[offset:offset + 4]
                              for offset in range(16, min(box_size, len(head)) - 3, 4))
                if brands & {b"avif", b"avis"}:
                    return True, "image/avif"
                if brands & {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}:
                    return True, "image/heic"
        for prefix, mime in signatures.get(kind, ()):
            if (kind == "video" and prefix == b"\x00\x00\x00" and len(head) >= 8 and head[4:8] == b"ftyp") or head.startswith(prefix):
                if kind == "image" and prefix == b"RIFF" and head[8:12] != b"WEBP":
                    continue
                return True, mime
        return False, "application/octet-stream"

    def _prune_forward_handles(self, now: float) -> None:
        expired = [key for key, value in self._forward_handles.items()
                   if value["expires"] <= now]
        for key in expired:
            path = self._forward_handles.pop(key).get("cached_path")
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass

    def expand_forward_resources(self, row: dict[str, Any], account_id: str,
                                 cache_root: Path) -> dict[str, Any]:
        """Resolve a single indexed forward message; resources stay lazy until download."""
        if row.get("platform") != "qq" or row.get("account_id") != account_id:
            raise ValueError("QQ account/message scope mismatch")
        peer = str(row.get("conversation_id") or "")
        message_id = str(row.get("id") or "")
        if not peer or not message_id.startswith(peer + ":"):
            raise ValueError("invalid indexed QQ message identity")
        rich = (row.get("extra") or {}).get("roaming_content", {}).get("elements", [])
        if not any(isinstance(item, dict) and item.get("type") == "forward" for item in rich):
            raise ValueError("indexed QQ message is not a merged forward")
        own = self.status().get("account_id")
        if not own or str(own) != str(account_id):
            raise ValueError("QQ account is not the active indexed account")
        chat_type = int((row.get("extra") or {}).get("chatType") or 1)
        raw_id = message_id[len(peer) + 1:]
        timestamp = int(row.get("timestamp") or 0)
        start = max(0, timestamp * 1000 - 5000)
        fetched = self._request("POST", "/api/messages/fetch", {
            "peer": {"chatType": chat_type, "peerUid": peer}, "page": 1,
            "limit": 2000, "filter": {"startTime": start, "endTime": timestamp * 1000 + 5000},
        }).get("messages", [])
        parent = next((item for item in fetched if isinstance(item, dict)
                       and str(item.get("msgId") or item.get("msgSeq") or "") == raw_id), None)
        if (not parent or str(parent.get("peerUid") or peer) != peer or
                int(parent.get("chatType") or chat_type) != chat_type):
            raise ValueError("indexed QQ forward parent could not be verified")
        elements = parent.get("elements") if isinstance(parent.get("elements"), list) else []
        forward = next((el.get("multiForwardMsgElement") for el in elements
                        if isinstance(el, dict) and isinstance(el.get("multiForwardMsgElement"), dict)), None)
        if not forward:
            raise ValueError("raw QQ message is not a merged forward")
        result = self._rpc("MsgService.getMultiMsg", [
            {"chatType": chat_type, "peerUid": peer, "guildId": ""}, raw_id, raw_id])
        records = result.get("msgList", result.get("messages", [])) if isinstance(result, dict) else []
        found, truncated, declared_total = [], False, 0
        now = time.monotonic()
        def walk(rows: Any, depth: int = 0) -> None:
            nonlocal truncated, declared_total
            if depth >= 3:
                truncated = True
                return
            if not isinstance(rows, list):
                return
            for message in rows:
                if len(found) >= 50:
                    truncated = True
                    return
                if not isinstance(message, dict):
                    continue
                msg_id = str(message.get("msgId") or "")
                msg_chat = int(message.get("chatType") or chat_type)
                msg_peer = str(message.get("peerUid") or peer)
                if msg_chat != chat_type or msg_peer != peer or not msg_id:
                    continue
                for element in message.get("elements", []) if isinstance(message.get("elements"), list) else []:
                    if not isinstance(element, dict):
                        continue
                    for key, kind in (("picElement", "image"), ("fileElement", "file"),
                                      ("videoElement", "video"), ("pttElement", "voice")):
                        detail = element.get(key)
                        if not isinstance(detail, dict):
                            continue
                        size = detail.get("fileSize")
                        if type(size) is int and (size < 0 or size > 50 * 1024 * 1024):
                            continue
                        budget_size = size if type(size) is int else 50 * 1024 * 1024
                        if declared_total + budget_size > 512 * 1024 * 1024:
                            truncated = True
                            return
                        element_id = str(detail.get("elementId") or element.get("elementId") or "")
                        if not element_id:
                            continue
                        handle = secrets.token_urlsafe(24)
                        found.append({"handle": handle, "type": kind,
                                      "name": str(detail.get("fileName") or kind)[:200],
                                      "size": size, "mime_type": {
                                          "image": "image/*", "video": "video/*",
                                          "voice": "audio/*", "file": "application/octet-stream"}[kind]})
                        with self._forward_lock:
                            self._prune_forward_handles(now)
                            reserved = sum(value["budget_size"] for value in self._forward_handles.values())
                            if reserved + budget_size > 512 * 1024 * 1024:
                                found.pop()
                                truncated = True
                                return
                            self._forward_handles[handle] = {
                                "expires": now + 600, "account_id": account_id,
                                "peer": peer, "parent_id": raw_id, "msg_id": msg_id,
                                "chat_type": msg_chat, "element_id": element_id,
                                "source_path": str(detail.get("sourcePath") or ""),
                                "kind": kind, "size": size,
                                "budget_size": budget_size,
                            }
                        declared_total += budget_size
                        if len(found) >= 50:
                            truncated = True
                            return
                    for element in message.get("elements", []) if isinstance(message.get("elements"), list) else []:
                        fwd = element.get("multiForwardMsgElement") if isinstance(element, dict) else None
                        if isinstance(fwd, dict):
                            nested = self._rpc("MsgService.getMultiMsg", [
                                {"chatType": chat_type, "peerUid": peer, "guildId": ""}, raw_id, msg_id])
                            walk(nested.get("msgList", []) if isinstance(nested, dict) else [], depth + 1)
        walk(records)
        with self._forward_lock:
            self._forward_handles = {key: value for key, value in self._forward_handles.items()
                                     if value["expires"] > now}
        return {"items": found, "truncated": truncated}

    def download_forward_resource(self, handle: str, cache_root: Path, *,
                                  account_id: str | None = None, peer: str | None = None,
                                  parent_id: str | None = None,
                                  max_bytes: int | None = None) -> tuple[Path, str]:
        now = time.monotonic()
        with self._forward_lock:
            self._prune_forward_handles(now)
            item = self._forward_handles.get(handle)
            if not item or item["expires"] <= now:
                raise FileNotFoundError("forward resource handle expired")
            if ((account_id is not None and item["account_id"] != str(account_id)) or
                    (peer is not None and item["peer"] != str(peer)) or
                    (parent_id is not None and item["parent_id"] != str(parent_id))):
                raise PermissionError("QQ forward resource scope mismatch")
            if (max_bytes is not None and type(item.get("size")) is int and
                    item["size"] > max_bytes):
                raise ValueError("QQ forward resource exceeds the requested byte limit")
            if item.get("cached_path"):
                cached = Path(item["cached_path"])
                if cached.is_file() and not cached.is_symlink():
                    resolved = cached.resolve(strict=True)
                    ok, mime = self._media_signature(resolved, item["kind"])
                    if resolved.stat().st_size <= 50 * 1024 * 1024 and ok:
                        return resolved, mime
                item.pop("cached_path", None)
        if str(self.status().get("account_id") or "") != item["account_id"]:
            raise PermissionError("QQ account changed after forward expansion")
        cache_root.mkdir(parents=True, exist_ok=True)
        if cache_root.is_symlink():
            raise ValueError("invalid Hub cache root")
        qq_root = cache_root / "qq"
        root = qq_root / "forward-cache"
        for directory in (qq_root, root):
            if directory.is_symlink():
                raise ValueError("invalid forward cache root")
            directory.mkdir(exist_ok=True)
        root = root.resolve(strict=True)
        if not root.is_relative_to(cache_root.resolve(strict=True)):
            raise ValueError("invalid forward cache root")
        target = root / (secrets.token_hex(16) + ".bin")
        if target.exists() or target.is_symlink():
            raise ValueError("invalid forward cache target")
        source_path = item["source_path"]
        result = self._rpc("FileApi.downloadMedia", [item["msg_id"], item["chat_type"],
            item["peer"], item["element_id"], str(target), source_path, 30000, True])
        if not target.is_file() or target.is_symlink():
            raise FileNotFoundError("QQ forward media was not written to Hub cache")
        resolved = target.resolve(strict=True)
        if not resolved.is_relative_to(root) or resolved.stat().st_size > 50 * 1024 * 1024:
            resolved.unlink(missing_ok=True)
            raise ValueError("QQ forward media exceeded cache limits")
        ok, mime = self._media_signature(resolved, item["kind"])
        if not ok:
            resolved.unlink(missing_ok=True)
            raise ValueError("QQ forward media signature is not supported")
        with self._forward_lock:
            current = self._forward_handles.get(handle)
            if current is not item:
                resolved.unlink(missing_ok=True)
                raise FileNotFoundError("forward resource handle expired")
            item["cached_path"] = str(resolved)
        return resolved, mime

    def _validate_local_url(self) -> None:
        from urllib.parse import urlsplit

        parts = urlsplit(self.base_url)
        if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("QCE_BASE_URL must use HTTP loopback; access tokens are local-only")

    def _token(self) -> str | None:
        try:
            config = json.loads(self.token_file.read_text(encoding="utf-8-sig"))
            token = config.get("accessToken") if isinstance(config, dict) else None
            return token.strip() if isinstance(token, str) and token.strip() else None
        except (OSError, json.JSONDecodeError):
            return None

    def _napcat_request(self, path: str) -> dict[str, Any]:
        config_path = self.bundle_dir / "config" / "webui.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
            host = config.get("host")
            port = int(config.get("port"))
            token = config.get("token")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            raise QQProviderError("NapCat WebUI configuration is unavailable") from None
        if host not in {"127.0.0.1", "localhost", "::1"} or not isinstance(token, str) or not token:
            raise QQProviderError("NapCat WebUI is not configured for authenticated loopback access")
        base_url = f"http://{host}:{port}"
        cache_key = hashlib.sha256(f"{base_url}\0{token}".encode("utf-8")).hexdigest()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        def get_credential() -> str:
            with _NAPCAT_CREDENTIALS_LOCK:
                cached = _NAPCAT_CREDENTIALS.get(cache_key)
                if cached:
                    return cached
                login = Request(
                    f"{base_url}/api/auth/login",
                    data=json.dumps({
                        "hash": hashlib.sha256(f"{token}.napcat".encode("utf-8")).hexdigest(),
                    }).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urlopen(login, timeout=self.timeout) as response:
                    auth = json.loads(response.read().decode("utf-8"))
                credential = auth.get("data", {}).get("Credential")
                if auth.get("code") != 0 or not isinstance(credential, str) or not credential:
                    raise QQProviderError("NapCat WebUI authentication failed")
                _NAPCAT_CREDENTIALS[cache_key] = credential
                return credential

        result = None
        try:
            for attempt in range(2):
                credential = get_credential()
                request_headers = {**headers, "Authorization": f"Bearer {credential}"}
                request = Request(
                    f"{base_url}/api/{path.lstrip('/')}", data=b"{}",
                    headers=request_headers, method="POST",
                )
                try:
                    with urlopen(request, timeout=self.timeout) as response:
                        result = json.loads(response.read().decode("utf-8"))
                    break
                except HTTPError as exc:
                    if exc.code != 401 or attempt:
                        raise
                    with _NAPCAT_CREDENTIALS_LOCK:
                        _NAPCAT_CREDENTIALS.pop(cache_key, None)
        except HTTPError as exc:
            raise QQProviderError(f"NapCat local API request failed ({exc.code})") from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise QQProviderError(f"NapCat WebUI is unavailable: {type(exc).__name__}") from None
        if not isinstance(result, dict) or result.get("code") != 0:
            raise QQProviderError("NapCat local API rejected the request")
        data = result.get("data")
        return data if isinstance(data, dict) else {}

    def login_status(self) -> dict[str, Any]:
        data = self._napcat_request("QQLogin/CheckLoginStatus")
        return {
            "isLogin": data.get("isLogin") is True,
            **{key: data.get(key) for key in ("isOffline", "loginPhase", "qrLoginAccepted", "coreReady")},
        }

    def login_qr(self, refresh: bool = False) -> dict[str, Any]:
        """Return the QR payload for the local UI, never NapCat credentials."""
        state = self.login_status()
        if state.get("isLogin"):
            return {"qr_url": None, "login_phase": state.get("loginPhase")}
        if refresh and state.get("loginPhase") == "waiting_qrcode":
            data = self._napcat_request("QQLogin/RefreshQRcode")
            value = data.get("qrcodeurl")
            if isinstance(value, str) and value:
                return {"qr_url": value, "login_phase": state.get("loginPhase")}
        data = self._napcat_request("QQLogin/GetQQLoginQrcode")
        value = data.get("qrcode")
        if not isinstance(value, str) or not value:
            raise QQProviderError("NapCat has not produced a QQ login QR code")
        return {"qr_url": value, "login_phase": state.get("loginPhase")}

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None,
                 *, authenticated: bool = True,
                 request_timeout: float | None = None) -> dict[str, Any]:
        token = self._token() if authenticated else None
        headers = {"Accept": "application/json"}
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authenticated:
            if not token:
                raise QQProviderError("QCE access token is unavailable; start/login to the local QCE runtime first")
            headers["Authorization"] = f"Bearer {token}"
        request = Request(self.base_url + path, data=payload, headers=headers, method=method)
        try:
            timeout = self.timeout if request_timeout is None else min(max(request_timeout, 1.0), 30.0)
            with urlopen(request, timeout=timeout) as response:
                raw = response.read(_MAX_QCE_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_QCE_RESPONSE_BYTES:
                raise QQProviderError("QCE response exceeds the size limit")
            result = json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 401:
                raise QQProviderError("QCE rejected the local access token") from None
            raise QQProviderError(f"QCE HTTP request failed ({exc.code})") from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise QQProviderError(f"QCE service is unavailable: {type(exc).__name__}") from None
        if not isinstance(result, dict):
            raise QQProviderError("QCE returned an invalid response")
        if result.get("success") is False:
            error = result.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            raise QQProviderError(f"QCE request failed{f' ({code})' if code else ''}")
        data = result.get("data", result)
        return data if isinstance(data, dict) else {"value": data}

    def status(self) -> dict[str, Any]:
        login_url = f"{self.base_url}/qce/auth"
        try:
            self._request("GET", "/health", authenticated=False)
        except QQProviderError:
            try:
                login = self.login_status()
            except QQProviderError:
                return {"provider": "qq", "available": False, "online": False,
                        "authenticated": False, "login_phase": None}
            return {"provider": "qq", "available": True, "online": False,
                    "authenticated": False, "mode": "napcat_waiting_login",
                    "login_phase": login.get("loginPhase"), "account_id": None}
        if not self._token():
            return {
                "provider": "qq",
                "available": True,
                "online": False,
                "authenticated": False,
                "login_url": login_url,
            }
        try:
            state = self._request("GET", "/api/system/status")
        except QQProviderError:
            return {
                "provider": "qq",
                "available": True,
                "online": False,
                "authenticated": False,
                "login_url": login_url,
            }
        login = None
        account_id = None
        try:
            login = self.login_status()
        except QQProviderError:
            pass
        try:
            info = self._request("GET", "/api/system/info").get("napcat", {}).get("selfInfo", {})
            account_id = info.get("uin") or info.get("uid")
        except (QQProviderError, AttributeError):
            pass
        return {
            "provider": "qq",
            "available": True,
            "online": bool(state.get("online")) and bool(login and login.get("isLogin")),
            "authenticated": True,
            "mode": state.get("mode"),
            "login_url": login_url,
            "login_phase": login.get("loginPhase") if login else None,
            "account_id": str(account_id) if account_id is not None else None,
        }

    def _launch_result(self, refresh: bool = False) -> dict[str, Any]:
        qr = self.login_qr(refresh=refresh)
        return {
            "started": True,
            "qr_url": qr.get("qr_url"),
            "login_phase": qr.get("login_phase"),
            "message": "Scan the QQ QR code in the app to connect." if qr.get("qr_url")
            else "NapCat is ready; QQ login is already established.",
        }

    def launch(self, refresh: bool = False) -> dict[str, Any]:
        """Launch bundled NapCat against QQ without writing into the QQ installation."""
        if sys.platform != "win32":
            raise QQProviderError("The bundled QQ shell launcher is Windows-only")
        try:
            return self._launch_result(refresh=refresh)
        except QQProviderError:
            if refresh:
                try:
                    return self._launch_result()
                except QQProviderError:
                    pass

        self._validate_qce_binary()

        boot = self.bundle_dir / "NapCatWinBootMain.exe"
        hook = self.bundle_dir / "NapCatWinBootHook.dll"
        qq_exe = self._find_qq_exe()
        if not boot.is_file() or not hook.is_file():
            raise QQProviderError("Bundled NapCat boot files were not found")
        if qq_exe is None:
            raise QQProviderError("QQNT executable was not found; set NAPCAT_QQ_PATH to QQ.exe")
        if self._qq_process_running():
            raise QQProviderError("QQ.exe is already running; refusing to start a second client instance")

        self._prepare_runtime(qq_exe)
        log_dir = self.bundle_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "napcat-boot.log"
        env = os.environ.copy()
        env.update({
            "NAPCAT_QQ_PATH": str(qq_exe),
            "NAPCAT_PATCH_PACKAGE": str(self.bundle_dir / "qqnt.json"),
            "NAPCAT_LOAD_PATH": str(self.bundle_dir / "loadNapCat.js"),
            "NAPCAT_INJECT_PATH": str(hook),
            "NAPCAT_LAUNCHER_PATH": str(boot),
            "NAPCAT_MAIN_PATH": str(self.bundle_dir / "napcat.mjs"),
            "QCE_LOG_DIR": str(log_dir),
            "QCE_LOG_FILE": str(log_dir / "qce-runtime.log"),
            "QCE_NO_AUTO_OPEN": "1",
        })

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        try:
            log = log_file.open("ab")
            try:
                self._process = subprocess.Popen(
                    [str(boot), str(qq_exe), str(hook)],
                    cwd=str(self.bundle_dir),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    startupinfo=startupinfo,
                    creationflags=creationflags,
                )
            finally:
                log.close()
        except OSError as exc:
            raise QQProviderError(f"Could not start bundled NapCat bootloader ({type(exc).__name__})") from None
        deadline = time.monotonic() + min(max(self.timeout, 1.0), 20.0)
        last_error = None
        while time.monotonic() < deadline:
            try:
                result = self._launch_result(refresh=refresh)
                result.update({"pid": self._process.pid, "log_file": str(log_file)})
                return result
            except QQProviderError as exc:
                last_error = _safe_launch_error(exc)
            time.sleep(0.25)
        message = "NapCat login API did not become ready; QQ was not confirmed connected"
        if last_error:
            message += f" (last error: {last_error})"
        raise QQProviderError(message)

    def _validate_qce_binary(self) -> None:
        binary = self.bundle_dir / "qce-server.exe"
        try:
            with binary.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError:
            raise QQProviderError("The bundled loopback QCE server binary is missing") from None
        if digest not in APPROVED_QCE_SHA256:
            raise QQProviderError("The bundled QCE server is not an approved loopback build")

    def _prepare_runtime(self, qq_exe: Path) -> None:
        """Prepare the same injection metadata as upstream, but only inside our bundle."""
        package_paths = [qq_exe.parent / "resources" / "app" / "package.json"]
        versions_dir = qq_exe.parent / "versions"
        if versions_dir.is_dir():
            package_paths.extend(
                child / "resources" / "app" / "package.json"
                for child in sorted(versions_dir.iterdir(), reverse=True)
                if child.is_dir()
            )
        package = None
        for path in package_paths:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(loaded, dict):
                    package = loaded
                    break
            except (OSError, json.JSONDecodeError):
                continue
        if package is None:
            raise QQProviderError("QQNT package metadata was not found; refusing an unverified injection")
        keys = (
            "name", "verHash", "version", "linuxVersion", "linuxVerHash", "private",
            "description", "productName", "author", "homepage", "sideEffects", "bin", "buildVersion",
        )
        metadata = {key: package[key] for key in keys if key in package}
        metadata.update({
            "main": "./loadNapCat.js",
            "isPureShell": True,
            "isByteCodeShell": True,
            "platform": "win32",
            "eleArch": "x64",
        })
        (self.bundle_dir / "qqnt.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        main_uri = (self.bundle_dir / "napcat.mjs").resolve().as_uri()
        (self.bundle_dir / "loadNapCat.js").write_text(
            f'(async () => {{ await import("{main_uri}") }})()\n', encoding="utf-8"
        )
        self._set_runtime_config("webui.json", {"host": "127.0.0.1"})
        self._set_runtime_config("napcat.json", {"consoleLog": False})
        from .sender import ensure_local_http_server
        ensure_local_http_server(self.bundle_dir / "config" / "onebot11.json")

    def _set_runtime_config(self, filename: str, updates: dict[str, Any]) -> None:
        path = self.bundle_dir / "config" / filename
        try:
            config = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QQProviderError(f"Could not read bundled {filename} runtime config") from exc
        if not isinstance(config, dict):
            raise QQProviderError(f"Bundled {filename} runtime config is invalid")
        config.update(updates)
        descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(config, ensure_ascii=False, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise QQProviderError(f"Could not write bundled {filename} runtime config") from exc

    def _service_ready(self) -> bool:
        try:
            self._request("GET", "/health", authenticated=False)
            return True
        except QQProviderError:
            return False

    def _find_qq_exe(self) -> Path | None:
        candidates: list[Path] = []
        env_path = os.environ.get("NAPCAT_QQ_PATH")
        if env_path:
            candidates.append(Path(env_path))
        saved_path = self.bundle_dir / "config" / "qq_path.txt"
        try:
            saved = saved_path.read_text(encoding="utf-8-sig").strip()
            if saved:
                candidates.append(Path(saved))
        except OSError:
            pass
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("LocalAppData"), "C:/Program Files", "D:/Program Files"):
            if base:
                candidates.append(Path(base) / "Tencent" / "QQNT" / "QQ.exe")
        for candidate in candidates:
            try:
                resolved = candidate.expanduser().resolve(strict=True)
                if resolved.is_file() and resolved.name.casefold() == "qq.exe":
                    return resolved
            except OSError:
                continue
        return None

    @staticmethod
    def _qq_process_running() -> bool:
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq QQ.exe", "/FO", "CSV", "/NH"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
        except (OSError, subprocess.SubprocessError):
            return True
        return result.returncode == 0 and '"QQ.exe"' in result.stdout

    def conversations(self) -> list[dict[str, Any]]:
        data = self._request(
            "GET", "/api/recent-contacts?limit=2000&includeAll=true"
        )
        contacts = data.get("contacts", [])
        if not isinstance(contacts, list):
            return []
        result = []
        for item in contacts:
            if not isinstance(item, dict):
                continue
            chat_type = item.get("chatType")
            peer_uid = str(item.get("peerUid") or item.get("peerUin") or "")
            stamp = item.get("lastMsgTime", item.get("msgTime"))
            try:
                stamp = int(stamp)
                if abs(stamp) >= 100_000_000_000:
                    stamp //= 1000
            except (TypeError, ValueError):
                stamp = None
            result.append({
                "id": peer_uid,
                "title": item.get("name") or item.get("peerName") or "",
                "kind": "group" if chat_type == 2 else "private",
                "last_timestamp": stamp,
                "chatType": chat_type,
                "raw": item,
            })
        return result

    def contacts(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for endpoint, collection, kind in (
            ("/api/friends", "friends", "private"),
            ("/api/groups", "groups", "group"),
        ):
            page = 1
            while True:
                data = self._request("GET", f"{endpoint}?page={page}&limit=2000")
                items = data.get(collection, [])
                if not isinstance(items, list):
                    break
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if kind == "group":
                        uid = item.get("groupCode")
                        name = item.get("remark") or item.get("groupName") or ""
                        avatar = item.get("avatarUrl")
                    else:
                        core = item.get("coreInfo") if isinstance(item.get("coreInfo"), dict) else item
                        uid = core.get("uid") or core.get("uin") or item.get("uid")
                        name = core.get("remark") or core.get("nick") or core.get("nickName") or ""
                        avatar = core.get("avatarUrl") or item.get("avatarUrl")
                    result.append({
                        "id": str(uid or ""),
                        "uin": str(item.get("groupCode") or (core.get("uin") if kind == "private" else "") or ""),
                        "name": name,
                        "kind": kind,
                        "avatar_url": avatar,
                        "raw": item,
                    })
                pages = int(data.get("totalPages") or 1)
                if page >= pages or not items:
                    break
                page += 1
        return result

    def members(self, conversation_id: str, account_id: str | None = None,
                limit: int = 1000, query: str = "") -> dict[str, Any]:
        """Read one QQ group's roster through the bundled local QCE API.

        QCE currently returns the complete member array (no paging contract), so
        filtering and the returned page are bounded locally after that response.
        """
        group_code = str(conversation_id or "").strip()
        if (not group_code or len(group_code) > 128
                or any(ord(char) < 32 or char in "/\\" for char in group_code)):
            raise ValueError("conversation_id must be a valid QQ group code")
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if account_id is not None and str(account_id).strip() not in {"", "local"}:
            info = self._request("GET", "/api/system/info").get("napcat", {}).get("selfInfo", {})
            active_ids = {str(info.get(key) or "") for key in ("uid", "uin")}
            active_ids.discard("")
            if str(account_id).strip() not in active_ids:
                raise ValueError("account_id does not match the active QQ account")

        data = self._request(
            "GET", f"/api/groups/{quote(group_code, safe='')}/members"
        )
        members = data.get("value", data.get("members", data.get("items", [])))
        if not isinstance(members, list):
            raise QQProviderError("QCE returned an invalid group member list")
        needle = str(query or "").casefold().strip()
        normalized = []
        seen: set[str] = set()
        for row in members:
            if not isinstance(row, dict):
                continue
            member_id = str(row.get("uid") or row.get("uin") or row.get("user_id") or "").strip()
            if not member_id or member_id in seen:
                continue
            name = str(row.get("cardName") or row.get("nick") or row.get("name") or member_id).strip()
            remark = str(row.get("remark") or "").strip()
            if needle and not any(needle in value.casefold() for value in (member_id, name, remark)):
                continue
            role = str(row.get("role") or "member").lower()
            if role not in {"owner", "admin", "member"}:
                role = "member"
            seen.add(member_id)
            normalized.append({
                "id": member_id,
                "name": name,
                "avatar_url": row.get("avatarUrl") or row.get("avatar_url"),
                "role": role,
                "remark": remark,
            })
        normalized.sort(key=lambda item: (item["name"].casefold(), item["id"]))
        return {"items": normalized[:limit], "total": len(normalized)}

    def messages(self, conversation: dict[str, Any] | str, limit: int = 50,
                 offset: int = 0) -> list[dict[str, Any]]:
        if type(limit) is not int or limit < 1 or limit > 2000:
            raise ValueError("limit must be between 1 and 2000")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset cannot be negative")
        if isinstance(conversation, str):
            peer_uid, chat_type = conversation, 1
        elif isinstance(conversation, dict):
            peer_uid = str(conversation.get("peerUid") or conversation.get("id") or "")
            chat_type = conversation.get("chatType", 2 if conversation.get("kind", conversation.get("type")) == "group" else 1)
        else:
            raise TypeError("conversation must be a peer id or conversation object")
        if not peer_uid:
            raise ValueError("conversation requires peerUid or id")

        # QCE prefetches roughly eleven pages per request and defaults its
        # underlying fetch batch to 5,000. Keep both bounds small so a Hub
        # sync page does not make QCE materialize several thousand rich rows.
        page_size = min(limit, 100)
        page = offset // page_size + 1
        skip = offset % page_size
        raw_messages: list[dict[str, Any]] = []
        previous_page_ids: tuple[str, ...] | None = None
        while len(raw_messages) < skip + limit:
            request_body = {
                "peer": {"chatType": int(chat_type), "peerUid": peer_uid},
                "page": page,
                "limit": page_size,
                "batchSize": page_size,
            }
            for attempt in range(2):
                try:
                    data = self._request("POST", "/api/messages/fetch", request_body,
                                         request_timeout=30.0)
                    break
                except QQProviderError as exc:
                    message = str(exc)
                    retryable = (re.fullmatch(r"QCE HTTP request failed \(5\d\d\)", message) or
                                 re.fullmatch(r"QCE service is unavailable: (URLError|TimeoutError|OSError)",
                                              message))
                    if attempt or not retryable:
                        raise
                    time.sleep(0.1)
            batch = data.get("messages", [])
            if not isinstance(batch, list) or not batch:
                break
            batch_ids = tuple(str(item.get("msgId") or item.get("msgSeq") or "")
                              for item in batch if isinstance(item, dict))
            if (batch_ids and all(batch_ids) and batch_ids == previous_page_ids):
                break
            previous_page_ids = batch_ids if batch_ids and all(batch_ids) else None
            previous_count = len(raw_messages)
            raw_messages.extend(item for item in batch if isinstance(item, dict))
            if len(raw_messages) == previous_count:
                break
            has_next = data.get("hasNext")
            if (has_next is False or (not isinstance(has_next, bool) and len(batch) < page_size)):
                break
            page += 1
        raw_messages = raw_messages[skip:skip + limit]
        try:
            info = self._request("GET", "/api/system/info").get("napcat", {}).get("selfInfo", {})
            own_ids = {str(info.get(key) or "") for key in ("uid", "uin")}
            own_ids.discard("")
        except (QQProviderError, AttributeError):
            own_ids = set()
        return [self._normalize_message(item, peer_uid, int(chat_type), own_ids)
                for item in raw_messages]

    def start_roaming_export(self, conversation: dict[str, Any], start_time: int,
                             end_time: int, output_dir: str | os.PathLike[str],
                             max_messages: int = 50000,
                             max_sequence_queries: int = 50000) -> dict[str, Any]:
        """Start a bounded QQ private-chat roaming history export."""
        peer_uid = str(conversation.get("peerUid") or conversation.get("id") or "")
        chat_type = conversation.get("chatType", 2 if conversation.get("kind") == "group" else 1)
        if int(chat_type) != 1:
            raise ValueError("QQ 漫游历史目前仅支持私聊")
        if not peer_uid or peer_uid.isdecimal():
            raise ValueError("QQ 漫游历史需要有效的私聊 peer UID")
        if type(start_time) is not int or type(end_time) is not int:
            raise ValueError("QQ 漫游历史时间范围必须使用整数 Unix 秒")
        if end_time < start_time:
            raise ValueError("结束时间不能早于开始时间")
        if (type(max_messages) is not int or type(max_sequence_queries) is not int
                or not 1 <= max_messages <= 100000
                or not 1 <= max_sequence_queries <= 100000):
            raise ValueError("QQ 漫游历史预算必须在 1 到 100000 之间")
        result = self._request("POST", "/api/messages/roaming/export", {
            "peer": {"chatType": 1, "peerUid": peer_uid, "guildId": ""},
            "sessionName": str(conversation.get("title") or peer_uid),
            "format": "JSON",
            "filter": {"startTime": start_time, "endTime": end_time},
            "options": {
                "outputDir": str(Path(output_dir).resolve()),
                "includeResourceLinks": True,
            },
            "roaming": {"maxMessages": max_messages,
                        "maxSequenceQueries": max_sequence_queries},
        })
        task_id = result.get("taskId")
        if not isinstance(task_id, str) or not task_id:
            raise QQProviderError("QCE did not return a roaming export task")
        return result

    def roaming_task(self, task_id: str) -> dict[str, Any]:
        if not task_id or len(task_id) > 160 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in task_id):
            raise ValueError("无效的 QQ 历史任务编号")
        return self._request("GET", f"/api/tasks/{quote(task_id, safe='')}")

    def cancel_roaming_task(self, task_id: str) -> dict[str, Any]:
        if not task_id or len(task_id) > 160 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in task_id):
            raise ValueError("无效的 QQ 历史任务编号")
        return self._request("POST", f"/api/tasks/{quote(task_id, safe='')}/cancel", {})

    @staticmethod
    def _forward_preview(records: Any, depth: int = 0, budget: dict[str, int] | None = None) -> list[dict[str, Any]]:
        if not isinstance(records, list) or depth >= 3:
            return []
        budget = budget if budget is not None else {"messages": 0, "bytes": 0, "resources": 0}
        result = []
        for row in records[:10]:
            if not isinstance(row, dict) or budget["messages"] >= 50 or budget["bytes"] >= 16_384:
                break
            budget["messages"] += 1
            parts = []
            resources = []
            elements = row.get("elements")
            if isinstance(elements, list):
                for element in elements[:50]:
                    if not isinstance(element, dict):
                        continue
                    text_element = element.get("textElement")
                    if isinstance(text_element, dict) and isinstance(text_element.get("content"), str):
                        parts.append(text_element["content"][:4096])
                    face = element.get("faceElement")
                    if isinstance(face, dict):
                        parts.append(str(face.get("faceText") or "[表情]")[:256])
                    for key, kind, label in (("picElement", "image", "图片"),
                                             ("fileElement", "file", "文件"),
                                             ("videoElement", "video", "视频"),
                                             ("pttElement", "voice", "语音")):
                        item = element.get(key)
                        if isinstance(item, dict):
                            name = str(item.get("fileName") or "")[:256]
                            parts.append(f"[{label}{': ' + name if name else ''}]")
                            if budget["resources"] < 50:
                                resource = {"type": kind, "available": False}
                                if name:
                                    resource["name"] = name
                                size = item.get("fileSize")
                                if type(size) is int and 0 <= size <= 2**63 - 1:
                                    resource["size"] = size
                                digest = item.get("md5HexStr") or item.get("fileMd5")
                                if isinstance(digest, str) and len(digest) <= 64:
                                    resource["md5"] = digest
                                for source, target in (("picWidth", "width"), ("picHeight", "height")):
                                    value = item.get(source)
                                    if type(value) is int and 0 <= value <= 100_000:
                                        resource[target] = value
                                duration = item.get("duration")
                                if type(duration) is int and 0 <= duration <= 86_400:
                                    resource["duration"] = duration
                                resources.append(resource)
                                budget["resources"] += 1
                    is_forward = isinstance(element.get("multiForwardMsgElement"), dict)
                    ark = element.get("arkElement")
                    if isinstance(ark, dict) and isinstance(ark.get("bytesData"), str) and len(ark["bytesData"]) <= 65_536:
                        try:
                            is_forward = is_forward or json.loads(ark["bytesData"]).get("app") == "com.tencent.multimsg"
                        except (json.JSONDecodeError, AttributeError):
                            pass
                    if is_forward:
                        nested = QQProvider._forward_preview(row.get("records"), depth + 1, budget)
                        parts.extend(f"{item['sender']['name']}：{item['content']['text']}"
                                     for item in nested if item["content"]["text"])
            text = " ".join(parts)
            remaining = max(0, 16_384 - budget["bytes"])
            encoded = text.encode("utf-8")[:remaining]
            text = encoded.decode("utf-8", errors="ignore")
            budget["bytes"] += len(encoded)
            sender = str(row.get("sendMemberName") or row.get("sendNickName")
                         or row.get("senderUin") or row.get("senderUid") or "")[:256]
            content = {"text": text}
            if resources:
                content["resources"] = resources
            result.append({"sender": {"name": sender}, "content": content})
        return result

    @staticmethod
    def _normalize_message(message: dict[str, Any], peer_uid: str,
                           chat_type: int, own_ids: set[str] | None = None) -> dict[str, Any]:
        raw_time = message.get("msgTime")
        try:
            timestamp = int(raw_time)
        except (TypeError, ValueError):
            timestamp = None
        if timestamp is not None and abs(timestamp) >= 100_000_000_000:
            timestamp //= 1000
        elements = message.get("elements")
        elements = elements if isinstance(elements, list) else []
        text_parts: list[str] = []
        media: list[dict[str, Any]] = []
        rich_elements: list[dict[str, Any]] = []
        message_type = "text"
        types = (
            ("picElement", "image"), ("pttElement", "voice"),
            ("videoElement", "video"), ("fileElement", "file"),
            ("marketFaceElement", "image"), ("arkElement", "link"),
            ("lightAppElement", "link"), ("grayTipElement", "system"),
        )
        for element in elements:
            if not isinstance(element, dict):
                continue
            text_element = element.get("textElement")
            if isinstance(text_element, dict):
                content = text_element.get("content")
                if isinstance(content, str):
                    text_parts.append(content)
                    at_type = text_element.get("atType")
                    if type(at_type) is int and at_type == 1:
                        rich_elements.append({"type": "at", "data": {
                            "uid": "all", "uin": "0", "name": "全体成员", "atType": 1,
                        }})
                    elif type(at_type) is int and at_type == 2:
                        name = content.strip().removeprefix("@")[:256]
                        uid = text_element.get("atNtUid") or text_element.get("atUid") or "unknown"
                        uin = text_element.get("atUid") or "0"
                        rich_elements.append({"type": "at", "data": {
                            "uid": str(uid)[:256], "uin": str(uin)[:64],
                            "name": name, "atType": 2,
                        }})
                    else:
                        rich_elements.append({"type": "text", "data": {"text": content}})
            markdown = element.get("markdownElement")
            if isinstance(markdown, dict) and isinstance(markdown.get("content"), str):
                content = markdown["content"][:4096]
                text_parts.append(content)
                rich_elements.append({"type": "markdown", "data": {"content": content}})
            face = element.get("faceElement")
            if isinstance(face, dict):
                face_id = str(face.get("faceIndex") if face.get("faceIndex") is not None else "")
                face_name = face.get("faceText") or (f"表情{face_id}" if face_id else "[表情]")
                rich_elements.append({"type": "face", "data": {"id": face_id, "name": str(face_name)}})
            reply = element.get("replyElement")
            if isinstance(reply, dict):
                reply_text = reply.get("sourceMsgText")
                if not isinstance(reply_text, str):
                    source_elements = reply.get("sourceMsgTextElems")
                    reply_text = "".join(
                        text.get("content", "")
                        for item in source_elements if isinstance(item, dict)
                        for text in [item.get("textElement")]
                        if isinstance(text, dict) and isinstance(text.get("content"), str)
                    ) if isinstance(source_elements, list) else ""
                if not reply_text:
                    referenced = reply.get("referencedMsg")
                    reply_text = referenced.get("msgBody", "") if isinstance(referenced, dict) else ""
                rich_elements.append({"type": "reply", "data": {
                    "messageId": str(reply.get("referencedMessageId") or reply.get("replayMsgId")
                                     or reply.get("replyMsgId") or reply.get("sourceMsgIdInRecords") or ""),
                    "senderName": str(reply.get("senderNick") or reply.get("senderMemberName")
                                       or reply.get("senderUin") or reply.get("senderUidStr") or ""),
                    "content": reply_text[:4096] if isinstance(reply_text, str) else "",
                }})
            forward = element.get("multiForwardMsgElement")
            ark = element.get("arkElement")
            if isinstance(ark, dict) and isinstance(ark.get("bytesData"), str) and len(ark["bytesData"]) <= 65_536:
                try:
                    if json.loads(ark["bytesData"]).get("app") == "com.tencent.multimsg":
                        forward = forward or ark
                except (json.JSONDecodeError, AttributeError):
                    pass
            if isinstance(forward, dict):
                nested = QQProvider._forward_preview(message.get("records"))
                rich_elements.append({"type": "forward", "data": {
                    "title": "合并转发",
                    "summary": f"查看{len(nested)}条转发消息" if nested else "查看转发消息",
                    "messages": nested,
                }})
            if isinstance(element.get("shareLocationElement"), dict):
                rich_elements.append({"type": "location", "data": {
                    "title": "位置消息", "summary": "分享了位置",
                }})
            if isinstance(element.get("structLongMsgElement"), dict):
                rich_elements.append({"type": "long_message", "data": {
                    "summary": "长消息",
                }})
            call = element.get("avRecordElement")
            if isinstance(call, dict):
                call_type = call.get("type")
                label = "语音通话" if call_type == 1 else "视频通话" if call_type == 2 else "通话"
                status = call.get("text")
                status = status[:128] if isinstance(status, str) and status else "已结束"
                rich_elements.append({"type": "system", "data": {"text": f"{label} - {status}"}})
            wallet = element.get("walletElement")
            if isinstance(wallet, dict):
                summary = next((wallet.get(field) for field in
                                ("summary", "title", "name", "wording")
                                if isinstance(wallet.get(field), str) and wallet[field].strip()),
                               "红包/钱包消息")
                rich_elements.append({"type": "wallet", "data": {"summary": summary[:256]}})
            gift = element.get("liveGiftElement")
            if isinstance(gift, dict):
                summary = next((gift.get(field) for field in
                                ("summary", "giftName", "name", "text")
                                if isinstance(gift.get(field), str) and gift[field].strip()),
                               "群礼物")
                rich_elements.append({"type": "live_gift", "data": {"summary": summary[:256]}})
            tofu = element.get("tofuRecordElement")
            if isinstance(tofu, dict):
                description = tofu.get("descriptionContent")
                rich_elements.append({"type": "tofu_record", "data": {
                    "summary": description[:256] if isinstance(description, str) and description else "豆腐记录",
                }})
            if isinstance(element.get("giphyElement"), dict):
                rich_elements.append({"type": "giphy", "data": {"summary": "Giphy 动图"}})
            for key, kind in types:
                detail = element.get(key)
                if not isinstance(detail, dict):
                    continue
                message_type = kind
                filename = detail.get("fileName") or detail.get("faceName")
                metadata = {
                    "kind": kind,
                    "file_name": filename,
                    "size": detail.get("fileSize"),
                    "md5": detail.get("md5HexStr") or detail.get("fileMd5"),
                    "width": detail.get("picWidth"),
                    "height": detail.get("picHeight"),
                    "duration": detail.get("duration"),
                    "origin_url": detail.get("originImageUrl"),
                }
                media.append({k: v for k, v in metadata.items() if v is not None})
                if key == "marketFaceElement":
                    rich_elements.append({"type": "market_face", "data": {"name": str(filename or "[原创表情]")}})
                elif kind in {"link", "system"}:
                    content = next((detail.get(field) for field in
                                    ("title", "summary", "description", "content", "text", "name")
                                    if isinstance(detail.get(field), str) and detail[field].strip()), "")
                    rich_elements.append({"type": "json" if kind == "link" else "system",
                                          "data": {"title": filename or content,
                                                   "description": content if filename else "",
                                                   "text": content}})
                elif kind not in {"image", "voice", "video", "file"}:
                    rich_elements.append({"type": kind, "data": {"text": str(filename or "")}})
                break
        raw_kind = message.get("msgType")
        if raw_kind == 5 or any(item.get("type") == "system" for item in rich_elements):
            message_type = "system"
        elif not media and raw_kind in (3, 4, 6, 7):
            message_type = {3: "file", 4: "video", 6: "voice", 7: "video"}[raw_kind]
        sender_id = str(message.get("senderUid") or message.get("senderUin") or "")
        sender_name = message.get("sendNickName") or message.get("senderNick") or ""
        message_id = str(message.get("msgId") or message.get("msgSeq") or "")
        return {
            "id": f"{peer_uid}:{message_id}" if message_id else f"{peer_uid}:{timestamp}:{sender_id}",
            "conversation_id": peer_uid,
            "chatType": chat_type,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "timestamp": timestamp,
            "type": message_type,
            "text": "".join(text_parts).strip(),
            "is_self": sender_id in (own_ids or set()),
            "file_name": next((m.get("file_name") for m in media if m.get("file_name")), None),
            "media": media,
            "roaming_content": {"elements": rich_elements} if rich_elements else {},
        }

    def media(self, row: dict[str, Any]) -> dict[str, Any]:
        """Return a local cached resource, exporting the containing message if needed."""
        name = row.get("file_name")
        digest = None
        entries = row.get("media")
        if isinstance(entries, list):
            wanted_name = str(name or "").casefold()
            entry = next((entry for entry in entries if isinstance(entry, dict)
                          and (not wanted_name or str(entry.get("file_name") or "").casefold()
                               == wanted_name)), None)
            if entry is None:
                entry = next((entry for entry in entries if isinstance(entry, dict)), None)
            if entry:
                name = name or entry.get("file_name")
                digest = entry.get("md5")
        if not name and not digest:
            return {"available": False, "path": None, "reason": "message has no named media resource"}
        media_type = row.get("type")
        category = {"image": "images", "video": "videos", "voice": "audios", "file": "files"}.get(media_type, "all")
        match = None
        if digest:
            try:
                data = self._request(
                    "GET", f"/api/resources/files?type={category}&limit=200&nameSearch={quote(str(digest))}"
                )
                files = data.get("files", [])
                digest_key = str(digest).casefold()
                match = next((entry for entry in files if isinstance(entry, dict)
                              and digest_key in str(entry.get("fileName") or "").casefold()), None)
            except QQProviderError:
                match = None
        if not match:
            try:
                match = self._export_message_media(row, name, digest)
            except Exception as exc:
                reason = str(exc) if isinstance(exc, QQProviderError) else type(exc).__name__
                return {"available": False, "path": None, "reason": reason}
        if not match:
            return {"available": False, "path": None, "reason": "resource is not present in the QCE media cache"}
        resource_url = match.get("url") or match.get("relativePath")
        filename = str(match.get("fileName") or name or "media")
        if not isinstance(resource_url, str) or not resource_url.startswith("/"):
            return {"available": False, "path": None, "reason": "QCE returned an invalid local resource path"}
        try:
            target = self._download_to_cache(resource_url, filename)
            return {"available": True, "path": str(target.resolve()), "reason": None}
        except (QQProviderError, OSError) as exc:
            return {"available": False, "path": None, "reason": str(exc)}

    def cached_media(self, row: dict[str, Any], resource_index: int | None = None) -> dict[str, Any]:
        """Resolve media already in Hub-managed QQ storage without contacting QCE."""
        if not isinstance(row, dict):
            return {"available": False, "path": None, "reason": "invalid_message"}
        kind = "voice" if row.get("type") == "audio" else row.get("type")
        if kind not in {"image", "video", "voice", "file"}:
            return {"available": False, "path": None, "reason": "message_type_not_supported"}
        suffix = "image" if kind == "image" else "attachment"

        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        resources = extra.get("roaming_resources")
        if not isinstance(resources, list):
            resources = extra.get("media") if isinstance(extra.get("media"), list) else []

        if resource_index is not None:
            if type(resource_index) is not int or not 0 <= resource_index <= 100:
                return {"available": False, "path": None, "reason": "invalid_resource_index"}
            if resource_index >= len(resources):
                return {"available": False, "path": None, "reason": "resource_not_found"}
            resource = resources[resource_index]
            if not isinstance(resource, dict) or not _is_media_resource(resource, kind):
                return {"available": False, "path": None, "reason": f"resource_is_not_{suffix}"}
        else:
            resource = next((item for item in resources
                             if isinstance(item, dict) and _is_media_resource(item, kind)), None)

        relative = resource.get("path") if isinstance(resource, dict) else None
        if isinstance(relative, str) and relative:
            root, _cache = self._qq_storage_roots()
            rel_path = Path(relative)
            if rel_path.is_absolute() or any(part in {"", ".", ".."} for part in rel_path.parts):
                return {"available": False, "path": None, "reason": "cached_resource_missing_or_unsafe"}
            candidate = root
            try:
                for part in rel_path.parts:
                    candidate = candidate / part
                    if candidate.is_symlink():
                        return {"available": False, "path": None,
                                "reason": "cached_resource_missing_or_unsafe"}
                resolved = candidate.resolve(strict=True)
                if resolved.is_relative_to(root) and resolved.is_file():
                    return {"available": True, "path": str(resolved), "reason": None}
            except (OSError, RuntimeError, ValueError):
                pass
            return {"available": False, "path": None, "reason": "cached_resource_missing_or_unsafe"}

        name = (((resource or {}).get("name") or (resource or {}).get("file_name"))
                if isinstance(resource, dict) else None) or row.get("file_name")

        if not isinstance(name, str) or not name.strip():
            return {"available": False, "path": None, "reason": f"message_has_no_cached_{suffix}_name"}
        safe_name = Path(name).name.replace("\x00", "_")
        if not safe_name or safe_name in {".", ".."}:
            return {"available": False, "path": None, "reason": f"invalid_{suffix}_name"}
        _root, cache = self._qq_storage_roots()
        if cache is None:
            return {"available": False, "path": None, "reason": f"cached_{suffix}_missing"}
        try:
            candidates = [path for path in cache.iterdir() if path.name.endswith("-" + safe_name)
                          if not path.is_symlink() and path.is_file() and
                          path.resolve(strict=True).is_relative_to(cache)]
        except (OSError, RuntimeError, ValueError):
            candidates = []
        if len(candidates) != 1:
            return {"available": False, "path": None,
                    "reason": f"cached_{suffix}_missing" if not candidates else f"cached_{suffix}_ambiguous"}
        return {"available": True, "path": str(candidates[0].resolve()), "reason": None}

    def _qq_storage_roots(self) -> tuple[Path, Path | None]:
        configured_cache = Path(self._media_cache_dir())
        root = configured_cache.parent.parent.resolve()
        expected_cache = root / "qq" / "media-cache"
        try:
            cache = configured_cache.resolve(strict=True)
        except (OSError, RuntimeError):
            return root, None
        if cache != expected_cache or not cache.is_dir():
            return root, None
        return root, cache

    @staticmethod
    def _media_cache_dir() -> Path:
        storage = Path(os.environ.get("LMH_STORAGE_DIR", Path(__file__).resolve().parents[2] / ".local"))
        return storage / "qq" / "media-cache"

    def _download_to_cache(self, path: str, name: str, expected_size: int | None = None) -> Path:
        parsed = urlsplit(path)
        decoded_path = unquote(parsed.path)
        if (not path.startswith("/") or path.startswith("//") or parsed.scheme or parsed.netloc
                or parsed.query or parsed.fragment or "\\" in decoded_path
                or any(part in {"", ".", ".."} for part in decoded_path.split("/")[1:])):
            raise QQProviderError("QCE returned an invalid local resource path")
        token = self._token()
        if not token:
            raise QQProviderError("QCE access token is unavailable")
        cache = Path(self._media_cache_dir())
        cache.mkdir(parents=True, exist_ok=True)
        safe_name = Path(name).name.replace("\x00", "_") or "media"
        temporary = cache / (".download-" + secrets.token_hex(12))
        request = Request(self.base_url + path, headers={
            "Accept": "*/*", "Authorization": f"Bearer {token}",
        }, method="GET")
        try:
            # Resource bytes must come from the authenticated loopback QCE endpoint;
            # never let an HTTP redirect turn this local cache read into a network fetch.
            with build_opener(_RejectRedirects()).open(request, timeout=self.timeout) as response:
                total = 0
                digest = hashlib.sha256()
                with temporary.open("wb") as output:
                    while True:
                        chunk = response.read(min(1024 * 1024, 50 * 1024 * 1024 + 1 - total))
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > 50 * 1024 * 1024:
                            raise QQProviderError("QCE media exceeds the 50 MiB local limit")
                        output.write(chunk)
                        digest.update(chunk)
                    if expected_size is not None and total != expected_size:
                        raise QQProviderError("QCE cached resource size changed; refresh the resource list")
                    output.flush()
                    os.fsync(output.fileno())
            target = cache / f"{digest.hexdigest()[:12]}-{safe_name}"
            if target.exists():
                temporary.unlink(missing_ok=True)
            else:
                os.replace(temporary, target)
            return target
        except HTTPError as exc:
            raise QQProviderError(f"QCE media download failed ({exc.code})") from None
        except (URLError, TimeoutError, OSError) as exc:
            raise QQProviderError(f"QCE media download unavailable: {type(exc).__name__}") from None
        finally:
            temporary.unlink(missing_ok=True)

    def cached_resources(self, *, kind="", query="", offset=0, limit=60,
                         start_ms=None, end_ms=None) -> dict[str, Any]:
        """List QCE's global cached-resource gallery without exposing its URLs."""
        types = {"image": "images", "video": "videos", "voice": "audios", "file": "files"}
        if kind and kind not in types:
            return {"items": [], "total": 0, "has_more": False, "available": True,
                    "account_id": ""}
        if type(offset) is not int or not 0 <= offset <= 60_000_000:
            raise ValueError("Invalid cached resource offset")
        if type(limit) is not int or not 1 <= limit <= 60:
            raise ValueError("Invalid cached resource page size")
        if any(value is not None and (type(value) is not int or value < 0)
               for value in (start_ms, end_ms)):
            raise ValueError("Invalid cached resource date range")
        if start_ms is not None and end_ms is not None and start_ms > end_ms:
            raise ValueError("Invalid cached resource date range")
        query = str(query or "").strip()[:200]
        state = self.status()
        account_id = str(state.get("account_id") or "")
        if not state.get("online") or not account_id:
            return {"items": [], "total": 0, "has_more": False, "available": False,
                    "account_id": ""}
        page = offset // limit + 1
        page_skip = offset % limit
        params = {"type": types.get(kind, "all"), "page": page, "limit": limit}
        if query:
            params["nameSearch"] = query
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        def read_page(page_number):
            page_params = {**params, "page": page_number}
            result = self._request("GET", "/api/resources/files?" + urlencode(page_params))
            if ((start_ms is not None or end_ms is not None)
                    and result.get("dateFilterSupported") is not True):
                raise QQProviderError("Bundled QCE does not support QQ cache date filtering; rebuild the local runtime")
            page_files = result.get("files", [])
            if not isinstance(page_files, list):
                raise QQProviderError("QCE returned an invalid resource list")
            return result, page_files

        data, raw_files = read_page(page)
        pages_read = 1
        while (len(raw_files) < page_skip + limit and data.get("hasMore") is True
               and pages_read < 4):
            data, next_files = read_page(page + pages_read)
            raw_files.extend(next_files)
            pages_read += 1
        raw_files = raw_files[page_skip:page_skip + limit]
        items = []
        refs = {}
        for row in raw_files[:limit]:
            if not isinstance(row, dict):
                continue
            filename = row.get("fileName")
            resource_url = row.get("url")
            resource_type = row.get("type")
            parsed = urlsplit(resource_url) if isinstance(resource_url, str) else None
            if (not isinstance(filename, str) or not filename or Path(filename).name != filename
                    or "/" in filename or "\\" in filename or ":" in filename
                    or any(ord(char) < 32 for char in filename) or len(filename) > 240
                    or resource_type not in {"image", "video", "audio", "file"}
                    or (kind and resource_type != {"voice": "audio"}.get(kind, kind))
                    or not parsed or parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
                    or not parsed.path.startswith("/resources/") or parsed.path.startswith("//")
                    or "\\" in unquote(parsed.path)
                    or any(part in {"", ".", ".."} for part in unquote(parsed.path).split("/")[1:])):
                continue
            category = {"image": "images", "video": "videos", "audio": "audios", "file": "files"}[resource_type]
            decoded_segments = [unquote(part) for part in parsed.path.split("/")[1:]]
            if decoded_segments != ["resources", category, filename]:
                continue
            size = row.get("size")
            if type(size) is not int or not 0 <= size <= 50 * 1024 * 1024:
                continue
            resource_id = secrets.token_urlsafe(24)
            refs[resource_id] = {"account_id": account_id,
                                 "url": f"/resources/{category}/{quote(filename, safe='')}",
                                 "name": filename, "size": size, "type": resource_type}
            mime = row.get("mimeType")
            if not isinstance(mime, str) or len(mime) > 100 or "/" not in mime:
                mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            items.append({"resource_id": resource_id, "name": filename, "type": resource_type,
                          "mime_type": mime, "size": size,
                          "modified_at": str(row.get("modifyTime") or "")[:64]})
        with self._gallery_lock:
            self._gallery_resources.update(refs)
            while len(self._gallery_resources) > 4096:
                self._gallery_resources.pop(next(iter(self._gallery_resources)))
        raw_total = data.get("total", 0)
        total = raw_total if type(raw_total) is int and raw_total >= 0 else len(items)
        has_more = offset + len(items) < total
        return {"items": items, "total": total, "has_more": has_more,
                "available": True, "account_id": account_id}

    def resolve_cached_resource(self, resource_id: str, account_id: str) -> tuple[Path, str]:
        ref = self._cached_resource_ref(resource_id, account_id)
        safe_name = Path(ref["name"]).name
        target = self._download_to_cache(ref["url"], ref["name"], ref["size"])
        return target.resolve(), safe_name

    def cached_resource_metadata(self, resource_id: str, account_id: str) -> dict[str, str]:
        ref = self._cached_resource_ref(resource_id, account_id)
        return {"type": str(ref["type"]), "name": str(ref["name"])}

    def _cached_resource_ref(self, resource_id: str, account_id: str) -> dict[str, Any]:
        if not isinstance(resource_id, str) or len(resource_id) > 128:
            raise ValueError("Invalid cached resource identity")
        with self._gallery_lock:
            ref = self._gallery_resources.get(resource_id)
        if not ref or ref["account_id"] != str(account_id):
            raise FileNotFoundError("Cached resource is no longer available")
        state = self.status()
        if not state.get("online") or str(state.get("account_id") or "") != ref["account_id"]:
            raise ValueError("QQ login account changed; refresh the resource list")
        return ref

    def _export_message_media(self, row: dict[str, Any], name: Any,
                              digest: Any) -> dict[str, Any] | None:
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        timestamp = row.get("timestamp") or raw.get("msgTime")
        try:
            timestamp = int(timestamp)
            if abs(timestamp) >= 100_000_000_000:
                timestamp //= 1000
        except (TypeError, ValueError):
            return None
        peer_uid = str(row.get("conversation_id") or "")
        raw_peer = raw.get("peerUid") or raw.get("peerUin") or peer_uid
        try:
            chat_type = int(row.get("chatType") or raw.get("chatType") or 1)
        except (TypeError, ValueError):
            chat_type = 1
        if not peer_uid or not raw_peer:
            return None
        created = self._request("POST", "/api/messages/export", {
            "peer": {"chatType": chat_type, "peerUid": str(raw_peer)},
            "sessionName": "Local media lookup",
            "filter": {"startTime": timestamp - 2, "endTime": timestamp + 2},
            "options": {"skipDownloadResourceTypes": [], "filterPureImageMessages": False},
            "format": "HTML",
        })
        task_id = created.get("taskId")
        if not isinstance(task_id, str) or not task_id:
            return None
        deadline = time.monotonic() + min(max(self.timeout, 1.0), 45.0)
        task: dict[str, Any] = {}
        while time.monotonic() < deadline:
            task = self._request("GET", f"/api/tasks/{quote(task_id, safe='')}")
            status = task.get("status")
            if status in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.25)
        if task.get("status") != "completed":
            return None
        file_name = task.get("fileName") or created.get("fileName")
        if not isinstance(file_name, str) or not file_name:
            return None
        resources = self._request("GET", f"/api/resources/export/{quote(file_name, safe='')}")
        items = resources.get("resources", [])
        if not isinstance(items, list):
            return None
        name_key, digest_key = str(name or "").casefold(), str(digest or "").casefold()
        return next((item for item in items if isinstance(item, dict)
                     and ((name_key and (str(item.get("fileName") or "").casefold() == name_key
                                         or str(item.get("fileName") or "").casefold().endswith("_" + name_key)))
                          or (digest_key and digest_key in str(item.get("fileName") or "").casefold()))), None)
