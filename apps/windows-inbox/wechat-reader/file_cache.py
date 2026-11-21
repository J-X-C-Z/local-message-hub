"""Match WeChat file messages to already downloaded files in this account."""
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

from storage_cache import attachment_reference, write_manifest


def valid_name(name):
    return (isinstance(name, str) and 0 < len(name) <= 255 and not name.endswith(('.', ' '))
            and not re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            and not re.fullmatch(r'(?:CON|PRN|AUX|NUL|(?:COM|LPT)[0-9¹²³])\s*', name.split('.')[0], re.I))


def file_description(app):
    name = app.findtext('title', '').strip()
    if not valid_name(name):
        return None
    length = app.findtext('appattach/totallen', '').strip()
    digest = (app.findtext('md5') or app.findtext('appattach/filemd5', '')).strip().lower()
    return {'name': name, 'size': int(length) if re.fullmatch(r'[0-9]{1,14}', length) else None,
            'md5': digest if re.fullmatch(r'[a-f0-9]{32}', digest) else ''}


def file_identity(path):
    s = path.stat()
    return (s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino)


def cache_files(account):
    root = Path(account) / 'msg' / 'file'
    return root.glob('*/*')


class FileCache:
    def __init__(self, account, output, hashes, offline=False):
        self.account, self.output, self.hashes, self.offline = Path(account).resolve(), Path(output), hashes, offline
        self.by_size = defaultdict(list)
        for file in cache_files(account):
            try:
                if not file.is_symlink() and file.is_file() and file.resolve().is_relative_to(self.account / 'msg' / 'file'):
                    self.by_size[(file.suffix.casefold(), file.stat().st_size)].append(file)
            except OSError:
                continue

    def matches(self, path, digest, stamp):
        if not digest:
            return True
        key = (path, stamp)
        if key not in self.hashes:
            try:
                with path.open('rb') as f:
                    value = hashlib.file_digest(f, 'md5').hexdigest()
                if file_identity(path) != stamp:
                    return False
                if len(self.hashes) >= 4096:
                    self.hashes.clear()
                self.hashes[key] = value
            except OSError:
                return False
        return self.hashes[key] == digest

    def resolve(self, identifier, description, created):
        name, size, digest = (description[k] for k in ('name', 'size', 'md5'))
        attachment = {'id': identifier, 'name': name, 'size': size, 'type': Path(name).suffix[1:].upper() or '文件',
                      'source': 'wechat-file', 'status': 'pending'}
        folder = self.output / identifier
        manifest = folder / 'manifest.json'
        try:
            if folder.is_symlink() or not folder.resolve().is_relative_to(self.output.resolve()) or manifest.is_symlink():
                return {**attachment, 'status': 'unreadable'}
            try:
                old = json.loads(manifest.read_text('utf-8'))
            except (OSError, ValueError):
                old = {}
            saved_name = old.get('name') or ''
            if old.get('version') == 1 and old.get('id') == identifier and valid_name(saved_name) and Path(saved_name).suffix.casefold() == Path(name).suffix.casefold():
                local = folder / 'content' / saved_name
                if not local.is_symlink() and local.is_file() and local.resolve().parent == folder.resolve() / 'content':
                    stamp = file_identity(local)
                    if stamp[0] == old.get('size') and (size is None or stamp[0] == size) and self.matches(local, digest, stamp):
                        if saved_name != name and old.get('display_name') != name:
                            write_manifest(manifest, {**old, 'display_name': name})
                        return {**attachment, 'size': stamp[0], 'status': 'ready'}
            if size is None or created <= 0:
                return attachment
            exact = self.account / 'msg' / 'file' / time.strftime('%Y-%m', time.localtime(created)) / name
            other = self.by_size.get((Path(name).suffix.casefold(), size), ()) if digest else ()
            for source in dict.fromkeys((exact, *other)):
                if source.is_symlink() or not source.is_file():
                    continue
                stamp = file_identity(source)
                reference = attachment_reference(source, self.account, 'wechat', source.stat())
                if not reference or not reference['relative'].startswith('msg/file/'):
                    continue
                if stamp[0] != size or not self.matches(source, digest, stamp) or file_identity(source) != stamp:
                    if source == exact:
                        attachment['status'] = 'incomplete'
                    continue
                record = {'version': 1, 'id': identifier, 'name': name, 'size': size, 'source': reference}
                if self.offline:
                    self.save_copy(source, folder, name, stamp)
                if record != old:
                    write_manifest(manifest, record)
                return {**attachment, 'status': 'ready' if self.offline else 'on_demand'}
            return attachment
        except (OSError, ValueError, TypeError, AttributeError):
            return {**attachment, 'status': 'unreadable'}

    @staticmethod
    def save_copy(source, folder, name, stamp):
        target = folder / 'content'
        target.mkdir(parents=True, exist_ok=True)
        if target.resolve() != folder.resolve() / 'content':
            raise OSError('Unexpected attachment destination')
        temporary = None
        try:
            with source.open('rb') as f, tempfile.NamedTemporaryFile(dir=target, delete=False) as copy:
                temporary = Path(copy.name)
                shutil.copyfileobj(f, copy, 1024 * 1024)
            if file_identity(source) != stamp or temporary.stat().st_size != stamp[0]:
                raise OSError('Attachment changed during copy')
            os.replace(temporary, target / name)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
