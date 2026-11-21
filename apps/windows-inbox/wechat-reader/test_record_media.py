from base64 import b64decode
import hashlib
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from xml.etree.ElementTree import fromstring

from file_cache import FileCache
from message_render import render_message
from record_media import RecordMedia
from vendor.media import MediaDownloader


PNG = b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRuoAAAAASUVORK5CYII=')


class RecordMediaTest(unittest.TestCase):
    def test_record_cache_appears_late_and_only_matches_verified_thumbnail(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            account, private = root / 'account', root / 'wechat-reading'
            private.mkdir()
            folder = account / 'cache' / '2026-11' / 'Message' / 'group' / 'RecTmp' / 'record' / 'Images'
            folder.mkdir(parents=True)
            media = SimpleNamespace(decrypt_image=lambda filename: Path(filename).read_bytes()[5:],
                                    _image_complete=MediaDownloader._image_complete)
            reader = SimpleNamespace(account_dir=str(account), _media=media)
            files = FileCache(account, root / 'attachments', {})
            checksum = hashlib.md5(PNG).hexdigest()
            item = fromstring(f'<dataitem><thumbsize>{len(PNG)}</thumbsize><thumbfullmd5>{checksum}</thumbfullmd5></dataitem>')
            def resolve(node):
                return RecordMedia(reader, private, {}, files).resolve('parent', 1, '2', node, (0,), '')['image']
            self.assertEqual(resolve(item), {'status': 'pending'})
            (folder / '1_preview').write_bytes(b'cache' + PNG)
            bad = fromstring(f'<dataitem><thumbsize>{len(PNG)}</thumbsize><thumbfullmd5>{"1" * 32}</thumbfullmd5></dataitem>')
            self.assertEqual(resolve(bad), {'status': 'pending'})
            result = resolve(item)
            self.assertEqual(result['quality'], 'preview')
            self.assertEqual((root / 'attachments' / result['id'] / 'content' / 'image.png').read_bytes(), PNG)

    def test_nested_record_and_late_link_show_user_fields_only(self):
        start = '<appmsg><type>19</type><title>转发</title><recorditem><recordinfo><datalist>'
        plain = '<dataitem datatype="1"><datadesc>正文</datadesc></dataitem>'
        nested = '<dataitem datatype="17"><recordxml><recordinfo><datalist><dataitem datatype="5"><weburlitem><url>https://example.org/item</url><title>网页</title></weburlitem><cdnthumbkey>transport-secret</cdnthumbkey></dataitem></datalist></recordinfo></recordxml></dataitem>'
        payload = render_message({'local_type': (19 << 32) | 49, 'content': start + plain * 60 + nested + '</datalist></recordinfo></recorditem></appmsg>'})
        items = payload['ChatRecord']['entries']
        self.assertEqual(len(items), 61)
        self.assertEqual(items[60]['record']['entries'][0]['url'], 'https://example.org/item')
        self.assertNotIn('transport-secret', str(payload))
