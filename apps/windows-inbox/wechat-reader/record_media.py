"""Associate forwarded media with local copies; never use transport URLs."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

from file_cache import file_identity
from media_sync import image_extension, save_image
from video_cache import video_file

LIMIT = 10 * 1024 * 1024


def digest(item, name):
    value = item.findtext(name, '').strip().lower()
    return value if re.fullmatch('[a-f0-9]{32}', value) and value != '0' * 32 else ''


def number(item, name):
    value = item.findtext(name, '').strip()
    return int(value) if re.fullmatch('[0-9]{1,20}', value) and int(value) else None


def record_cache_files(account):
    root = Path(account).resolve()
    for pattern in ('cache/*/Message/*/RecTmp/*/*/*', 'cache/*/Message/*/Thumb/*',
                    'cache/*/Message/*/Bubble/*', 'cache/*/Message/*/ImageTemp/*'):
        for path in root.glob(pattern):
            if not path.is_symlink() and path.is_file() and path.resolve().is_relative_to(root / 'cache'):
                yield path


class RecordMedia:
    def __init__(self, reader, private, originals, files):
        self.reader, self.private, self.files = reader, private, files
        self.output = private.parent / 'attachments'
        self.originals = originals
        self.candidates = defaultdict(list)
        self.known = defaultdict(list)
        self.checked = {}
        self.reader._record_digests = getattr(self.reader, '_record_digests', {})
        scopes = [*record_cache_files(reader.account_dir)]
        scopes.extend(path for path in self.output.glob('*/content/*') if path.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp', '.gif'})
        for path in scopes:
            try:
                local = path.is_relative_to(self.output)
                boundary = self.output.resolve() if local else Path(reader.account_dir).resolve() / 'cache'
                if path.is_symlink() or not path.resolve().is_relative_to(boundary) or not path.is_file():
                    continue
                stamp = file_identity(path)
                if 0 < stamp[0] <= LIMIT + 128:
                    self.candidates[stamp[0]].append((path, stamp))
                    if not local and 'RecTmp' in path.parts:
                        self.known['record'].append((path, stamp))
            except (OSError, ValueError):
                continue

    def read_image(self, path, stamp):
        try:
            with path.open('rb') as handle:
                data = handle.read(LIMIT + 129)
            if not image_extension(data) and not path.is_relative_to(self.output):
                data = self.reader._media.decrypt_image(str(path))
            if (0 < len(data) <= LIMIT and image_extension(data) and file_identity(path) == stamp
                    and self.reader._media._image_complete(data)):
                return data
        except (OSError, ValueError, RuntimeError, ImportError):
            pass
        return None

    def by_hash(self, hashed, size, quality):
        identity = 'wechat-record-image\0' + self.reader.account_dir + '\0' + hashed
        mid = hashlib.sha256(identity.encode()).hexdigest()
        if self.cached(mid):
            return {'id': mid, 'status': 'ready', 'quality': quality}
        if (hashed, size) not in self.checked:
            found = None
            if size and size <= LIMIT:
                candidates = [(path, stamp) for length in range(size, size + 129)
                              for path, stamp in self.candidates.get(length, ())
                              if length == size or not path.is_relative_to(self.output)]
            else:
                candidates = []
            # Record temporary files may include container metadata of a different size.
            candidates = dict.fromkeys((*candidates, *self.known['record']))
            for path, stamp in candidates:
                key = (path, stamp)
                saved = self.reader._record_digests.get(key)
                if saved and saved != hashed:
                    continue
                data = self.read_image(path, stamp)
                actual = hashlib.md5(data).hexdigest() if data else ''
                if actual:
                    if len(self.reader._record_digests) > 12000:
                        self.reader._record_digests.clear()
                    self.reader._record_digests[key] = actual
                if actual != hashed:
                    continue
                if path.is_relative_to(self.output) and self.cached(path.parent.parent.name) == path:
                    found = path.parent.parent.name
                else:
                    try:
                        folder = self.output / mid
                        if (not folder.is_symlink() and folder.resolve().parent == self.output.resolve()
                                and not (folder / 'content').is_symlink() and not (folder / 'manifest.json').is_symlink()
                                and save_image(self.private, identity, data, 'Image')):
                            found = mid
                    except OSError:
                        continue
                if found:
                    break
            self.checked[(hashed, size)] = found
        hit = self.checked[(hashed, size)]
        return {'id': hit, 'status': 'ready', 'quality': quality} if hit else None

    def cached(self, identifier):
        try:
            if not re.fullmatch('[a-f0-9]{64}', identifier):
                return None
            folder = self.output / identifier
            manifest = folder / 'manifest.json'
            if manifest.is_symlink() or folder.resolve().parent != self.output.resolve():
                return None
            data = json.loads(manifest.read_text('utf-8'))
            name = data['name']
            if data['id'] != identifier or Path(name).name != name or ':' in name:
                return None
            path = folder / 'content' / name
            if path.is_symlink() or path.resolve().parent != folder.resolve() / 'content' or path.stat().st_size != data['size']:
                return None
            with path.open('rb') as stream:
                return path if image_extension(stream.read(24)) else None
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def from_original(self, item):
        stamp = number(item, 'srcMsgCreateTime')
        if stamp and stamp > 20000000000:
            stamp //= 1000
        target = number(item, 'fromnewmsgid')
        rows = self.originals.get((target, stamp), ()) if stamp and target else ()
        if len(rows) == 1:
            identifier = rows[0]
            if self.cached(identifier):
                return {'id': identifier, 'status': 'ready', 'quality': 'preview'}
        return None

    def resolve(self, parent, created, code, item, path, title):
        if code == '2':
            for key, size, quality in [('fullmd5', 'datasize', 'original'), ('thumbfullmd5', 'thumbsize', 'preview')]:
                checksum = digest(item, key)
                if checksum:
                    image = self.by_hash(checksum, number(item, size), quality)
                    if image:
                        return {'image': image}
            return {'image': self.from_original(item) or {'status': 'pending'}}
        checksum, size = digest(item, 'fullmd5'), number(item, 'datasize')
        identifier = hashlib.sha256((parent + '\0record\0' + '.'.join(map(str, path))).encode()).hexdigest()
        if code == '8' and title and checksum and size:
            return {'file': self.files.resolve(identifier, {'name': title, 'size': size, 'md5': checksum}, created)}
        if code == '4':
            video = None
            if checksum and size:
                for source in Path(self.reader.account_dir).glob('msg/video/*/*.mp4'):
                    try:
                        stamp = file_identity(source)
                        if stamp[0] == size and self.files.matches(source, checksum, stamp):
                            video = video_file(self.output, identifier, source, self.reader.account_dir, 'wechat', size, self.files.offline)
                            if video:
                                break
                    except OSError:
                        continue
            return {'video': {**(video or {'status': 'pending'}), 'name': title or '视频消息', 'size': size,
                              'duration': number(item, 'duration')}}
        return None
