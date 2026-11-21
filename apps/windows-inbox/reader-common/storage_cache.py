from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time


LARGE_ATTACHMENT = 8 * 1024 * 1024


def cache_on_demand(directory):
    try:
        return json.loads((Path(directory) / 'storage-preferences.json').read_text('utf-8')).get('attachmentMode') != 'offline'
    except (OSError, ValueError, AttributeError):
        return True


def attachment_reference(source, root, platform, stat):
    try:
        root = Path(root).resolve(strict=True)
        source = Path(source).resolve(strict=True)
        relative = source.relative_to(root).as_posix()
        if not relative or any(ord(char) < 32 or char == ':' for char in relative):
            return None
        identity = hashlib.sha256(os.path.normcase(str(root)).encode('utf-8')).hexdigest()
        return {'platform': platform, 'relative': relative, 'mtime_ms': stat.st_mtime_ns // 1000000, 'root_id': identity}
    except (ValueError, OSError):
        return None


def write_manifest(filename, value):
    filename.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=filename.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=False)
        os.replace(temporary, filename)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def acquire_lease(handle):
    import msvcrt
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)


def cleanup_snapshots(private):
    for entry in private.glob('snapshot-*'):
        try:
            if not re.fullmatch(r'snapshot-[a-z0-9_]{8}', entry.name) or entry.is_symlink() or entry.is_junction() or not entry.is_dir() or entry.resolve().parent != private.resolve():
                continue
            marker = entry / '.inbox-lease'
            if marker.is_symlink() or not marker.is_file() or time.time() - marker.stat().st_mtime < 3600:
                continue
            if any(child.is_symlink() or child.is_junction() for child in entry.rglob('*')):
                continue
            with marker.open('r+b') as handle:
                acquire_lease(handle)
            shutil.rmtree(entry)
        except OSError:
            continue


@contextmanager
def managed_snapshot(private):
    private = Path(private)
    if os.name == 'nt':
        cleanup_snapshots(private)
    with tempfile.TemporaryDirectory(prefix='snapshot-', dir=private) as folder:
        with (Path(folder) / '.inbox-lease').open('w+b') as handle:
            handle.write(b'1')
            handle.flush()
            if os.name == 'nt':
                acquire_lease(handle)
            yield folder
