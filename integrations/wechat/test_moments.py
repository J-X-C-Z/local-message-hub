import sqlite3
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from integrations.wechat.moments import MAX_XML_BYTES, parse_post, read_timeline
from integrations.wechat.provider import WeChatProvider
from integrations.wechat.sns_media import normalized_cache_url


class MomentsReaderTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE SnsTimeLine(tid INTEGER, user_name TEXT, content TEXT, createTime INTEGER)')
        self.db.executemany('INSERT INTO SnsTimeLine VALUES(?,?,?,?)', [
            (3, 'wxid_alice', '<TimelineObject><CreateTime>300</CreateTime><ContentDesc>third hello</ContentDesc><Type>1</Type><LikeUserList><LikeUser><Username>wxid_bob</Username></LikeUser></LikeUserList><CommentUserList><CommentUser><Username>wxid_bob</Username><NickName>Bob</NickName><Content>nice</Content></CommentUser></CommentUserList><Location><City>HK</City></Location></TimelineObject>', 250),
            (2, 'wxid_bob', '<TimelineObject><CreateTime>200</CreateTime><ContentDesc>second</ContentDesc></TimelineObject>', 190),
            (1, 'wxid_alice', '<TimelineObject><CreateTime>100</CreateTime><ContentDesc>first</ContentDesc></TimelineObject>', 90),
            (4, 'wxid_alice', 'not xml', 400),
        ])

    def tearDown(self):
        self.db.close()

    def test_read_filters_sorts_paginates_and_parses_interactions(self):
        result = read_timeline(self.db, limit=1, usernames=['wxid_alice'], keyword='hello',
                               start_time=250, nickname_for=lambda username: 'Alice')
        self.assertTrue(result['available'])
        self.assertEqual(result['total'], 1)
        self.assertFalse(result['has_more'])
        post = result['items'][0]
        self.assertEqual(post['id'], '3')
        self.assertEqual(post['nickname'], 'Alice')
        self.assertEqual(post['likes'], ['wxid_bob'])
        self.assertEqual(len(post['comments']), 1)
        self.assertEqual(post['comments'][0]['content'], 'nice')
        self.assertEqual(post['location']['city'], 'HK')

    def test_pagination_and_unknown_schema_fail_closed(self):
        page = read_timeline(self.db, limit=1, offset=1)
        self.assertEqual(page['items'][0]['id'], '2')
        self.assertTrue(page['has_more'])
        other = sqlite3.connect(':memory:')
        other.execute('CREATE TABLE SnsTimeLine(unexpected TEXT)')
        result = read_timeline(other)
        self.assertFalse(result['available'])
        self.assertEqual(result['reason'], 'moments_schema_unsupported')
        other.close()

    def test_filtered_pagination_retains_only_requested_page(self):
        self.db.executemany('INSERT INTO SnsTimeLine VALUES(?,?,?,?)', [
            (8, 'wxid_alice', '<TimelineObject><ContentDesc>fourth</ContentDesc></TimelineObject>', 80),
            (9, 'wxid_alice', '<TimelineObject><ContentDesc>fifth</ContentDesc></TimelineObject>', 90),
        ])
        self.db.commit()

        result = read_timeline(self.db, limit=2, offset=1)

        self.assertEqual(result['total'], 5)
        self.assertEqual([item['id'] for item in result['items']], ['8', '3'])
        self.assertTrue(result['has_more'])

    def test_scan_byte_budget_counts_xml_payload_not_timestamp(self):
        db = sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE SnsTimeLine(tid INTEGER, user_name TEXT, content TEXT, createTime INTEGER)')
            xml = '<TimelineObject><ContentDesc>' + ('x' * 40) + '</ContentDesc></TimelineObject>'
            db.executemany('INSERT INTO SnsTimeLine VALUES(?,?,?,?)', [
                (2, 'wxid_fixture', xml, 2),
                (1, 'wxid_fixture', xml, 1),
            ])
            with patch('integrations.wechat.moments.MAX_SCAN_BYTES', 120):
                result = read_timeline(db)
            self.assertTrue(result['truncated'])
            self.assertEqual([item['id'] for item in result['items']], ['2'])
        finally:
            db.close()

    def test_reads_supported_without_rowid_schema(self):
        db = sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE SnsTimeLine(tid INTEGER PRIMARY KEY, user_name TEXT, content TEXT) WITHOUT ROWID')
            db.executemany('INSERT INTO SnsTimeLine VALUES(?,?,?)', [
                (2, 'wxid_bob', '<TimelineObject><ContentDesc>second</ContentDesc></TimelineObject>'),
                (1, 'wxid_alice', '<TimelineObject><ContentDesc>first</ContentDesc></TimelineObject>'),
            ])
            result = read_timeline(db)
            self.assertTrue(result['available'], result)
            self.assertEqual([item['id'] for item in result['items']], ['2', '1'])
        finally:
            db.close()

    def test_rejects_xml_entities_and_oversized_payloads(self):
        self.assertIsNone(parse_post(1, 'wxid', '<!DOCTYPE a [<!ENTITY x "boom">]><a>&x;</a>'))
        self.assertIsNone(parse_post(1, 'wxid', '<a>' + ('x' * (2 * 1024 * 1024)) + '</a>'))
        multibyte = '<a>' + ('界' * (MAX_XML_BYTES // 3 + 1)) + '</a>'
        self.assertLess(len(multibyte), MAX_XML_BYTES)
        self.assertGreater(len(multibyte.encode('utf-8')), MAX_XML_BYTES)
        self.assertIsNone(parse_post(1, 'wxid', multibyte))

    def test_parses_live_photo_urls_and_private_attributes(self):
        post = parse_post(1, 'wxid', '''<TimelineObject><MediaList><Media>
          <Url>https://media.example/still.jpg</Url><Thumb>https://media.example/still-thumb.jpg</Thumb>
          <livePhoto><url token="live-token" key="live-key" enc_idx="4">https://media.example/clip</url>
          <thumb token="thumb-token">https://media.example/clip-thumb.jpg</thumb></livePhoto>
        </Media></MediaList></TimelineObject>''')
        media = post['media'][0]
        self.assertEqual(media['url'], 'https://media.example/still.jpg')
        self.assertEqual(media['livePhoto']['url'], 'https://media.example/clip')
        self.assertEqual(media['livePhoto']['thumb'], 'https://media.example/clip-thumb.jpg')
        self.assertEqual(media['livePhoto']['token'], 'live-token')
        self.assertEqual(media['livePhoto']['thumbToken'], 'thumb-token')
        self.assertEqual(media['livePhoto']['key'], 'live-key')
        self.assertEqual(media['livePhoto']['encIdx'], '4')
        self.assertFalse(media['livePhoto']['local_available'])

    def test_live_photo_only_does_not_become_ordinary_media(self):
        post = parse_post(1, 'wxid', '''<TimelineObject><MediaList><Media>
          <livePhoto><url token="live-token">https://media.example/clip.mp4</url>
          <thumb>https://media.example/clip-thumb.jpg</thumb></livePhoto>
        </Media></MediaList></TimelineObject>''')
        media = post['media'][0]
        self.assertEqual(media['url'], '')
        self.assertEqual(media['thumb'], '')
        self.assertEqual(media['token'], '')
        self.assertEqual(media['livePhoto']['url'], 'https://media.example/clip.mp4')
        self.assertEqual(media['livePhoto']['thumb'], 'https://media.example/clip-thumb.jpg')

    def test_parses_weflow_media_url_and_thumbnail_attributes(self):
        post = parse_post(1, 'wxid', '''<TimelineObject><MediaList><Media>
          <url token="url-token" key="url-key" md5="url-md5" enc_idx="7">https://media.example/full</url>
          <thumb token="thumb-token" key="thumb-key">https://media.example/thumb</thumb>
        </Media></MediaList></TimelineObject>''')
        media = post['media'][0]
        self.assertEqual(media, {
            'url': 'https://media.example/full', 'thumb': 'https://media.example/thumb',
            'md5': 'url-md5', 'token': 'url-token', 'thumbToken': 'thumb-token',
            'key': 'url-key', 'encIdx': '7', 'local_available': False,
        })

    def test_deep_media_xml_stays_within_size_bound_without_recursive_walk(self):
        depth = 1500
        nested = '<Layer>' * depth + '<Url>https://media.example/photo.jpg</Url>' + '</Layer>' * depth
        payload = f'<TimelineObject><MediaList><Media>{nested}</Media></MediaList></TimelineObject>'
        self.assertLess(len(payload.encode('utf-8')), MAX_XML_BYTES)

        post = parse_post(42, 'wxid_fixture', payload)

        self.assertEqual(len(post['media']), 1)
        self.assertEqual(post['media'][0]['url'], 'https://media.example/photo.jpg')

    def test_preserves_weflow_location_coordinates_and_distinct_labels(self):
        post = parse_post(1, 'wxid', '''<TimelineObject><Location latitude="22.302711" longitude="114.177216">
          <PoiName>Central</PoiName><PoiAddress>Queen's Road</PoiAddress>
          <PoiAddressName>Queen's Road Central</PoiAddressName><Label>Office</Label>
          <City>Hong Kong</City><Country>China</Country>
        </Location></TimelineObject>''')
        self.assertEqual(post['location'], {
            'city': 'Hong Kong', 'country': 'China', 'poiName': 'Central',
            'poiAddress': "Queen's Road", 'poiAddressName': "Queen's Road Central",
            'label': 'Office', 'latitude': 22.302711, 'longitude': 114.177216,
        })

    def test_omits_out_of_range_moments_coordinates(self):
        post = parse_post(1, 'wxid', '''<TimelineObject><Location latitude="91" longitude="181">
          <City>HK</City></Location></TimelineObject>''')
        self.assertEqual(post['location'], {'city': 'HK'})

    def test_provider_resolves_existing_moments_media_and_strips_remote_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'sns' / 'sns.db'
            source.parent.mkdir()
            db = sqlite3.connect(source)
            try:
                db.execute('CREATE TABLE SnsTimeLine(tid INTEGER, user_name TEXT, content TEXT, createTime INTEGER)')
                url = 'https://media.example/image.jpg?token=private&idx=7'
                live_url = 'https://media.example/clip?token=live-secret&idx=9'
                content = ('<TimelineObject><ContentDesc>fixture</ContentDesc><MediaList><Media>'
                           f'<Url>{url.replace("&", "&amp;")}</Url><Token>private</Token><Key>private-key</Key>'
                           f'<livePhoto><url token="live-secret" key="live-key">{live_url.replace("&", "&amp;")}</url>'
                           '</livePhoto>'
                           '</Media></MediaList></TimelineObject>')
                db.execute('INSERT INTO SnsTimeLine VALUES(1,?,?,1)', ('wxid_fixture', content))
                db.commit()
            finally:
                db.close()
            cache = root / 'sns_cache'
            cache.mkdir()
            digest = hashlib.md5(normalized_cache_url(url).encode()).hexdigest()
            media_path = cache / (digest + '.jpg')
            media_path.write_bytes(b'\xff\xd8\xfffixture-image')
            video_path = cache / (hashlib.md5(normalized_cache_url(live_url).encode()).hexdigest() + '.mp4')
            video_path.write_bytes(b'\x00\x00\x00\x18ftypisomfixture-video')

            provider = WeChatProvider.__new__(WeChatProvider)
            provider.sns_cache_dir = cache
            provider.reader = Mock()
            provider.reader._db_files = [('sns/sns.db', str(source), 0)]
            provider.reader._key_works.return_value = True
            provider.reader._open.side_effect = lambda _rel: sqlite3.connect(source)
            provider.reader.get_nickname.return_value = 'Fixture'

            result = provider.moments()

            self.assertTrue(result['available'], result)
            self.assertTrue(result['items'], result)
            media = result['items'][0]['media'][0]
            self.assertTrue(media['local_available'])
            self.assertEqual(media['_local_path'], str(media_path.resolve()))
            self.assertEqual(media['mime_type'], 'image/jpeg')
            self.assertNotIn('url', media)
            self.assertNotIn('token', media)
            self.assertNotIn('key', media)
            live_photo = media['livePhoto']
            self.assertTrue(live_photo['local_available'])
            self.assertEqual(live_photo['_local_path'], str(video_path.resolve()))
            self.assertEqual(live_photo['mime_type'], 'video/mp4')
            self.assertEqual(live_photo['type'], 'video')
            for field in ('url', 'thumb', 'token', 'key'):
                self.assertNotIn(field, live_photo)
            self.assertNotIn('live-secret', repr(result))


if __name__ == '__main__':
    unittest.main()
