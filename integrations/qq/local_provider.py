"""Read-only QQNT databases; format references are listed in docs/qq-local.md."""
import hashlib
from contextlib import closing
import hmac
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import struct
import tempfile
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from integrations.qq.provider import QQProviderError
from integrations.qq.local_wal import LocalWALError, inspect_qq_wal, snapshot_qq_database
from integrations.qq.send_target import SendTargetError, resolve_send_target

PLAIN = b'SQLite format 3\x00'
HEADER, PAGE, RESERVE = 1024, 4096, 48
TABLES = {'private': 'c2c_msg_table', 'group': 'group_msg_table'}
MEDIA_SCAN_LIMIT, MEDIA_SCAN_SECONDS = 200000, 5
MAX_LOCAL_MEDIA_BYTES = 50 * 1024 * 1024


def _qq_crc64(value):
    crc = (1 << 64) - 1
    for char in value:
        byte = ord(char)
        for _ in range(8):
            mix = (crc ^ byte) & 1
            crc >>= 1
            if mix:
                crc ^= 0x95AC9329AC4BC9B5
            byte >>= 1
    return crc


def _qq_image_cache_names(digest):
    if not re.fullmatch(r'[a-f0-9]{32}', digest):
        return ()
    names = []
    for folder in ('chatraw', 'chatimg', 'chatthumb'):
        name = f'Cache_{_qq_crc64(f"{folder}:{digest.upper()}"):x}'
        names.extend((name, name + '_hd') if folder == 'chatthumb' else (name,))
    return tuple(names)


class LocalQQError(QQProviderError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _text(value):
    return value.decode('utf-8', 'replace') if isinstance(value, bytes) else str(value or '')


def _fields(data):
    """Bounded protobuf wire reader; unknown fields remain opaque."""
    if len(data) > 4 * 1024 * 1024:
        raise LocalQQError('message_too_large', 'QQ 消息内容超出读取上限')
    pos = 0

    def varint():
        nonlocal pos
        value = 0
        for shift in range(0, 70, 7):
            if pos >= len(data):
                raise ValueError('truncated protobuf')
            byte = data[pos]
            pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError('invalid protobuf')

    result = []
    while pos < len(data):
        tag = varint()
        number, wire = tag >> 3, tag & 7
        if not number or len(result) >= 4096:
            raise ValueError('invalid protobuf')
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            size = varint() if wire == 2 else 8 if wire == 1 else 4
            if size > len(data) - pos:
                raise ValueError('truncated protobuf')
            value = data[pos:pos + size]
            pos += size
        else:
            raise ValueError('unsupported protobuf')
        result.append((number, wire, value))
    return result


def _find(fields, wanted, depth=0):
    for number, wire, value in fields:
        if number == wanted:
            return value
        if wire == 2 and number != 47423 and depth < 6:
            try:
                found = _find(_fields(value), wanted, depth + 1)
            except ValueError:
                continue
            if found is not None:
                return found
    return None


def _decode(blob):
    if not isinstance(blob, bytes):
        return [], []
    try:
        fields = _fields(blob)
        elements = [value for number, wire, value in fields if number == 40800 and wire == 2]
        if not elements and any(number == 45002 for number, _, _ in fields):
            elements = [blob]
        rich, media = [], []
        for element in elements[:101]:
            detail = _fields(element)
            kind = _find(detail, 45002)
            text = _text(_find(detail, 45101))
            if kind == 1 or (kind is None and text):
                rich.append({'type': 'text', 'data': {'text': text}})
            elif kind in (2, 3, 4, 5):
                name = _text(_find(detail, 45402))
                digest = None
                for number in (45424, 45406, 45407, 45408):
                    candidate = _find(detail, number)
                    if isinstance(candidate, bytes):
                        candidate = candidate.hex() if len(candidate) == 16 else _text(candidate)
                    if isinstance(candidate, str) and re.fullmatch(r'[a-fA-F0-9]{32}', candidate):
                        digest = candidate.lower()
                        break
                media_type = {2: 'image', 3: 'file', 4: 'voice', 5: 'video'}[kind]
                item = {'kind': media_type, 'type': media_type, 'file_name': name, 'name': name}
                for field, label in ((45405, 'size'), (45411, 'width'), (45412, 'height'),
                                     (45906 if kind == 4 else 45410, 'duration')):
                    value = _find(detail, field)
                    if type(value) is int and 0 <= value <= 2**63 - 1:
                        item[label] = value
                if isinstance(digest, str) and re.fullmatch(r'[a-fA-F0-9]{32}', digest):
                    item['md5'] = digest.lower()
                media.append(item)
                transcript = _text(_find(detail, 45923)) if kind == 4 else ''
                if transcript:
                    rich.append({'type': 'text', 'data': {'text': transcript}})
            elif kind == 7:
                quoted = [value for number, wire, value in detail if number == 47423 and wire == 2]
                parts = []
                for value in quoted[:50]:
                    quote_fields = _fields(value)
                    quote_text = _text(_find(quote_fields, 45101))
                    quote_kind = _find(quote_fields, 45002)
                    parts.append(quote_text or {2: '[图片]', 3: '[文件]', 4: '[语音]', 5: '[视频]'}.get(quote_kind, ''))
                text = ''.join(parts) or _text(_find(detail, 47413))
                rich.append({'type': 'reply', 'data': {'content': text[:4096],
                    'senderUid': _text(_find(detail, 40020)),
                    'messageId': _text(_find(detail, 47416) or _find(detail, 47401))}})
            elif kind == 6:
                rich.append({'type': 'face', 'data': {'name': _text(_find(detail, 47602)) or '[表情]'}})
            elif kind == 11:
                rich.append({'type': 'face', 'data': {'name': _text(_find(detail, 80900)) or '[商城表情]'}})
            elif kind == 10:
                card = _text(_find(detail, 47901))[:65536]
                rich.append({'type': 'json', 'data': {'text': card or '[卡片]'}})
            else:
                notice = next((_text(_find(detail, number)) for number in (48153, 48157, 48214, 48271)
                               if _find(detail, number) is not None), '')
                rich.append({'type': 'system' if notice else 'unknown', 'data': {
                    'text': notice or text or '[未解析的 QQ 消息]', 'element_type': kind}})
        if not rich and not media:
            rich.append({'type': 'unknown', 'data': {'text': '[未解析的 QQ 消息]'}})
        return rich, media
    except ValueError:
        return [{'type': 'unknown', 'data': {'text': '[无法解析的 QQ 消息]'}}], []


def _keys(password, salt):
    key = bytes.fromhex(password[4:]) if len(password) == 68 and password.startswith('hex:') else hashlib.pbkdf2_hmac('sha512', password.encode(), salt, 4000, 32)
    mac_key = hashlib.pbkdf2_hmac('sha512', key, bytes(b ^ 0x3a for b in salt), 2, 32)
    return key, mac_key


def _decrypt_page(page, number, key, mac_key):
    if len(page) != PAGE:
        raise LocalQQError('database_incomplete', 'QQ 数据库页不完整，请等待本地写入完成')
    cipher = page[16 if number == 1 else 0:PAGE - RESERVE]
    iv = page[PAGE - RESERVE:PAGE - RESERVE + 16]
    digest = hmac.new(mac_key, cipher + iv + struct.pack('<I', number), hashlib.sha1).digest()
    if not hmac.compare_digest(digest, page[PAGE - RESERVE + 16:PAGE - RESERVE + 36]):
        raise LocalQQError('key_invalid', 'QQ 数据库密钥无效或数据库校验失败')
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return (PLAIN if number == 1 else b'') + decryptor.update(cipher) + decryptor.finalize() + page[-RESERVE:]


class LocalQQProvider:
    def __init__(self, config_path, cache_dir):
        self.config_path, self.cache_dir = Path(config_path), Path(cache_dir)
        self._conn = None
        self._temp = None
        self._profiles = {}
        self._groups = {}
        self._uins = {}
        self._schemas = {}
        self._source = None
        self.account_root = None
        self.account_id = ''
        self.warnings = []
        self._media_index = None
        self._legacy_key = False

    def _discover(self):
        try:
            config = json.loads(self.config_path.read_text('utf-8')) if self.config_path.is_file() else {}
        except (OSError, ValueError):
            raise LocalQQError('configuration_invalid', 'QQ 本地数据库配置无法读取') from None
        directory = config.get('data_dir')
        if directory:
            root = Path(directory)
            if not root.is_absolute():
                raise LocalQQError('directory_invalid', 'QQ 数据目录必须为本机绝对路径')
            paths = [root] if root.name == 'nt_msg.db' else [root / 'nt_msg.db', root / 'nt_db' / 'nt_msg.db',
                    root / 'nt_qq' / 'nt_db' / 'nt_msg.db']
        else:
            roots = [Path.home() / 'Documents' / 'Tencent Files', Path.home() / 'OneDrive' / 'Documents' / 'Tencent Files']
            if os.name == 'nt':
                import winreg
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as registry:
                        personal = winreg.QueryValueEx(registry, 'Personal')[0]
                    roots.append(Path(os.path.expandvars(personal)) / 'Tencent Files')
                except OSError:
                    pass
            paths = [path for root in roots if root.is_dir() for path in root.glob('*/nt_qq/nt_db/nt_msg.db')]
        found = sorted({path.resolve() for path in paths if path.is_file()})
        if not found:
            raise LocalQQError('database_missing', '未找到 QQNT 本地消息数据库，请选择账号数据目录')
        if len(found) != 1:
            raise LocalQQError('account_required', '检测到多个 QQ 账号，请选择需要读取的账号目录')
        source = found[0]
        account_root = source.parent.parent.parent if source.parent.name == 'nt_db' and source.parent.parent.name == 'nt_qq' else source.parent
        account = account_root.name if account_root.name.isdecimal() else 'db-' + hashlib.sha256(str(account_root).encode()).hexdigest()[:16]
        self._source, self.account_root, self.account_id = source, account_root, account
        configured_key = config.get('key')
        if configured_key:
            from integrations.qq.key_store import QQKeyProtectionError, load_key
            try:
                key, self._legacy_key = load_key(configured_key)
            except QQKeyProtectionError as exc:
                raise LocalQQError('key_protection_unavailable', str(exc)) from None
            except ValueError:
                raise LocalQQError('key_invalid', 'QQNT 密钥格式无效') from None
        else:
            key = os.environ.get('QQ_DB_KEY', '')
            self._legacy_key = False
            if not isinstance(key, str) or key and not re.fullmatch(r'[\x20-\x7e]{16}|hex:[a-fA-F0-9]{64}', key):
                raise LocalQQError('key_invalid', 'QQNT 密钥格式无效')
        return source, key

    @staticmethod
    def _probe(source, password):
        with source.open('rb') as handle:
            header = handle.read(HEADER)
            if header[:16] == PLAIN:
                return 'plain'
            if header[:16] != b'SQLite header 3\x00' or b'QQ_NT DB' not in header[:256]:
                raise LocalQQError('format_unsupported', '此数据库格式尚不支持，请选择 QQNT 的 nt_msg.db')
            if not password:
                raise LocalQQError('key_required', '已找到加密 QQ 本地数据库，请填写已有的 16 位数据库密钥；无需扫码')
            page = handle.read(PAGE)
            key, mac_key = _keys(password, page[:16])
            _decrypt_page(page, 1, key, mac_key)
            try:
                inspect_qq_wal(source)
            except LocalWALError as exc:
                raise LocalQQError(exc.code, 'QQ 数据库日志尚不完整或格式不受支持，请稍后重试') from None
            return 'encrypted'

    def status(self):
        result = {'provider': 'qq', 'mode': 'local_database', 'available': False, 'state': 'error', 'database_count': 0}
        try:
            source, key = self._discover()
            result['database_count'] = 1
            database_kind = self._probe(source, key)
            if self._legacy_key and database_kind == 'encrypted':
                from integrations.qq.key_store import migrate_legacy_key
                try:
                    migrate_legacy_key(self.config_path, key)
                except Exception:
                    raise LocalQQError('key_protection_unavailable',
                                       'QQ 数据库密钥已验证，但无法安全加密保存') from None
                self._legacy_key = False
            result.update(available=True, state='ready', error_code='', message='QQ 本地数据库可读取', account_id=self.account_id)
        except LocalQQError as exc:
            result.update(error_code=exc.code, message=str(exc),
                          state='locked' if exc.code in ('key_required', 'key_invalid') else 'error')
        except OSError:
            result.update(error_code='database_unavailable', message='QQ 本地数据库暂时无法读取')
        return result

    def _snapshot(self, source, password, target):
        kind = self._probe(source, password)
        if kind == 'plain':
            with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
                with closing(sqlite3.connect(target)) as output:
                    original.backup(output, pages=256)
            return
        with source.open('rb') as original:
            original.seek(HEADER)
            page = original.read(PAGE)
            key, mac_key = _keys(password, page[:16])
        try:
            snapshot_qq_database(source, target, lambda page, number: _decrypt_page(page, number, key, mac_key))
        except LocalWALError as exc:
            raise LocalQQError(exc.code, 'QQ 本地数据库快照校验失败或文件正在变化，请稍后重试') from None

    def _open(self):
        if self._conn is not None:
            return self._conn
        source, password = self._discover()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._temp = tempfile.TemporaryDirectory(prefix='snapshot-', dir=self.cache_dir)
        try:
            target = Path(self._temp.name) / 'nt_msg.db'
            self._snapshot(source, password, target)
            self._conn = sqlite3.connect(target.as_uri() + '?mode=rw', uri=True)
            self._conn.row_factory = sqlite3.Row
            mapping_columns = {row[1] for row in self._conn.execute('PRAGMA table_info("nt_uid_mapping_table")')}
            if {'48902', '1002'}.issubset(mapping_columns):
                self._uins = {_text(uid): _text(uin) for uid, uin in self._conn.execute(
                    'SELECT "48902","1002" FROM nt_uid_mapping_table')}
            for kind, table in TABLES.items():
                columns = {row[1] for row in self._conn.execute(f'PRAGMA table_info("{table}")')}
                if columns:
                    required = {'40050', '40800'}
                    peers = [field for field in (('40021', '40027', '40030') if kind == 'group' else ('40021', '40030')) if field in columns]
                    if not required.issubset(columns) or not peers:
                        raise LocalQQError('schema_unsupported', '此 QQNT 消息表结构尚未支持')
                    choices = [f'NULLIF(NULLIF(CAST("{field}" AS TEXT),\'\'),\'0\')' for field in peers]
                    peer = 'COALESCE(' + ','.join(choices) + ')' if len(choices) > 1 else choices[0]
                    self._schemas[kind] = (table, columns, peer)
                    # Index only the owned snapshot; source QQ files stay untouched.
                    self._conn.execute(f'CREATE INDEX hub_qq_{kind}_peer_time ON "{table}" ({peer},"40050" DESC)')
            if not self._schemas:
                raise LocalQQError('schema_unsupported', '数据库未包含受支持的 QQNT 消息表')
            self._conn.commit()
            self._conn.execute('PRAGMA query_only=ON')
            for filename, table, columns, destination in (
                ('profile_info.db', 'profile_info_v6', ('1000', '20002', '20009'), self._profiles),
                ('group_info.db', 'group_list', ('60001', '60007'), self._groups)):
                companion = source.parent / filename
                if not companion.is_file():
                    continue
                path = Path(self._temp.name) / filename
                try:
                    self._snapshot(companion, password, path)
                except LocalQQError:
                    self.warnings.append(filename + ': unavailable')
                    continue
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
                    available = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
                    if set(columns).issubset(available):
                        selected = ','.join('"' + column + '"' for column in columns)
                        for row in conn.execute(f'SELECT {selected} FROM "{table}"'):
                            destination[_text(row[0])] = _text(row[-1]) or _text(row[1])
                        if table == 'profile_info_v6' and '1002' in available:
                            self._uins.update({_text(uid): _text(uin) for uid, uin in conn.execute(
                                'SELECT "1000","1002" FROM profile_info_v6')})
            return self._conn
        except LocalQQError:
            self.close()
            raise
        except (OSError, sqlite3.Error):
            self.close()
            raise LocalQQError('database_unavailable', 'QQ 本地数据库读取失败，请检查目录、数据库完整性和密钥') from None

    def conversations(self):
        conn = self._open()
        result = []
        for kind, (table, _, peer) in self._schemas.items():
            for row in conn.execute(f'SELECT {peer},MAX("40050") FROM "{table}" GROUP BY {peer}'):
                identity = _text(row[0])
                if identity:
                    result.append({'id': identity, 'kind': kind, 'title': (self._groups if kind == 'group' else self._profiles).get(identity, identity),
                                   'last_timestamp': row[1], 'chatType': 2 if kind == 'group' else 1})
        return result

    def contacts(self):
        self._open()
        result = {conv['id']: {'id': conv['id'], 'name': conv['title'], 'kind': conv['kind']}
                  for conv in self.conversations()}
        for identity, name in self._profiles.items():
            result.setdefault(identity, {'id': identity, 'name': name or identity, 'kind': 'private'})
        for identity, name in self._groups.items():
            result.setdefault(identity, {'id': identity, 'name': name or identity, 'kind': 'group'})
        for identity, item in result.items():
            if identity in self._uins:
                item['uin'] = self._uins[identity]
        return list(result.values())

    def messages(self, conversation, limit=50, offset=0):
        if type(limit) is not int or not 1 <= limit <= 2000 or type(offset) is not int or offset < 0:
            raise ValueError('invalid message page')
        conn = self._open()
        kind = conversation.get('kind', 'private') if isinstance(conversation, dict) else 'private'
        identity = str(conversation.get('id', '')) if isinstance(conversation, dict) else str(conversation)
        if kind not in self._schemas:
            return []
        table, columns, peer = self._schemas[kind]
        rows = conn.execute(f'SELECT rowid AS local_rowid,* FROM "{table}" WHERE {peer}=? ORDER BY "40050" DESC,rowid DESC LIMIT ? OFFSET ?',
                            (identity, limit, offset))
        result = []
        for row in rows:
            value = lambda field: row[field] if field in columns else None
            rich, media = _decode(value('40800'))
            sender = _text(value('40020') or value('40033'))
            name = _text(value('40090') or value('40093')) or self._profiles.get(sender, sender)
            source_id = value('40001')
            stable_id = _text(source_id).strip() if source_id is not None else ''
            has_source_id = stable_id not in ('', '0')
            if not has_source_id:
                stable_id = str(row['local_rowid'])
            message_id = (f'{identity}:{stable_id}' if has_source_id
                          else f'{identity}:db:{table}:{stable_id}')
            result.append({'id': message_id, 'conversation_id': identity,
                           'sender_id': sender, 'sender_name': name, 'timestamp': value('40050'),
                           'type': media[0]['kind'] if media else 'system' if rich and rich[0]['type'] == 'system' else 'text',
                           'text': ''.join(item['data'].get('text', '') for item in rich),
                           'is_self': str(value('40033') or '') == self.account_id or self._uins.get(sender) == self.account_id,
                           'media': media, 'file_name': media[0]['file_name'] if media else None,
                           'roaming_content': {'elements': rich}, 'local_database': True})
        return result

    def local_avatar_url(self, identity, scope='user'):
        digest = hashlib.sha256(f'{self.account_id}\0{scope}\0{identity}'.encode()).hexdigest()
        return 'hub-avatar://qq/' + digest

    def send_target(self, identity, kind='private'):
        """Map a local peer identity onto the numeric target OneBot 11 accepts.

        Private peers carry QQNT uids while NapCat's ``send_private_msg`` needs
        the numeric uin; groups already carry their group code. Resolution uses
        only this provider's own snapshot tables, so no online lookup happens.
        """
        identity = str(identity or '')
        kind = str(kind or 'private')
        self._open()
        try:
            return resolve_send_target(identity, kind, self._uins)
        except SendTargetError as exc:
            # Callers only catch LocalQQError, which carries a stable code.
            raise LocalQQError(exc.code, str(exc)) from None

    def avatar_buffers(self, contacts):
        if self._source is None:
            self._discover()
        root = self.account_root / 'nt_qq' / 'nt_data' / 'avatar'
        if root.is_symlink() or not root.is_dir():
            return {}
        root = root.resolve()
        if not root.is_relative_to(self.account_root.resolve()):
            return {}
        result = {}
        for contact in contacts[:8]:
            identity = str(contact.get('id') or '')
            if not identity:
                continue
            scope = 'group' if contact.get('kind') == 'group' else 'user'
            md5 = lambda value: hashlib.md5(value.encode()).hexdigest()
            digest = md5(md5(md5(identity) + identity) + identity)
            for prefix in ('b_', 's_'):
                path = root / scope / digest[:2] / (prefix + digest)
                if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
                    continue
                try:
                    with path.open('rb') as stream:
                        payload = stream.read(2 * 1024 * 1024 + 1)
                except OSError:
                    continue
                if len(payload) <= 2 * 1024 * 1024:
                    result[identity] = payload
                    break
        return result

    def media(self, row, resource_index=None):
        if self._source is None:
            self._discover()
        extra = row.get('extra') if isinstance(row.get('extra'), dict) else {}
        entries = row.get('media') or extra.get('media') or []
        index = resource_index if resource_index is not None else 0
        if type(index) is not int or not isinstance(entries, list) or not 0 <= index < len(entries):
            return {'available': False, 'path': None, 'reason': '本地消息没有此附件'}
        account = str(row.get('account_id') or self.account_id)
        if account != self.account_id:
            raise LocalQQError('account_mismatch', '附件不属于当前 QQ 本地账号')
        entry = entries[index]
        name = str(entry.get('file_name') or '') if isinstance(entry, dict) else ''
        if not name or name != Path(name).name or any(char in name for char in '/\\'):
            return {'available': False, 'path': None, 'reason': '附件缺少安全的本地文件名'}
        account_root = self.account_root.resolve()
        data_root = self.account_root / 'nt_qq' / 'nt_data'
        if data_root.is_symlink():
            return {'available': False, 'path': None, 'reason': 'QQ 附件缓存目录不安全'}
        data_root = data_root.resolve()
        if not data_root.is_relative_to(account_root):
            return {'available': False, 'path': None, 'reason': 'QQ 附件缓存目录不安全'}
        category = {'image': 'Pic', 'voice': 'Ptt', 'video': 'Video', 'file': 'File'}.get(entry.get('kind') or entry.get('type'))
        roots = ([data_root / category] if category else [])
        if category == 'Pic':
            roots.append(data_root / 'chatpic')
        roots.append(data_root / 'chatraw')
        roots = [root for root in roots if root.is_dir() and not root.is_symlink()]
        if not roots:
            return {'available': False, 'path': None, 'reason': 'QQ 尚未将附件缓存到本机'}
        digest = str(entry.get('md5') or '').lower()
        cache_names = set(_qq_image_cache_names(digest)) if category == 'Pic' else set()
        expected_size = entry.get('size')
        matches = lambda candidate: candidate == name or bool(digest and digest in candidate.lower()) or candidate in cache_names
        indexed, limited = self._media_candidates(account, roots, matches, (name, digest, tuple(sorted(cache_names))))
        for root, relative_path in indexed:
            path = root / relative_path
            if path.is_symlink():
                continue
            path = path.resolve()
            if not path.is_relative_to(root) or not path.is_file():
                continue
            try:
                before = path.stat()
            except OSError:
                continue
            size = before.st_size
            if size > MAX_LOCAL_MEDIA_BYTES or type(expected_size) is int and expected_size != size:
                continue
            owned_root = self.cache_dir.resolve()
            candidate = path.name
            destination = owned_root / 'media' / hashlib.sha256((account + ':' + str(path)).encode()).hexdigest() / candidate
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.resolve().is_relative_to(owned_root):
                raise LocalQQError('cache_invalid', 'QQ 附件缓存目录不安全')
            temporary = destination.with_name(destination.name + '.part')
            if temporary.is_symlink():
                raise LocalQQError('cache_invalid', 'QQ 附件缓存文件不安全')
            actual_digest = hashlib.md5() if digest else None
            copied = 0
            try:
                with path.open('rb') as source:
                    source_before = os.fstat(source.fileno())
                    if self._media_stat_identity(source_before) != self._media_stat_identity(before):
                        temporary.unlink(missing_ok=True)
                        continue
                    with temporary.open('wb') as output:
                        while True:
                            chunk = source.read(min(1024 * 1024, MAX_LOCAL_MEDIA_BYTES - copied + 1))
                            if not chunk:
                                break
                            copied += len(chunk)
                            if copied > MAX_LOCAL_MEDIA_BYTES:
                                raise LocalQQError('media_changed', 'QQ 附件在读取期间超过大小上限')
                            if actual_digest is not None:
                                actual_digest.update(chunk)
                            output.write(chunk)
                        output.flush()
                    source_after = os.fstat(source.fileno())
                after = path.stat()
                if (copied != size or self._media_stat_identity(source_after) != self._media_stat_identity(before)
                        or self._media_stat_identity(after) != self._media_stat_identity(before)):
                    raise LocalQQError('media_changed', 'QQ 附件在读取期间发生变化')
                if type(expected_size) is int and copied != expected_size:
                    raise LocalQQError('media_changed', 'QQ 附件大小与消息索引不一致')
                if actual_digest is not None and actual_digest.hexdigest() != digest:
                    temporary.unlink(missing_ok=True)
                    continue
                os.replace(temporary, destination)
            except OSError:
                temporary.unlink(missing_ok=True)
                raise LocalQQError('media_unavailable', 'QQ 附件读取失败，未替换已有缓存') from None
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            return {'available': True, 'path': str(destination), 'content_type': mimetypes.guess_type(candidate)[0] or 'application/octet-stream'}
        reason = 'QQ 附件尚未缓存到本机，或缓存扫描达到上限' if limited else 'QQ 附件尚未缓存到本机'
        return {'available': False, 'path': None, 'reason': reason}

    @staticmethod
    def _media_stat_identity(stat):
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns

    def _media_candidates(self, account, roots, matches, match_key):
        key = (account, tuple(str(root.resolve()) for root in roots), match_key)
        index = self._media_index
        if index and index['key'] == key and index['complete']:
            try:
                if all(Path(directory).stat().st_mtime_ns == modified
                       for directory, modified in index['directories']):
                    return index['matches'], False
            except OSError:
                pass

        found, directories, scanned = [], [], 0
        deadline = time.monotonic() + MEDIA_SCAN_SECONDS
        limited = False
        for root in roots:
            for directory, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [child for child in dirs if not (Path(directory) / child).is_symlink()]
                scanned += len(files) + len(dirs)
                if scanned > MEDIA_SCAN_LIMIT or time.monotonic() > deadline:
                    limited = True
                    break
                current = Path(directory)
                directories.append((str(current), current.stat().st_mtime_ns))
                relative_directory = current.relative_to(root)
                found.extend((root, str(relative_directory / candidate)) for candidate in files if matches(candidate))
        self._media_index = {'key': key, 'matches': found, 'directories': directories,
                             'complete': not limited}
        return found, limited

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._temp is not None:
            self._temp.cleanup()
            self._temp = None
        self._schemas.clear()
        self._profiles.clear()
        self._groups.clear()
        self._uins.clear()
        self._source = None
        self.account_root = None
        self.account_id = ''
        self.warnings.clear()
        self._media_index = None
