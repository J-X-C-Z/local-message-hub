"""Resolve WeFlow-compatible Moments media from an existing local cache only."""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


MAX_CACHED_MEDIA_BYTES = 512 * 1024 * 1024


def normalized_cache_url(raw_url):
    if not isinstance(raw_url, str) or not raw_url or len(raw_url) > 8192:
        return ''
    try:
        parsed = urlsplit(raw_url)
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
            return raw_url
        hostname = parsed.hostname.encode('idna').decode('ascii').lower()
        if ':' in hostname and not hostname.startswith('['):
            hostname = f'[{hostname}]'
        port = parsed.port
        if port and port != {'http': 80, 'https': 443}[parsed.scheme.lower()]:
            hostname = f'{hostname}:{port}'
        query = urlencode([(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                           if key.casefold() not in ('token', 'idx')])
        return hostname + parsed.path + (('?' + query) if query else '')
    except (ValueError, UnicodeError):
        return raw_url


def _video_url(raw_url):
    try:
        value = raw_url.casefold()
        return 'video' in value or '.mp4' in value
    except (TypeError, ValueError):
        return False


def _media_signature(path):
    try:
        size = path.stat().st_size
        with path.open('rb') as source:
            header = source.read(64)
    except OSError:
        return None
    if header.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg', 'image'
    if header.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png', 'image'
    if header.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif', 'image'
    if header.startswith(b'RIFF') and header[8:12] == b'WEBP':
        return 'image/webp', 'image'
    if header.startswith(b'BM'):
        return 'image/bmp', 'image'
    if len(header) >= 16 and header[4:8] == b'ftyp':
        box_size = int.from_bytes(header[:4], 'big')
        if (box_size < 16 or box_size > size or (box_size - 16) % 4):
            return None
        available = min(box_size, len(header))
        brands = {header[8:12].lower()}
        brands.update(header[index:index + 4].lower()
                      for index in range(16, available - 3, 4))
        if brands & {b'avif', b'avis'}:
            return 'image/avif', 'image'
        if brands & {b'heic', b'heix', b'hevc', b'hevx', b'mif1', b'msf1'}:
            return 'image/heic', 'image'
        return 'video/mp4', 'video'
    return None


def resolve_cached_media(cache_dir, raw_url, kind=None):
    """Return (path, mime, type) for a verified existing cache entry, else None."""
    if not raw_url:
        return None
    if kind not in (None, 'image', 'video'):
        raise ValueError('unsupported cached media kind')
    root = Path(cache_dir).expanduser()
    try:
        if root.is_symlink() or not root.is_dir():
            return None
        resolved_root = root.resolve(strict=True)
    except OSError:
        return None

    stable = normalized_cache_url(raw_url)
    is_video = kind == 'video' or (kind is None and _video_url(raw_url))
    extension = '.mp4' if is_video else '.jpg'
    digests = {hashlib.md5(stable.encode('utf-8')).hexdigest(),
               hashlib.md5(raw_url.encode('utf-8')).hexdigest()}
    for digest in digests:
        candidate = root / (digest + extension)
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(resolved_root):
                continue
            size = resolved.stat().st_size
            if size <= 0 or size > MAX_CACHED_MEDIA_BYTES:
                continue
        except OSError:
            continue
        signature = _media_signature(resolved)
        if signature and (kind is None or signature[1] == kind):
            return resolved, signature[0], signature[1]
    return None
