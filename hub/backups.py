"""Portable backups for the index and explicitly Hub-managed media."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
import zipfile


MAX_BACKUP_BYTES = 8 * 1024 * 1024 * 1024
MAX_MEDIA_FILES = 100_000
MAX_ARCHIVE_ENTRIES = MAX_MEDIA_FILES + 2
CHUNK_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024 * 1024


def _read_manifest(archive, member):
    if member.file_size > MAX_MANIFEST_BYTES:
        raise ValueError('媒体备份清单超过读取上限')
    payload = bytearray()
    with archive.open(member) as source:
        while True:
            chunk = source.read(min(CHUNK_BYTES, MAX_MANIFEST_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > MAX_MANIFEST_BYTES:
                raise ValueError('媒体备份清单超过读取上限')
    return payload


def _media_relative_path(value):
    if not isinstance(value, str):
        return None
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in ('', '.', '..') or ':' in part or '\\' in part
                                     for part in relative.parts):
        return None
    if (len(relative.parts) >= 3 and relative.parts[0] == 'qq-roaming' and
            re.fullmatch(r'[A-Fa-f0-9]{20}', relative.parts[1])):
        return relative.as_posix()
    if (len(relative.parts) == 3 and relative.parts[0] == 'wechat-media' and
            re.fullmatch(r'[a-f0-9]{64}', relative.parts[1]) and
            relative.parts[2] not in ('', '.', '..')):
        return relative.as_posix()
    return None


def _referenced_media(database):
    uri = Path(database).resolve().as_uri() + '?mode=ro'
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        rows = connection.execute(
            "SELECT extra FROM messages WHERE platform='qq' "
            "AND (extra LIKE '%roaming_resources%' OR extra LIKE '%\"media\"%')"
        )
        found = set()
        for (encoded,) in rows:
            try:
                extra = json.loads(encoded or '{}')
            except (TypeError, ValueError):
                continue
            resources = extra.get('roaming_resources') if isinstance(extra, dict) else None
            if not isinstance(resources, list):
                resources = extra.get('media') if isinstance(extra, dict) else None
            if not isinstance(resources, list):
                continue
            for resource in resources:
                if not isinstance(resource, dict):
                    continue
                path = _media_relative_path(resource.get('path'))
                if path:
                    found.add(path)
                    if len(found) > MAX_MEDIA_FILES:
                        raise ValueError('备份媒体文件数量超过限制')
        return sorted(found)
    finally:
        connection.close()


def _referenced_wechat_cache_dirs(database):
    from integrations.wechat.provider import media_cache_identity

    uri = Path(database).resolve().as_uri() + '?mode=ro'
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        rows = connection.execute(
            "SELECT account_id, conversation_id, id, raw_type, type, extra "
            "FROM messages WHERE platform='wechat'"
        )
        found = set()
        media_types = {3, 34, 43, 49, 34359738417, 103079215153, 25769803825}
        for account, conversation, message_id, raw_type, kind, encoded in rows:
            if kind not in ('image', 'voice', 'video', 'file'):
                continue
            try:
                extra = json.loads(encoded or '{}')
                local_type = int(extra.get('local_type') or raw_type or 0)
                session = extra.get('session_id')
                shard = extra.get('shard')
                local_id = extra.get('local_id')
                if (not isinstance(extra, dict) or local_type not in media_types or
                        local_type == 47 or not isinstance(session, str) or
                        session != conversation or not isinstance(shard, str) or
                        not shard or PurePosixPath(shard).name != shard or
                        '\\' in shard or ':' in shard or type(local_id) is not int or
                        local_id < 0 or message_id != f'{session}:{shard}:{local_id}'):
                    continue
                identity = media_cache_identity(account, session, shard, local_id)
            except (TypeError, ValueError, AttributeError):
                continue
            found.add(f'wechat-media/{identity}')
            if len(found) > MAX_MEDIA_FILES:
                raise ValueError('备份媒体文件数量超过限制')
        return sorted(found)
    finally:
        connection.close()


def _source_media_path(storage, relative):
    storage = Path(storage).resolve()
    root = storage / 'qq-roaming'
    if root.is_symlink():
        return None
    candidate = storage.joinpath(*PurePosixPath(relative).parts)
    current = storage
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            return None
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _source_wechat_cache_files(cache_root, relative_dir):
    root = Path(cache_root)
    if root.is_symlink() or not root.exists():
        return []
    directory = root / PurePosixPath(relative_dir).parts[1]
    if directory.is_symlink():
        return []
    try:
        directory.resolve(strict=True).relative_to(root.resolve(strict=True))
        return sorted((path for path in directory.iterdir()
                       if not path.is_symlink() and path.is_file() and
                       _media_relative_path(f'{relative_dir}/{path.name}')), key=lambda p: p.name)
    except (OSError, ValueError):
        return []


def _digest_file(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open('rb') as stream:
        while True:
            chunk = stream.read(CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def create_media_backup(store, storage, destination, wechat_cache_root=None):
    """Create a self-contained ZIP containing the index and referenced Hub media."""
    storage = Path(storage).resolve()
    target = Path(destination).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.media-backup-', dir=target.parent))
    database = stage / 'index.sqlite3'
    temporary = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.part')
    try:
        store.backup_to(database)
        refs = _referenced_media(database)
        wechat_dirs = _referenced_wechat_cache_dirs(database) if wechat_cache_root else []
        files = []
        total = 0
        for relative in refs:
            source = _source_media_path(storage, relative)
            if source is None:
                continue
            size = source.stat().st_size
            total += size
            if total > MAX_BACKUP_BYTES:
                raise ValueError('漫游媒体总量超过 8 GiB 备份限制')
            files.append((relative, source, size))
        included_wechat_dirs = set()
        for relative_dir in wechat_dirs:
            cached = _source_wechat_cache_files(wechat_cache_root, relative_dir)
            if not cached:
                continue
            included_wechat_dirs.add(relative_dir)
            for source in cached:
                relative = f'{relative_dir}/{source.name}'
                size = source.stat().st_size
                total += size
                if total > MAX_BACKUP_BYTES:
                    raise ValueError('媒体总量超过 8 GiB 备份限制')
                files.append((relative, source, size))
        if database.stat().st_size + total > MAX_BACKUP_BYTES:
            raise ValueError('索引与漫游媒体总量超过 8 GiB 备份限制')

        manifest = {'format': 'local-message-hub-media-backup', 'version': 1,
                    'created_at': int(time.time()), 'media_count': len(files),
                    'missing_media_count': (len(refs) - sum(1 for path, _, _ in files
                                              if path.startswith('qq-roaming/')) +
                                            len(wechat_dirs) - len(included_wechat_dirs)),
                    'media_bytes': total,
                    'media': []}
        with zipfile.ZipFile(temporary, 'x', allowZip64=True) as archive:
            archive.write(database, 'index.sqlite3', compress_type=zipfile.ZIP_DEFLATED)
            for relative, source, expected_size in files:
                digest = hashlib.sha256()
                copied = 0
                entry = 'media/' + relative
                info = zipfile.ZipInfo(entry)
                info.compress_type = zipfile.ZIP_STORED
                info.file_size = expected_size
                with source.open('rb') as reader, archive.open(info, 'w') as writer:
                    while True:
                        chunk = reader.read(CHUNK_BYTES)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > expected_size or total > MAX_BACKUP_BYTES:
                            raise ValueError('媒体文件在备份过程中发生变化')
                        writer.write(chunk)
                        digest.update(chunk)
                if copied != expected_size or source.stat().st_size != expected_size:
                    raise ValueError('媒体文件在备份过程中发生变化')
                manifest['media'].append({'path': entry, 'size': copied,
                                          'sha256': digest.hexdigest()})
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False,
                                                         separators=(',', ':')))
        if temporary.stat().st_size > MAX_BACKUP_BYTES:
            raise ValueError('备份文件超过 8 GiB 限制')
        with temporary.open('r+b') as stream:
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temporary, target)
        stat = target.stat()
        return {'id': target.name, 'size': stat.st_size, 'created_at': int(stat.st_mtime),
                'kind': 'media', 'media_count': len(files),
                'missing_media_count': manifest['missing_media_count']}
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _validate_archive(archive):
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise ValueError('备份条目数量超过限制')
    by_name = {}
    total = 0
    for info in infos:
        name = info.filename
        if name in by_name or info.is_dir():
            raise ValueError('备份包含重复或不支持的路径')
        mode = (info.external_attr >> 16) & 0xFFFF
        kind = mode & 0o170000
        if kind and kind != 0o100000:
            raise ValueError('备份只能包含普通文件')
        if info.file_size < 0 or info.file_size > MAX_BACKUP_BYTES:
            raise ValueError('备份条目超过大小限制')
        if ((info.file_size and not info.compress_size) or
                (info.compress_size and info.file_size > info.compress_size * 1000)):
            raise ValueError('备份压缩比例异常')
        total += info.file_size
        if total > MAX_BACKUP_BYTES:
            raise ValueError('备份解压总量超过 8 GiB 限制')
        by_name[name] = info
    if 'index.sqlite3' not in by_name or 'manifest.json' not in by_name:
        raise ValueError('媒体备份缺少索引或清单')
    if by_name['index.sqlite3'].file_size == 0:
        raise ValueError('媒体备份索引为空')
    return by_name


def restore_media_backup(store, storage, archive_path, wechat_cache_root=None):
    """Validate a media bundle fully, then merge missing managed files and restore its index."""
    storage = Path(storage).resolve()
    storage.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.restore-media-', dir=storage))
    added = []
    created_dirs = []
    try:
        with zipfile.ZipFile(archive_path, 'r') as archive:
            members = _validate_archive(archive)
            try:
                manifest = json.loads(_read_manifest(archive, members['manifest.json']))
            except ValueError as exc:
                if str(exc) == '媒体备份清单超过读取上限':
                    raise
                raise ValueError('媒体备份清单无效') from exc
            except OSError as exc:
                raise ValueError('媒体备份清单无效') from exc
            if (not isinstance(manifest, dict) or
                    manifest.get('format') != 'local-message-hub-media-backup' or
                    type(manifest.get('version')) is not int or manifest['version'] != 1 or
                    not isinstance(manifest.get('media'), list)):
                raise ValueError('媒体备份格式不受支持')
            if (type(manifest.get('media_count')) is not int or
                    manifest['media_count'] != len(manifest['media']) or
                    len(manifest['media']) > MAX_MEDIA_FILES):
                raise ValueError('媒体备份清单数量无效')
            declared = {}
            for item in manifest['media']:
                if not isinstance(item, dict):
                    raise ValueError('媒体备份清单无效')
                path = item.get('path')
                if (not isinstance(path, str) or not path.startswith('media/') or
                        path in declared or type(item.get('size')) is not int or
                        item['size'] < 0 or not re.fullmatch(r'[a-f0-9]{64}', str(item.get('sha256') or ''))):
                    raise ValueError('媒体备份清单无效')
                relative = _media_relative_path(path[len('media/'):])
                if relative is None:
                    raise ValueError('媒体备份路径无效')
                info = members.get(path)
                if info is None or info.file_size != item['size']:
                    raise ValueError('媒体备份清单与文件不匹配')
                declared[path] = (relative, item)
            allowed = {'index.sqlite3', 'manifest.json', *declared.keys()}
            if set(members) != allowed:
                raise ValueError('媒体备份包含清单外文件')
            if (type(manifest.get('media_bytes')) is not int or
                    manifest['media_bytes'] != sum(item['size'] for _, item in declared.values()) or
                    type(manifest.get('missing_media_count')) is not int or
                    manifest['missing_media_count'] < 0):
                raise ValueError('媒体备份清单统计无效')

            staged_db = stage / 'index.sqlite3'
            with archive.open(members['index.sqlite3']) as reader, staged_db.open('xb') as writer:
                shutil.copyfileobj(reader, writer, CHUNK_BYTES)
            referenced = set(_referenced_media(staged_db))
            referenced_wechat = set(_referenced_wechat_cache_dirs(staged_db)) if wechat_cache_root else set()
            declared_relative = {relative for relative, _ in declared.values()}
            declared_qq = {path for path in declared_relative if path.startswith('qq-roaming/')}
            declared_wechat = {path.rsplit('/', 1)[0] for path in declared_relative
                                if path.startswith('wechat-media/')}
            if (not declared_qq.issubset(referenced) or
                    not declared_wechat.issubset(referenced_wechat) or
                    (len(referenced) - len(declared_qq) +
                     len(referenced_wechat) - len(declared_wechat)) != manifest['missing_media_count']):
                raise ValueError('媒体备份清单与索引引用不匹配')
            staged_media = []
            for path, (relative, item) in declared.items():
                destination = stage.joinpath(*PurePosixPath(relative).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                copied = 0
                with archive.open(members[path]) as reader, destination.open('xb') as writer:
                    while True:
                        chunk = reader.read(CHUNK_BYTES)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > item['size'] or copied > MAX_BACKUP_BYTES:
                            raise ValueError('媒体备份条目超过大小限制')
                        writer.write(chunk)
                        digest.update(chunk)
                if copied != item['size'] or digest.hexdigest() != item['sha256']:
                    raise ValueError('媒体文件摘要校验失败')
                if relative.startswith('wechat-media/'):
                    if wechat_cache_root is None:
                        raise ValueError('恢复 WeChat 媒体需要本地缓存目录')
                    final = Path(wechat_cache_root).joinpath(*PurePosixPath(relative).parts[1:])
                else:
                    final = storage.joinpath(*PurePosixPath(relative).parts)
                if final.exists() or final.is_symlink():
                    existing = (_source_media_path(storage, relative) if relative.startswith('qq-roaming/')
                                else next((p for p in _source_wechat_cache_files(
                                    wechat_cache_root, relative.rsplit('/', 1)[0])
                                    if p.name == PurePosixPath(relative).name), None))
                    if (existing is None or existing.stat().st_size != copied or
                            _digest_file(existing) != (copied, item['sha256'])):
                        raise ValueError('目标目录已有内容不同的媒体文件，恢复发生冲突')
                else:
                    staged_media.append((destination, final))

        for source, destination in staged_media:
            cache_root = Path(wechat_cache_root) if wechat_cache_root is not None else None
            target_root = cache_root if cache_root is not None and destination.is_relative_to(cache_root) else storage
            if target_root == cache_root and not target_root.exists():
                target_root.mkdir(parents=True)
                created_dirs.append(target_root)
            if target_root.is_symlink():
                raise ValueError('目标媒体目录包含符号链接')
            missing = []
            parent = destination.parent
            while not parent.exists() and parent != target_root:
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                created_dirs.append(directory)
            if any(part.is_symlink() for part in (destination.parent, *destination.parents)
                   if part == target_root or target_root in part.parents):
                raise ValueError('目标媒体目录包含符号链接')
            os.replace(source, destination)
            added.append(destination)

        store.restore_from(staged_db)
        return {'media_count': len(declared), 'restored_media_count': len(added)}
    except zipfile.BadZipFile as exc:
        raise ValueError('媒体备份 ZIP 无效') from exc
    except Exception:
        for path in reversed(added):
            path.unlink(missing_ok=True)
        for path in reversed(created_dirs):
            try:
                path.rmdir()
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
