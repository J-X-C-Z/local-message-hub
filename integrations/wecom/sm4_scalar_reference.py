"""Compatibility shim for the legacy SM4 block API, backed by cryptography."""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def expand(key: bytes) -> bytes:
    """Return the key token expected by the legacy decrypt(round_keys, block) API."""
    key = bytes(key)
    if len(key) != 16:
        raise ValueError("SM4 requires a 16-byte key")
    return key


def decrypt(round_keys: bytes, block: bytes) -> bytes:
    """Decrypt one 16-byte SM4 block using the installed cryptography backend."""
    key = bytes(round_keys)
    block = bytes(block)
    if len(key) != 16 or len(block) != 16:
        raise ValueError("SM4 key and block must each be 16 bytes")
    decryptor = Cipher(algorithms.SM4(key), modes.ECB()).decryptor()
    return decryptor.update(block) + decryptor.finalize()

