import hashlib
from contextlib import closing
import hmac
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from integrations.qq.local_provider import (
    HEADER, PAGE, RESERVE, LocalQQError, LocalQQProvider, _decode, _decrypt_page,
    _keys, _qq_image_cache_names,
)
from integrations.qq import key_store


def varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def field(number, value):
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return varint(number * 8 + 2) + varint(len(value)) + value
    return varint(number * 8) + varint(value)


class LocalProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.account = self.root / '10001'
        self.source = self.account / 'nt_qq' / 'nt_db' / 'nt_msg.db'
        self.source.parent.mkdir(parents=True)
        self.config = self.root / 'qq-local.json'
        self.config.write_text(json.dumps({'data_dir': str(self.account)}))
        self.provider = LocalQQProvider(self.config, self.root / 'cache')

    def tearDown(self):
        self.provider.close()
        self.temp.cleanup()

    def fixture(self):
        with closing(sqlite3.connect(self.source)) as conn:
            conn.execute('CREATE TABLE c2c_msg_table ("40001" INTEGER,"40021" TEXT,"40020" TEXT,"40033" TEXT,"40050" INTEGER,"40800" BLOB)')
            body = field(40800, field(45002, 1) + field(45101, 'synthetic text')) + field(40800, field(45002, 2) + field(45402, 'fixture.png'))
            conn.execute('INSERT INTO c2c_msg_table VALUES (1,?,?,?,100,?)', ('peer', 'owner', '10001', body))
            conn.commit()

    def test_plain_database_query_media_and_original_unchanged(self):
        self.fixture()
        before = self.source.read_bytes()
        self.assertTrue(self.provider.status()['available'])
        self.assertEqual(self.provider.status()['account_id'], '10001')
        conv = self.provider.conversations()[0]
        row = self.provider.messages(conv, limit=1)[0]
        self.assertEqual(row['text'], 'synthetic text')
        self.assertTrue(row['local_database'])
        self.assertTrue(row['is_self'])
        self.assertEqual(row['media'][0]['kind'], 'image')
        media = self.account / 'nt_qq' / 'nt_data' / 'chatraw' / 'year' / 'fixture.png'
        media.parent.mkdir(parents=True)
        media.write_bytes(b'synthetic fixture bytes')
        resolved = self.provider.media(dict(row, account_id='10001'))
        self.assertTrue(resolved['available'])
        self.assertTrue(Path(resolved['path']).is_relative_to(self.provider.cache_dir))
        self.assertEqual(Path(resolved['path']).read_bytes(), media.read_bytes())
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.provider.messages(conv, offset=1), [])

    def test_messages_page_keyset_covers_tied_and_null_timestamps(self):
        self.fixture()
        body = field(40800, field(45002, 1) + field(45101, 'synthetic page'))
        with closing(sqlite3.connect(self.source)) as conn:
            conn.executemany('INSERT INTO c2c_msg_table VALUES (?,?,?,?,?,?)', [
                (2, 'peer', 'peer', '10001', 100, body),
                (3, 'peer', 'peer', '10001', 100, body),
                (4, 'peer', 'peer', '10001', None, body),
            ])
            conn.commit()

        conversation = self.provider.conversations()[0]
        expected = [row['id'] for row in self.provider.messages(conversation, limit=10)]
        cursor, actual = None, []
        while True:
            page = self.provider.messages_page(conversation, limit=2, cursor=cursor)
            actual.extend(row['id'] for row in page['items'])
            cursor = page['next_cursor']
            if not page['has_more']:
                break

        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), len(set(actual)))
        self.assertIsNone(cursor['timestamp'])

    def test_missing_or_zero_source_ids_fall_back_to_unique_snapshot_rowids(self):
        self.fixture()
        body = field(40800, field(45002, 1) + field(45101, 'duplicate-safe'))
        with closing(sqlite3.connect(self.source)) as conn:
            conn.executemany('INSERT INTO c2c_msg_table VALUES (?,?,?,?,?,?)', [
                (0, 'peer', 'peer', '10001', 300, body),
                (0, 'peer', 'peer', '10001', 200, body),
                ('', 'peer', 'peer', '10001', 100, body),
            ])
            conn.commit()

        rows = self.provider.messages(self.provider.conversations()[0], limit=10)
        ids = [row['id'] for row in rows]
        self.assertEqual(len(ids), 4)
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(ids, [
            'peer:db:c2c_msg_table:2',
            'peer:db:c2c_msg_table:3',
            'peer:db:c2c_msg_table:4',
            'peer:1',
        ])

    def test_qq_crc64_image_cache_path(self):
        self.fixture()
        conv = self.provider.conversations()[0]
        row = self.provider.messages(conv, limit=1)[0]
        payload = b'synthetic QQ image bytes'
        digest = hashlib.md5(payload).hexdigest()
        cache_name = _qq_image_cache_names(digest)[0]
        source = self.account / 'nt_qq' / 'nt_data' / 'Pic' / 'chatraw' / cache_name[-3:] / cache_name
        source.parent.mkdir(parents=True)
        source.write_bytes(payload)

        resolved = self.provider.media({**row, 'account_id': '10001', 'media': [{
            'kind': 'image', 'file_name': 'fixture.png', 'md5': digest, 'size': len(payload),
        }]})

        self.assertTrue(resolved['available'])
        self.assertEqual(Path(resolved['path']).read_bytes(), payload)

    def test_market_face_is_an_image_resource_and_resolves_local_cache(self):
        self.fixture()
        payload = b'synthetic QQ market-face image'
        digest = hashlib.md5(payload).hexdigest()
        cache_name = _qq_image_cache_names(digest)[0]
        source = self.account / 'nt_qq' / 'nt_data' / 'Pic' / 'chatraw' / cache_name[-3:] / cache_name
        source.parent.mkdir(parents=True)
        source.write_bytes(payload)
        element = (field(45002, 11) + field(80900, 'fixture-sticker.gif') +
                   field(45424, digest) + field(45405, len(payload)))
        rich, media = _decode(field(40800, element))

        self.assertEqual(rich, [{'type': 'market_face', 'data': {'name': 'fixture-sticker.gif'}}])
        self.assertEqual(media[0]['kind'], 'image')
        result = self.provider.media({'account_id': '10001', 'media': media})
        self.assertTrue(result['available'])
        self.assertEqual(Path(result['path']).read_bytes(), payload)

    def test_plain_snapshot_includes_committed_wal(self):
        self.fixture()
        conn = sqlite3.connect(self.source)
        try:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('INSERT INTO c2c_msg_table VALUES (2,"peer","peer","10002",200,?)', (field(40800, field(45002, 1) + field(45101, 'new in WAL')),))
            conn.commit()
            self.assertGreater(self.source.with_name('nt_msg.db-wal').stat().st_size, 32)
            rows = self.provider.messages(self.provider.conversations()[0])
            self.assertEqual([row['text'] for row in rows], ['new in WAL', 'synthetic text'])
        finally:
            conn.close()

    def encrypted_page(self, password='SyntheticKey1234', number=1):
        salt, iv = b'synthetic-salt!!', b'synthetic-iv!!!!'
        key, mac_key = _keys(password, salt)
        plaintext = b'\x00' * (PAGE - RESERVE - (16 if number == 1 else 0))
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        cipher = encryptor.update(plaintext) + encryptor.finalize()
        mac = hmac.new(mac_key, cipher + iv + struct.pack('<I', number), hashlib.sha1).digest()
        return (salt if number == 1 else b'') + cipher + iv + mac + b'\x00' * 12

    def test_encrypted_first_page_key_and_hmac_validation(self):
        password = 'SyntheticKey1234'
        page = self.encrypted_page(password)
        header = b'SQLite header 3\x00' + b'\x00' * 16 + b'QQ_NT DB'
        self.source.write_bytes(header.ljust(HEADER, b'\x00') + page)
        status = self.provider.status()
        self.assertFalse(status['available'])
        self.assertEqual(status['error_code'], 'key_required')
        self.config.write_text(json.dumps({'data_dir': str(self.account), 'key': password}))
        with patch.object(key_store, '_crypt', side_effect=lambda data, protect:
                          __import__('base64').b64encode(data) if protect else __import__('base64').b64decode(data)):
            self.assertTrue(self.provider.status()['available'])
            saved = json.loads(self.config.read_text(encoding='utf-8'))['key']
            self.assertTrue(saved.startswith('dpapi:v1:'))
            self.assertNotIn(password, saved)
        key, mac_key = _keys(password, page[:16])
        self.assertTrue(_decrypt_page(page, 1, key, mac_key).startswith(b'SQLite format 3\x00'))
        broken = page[:40] + bytes([page[40] ^ 1]) + page[41:]
        with self.assertRaises(LocalQQError):
            _decrypt_page(broken, 1, key, mac_key)
        self.config.write_text(json.dumps({'data_dir': str(self.account), 'key': 'IncorrectKey1234'}))
        self.assertEqual(self.provider.status()['error_code'], 'key_invalid')
        self.assertEqual(json.loads(self.config.read_text(encoding='utf-8'))['key'],
                         'IncorrectKey1234', 'an unverified legacy key must not be migrated')

    def test_foreign_user_ciphertext_does_not_fall_back_to_environment_key(self):
        self.fixture()
        self.config.write_text(json.dumps({'data_dir': str(self.account),
                                           'key': 'dpapi:v1:foreign-ciphertext'}))
        with patch.object(key_store, '_crypt', side_effect=key_store.QQKeyProtectionError('foreign user')), \
                patch.dict(os.environ, {'QQ_DB_KEY': 'SyntheticKey1234'}):
            status = self.provider.status()
        self.assertFalse(status['available'])
        self.assertEqual(status['error_code'], 'key_protection_unavailable')

    def test_media_rejects_other_accounts_and_path_traversal(self):
        self.fixture()
        self.provider.status()
        with self.assertRaises(LocalQQError):
            self.provider.media({'account_id': '10002', 'media': [{'file_name': 'fixture.png'}]})
        self.assertFalse(self.provider.media({'media': [{'file_name': '../secret.png'}]})['available'])

    def test_media_growth_after_size_check_does_not_replace_complete_cache(self):
        self.fixture()
        media = self.account / 'nt_qq' / 'nt_data' / 'chatraw' / 'year' / 'fixture.png'
        media.parent.mkdir(parents=True)
        media.write_bytes(b'first')
        cache_key = hashlib.sha256(('10001:' + str(media.resolve())).encode()).hexdigest()
        destination = self.root / 'cache' / 'media' / cache_key / media.name
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b'previous complete cache')
        original_open = Path.open

        def grow_between_stat_and_open(path, *args, **kwargs):
            if path == media and args and args[0] == 'rb':
                with original_open(path, 'ab') as writer:
                    writer.write(b' appended while QQ cache changed')
            return original_open(path, *args, **kwargs)

        row = {'account_id': '10001', 'media': [{'file_name': media.name}]}
        with patch.object(Path, 'open', grow_between_stat_and_open):
            result = self.provider.media(row)

        self.assertFalse(result['available'])
        self.assertEqual(destination.read_bytes(), b'previous complete cache')
        self.assertFalse(destination.with_name(destination.name + '.part').exists())

    def test_media_file_index_is_reused_and_invalidated_for_new_account_root(self):
        self.fixture()
        media = self.account / 'nt_qq' / 'nt_data' / 'chatraw' / 'year' / 'fixture.png'
        media.parent.mkdir(parents=True)
        media.write_bytes(b'account one')
        row = {'account_id': '10001', 'media': [{'file_name': 'fixture.png'}]}
        with patch('integrations.qq.local_provider.os.walk', wraps=os.walk) as walk:
            first = self.provider.media(row)
            self.assertTrue(first['available'])
            first_calls = walk.call_count
            second = self.provider.media(row)
            self.assertTrue(second['available'])
            self.assertEqual(walk.call_count, first_calls)

            other = self.root / '10002'
            other_media = other / 'nt_qq' / 'nt_data' / 'chatraw' / 'year' / 'fixture.png'
            other_media.parent.mkdir(parents=True)
            other_media.write_bytes(b'account two')
            self.provider.account_root = other
            self.provider.account_id = '10002'
            switched = self.provider.media({'account_id': '10002', 'media': row['media']})
            self.assertTrue(switched['available'])
            self.assertEqual(Path(switched['path']).read_bytes(), b'account two')
            self.assertGreater(walk.call_count, first_calls)

    def test_media_file_index_does_not_reuse_another_attachment_query(self):
        self.fixture()
        media_root = self.account / 'nt_qq' / 'nt_data' / 'chatraw' / 'year'
        media_root.mkdir(parents=True)
        (media_root / 'first.png').write_bytes(b'first')
        (media_root / 'second.png').write_bytes(b'second')
        first = {'account_id': '10001', 'media': [{'file_name': 'first.png'}]}
        second = {'account_id': '10001', 'media': [{'file_name': 'second.png'}]}
        with patch('integrations.qq.local_provider.os.walk', wraps=os.walk) as walk:
            self.assertEqual(Path(self.provider.media(first)['path']).read_bytes(), b'first')
            first_calls = walk.call_count
            self.assertEqual(Path(self.provider.media(second)['path']).read_bytes(), b'second')
            self.assertGreater(walk.call_count, first_calls)

    def test_media_index_invalidates_when_cache_directory_changes(self):
        self.fixture()
        media_root = self.account / 'nt_qq' / 'nt_data' / 'chatraw'
        media = media_root / 'year' / 'fixture.png'
        media.parent.mkdir(parents=True)
        media.write_bytes(b'first')
        row = {'account_id': '10001', 'media': [{'file_name': 'fixture.png'}]}
        self.assertTrue(self.provider.media(row)['available'])
        media.unlink()
        new_media = media_root / 'later' / 'fixture.png'
        new_media.parent.mkdir(parents=True)
        new_media.write_bytes(b'second')
        result = self.provider.media(row)
        self.assertTrue(result['available'])
        self.assertEqual(Path(result['path']).read_bytes(), b'second')

    def test_media_index_keeps_symlink_files_outside_cache_unavailable(self):
        self.fixture()
        media_root = self.account / 'nt_qq' / 'nt_data' / 'chatraw' / 'year'
        media_root.mkdir(parents=True)
        outside = self.root / 'outside.png'
        outside.write_bytes(b'outside')
        try:
            (media_root / 'fixture.png').symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'symlink unavailable: {exc}')
        result = self.provider.media({'account_id': '10001', 'media': [{'file_name': 'fixture.png'}]})
        self.assertFalse(result['available'])
        self.assertFalse((self.provider.cache_dir / 'media').exists())

    def test_media_rejects_nt_data_symlink_outside_selected_account(self):
        self.fixture()
        outside = self.root / 'outside-data'
        media = outside / 'chatraw' / 'year' / 'fixture.png'
        media.parent.mkdir(parents=True)
        media.write_bytes(b'outside account data')
        try:
            (self.account / 'nt_qq' / 'nt_data').symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'symlink unavailable: {exc}')

        result = self.provider.media({'account_id': '10001', 'media': [{'file_name': 'fixture.png'}]})

        self.assertFalse(result['available'])
        self.assertFalse((self.provider.cache_dir / 'media').exists())

    def test_media_rejects_symlinked_descendant_outside_selected_account(self):
        self.fixture()
        outside = self.root / 'outside-media'
        outside.mkdir()
        (outside / 'fixture.png').write_bytes(b'outside account data')
        media_root = self.account / 'nt_qq' / 'nt_data' / 'chatraw'
        media_root.mkdir(parents=True)
        try:
            (media_root / 'year').symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'symlink unavailable: {exc}')

        result = self.provider.media({'account_id': '10001', 'media': [{'file_name': 'fixture.png'}]})

        self.assertFalse(result['available'])
        self.assertFalse((self.provider.cache_dir / 'media').exists())

    def test_media_index_scan_budget_boundary_and_exhaustion(self):
        self.fixture()
        media_root = self.account / 'nt_qq' / 'nt_data' / 'chatraw' / 'year'
        media_root.mkdir(parents=True)
        (media_root / 'fixture.png').write_bytes(b'fixture')
        row = {'account_id': '10001', 'media': [{'file_name': 'fixture.png'}]}
        with patch('integrations.qq.local_provider.MEDIA_SCAN_LIMIT', 2):
            self.assertTrue(self.provider.media(row)['available'])
        self.provider._media_index = None
        with patch('integrations.qq.local_provider.MEDIA_SCAN_LIMIT', 1):
            limited = self.provider.media(row)
        self.assertFalse(limited['available'])
        self.assertIn('扫描达到上限', limited['reason'])

    def test_media_scans_category_cache_before_large_chatraw_tree(self):
        self.fixture()
        data_root = self.account / 'nt_qq' / 'nt_data'
        category_root = data_root / 'Pic'
        category_root.mkdir(parents=True)
        target = category_root / 'fixture.png'
        target.write_bytes(b'cached category image')
        broad_root = data_root / 'chatraw' / 'year'
        broad_root.mkdir(parents=True)
        (broad_root / 'one.bin').write_bytes(b'one')
        (broad_root / 'two.bin').write_bytes(b'two')

        with patch('integrations.qq.local_provider.MEDIA_SCAN_LIMIT', 1):
            result = self.provider.media({'account_id': '10001', 'media': [{
                'kind': 'image', 'file_name': target.name,
            }]})

        self.assertTrue(result['available'])
        self.assertEqual(Path(result['path']).read_bytes(), target.read_bytes())

    def test_configuration_keeps_printable_key_spaces(self):
        password = 'Synthetic Key123'
        page = self.encrypted_page(password)
        self.source.write_bytes((b'SQLite header 3\x00' + b'QQ_NT DB').ljust(HEADER, b'\x00') + page)
        self.config.write_text(json.dumps({'data_dir': str(self.account), 'key': password}))
        self.assertTrue(self.provider.status()['available'])

    def test_parser_keeps_unknown_and_does_not_duplicate_quotes(self):
        quote = field(45002, 7) + field(47423, field(45002, 1) + field(45101, 'quoted'))
        body = field(40800, quote) + field(40800, field(45002, 1) + field(45101, 'reply'))
        rich, media = _decode(body)
        self.assertEqual(rich[0]['type'], 'reply')
        self.assertEqual(rich[0]['data']['content'], 'quoted')
        self.assertEqual(rich[1]['data']['text'], 'reply')
        self.assertEqual(media, [])
        self.assertEqual(_decode(b'\xff')[0][0]['type'], 'unknown')

    def test_reply_preview_keeps_nested_attachment_for_local_cache_resolution(self):
        digest = hashlib.md5(b'image').digest()
        quote = (field(45002, 7) + field(47423, field(45002, 2) +
                 field(45402, 'quoted.png') + field(45405, 5) + field(45424, digest)))
        rich, media = _decode(field(40800, quote))
        self.assertEqual(media, [{'kind': 'image', 'type': 'image', 'file_name': 'quoted.png',
                                  'name': 'quoted.png', 'size': 5, 'md5': digest.hex(),
                                  'quoted': True}])
        self.assertEqual(rich[0]['data']['resources'], [
            {'resource_index': 0, 'type': 'image', 'name': 'quoted.png', 'size': 5}])

        self.fixture()
        self.provider.status()
        cached = self.account / 'nt_qq' / 'nt_data' / 'chatraw' / 'quoted.png'
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(b'image')
        result = self.provider.media({'account_id': '10001', 'media': media}, resource_index=0)
        self.assertTrue(result['available'])
        self.assertEqual(Path(result['path']).read_bytes(), b'image')

    def test_parser_preserves_attachment_at_index_100(self):
        body = b''.join(
            field(40800, field(45002, 2) + field(45402, f'{index}.png'))
            for index in range(101)
        )

        _, media = _decode(body)

        self.assertEqual(len(media), 101)
        self.assertEqual(media[100]['file_name'], '100.png')
        self.fixture()
        self.provider.status()
        cached = self.account / 'nt_qq' / 'nt_data' / 'chatraw' / 'year' / '100.png'
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b'synthetic high-index attachment')

        result = self.provider.media(
            {'account_id': '10001', 'media': media}, resource_index=100)

        self.assertTrue(result['available'])
        self.assertEqual(Path(result['path']).read_bytes(), cached.read_bytes())

    def test_group_peer_fallback_uses_owned_snapshot_index(self):
        self.fixture()
        with closing(sqlite3.connect(self.source)) as conn:
            conn.execute('CREATE TABLE group_msg_table ("40001" INTEGER,"40021" TEXT,"40027" TEXT,"40030" TEXT,"40050" INTEGER,"40800" BLOB)')
            for number, primary, secondary, last in ((1, 'group-a', 'wrong', 'wrong'), (2, '0', 'group-b', 'wrong'), (3, '', '', 'group-c')):
                conn.execute('INSERT INTO group_msg_table VALUES (?,?,?,?,100,?)', (number, primary, secondary, last, field(40800, field(45002, 1) + field(45101, 'fixture'))))
            conn.commit()
        original = self.source.read_bytes()
        groups = [item for item in self.provider.conversations() if item['kind'] == 'group']
        self.assertEqual({item['id'] for item in groups}, {'group-a', 'group-b', 'group-c'})
        for group in groups:
            self.assertEqual(len(self.provider.messages(group)), 1)
        table, _, peer = self.provider._schemas['group']
        plan = self.provider._conn.execute(f'EXPLAIN QUERY PLAN SELECT * FROM "{table}" WHERE {peer}=? ORDER BY "40050" DESC', ('group-a',)).fetchall()
        self.assertIn('hub_qq_group_peer_time', str([tuple(row) for row in plan]))
        self.assertEqual(self.source.read_bytes(), original)

    def test_media_hash_fallback_and_repeated_quote_elements(self):
        digest = bytes.fromhex('ab' * 16)
        for number in (45424, 45406, 45407, 45408):
            body = field(40800, field(45002, 2) + field(number, digest if number == 45424 else digest.hex()))
            self.assertEqual(_decode(body)[1][0]['md5'], digest.hex())
        quote = field(45002, 7) + field(47423, field(45002, 1) + field(45101, 'first')) + field(47423, field(45002, 2)) + field(47423, field(45002, 1) + field(45101, 'last'))
        self.assertEqual(_decode(field(40800, quote))[0][0]['data']['content'], 'first[图片]last')

    def test_avatar_reads_hashed_local_cache_only(self):
        self.fixture()
        self.provider.status()
        identity = 'synthetic-peer'
        md5 = lambda value: hashlib.md5(value.encode()).hexdigest()
        digest = md5(md5(md5(identity) + identity) + identity)
        path = self.account / 'nt_qq' / 'nt_data' / 'avatar' / 'user' / digest[:2] / ('b_' + digest)
        path.parent.mkdir(parents=True)
        payload = b'\x89PNG\r\n\x1a\nfixture'
        path.write_bytes(payload)
        self.assertEqual(self.provider.avatar_buffers([{'id': identity}]), {identity: payload})
        url = self.provider.local_avatar_url(identity)
        self.assertTrue(url.startswith('hub-avatar://qq/'))
        self.assertNotIn(identity, url)

    def test_send_target_maps_local_uid_through_uid_mapping_table(self):
        self.fixture()
        with closing(sqlite3.connect(self.source)) as conn:
            conn.execute('CREATE TABLE nt_uid_mapping_table ("48902" TEXT,"1002" TEXT)')
            conn.execute('INSERT INTO nt_uid_mapping_table VALUES (?,?)', ('peer', '2747277822'))
            conn.commit()
        self.assertEqual(self.provider.send_target('peer', 'private'),
                         {'conversation_id': '2747277822'})
        self.assertEqual(self.provider.contacts()[0].get('uin'), '2747277822')

    def test_close_clears_account_metadata_before_reopening_another_account(self):
        self.fixture()
        with closing(sqlite3.connect(self.source.parent / 'profile_info.db')) as conn:
            conn.execute('CREATE TABLE profile_info_v6 ("1000" TEXT,"20002" TEXT,"20009" TEXT,"1002" TEXT)')
            conn.execute('INSERT INTO profile_info_v6 VALUES (?,?,?,?)',
                         ('peer', 'Old contact', 'Old account label', '10002'))
            conn.commit()
        self.assertEqual(self.provider.conversations()[0]['title'], 'Old account label')
        self.assertEqual(self.provider.send_target('peer'), {'conversation_id': '10002'})

        other = self.root / '10002'
        source = other / 'nt_qq' / 'nt_db' / 'nt_msg.db'
        source.parent.mkdir(parents=True)
        with closing(sqlite3.connect(source)) as conn:
            conn.execute('CREATE TABLE c2c_msg_table ("40001" INTEGER,"40021" TEXT,"40020" TEXT,"40033" TEXT,"40050" INTEGER,"40800" BLOB)')
            conn.execute('INSERT INTO c2c_msg_table VALUES (1,"peer","other","10002",100,?)',
                         (field(40800, field(45002, 1) + field(45101, 'second account')),))
            conn.commit()
        self.config.write_text(json.dumps({'data_dir': str(other)}))
        self.provider.close()

        self.assertEqual(self.provider.conversations()[0]['title'], 'peer')
        with self.assertRaises(LocalQQError) as failure:
            self.provider.send_target('peer')
        self.assertEqual(failure.exception.code, 'send_target_private_unresolved')

    def test_send_target_uses_numeric_peer_ids_directly(self):
        self.fixture()
        self.assertEqual(self.provider.send_target('1105185565', 'group'),
                         {'conversation_id': '1105185565'})
        self.assertEqual(self.provider.send_target('2747277822', 'private'),
                         {'conversation_id': '2747277822'})

    def test_send_target_refuses_unresolvable_local_identities(self):
        self.fixture()
        for identity, kind, code in (('u_unknown', 'private', 'send_target_private_unresolved'),
                                     ('u_group', 'group', 'send_target_group_unresolved'),
                                     ('', 'private', 'send_target_missing'),
                                     ('123456', 'channel', 'send_target_kind_unsupported')):
            with self.subTest(identity=identity, kind=kind):
                with self.assertRaises(LocalQQError) as failure:
                    self.provider.send_target(identity, kind)
                self.assertEqual(failure.exception.code, code)

    def test_send_target_ignores_mapping_values_that_are_not_uins(self):
        self.fixture()
        with closing(sqlite3.connect(self.source)) as conn:
            conn.execute('CREATE TABLE nt_uid_mapping_table ("48902" TEXT,"1002" TEXT)')
            conn.executemany('INSERT INTO nt_uid_mapping_table VALUES (?,?)',
                             (('peer', '12345'), ('peer2', '')))
            conn.commit()
        with self.assertRaises(LocalQQError) as failure:
            self.provider.send_target('peer2', 'private')
        self.assertEqual(failure.exception.code, 'send_target_private_unresolved')
        self.assertEqual(self.provider.avatar_buffers([{'id': '../secret'}]), {})


if __name__ == '__main__':
    unittest.main()
