"""Copy verified images and stickers into the inbox-owned attachment store."""
import hashlib
import json
import re
from pathlib import Path
import urllib.request
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
from message_render import clean_content


def image_extension(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return '.png'
    if data.startswith(b'GIF87a') or data.startswith(b'GIF89a'): return '.gif'
    if data.startswith(b'\xff\xd8\xff'): return '.jpg'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP': return '.webp'
    return None


def save_image(private, identity, data, kind='Sticker'):
    extension = image_extension(data)
    if not extension or len(data) > 10 * 1024 * 1024: return {}
    mid = hashlib.sha256(identity.encode()).hexdigest()
    folder = private.parent / 'attachments' / mid
    target = folder / 'content' / ('image' + extension)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    (folder / 'manifest.json').write_text(json.dumps({'version': 1, 'id': mid,
        'name': target.name, 'size': len(data)}), encoding='utf-8')
    return {kind: {'id': mid, 'status': 'ready', 'quality': 'preview'}}


ALLOWED_HOSTS = {'vweixinf.tc.qq.com', 'wxapp.tc.qq.com', 'mmbiz.qpic.cn',
                 'mmbiz.qlogo.cn', 'emoji.qpic.cn'}


def valid_url(value):
    try:
        url = urlsplit(value)
        return url.scheme in ('http', 'https') and url.hostname in ALLOWED_HOSTS and not url.username and not url.password and url.port in (None, 80, 443)
    except ValueError:
        return False


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not valid_url(newurl): raise ValueError('Unsupported media redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def sticker(reader, row, private):
    try:
        raw = clean_content(row.get('content'))
        if len(raw) > 2 * 1024 * 1024 or '<!DOCTYPE' in raw or '<!ENTITY' in raw: return {}
        node = ET.fromstring(raw).find('emoji')
        if node is None: return {}
        md5 = node.get('md5', '').lower()
        if not re.fullmatch('[a-f0-9]{32}', md5): return {}
        identity = 'wechat-sticker:' + reader.account + ':' + md5
        mid = hashlib.sha256(identity.encode()).hexdigest()
        if (private.parent / 'attachments' / mid / 'manifest.json').exists():
            return {'Sticker': {'id': mid, 'status': 'ready'}}
        if md5 in reader._sticker_attempted: return {'Sticker': {'status': 'pending'}}
        # Check local HTTP and emoticon caches before fetching CDN content.
        for file in reader._sticker_files.get(md5, []):
            if file.stat().st_size <= 10 * 1024 * 1024:
                result = save_image(private, identity, file.read_bytes())
                if result: return result
        if reader._sticker_budget <= 0: return {'Sticker': {'status': 'pending'}}
        reader._sticker_budget -= 1
        reader._sticker_attempted.add(md5)
        for field in ('cdnurl', 'thumburl'):
            url = node.get(field, '')
            if not valid_url(url): continue
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.build_opener(SafeRedirect()).open(request, timeout=5) as response:
                    data = response.read(10 * 1024 * 1024 + 1)
                if field == 'cdnurl' and hashlib.md5(data).hexdigest() != md5:
                    continue
                result = save_image(private, identity, data)
                if result: return result
            except Exception: continue
    except Exception: pass
    return {'Sticker': {'status': 'pending'}}
