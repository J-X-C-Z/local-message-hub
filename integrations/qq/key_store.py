"""Current-user DPAPI storage for QQ database keys."""
from __future__ import annotations

import base64
import binascii
import ctypes
import json
import os
from pathlib import Path
import re
import tempfile
from ctypes import wintypes


_PREFIX = "dpapi:v1:"
_KEY = re.compile(r"[\x20-\x7e]{16}|hex:[a-fA-F0-9]{64}")
_ENTROPY = b"Local Message Hub QQ database key v1"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class QQKeyProtectionError(RuntimeError):
    pass


def _crypt(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise QQKeyProtectionError("QQ 数据库密钥仅支持 Windows 当前用户 DPAPI 存储")
    source_buffer = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)))
    entropy_buffer = ctypes.create_string_buffer(_ENTROPY)
    entropy = _DataBlob(len(_ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_byte)))
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
        ok = crypt.CryptProtectData(ctypes.byref(source), "Local Message Hub QQ database key",
                                    ctypes.byref(entropy), None, None, 1, ctypes.byref(output))
    else:
        crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob),
                                             ctypes.POINTER(wintypes.LPWSTR),
                                             ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                                             ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(_DataBlob)]
        crypt.CryptUnprotectData.restype = wintypes.BOOL
        ok = crypt.CryptUnprotectData(ctypes.byref(source), None, ctypes.byref(entropy), None,
                                      None, 1, ctypes.byref(output))
    if not ok:
        raise QQKeyProtectionError("QQ 数据库密钥无法由当前 Windows 用户解锁")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel.LocalFree(output.pbData)


def protect_key(key: str) -> str:
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError("QQ 数据库密钥格式无效")
    encrypted = _crypt(key.encode("ascii"), True)
    return _PREFIX + base64.b64encode(encrypted).decode("ascii")


def load_key(value):
    """Return (plaintext key, legacy_plaintext); protected data never falls back."""
    if not isinstance(value, str) or not value:
        return "", False
    if value.startswith(_PREFIX):
        try:
            encrypted = base64.b64decode(value[len(_PREFIX):], validate=True)
            plaintext = _crypt(encrypted, False).decode("ascii")
        except (binascii.Error, UnicodeDecodeError, QQKeyProtectionError) as exc:
            raise QQKeyProtectionError("QQ 数据库密钥无法由当前 Windows 用户解锁") from exc
        if not _KEY.fullmatch(plaintext):
            raise QQKeyProtectionError("QQ 数据库密钥密文格式无效")
        return plaintext, False
    if not _KEY.fullmatch(value):
        raise ValueError("QQ 数据库密钥格式无效")
    return value, True


def persist_key(path, config, key: str) -> None:
    """Atomically persist a DPAPI-protected key while preserving other settings."""
    path = Path(path)
    updated = dict(config)
    updated["key"] = protect_key(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(updated, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def migrate_legacy_key(path, expected_key: str) -> bool:
    """Migrate only if the config still contains the verified legacy value."""
    path = Path(path)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QQKeyProtectionError("QQ 数据库密钥配置无法安全迁移") from exc
    if not isinstance(config, dict) or config.get("key") != expected_key:
        return False
    persist_key(path, config, expected_key)
    return True
