"""Loopback-only OneBot 11 text delivery for the bundled NapCat runtime."""
from __future__ import annotations

import json
from http.client import HTTPException
import os
import secrets
import socket
from pathlib import Path
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


_SERVER_NAME = "Local Message Hub"
_CONFIG = "onebot11.json"


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class QQSendError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class QQDeliveryUnknown(QQSendError):
    """The request may have reached QQ, but no trustworthy receipt was obtained."""

    delivery_unknown = True


def ensure_local_http_server(config_path):
    """Enable one authenticated, loopback-only OneBot endpoint in NapCat config."""
    path = Path(config_path)
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QQSendError("onebot_config_unavailable") from exc
    if not isinstance(config, dict):
        raise QQSendError("onebot_config_invalid")
    network = config.setdefault("network", {})
    if not isinstance(network, dict):
        raise QQSendError("onebot_network_config_invalid")
    servers = network.setdefault("httpServers", [])
    if not isinstance(servers, list):
        raise QQSendError("onebot_http_config_invalid")
    server = next((item for item in servers
                   if isinstance(item, dict) and item.get("name") == _SERVER_NAME), None)
    if server is None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = {"name": _SERVER_NAME, "enable": True, "host": "127.0.0.1",
                  "port": port, "enableCors": False, "enableWebsocket": False,
                  "messagePostFormat": "array", "token": secrets.token_urlsafe(32), "debug": False}
        servers.append(server)
    else:
        server.update({"enable": True, "host": "127.0.0.1", "enableCors": False,
                       "enableWebsocket": False, "messagePostFormat": "array", "debug": False})
        if not isinstance(server.get("port"), int) or not 1 <= server["port"] <= 65535:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                server["port"] = sock.getsockname()[1]
        if not isinstance(server.get("token"), str) or len(server["token"]) < 32:
            server["token"] = secrets.token_urlsafe(32)
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
        raise QQSendError("onebot_config_write_failed") from exc
    return {"host": "127.0.0.1", "port": server["port"], "token": server["token"]}


class NapCatOneBotSender:
    def __init__(self, config_path, timeout=8.0, opener=None):
        self.config_path = Path(config_path)
        self.timeout = min(max(float(timeout), 1.0), 15.0)
        self.opener = opener or build_opener(_RejectRedirects()).open

    def deliver(self, request):
        endpoint = self._endpoint()
        if endpoint["host"] != "127.0.0.1":
            raise QQSendError("onebot_endpoint_not_loopback")
        action = "send_private_msg" if request["conversation_type"] == "private" else "send_group_msg"
        target_key = "user_id" if action == "send_private_msg" else "group_id"
        body = json.dumps({target_key: int(request["conversation_id"]),
                           "message": [{"type": "text", "data": {"text": request["text"]}}]},
                          ensure_ascii=False).encode("utf-8")
        http_request = Request(f"http://127.0.0.1:{endpoint['port']}/{action}", data=body,
                               headers={"Content-Type": "application/json",
                                        "Authorization": "Bearer " + endpoint["token"]},
                               method="POST")
        try:
            with self.opener(http_request, timeout=self.timeout) as response:
                payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
        except TimeoutError:
            raise TimeoutError("OneBot request timed out") from None
        except HTTPError as exc:
            if 300 <= exc.code < 400 or exc.code == 408 or exc.code >= 500:
                raise QQDeliveryUnknown("onebot_http_" + str(exc.code)) from None
            raise QQSendError("onebot_http_" + str(exc.code)) from None
        except (URLError, OSError, HTTPException):
            raise QQDeliveryUnknown("onebot_transport_unknown") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise QQDeliveryUnknown("onebot_invalid_response") from None
        if not isinstance(payload, dict):
            raise QQDeliveryUnknown("onebot_invalid_response")
        if payload.get("status") == "failed" and isinstance(payload.get("retcode"), int) and payload["retcode"] != 0:
            raise QQSendError("onebot_rejected")
        if payload.get("status") != "ok" or payload.get("retcode") != 0:
            raise QQDeliveryUnknown("onebot_invalid_receipt")
        data = payload.get("data")
        message_id = data.get("message_id") if isinstance(data, dict) else None
        if message_id is None:
            raise QQDeliveryUnknown("onebot_receipt_missing")
        if isinstance(message_id, bool) or not isinstance(message_id, int) or not -(2 ** 31) <= message_id < 2 ** 31:
            raise QQDeliveryUnknown("onebot_invalid_receipt")
        return {"message_id": str(message_id)}

    def readiness(self, capability_enabled=True):
        """Explicit, read-only OneBot login probe; never sends a chat message."""
        result = {"capability_enabled": bool(capability_enabled),
                  "endpoint_configured": False, "endpoint_reachable": False,
                  "account_online": None, "account_id": None, "nickname": None,
                  "state": "capability_disabled" if not capability_enabled else "not_configured"}
        try:
            endpoint = self._endpoint()
        except QQSendError:
            return result
        result["endpoint_configured"] = True
        result["endpoint"] = f"127.0.0.1:{endpoint['port']}"
        if not capability_enabled:
            return result

        request = Request(f"http://127.0.0.1:{endpoint['port']}/get_login_info",
                          data=b"{}", headers={"Content-Type": "application/json",
                          "Authorization": "Bearer " + endpoint["token"]}, method="POST")
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read(64 * 1024)
            result["endpoint_reachable"] = True
            payload = json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            result["endpoint_reachable"] = True
            result["state"] = "unauthorized" if exc.code in (401, 403) else "api_unavailable"
            return result
        except (TimeoutError, URLError, OSError, HTTPException):
            result["state"] = "unreachable"
            return result
        except (UnicodeDecodeError, json.JSONDecodeError):
            result["endpoint_reachable"] = True
            result["state"] = "invalid_response"
            return result
        if not isinstance(payload, dict) or payload.get("status") != "ok" or payload.get("retcode") != 0:
            result["state"] = "api_unavailable"
            return result
        data = payload.get("data")
        if not isinstance(data, dict):
            result["state"] = "identity_unavailable"
            return result
        user_id = data.get("user_id")
        if isinstance(user_id, (str, int)) and not isinstance(user_id, bool) and str(user_id).isdigit():
            result["account_id"] = str(user_id)[:20]
            result["account_online"] = True
            result["state"] = "online"
        else:
            result["state"] = "identity_unavailable"
        nickname = data.get("nickname")
        if isinstance(nickname, str):
            result["nickname"] = nickname[:80]
        return result

    def _endpoint(self):
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
            servers = config["network"]["httpServers"]
            server = next(item for item in servers
                          if isinstance(item, dict) and item.get("name") == _SERVER_NAME)
        except (OSError, ValueError, KeyError, TypeError, StopIteration):
            raise QQSendError("onebot_config_unavailable") from None
        host, port, token = server.get("host"), server.get("port"), server.get("token")
        if (host != "127.0.0.1" or not isinstance(port, int) or not 1 <= port <= 65535 or
                not isinstance(token, str) or len(token) < 32 or server.get("enable") is not True):
            raise QQSendError("onebot_endpoint_invalid")
        return {"host": host, "port": port, "token": token}


class QQAccountBoundSender:
    """Verify the logged-in NapCat identity immediately before delivery."""

    def __init__(self, sender, status_reader):
        self.sender = sender
        self.status_reader = status_reader

    def deliver(self, request):
        try:
            status = self.status_reader()
        except Exception:
            raise QQSendError("qq_account_unavailable") from None
        if not isinstance(status, dict) or not status.get("online"):
            raise QQSendError("qq_account_offline")
        if str(status.get("account_id") or "") != request.get("account_id"):
            raise QQSendError("qq_account_mismatch")
        return self.sender.deliver(request)
