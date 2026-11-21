import hashlib
import re
from pathlib import Path
from xml.etree import ElementTree

from media_sync import image_extension, save_image


def parse_moment(tid, username, source, nickname, avatars):
    if not username or not isinstance(source, (str, bytes)) or len(source) > 2 * 1024 * 1024:
        return None
    if isinstance(source, bytes):
        source = source.decode('utf-8', 'replace')
    if re.search(r'<!\s*(?:doctype|entity)', source, re.I):
        return None
    try:
        root = ElementTree.fromstring(source)
        timeline = root if root.tag == 'TimelineObject' else root.find('TimelineObject')
        if timeline is None or timeline.findtext('username', username) != username:
            return None
        stamp = int(timeline.findtext('createTime', '0'))
        if not 0 < stamp < 4102444800:
            return None
    except (ValueError, ElementTree.ParseError):
        return None
    extra = root.find('LocalExtraInfo')
    text = (timeline.findtext('contentDesc') or '').strip()[:8000]
    content = timeline.find('ContentObject')
    title = (content.findtext('title') or '').strip()[:400] if content is not None else ''
    nodes = content.findall('mediaList/media')[:12] if content is not None else []
    media = []
    for node in nodes:
        kind = {'2': 'image', '6': 'video'}.get(node.findtext('type'), 'other')
        url = node.find('url')
        digest = url.get('md5', '').lower() if url is not None else ''
        media.append({'kind': kind, 'digest': digest if kind == 'image' and re.fullmatch(r'[0-9a-f]{32}', digest) else ''})
    comments = []
    likes = []
    if extra is not None:
        for item in extra.findall('comment_user_list/user_comment')[:50]:
            if item.findtext('b_deleted', '0') != '0':
                continue
            author = item.findtext('nickname') or nickname(item.findtext('username', ''))
            body = (item.findtext('content') or '').strip()
            if author and body:
                comments.append({'author': author[:150], 'text': body[:1000]})
        for item in extra.findall('like_user_list/user_comment')[:50]:
            if item.findtext('b_deleted', '0') != '0':
                continue
            author = item.findtext('nickname') or nickname(item.findtext('username', ''))
            if author:
                likes.append(author[:150])
    if not (text or title or media):
        return None
    author = nickname(username)
    if author == username:
        author = (extra.findtext('nickname') if extra is not None else '') or '微信联系人'
    return {'id': str(tid), 'author': author[:150], 'avatar': avatars.get(username, ''),
            'time': stamp, 'text': text, 'title': title, 'media': media, 'comments': comments, 'likes': likes}


def local_image_index(reader, wanted):
    found = {}
    if not wanted:
        return found
    cache = Path(reader.account_dir) / 'cache'
    scanned = 0
    for folder in cache.glob('*/Sns'):
        if not folder.resolve().is_relative_to(cache.resolve()):
            continue
        for file in folder.rglob('*'):
            try:
                if not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to(folder.resolve()):
                    continue
                scanned += 1
                if scanned > 1200 or len(found) == len(wanted):
                    return found
                if file.stat().st_size > 10 * 1024 * 1024:
                    continue
                with file.open('rb') as stream:
                    prefix = stream.read(12)
                    if not image_extension(prefix):
                        continue
                    data = prefix + stream.read(10 * 1024 * 1024 - len(prefix) + 1)
                if len(data) > 10 * 1024 * 1024:
                    continue
                digest = hashlib.md5(data).hexdigest()
                if digest in wanted and digest not in found:
                    found[digest] = file
            except OSError:
                continue
    return found


def social_snapshot(reader, private):
    account = hashlib.sha256(reader.account_dir.encode()).hexdigest()
    avatars = {}
    posts = None
    sources = {Path(filename).name: rel for rel, filename, _ in reader._db_files}
    if 'head_image.db' in sources:
        try:
            connection = reader._open(sources['head_image.db'])
            try:
                for username, data in connection.execute('SELECT username,image_buffer FROM head_image WHERE length(image_buffer) BETWEEN 1 AND 1048576 ORDER BY update_time DESC LIMIT 10000'):
                    if not username or username in avatars or not isinstance(data, bytes) or not image_extension(data):
                        continue
                    identity = 'wechat-avatar:' + account + ':' + hashlib.sha256(data).hexdigest()
                    media_id = hashlib.sha256(identity.encode()).hexdigest()
                    if (private.parent / 'attachments' / media_id / 'manifest.json').is_file() or save_image(private, identity, data, 'Avatar'):
                        avatars[username] = media_id
            finally:
                connection.close()
        except Exception:
            pass
    if 'sns.db' in sources:
        try:
            connection = reader._open(sources['sns.db'])
            try:
                items = [parse_moment(tid, author, body, reader.get_nickname, avatars) for tid, author, body in connection.execute(
                    'SELECT tid,user_name,content FROM SnsTimeLine WHERE length(content)<=2097152 ORDER BY tid DESC LIMIT 500')]
            finally:
                connection.close()
            items = sorted((item for item in items if item), key=lambda item: (item['time'], item['id']), reverse=True)
            wanted = {media['digest'] for item in items for media in item['media'] if media['digest']}
            images = local_image_index(reader, wanted)
            for item in items:
                for media in item['media']:
                    digest = media.pop('digest')
                    file = images.get(digest)
                    if file:
                        try:
                            with file.open('rb') as stream:
                                data = stream.read(10 * 1024 * 1024 + 1)
                            if hashlib.md5(data).hexdigest() == digest:
                                image = save_image(private, 'wechat-moment:' + account + ':' + digest, data, 'Image').get('Image')
                                if image:
                                    media['id'] = image['id']
                        except OSError:
                            pass
            posts = items
        except Exception:
            pass
    return {'account': account, 'avatars': [['wechat:' + reader.account + ':' + user, media_id] for user, media_id in avatars.items()], 'posts': posts}
