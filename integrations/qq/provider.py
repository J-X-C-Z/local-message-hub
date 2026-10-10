"""Read-only adapter for the local qq-chat-exporter/NapCat QCE service."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:40653"
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


class QQProviderError(RuntimeError):
    """Raised when the local QCE service cannot satisfy a read request."""


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
        self._validate_local_url()

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
            key: data.get(key)
            for key in ("isLogin", "isOffline", "loginPhase", "qrLoginAccepted", "coreReady")
        }

    def login_qr(self, refresh: bool = False) -> dict[str, Any]:
        """Return the QR payload for the local UI, never NapCat credentials."""
        state = self.login_status()
        if state.get("coreReady"):
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
                 *, authenticated: bool = True) -> dict[str, Any]:
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
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
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
        while time.monotonic() < deadline:
            try:
                result = self._launch_result(refresh=refresh)
                result.update({"pid": self._process.pid, "log_file": str(log_file)})
                return result
            except QQProviderError:
                pass
            time.sleep(0.25)
        raise QQProviderError("NapCat login API did not become ready; QQ was not confirmed connected")

    def _validate_qce_binary(self) -> None:
        binary = self.bundle_dir / "qce-server.exe"
        try:
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
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

    def _set_runtime_config(self, filename: str, updates: dict[str, Any]) -> None:
        path = self.bundle_dir / "config" / filename
        try:
            config = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QQProviderError(f"Could not read bundled {filename} runtime config") from exc
        if not isinstance(config, dict):
            raise QQProviderError(f"Bundled {filename} runtime config is invalid")
        config.update(updates)
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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

    def messages(self, conversation: dict[str, Any] | str, limit: int = 50,
                 offset: int = 0) -> list[dict[str, Any]]:
        if limit < 1 or limit > 2000:
            raise ValueError("limit must be between 1 and 2000")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        if isinstance(conversation, str):
            peer_uid, chat_type = conversation, 1
        elif isinstance(conversation, dict):
            peer_uid = str(conversation.get("peerUid") or conversation.get("id") or "")
            chat_type = conversation.get("chatType", 2 if conversation.get("type") == "group" else 1)
        else:
            raise TypeError("conversation must be a peer id or conversation object")
        if not peer_uid:
            raise ValueError("conversation requires peerUid or id")

        page_size = limit
        page = offset // page_size + 1
        skip = offset % page_size
        raw_messages: list[dict[str, Any]] = []
        while len(raw_messages) < skip + limit:
            data = self._request("POST", "/api/messages/fetch", {
                "peer": {"chatType": int(chat_type), "peerUid": peer_uid},
                "page": page,
                "limit": page_size,
            })
            batch = data.get("messages", [])
            if not isinstance(batch, list) or not batch:
                break
            raw_messages.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < page_size:
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
        if end_time < start_time:
            raise ValueError("结束时间不能早于开始时间")
        if not 1 <= max_messages <= 100000 or not 1 <= max_sequence_queries <= 100000:
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
                break
        raw_kind = message.get("msgType")
        if raw_kind == 5:
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
            "raw": message,
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
            content = self._request_bytes(resource_url)
            cache = self._media_cache_dir()
            cache.mkdir(parents=True, exist_ok=True)
            safe_name = Path(filename).name.replace("\x00", "_") or "media"
            suffix = hashlib.sha256(content).hexdigest()[:12]
            target = cache / f"{suffix}-{safe_name}"
            if not target.exists():
                target.write_bytes(content)
            return {"available": True, "path": str(target.resolve()), "reason": None}
        except (QQProviderError, OSError) as exc:
            return {"available": False, "path": None, "reason": str(exc)}

    @staticmethod
    def _media_cache_dir() -> Path:
        storage = Path(os.environ.get("LMH_STORAGE_DIR", Path(__file__).resolve().parents[2] / ".local"))
        return storage / "qq" / "media-cache"

    def _request_bytes(self, path: str) -> bytes:
        if not path.startswith("/") or path.startswith("//"):
            raise QQProviderError("QCE returned an invalid local resource path")
        token = self._token()
        if not token:
            raise QQProviderError("QCE access token is unavailable")
        request = Request(self.base_url + path, headers={
            "Accept": "*/*", "Authorization": f"Bearer {token}",
        }, method="GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except HTTPError as exc:
            raise QQProviderError(f"QCE media download failed ({exc.code})") from None
        except (URLError, TimeoutError, OSError) as exc:
            raise QQProviderError(f"QCE media download unavailable: {type(exc).__name__}") from None

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
