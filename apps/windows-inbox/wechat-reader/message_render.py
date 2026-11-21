"""Display structured WeChat messages without exposing their transport XML."""
import hashlib
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from file_cache import file_description, valid_name


APP_LABELS = {'5': '链接', '6': '文件', '19': '聊天记录', '33': '小程序',
              '36': '小程序', '57': '引用消息', '2000': '转账', '2001': '红包',
              '4': '视频', '3': '音乐', '51': '视频号', '87': '群公告'}
QUOTE_MEDIA = {'3': ('image', '图片'), '34': ('voice', '语音'), '43': ('video', '视频'),
               '47': ('sticker', '表情包'), '11000': ('sticker', '表情包'), '48': ('location', '位置')}
XML_DECLARATION = re.compile(r'<!\[CDATA\[.*?\]\]>|(?P<unsafe><!\s*(?i:DOCTYPE|ENTITY))', re.S)


def parse_xml(text):
    if len(text) > 2 * 1024 * 1024 or any(m.lastgroup == 'unsafe' for m in XML_DECLARATION.finditer(text)):
        return None
    try:
        return ET.fromstring(text)
    except (ET.ParseError, ValueError):
        return None


def display_title(element, path):
    text = element.findtext(path, '').strip()
    return '' if text.startswith('<') else text[:2000]


def record_root(wrapper):
    root = (wrapper[0] if len(wrapper) else parse_xml((wrapper.text or '').strip())) if wrapper is not None else None
    return root if root is not None and root.tag == 'recordinfo' else None


def record_link(item):
    for field in ('weburlitem/url', 'weburlitem/link', 'link', 'streamweburl'):
        value = item.findtext(field, '').strip()
        if len(value) <= 4096 and re.fullmatch(r'https?://[^\s<>\\]+', value, re.I):
            try:
                u = urlsplit(value)
                if u.hostname and not u.username and not u.password:
                    return value
            except ValueError:
                pass
    return ''


def record_entries(root, resolve, path=(), budget=None):
    data = root.findall('./datalist/dataitem') if root is not None else []
    labels = {'1': '文字', '2': '图片', '3': '语音', '4': '视频', '5': '链接',
              '8': '文件', '15': '表情包', '17': '聊天记录', '29': '小程序', '37': '消息摘要'}
    entries = []
    if budget is None:
        budget = [450]
    for index, item in enumerate(data):
        if budget[0] <= 0:
            break
        budget[0] -= 1
        code = item.get('datatype', '')
        title = display_title(item, 'datatitle') or (display_title(item, 'weburlitem/title') if code == '5' else '')
        text = item.findtext('datadesc', '')[:12000] if code == '1' else display_title(item, 'datadesc')
        if not text and code == '5':
            text = display_title(item, 'weburlitem/desc')
        entry = {'kind': labels.get(code, '消息'), 'sender': display_title(item, 'sourcename')[:200],
                 'time': display_title(item, 'sourcetime')[:80], 'text': text}
        if title:
            entry['title'] = title
        if code == '5':
            url = record_link(item)
            if url:
                entry['url'] = url
        location = (*path, index)
        if code == '17' and len(path) < 4:
            nested = record_root(item.find('recordxml'))
            if nested is not None:
                items, partial = record_entries(nested, resolve, location, budget)
                entry['record'] = {'entries': items, 'partial': partial}
        if resolve and code in ('2', '4', '8'):
            entry.update(resolve(code, item, location, title if valid_name(title) else '') or {})
        entries.append(entry)
    return entries, len(data) > len(entries)


def chat_record(app, title, resolve=None):
    entries, partial = record_entries(record_root(app.find('recorditem')), resolve)
    return {'title': title or '合并转发的聊天记录', 'entries': entries,
            'summary': display_title(app, 'des') if not entries else '', 'partial': partial}


def render_quote(reference):
    if reference is None:
        return {'kind': 'unavailable', 'text': '原消息内容不可用'}
    code = reference.findtext('type', '').strip()
    content = reference.find('content')
    raw = (content.text or '').strip() if content is not None else ''
    sender = reference.findtext('displayname', '').strip()[:200]
    kind, text = QUOTE_MEDIA.get(code, ('unavailable', '原消息内容不可用'))
    if code == '1' and raw:
        kind, text = 'text', raw[:2000]
    elif code in {'49', '822083633'}:
        nested = content[0] if content is not None and len(content) else parse_xml(clean_content(raw).strip())
        app = None if nested is None else nested if nested.tag == 'appmsg' else nested.find('appmsg')
        if app is None:
            kind, text = 'card', '卡片消息'
        else:
            subtype = app.findtext('type', '').strip()
            label = APP_LABELS.get(subtype, '卡片消息')
            title = display_title(app, 'title') or display_title(app, 'mmreader/category/item/title')
            kind = {'4': 'video', '5': 'link', '6': 'file', '57': 'text'}.get(subtype, 'card')
            # For a quote of a reply, keep that reply's text without recursively
            # rendering the entire reference chain or mixing in its metadata.
            text = title if kind == 'text' and title else f'{label} · {title}' if title else label
    return {'sender': sender, 'kind': kind, 'text': text}


def message_reference(scope, server_id):
    # Keep 64-bit message IDs exact, and scope their anonymous lookup key
    # to the account and conversation. Never send the raw identifier to the UI.
    value = str(server_id or '').strip()
    if not scope or not re.fullmatch(r'-?[1-9][0-9]{0,19}', value):
        return ''
    return hashlib.sha256(('wechat-reference\0' + scope + '\0' + value).encode()).hexdigest()[:32]


def clean_content(content):
    content = str(content or '')
    # Group message transport prefixes are account identifiers, not body text.
    return re.sub(r'\A[A-Za-z0-9_@.\-]{3,128}:\r?\n', '', content, count=1)


def render_message(row, nickname=lambda value: value, scope='', record_media=None):
    content = clean_content(row.get('content'))
    code = int(row.get('local_type') or 0) & 0xffffffff
    if code == 43:
        root = parse_xml(content.strip()) if content.lstrip().startswith('<') else None
        info = root if root is not None and root.tag == 'videomsg' else root.find('videomsg') if root is not None else None

        def number(key, ceiling):
            value = info.get(key, '') if info is not None else ''
            if re.fullmatch(r'[0-9]{1,14}', value) and 0 < int(value) <= ceiling:
                return int(value)
            return None

        return {'Content': '[视频]', 'System': False, 'Video': {
            'name': '视频消息', 'status': 'pending', 'size': number('length', 2**40),
            'duration': number('playlength', 86400)}}
    # Ordinary text can itself contain XML examples; keep it literal.
    structured = code != 1 and re.match(r'\s*(?:<\?xml[^>]*>\s*)?<(?:msg|appmsg|sysmsg|voipmsg|voipinvitemsg|emoji|location)(?:\s|/?>)', content)
    if not structured:
        return {'Content': content or '[' + str(row.get('type') or '消息') + ']', 'System': code == 10000}
    root = parse_xml(content.strip())
    if root is None:
        return {'Content': '[' + str(row.get('type') or '消息') + ' · 内容暂不支持预览]', 'System': False}

    def text(path):
        value = root.findtext(path)
        return value.strip() if value else ''

    app = root.find('appmsg') if root.tag != 'appmsg' else root
    if app is not None and app.findtext('type', '').strip() == '57':
        reference = app.find('refermsg')
        quote = render_quote(reference)
        target = message_reference(scope, reference.findtext('svrid', '')) if reference is not None else ''
        if target:
            quote['target'] = target
        return {'Content': app.findtext('title', '').strip(), 'Quote': quote, 'System': False}
    if app is not None and app.findtext('type', '').strip() == '19':
        record = chat_record(app, display_title(app, 'title'), record_media)
        if record_media is not None:
            record['media_checked'] = True
        return {'Content': record['title'], 'ChatRecord': record, 'System': False}
    if app is not None and app.findtext('type', '').strip() == '6' and code == 49:
        description = file_description(app)
        if description:
            return {'Content': '[文件] ' + description['name'], 'System': False, '_file': description}
    items = root.findall('.//mmreader/category/item')
    if items:
        articles = []
        for item in items[:20]:
            title, url = item.findtext('title', '').strip(), item.findtext('url', '').strip()
            if title and re.match(r'^https?://mp\.weixin\.qq\.com/', url):
                articles.append({'title': title, 'description': item.findtext('digest', '').strip(),
                                 'url': re.sub(r'^http:', 'https:', url)})
        if articles: return {'Content': '\n'.join(a['title'] for a in articles), 'Articles': articles, 'System': False}
    if app is not None:
        subtype = app.findtext('type', '').strip()
        title = app.findtext('title', '').strip()
        if subtype == '62':
            template = app.findtext('patinfo/template', '').strip()
            if template:
                def replace(match):
                    uid = match.group(1)
                    name = nickname(uid)
                    return name if name and name != uid else '一位成员'
                result = re.sub(r'\$\{([^}]+)\}', replace, template)
            else:
                result = title or '拍一拍'
            return {'Content': result, 'System': True}
        label = APP_LABELS.get(subtype, '卡片消息')
        parts = ['[' + label + ']' + (' ' + title if title else '')]
        description = app.findtext('des', '').strip()
        if description and description != title and not description.lstrip().startswith('<'):
            parts.append(description[:2000])
        if subtype == '5':
            url = app.findtext('url', '').strip()
            if re.match(r'^https?://[^\s<>]+$', url):
                parts.append(url)
        return {'Content': '\n'.join(parts), 'System': False}
    labels = {3: '图片', 34: '语音', 43: '视频', 47: '表情包', 48: '位置', 50: '音视频通话', 10000: '系统通知'}
    if code == 48:
        location = root.find('location')
        if location is not None:
            return {'Content': '[位置] ' + (location.get('poiname') or location.get('label') or ''), 'System': False}
    if root.tag == 'sysmsg':
        summary = text('revokemsg/replacemsg') or text('content_template/plain')
        if summary and not summary.startswith('<'):
            return {'Content': summary, 'System': True}
    return {'Content': '[' + labels.get(code, str(row.get('type') or '消息')) + ']', 'System': code == 10000}
