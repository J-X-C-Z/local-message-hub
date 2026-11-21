"""Read-only adapter for the user's selected, local WeCom profile.

Upstream decryption/parser: freanocank/wecom-agent, MIT,
9e99e05954a8c02a6c1b46a9df91fef174ad1820. No client injection or send API.
"""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent / 'vendor'))
from wxwork_crypto import decrypt_database
from local_io import connect_readonly
from local_query import MessageStore
from message_render import render_message
from attachments import AttachmentSync
from rich_messages import StickerSync, ImageSync, VideoSync
from storage_cache import managed_snapshot


def unprotect(filename):
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    encrypted = Path(filename).read_bytes()
    buf = (ctypes.c_ubyte * len(encrypted)).from_buffer_copy(encrypted)
    source, target = Blob(len(encrypted), buf), Blob()
    api = ctypes.windll.crypt32.CryptUnprotectData
    api.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    api.restype = wintypes.BOOL
    if not api(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('本机加密密钥不可读')
    try:
        result = ctypes.string_at(target.data, target.size)
        if len(result) != 16:
            raise ValueError('密钥格式不正确')
        return result
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(target.data)


def read_shared(filename):
    k = ctypes.windll.kernel32
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = k.CreateFileW(str(filename), 0x80000000, 7, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        if k.GetLastError() in (2, 3):
            raise FileNotFoundError()
        raise OSError('源数据库暂时不可读')
    try:
        buf, size, chunks = ctypes.create_string_buffer(1024 * 1024), wintypes.DWORD(), []
        while True:
            if not k.ReadFile(handle, buf, len(buf), ctypes.byref(size), None):
                raise OSError('源数据库读取失败')
            if not size.value:
                return b''.join(chunks)
            chunks.append(buf.raw[:size.value])
    finally:
        k.CloseHandle(handle)


def selected_profile(private):
    profile = Path((private / 'profile-path.txt').read_text(encoding='utf-8-sig').strip()).resolve()
    # The stored selection must remain within this Windows user's documents.
    documents = ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, documents):
        raise ValueError('无法定位当前用户文档')
    allowed = (Path(documents.value) / 'WXWork').resolve()
    if not profile.is_relative_to(allowed) or profile.name != 'Data' or not (profile / 'message.db').is_file():
        raise ValueError('所选企业微信账号目录不可用')
    return profile


def fingerprint(profile, private):
    result = []
    for name in ('message.db', 'user.db', 'session.db'):
        for suffix in ('', '-wal'):
            try:
                stat = (profile / (name + suffix)).stat()
                result.append((stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))
            except FileNotFoundError:
                result.append(None)
    result.append((private.parent / 'storage-preferences.json').stat().st_mtime_ns if (private.parent / 'storage-preferences.json').is_file() else None)
    return tuple(result)


def snapshot(profile, private, key, days=30):
    # Plaintext snapshots exist only in a temporary directory and are removed
    # before the next iteration. Never modify the original client databases.
    with managed_snapshot(private) as folder:
        def database(name):
            source = profile / name
            if not source.is_file():
                return None
            destination = Path(folder) / name
            decrypt_database(str(source), str(destination), key, reader=read_shared)
            conn = connect_readonly(destination)
            conn.text_factory = lambda value: value.decode('utf-8', 'replace')
            return conn

        users, names, counts, members = {}, {}, Counter(), {}
        for dbname, query, target in [('user.db', 'SELECT id,name FROM user_table', users),
                                      ('session.db', 'SELECT id,name FROM conversation_table', names)]:
            conn = database(dbname)
            if conn is not None:
                try:
                    for uid, name in conn.execute(query):
                        if name:
                            target[str(uid)] = str(name)
                        if dbname == 'session.db' and str(uid).startswith('S:'):
                            counts.update(str(uid)[2:].split('_'))
                    if dbname == 'session.db':
                        columns = {row[1] for row in conn.execute('PRAGMA table_info(conversation_user_table)')}
                        if {'conversation_id', 'user_id', 'join_time', 'invite_user_id', 'nick_name'}.issubset(columns):
                            for cid, uid, joined, inviter, nickname in conn.execute('SELECT conversation_id,user_id,join_time,invite_user_id,nick_name FROM conversation_user_table'):
                                members[(str(cid), str(uid))] = {'name': nickname, 'join_time': joined, 'inviter': inviter}
                finally:
                    conn.close()
        self_id = counts.most_common(1)[0][0] if counts else None
        conn = database('message.db')
        if conn is None:
            raise ValueError('消息数据库不存在')
        try:
            for (cid,) in conn.execute('SELECT DISTINCT conversation_id FROM message_table'):
                cid = str(cid)
                if not names.get(cid) and cid.startswith('S:'):
                    other = [uid for uid in cid[2:].split('_') if uid != self_id]
                    names[cid] = users.get(other[0], cid) if other else cid
            store = MessageStore(conn, users=users, conversations=names, platform='windows',
                                 sender=lambda uid: users.get(str(uid), str(uid)))
            messages = []
            attachments = AttachmentSync(profile, private, read_shared)
            stickers = StickerSync(profile, private, read_shared, attachments)
            images = ImageSync(profile, private, read_shared, attachments)
            videos = VideoSync(profile, private, read_shared, attachments)
            for row in store.iter_messages(days=days):
                stable = '\0'.join(map(str, (str(profile), row['table'], row['conv_id'], row['local_id'])))
                timestamp = float(row['send_time'] or 0)
                if timestamp > 20000000000:
                    timestamp /= 1000
                attachment = attachments.attach(row)
                sticker = stickers.sync(row)
                image = images.sync(row)
                messages.append({'key': 'local:' + hashlib.sha256(stable.encode()).hexdigest(),
                    'payload': {'Source': 'local-database', 'ConversationId': row['conv_id'],
                        'ConversationName': row['conversation'],
                        'ConversationType': ('person' if row['conv_id'].startswith('S:') else
                                             'group' if row['conv_id'].startswith('R:') else 'service'),
                        'FromUserName': str(row['sender_id']), 'SenderName': row['sender'],
                        'CreateTime': timestamp, 'MsgType': 'text', 'NativeType': row['type'],
                        'NativeTypeCode': row['type_code'], **render_message(row, users, members, images.sync, videos.sync),
                        **({'Attachment': attachment} if attachment else {}),
                        **({'Sticker': sticker} if sticker else {}),
                        **({'Image': image} if image else {})}})
            return {'messages': messages, 'conversations': len(store.conversations()),
                    'total': conn.execute('SELECT COUNT(*) FROM message_table').fetchone()[0]}
        finally:
            conn.close()


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    private = Path(sys.argv[sys.argv.index('--directory') + 1]) if '--directory' in sys.argv else Path(os.environ['APPDATA']) / '企微收件台' / 'local-reading'
    days = int(sys.argv[sys.argv.index('--days') + 1]) if '--days' in sys.argv else 30
    private.mkdir(parents=True, exist_ok=True)
    check = '--check' in sys.argv
    last, force, last_scan = None, False, 0
    while True:
        try:
            profile = selected_profile(private)
            current = fingerprint(profile, private)
            if force or current != last or time.monotonic() - last_scan >= 300:
                key = unprotect(private / 'database-key.dpapi')
                result = snapshot(profile, private, key, days)
                if check:
                    emit({'ok': True, 'total_messages': result['total'],
                          'conversations': result['conversations'], 'recent_messages': len(result['messages'])})
                    return
                emit({'type': 'snapshot', **result})
                last = current
                last_scan = time.monotonic()
            emit({'type': 'health', 'ok': True})
        except Exception:
            # No exceptions containing keys, names, SQL or account paths in logs.
            emit({'type': 'health', 'ok': False, 'error': '本地消息读取失败；请确认企业微信已登录。客户端升级或密钥变化后需要重新适配。'})
            if check:
                sys.exit(1)
        command = sys.stdin.readline()
        if not command:
            return
        force = command.strip() == 'attachments'


if __name__ == '__main__':
    main()
