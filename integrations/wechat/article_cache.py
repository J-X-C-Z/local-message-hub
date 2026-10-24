"""Read an exact public article from WeChat's local Chromium blockfile cache."""
from __future__ import annotations

import os
from pathlib import Path
import re
import struct
import zlib

from integrations.campus.adapter import public_wechat_url

MAX_HTML_BYTES = 4 * 1024 * 1024
MAX_CACHE_BYTES = 16 * 1024 * 1024
_BLOCK_SIZES = {2: 256, 3: 1024, 4: 4096}
_MAGIC = 0xC104CAC3


def _shared_open(path):
    if os.name != 'nt':
        return path.open('rb')
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x80000000, 7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), 'article cache unavailable')
    return os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY), 'rb')


def _cache_roots():
    appdata = os.environ.get('APPDATA')
    if not appdata:
        return []
    profiles = Path(appdata) / 'Tencent/xwechat/radium/web/profiles'
    try:
        # Only browser article profiles; never inspect account, cookie or login stores.
        names = sorted(p for p in profiles.iterdir() if p.is_dir() and
                       (p.name == 'multitab' or p.name.startswith('multitab_') or
                        p.name == 'webview' or p.name.startswith('webview_')))
        return [p / 'Cache/Cache_Data' for p in names[:16]]
    except OSError:
        return []


def _stream(root, address, size, limit):
    if not address & 0x80000000 or not 0 < size <= limit:
        return None
    kind = (address >> 28) & 7
    if kind == 0:
        path, offset = root / f'f_{address & 0x0fffffff:06x}', 0
    elif kind in _BLOCK_SIZES:
        block_size = _BLOCK_SIZES[kind]
        capacity = (((address >> 24) & 3) + 1) * block_size
        if size > capacity:
            return None
        path = root / f'data_{(address >> 16) & 255}'
        offset = 8192 + (address & 65535) * block_size
    else:
        return None
    try:
        with _shared_open(path) as file:
            if kind:
                header = file.read(16)
                if len(header) != 16 or struct.unpack_from('<I', header)[0] != _MAGIC or \
                        struct.unpack_from('<I', header, 12)[0] != block_size:
                    return None
            file.seek(offset)
            data = file.read(size)
        return data if len(data) == size else None
    except OSError:
        return None


def _decode_body(body, headers):
    encoding = re.search(rb'(?:^|[\x00\r\n])content-encoding:\s*([^\x00\r\n]+)', headers, re.I)
    name = encoding.group(1).strip().lower() if encoding else b''
    if name in (b'gzip', b'deflate'):
        decoder = zlib.decompressobj(31 if name == b'gzip' else zlib.MAX_WBITS)
        try:
            body = decoder.decompress(body, MAX_HTML_BYTES + 1)
        except zlib.error:
            return None
        if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
            return None
    elif name not in (b'', b'identity'):
        return None
    if len(body) > MAX_HTML_BYTES:
        return None
    try:
        return body.decode('utf-8')
    except UnicodeError:
        return None


def _read_cache(root, wanted):
    try:
        with _shared_open(root / 'data_1') as file:
            raw = file.read(MAX_CACHE_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_CACHE_BYTES or len(raw) < 8192 or \
            struct.unpack_from('<I', raw)[0] != _MAGIC or struct.unpack_from('<I', raw, 12)[0] != 256:
        return None
    for offset in range(8192, len(raw) - 255, 256):
        entry = raw[offset:offset + 256]
        if struct.unpack_from('<i', entry, 20)[0] != 0:
            continue
        length, address = struct.unpack_from('<iI', entry, 32)
        if not 0 < length <= 8192:
            continue
        key = _stream(root, address, length, 8192) if address else entry[96:96 + length]
        if key is None or len(key) != length:
            continue
        match = re.search(rb'https?://mp\.weixin\.qq\.com/s(?:[/?][^\x00\s]*)?', key)
        if not match:
            continue
        try:
            url = match.group().decode('utf-8')
            if public_wechat_url(re.sub(r'^http:', 'https:', url)) != wanted:
                continue
        except (UnicodeError, ValueError):
            continue
        sizes = struct.unpack_from('<4i', entry, 40)
        addresses = struct.unpack_from('<4I', entry, 56)
        headers = _stream(root, addresses[0], sizes[0], 64 * 1024)
        body = _stream(root, addresses[1], sizes[1], MAX_HTML_BYTES)
        if headers is None or body is None:
            continue
        html = _decode_body(body, headers)
        if html is not None:
            return html
    return None


def read_cached_article_html(url):
    """Return only the requested public article HTML, without network or cache writes."""
    try:
        wanted = public_wechat_url(url)
    except (ValueError, TypeError):
        return None
    for root in _cache_roots():
        html = _read_cache(root, wanted)
        if html is not None:
            return html
    return None
