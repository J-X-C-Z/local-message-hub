"""Native document cards and locally cached sticker resources."""
from pathlib import Path
import hashlib
import json
import re
import tempfile
from urllib.parse import urlsplit
from protobuf_fields import fields, single_text
from local_io import connect_readonly
from wal_snapshot import read_snapshot
from video_cache import video_file

DOCUMENT_HOSTS = {'doc.weixin.qq.com', 'docs.qq.com', 'drive.weixin.qq.com'}


def display_text(value, limit=2000):
    value = value.strip()
    if value.startswith(('<', '*1*')) or any(not c.isprintable() and c not in '\r\n\t\u200d' for c in value):
        return ''
    return value[:limit]


def web_url(value):
    if not re.fullmatch(r'https?://[^\s<>\\]+', value, re.I) or len(value) > 4096:
        return ''
    try:
        parsed = urlsplit(value)
        return value if parsed.hostname and not parsed.username and not parsed.password else ''
    except ValueError:
        return ''


def web_card(row):
    # Use the destination field for each observed envelope, never its cover URL.
    code = int(row['type_code'])
    layout = {13: (1, 3, 4, '网页链接'), 38: (3, 1, 2, '通知'), 570: (0, 0, 2, '协作消息'),
              573: (4, 1, 2, '卡片')}.get(code)
    if layout is None:
        return None
    link, heading, summary, label = layout
    try:
        payload = fields(row['_raw'])
        title = display_text(single_text(payload, heading), 200)
        description = single_text(payload, summary)
        url = web_url(single_text(payload, link))
        if code in (570, 573):
            try:
                data = json.loads(description) if len(description) <= 65536 else None
            except (ValueError, RecursionError):
                data = None
            description = ''
            if isinstance(data, dict):
                if code == 570 and isinstance(data.get('text'), str):
                    # This collaborative text includes inline editor markers.
                    # Render the visible text without its formatting metadata.
                    description = ''.join(c for c in data['text'] if c.isprintable() or c in '\r\n\t\u200d')
                if code == 573:
                    header, detail = data.get('card_header'), data.get('card_desc')
                    if isinstance(header, dict) and isinstance(header.get('title'), str):
                        title = display_text(header['title'], 200) or title
                    if isinstance(detail, dict):
                        description = '\n'.join(detail[key] for key in ('title', 'sub_title')
                                                if isinstance(detail.get(key), str) and detail[key] != title)
        description = display_text(description)
        if title or description or url:
            return {'title': title or label, 'description': description, 'url': url, 'label': label}
    except (ValueError, TypeError):
        pass
    return None


def chat_record(row, image_sync=None):
    if int(row['type_code']) != 123:
        return None
    from export_wxwork import decode_text
    try:
        rows = fields(row['_raw']).get(1, [])
    except (ValueError, TypeError):
        rows = []
    entries = []
    for raw in rows[:60]:
        try:
            item = fields(raw)
        except (ValueError, TypeError):
            continue
        types = item.get(5, [])
        if len(types) != 1 or not isinstance(types[0], int):
            continue
        kind = types[0]
        labels = {0: '文字', 2: '文字', 13: '链接', 14: '图片', 15: '文件', 16: '语音',
                  20: '文件', 22: '视频', 29: '表情包', 123: '聊天记录'}
        label = labels.get(kind, '消息')
        body = item.get(101, []) if kind in (0, 2) else []
        text = display_text(decode_text(body[0])) if len(body) == 1 and isinstance(body[0], bytes) else ''
        entry = {'kind': label, 'text': text}
        if kind == 14 and image_sync:
            media = item.get(103, [])
            if len(media) == 1 and isinstance(media[0], bytes):
                image = image_sync({'type_code': 14, '_raw': media[0]})
                if image and image.get('status') == 'ready':
                    entry['image'] = image
        entries.append(entry)
    return {'title': '合并转发的聊天记录', 'entries': entries, 'partial': len(entries) < len(rows)}


def document_card(row):
    if int(row['type_code']) != 13:
        return None
    try:
        payload = fields(row['_raw'])
        url, title, description = single_text(payload, 1), single_text(payload, 3), single_text(payload, 4)
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname not in DOCUMENT_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
        spreadsheet = '[表格]' in description or parsed.path.startswith(('/sheet/', '/spreadsheet/'))
        return {'title': title or ('未命名表格' if spreadsheet else '在线文档'),
                'url': url, 'kind': 'spreadsheet' if spreadsheet else 'document',
                'description': description}
    except (ValueError, TypeError):
        return None


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128); value >>= 7
    result.append(value)
    return bytes(result)


def text_field(number, value):
    value = value.encode()
    return varint(number * 8 + 2) + varint(len(value)) + value


def image_extension(header):
    if header.startswith(b'\x89PNG\r\n\x1a\n'): return '.png'
    if header.startswith((b'GIF87a', b'GIF89a')): return '.gif'
    if header.startswith(b'\xff\xd8\xff'): return '.jpg'
    if header.startswith(b'RIFF') and header[8:12] == b'WEBP': return '.webp'
    return ''


class StickerSync:
    def __init__(self, profile, private, shared_reader, attachments):
        self.root = Path(profile).parent / 'Emotion'
        self.attachments = attachments
        self.mapping = {}
        for source in self.root.glob('*.db'):
            try:
                with tempfile.TemporaryDirectory(dir=private) as temporary:
                    target = Path(temporary) / 'emotions.db'
                    target.write_bytes(read_snapshot(source, reader=shared_reader))
                    conn = connect_readonly(target)
                    try:
                        for key, name in conn.execute('SELECT key,file_name FROM mapping WHERE type=0'):
                            self.mapping.setdefault(key, []).append(name)
                    finally:
                        conn.close()
            except Exception:
                continue

    def sync(self, row):
        if int(row['type_code']) != 29: return None
        try:
            payload = fields(row['_raw'])
            resource = single_text(payload, 6).lower()
            exact = [key for key in self.mapping if any(key.encode() == value for values in payload.values() for value in values if isinstance(value, bytes))]
            keys = exact or ([key for key in self.mapping if resource in key.lower()] if re.fullmatch('[a-f0-9]{32}', resource) else [])
            if len(keys) != 1: return {'status': 'pending', 'name': '表情包'}
            key = keys[0]
            paths = set()
            for name in self.mapping[key]:
                source = Path(name)
                if not source.is_absolute(): source = self.root / source
                if source.is_file() and not source.is_symlink() and source.resolve().is_relative_to(self.root.resolve()): paths.add(source.resolve())
            if len(paths) != 1: return {'status': 'pending', 'name': '表情包'}
            source = paths.pop()
            if source.stat().st_size > 10 * 1024 * 1024: return {'status': 'unsupported', 'name': '表情包'}
            with source.open('rb') as stream: extension = image_extension(stream.read(16))
            if not extension: return {'status': 'unsupported', 'name': '表情包'}
            # Reuse the document copier's containment, stability and hash checks.
            self.attachments.mapping[key] = {str(source)}
            raw = text_field(1, key) + text_field(2, '表情' + extension) + varint(32) + varint(source.stat().st_size)
            return self.attachments.attach({'type_code': 15, '_raw': raw}, preview=True)
        except (OSError, ValueError, TypeError):
            return {'status': 'pending', 'name': '表情包'}


class VideoSync:
    def __init__(self, profile, private, shared_reader, attachments):
        self.root = Path(profile).parent / 'Cache' / 'Video'
        self.attachments, self.mapping = attachments, {}
        for source in (Path(profile).parent / 'CacheMapping').glob('*.db'):
            try:
                with tempfile.TemporaryDirectory(dir=private) as temporary:
                    target = Path(temporary) / 'videos.db'
                    target.write_bytes(read_snapshot(source, reader=shared_reader))
                    conn = connect_readonly(target)
                    try:
                        for key, filename in conn.execute('SELECT key,file_name FROM mapping WHERE type=4'):
                            if isinstance(key, str) and isinstance(filename, str):
                                self.mapping.setdefault(key, set()).add(filename)
                    finally:
                        conn.close()
            except (OSError, ValueError):
                continue
            except Exception:
                # A cache mapping schema/version change must not stop message import.
                continue

    def sync(self, payload, size):
        key = single_text(payload, 2)
        if not key or size is None:
            return None
        identifier = hashlib.sha256((str(self.attachments.profile.resolve()) + '\0video\0' + key).encode()).hexdigest()
        candidates = set()
        for filename in self.mapping.get(key, ()):
            source = self.root / filename
            try:
                if not source.is_symlink() and source.resolve().is_relative_to(self.root.resolve()) and source.is_file():
                    candidates.add(source.resolve())
            except (OSError, ValueError):
                continue
        source = next(iter(candidates)) if len(candidates) == 1 else None
        return video_file(self.attachments.output, identifier, source, self.root, 'wecom', size, not self.attachments.on_demand)


class ImageSync:
    def __init__(self, profile, private, shared_reader, attachments):
        self.root = Path(profile).parent / 'Cache' / 'Image'
        self.attachments, self.mapping = attachments, {}
        for source in (Path(profile).parent / 'CacheMapping').glob('*.db'):
            try:
                with tempfile.TemporaryDirectory(dir=private) as temporary:
                    target = Path(temporary) / 'images.db'
                    target.write_bytes(read_snapshot(source, reader=shared_reader))
                    conn = connect_readonly(target)
                    try:
                        for key, filename in conn.execute('SELECT key,file_name FROM mapping WHERE type=2'):
                            self.mapping.setdefault(key, []).append(filename)
                    finally:
                        conn.close()
            except Exception:
                continue

    def sync(self, row):
        if int(row['type_code']) != 14: return None
        try:
            native = single_text(fields(row['_raw']), 1)
            if not native: return {'status': 'unsupported', 'name': '图片'}
            # Both forms are present in this client's cache-map table. A thumb
            # must remain labelled as a preview, never presented as an original.
            for key, quality in [(native, 'original'), (native + '_thumb', 'preview')]:
                candidates = set()
                for name in self.mapping.get(key, ()):
                    source = Path(name)
                    if not source.is_absolute(): source = self.root / source
                    if source.is_file() and not source.is_symlink() and source.resolve().is_relative_to(self.root.resolve()): candidates.add(source.resolve())
                if not candidates:
                    identifier=hashlib.sha256((str(self.attachments.profile.resolve())+'\0'+key).encode()).hexdigest()
                    try:
                        folder=self.attachments.output/identifier
                        saved=json.loads((folder/'manifest.json').read_text('utf-8'))
                        filename=saved.get('name','')
                        target=folder/'content'/filename
                        if Path(filename).name!=filename or not target.resolve().is_relative_to(self.attachments.output.resolve()) or target.is_symlink(): continue
                        with target.open('rb') as stream: supported=image_extension(stream.read(16))
                        if not supported: continue
                        raw=text_field(1,key)+text_field(2,filename)+varint(32)+varint(saved['size'])
                        result=self.attachments.attach({'type_code':15,'_raw':raw}, preview=True)
                        if result['status']=='ready': return {**result,'quality':quality}
                    except (OSError,ValueError,TypeError,KeyError):
                        pass
                    continue
                if len(candidates) != 1: continue
                source = candidates.pop()
                if source.stat().st_size > 10 * 1024 * 1024: continue
                with source.open('rb') as stream: extension = image_extension(stream.read(16))
                if not extension: continue
                self.attachments.mapping[key] = {str(source)}
                raw = text_field(1, key) + text_field(2, '图片' + extension) + varint(32) + varint(source.stat().st_size)
                result = self.attachments.attach({'type_code': 15, '_raw': raw}, preview=True)
                if result['status'] == 'ready': return {**result, 'quality': quality}
            return {'status': 'pending', 'name': '图片'}
        except (OSError, ValueError, TypeError):
            return {'status': 'pending', 'name': '图片'}
