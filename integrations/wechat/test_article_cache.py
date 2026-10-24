import gzip
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from integrations.wechat import article_cache as cache


class ArticleCacheTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.url = 'https://mp.weixin.qq.com/s?__biz=fixture&mid=123&idx=1&sn=abc'
        self.html = '<meta property="og:title" content="文章"><div id="js_content">真实正文</div>'
        self.patch = patch.object(cache, '_cache_roots', return_value=[self.root])
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def fixture(self, key=None, body=None, encoding=b'gzip', long_key=True):
        key = (key or self.url).encode()
        raw = bytearray(8192 + 256)
        struct.pack_into('<I', raw, 0, cache._MAGIC)
        struct.pack_into('<I', raw, 12, 256)
        struct.pack_into('<iI', raw, 8192 + 32, len(key), 0x80000001 if long_key else 0)
        if long_key:
            (self.root / 'f_000001').write_bytes(key)
        else:
            raw[8192 + 96:8192 + 96 + len(key)] = key
        headers = b'HTTP/1.1 200 OK\x00content-type:text/html; charset=utf-8\x00content-encoding:' + encoding + b'\x00'
        body = body if body is not None else gzip.compress(self.html.encode())
        struct.pack_into('<4i', raw, 8192 + 40, len(headers), len(body), 0, 0)
        struct.pack_into('<4I', raw, 8192 + 56, 0x80000002, 0x80000003, 0, 0)
        (self.root / 'f_000002').write_bytes(headers)
        (self.root / 'f_000003').write_bytes(body)
        (self.root / 'data_1').write_bytes(raw)

    def test_exact_article_ignores_private_url_parameters(self):
        self.fixture(self.url + '&uin=private&key=secret&pass_ticket=secret')
        self.assertEqual(cache.read_cached_article_html(self.url + '&scene=1'), self.html)
        self.assertIsNone(cache.read_cached_article_html(self.url.replace('mid=123', 'mid=124')))

    def test_short_inline_key_and_identity_body(self):
        self.fixture(body=self.html.encode(), encoding=b'identity', long_key=False)
        self.assertEqual(cache.read_cached_article_html(self.url), self.html)

    def test_gzip_whitespace_and_truncation(self):
        self.fixture(encoding=b' gzip')
        self.assertEqual(cache.read_cached_article_html(self.url), self.html)
        compressed = gzip.compress(self.html.encode())
        self.fixture(body=compressed[:-3])
        self.assertIsNone(cache.read_cached_article_html(self.url))

    def test_decompression_bound_and_unsupported_encoding(self):
        self.fixture(body=gzip.compress(b'x' * (cache.MAX_HTML_BYTES + 1)))
        self.assertIsNone(cache.read_cached_article_html(self.url))
        self.fixture(encoding=b'br')
        self.assertIsNone(cache.read_cached_article_html(self.url))

    def test_corrupt_address_and_short_file_rejected(self):
        self.fixture()
        raw = bytearray((self.root / 'data_1').read_bytes())
        struct.pack_into('<I', raw, 8192 + 60, 0xa000ffff)
        (self.root / 'data_1').write_bytes(raw)
        self.assertIsNone(cache.read_cached_article_html(self.url))
        (self.root / 'data_1').write_bytes(b'corrupt')
        self.assertIsNone(cache.read_cached_article_html(self.url))

    def test_rejects_non_public_input_without_reading_cache(self):
        with patch.object(cache, '_cache_roots') as roots:
            for url in ['https://example.org/s/a', 'file:///secret', None,
                        'https://mp.weixin.qq.com/s?__biz=a&mid=1']:
                self.assertIsNone(cache.read_cached_article_html(url))
            roots.assert_not_called()

    def test_stream_rejects_block_size_mismatch(self):
        self.fixture()
        block = bytearray(8192 + 256)
        struct.pack_into('<I', block, 0, cache._MAGIC)
        struct.pack_into('<I', block, 12, 1024)
        (self.root / 'data_2').write_bytes(block)
        self.assertIsNone(cache._stream(self.root, 0xa0020000, 10, 20))
        self.assertIsNone(cache._stream(self.root, 0xa0020000, 300, 500))

    def test_stream_reads_internal_block_at_declared_offset(self):
        block = bytearray(8192 + 3 * 1024)
        struct.pack_into('<I', block, 0, cache._MAGIC)
        struct.pack_into('<I', block, 12, 1024)
        block[8192 + 1024:8192 + 1029] = b'hello'
        (self.root / 'data_2').write_bytes(block)
        self.assertEqual(cache._stream(self.root, 0xb0020001, 5, 10), b'hello')


if __name__ == '__main__':
    unittest.main()
