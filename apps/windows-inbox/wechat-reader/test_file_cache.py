import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from file_cache import FileCache
from message_render import render_message


class FileCacheTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.account = Path(directory.name) / 'account'
        self.output = Path(directory.name) / 'attachments'
        self.month = self.account / 'msg' / 'file' / '2026-11'
        self.month.mkdir(parents=True)
        self.created = int(time.mktime((2026, 11, 15, 12, 0, 0, 0, 0, -1)))
        self.identifier = 'a' * 64
        self.hashes = {}

    def description(self, name, body, with_digest=True):
        digest = hashlib.md5(body).hexdigest() if with_digest else ''
        xml = f'<msg><appmsg><title>{name}</title><type>6</type><appattach><totallen>{len(body)}</totallen></appattach><md5>{digest}</md5></appmsg></msg>'
        message = render_message({'local_type': (6 << 32) | 49, 'content': xml})
        self.assertEqual(message['Content'], '[文件] ' + name)
        return message['_file']

    def resolve(self, description, offline=False):
        return FileCache(self.account, self.output, self.hashes, offline).resolve(self.identifier, description, self.created)

    def test_pdf_is_referenced_without_copy_and_becomes_pending_after_removal(self):
        data = b'%PDF-1.4\nfixture'
        source = self.month / '通知.pdf'
        description = self.description(source.name, data)
        self.assertEqual(self.resolve(description)['status'], 'pending')
        source.write_bytes(data)
        self.assertEqual(self.resolve(description)['status'], 'on_demand')
        folder = self.output / self.identifier
        manifest = json.loads((folder / 'manifest.json').read_text('utf-8'))
        self.assertEqual(manifest['source']['relative'], 'msg/file/2026-11/通知.pdf')
        self.assertFalse((folder / 'content').exists())
        source.unlink()
        self.assertEqual(self.resolve(description)['status'], 'pending')

    def test_wrong_same_size_file_is_rejected_and_renamed_mp3_requires_matching_hash(self):
        data = b'ID3 original fixture'
        source = self.month / '原文件.mp3'
        source.write_bytes(b'x' * len(data))
        description = self.description(source.name, data)
        self.assertEqual(self.resolve(description)['status'], 'incomplete')
        earlier = self.month.parent / '2026-10' / '原文件(1).mp3'
        earlier.parent.mkdir()
        earlier.write_bytes(data)
        source.unlink()
        self.assertEqual(self.resolve(self.description(source.name, data, False))['status'], 'pending')
        self.assertEqual(self.resolve(description, offline=True)['status'], 'ready')
        earlier.unlink()
        self.assertEqual(self.resolve(description)['status'], 'ready')
        self.assertEqual((self.output / self.identifier / 'content' / source.name).read_bytes(), data)

    def test_legacy_copy_retains_original_save_name(self):
        data = b'%PDF-1.4\nfixture'
        description = self.description('report.pdf', data)
        folder = self.output / self.identifier
        content = folder / 'content'
        content.mkdir(parents=True)
        (content / 'old-report.pdf').write_bytes(data)
        manifest = folder / 'manifest.json'
        manifest.write_text(json.dumps({'version': 1, 'id': self.identifier, 'name': 'old-report.pdf', 'size': len(data)}), 'utf-8')
        self.assertEqual(self.resolve(description)['status'], 'ready')
        record = json.loads(manifest.read_text('utf-8'))
        self.assertEqual(record['display_name'], 'report.pdf')
        self.assertEqual(record['name'], 'old-report.pdf')
