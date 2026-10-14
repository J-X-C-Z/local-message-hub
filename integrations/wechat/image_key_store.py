"""Current-user DPAPI storage for explicitly supplied WeChat image keys."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import tempfile
from ctypes import wintypes


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _crypt(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("微信图片密钥仅支持 Windows 当前用户 DPAPI 存储")
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
        ok = crypt.CryptProtectData(ctypes.byref(source), "Local Message Hub WeChat image key",
                                    None, None, None, 1, ctypes.byref(output))
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
        raise OSError(ctypes.get_last_error(), "Windows DPAPI operation failed")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel.LocalFree(output.pbData)


class WeChatImageKeyStore:
    def __init__(self, root):
        self.root = Path(root)

    @staticmethod
    def _account_id(account_id):
        if not isinstance(account_id, str) or not account_id or len(account_id) > 256:
            raise ValueError("微信账号标识无效")
        return account_id

    def _path(self, account_id):
        account_id = self._account_id(account_id)
        digest = hashlib.sha256(account_id.encode("utf-8")).hexdigest()
        return self.root / (digest + ".dpapi")

    def has(self, account_id):
        return self._path(account_id).is_file()

    def get(self, account_id):
        path = self._path(account_id)
        try:
            value = json.loads(_crypt(path.read_bytes(), False).decode("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("微信图片密钥不可用") from exc
        if value.get("account_id") != account_id or not isinstance(value.get("key"), str):
            raise RuntimeError("微信图片密钥账号绑定无效")
        return value["key"]

    def set(self, account_id, key):
        account_id = self._account_id(account_id)
        if not isinstance(key, str) or len(key) != 16 or not key.isascii() or not key.isprintable():
            raise ValueError("图片 AES 密钥必须是 16 位可打印 ASCII 字符")
        payload = json.dumps({"account_id": account_id, "key": key},
                             separators=(",", ":")).encode("utf-8")
        encrypted = _crypt(payload, True)
        path = self._path(account_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return {"configured": True}
