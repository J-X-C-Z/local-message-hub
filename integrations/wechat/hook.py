"""Loopback client for the optional WeChatAgent-compatible native Hook."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from .sender import (
    MAX_TEXT,
    WeChatDeliveryUnknown,
    WeChatGUISender,
    WeChatSendError,
    _WXID_RE,
)


ROOT = Path(__file__).resolve().parents[2]
LOCAL = Path(os.environ.get("LMH_STORAGE_DIR", ROOT / ".local"))
DEFAULT_CONFIG = LOCAL / "wechat-hook.json"


class _RejectRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def config_path():
    return Path(os.environ.get("WECHAT_HOOK_CONFIG_FILE", DEFAULT_CONFIG))


def hook_configured():
    return config_path().is_file()


def load_config(path=None):
    path = Path(path or config_path())
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise WeChatSendError("wechat_hook_config_invalid") from None
    if not isinstance(value, dict) or set(value) - {
            "base_url", "token_file", "expected_version",
            "request_timeout_seconds", "receipt_timeout_seconds"}:
        raise WeChatSendError("wechat_hook_config_invalid")
    base_url = value.get("base_url")
    if not isinstance(base_url, str):
        raise WeChatSendError("wechat_hook_config_invalid")
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError:
        raise WeChatSendError("wechat_hook_config_invalid") from None
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment or not port):
        raise WeChatSendError("wechat_hook_loopback_required")
    expected = value.get("expected_version")
    if not isinstance(expected, str) or not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", expected):
        raise WeChatSendError("wechat_hook_config_invalid")
    token_file = value.get("token_file")
    if not isinstance(token_file, str) or not token_file:
        raise WeChatSendError("wechat_hook_token_missing")
    token_path = Path(token_file)
    if not token_path.is_absolute():
        token_path = path.parent / token_path
    try:
        token = token_path.read_text(encoding="ascii").strip()
    except OSError:
        raise WeChatSendError("wechat_hook_token_missing") from None
    if not re.fullmatch(r"[a-f0-9]{64}", token):
        raise WeChatSendError("wechat_hook_token_invalid")
    request_timeout = value.get("request_timeout_seconds", 5)
    receipt_timeout = value.get("receipt_timeout_seconds", 45)
    if (type(request_timeout) is not int or not 1 <= request_timeout <= 30
            or type(receipt_timeout) is not int or not 5 <= receipt_timeout <= 120):
        raise WeChatSendError("wechat_hook_config_invalid")
    return {
        "base_url": f"http://127.0.0.1:{port}",
        "token": token,
        "expected_version": expected,
        "request_timeout_seconds": request_timeout,
        "receipt_timeout_seconds": receipt_timeout,
    }


class WeChatHookTextSender(WeChatGUISender):
    """Send one text through the native Hook, then prove it in the local DB."""

    def __init__(self, provider_factory=None, reader_factory=None, timeout=45.0,
                 sleep=time.sleep, config_loader=load_config, transport=None):
        super().__init__(
            provider_factory=provider_factory,
            reader_factory=reader_factory,
            timeout=timeout,
            sleep=sleep,
            win32=object(),
        )
        self.config_loader = config_loader
        self.transport = transport or self._http_transport

    def _http_transport(self, config, method, path, payload=None):
        headers = {
            "Authorization": "Bearer " + config["token"],
            "Accept": "application/json",
        }
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = Request(config["base_url"] + path, data=data, headers=headers, method=method)
        opener = build_opener(ProxyHandler({}), _RejectRedirect())
        try:
            with opener.open(request, timeout=config["request_timeout_seconds"]) as response:
                if response.status != 200:
                    raise WeChatSendError("wechat_hook_http_error")
                raw = response.read(65537)
        except HTTPError as error:
            code = error.code
            error.close()
            raise WeChatSendError(
                "wechat_hook_unauthorized" if code == 401 else "wechat_hook_rejected") from None
        except (TimeoutError, URLError, OSError):
            raise WeChatDeliveryUnknown("wechat_hook_delivery_unknown") from None
        if len(raw) > 65536:
            raise WeChatSendError("wechat_hook_response_invalid")
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise WeChatSendError("wechat_hook_response_invalid") from None
        if not isinstance(result, dict):
            raise WeChatSendError("wechat_hook_response_invalid")
        return result

    def health(self, config=None):
        config = config or self.config_loader()
        result = self.transport(config, "GET", "/WeChatAgent/health")
        if (result.get("backend") != "wechat-hook"
                or result.get("startupState") != "ready"
                or result.get("version") != config["expected_version"]):
            raise WeChatSendError("wechat_hook_version_mismatch")
        accounts = result.get("databaseAccounts")
        if not isinstance(accounts, list):
            raise WeChatSendError("wechat_hook_health_invalid")
        return result

    def readiness(self, capability_enabled=True):
        result = {
            "capability_enabled": bool(capability_enabled),
            "window_found": True,
            "window_foreground": False,
            "input_available": True,
            "account_id": None,
            "state": "unavailable",
            "transport": "wechat-hook",
        }
        if not capability_enabled:
            result["state"] = "capability_disabled"
            return result
        try:
            health = self.health()
        except WeChatSendError as error:
            result["state"] = error.code
            return result
        accounts = health["databaseAccounts"]
        if not accounts:
            result["state"] = "client_logged_out"
        elif len(accounts) == 1 and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(accounts[0])):
            result["account_id"] = str(accounts[0])
            result["state"] = "ready"
        else:
            result["state"] = "account_ambiguous"
        return result

    def deliver(self, request):
        if request.get("attachment"):
            raise WeChatSendError("wechat_send_media_unsupported")
        text = request.get("text")
        if not isinstance(text, str) or not text.strip():
            raise WeChatSendError("wechat_send_text_empty")
        if len(text) > MAX_TEXT:
            raise WeChatSendError("wechat_send_text_too_long")
        conversation_id = request.get("conversation_id")
        if not isinstance(conversation_id, str) or not _WXID_RE.fullmatch(conversation_id):
            raise WeChatSendError("wechat_send_conversation_invalid")
        config = self.config_loader()
        health = self.health(config)
        accounts = health["databaseAccounts"]
        if len(accounts) != 1:
            raise WeChatSendError("wechat_hook_account_unavailable")
        account_id = str(accounts[0])
        if request.get("account_id") != account_id:
            raise WeChatSendError("wechat_account_mismatch")
        watermark = self._watermark(conversation_id)
        result = self.transport(
            config, "POST", "/SendTextMsg",
            {"wxidorgid": conversation_id, "msg": text})
        if result.get("ret") != 0:
            raise WeChatSendError("wechat_hook_rejected")
        receipt = self._verify(conversation_id, text, watermark, account_id)
        return {"message_id": receipt, "account_id": account_id}
