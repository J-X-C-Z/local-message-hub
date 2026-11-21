"""Synchronize documents using exact native resource-key -> cache mappings."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from local_io import atomic_output, connect_readonly
from protobuf_fields import fields, single_text
from wal_snapshot import read_snapshot
from storage_cache import LARGE_ATTACHMENT, attachment_reference, cache_on_demand, write_manifest


def safe_name(value):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')[:180]
    if not name or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *[f'COM{i}' for i in range(1, 10)], *[f'LPT{i}' for i in range(1, 10)]}:
        name = '附件' + Path(name).suffix
    return name


class AttachmentSync:
    def __init__(self, profile, private, shared_reader, allowed_roots=None):
        self.profile, self.private = Path(profile), Path(private)
        self.output = self.private.parent / 'attachments'
        self.on_demand = cache_on_demand(self.private.parent)
        self.allowed_roots = [Path(p).resolve() for p in (allowed_roots or [Path.home()])]
        self.mapping = {}
        self.mapping_ready = False
        folder = self.profile.parent / 'CacheMapping'
        for source in folder.glob('*.db'):
            try:
                with tempfile.TemporaryDirectory(prefix='mapping-', dir=private) as temporary:
                    target = Path(temporary) / 'mapping.db'
                    target.write_bytes(read_snapshot(source, reader=shared_reader))
                    conn = connect_readonly(target)
                    try:
                        for key, filename in conn.execute('SELECT key,file_name FROM mapping WHERE type=1'):
                            if isinstance(key, str) and isinstance(filename, str):
                                self.mapping.setdefault(key, set()).add(filename)
                        self.mapping_ready = True
                    finally:
                        conn.close()
            except Exception:
                # A changing or unknown cache-map format must not interrupt chat.
                continue

    def attach(self, row, *, preview=False):
        if int(row['type_code']) == 20:
            # Another native file envelope: token/name/size are fields 1/2/4.
            return self.attach({**row, 'type_code': 15}, preview=preview)
        if int(row['type_code']) != 15:
            return None
        try:
            payload = fields(row['_raw'])
            key, filename = single_text(payload, 1), single_text(payload, 2)
            declared = payload.get(4, [None])[0]
            declared = declared if type(declared) is int and declared >= 0 else None
        except (ValueError, TypeError):
            return {'name': '附件', 'size': None, 'status': 'unsupported', 'type': '文件'}
        identifier = hashlib.sha256((str(self.profile.resolve()) + '\0' + key).encode()).hexdigest()
        filename = safe_name(filename or '附件')
        item = {'id': identifier, 'name': filename, 'size': declared,
                'type': Path(filename).suffix.lstrip('.').upper() or '文件', 'status': 'pending'}
        if not key:
            item['status'] = 'unsupported'
            return item
        directory = self.output / identifier
        destination, manifest = directory / 'content' / filename, directory / 'manifest.json'
        def saved_copy():
            try:
                old = json.loads(manifest.read_text('utf-8'))
                if old.get('id') != identifier or old.get('name') != filename or not destination.resolve().is_relative_to(self.output.resolve()) or destination.is_symlink():
                    return None
                size = destination.stat().st_size
                if size != old.get('size') or (declared is not None and size != declared):
                    return None
                return {**item, 'size': size, 'status': 'ready'}
            except (OSError, ValueError, TypeError):
                return None
        candidates = set()
        for entry in self.mapping.get(key, ()):
            source = Path(entry)
            if not source.is_absolute():
                source = self.profile.parent / 'Cache' / 'File' / source
            try:
                resolved = source.resolve()
                if source.is_symlink() or not resolved.is_file() or not any(resolved.is_relative_to(root) for root in self.allowed_roots):
                    continue
                candidates.add(resolved)
            except OSError:
                continue
        if len(candidates) > 1:
            item['status'] = 'ambiguous'
            return item
        if not candidates:
            return saved_copy() or item
        source = candidates.pop()
        try:
            before = source.stat()
            if declared is not None and before.st_size != declared:
                item['status'] = 'incomplete'
                return saved_copy() or item
            if directory.is_symlink() or not directory.resolve().is_relative_to(self.output.resolve()):
                raise ValueError('unsafe attachment destination')
            signature = hashlib.sha256(f'{source}\0{before.st_size}\0{before.st_mtime_ns}'.encode()).hexdigest()
            old = json.loads(manifest.read_text('utf-8')) if manifest.is_file() else {}
            if old.get('source_signature') == signature and destination.is_file() and not destination.is_symlink() and destination.stat().st_size == before.st_size:
                return {**item, 'size': before.st_size, 'status': 'ready'}
            if self.on_demand and not preview and before.st_size >= LARGE_ATTACHMENT:
                saved = saved_copy()
                if saved:
                    return saved
                reference = attachment_reference(source, self.profile.parent / 'Cache' / 'File', 'wecom', before)
                if reference and source.suffix.lower() == Path(filename).suffix.lower():
                    data = {'version': 1, 'id': identifier, 'name': filename, 'size': before.st_size, 'source': reference}
                    if old != data:
                        write_manifest(manifest, data)
                    return {**item, 'size': before.st_size, 'status': 'on_demand'}
            digest, copied = hashlib.sha256(), 0
            with source.open('rb') as stream, atomic_output(destination, binary=True) as output:
                opened = os.fstat(stream.fileno())
                while chunk := stream.read(1024 * 1024):
                    output.write(chunk); digest.update(chunk); copied += len(chunk)
                after = os.fstat(stream.fileno())
                latest = source.stat()
                if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or (before.st_size, before.st_mtime_ns) != (latest.st_size, latest.st_mtime_ns) or copied != before.st_size:
                    raise OSError('attachment changed during copy')
            with atomic_output(manifest) as output:
                json.dump({'version': 1, 'id': identifier, 'name': filename, 'size': copied,
                           'sha256': digest.hexdigest(), 'source_signature': signature,
                           'synced_at': int(time.time() * 1000)}, output, ensure_ascii=False)
            return {**item, 'size': copied, 'status': 'ready'}
        except (OSError, ValueError, TypeError):
            item['status'] = 'unreadable'
            return saved_copy() or item
