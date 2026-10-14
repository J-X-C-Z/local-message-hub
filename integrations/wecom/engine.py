"""同步引擎：把 sync.py 的核心逻辑抽成可被服务端调用的模块。

职责
  1. 快照活库 -> 解密 -> 读取消息
  2. 与状态对比，找出新增/变更消息
  3. 增量落盘（JSONL / TXT）并回调通知订阅者
  4. 维护内存索引，供 HTTP 接口快速查询

与 sync.py 的差别：本模块不打印进度，改为通过 hooks 回调，
并且把「这一轮的新增消息」返回给调用方，以便 SSE 推送。
"""
from __future__ import annotations

import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = HERE
if WORK not in sys.path:
    sys.path.insert(0, WORK)

from .config import CONFIG_LOCK, load_local_config, local_data_dir  # noqa: E402
from .decrypt_all import decrypt_db, PAGE_CONST          # noqa: E402
from .extract_chat2 import best_text, CTYPE, parse_fields  # noqa: E402

SETTINGS = load_local_config()
K16 = bytes.fromhex(SETTINGS['key_hex'])
WXROOT = SETTINGS['database_root']
MYVID = SETTINGS['user_slot_id']
LOCAL_DATA = local_data_dir()
OUT = os.path.join(LOCAL_DATA, 'decrypted')
SYNC_DIR = os.path.join(LOCAL_DATA, 'sync')
SNAP_ROOT = os.path.join(LOCAL_DATA, 'snapshots')
STATE = os.path.join(LOCAL_DATA, 'state.json')
MEDIA_INDEX = os.path.join(LOCAL_DATA, 'media_index.json')

JSONL = os.path.join(SYNC_DIR, 'messages.jsonl')
NEWTXT = os.path.join(SYNC_DIR, 'messages.txt')


def reload_runtime_settings() -> None:
    """Refresh mutable source settings after a local configure() update."""
    global SETTINGS, K16, WXROOT, MYVID
    with CONFIG_LOCK:
        settings = load_local_config()
        SETTINGS = settings
        K16 = bytes.fromhex(settings['key_hex'])
        WXROOT = settings['database_root']
        MYVID = settings['user_slot_id']

NON_TEXT = {2, 4, 13, 14, 15, 20, 22, 23, 29, 36, 2101, 1002}
EXTRA = {6: '位置', 1011: '系统', 1052: '置顶', 1055: '取消置顶',
         1022: '群公告', 38: '登录通知', 1001: '会话名'}
# 内容类型 -> 可挂载媒体时的类别
MEDIA_CT = {2: '图片', 4: '文件', 23: '语音', 36: '视频'}

# 「公告类」内容类型：群公告 / 公告 / 登录通知 / 系统提示
NOTICE_CT = {
    1022: '群公告',
    2101: '公告',
    38: '登录通知',
    1001: '会话名',
    1011: '系统提示',
    1002: '系统',
}
# 应用类消息（小程序 / 图文 / 会议等），可通过 message_appinfo 关联到企业应用
APP_CT = {14: '小程序', 15: '图文', 13: '链接', 1022: '会议'}


def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# --------------------------------------------------------------------------
# 状态
# --------------------------------------------------------------------------
def load_state() -> dict:
    if os.path.exists(STATE):
        try:
            with open(STATE, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {'seen': [], 'runs': 0, 'last': None}


def save_state(s: dict) -> None:
    if len(s.get('seen', [])) > 20000:
        s['seen'] = s['seen'][-20000:]
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False)
    os.replace(tmp, STATE)


# --------------------------------------------------------------------------
# 快照 / 解密
# --------------------------------------------------------------------------
def _safe_copy(src, dst) -> bool:
    for _ in range(3):
        try:
            shutil.copy2(src, dst)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def _clear_dir(path: str) -> None:
    """清空目录内容但保留目录本身（避免触发批量删除保护）。"""
    if not os.path.isdir(path):
        return
    for name in os.listdir(path):
        p = os.path.join(path, name)
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
        except Exception:
            pass


def _prune_snapshots(keep: int = 2) -> None:
    """只保留最近 N 个快照目录，其余清空内容（逐文件，避免批量删除保护）。"""
    if not os.path.isdir(SNAP_ROOT):
        return
    dirs = []
    for name in os.listdir(SNAP_ROOT):
        p = os.path.join(SNAP_ROOT, name)
        if os.path.isdir(p):
            try:
                dirs.append((os.path.getmtime(p), p))
            except Exception:
                pass
    dirs.sort(reverse=True)
    for _mt, p in dirs[keep:]:
        _clear_dir(p)
        try:
            os.rmdir(p)
        except Exception:
            pass


def snapshot_live() -> tuple[int, str | None]:
    """把活库复制到本轮专属快照目录。

    每轮使用独立目录，避免删除海量旧文件（既慢又会触发批量删除保护）。
    返回 (复制文件数, 本轮快照目录)。
    """
    os.makedirs(SNAP_ROOT, exist_ok=True)
    _prune_snapshots(keep=2)

    snap = os.path.join(SNAP_ROOT, time.strftime('run_%Y%m%d_%H%M%S'))
    os.makedirs(snap, exist_ok=True)

    n = 0
    srcs = []
    for pat in ('Data', 'Index', 'CacheMapping', 'Emotion', 'Avator'):
        d = os.path.join(WXROOT, MYVID, pat)
        if os.path.isdir(d):
            srcs.append((d, pat))
    olds = sorted(glob.glob(os.path.join(WXROOT, MYVID, 'Data.*.old')),
                  key=os.path.getmtime, reverse=True)[:2]
    for d in olds:
        srcs.append((d, os.path.basename(d)))

    for d, tag in srcs:
        dst_dir = os.path.join(snap, tag)
        os.makedirs(dst_dir, exist_ok=True)
        for root, _dirs, files in os.walk(d):
            rel = os.path.relpath(root, d)
            for f in files:
                if not (f.endswith('.db') or f.endswith('.db-wal')
                        or f.endswith('.db-shm')):
                    continue
                s = os.path.join(root, f)
                try:
                    if os.path.getsize(s) < 4096:
                        continue
                except Exception:
                    continue
                o = (os.path.join(dst_dir, rel, f) if rel != '.'
                     else os.path.join(dst_dir, f))
                os.makedirs(os.path.dirname(o), exist_ok=True)
                if _safe_copy(s, o):
                    n += 1
    return n, snap
    n = 0
    srcs = []
    for pat in ('Data', 'Index', 'CacheMapping', 'Emotion', 'Avator'):
        d = os.path.join(WXROOT, MYVID, pat)
        if os.path.isdir(d):
            srcs.append((d, pat))
    olds = sorted(glob.glob(os.path.join(WXROOT, MYVID, 'Data.*.old')),
                  key=os.path.getmtime, reverse=True)[:2]
    for d in olds:
        srcs.append((d, os.path.basename(d)))

    for d, tag in srcs:
        dst_dir = os.path.join(SNAP, tag)
        os.makedirs(dst_dir, exist_ok=True)
        for root, _dirs, files in os.walk(d):
            rel = os.path.relpath(root, d)
            for f in files:
                if not (f.endswith('.db') or f.endswith('.db-wal')
                        or f.endswith('.db-shm')):
                    continue
                s = os.path.join(root, f)
                try:
                    if os.path.getsize(s) < 4096:
                        continue
                except Exception:
                    continue
                o = (os.path.join(dst_dir, rel, f) if rel != '.'
                     else os.path.join(dst_dir, f))
                os.makedirs(os.path.dirname(o), exist_ok=True)
                if _safe_copy(s, o):
                    n += 1
    return n


def decrypt_snapshot(snap: str) -> dict:
    """解密本轮快照里的加密库，返回 {相对路径: 明文库路径}。"""
    out = {}
    for p in glob.glob(os.path.join(snap, '**', '*.db'), recursive=True):
        try:
            if os.path.getsize(p) < 12288:
                continue
            with open(p, 'rb') as f:
                h = f.read(24)
        except Exception:
            continue
        if len(h) < 24 or h[16:24] != PAGE_CONST:
            continue
        res, _good, _note = decrypt_db(p, 'ct1', K16)
        if res is None:
            continue
        rel = os.path.relpath(p, snap)
        dst = os.path.join(snap, '__dec__', rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(res)
        out[rel] = dst
    return out


def pick_msg_db(decs: dict):
    best = (0, None)
    for rel, p in decs.items():
        if os.path.basename(rel) != 'message.db':
            continue
        try:
            c = sqlite3.connect('file:' + p + '?mode=ro', uri=True)
            n = c.execute('select count(*) from message_table').fetchone()[0]
            c.close()
            if n > best[0]:
                best = (n, p)
        except Exception:
            pass
    return best[1]


def load_names_convs(decs: dict):
    names, convs = {}, {}
    for rel, p in decs.items():
        b = os.path.basename(rel)
        try:
            c = sqlite3.connect('file:' + p + '?mode=ro', uri=True)
            if b == 'user.db':
                cols = [r[1] for r in c.execute('pragma table_info(user_table)')]
                if 'id' in cols and 'name' in cols:
                    for i, nm in c.execute('select id,name from user_table'):
                        if i is not None and nm:
                            names.setdefault(str(i), nm)
            elif b == 'session.db':
                for (n,) in c.execute(
                        "select name from sqlite_master where type='table'"):
                    if 'conversation' not in n.lower():
                        continue
                    cols = [r[1] for r in c.execute(f'pragma table_info("{n}")')]
                    idc = next((x for x in cols if x.lower() in
                                ('conversation_id', 'conv_id', 'id')), None)
                    nc = next((x for x in cols if x.lower() in
                               ('name', 'title', 'conversation_name')), None)
                    if idc and nc:
                        for i, nm in c.execute(
                                f'select "{idc}","{nc}" from "{n}"'):
                            if i and nm and str(nm).strip():
                                convs.setdefault(str(i), nm)
            c.close()
        except Exception:
            pass
    return names, convs


def conv_name(cid, convs, names) -> str:
    s = str(cid)
    if s in convs:
        return convs[s]
    if s.startswith('S:'):
        for x in s[2:].split('_'):
            if x != MYVID and x in names:
                return names[x]
    return s


def msg_key(mid, conv, st, ctype, content) -> str:
    h = hashlib.md5()
    h.update(str(mid).encode())
    h.update(str(conv).encode())
    h.update(str(st).encode())
    h.update(str(ctype).encode())
    if content:
        b = content if isinstance(content, (bytes, bytearray)) else str(content).encode()
        h.update(b[:512])
    return h.hexdigest()


def clean_ctrl(s: str) -> str:
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s or '')


def render(row, names, convs, media_by_mid=None) -> dict:
    mid, sid, sender, conv, ctype, st, content = row
    if ctype in NON_TEXT:
        txt = f"[{CTYPE.get(ctype, EXTRA.get(ctype, 'type' + str(ctype)))}]"
        kind = 'sys'
    else:
        txt = clean_ctrl(best_text(content, ctype)).strip()
        if txt and re.fullmatch(r'[0-9a-fA-F]{24,}', txt):
            txt = ''
        kind = 'text'
        if not txt:
            txt = f"[{CTYPE.get(ctype, EXTRA.get(ctype, 'type' + str(ctype)))}]"
            kind = 'sys'
    try:
        ts = datetime.datetime.fromtimestamp(st).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        ts = str(st)
    m = {
        'message_id': mid, 'server_id': sid,
        'conversation': conv_name(conv, convs, names),
        'conversation_id': str(conv),
        'sender': names.get(str(sender), str(sender)),
        'sender_id': str(sender),
        'content_type': ctype, 'send_time': st, 'time': ts,
        'text': txt, 'kind': kind, 'me': str(sender) == MYVID,
    }
    if media_by_mid:
        med = media_by_mid.get(mid)
        if med:
            m['media'] = med
    return m


def read_messages(msgdb):
    return list(iter_read_messages(msgdb))


def iter_read_messages(msgdb):
    c = sqlite3.connect('file:' + msgdb + '?mode=ro', uri=True)
    try:
        yield from c.execute(
            'select message_id,server_id,sender_id,conversation_id,content_type,'
            'send_time,content from message_table order by send_time')
    finally:
        c.close()


# --------------------------------------------------------------------------
# 媒体索引（供消息挂附件）
# --------------------------------------------------------------------------
def rel_to_media_path(p: str) -> str:
    """把 media_index 里的绝对保存路径转成服务端可路由的相对路径。"""
    p = str(p).replace('\\', '/')
    candidate = ''
    for pre in ('提取结果/', './提取结果/'):
        if p.startswith(pre):
            candidate = p[len(pre):]
            break
    # 绝对路径 -> 去掉工作区前缀
    if not candidate:
        idx = p.find('提取结果/')
        candidate = p[idx + len('提取结果/'):] if idx >= 0 else os.path.basename(p)
    parts = candidate.split('/')
    if candidate.startswith('/') or any(part in ('', '..') for part in parts):
        return ''
    return candidate


def _iter_readonly_rows(path: str, table: str, columns: tuple[str, ...]):
    """Stream one known WeCom table only when its schema matches the reader."""
    connection = None
    try:
        connection = sqlite3.connect('file:' + path + '?mode=ro', uri=True)
        available = {row[1] for row in connection.execute(f'pragma table_info({table})')}
        if not set(columns).issubset(available):
            return
        projection = ','.join('"' + column + '"' for column in columns)
        yield from connection.execute(f'SELECT {projection} FROM "{table}"')
    except (OSError, sqlite3.Error, ValueError):
        return
    finally:
        if connection is not None:
            connection.close()


def _cache_file(root: str, filename: str, media_type: str = '') -> tuple[str, str] | None:
    """Resolve known CacheMapping names without leaving this account's cache."""
    from pathlib import Path

    cache_root = Path(root).resolve()
    raw = str(filename or '').strip().replace('\\', '/')
    if not raw:
        return None
    supplied = Path(raw)
    kind = str(media_type or '').strip().casefold()
    folder = {'image': ('Image',), 'photo': ('Image',), 'picture': ('Image',),
              'video': ('Video',), 'voice': ('Voice', 'Audio'), 'audio': ('Audio', 'Voice'),
              'file': ('File',), '图片': ('Image',), '视频': ('Video',),
              '语音': ('Voice', 'Audio'), '文件': ('File',)}.get(kind, ())
    candidates = [supplied] if supplied.is_absolute() else [
        cache_root / raw,
        *(cache_root / 'Cache' / directory / raw for directory in folder),
        cache_root / 'Cache' / 'Image' / raw,
        cache_root / 'Cache' / 'File' / raw,
        cache_root / 'Cache' / raw,
    ]
    for candidate in candidates:
        try:
            if candidate.is_symlink():
                continue
            resolved = candidate.resolve(strict=True)
            if resolved.is_file():
                relative = resolved.relative_to(cache_root).as_posix()
                return relative, str(resolved)
        except (OSError, RuntimeError, ValueError):
            continue
    return None


def build_media_context(full_root: str, snapshot_root: str | None = None,
                        decrypted_files: dict | None = None):
    """Return bounded media metadata and paths linked via local WeCom indexes."""
    media = []
    if os.path.exists(MEDIA_INDEX):
        try:
            with open(MEDIA_INDEX, encoding='utf-8') as f:
                media = json.load(f)
        except Exception:
            media = []
    if not isinstance(media, list):
        media = []
    media = [row for row in media if isinstance(row, dict)]

    by_md5 = {}
    for r in media:
        md5 = r.get('MD5')
        if isinstance(md5, str) and md5:
            by_md5.setdefault(md5.lower(), []).append(r)
    for r in media:
        r['rel'] = rel_to_media_path(r.get('保存路径', ''))

    mids_by_md5 = {}
    file_db_paths = [path for rel, path in (decrypted_files or {}).items()
                     if os.path.basename(rel).casefold() == 'file.db']
    raw_db_root = snapshot_root or full_root
    file_db_paths.extend(path for path in glob.glob(
        os.path.join(raw_db_root, '**', 'file.db'), recursive=True) if path not in file_db_paths)
    for path in file_db_paths:
        for mid, md5 in _iter_readonly_rows(path, 'file_table4', ('message_id', 'md5')):
            normalized = str(md5 or '').lower()
            if normalized:
                mids = mids_by_md5.setdefault(normalized, [])
                if mid not in mids:
                    mids.append(mid)

    # CacheMapping databases are copied into the per-run snapshot and inspected
    # read-only. This replaces the external media-index generation step.
    mapping_paths = []
    if snapshot_root:
        mapping_paths.extend(glob.glob(os.path.join(snapshot_root, 'CacheMapping', '**', '*.db'),
                                       recursive=True))
    mapping_paths.extend(path for rel, path in (decrypted_files or {}).items()
                         if 'cachemapping' in rel.casefold() and path not in mapping_paths)
    cache_mapping = {}
    for path in mapping_paths:
        for _kind, _key, filename, _modified, md5 in _iter_readonly_rows(
                path, 'mapping', ('type', 'key', 'file_name', 'last_modify_time', 'file_md5')):
            normalized = str(md5 or '').lower()
            if normalized and normalized in mids_by_md5:
                cache_mapping.setdefault(normalized, []).append((str(filename or ''), str(_kind or '')))

    resolved_by_md5 = {}
    for md5, filenames in cache_mapping.items():
        for filename, media_type in filenames:
            resolved = _cache_file(full_root, filename, media_type)
            if not resolved:
                continue
            relative, absolute = resolved
            if not any(item[0] == relative for item in resolved_by_md5.setdefault(md5, [])):
                resolved_by_md5[md5].append((relative, absolute, filename, media_type))

    by_mid = {}
    for md5, rows in by_md5.items():
        if md5 in resolved_by_md5:
            continue
        message_ids = mids_by_md5.get(md5, ())
        if not message_ids:
            continue
        for mid in message_ids:
            for r in rows:
                if not r.get('rel'):
                    continue
                by_mid.setdefault(mid, []).append({
                    'p': r['rel'], 'n': r.get('文件名', ''), 'k': r.get('类型', ''),
                    'sz': r.get('大小', 0),
                })
    indexed_media = {(str(row.get('MD5') or '').lower(), str(row.get('rel') or ''))
                     for row in media}
    for md5, resolved_files in resolved_by_md5.items():
        message_ids = mids_by_md5.get(md5, ())
        if not message_ids:
            continue
        for relative, absolute, filename, media_type in resolved_files:
            attachment = {'_local_cache_path': relative,
                          'n': os.path.basename(filename.replace('\\', '/')),
                          'k': media_type or os.path.splitext(filename)[1].lower(),
                          'sz': os.path.getsize(absolute)}
            for message_id in message_ids:
                by_mid.setdefault(message_id, []).append(dict(attachment))
            media_key = (md5, relative)
            if media_key not in indexed_media:
                media.append({'MD5': md5, '文件名': attachment['n'], '大小': attachment['sz'],
                              'rel': relative})
                indexed_media.add(media_key)
    return media, by_mid


# --------------------------------------------------------------------------
# 非聊天内容：公告 / 企业应用 / 通讯录 / 群成员
# --------------------------------------------------------------------------
def _ro(p):
    return sqlite3.connect('file:' + p + '?mode=ro', uri=True)


def _table_exists(c, name) -> bool:
    try:
        return bool(c.execute(
            "select 1 from sqlite_master where type='table' and name=?",
            (name,)).fetchone())
    except Exception:
        return False


def extract_notices(messages: list) -> list:
    """从消息里挑出公告类内容（群公告 / 公告 / 登录通知 / 系统提示）。

    企业微信的群公告没有独立表，而是以特定 content_type 的消息存在，
    因此直接从已解析的消息列表中筛选。
    """
    out = []
    for m in messages:
        ct = m.get('content_type')
        if ct not in NOTICE_CT:
            continue
        txt = (m.get('text') or '').strip()
        # 过滤掉纯占位（如 "[系统]"）与空内容
        if not txt or (txt.startswith('[') and txt.endswith(']')
                       and len(txt) <= 12):
            continue
        out.append({
            'kind': NOTICE_CT[ct],
            'conversation': m.get('conversation', ''),
            'sender': m.get('sender', ''),
            'time': m.get('time', ''),
            'send_time': m.get('send_time') or 0,
            'text': txt,
            'content_type': ct,
            'message_id': m.get('message_id'),
        })
    out.sort(key=lambda x: -(x['send_time'] or 0))
    return out


def extract_apps(full_root: str) -> list:
    """企业应用列表：解析 application_info.pb_content 取名称与图标。"""
    apps, seen = [], set()
    for p in glob.glob(os.path.join(full_root, '**', 'company.db'),
                       recursive=True):
        try:
            c = _ro(p)
            if not _table_exists(c, 'application_info'):
                c.close()
                continue
            cols = [r[1] for r in c.execute('pragma table_info(application_info)')]
            if 'pb_content' not in cols:
                c.close()
                continue
            for aid, bid, cid, oid, pb in c.execute(
                    'select app_id,business_id,circle_id,app_open_id,pb_content '
                    'from application_info'):
                if aid in seen:
                    continue
                seen.add(aid)
                name, icon, desc = '', '', ''
                try:
                    for fn, wt, val in parse_fields(bytes(pb or b'')):
                        if wt != 2:
                            continue
                        try:
                            t = val.decode('utf-8')
                        except Exception:
                            continue
                        if fn == 6 and not name and t.strip():
                            name = t.strip()
                        elif fn == 7 and t.startswith('http') and not icon:
                            icon = t
                        elif fn == 8 and not desc and t.strip():
                            desc = t.strip()
                except Exception:
                    pass
                if not name:
                    strs = []
                    try:
                        for _fn, _wt, v in parse_fields(bytes(pb or b'')):
                            try:
                                s = v.decode('utf-8')
                                if s.strip() and not s.startswith('http'):
                                    strs.append(s.strip())
                            except Exception:
                                pass
                    except Exception:
                        pass
                    name = strs[0] if strs else ''
                if not icon:
                    for _fn, _wt, v in parse_fields(bytes(pb or b'')):
                        try:
                            s = v.decode('utf-8')
                            if s.startswith('http'):
                                icon = s
                                break
                        except Exception:
                            pass
                apps.append({
                    'app_id': aid, 'business_id': bid, 'circle_id': cid,
                    'app_open_id': oid, 'name': name, 'icon': icon,
                    'desc': desc,
                })
            c.close()
        except Exception:
            pass
    apps.sort(key=lambda x: (x['name'] or 'zzz'))
    return apps


def extract_directory(full_root: str) -> dict:
    """企业通讯录：成员 / 部门 / 企业信息。"""
    members, depts, corps = [], [], []
    seen_u, seen_d = set(), set()
    for p in glob.glob(os.path.join(full_root, '**', '*.db'), recursive=True):
        base = os.path.basename(p)
        try:
            c = _ro(p)
            # 成员
            if _table_exists(c, 'user_table'):
                cols = [r[1] for r in c.execute('pragma table_info(user_table)')]
                want = [x for x in ('id', 'name', 'english_name', 'email',
                                    'mobile', 'telephone', 'position', 'gender',
                                    'avator_url', 'avatar_url', 'account',
                                    'office_phone', 'address', 'real_name',
                                    'birthday') if x in cols]
                if 'id' in cols:
                    q = 'select ' + ','.join(f'"{x}"' for x in want) + ' from user_table'
                    for row in c.execute(q):
                        d = dict(zip(want, row))
                        uid = str(d.get('id'))
                        if uid in seen_u or not uid:
                            continue
                        seen_u.add(uid)
                        members.append({
                            'user_id': uid,
                            'name': d.get('name') or '',
                            'english_name': d.get('english_name') or '',
                            'email': d.get('email') or '',
                            'mobile': d.get('mobile') or '',
                            'telephone': d.get('telephone') or '',
                            'position': d.get('position') or '',
                            'gender': d.get('gender'),
                            'avatar': d.get('avator_url') or d.get('avatar_url') or '',
                            'account': d.get('account') or '',
                            'office_phone': d.get('office_phone') or '',
                            'address': d.get('address') or '',
                        })
            # 部门
            for tname in ('department_tableV2', 'department_table_v2',
                          'department_table'):
                if _table_exists(c, tname):
                    cols = [r[1] for r in
                            c.execute(f'pragma table_info("{tname}")')]
                    want = [x for x in ('id', 'name', 'parent_id', 'level',
                                        'user_count', 'corpany_id',
                                        'company_id') if x in cols]
                    if 'id' in cols:
                        for row in c.execute(
                                'select ' + ','.join(f'"{x}"' for x in want)
                                + f' from "{tname}"'):
                            d = dict(zip(want, row))
                            did = str(d.get('id'))
                            if did in seen_d or not did:
                                continue
                            seen_d.add(did)
                            depts.append({
                                'dept_id': did,
                                'name': d.get('name') or '',
                                'parent_id': str(d.get('parent_id') or ''),
                                'level': d.get('level'),
                                'user_count': d.get('user_count'),
                            })
                    break
            # 企业信息
            if _table_exists(c, 'self_corp_list_table'):
                for cid, pb in c.execute(
                        'select corpany_id,self_corp_info from self_corp_list_table'):
                    txts = []
                    try:
                        for _fn, _wt, v in parse_fields(bytes(pb or b'')):
                            try:
                                s = v.decode('utf-8')
                                if s.strip():
                                    txts.append(s.strip())
                            except Exception:
                                pass
                    except Exception:
                        pass
                    corps.append({'corp_id': str(cid),
                                  'info': ' | '.join(txts)[:300]})
            c.close()
        except Exception:
            pass

    # 去重
    def dedup(seq, key):
        seen, out = set(), []
        for x in seq:
            k = x.get(key)
            if k in seen:
                continue
            seen.add(k)
            out.append(x)
        return out

    members = dedup(members, 'user_id')
    depts = dedup(depts, 'dept_id')
    corps = dedup(corps, 'corp_id')
    members.sort(key=lambda x: (x['name'] or 'zzz'))
    depts.sort(key=lambda x: (x['level'] if x['level'] is not None else 0,
                              x['name'] or ''))
    return {'members': members, 'departments': depts, 'corps': corps}


def extract_members_of_groups(full_root: str) -> list:
    """群成员表：会话 <-> 成员映射。"""
    out, seen = [], set()
    for p in glob.glob(os.path.join(full_root, '**', 'session.db'),
                       recursive=True):
        try:
            c = _ro(p)
            for tname in ('conversation_user_table',
                          'increment_conversation_user_table'):
                if not _table_exists(c, tname):
                    continue
                cols = [r[1] for r in c.execute(f'pragma table_info("{tname}")')]
                want = [x for x in ('conversation_id', 'conversation_nid', 'cid',
                                    'user_id', 'uid', 'vid', 'join_time',
                                    'nick_name', 'nickname', 'remark')
                        if x in cols]
                if not want:
                    continue
                for row in c.execute(
                        'select ' + ','.join(f'"{x}"' for x in want)
                        + f' from "{tname}"'):
                    d = dict(zip(want, row))
                    conv = str(d.get('conversation_id')
                               or d.get('conversation_nid') or d.get('cid') or '')
                    uid = str(d.get('user_id') or d.get('uid')
                              or d.get('vid') or '')
                    k = (conv, uid)
                    if not conv or not uid or k in seen:
                        continue
                    seen.add(k)
                    out.append({
                        'conversation_id': conv, 'user_id': uid,
                        'nick_name': d.get('nick_name') or d.get('nickname') or '',
                        'remark': d.get('remark') or '',
                        'join_time': d.get('join_time'),
                    })
            c.close()
        except Exception:
            pass
    return out


# --------------------------------------------------------------------------
# 引擎
# --------------------------------------------------------------------------
def _existing_ids(path: str) -> set:
    """读出 JSONL 里已存在的 message_id 集合（幂等写入用）。"""
    ids = set()
    if not os.path.exists(path):
        return ids
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ids.add(str(json.loads(line).get('message_id')))
                except Exception:
                    continue
    except Exception:
        pass
    return ids


class SyncEngine:
    """周期性同步，维护内存消息索引，并向订阅者推送增量。"""

    def __init__(self, interval: int = 30, full_root: str | None = None,
                 logger=print):
        reload_runtime_settings()
        self.interval = interval
        self._uses_config_root = full_root is None
        self.full_root = full_root or WXROOT
        self.log = logger
        self.lock = threading.Lock()
        self.messages: list[dict] = []      # 全量消息（内存）
        self.msg_db_name = ''
        self.names: dict = {}
        self.convs: dict = {}
        self.media: list = []
        self.media_by_mid: dict = {}
        self.stats_cache = None
        self.running = False
        self.thread = None
        self.subscribers: list = []         # callable(event: dict)
        self.last_run = None
        self.run_count = 0
        self.new_count_total = 0
        self.error = None
        # 非聊天内容（较慢，按轮缓存，不每轮重算）
        self.content_cache = None
        self.content_stamp = None
        self._released_notices = None
        self._released_summary = None

    # ---- 订阅 ----
    def subscribe(self, cb):
        with self.lock:
            self.subscribers.append(cb)

    def unsubscribe(self, cb):
        with self.lock:
            if cb in self.subscribers:
                self.subscribers.remove(cb)

    def _emit(self, ev: dict):
        with self.lock:
            subs = list(self.subscribers)
        if not subs:
            return
        if callable(ev):
            ev = ev()
        for cb in subs:
            try:
                cb(ev)
            except Exception:
                pass

    # ---- 单轮 ----
    def run_once(self, do_viewer=False) -> list:
        with CONFIG_LOCK:
            return self._run_once_locked(do_viewer)

    def _run_once_locked(self, do_viewer=False) -> list:
        if self._uses_config_root:
            self.full_root = WXROOT
        os.makedirs(SYNC_DIR, exist_ok=True)
        t0 = time.time()
        n, snap = snapshot_live()
        decs = decrypt_snapshot(snap)
        if not decs:
            self.error = '未找到可解密的库（客户端可能未运行过）'
            self._emit({'type': 'status', 'error': self.error, 'ts': _now()})
            return []

        msgdb = pick_msg_db(decs)
        if not msgdb:
            self.error = '未找到可用 message.db'
            self._emit({'type': 'status', 'error': self.error, 'ts': _now()})
            return []

        names, convs = load_names_convs(decs)
        media, by_mid = build_media_context(os.path.join(self.full_root, MYVID), snap, decs)

        state = load_state()
        seen = set(state.get('seen', []))
        new = []
        allmsgs = []
        for r in iter_read_messages(msgdb):
            m = render(r, names, convs, by_mid)
            kk = msg_key(r[0], r[3], r[5], r[4], r[6])
            m['_k'] = kk
            allmsgs.append(m)
            if kk not in seen:
                seen.add(kk)
                new.append(m)

        allmsgs.sort(key=lambda x: x['send_time'] or 0)

        # 更新内存索引
        with self.lock:
            self.messages = allmsgs
            self.names = names
            self.convs = convs
            self.media = media
            self.media_by_mid = by_mid
            self.msg_db_name = os.path.relpath(msgdb, snap)
            self.stats_cache = None
            self._released_notices = None
            self._released_summary = None
            self.last_run = _now()
            self.run_count += 1
            self.error = None

        if new:
            new.sort(key=lambda m: m['send_time'] or 0)
            self._append_new(new)
            self.new_count_total += len(new)
            self.log(f"★ 新增 {len(new)} 条消息")
            self._emit(lambda: {
                'type': 'new', 'ts': _now(), 'count': len(new),
                'messages': [strip_internal(m) for m in new],
            })
        else:
            self.log('无新增消息')

        st = load_state()
        st['seen'] = list(seen)
        st['runs'] = st.get('runs', 0) + 1
        st['last'] = _now()
        st['msg_total'] = len(allmsgs)
        save_state(st)

        self._emit({
            'type': 'status', 'ts': _now(), 'error': None,
            'files': n, 'dbs': len(decs), 'messages': len(allmsgs),
            'conversations': len(set(m['conversation'] for m in allmsgs)),
            'people': len(names), 'media': len(media),
            'elapsed': round(time.time() - t0, 1),
        })
        return new

    def _append_new(self, new: list) -> None:
        """追加新增消息到 JSONL / TXT。

        幂等保护：写之前先把文件里已有的 message_id 集合读出来，
        只追加真正没写过的，避免因状态回退或重复运行导致日志膨胀。
        """
        have = _existing_ids(JSONL)
        written = []
        for m in new:
            mid = str(m.get('message_id'))
            if mid in have:
                continue
            have.add(mid)
            written.append(m)
        if not written:
            return

        with open(JSONL, 'a', encoding='utf-8') as f:
            for m in written:
                f.write(json.dumps(strip_internal(m), ensure_ascii=False) + '\n')

        # 文本侧用「时间+会话+发送者」去重
        seen_txt = set()
        if os.path.exists(NEWTXT):
            try:
                with open(NEWTXT, encoding='utf-8') as f:
                    seen_txt = set(f.read().splitlines())
            except Exception:
                pass
        with open(NEWTXT, 'a', encoding='utf-8') as f:
            for m in written:
                line = (f"[{m['time']}] ({m['conversation']}) "
                        f"{m['sender']}: {m['text']}")
                if line in seen_txt:
                    continue
                seen_txt.add(line)
                f.write(line + '\n')

    # ---- 循环 ----
    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            try:
                self.run_once()
            except Exception as e:      # noqa: BLE001
                self.error = f'{type(e).__name__}: {e}'
                self.log(f'轮次异常: {self.error}')
                self._emit({'type': 'status', 'error': self.error, 'ts': _now()})
            for _ in range(self.interval):
                if not self.running:
                    return
                time.sleep(1)

    # ---- 非聊天内容 ----
    def get_content(self, force: bool = False) -> dict:
        """公告 / 应用 / 通讯录 / 群成员，带缓存。

        这些数据来自全量解密目录，变化很慢，因此默认只在首次或显式
        刷新时重算，避免每轮同步都做一遍全库扫描。
        """
        with self.lock:
            if self.content_cache is not None and not force:
                return self.content_cache

        t0 = time.time()
        with self.lock:
            notices = self._released_notices
            messages = self.messages
        if notices is None:
            notices = extract_notices(messages)
        apps = extract_apps(self.full_root)
        directory = extract_directory(self.full_root)
        gmembers = extract_members_of_groups(self.full_root)

        # 群成员富化：补上姓名
        nmap = {m['user_id']: m['name'] for m in directory['members']}
        for g in gmembers:
            g['name'] = nmap.get(g['user_id'], g['nick_name'] or '')

        # 会话 -> 群成员（同时按 原始ID 与 显示名 建索引，便于前端直接用会话名取）
        name_by_id = {}
        try:
            for p in glob.glob(os.path.join(self.full_root, '**', 'session.db'),
                               recursive=True):
                c = _ro(p)
                if _table_exists(c, 'conversation_table'):
                    for cid, nm in c.execute(
                            'select id,name from conversation_table'):
                        if cid and nm and str(nm).strip():
                            name_by_id[str(cid)] = str(nm).strip()
                c.close()
        except Exception:
            pass

        by_conv = {}
        for g in gmembers:
            by_conv.setdefault(g['conversation_id'], []).append(g)
            disp = name_by_id.get(g['conversation_id'])
            if disp:
                by_conv.setdefault(disp, []).append(g)

        for k in by_conv:
            by_conv[k].sort(key=lambda x: (x.get('name') or 'zzz'))

        data = {
            'notices': notices,
            'apps': apps,
            'members': directory['members'],
            'departments': directory['departments'],
            'corps': directory['corps'],
            'group_members': by_conv,
            'counts': {
                'notices': len(notices),
                'apps': len(apps),
                'members': len(directory['members']),
                'departments': len(directory['departments']),
                'group_members': len(gmembers),
            },
            'elapsed': round(time.time() - t0, 1),
        }
        with self.lock:
            self.content_cache = data
            self.content_stamp = _now()
        return data

    # ---- 查询 ----
    def snapshot_messages(self):
        with self.lock:
            return list(self.messages)

    def iter_messages(self):
        # Sync publishes a replacement list; retaining this reference gives a
        # stable read view without another full-size array of message pointers.
        with self.lock:
            snapshot = self.messages
        return iter(snapshot)

    def release_messages(self):
        """Drop Hub-ingested text while retaining non-chat notices and metadata."""
        with self.lock:
            if self._released_summary is not None:
                return dict(self._released_summary)
            self._released_notices = extract_notices(self.messages)
            self._released_summary = {
                'messages': len(self.messages),
                'conversations': len(set(m['conversation'] for m in self.messages)),
            }
            self.messages = []
            self.stats_cache = None
            return dict(self._released_summary)

    def snapshot_media(self):
        with self.lock:
            return list(self.media)

    @property
    def cache_root(self):
        return os.path.join(self.full_root, MYVID)

    def info(self):
        with self.lock:
            return {
                'running': self.running,
                'interval': self.interval,
                'run_count': self.run_count,
                'last_run': self.last_run,
                'messages': (self._released_summary or {}).get('messages', len(self.messages)),
                'conversations': (self._released_summary or {}).get(
                    'conversations', len(set(m['conversation'] for m in self.messages))),
                'people': len(self.names),
                'media': len(self.media),
                'new_total': self.new_count_total,
                'db': self.msg_db_name,
                'error': self.error,
                'content': (self.content_cache or {}).get('counts'),
            }


def strip_internal(m: dict) -> dict:
    if isinstance(m, dict):
        return {k: strip_internal(v) for k, v in m.items() if not k.startswith('_')}
    if isinstance(m, list):
        return [strip_internal(value) for value in m]
    return m


# --------------------------------------------------------------------------
# 统计
# --------------------------------------------------------------------------
def compute_stats(messages: list, media: list) -> dict:
    by_type, by_hour, by_day, by_sender, by_conv = {}, {}, {}, {}, {}
    me_count = 0
    for m in messages:
        lab = CTYPE.get(m['content_type'],
                        EXTRA.get(m['content_type'], 'type' + str(m['content_type'])))
        by_type[lab] = by_type.get(lab, 0) + 1
        try:
            dt = datetime.datetime.fromtimestamp(m['send_time'])
            by_hour[dt.hour] = by_hour.get(dt.hour, 0) + 1
            d = dt.strftime('%Y-%m-%d')
            by_day[d] = by_day.get(d, 0) + 1
        except Exception:
            pass
        by_sender[m['sender']] = by_sender.get(m['sender'], 0) + 1
        by_conv[m['conversation']] = by_conv.get(m['conversation'], 0) + 1
        if m['me']:
            me_count += 1

    def top(d, n=20):
        return [{'name': k, 'value': v}
                for k, v in sorted(d.items(), key=lambda x: -x[1])[:n]]

    # 逐会话的参与者排行
    conv_detail = {}
    for m in messages:
        c = m['conversation']
        cd = conv_detail.setdefault(c, {'messages': 0, 'people': {}, 'media': 0})
        cd['messages'] += 1
        cd['people'][m['sender']] = cd['people'].get(m['sender'], 0) + 1
        if m.get('media'):
            cd['media'] += len(m['media'])
    conv_list = []
    for c, cd in conv_detail.items():
        conv_list.append({
            'name': c, 'messages': cd['messages'],
            'people': len(cd['people']), 'media': cd['media'],
            'top': [{'name': k, 'value': v} for k, v in
                    sorted(cd['people'].items(), key=lambda x: -x[1])[:8]],
        })
    conv_list.sort(key=lambda x: -x['messages'])

    hours = [{'hour': h, 'value': by_hour.get(h, 0)} for h in range(24)]
    days = [{'date': k, 'value': v} for k, v in sorted(by_day.items())]

    return {
        'total': len(messages),
        'me': me_count,
        'other': len(messages) - me_count,
        'conversations': len(by_conv),
        'people': len(by_sender),
        'media_files': len(media),
        'by_type': top(by_type, 30),
        'by_hour': hours,
        'by_day': days,
        'top_senders': top(by_sender, 30),
        'top_convs': top(by_conv, 30),
        'conv_detail': conv_list,
    }
