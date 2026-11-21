"""Local WeChat -> inbox transport. Never writes to the client databases."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
import sys
import tempfile
import time
from vendor.db import WeChatDB, auto_detect_db_dir, list_accounts
from vendor.media import MediaDownloader
from message_render import message_reference, render_message
from media_sync import sticker
from social_cache import social_snapshot
from storage_cache import LARGE_ATTACHMENT, attachment_reference, cache_on_demand, managed_snapshot, write_manifest
from video_cache import video_file
from file_cache import FileCache, cache_files, file_identity
from record_media import RecordMedia, record_cache_files


def protect(data, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = Blob(len(data), buf), Blob()
    api = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    api.restype = wintypes.BOOL
    if decrypt:
        ok = api(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        ok = api(ctypes.byref(source), 'WeChat inbox database keys', None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise RuntimeError('Windows 本机密钥加密服务不可用')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(target.data)


class Reader(WeChatDB):
    def _message_dbs(self):
        self._refresh_db_files()
        import re
        return sorted(rel for rel, source, _ in self._db_files
                      if re.fullmatch(r'(?:biz_)?message_\d+\.db', Path(source).name))

    def inbox_sessions(self):
        sessions = self.get_sessions(limit=100000)
        known = {hashlib.md5(session['username'].encode()).hexdigest() for session in sessions}
        index = self._build_md5_index()
        for rel in self._message_dbs():
            conn = self._open(rel)
            try:
                tables = {name[4:].lower() for name, in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'Msg_*'")
                    if re.fullmatch(r'Msg_[a-fA-F0-9]{32}', name)}
                missing = tables - known
                if not missing:
                    continue
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='Name2Id'").fetchone():
                    for username, in conn.execute('SELECT user_name FROM Name2Id'):
                        if username:
                            digest = hashlib.md5(username.encode()).hexdigest()
                            if digest in missing:
                                index.setdefault(digest, username)
                for digest in sorted(missing & index.keys()):
                    sessions.append({'username': index[digest]})
                    known.add(digest)
            finally:
                conn.close()
        return sessions

    def recent_messages(self, user, cutoff, cursor=None, limit=500):
        def read(tables):
            rows = []
            for conn, table in tables:
                shard = self._shard_label(conn)
                params = [shard, cutoff]
                where = '(CASE WHEN create_time>20000000000 THEN create_time/1000.0 ELSE create_time END) >= ?'
                sort_key = 'COALESCE(sort_seq, 0)'
                if cursor:
                    seq, previous_shard, local_id = cursor
                    if shard == previous_shard:
                        where += f' AND ({sort_key} > ? OR ({sort_key} = ? AND local_id > ?))'
                        params.extend((seq, seq, local_id))
                    else:
                        where += f' AND {sort_key} ' + ('>' if shard < previous_shard else '>=') + ' ?'
                        params.append(seq)
                rows.extend(conn.execute(
                    'SELECT *, ? AS shard FROM ' + table + ' WHERE ' + where +
                    f' ORDER BY {sort_key},local_id LIMIT ?', (*params, limit)).fetchall())
            rows.sort(key=lambda row: (row['sort_seq'] or 0, row['shard'], row['local_id']))
            return rows[:limit]
        return [self._msg_row_to_dict(row) for row in (self._run_msg_query(user, read) or [])]

    def get_nickname(self, user):
        if not hasattr(self, '_inbox_names'):
            self._inbox_names = self.nickname_map()
        return self._inbox_names.get(user, user)

    def _stable_key_dirs(self):
        return []

    def _load_key_cache(self, filename):
        # Only this account's DPAPI file; never inspect other tools' key caches.
        if Path(filename) != Path(self.keys_file) or not Path(filename).is_file():
            return {}
        data = json.loads(protect(Path(filename).read_bytes(), True))
        return {k: bytes.fromhex(v) for k, v in data.items()}

    def _save_keys(self):
        if self._keys:
            filename = Path(self.keys_file)
            temporary = filename.with_suffix('.tmp')
            temporary.write_bytes(protect(json.dumps({k: v.hex() for k, v in self._keys.items()}).encode()))
            os.replace(temporary, filename)

    def _try_other_accounts(self):
        return False

    def _select_account_by_keys(self, candidates):
        return False

    def _all_key_candidates(self):
        return set()

    def _auto_diagnose_key_failure(self, rel):
        raise RuntimeError('微信数据库密钥不可用，请保持微信登录后重启微信接收')

    def _msg_row_to_dict(self, row):
        result = super()._msg_row_to_dict(row)
        result['local_type'] = int(row['local_type'])
        if result['local_type'] & 0xffffffff == 43 and 'packed_info_data' in row.keys():
            packed = row['packed_info_data']
            if isinstance(packed, bytes):
                match = re.search(rb'([0-9a-fA-F]{32})', packed)
                if match:
                    result['video_key'] = match.group(1).decode('ascii').lower()
        result['sort_seq'] = int(result['sort_seq'] or 0)
        if (result['create_time'] or 0) > 20000000000:
            result['create_time'] /= 1000
        shard = row['shard']
        if not hasattr(self, '_names_by_shard'):
            self._names_by_shard = {}
        if shard not in self._names_by_shard:
            rel = next((rel for rel, path, _ in self._db_files if Path(path).name == shard), None)
            names = {}
            if rel and rel in self._keys:
                conn = self._open(rel)
                try:
                    table = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Name2Id%' ORDER BY name DESC LIMIT 1").fetchone()
                    if table:
                        quoted = table[0].replace('"', '""')
                        names = dict(conn.execute(f'SELECT rowid,user_name FROM "{quoted}"'))
                finally:
                    conn.close()
            self._names_by_shard[shard] = names
        result['sender_username'] = self._names_by_shard[shard].get(int(row['real_sender_id'] or 0), '')
        # Some native content columns are decoded as replacement characters by
        # the upstream connection. Prefer the compressed UTF-8 source when present.
        content = result.get('content', '')
        if '\ufffd' in content or any(ord(c) < 32 and c not in '\r\n\t' for c in content):
            compressed = row['compress_content']
            if isinstance(compressed, bytes) and compressed:
                from vendor.db import _get_zstd_module, _zstd_decompress
                zstd = _get_zstd_module()
                recovered = _zstd_decompress(zstd, compressed) if zstd else None
                if recovered: result['content'] = recovered
        return result

    def _shard_label(self, conn):
        return super()._shard_label(conn).rsplit('__', 1)[-1]


def select(private):
    selection = private / 'account-path.txt'
    if selection.exists():
        account_path = Path(selection.read_text(encoding='utf-8-sig').strip()).resolve()
        if not (account_path / 'db_storage').is_dir():
            raise RuntimeError('已选择的微信账号目录不可用，请重新选择')
        return account_path
    root = auto_detect_db_dir()
    accounts = list_accounts(root) if root else []
    if len(accounts) != 1:
        raise RuntimeError('未找到唯一微信账号，请从应用菜单选择微信账号目录')
    account_path = Path(accounts[0]['path']).resolve()
    selection.write_text(str(account_path), encoding='utf-8')
    return account_path


class CachedMedia(MediaDownloader):
    deferred_target = None

    def _copy_cached_attachment(self, source_path, cache_root, output_name, save_dir=None):
        if self.deferred_target and Path(cache_root).name in {'file', 'video'}:
            try:
                source = Path(source_path)
                info = source.stat()
                if source.is_file() and not source.is_symlink() and LARGE_ATTACHMENT <= info.st_size <= 50 * 1024 * 1024:
                    reference = attachment_reference(source, Path(self.db.account_dir), 'wechat', info)
                    if reference and reference['relative'].split('/')[:2] == ['msg', Path(cache_root).name]:
                        identifier, manifest = self.deferred_target
                        name = Path(output_name).name
                        if name and ':' not in name and not any(ord(char) < 32 for char in name):
                            record = {'version': 1, 'id': identifier, 'name': name, 'size': info.st_size, 'source': reference}
                            try:
                                previous = json.loads(manifest.read_text('utf-8'))
                            except (OSError, ValueError):
                                previous = None
                            if previous != record:
                                write_manifest(manifest, record)
                            self.deferred_result = {'id': identifier, 'name': name, 'size': info.st_size, 'status': 'on_demand'}
                            return None
            except (OSError, ValueError):
                pass
        return super()._copy_cached_attachment(source_path, cache_root, output_name, save_dir)

    @staticmethod
    def _ffmpeg_exe():
        local = Path(sys.executable).parent / 'ffmpeg.exe' if getattr(sys, 'frozen', False) else None
        return str(local) if local and local.is_file() else MediaDownloader._ffmpeg_exe()

    def _load_persisted_key(self):
        private = getattr(self.db, '_private', None)
        if private and (private / 'image-key.dpapi').exists():
            return protect((private / 'image-key.dpapi').read_bytes(), True).decode('ascii')
        return None

    def _persist_key(self, aes_key):
        pass

    def _scan_aes_key(self, *args, **kwargs):
        return None

    def download_image_original(self, *args, **kwargs):
        raise RuntimeError('仅允许读取本机已缓存媒体')


def cached_media(reader, cid, row, private):
    code = int(row.get('local_type', 0))
    if code in (47, 11000): return sticker(reader, row, private)
    method = {3: 'download_image'}.get(code)
    if not method:
        return {}
    identity = '\0'.join((reader.account_dir, cid, row['shard'], str(row['local_id'])))
    mid = hashlib.sha256(identity.encode()).hexdigest()
    folder = private.parent / 'attachments' / mid
    manifest = folder / 'manifest.json'
    try:
        if manifest.is_file():
            saved = json.loads(manifest.read_text(encoding='utf-8'))
            cached = folder / 'content' / saved['name']
            if cached.is_file() and code == 3 and cached.suffix == '.wxgf':
                decoded = reader._media._wxgf_to_jpg(cached.read_bytes())
                if decoded:
                    cached = cached.with_suffix('.jpg');cached.write_bytes(decoded)
                    saved.update(name=cached.name, size=len(decoded))
                    manifest.write_text(json.dumps(saved), encoding='utf-8')
            if cached.is_file() and (code != 3 or cached.suffix != '.wxgf'):
                return {('Image' if code == 3 else 'Attachment'): {
                    'id': mid, 'name': saved['name'], 'size': saved['size'],
                    'status': 'ready', 'quality': 'preview', 'type': row['type']}}
            if code != 3 and saved.get('source') and reader._on_demand:
                reference = saved['source']
                relative = Path(reference.get('relative', ''))
                if not relative.is_absolute() and len(relative.parts) >= 4 and relative.parts[:2] in {('msg', 'file'), ('msg', 'video')} and '..' not in relative.parts:
                    candidate = Path(reader.account_dir) / relative
                    try:
                        info = candidate.stat()
                        current = attachment_reference(candidate, reader.account_dir, 'wechat', info)
                        if current == reference and info.st_size == saved['size']:
                            return {'Attachment': {'id': mid, 'name': saved['name'], 'size': saved['size'], 'status': 'on_demand', 'type': row['type']}}
                    except OSError:
                        pass
                reader._media_attempted.discard(mid)
        if reader._media_budget <= 0 or mid in reader._media_attempted:
            return {'Image': {'status': 'pending'}} if code == 3 else {}
        reader._media_budget -= 1
        reader._media_attempted.add(mid)
        media = reader._media
        media.deferred_target = (mid, manifest) if reader._on_demand and code != 3 else None
        media.deferred_result = None
        try:
            source = getattr(media, method)(cid, row['local_id'], shard=row['shard'])
        finally:
            media.deferred_target = None
        if media.deferred_result:
            return {'Attachment': {**media.deferred_result, 'type': row['type']}}
        if source:
            source = Path(source)
            if code == 3 and source.suffix == '.wxgf': return {'Image': {'status': 'pending'}}
            name = source.name
            if any(c in name for c in '\\/:\x00') or source.stat().st_size > 50 * 1024 * 1024:
                return {}
            destination = folder / 'content' / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if source.resolve().is_relative_to(Path(media.save_dir).resolve()):
                source.unlink(missing_ok=True)
            manifest.write_text(json.dumps({'version': 1, 'id': mid, 'name': name,
                                           'size': destination.stat().st_size}), encoding='utf-8')
            return {('Image' if code == 3 else 'Attachment'): {
                'id': mid, 'name': name, 'size': destination.stat().st_size,
                'status': 'ready', 'quality': 'preview', 'type': row['type']}}
    except Exception:
        pass
    return {'Image': {'status': 'pending'}} if code == 3 else {}


def cached_video(reader, row, private, identifier, declared):
    account = Path(reader.account_dir)
    output = private.parent / 'attachments'
    offline = not reader._on_demand
    existing = video_file(output, identifier, None, account, 'wechat', declared)
    if existing:
        return existing
    key = row.get('video_key') or ''
    if not re.fullmatch(r'[a-f0-9]{32}', key) or row['create_time'] <= 0:
        return None
    month = time.strftime('%Y-%m', time.localtime(row['create_time']))
    for suffix in ('.mp4', '_raw.mp4'):
        source = account / 'msg' / 'video' / month / (key + suffix)
        found = video_file(output, identifier, source, account, 'wechat', declared, offline)
        if found:
            return found
    return None


def message_identity(reader, cid, row):
    return hashlib.sha256('\0'.join((reader.account_dir, cid, row['shard'], str(row['local_id']))).encode()).hexdigest()


def event(reader, session, row, private=None, files=None, records=None):
    cid = session['username']
    sender = row.get('sender_username') or ''
    conversation = 'wechat:' + reader.account + ':' + cid
    reference = message_reference(conversation, row.get('server_id'))
    identifier = message_identity(reader, cid, row)
    resolve = (lambda *args: records.resolve(identifier, row['create_time'], *args)) if records else None
    rendered = render_message(row, reader.get_nickname, conversation, record_media=resolve)
    description = rendered.pop('_file', None)
    if description and files is not None:
        rendered['Attachment'] = files.resolve(identifier, description, row['create_time'])
    if private and rendered.get('Video'):
        video = rendered['Video']
        media = cached_video(reader, row, private, identifier, video['size'])
        if media:
            video.update(media)
    return {'key': 'wechat:' + identifier, 'payload': {
        'Source': 'local-database', 'Platform': 'wechat',
        'ConversationId': conversation,
        'ConversationName': '[微信] ' + reader.get_nickname(cid),
        'ConversationType': 'group' if cid.endswith('@chatroom') else 'official' if cid.startswith('gh_') else 'person',
        'IsSelf': sender == reader.wxid,
        'FromUserName': 'wechat:' + sender, 'SenderName': reader.get_nickname(sender) if sender else '微信用户',
        'CreateTime': row['create_time'], 'MsgType': 'text', 'NativeType': row['type'],
        'NativeTypeCode': row.get('local_type'), **rendered,
        **({'MessageReference': reference} if reference else {}),
        **(cached_media(reader, cid, row, private) if private else {})}}


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def snapshot(reader, days, private):
    reader._refresh_db_files()
    reader._names_by_shard = {}
    reader._inbox_names = reader.nickname_map(refresh=True)
    reader._media_budget = 0
    reader._sticker_budget = 0
    reader._private = private
    current_mode = cache_on_demand(private.parent)
    if current_mode != getattr(reader, '_on_demand', None):
        reader._media_attempted = set()
    reader._on_demand = current_mode
    reader._media = CachedMedia(reader, save_dir=str(Path(reader.workdir) / 'media'))
    if not hasattr(reader, '_sticker_attempted'): reader._sticker_attempted = set()
    reader._sticker_files = {}
    for file in (Path(reader.account_dir) / 'cache').glob('*/Emoticon/*/*'):
        if file.is_file(): reader._sticker_files.setdefault(file.name, []).append(file)
    if not hasattr(reader, '_media_attempted'):
        reader._media_attempted = set()
    sessions = reader.inbox_sessions()
    cutoff = time.time() - days * 86400
    total = 0
    media_rows = []
    file_rows = []
    record_rows = []
    originals = {}
    for session in sessions:
        cursor = None
        while True:
            rows = reader.recent_messages(session['username'], cutoff, cursor)
            if not rows:
                break
            records = [event(reader, session, row, private if row.get('local_type', 0) & 0xffffffff == 43 else None)
                       for row in rows if row['create_time'] >= cutoff]
            media_rows.extend((session, row) for row in rows if row.get('local_type') in
                              (3, 47, 11000))
            file_rows.extend((session, row) for row in rows if row.get('local_type', 0) & 0xffffffff == 49)
            for row in rows:
                code = row.get('local_type', 0)
                if code & 0xffffffff == 49 and code >> 32 == 19:
                    record_rows.append((session, row))
                if code == 3 and str(row.get('server_id', '')).isdigit():
                    originals.setdefault((int(row['server_id']), int(row['create_time'])), []).append(
                        message_identity(reader, session['username'], row))
            if records:
                emit({'type': 'snapshot', 'messages': records})
                total += len(records)
            last = rows[-1]
            cursor = (last['sort_seq'], last['shard'], last['local_id'])
            if len(rows) < 500:
                break
    emit({'type': 'progress', 'ok': True, 'total': total, 'conversations': len(sessions),
          'partial': bool(reader.unkeyed), 'locked': len(reader.unkeyed)})
    if file_rows or record_rows:
        if not hasattr(reader, '_file_hashes'):
            reader._file_hashes = {}
        files = FileCache(reader.account_dir, private.parent / 'attachments', reader._file_hashes, not current_mode)
        file_batch = []
        for session, row in file_rows:
            item = event(reader, session, row, files=files)
            if 'Attachment' not in item['payload']:
                continue
            file_batch.append(item)
            if len(file_batch) >= 25:
                emit({'type': 'snapshot', 'messages': file_batch})
                file_batch = []
        if file_batch:
            emit({'type': 'snapshot', 'messages': file_batch})
    try:
        emit({'type': 'social', **social_snapshot(reader, private)})
    except Exception:
        emit({'type': 'social-error'})
    # Text and account discovery must never wait for an expired sticker URL.
    # Warm sticker cache concurrently, then emit media revisions in source order.
    from concurrent.futures import ThreadPoolExecutor
    reader._sticker_budget = 120
    reader._media_budget = 5000
    stickers = {}
    import re
    for session, row in sorted(media_rows, key=lambda pair: pair[1]['create_time'], reverse=True):
        if row.get('local_type') not in (47, 11000): continue
        match = re.search(r'\bmd5\s*=\s*[\'"]([a-fA-F0-9]{32})', row.get('content', ''))
        if match and match[1].lower() not in reader._sticker_attempted: stickers[match[1].lower()] = row
        if len(stickers) >= 12: break
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda row: sticker(reader, row, private), stickers.values()))
    reader._sticker_budget = 0
    batch = []
    end = time.monotonic() + 5
    for session, row in sorted(media_rows, key=lambda pair: pair[1]['create_time'], reverse=True):
        if time.monotonic() >= end:
            break
        batch.append(event(reader, session, row, private))
        if len(batch) >= 200:
            emit({'type': 'snapshot', 'messages': batch}); batch = []
    if batch: emit({'type': 'snapshot', 'messages': batch})
    if record_rows:
        media = RecordMedia(reader, private, originals, files)
        batch = []
        for session, row in record_rows:
            batch.append(event(reader, session, row, records=media))
            if len(batch) >= 15:
                emit({'type': 'snapshot', 'messages': batch})
                batch = []
        if batch:
            emit({'type': 'snapshot', 'messages': batch})
    emit({'type': 'health', 'ok': True, 'total': total, 'conversations': len(sessions),
          'partial': bool(reader.unkeyed), 'locked': len(reader.unkeyed)})
    return total, len(sessions)


def main():
    private = Path(sys.argv[sys.argv.index('--directory') + 1])
    private.mkdir(parents=True, exist_ok=True)
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 30
    reader = None
    last, last_scan = None, 0
    def scan_if_changed():
        nonlocal last, last_scan
        files = list((Path(reader.account_dir) / 'db_storage').glob('*/*.db*'))
        files.append(private.parent / 'storage-preferences.json')
        stamp = []
        for file in files:
            if file.name == 'storage-preferences.json' or (file.name.endswith(('.db', '.db-wal')) and 'fts' not in file.name):
                try:
                    stat = file.stat()
                    stamp.append((str(file), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))
                except FileNotFoundError:
                    pass
        for file in [*(Path(reader.account_dir) / 'msg' / 'video').glob('*/*.mp4'), *cache_files(reader.account_dir),
                     *record_cache_files(reader.account_dir)]:
            try:
                stamp.append((str(file), *file_identity(file)))
            except OSError:
                pass
        current = sorted(stamp)
        if current != last or time.monotonic() - last_scan >= 300:
            snapshot(reader, days, private)
            last, last_scan = current, time.monotonic()
        else:
            emit({'type': 'health', 'ok': True})
    with managed_snapshot(private) as workdir:
        try:
            profile = select(private)
            emit({'type': 'profile', 'path': str(profile / 'db_storage'), 'account': hashlib.sha256(str(profile).encode()).hexdigest()})
            reader = Reader(db_dir=str(profile.parent), account=profile.name,
                            keys_file=str(private / 'database-keys.dpapi'), workdir=workdir)
            required = [rel for rel, _, _ in reader._db_files if Path(rel).name == 'session.db' or Path(rel).name.startswith('message_')]
            if not required or any(not reader._key_works(rel) for rel in required):
                raise RuntimeError('微信会话或消息数据库未全部解锁，请保持微信登录后重试')
            if '--check' in sys.argv:
                emit({'type': 'check', 'ok': True, 'databases': len(reader._keys), 'sessions': len(reader.get_sessions(limit=100000))})
                return
            scan_if_changed()
        except Exception:
            emit({'type': 'health', 'ok': False, 'error': '微信读取失败：请保持微信登录，检查账号目录与客户端版本后重启微信接收。'})
            if '--check' in sys.argv:
                sys.exit(1)
        for line in sys.stdin:
            if line.strip() in {'refresh', 'attachments'} and reader is not None:
                try:
                    if line.strip() == 'attachments':
                        last = None
                    scan_if_changed()
                except Exception:
                    emit({'type': 'health', 'ok': False, 'error': '微信缓存暂时不可读，将在下次文件变化时重试。'})


if __name__ == '__main__':
    main()
