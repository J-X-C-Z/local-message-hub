"""Campus index tests use synthetic public notices and no personal messages."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from hub.campus import CampusService


SOURCES = [dict(id='academic', name='教务', category='study', kind='feed'),
           dict(id='library', name='图书馆', category='life', kind='feed'),
           dict(id='portal', name='门户', category='study', kind='portal')]


def notice(url='https://www.sysu.edu.cn/notice', **values):
    return dict(title='公开通知', url=url, summary='正文', published_at='2026-10-23', **values)


class CampusTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.catalog = patch('hub.campus.SOURCES', SOURCES)
        self.catalog.start()
        self.service = CampusService(Path(self.temp.name))

    def tearDown(self):
        self.service.close()
        self.catalog.stop()
        self.temp.cleanup()

    def ingest(self, *items, source=0):
        self.service._store_result(SOURCES[source], dict(status='ok', items=list(items)))

    def test_default_profile_and_persistence(self):
        self.assertEqual(self.service.profile()['next_campus'], '东校园')
        self.service.configure_profile(dict(year='2025', keywords=['竞赛', '竞赛']))
        self.service.close()
        self.service = CampusService(Path(self.temp.name))
        self.assertEqual(self.service.profile()['year'], '2025')
        self.assertEqual(self.service.profile()['keywords'], ['竞赛'])

    def test_reject_bad_profile_and_state(self):
        for values in ({'password': 'secret'}, {'keywords': 'a'}, {'campus': None}, {'keywords': ['']}, {'keywords': ['a'] * 31}):
            with self.assertRaises(ValueError):
                self.service.configure_profile(values)
        self.ingest(notice())
        item = self.service.search()['items'][0]
        for values in ({'read': 1}, {'bookmarked': 'true'}, {'title': 'overwrite'}, {}):
            with self.assertRaises(ValueError):
                self.service.set_item_state(item['id'], values)

    def test_refresh_deduplicates_preserves_states_and_failure_cache(self):
        with patch('hub.campus.fetch_source', return_value=dict(status='ok', items=[notice()])):
            status = self.service.refresh()
            self.assertEqual(status['added'], 1)
            self.assertEqual(status['processed'], 2)
            self.assertEqual(status['state'], 'completed')
        item = self.service.search()['items'][0]
        self.service.set_item_state(item['id'], dict(bookmarked=True, read=True))
        with patch('hub.campus.fetch_source', return_value=dict(status='ok', items=[notice()])):
            self.assertEqual(self.service.refresh()['added'], 0)
        with patch('hub.campus.fetch_source', side_effect=RuntimeError('token=private')):
            self.assertEqual(self.service.refresh()['state'], 'completed_with_errors')
        result = self.service.search(bookmarked=True)
        self.assertEqual(result['total'], 1)
        self.assertTrue(result['items'][0]['read'])
        self.assertNotIn('private', str(self.service.sources()) + str(self.service.refresh_status()))

    def test_source_counts_filter_search_and_pagination(self):
        self.ingest(notice(), notice('https://www.sysu.edu.cn/new', content='奖学金申请'))
        self.ingest(notice('https://www.sysu.edu.cn/library'), source=1)
        self.assertEqual(self.service.search(category='study', limit=1, offset=1)['total'], 2)
        self.assertEqual(len(self.service.search(category='study', limit=1, offset=1)['items']), 1)
        self.assertEqual(self.service.search(source_id='library')['total'], 1)
        self.assertEqual(self.service.search(q='奖学金')['total'], 1)
        self.assertEqual(self.service.sources()['items'][0]['item_count'], 2)
        for kw in ({'limit': 0}, {'offset': -1}, {'relevant': 'false'}):
            with self.assertRaises(ValueError):
                self.service.search(**kw)

    def test_relevance_both_campuses_faculty_keywords_and_global(self):
        self.ingest(notice(), notice('https://www.sysu.edu.cn/south', campus='南校园'),
                    notice('https://www.sysu.edu.cn/east', campus='东校园'),
                    notice('https://www.sysu.edu.cn/other', campus='深圳校区'),
                    notice('https://www.sysu.edu.cn/faculty', faculty='电子与信息学院'),
                    notice('https://www.sysu.edu.cn/keyword', campus='深圳校区', content='竞赛'))
        items = self.service.search(relevant=True)['items']
        self.assertEqual(len(items), 5)
        self.assertTrue(all(item['relevance_reasons'] for item in items))
        self.assertEqual(self.service.search(campus='南校园')['total'], 3)

    def test_unknown_dates_stay_unknown_and_sort_after_dated(self):
        self.ingest(dict(title='无日期', url='https://www.sysu.edu.cn/unknown'))
        self.ingest(notice())
        items = self.service.search()['items']
        self.assertEqual(items[-1]['published_at'], '')
        self.assertEqual(items[0]['published_at'], '2026-10-23')

    def test_url_fragment_dedup_and_unknown_id(self):
        self.ingest(notice(), notice('https://www.sysu.edu.cn/notice#section'))
        self.assertEqual(self.service.search()['total'], 1)
        self.ingest(notice('javascript:alert(1)'))
        self.assertEqual(self.service.search()['total'], 1)
        with self.assertRaises(ValueError):
            self.service.get_item('missing')

    def test_refresh_singleflight_and_bounded_workers(self):
        started = threading.Event()
        release = threading.Event()
        def fetch(source):
            started.set()
            release.wait(3)
            return dict(status='ok', items=[])
        with patch('hub.campus.fetch_source', side_effect=fetch) as collector:
            self.assertEqual(self.service.start_refresh()['state'], 'running')
            self.assertTrue(started.wait(2))
            first = self.service.refresh_status()['started_at']
            self.assertEqual(self.service.start_refresh()['started_at'], first)
            release.set()
            self.service._thread.join(3)
            self.assertEqual(collector.call_count, 2)
            self.assertEqual(self.service.refresh_status()['processed'], 2)

    def test_wechat_import_uses_same_index(self):
        with patch('integrations.campus.adapter.fetch_wechat_article', return_value=dict(status='ok', items=[notice('https://mp.weixin.qq.com/s/example')])):
            item = self.service.import_wechat('https://mp.weixin.qq.com/s/example')
            self.assertEqual(item['source_id'], 'wechat-manual')
            self.assertEqual(self.service.search()['total'], 1)

    def test_source_scope_and_empty_fetch_and_external_links(self):
        scoped = dict(SOURCES[0], scope='深圳校区')
        self.service._store_result(scoped, dict(status='ok', items=[notice(), notice('https://outside.example/notice')]))
        item = self.service.search()['items'][0]
        self.assertEqual(len(item['id']), 64)
        self.assertEqual(item['campus'], '深圳校区')
        self.assertFalse(item['relevant'])
        self.assertEqual(self.service.search()['total'], 1)
        with patch('hub.campus.fetch_source', return_value=dict(status='empty', items=[])):
            status = self.service.refresh()
        self.assertEqual(status['state'], 'completed')
        self.assertEqual(status['errors'], [])
        self.assertEqual(self.service.search()['total'], 1)

    def test_official_feed_can_link_public_wechat_article(self):
        self.ingest(notice('https://mp.weixin.qq.com/s/public-article'))
        self.assertEqual(self.service.search()['total'], 1)
        self.assertEqual(self.service.search()['items'][0]['source_id'], 'academic')

    def test_personal_matches_rank_before_newer_global_only_notices(self):
        old = notice('https://www.sysu.edu.cn/older')
        old.update(title='奖学金申请', published_at='2026-09-01')
        self.ingest(notice(), old)
        chronological = self.service.search()['items']
        ranked = self.service.search(relevant=True)['items']
        self.assertEqual(chronological[0]['title'], '公开通知')
        self.assertEqual(ranked[0]['title'], '奖学金申请')
        self.assertIn('关注词: 奖学金', ranked[0]['relevance_reasons'])

    def test_undergraduate_relevance_excludes_graduate_only_notices(self):
        undergraduate = notice('https://www.sysu.edu.cn/undergraduate')
        undergraduate.update(title='本科奖学金申请', published_at='2026-09-01')
        graduate = notice('https://www.sysu.edu.cn/graduate', faculty='电子与信息学院')
        graduate['title'] = '研究生奖学金申请'
        recommendation = notice('https://www.sysu.edu.cn/recommendation')
        recommendation['title'] = '本科推荐免试研究生通知'
        explicit = notice('https://www.sysu.edu.cn/explicit', level='研究生')
        explicit['title'] = '奖学金申请'
        self.ingest(undergraduate, graduate, recommendation, explicit)
        self.assertEqual(self.service.search()['total'], 4)
        items = self.service.search(relevant=True)['items']
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['title'], '本科奖学金申请')
        self.assertIn('培养层次: 本科', items[0]['relevance_reasons'])
        self.service.configure_profile(dict(level='硕士'))
        titles = [i['title'] for i in self.service.search(relevant=True)['items']]
        self.assertIn('研究生奖学金申请', titles)
        self.assertNotIn('本科奖学金申请', titles)


if __name__ == '__main__':
    unittest.main()

