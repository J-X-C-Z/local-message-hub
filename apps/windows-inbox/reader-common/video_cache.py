"""Reuse the exact client video, optionally retaining a byte-for-byte copy."""
import json
import os
from pathlib import Path
import shutil
import tempfile

from storage_cache import attachment_reference, write_manifest


def video_file(output, identifier, source, root, platform, size=None, offline=False):
    folder = output / identifier
    manifest = folder / 'manifest.json'
    try:
        if not folder.resolve().is_relative_to(output.resolve()) or folder.is_symlink() or manifest.is_symlink():
            return None
        try:
            old = json.loads(manifest.read_text('utf-8'))
        except (OSError, ValueError):
            old = {}

        def is_mp4(file, length):
            with file.open('rb') as stream:
                header = stream.read(16)
            return len(header) == 16 and header[4:8] == b'ftyp' and 16 <= int.from_bytes(header[:4], 'big') < length

        name = '视频.mp4'
        old_name = old.get('name') or ''
        if old.get('id') == identifier and isinstance(old_name, str) and Path(old_name).name == old_name and old_name.lower().endswith('.mp4'):
            target = folder / 'content' / old_name
            if target.is_file() and not target.is_symlink() and target.resolve().is_relative_to(folder.resolve()):
                length = target.stat().st_size
                if length == old.get('size') and (size is None or size == length) and is_mp4(target, length):
                    return {'id': identifier, 'name': name, 'size': length, 'status': 'ready'}
        if source is None or source.is_symlink() or source.suffix.lower() != '.mp4':
            return None
        before = source.stat()
        reference = attachment_reference(source, root, platform, before)
        if not reference or (size is not None and before.st_size != size) or not is_mp4(source, before.st_size):
            return None
        if platform == 'wecom':
            reference['cache'] = 'video'
        elif not reference['relative'].startswith('msg/video/'):
            return None
        current = source.stat()
        if (current.st_size, current.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            return None
        record = {'version': 1, 'id': identifier, 'name': name, 'size': before.st_size, 'source': reference}
        status = 'on_demand'
        if offline:
            content = folder / 'content'
            content.mkdir(parents=True, exist_ok=True)
            if content.resolve() != folder.resolve() / 'content':
                return None
            temporary = None
            try:
                with source.open('rb') as stream, tempfile.NamedTemporaryFile(dir=content, delete=False) as copy:
                    temporary = Path(copy.name)
                    shutil.copyfileobj(stream, copy, 1024 * 1024)
                current = source.stat()
                if (current.st_size, current.st_mtime_ns) != (before.st_size, before.st_mtime_ns) or current.st_size != temporary.stat().st_size:
                    return None
                os.replace(temporary, content / name)
                status = 'ready'
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        if old != record:
            write_manifest(manifest, record)
        return {'id': identifier, 'name': name, 'size': before.st_size, 'status': status}
    except (OSError, ValueError, TypeError, AttributeError):
        return None
