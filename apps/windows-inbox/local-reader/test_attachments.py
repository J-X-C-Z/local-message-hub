import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).parent / 'vendor'))
from attachments import AttachmentSync


def varint(value):
    output = bytearray()
    while value > 127:
        output.append((value & 127) | 128); value >>= 7
    output.append(value)
    return bytes(output)


def row(key, name, size):
    def text(number, value):
        data = value.encode()
        return varint(number * 8 + 2) + varint(len(data)) + data
    return {'type_code': 15, '_raw': text(1, key) + text(2, name) + varint(4 * 8) + varint(size)}


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / 'account' / 'Data'; self.profile.mkdir(parents=True)
        self.private = self.root / 'inbox' / 'local-reading'; self.private.mkdir(parents=True)
        mapping = self.profile.parent / 'CacheMapping'; mapping.mkdir()
        self.mapping = mapping / 'cache.db'
        conn = sqlite3.connect(self.mapping); conn.execute('CREATE TABLE mapping(type INTEGER,key TEXT,file_name TEXT)'); conn.commit(); conn.close()

    def add(self, key, filename, data):
        target = self.root / filename; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        conn = sqlite3.connect(self.mapping); conn.execute('INSERT INTO mapping VALUES(1,?,?)', (key, str(target))); conn.commit(); conn.close()
        return target

    def sync(self):
        return AttachmentSync(self.profile, self.private, lambda p: Path(p).read_bytes(), [self.root])

    def test_exact_keys_keep_same_named_documents_separate(self):
        self.add('resource-a', 'first/file.pdf', b'%PDF-1.7 first')
        self.add('resource-b', 'second/file.pdf', b'%PDF-1.7 other')
        a = self.sync().attach(row('resource-a', 'file.pdf', 14))
        b = self.sync().attach(row('resource-b', 'file.pdf', 14))
        self.assertEqual(a['status'], 'ready'); self.assertEqual(b['status'], 'ready')
        self.assertNotEqual(a['id'], b['id'])
        for item, expected in [(a, b'%PDF-1.7 first'), (b, b'%PDF-1.7 other')]:
            folder = self.private.parent / 'attachments' / item['id']
            self.assertEqual((folder / 'content' / 'file.pdf').read_bytes(), expected)
            self.assertNotIn('resource-', (folder / 'manifest.json').read_text())

    def test_large_native_attachment_stays_at_source_until_offline_mode(self):
        size = 8 * 1024 * 1024
        source = self.add('large', 'account/Cache/File/big.pdf', b'0' * size)
        message = row('large', source.name, size)
        item = self.sync().attach(message)
        self.assertEqual(item['status'], 'on_demand')
        folder = self.private.parent / 'attachments' / item['id']
        self.assertFalse((folder / 'content').exists())
        source_ref = json.loads((folder / 'manifest.json').read_text('utf-8'))['source']
        self.assertEqual(source_ref['relative'], source.name)
        (self.private.parent / 'storage-preferences.json').write_text('{"attachmentMode":"offline"}', encoding='utf-8')
        self.assertEqual(self.sync().attach(message)['status'], 'ready')
        self.assertEqual((folder / 'content' / source.name).stat().st_size, size)

    def test_pending_download_becomes_ready_and_unchanged_copy_is_reused(self):
        message = row('late', 'file.pdf', 3)
        self.assertEqual(self.sync().attach(message)['status'], 'pending')
        self.add('late', 'download/file.pdf', b'abc')
        item = self.sync().attach(message); self.assertEqual(item['status'], 'ready')
        manifest = self.private.parent / 'attachments' / item['id'] / 'manifest.json'
        modified = manifest.stat().st_mtime_ns
        self.assertEqual(self.sync().attach(message)['status'], 'ready')
        self.assertEqual(manifest.stat().st_mtime_ns, modified)
        (self.root / 'download/file.pdf').unlink()
        self.assertEqual(self.sync().attach(message)['status'], 'ready')

    def test_different_key_with_same_filename_is_not_a_match(self):
        self.add('one', 'download/file.pdf', b'abc')
        self.assertEqual(self.sync().attach(row('two', 'file.pdf', 3))['status'], 'pending')

    def test_partial_download_and_paths_outside_scope_are_not_copied(self):
        source = self.add('partial', 'download/file.pdf', b'a')
        self.assertEqual(self.sync().attach(row('partial', 'file.pdf', 3))['status'], 'incomplete')
        scoped = AttachmentSync(self.profile, self.private, lambda p: Path(p).read_bytes(), [self.profile])
        self.assertEqual(scoped.attach(row('partial', 'file.pdf', 1))['status'], 'pending')

    def test_filename_cannot_escape_attachment_directory(self):
        self.add('escape', 'download/file.pdf', b'abc')
        item = self.sync().attach(row('escape', '../manifest.json', 3))
        self.assertEqual(item['status'], 'ready')
        self.assertNotIn('/', item['name'])
        folder = self.private.parent / 'attachments' / item['id']
        self.assertEqual(json.loads((folder / 'manifest.json').read_text())['version'], 1)


if __name__ == '__main__':
    unittest.main()
