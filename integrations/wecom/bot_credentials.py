"""Current-user DPAPI storage for the independent WeCom bot credentials."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import tempfile
from ctypes import wintypes


_BOT_ID = re.compile(r"^[A-Za-z0-9_@.-]{1,128}$")


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _crypt(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("企微机器人凭据仅支持 Windows 当前用户 DPAPI 存储")
    source_buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    output = _DataBlob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if protect:
        crypt.CryptProtectData.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR,
                                           ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                                           ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(_DataBlob)]
        crypt.CryptProtectData.restype = wintypes.BOOL
        ok = crypt.CryptProtectData(ctypes.byref(source), "Local Message Hub", None,
                                    None, None, 1, ctypes.byref(output))
    else:
        crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob),
                                             ctypes.POINTER(wintypes.LPWSTR),
                                             ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                                             ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(_DataBlob)]
        crypt.CryptUnprotectData.restype = wintypes.BOOL
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, None, None, None,
                                      1, ctypes.byref(output))
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows credential protection failed")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel.LocalFree(output.pbData)


class WeComBotCredentials:
    def __init__(self, path):
        self.path = Path(path)

    def status(self):
        credentials = self.get()
        return {"configured": credentials is not None,
                "bot_id": credentials["bot_id"] if credentials else "",
                "identity": "企业微信智能机器人（非当前员工身份）"}

    def get(self):
        try:
            value = json.loads(_crypt(self.path.read_bytes(), False).decode("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("企微机器人凭据不可用") from exc
        if (not isinstance(value, dict) or not isinstance(value.get("bot_id"), str)
                or not isinstance(value.get("secret"), str)):
            raise RuntimeError("企微机器人凭据格式无效")
        return value

    def set(self, bot_id, secret):
        # Keep stored identities compatible with SendService's account-id contract.
        if not isinstance(bot_id, str) or not _BOT_ID.fullmatch(bot_id):
            raise ValueError("机器人 ID 无效")
        if not isinstance(secret, str) or not 8 <= len(secret) <= 512 or not secret.isascii():
            raise ValueError("机器人密钥长度或编码无效")
        encrypted = _crypt(json.dumps({"bot_id": bot_id, "secret": secret},
                                      separators=(",", ":")).encode("utf-8"), True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp",
                                          dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return self.status()

    def delete(self):
        self.path.unlink(missing_ok=True)
        return self.status()
