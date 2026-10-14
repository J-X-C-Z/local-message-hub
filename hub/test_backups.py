import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from hub.backups import create_media_backup, restore_media_backup
from hub.store import ChatStore
from integrations.wechat.provider import media_cache_identity


class MediaBackupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.storage = self.root / 'source'
        self.storage.mkdir()
        self.store = ChatStore(self.storage / 'hub.sqlite3')
        self.job_id = 'a' * 20
        self.relative = f'qq-roaming/{self.job_id}/photo.png'
        self.media = self.storage / self.relative
        self.media.parent.mkdir(parents=True)
        self.media.write_bytes(b'local qq image bytes')
        (self.storage / 'qq-roaming' / self.job_id / 'chat.json').write_text(
            '{"message":"must not be bundled"}', encoding='utf-8')
        (self.storage / 'qq-roaming' / self.job_id / 'unreferenced.bin').write_bytes(b'ignore')
        self.store.ingest('qq', 'fixture-account', [{'id': 'room', 'title': 'Fixture'}], [{
            'id': 'message-1', 'conversation_id': 'room', 'timestamp': 1,
            'type': 'image', 'text': 'private body excluded from media manifest',
            'roaming_resources': [{'type': 'image', 'name': 'photo.png', 'path': self.relative}],
        }])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_bundle_contains_only_referenced_hub_media_and_restores(self):
        bundle = self.root / 'media-backup.zip'
        created = create_media_backup(self.store, self.storage, bundle)
        self.assertEqual(created['media_count'], 1)
        with ZipFile(bundle) as archive:
            names = set(archive.namelist())
            self.assertEqual(names, {'index.sqlite3', 'manifest.json', f'media/{self.relative}'})
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['media_count'], 1)
            self.assertNotIn('private body', archive.read('manifest.json').decode())

        restored_storage = self.root / 'restored'
        restored_storage.mkdir()
        restored_store = ChatStore(restored_storage / 'hub.sqlite3')
        try:
            result = restore_media_backup(restored_store, restored_storage, bundle)
            self.assertEqual(result['media_count'], 1)
            self.assertEqual(restored_store.count(), 1)
            self.assertEqual((restored_storage / self.relative).read_bytes(), b'local qq image bytes')
        finally:
            restored_store.close()

    def test_bundle_includes_referenced_qq_media_alias_but_no_unreferenced_files(self):
        relative = f'qq-roaming/{self.job_id}/alias-photo.png'
        (self.storage / relative).write_bytes(b'local qq alias image bytes')
        (self.storage / f'qq-roaming/{self.job_id}/media-only-orphan.png').write_bytes(b'orphan')
        self.store.ingest('qq', 'fixture-account', [{'id': 'room', 'title': 'Fixture'}], [{
            'id': 'message-media-alias', 'conversation_id': 'room', 'timestamp': 2,
            'type': 'text', 'media': [{'type': 'image', 'name': 'alias-photo.png',
                                       'path': relative}],
        }])

        bundle = self.root / 'media-alias-backup.zip'
        created = create_media_backup(self.store, self.storage, bundle)
        self.assertEqual(created['media_count'], 2)
        with ZipFile(bundle) as archive:
            media_entries = {name for name in archive.namelist() if name.startswith('media/')}
        self.assertEqual(media_entries, {f'media/{self.relative}', f'media/{relative}'})

    def test_corrupt_bundle_is_rejected_before_index_or_media_changes(self):
        bundle = self.root / 'media-backup.zip'
        create_media_backup(self.store, self.storage, bundle)
        corrupt = self.root / 'corrupt.zip'
        with ZipFile(bundle) as source, ZipFile(corrupt, 'w', ZIP_DEFLATED) as target:
            for entry in source.infolist():
                data = source.read(entry.filename)
                if entry.filename.startswith('media/'):
                    data = b'tampered image'
                target.writestr(entry.filename, data)

        restored_storage = self.root / 'restored'
        restored_storage.mkdir()
        restored_store = ChatStore(restored_storage / 'hub.sqlite3')
        try:
            with self.assertRaises(ValueError):
                restore_media_backup(restored_store, restored_storage, corrupt)
            self.assertEqual(restored_store.count(), 0)
            self.assertFalse((restored_storage / self.relative).exists())
        finally:
            restored_store.close()

    def test_oversized_manifest_is_rejected_before_restore_changes(self):
        bundle = self.root / 'oversized-manifest.zip'
        with ZipFile(bundle, 'w') as archive:
            archive.writestr('index.sqlite3', b'not an index')
            archive.writestr('manifest.json', b'{"x":"this manifest is over the test limit"}')

        restored_storage = self.root / 'oversized-manifest-target'
        restored_storage.mkdir()
        restored_store = ChatStore(restored_storage / 'hub.sqlite3')
        try:
            with patch('hub.backups.MAX_MANIFEST_BYTES', 8):
                with self.assertRaisesRegex(ValueError, '清单超过'):
                    restore_media_backup(restored_store, restored_storage, bundle)
            self.assertEqual(restored_store.count(), 0)
            self.assertFalse((restored_storage / self.relative).exists())
            self.assertEqual(list(restored_storage.glob('.restore-media-*')), [])
        finally:
            restored_store.close()

    def test_different_existing_media_is_not_overwritten(self):
        bundle = self.root / 'media-backup.zip'
        create_media_backup(self.store, self.storage, bundle)
        restored_storage = self.root / 'restored'
        conflict = restored_storage / self.relative
        conflict.parent.mkdir(parents=True)
        conflict.write_bytes(b'keep local file')
        restored_store = ChatStore(restored_storage / 'hub.sqlite3')
        try:
            with self.assertRaisesRegex(ValueError, '冲突'):
                restore_media_backup(restored_store, restored_storage, bundle)
            self.assertEqual(restored_store.count(), 0)
            self.assertEqual(conflict.read_bytes(), b'keep local file')
        finally:
            restored_store.close()

    def test_unreferenced_media_is_rejected_even_with_valid_manifest_digest(self):
        bundle = self.root / 'media-backup.zip'
        create_media_backup(self.store, self.storage, bundle)
        forged = self.root / 'forged.zip'
        fake_relative = f'qq-roaming/{"b" * 20}/unreferenced.png'
        fake_path = f'media/{fake_relative}'
        with ZipFile(bundle) as source, ZipFile(forged, 'w', ZIP_DEFLATED) as target:
            manifest = json.loads(source.read('manifest.json'))
            payload = b'not referenced by the bundled index'
            manifest['media'][0].update({
                'path': fake_path,
                'size': len(payload),
                'sha256': hashlib.sha256(payload).hexdigest(),
            })
            manifest['media_bytes'] = len(payload)
            for entry in source.infolist():
                if entry.filename.startswith('media/'):
                    continue
                data = (json.dumps(manifest).encode() if entry.filename == 'manifest.json'
                        else source.read(entry.filename))
                target.writestr(entry.filename, data)
            target.writestr(fake_path, payload)

        restored_storage = self.root / 'restored'
        restored_storage.mkdir()
        restored_store = ChatStore(restored_storage / 'hub.sqlite3')
        try:
            with self.assertRaisesRegex(ValueError, '引用不匹配'):
                restore_media_backup(restored_store, restored_storage, forged)
            self.assertEqual(restored_store.count(), 0)
            self.assertFalse((restored_storage / fake_relative).exists())
        finally:
            restored_store.close()

    def test_bundle_includes_only_account_scoped_indexed_wechat_cache(self):
        account, session, shard, local_id = 'wx-account', 'room-1', 'MSG0.db', 42
        identity = media_cache_identity(account, session, shard, local_id)
        self.assertNotEqual(identity, media_cache_identity('other-account', session, shard, local_id))
        cache_root = self.root / 'wechat-cache'
        cache_dir = cache_root / identity
        cache_dir.mkdir(parents=True)
        (cache_dir / 'mid.jpg').write_bytes(b'wechat image')
        (cache_root / ('f' * 64)).mkdir()
        (cache_root / ('f' * 64) / 'orphan.jpg').write_bytes(b'orphan')
        self.store.ingest('wechat', account, [{'id': session, 'title': 'Fixture'}], [{
            'id': f'{session}:{shard}:{local_id}', 'conversation_id': session,
            'timestamp': 2, 'type': 'image', 'text': 'private body excluded',
            'raw_type': '3', 'session_id': session, 'shard': shard,
            'local_id': local_id, 'local_type': 3,
        }])

        bundle = self.root / 'wechat-backup.zip'
        created = create_media_backup(self.store, self.storage, bundle, cache_root)
        self.assertEqual(created['media_count'], 2)
        self.assertEqual(created['missing_media_count'], 0)
        with ZipFile(bundle) as archive:
            self.assertEqual(set(archive.namelist()), {
                'index.sqlite3', 'manifest.json', f'media/wechat-media/{identity}/mid.jpg',
                f'media/qq-roaming/{self.job_id}/photo.png'})
            manifest = archive.read('manifest.json').decode()
            self.assertNotIn('private body', manifest)
            self.assertNotIn(account, manifest)

        restored_storage = self.root / 'restored-wechat'
        restored_storage.mkdir()
        restored_cache = restored_storage / 'wechat-cache'
        restored_store = ChatStore(restored_storage / 'hub.sqlite3')
        try:
            restore_media_backup(restored_store, restored_storage, bundle, restored_cache)
            self.assertEqual((restored_cache / identity / 'mid.jpg').read_bytes(), b'wechat image')
            self.assertFalse((restored_cache / ('f' * 64) / 'orphan.jpg').exists())
        finally:
            restored_store.close()


if __name__ == '__main__':
    unittest.main()
