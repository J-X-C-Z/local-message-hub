"""Presentation rules verified against this client's native group notices."""
import re


def render_message(row, users, members, image_sync=None, video_sync=None):
    content = row['content']
    code = int(row['type_code'])
    if code in (13, 38, 570, 573, 123):
        from rich_messages import chat_record, document_card, web_card
        if code == 123:
            record = chat_record(row, image_sync)
            return {'Content': record['title'], 'System': False, 'ChatRecord': record}
        document = document_card(row) if code == 13 else None
        if document:
            return {'Content': document['title'], 'System': False, 'Document': document}
        card = web_card(row)
        if card:
            return {'Content': card['title'], 'System': False, 'Card': card}
        return {'Content': '[卡片 · 内容暂不可用]', 'System': False}
    if code == 1011:
        raw = row.get('_raw')
        if isinstance(raw, bytes):
            try:
                raw = raw.decode('utf-8')
            except UnicodeDecodeError:
                raw = None
        if isinstance(raw, str):
            text = raw.strip()
            if text and not any(ord(c) < 32 and c not in '\r\n\t' for c in text):
                return {'Content': text, 'System': True}
        return {'Content': '协作通知（内容暂无法解析）', 'System': True}
    if code in (20, 22, 23):
        from protobuf_fields import fields, single_text
        try:
            payload = fields(row['_raw'])
            if int(row['type_code']) == 20:
                from attachments import safe_name
                name = safe_name(single_text(payload, 2) or '文件')
                return {'Content': '[文件] ' + name, 'System': False}
        except (ValueError, TypeError):
            payload = {}
        if code in (22, 23):
            size = payload.get(3, [None])[0]
            duration = payload.get(4, [None])[0]
            size = size if type(size) is int and size > 0 else None
            source = video_sync(payload, size) if video_sync else None
            return {'Content': '[视频]', 'System': False, 'Video': {
                'status': 'pending', 'name': '视频消息',
                'size': size, 'duration': duration if type(duration) is int and 0 < duration <= 86400 else None,
                **(source or {})}}
        return {'Content': '[文件] 暂未识别文件名', 'System': False}
    if int(row['type_code']) != 1002:
        return {'Content': content, 'System': False}
    raw = row.get('_raw')
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', 'replace')
    if not isinstance(raw, str) or not re.fullmatch(r'\s*\d{12,}(?:[\s,;]+\d{12,})*\s*', raw):
        return {'Content': content if content and not content.startswith('[类型') else '群成员变动通知', 'System': True}
    ids = re.findall(r'\d{12,}', raw)
    names, records = [], []
    for uid in ids:
        member = members.get((row['conv_id'], uid), {})
        records.append(member)
        name = member.get('name') or users.get(uid)
        names.append(str(name) if name else '一位成员（姓名未缓存）')
    stamp = float(row['send_time'] or 0)
    if stamp > 20000000000:
        stamp /= 1000
    def matching_join(member):
        joined = float(member.get('join_time') or 0)
        if joined > 20000000000:
            joined /= 1000
        return joined > 0 and abs(joined - stamp) < 60
    targets = '、'.join(names)
    if all(matching_join(member) for member in records):
        actor = users.get(str(row['sender_id']))
        invited = actor and all(str(member.get('inviter')) == str(row['sender_id']) for member in records)
        text = f'{actor} 邀请 {targets} 加入群聊' if invited else f'{targets} 加入群聊'
    else:
        # Member rows may have expired or represent a later re-join. Do not
        # invent an inviter or action when the cached record cannot confirm it.
        text = f'群成员变动：{targets}'
    return {'Content': text, 'System': True}
