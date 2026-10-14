import hashlib
from pathlib import Path
import tempfile
import unittest

from .sns_media import normalized_cache_url, resolve_cached_media


class MomentsMediaCacheTest(unittest.TestCase):
    def test_normalized_url_removes_volatile_credentials(self):
        self.assertEqual(normalized_cache_url(
            'https://MEDIA.example/path.jpg?token=one&idx=2&size=small#fragment'),
            'media.example/path.jpg?size=small')

    def test_normalized_url_matches_weflow_default_port_cache_identity(self):
        cache_url = 'https://media.example/path.jpg?size=small'
        signed_url = 'https://user:secret@MEDIA.example:443/path.jpg?token=x&idx=2&size=small'
        self.assertEqual(normalized_cache_url(signed_url), normalized_cache_url(cache_url))
        self.assertEqual(normalized_cache_url('http://media.example:80/path.jpg'),
                         normalized_cache_url('http://media.example/path.jpg'))
        self.assertEqual(normalized_cache_url('https://media.example:8443/path.jpg'),
                         'media.example:8443/path.jpg')

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            digest = hashlib.md5(normalized_cache_url(cache_url).encode()).hexdigest()
            path = cache / (digest + '.jpg')
            path.write_bytes(b'\xff\xd8\xffsynthetic-jpeg')
            self.assertEqual(resolve_cached_media(cache, signed_url),
                             (path.resolve(), 'image/jpeg', 'image'))

    def test_reads_only_verified_existing_cache_and_matches_weflow_name(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            raw_url = 'https://media.example/photo.jpg?token=rotating'
            stable = normalized_cache_url(raw_url)
            path = cache / (hashlib.md5(stable.encode()).hexdigest() + '.jpg')
            path.write_bytes(b'\xff\xd8\xffsynthetic-jpeg')

            found = resolve_cached_media(cache, raw_url)

            self.assertEqual(found, (path.resolve(), 'image/jpeg', 'image'))
            self.assertIsNone(resolve_cached_media(cache, 'https://media.example/missing.jpg'))

    def test_resolves_weflow_bmp_moments_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            raw_url = 'https://media.example/photo.bmp?token=rotating'
            digest = hashlib.md5(normalized_cache_url(raw_url).encode()).hexdigest()
            path = cache / (digest + '.jpg')
            payload = b'BM' + b'\x00' * 12 + b'synthetic-bmp'
            path.write_bytes(payload)

            self.assertEqual(resolve_cached_media(cache, raw_url, kind='image'),
                             (path.resolve(), 'image/bmp', 'image'))

    def test_forced_video_kind_resolves_signed_mp4_without_url_heuristics(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            raw_url = 'https://media.example/clip?token=rotating&idx=4'
            digest = hashlib.md5(normalized_cache_url(raw_url).encode()).hexdigest()
            path = cache / (digest + '.mp4')
            path.write_bytes(b'\x00\x00\x00\x18ftypisomsynthetic-video')

            self.assertEqual(resolve_cached_media(cache, raw_url, kind='video'),
                             (path.resolve(), 'video/mp4', 'video'))
            self.assertIsNone(resolve_cached_media(cache, 'https://media.example/missing', kind='video'))
            path.write_bytes(b'not-an-mp4-file')
            self.assertIsNone(resolve_cached_media(cache, raw_url, kind='video'))
            with self.assertRaises(ValueError):
                resolve_cached_media(cache, raw_url, kind='audio')

    def test_iso_bmff_image_brands_are_not_misclassified_as_video(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            for brand, mime in (('avif', 'image/avif'), ('avis', 'image/avif'),
                                ('heic', 'image/heic'), ('heix', 'image/heic'),
                                ('mif1', 'image/heic'), ('msf1', 'image/heic')):
                raw_url = f'https://media.example/photo-{brand}'
                digest = hashlib.md5(normalized_cache_url(raw_url).encode()).hexdigest()
                path = cache / f'{digest}.jpg'
                path.write_bytes(b'\x00\x00\x00\x18ftyp' + brand.encode() + b'\x00' * 16)

                self.assertEqual(resolve_cached_media(cache, raw_url),
                                 (path.resolve(), mime, 'image'))
                self.assertEqual(resolve_cached_media(cache, raw_url, kind='image'),
                                 (path.resolve(), mime, 'image'))
                self.assertIsNone(resolve_cached_media(cache, raw_url, kind='video'))

    def test_rejects_truncated_or_invalid_ftyp_box(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            raw_url = 'https://media.example/clip'
            digest = hashlib.md5(normalized_cache_url(raw_url).encode()).hexdigest()
            path = cache / (digest + '.mp4')
            for payload in (b'\x00\x00\x00\x08ftypisom',
                            b'\x00\x00\x00\x20ftypisomshort',
                            b'\x00\x00\x00\x19ftypisom' + b'\x00' * 17):
                path.write_bytes(payload)
                self.assertIsNone(resolve_cached_media(cache, raw_url, kind='video'))

    def test_iso_bmff_brand_matching_uses_exact_brand_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            compatible_image = 'https://media.example/compatible-image'
            digest = hashlib.md5(normalized_cache_url(compatible_image).encode()).hexdigest()
            path = cache / f'{digest}.jpg'
            path.write_bytes(b'\x00\x00\x00\x18ftypisom\x00\x00\x00\x00heic\x00\x00\x00\x00')
            self.assertEqual(resolve_cached_media(cache, compatible_image, kind='image'),
                             (path.resolve(), 'image/heic', 'image'))

            false_match = 'https://media.example/false-heic'
            digest = hashlib.md5(normalized_cache_url(false_match).encode()).hexdigest()
            path = cache / f'{digest}.jpg'
            path.write_bytes(b'\x00\x00\x00\x18ftypisom\x00\x00\x00\x00xheicxxx')
            self.assertIsNone(resolve_cached_media(cache, false_match, kind='image'))

    def test_forced_video_kind_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / 'cache'
            cache.mkdir()
            raw_url = 'https://media.example/clip'
            digest = hashlib.md5(normalized_cache_url(raw_url).encode()).hexdigest()
            outside = root / (digest + '.mp4')
            outside.write_bytes(b'\x00\x00\x00\x18ftypisomoutside')
            link = cache / (digest + '.mp4')
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest('symlinks unavailable')
            self.assertIsNone(resolve_cached_media(cache, raw_url, kind='video'))

    def test_rejects_symlink_cache_entry_and_unrecognized_content(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            raw_url = 'https://media.example/photo.jpg'
            digest = hashlib.md5(normalized_cache_url(raw_url).encode()).hexdigest()
            external = cache.parent / 'external-image.jpg'
            external.write_bytes(b'\xff\xd8\xffoutside')
            link = cache / (digest + '.jpg')
            try:
                link.symlink_to(external)
            except (OSError, NotImplementedError):
                self.skipTest('symlinks unavailable')
            self.assertIsNone(resolve_cached_media(cache, raw_url))

            link.unlink()
            link.write_bytes(b'not an image')
            self.assertIsNone(resolve_cached_media(cache, raw_url))


if __name__ == '__main__':
    unittest.main()
