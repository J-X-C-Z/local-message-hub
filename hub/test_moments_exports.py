import csv
import io
import json
import random
import string
import unittest
import zipfile

from hub.moments_exports import (MomentsExportTooLarge, render_moments_export,
                                 render_moments_export_bounded)


class MomentsExportTest(unittest.TestCase):
    def setUp(self):
        self.result = {'source': {'platform': 'wechat', 'database': 'sns.db', 'scope': 'local'},
            'total': 1, 'items': [{
                'id': 'fixture', 'username': '=HYPERLINK("x")', 'nickname': 'Alice',
                'createTime': 1_791_604_800, 'contentDesc': '<script>alert(1)</script>',
                'location': {'city': 'Hong Kong', 'poiName': 'Harbor'}, 'likes': ['=1+1'],
                'comments': [{'nickname': '<Bob>', 'content': '<img src=x>'}],
                'media': [{'url': 'https://private.invalid/?token=secret', 'key': 'secret',
                          'md5': 'secret', 'type': 'image', 'local_available': False}],
            }]}

    def test_json_retains_local_provenance_and_strips_media_credentials(self):
        payload, mime, ext = render_moments_export(self.result)
        data = json.loads(payload)
        self.assertEqual((mime, ext), ('application/json; charset=utf-8', 'json'))
        self.assertEqual(data['source'], self.result['source'])
        self.assertEqual(data['items'][0]['media'], [{'type': 'image', 'local_available': False}])
        self.assertNotIn(b'private.invalid', payload)
        self.assertNotIn(b'secret', payload)

    def test_json_export_drops_unrecognized_post_and_nested_fields(self):
        post = {
            **self.result['items'][0],
            '_local_path': 'PRIVATE_POST_PATH',
            'source_key': 'PRIVATE_SOURCE_KEY',
            'comments': [{
                'nickname': 'Bob', 'content': 'hello', 'refCommentId': 'comment-1',
                'cache_locator': 'PRIVATE_COMMENT_LOCATOR',
            }],
            'location': {'city': 'Hong Kong', 'cache_root': 'PRIVATE_CACHE_ROOT'},
        }

        payload, _, _ = render_moments_export({**self.result, 'items': [post]}, 'json')
        data = json.loads(payload)
        exported = data['items'][0]

        self.assertEqual(exported['comments'], [{
            'nickname': 'Bob', 'content': 'hello', 'refCommentId': 'comment-1',
        }])
        self.assertEqual(exported['location'], {'city': 'Hong Kong'})
        for secret in (b'PRIVATE_POST_PATH', b'PRIVATE_SOURCE_KEY', b'PRIVATE_COMMENT_LOCATOR',
                       b'PRIVATE_CACHE_ROOT'):
            self.assertNotIn(secret, payload)

    def test_json_export_preserves_selected_account_but_omits_account_directory(self):
        result = {
            **self.result,
            'account_id': 'wxid_selected',
            'accounts': ['wxid_selected', 'wxid_other_account'],
            'source': {'platform': 'wechat', 'database': 'sns.db', 'scope': 'local',
                       'source_path': 'PRIVATE_SOURCE_PATH'},
            'private_key': 'PRIVATE_KEY',
        }

        for renderer in (render_moments_export, render_moments_export_bounded):
            with self.subTest(renderer=renderer.__name__):
                payload, _, _ = renderer(result, 'json')
                payload = bytes(payload)
                exported = json.loads(payload)
                self.assertEqual(exported['account_id'], 'wxid_selected')
                self.assertEqual(exported['source'], {
                    'platform': 'wechat', 'database': 'sns.db', 'scope': 'local',
                })
                for secret in (b'wxid_other_account', b'PRIVATE_SOURCE_PATH', b'PRIVATE_KEY'):
                    self.assertNotIn(secret, payload)

    def test_csv_quotes_rows_and_prevents_formula_injection(self):
        payload, mime, ext = render_moments_export(self.result, 'csv')
        rows = list(csv.reader(io.StringIO(payload.decode('utf-8'))))
        self.assertEqual((mime, ext), ('text/csv; charset=utf-8', 'csv'))
        self.assertEqual(rows[1][:3], ['wechat', 'sns.db', 'local'])
        self.assertEqual(rows[1][4], "'=HYPERLINK(\"x\")")
        self.assertEqual(rows[1][10], "'=1+1")
        second = {**self.result['items'][0], 'id': 'tab', 'contentDesc': '\t=2+2', 'location': None}
        payload, _, _ = render_moments_export({**self.result, 'items': [second]}, 'csv')
        self.assertEqual(list(csv.reader(io.StringIO(payload.decode('utf-8'))))[1][6], "'\t=2+2")
        self.assertIn('Hong Kong', rows[1][8])
        self.assertIn('<Bob>: <img src=x>', rows[1][11])

    def test_html_escapes_user_content_and_markdown_keeps_interactions(self):
        html_data, _, _ = render_moments_export(self.result, 'html')
        markdown, _, _ = render_moments_export(self.result, 'md')
        self.assertIn(b'&lt;script&gt;', html_data)
        self.assertNotIn(b'<script>', html_data)
        self.assertNotIn(b'<img src=x>', html_data)
        self.assertIn('Hong Kong', markdown.decode())
        self.assertIn('<Bob>', markdown.decode())

    def test_xlsx_is_bounded_and_contains_only_safe_inline_text(self):
        payload, mime, ext = render_moments_export(self.result, 'xlsx')
        self.assertEqual((mime, ext), (
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'xlsx'))
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(archive.testzip(), None)
            sheet = archive.read('xl/worksheets/sheet1.xml')
        self.assertIn(b't="inlineStr"', sheet)
        self.assertIn(b'>=HYPERLINK("x")</t>', sheet)
        self.assertNotIn(b'<f>', sheet)
        self.assertIn(b'&lt;script&gt;', sheet)
        self.assertNotIn(b'private.invalid', sheet)
        self.assertNotIn(b'secret', sheet)

    def test_unknown_format_is_rejected(self):
        with self.assertRaises(ValueError):
            render_moments_export(self.result, 'ods')

    def test_bounded_renderer_matches_existing_formats(self):
        for format in ('json', 'csv', 'md', 'txt', 'html', 'xlsx'):
            with self.subTest(format=format):
                expected = render_moments_export(self.result, format)
                actual = render_moments_export_bounded(self.result, format)
                self.assertEqual(bytes(actual[0]), expected[0])
                self.assertEqual(actual[1:], expected[1:])

    def test_bounded_renderer_stops_consuming_items_at_output_limit(self):
        consumed = []

        def items():
            consumed.append('first')
            yield {'contentDesc': 'x' * 2_000}
            consumed.append('second')
            yield {'contentDesc': 'must not be consumed'}

        with self.assertRaises(MomentsExportTooLarge):
            render_moments_export_bounded({'source': self.result['source'], 'items': items()},
                                          max_bytes=500)
        self.assertEqual(consumed, ['first'])

    def test_xlsx_limit_stops_consuming_items(self):
        consumed = []

        def items():
            consumed.append('first')
            text = ''.join(random.Random(7).choices(string.ascii_letters + string.digits, k=32767))
            yield {'contentDesc': text}
            consumed.append('second')
            yield {'contentDesc': 'must not be consumed'}

        with self.assertRaises(MomentsExportTooLarge):
            render_moments_export_bounded({'source': self.result['source'], 'items': items()},
                                          'xlsx', max_bytes=4096)
        self.assertEqual(consumed, ['first'])


if __name__ == '__main__':
    unittest.main()
