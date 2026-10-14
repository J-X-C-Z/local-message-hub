"""Schema-checked, read-only parsing of WeChat's local Moments database."""
from __future__ import annotations

import re
import sqlite3
from xml.etree import ElementTree


MAX_XML_BYTES = 2 * 1024 * 1024
MAX_SCAN_ROWS = 100_000
MAX_SCAN_BYTES = 32 * 1024 * 1024
MAX_EXPORT_ROWS = MAX_SCAN_ROWS


def _tag(element):
    return element.tag.rsplit('}', 1)[-1].casefold() if isinstance(element.tag, str) else ''


def _children(element, *names):
    accepted = {name.casefold() for name in names}
    return [child for child in element.iter() if child is not element and _tag(child) in accepted]


def _children_without(element, excluded, *names):
    accepted = {name.casefold() for name in names}
    result = []
    stack = list(reversed(list(element)))
    while stack:
        child = stack.pop()
        tag = _tag(child)
        if tag in excluded:
            continue
        if tag in accepted:
            result.append(child)
        stack.extend(reversed(list(child)))
    return result


def _first_text(element, *names):
    for child in _children(element, *names):
        if child.text and child.text.strip():
            return child.text.strip()
    return ''


def _attribute(element, *names):
    accepted = {name.casefold() for name in names}
    for key, value in element.attrib.items():
        if key.rsplit('}', 1)[-1].casefold() in accepted and value:
            return value.strip()
    return ''


def _integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _number(value):
    try:
        number = float(value)
        return number if number == number and abs(number) != float('inf') else None
    except (TypeError, ValueError, OverflowError):
        return None


def _decode_xml(value):
    if isinstance(value, bytes):
        if len(value) > MAX_XML_BYTES:
            return None
        value = value.decode('utf-8', errors='replace')
    if not isinstance(value, str) or len(value.encode('utf-8', errors='replace')) > MAX_XML_BYTES:
        return None
    value = value.strip('\x00\ufeff \r\n\t')
    if not value.startswith('<') or re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', value, re.I):
        return None
    try:
        return ElementTree.fromstring(value)
    except ElementTree.ParseError:
        return None


def parse_post(tid, username, payload, fallback_time=0, nickname=''):
    root = _decode_xml(payload)
    if root is None:
        return None
    media = []
    for item in _children(root, 'media'):
        def normal_media_nodes(*names):
            return _children_without(item, {'livephoto'}, *names)

        def normal_media_text(*names):
            node = next(iter(normal_media_nodes(*names)), None)
            return node.text.strip() if node is not None and node.text and node.text.strip() else ''

        url = normal_media_text('url', 'videourl', 'extern_url')
        thumb = normal_media_text('thumb', 'thumburl', 'thumbaeskey')
        url_node = next(iter(normal_media_nodes('url', 'videourl', 'extern_url')), None)
        thumb_node = next(iter(normal_media_nodes('thumb', 'thumburl', 'thumbaeskey')), None)
        live_photo_node = next(iter(_children(item, 'livephoto')), None)
        live_photo = None
        if live_photo_node is not None:
            live_url_node = next(iter(_children(live_photo_node, 'url')), None)
            live_thumb_node = next(iter(_children(live_photo_node, 'thumb')), None)
            live_photo = {
                'url': _first_text(live_photo_node, 'url'),
                'thumb': _first_text(live_photo_node, 'thumb'),
                'token': (_attribute(live_url_node, 'token') if live_url_node is not None else '') or
                         (_attribute(live_thumb_node, 'token') if live_thumb_node is not None else ''),
                'thumbToken': _attribute(live_thumb_node, 'token') if live_thumb_node is not None else '',
                'key': ((_attribute(live_url_node, 'key') if live_url_node is not None else '') or
                        (_attribute(live_thumb_node, 'key') if live_thumb_node is not None else '')),
                'encIdx': ((_attribute(live_url_node, 'enc_idx', 'encidx') if live_url_node is not None else '') or
                           (_attribute(live_thumb_node, 'enc_idx', 'encidx') if live_thumb_node is not None else '')),
                'local_available': False,
            }
            if not live_photo['url'] and not live_photo['thumb']:
                live_photo = None
        if not url and not thumb and not live_photo:
            continue
        record = {
            'url': url,
            'thumb': thumb,
            'md5': (_attribute(url_node, 'md5') if url_node is not None else '') or normal_media_text('md5'),
            'token': ((_attribute(url_node, 'token') if url_node is not None else '') or
                      (_attribute(thumb_node, 'token') if thumb_node is not None else '') or
                      normal_media_text('token')),
            'thumbToken': _attribute(thumb_node, 'token') if thumb_node is not None else '',
            'key': ((_attribute(url_node, 'key') if url_node is not None else '') or
                    (_attribute(thumb_node, 'key') if thumb_node is not None else '') or normal_media_text('key')),
            'encIdx': ((_attribute(url_node, 'enc_idx', 'encidx') if url_node is not None else '') or
                       (_attribute(thumb_node, 'enc_idx', 'encidx') if thumb_node is not None else '') or
                       normal_media_text('encidx')),
            'local_available': False,
        }
        if live_photo:
            record['livePhoto'] = live_photo
        media.append(record)
    likes = []
    for item in _children(root, 'likeuser', 'like_user'):
        value = _first_text(item, 'username', 'user_name', 'id')
        if value:
            likes.append(value)
    comments = []
    seen_comments = set()
    for item in _children(root, 'commentuser', 'comment', 'user_comment'):
        content = _first_text(item, 'content')
        name = _first_text(item, 'nickname', 'nickName')
        if not content and not name:
            continue
        comment = {
            'id': _first_text(item, 'cmtid', 'commentid', 'comment_id', 'id'),
            'nickname': name,
            'username': _first_text(item, 'username'),
            'content': content,
            'refCommentId': _first_text(item, 'refcommentid', 'replycommentid', 'ref_comment_id'),
        }
        identity = (comment['id'], comment['username'], comment['content'])
        if identity not in seen_comments:
            comments.append(comment)
            seen_comments.add(identity)
    location = {}
    for key, aliases in {
        'city': ('city',), 'country': ('country',), 'poiName': ('poiname',),
        'poiAddress': ('poiaddress', 'address'), 'poiAddressName': ('poiaddressname',),
        'label': ('label',),
    }.items():
        value = _first_text(root, *aliases)
        if value:
            location[key] = value
    location_node = next(iter(_children(root, 'location')), None)
    if location_node is not None:
        coordinates = {
            'latitude': (('latitude', 'x'), -90, 90),
            'longitude': (('longitude', 'y'), -180, 180),
        }
        for key, (aliases, minimum, maximum) in coordinates.items():
            value = _attribute(location_node, *aliases) or _first_text(location_node, *aliases)
            number = _number(value)
            if number is not None and minimum <= number <= maximum:
                location[key] = number
    return {
        'id': str(tid), 'tid': str(tid), 'username': str(username or ''),
        'nickname': str(nickname or username or ''),
        'createTime': _integer(_first_text(root, 'createtime', 'create_time', 'timestamp'), fallback_time),
        'contentDesc': _first_text(root, 'contentdesc', 'content_desc', 'content'),
        'type': _integer(_first_text(root, 'type')),
        'media': media, 'likes': likes, 'comments': comments,
        'location': location or None,
    }


def read_timeline(connection: sqlite3.Connection, *, limit=20, offset=0,
                  usernames=None, keyword='', start_time=0, end_time=0, nickname_for=None):
    """Read known Moments rows, failing closed when the schema is unfamiliar."""
    limit = min(max(_integer(limit, 20), 1), MAX_EXPORT_ROWS)
    offset = max(_integer(offset), 0)
    start_time, end_time = max(_integer(start_time), 0), max(_integer(end_time), 0)
    accepted_users = {str(value).strip() for value in (usernames or []) if str(value).strip()}
    term = str(keyword or '').strip().casefold()
    table_row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND lower(name)=? LIMIT 1",
        ('snstimeline',),
    ).fetchone()
    if not table_row:
        return {'available': False, 'reason': 'moments_schema_unavailable', 'items': [], 'total': 0}
    table = table_row[0]
    columns = {str(row[1]).casefold(): str(row[1]) for row in
               connection.execute(f'PRAGMA table_info("{table.replace(chr(34), chr(34)*2)}")')}
    aliases = {
        'id': ('tid', 'id'), 'username': ('user_name', 'username', 'from_username'),
        'content': ('content', 'content_xml', 'raw_xml'),
        'time': ('create_time', 'createtime', 'timestamp'),
    }
    selected = {}
    for key, names in aliases.items():
        selected[key] = next((columns[name] for name in names if name in columns), None)
    if not all(selected[key] for key in ('id', 'username', 'content')):
        return {'available': False, 'reason': 'moments_schema_unsupported', 'items': [], 'total': 0}
    qtable = '"' + table.replace('"', '""') + '"'
    projection = [f'"{selected[key].replace(chr(34), chr(34)*2)}"' if selected[key] else 'NULL'
                  for key in ('id', 'username', 'content', 'time')]
    projection[2] = (f'substr("{selected["content"].replace(chr(34), chr(34)*2)}", 1, ?)'
                     if selected['content'] else 'NULL')
    cursor = connection.execute(
        f'SELECT {", ".join(projection)} FROM {qtable} '
        f'ORDER BY "{selected["id"].replace(chr(34), chr(34)*2)}" DESC LIMIT ?',
        (MAX_XML_BYTES + 1, MAX_SCAN_ROWS + 1),
    )
    page_posts = []
    total = 0
    scanned = 0
    scanned_bytes = 0
    truncated = False
    for row in cursor:
        scanned += 1
        if scanned > MAX_SCAN_ROWS:
            truncated = True
            break
        content_bytes = len(row[2]) if isinstance(row[2], bytes) else len(str(row[2] or '').encode('utf-8', errors='replace'))
        if scanned_bytes + content_bytes > MAX_SCAN_BYTES:
            truncated = True
            break
        scanned_bytes += content_bytes
        username = str(row[1] or '')
        if accepted_users and username not in accepted_users:
            continue
        name = nickname_for(username) if nickname_for else username
        post = parse_post(row[0], username, row[2], row[3], name)
        if post is None:
            continue
        stamp = post['createTime']
        if (start_time and stamp < start_time) or (end_time and stamp > end_time):
            continue
        haystack = (post['contentDesc'] + ' ' + ' '.join(
            str(item.get('content') or '') for item in post['comments'])).casefold()
        if term and term not in haystack:
            continue
        if offset <= total < offset + limit:
            page_posts.append(post)
        total += 1
    return {'available': True, 'items': page_posts, 'total': total,
            'has_more': offset + limit < total, 'truncated': truncated,
            'scan_limit': MAX_SCAN_ROWS, 'scan_byte_limit': MAX_SCAN_BYTES}
