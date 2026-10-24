"""Local information station processing without remote models or public network."""
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hub.campus import CampusService
from hub.campus_briefing import CHINA, build_briefing


SOURCES = [dict(id='teaching', name='教务部', category='teaching', kind='feed', url='https://jwb.sysu.edu.cn/', enabled=True),
           dict(id='faculty', name='电信院', category='student', kind='feed', url='https://seit.sysu.edu.cn/', enabled=True)]
PROFILE = dict(level='本科', keywords=['奖学金', '竞赛', '选课'])
NOW = datetime(2026, 10, 23, 12, tzinfo=CHINA)


def article(id, source='teaching', date='2026-10-23T10:00:00+08:00', **values):
    item = dict(id=id, title='2026 年本科生奖学金评选通知', url='https://jwb.sysu.edu.cn/' + id,
                source_id=source, source_name=source, published_at=date,
                summary='2026年本科生奖学金申请截止日期为10月30日，请提交申请材料。', content_status='summary_only')
    item.update(values)
    return item


def briefing(items, section='all', **values):
    return build_briefing(items, SOURCES, PROFILE, {}, section=section, now=NOW, **values)


class BriefingTest(unittest.TestCase):
    def test_selected_timeline_keeps_new_opportunities_ahead_of_old_high_keyword_scores(self):
        old = article('old', date='2026-10-11', title='本科选课奖学金竞赛报名通知',
                      summary='本通知提供本科选课、奖学金和竞赛报名说明。')
        recent = article('recent', date='2026-10-22', title='本科竞赛报名通知',
                         summary='本次本科竞赛开始接受报名，请按原文要求提交材料。')
        data = briefing([old, recent], 'selected')
        self.assertEqual([event['id'] for event in data['items']], ['recent', 'old'])

    def test_conservative_grouping_provenance_and_distinct_year_deadline_program(self):
        items = [article('a'), article('b', 'faculty'),
                 article('c', title='2025年本科生奖学金评选通知'),
                 article('d', summary='2026年本科生奖学金申请截止日期为10月31日，请提交申请材料。'),
                 article('e', summary='2026年本科生国家励志奖学金申请截止日期为10月30日，请提交申请材料。')]
        data = briefing(items)
        self.assertEqual(data['total'], 4)
        group = next(e for e in data['items'] if e['report_count'] == 2)
        self.assertEqual(group['source_count'], 2)
        self.assertEqual({r['id'] for r in group['reports']}, {'a', 'b'})
        self.assertNotIn('content', group)

    def test_heat_counts_unique_source_and_decays_and_excludes_unknown_old_future(self):
        data = briefing([article('a'), article('b'), article('c', 'faculty')], 'hot')
        heat = data['items'][0]['heat']
        self.assertAlmostEqual(heat, 2 * 2 ** (-2 / 24), places=3)
        stale = briefing([article('old', date='2026-10-20'), article('unknown', date=''), article('future', date='2027-01-01')], 'hot')
        self.assertEqual(stale['total'], 0)
        self.assertEqual(briefing([article('a')], 'hot')['items'][0]['source_count'], 1)

    def test_grouped_report_previews_bounded_while_totals_stay_exact(self):
        data = briefing([article(str(i)) for i in range(80)])
        self.assertEqual(data['total'], 1)
        event = data['items'][0]
        self.assertEqual(event['report_count'], 80)
        self.assertEqual(len(event['reports']), 50)
        self.assertTrue(event['reports_truncated'])

    def test_unknown_and_exact_china_calendar_day_no_latest_substitution(self):
        items = [article('before', date='2026-10-22T15:59:59Z'),
                 article('start', date='2026-10-22T16:00:00Z'),
                 article('end', date='2026-10-23T15:59:59Z'),
                 article('after', date='2026-10-23T16:00:00Z'), article('unknown', date='')]
        data = briefing(items, 'daily', date='2026-10-23')
        ids = {r['id'] for e in data['items'] for r in e['reports']}
        self.assertEqual(ids, {'start', 'end'})
        self.assertEqual(data['counts']['unknown_date'], 1)
        self.assertEqual(briefing(items, 'daily', date='2026-10-01')['total'], 0)
        self.assertEqual(data['available_dates'], ['2026-10-23', '2026-10-22'])
        self.assertEqual(data['counts']['future_date'], 1)
        self.assertTrue(all(e['date'] == '2026-10-23' for e in data['items']))

    def test_selected_undergraduate_exclusion_title_only_and_transparent_rules(self):
        items = [article('eligible'), article('graduate', title='2026年研究生国家奖学金申请'),
                 article('empty', title='本科生竞赛报名', summary=''),
                 article('failed', title='本科生竞赛报名', summary='', content_status='fetch_error'),
                 article('news', title='校园新闻', summary='今日校园风景')]
        data = briefing(items, 'selected')
        self.assertEqual([e['id'] for e in data['items']], ['eligible'])
        self.assertTrue(any('关注主题' in reason for reason in data['items'][0]['reasons']))
        self.assertEqual(data['pipeline']['mode'], 'local_rules')
        self.assertEqual(data['counts']['title_only'], 1)

    def test_request_validation(self):
        for values in ({'date': '2026-2-1'}, {'date': '2026-02-30'}, {'topic': 'missing'}, {'limit': True}, {'offset': -1}):
            with self.assertRaises(ValueError):
                briefing([], **values)

    def test_selected_matches_current_next_campus_and_faculty_without_hiding_archive(self):
        profile = dict(PROFILE, campus='南校园', next_campus='东校园', faculty='电子与信息学院')
        items = [article('south', campus='南校园', faculty='电子与信息学院'),
                 article('east', campus='东校区'), article('shenzhen', campus='深圳校区')]
        data = build_briefing(items, SOURCES, profile, {}, section='selected', now=NOW)
        ids = {r['id'] for e in data['items'] for r in e['reports']}
        # Campus scope is part of grouping identity as well as personalized selection.
        self.assertEqual(ids, {'south', 'east'})
        self.assertTrue(any('匹配学院' in reason for e in data['items'] for reason in e['reasons']))
        all_data = build_briefing(items, SOURCES, profile, {}, section='all', now=NOW)
        self.assertEqual({r['id'] for e in all_data['items'] for r in e['reports']}, {'south', 'east', 'shenzhen'})

    def test_selected_omits_teacher_prizes_and_meeting_recaps_prioritizes_student_actions(self):
        items = [article('teacher', title='全国一等奖！中大教师在全国高校青年教师教学竞赛再创佳绩！'),
                 article('meeting', title='学院召开本科学习质量提升研讨会', summary='围绕本科课程、选课和竞赛培养开展研讨。'),
                 article('application', title='2026年本科生奖学金申请通知'),
                 article('old', title='2025年本科生奖学金申请通知', date='2025-10-01'),
                 article('unknown', title='本科生奖学金申请通知', date='')]
        selected = briefing(items, 'selected')
        self.assertEqual({e['id'] for e in selected['items']}, {'application', 'old', 'unknown'})
        self.assertEqual(selected['items'][0]['id'], 'application')
        self.assertTrue(any('学生办理线索' in r for r in selected['items'][0]['reasons']))
        self.assertFalse(any('近 ' in r for e in selected['items'] if e['id'] == 'unknown' for r in e['reasons']))
        self.assertEqual(briefing(items, 'all')['total'], 5)

    def test_future_source_dates_retained_in_archive_but_not_selected_or_daily(self):
        items = [article('future', date='2026-12-08'), article('today')]
        archive = briefing(items)
        self.assertEqual(archive['total'], 2)
        self.assertTrue(next(e for e in archive['items'] if e['id'] == 'future')['future_date'])
        self.assertEqual(archive['available_dates'], ['2026-10-23'])
        self.assertEqual(briefing(items, 'daily', date='2026-12-08')['total'], 0)
        self.assertEqual([e['id'] for e in briefing(items, 'selected')['items']], ['today'])


class StationPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.catalog = patch('hub.campus.SOURCES', SOURCES)
        self.catalog.start()
        self.service = CampusService(Path(self.temp.name))

    def tearDown(self):
        self.service.close()
        self.catalog.stop()
        self.temp.cleanup()

    def test_disabled_source_persists_and_stops_refresh_without_deleting_history(self):
        self.service._store_result(SOURCES[0], dict(items=[article('a')]))
        self.service.configure_source('teaching', dict(enabled=False))
        self.service.close()
        self.service = CampusService(Path(self.temp.name))
        with patch('hub.campus.fetch_source', return_value=dict(items=[], status='empty')) as fetch:
            self.service.refresh()
        self.assertEqual([c.args[0]['id'] for c in fetch.call_args_list], ['faculty'])
        self.assertEqual(self.service.search()['total'], 1)
        self.assertEqual(self.service.briefing('all')['counts']['disabled_sources'], 1)
        for source_id, values in [('absent', {'enabled': True}), ('teaching', {'enabled': 1}), ('teaching', {'account_id': 'replacement'})]:
            with self.assertRaises(ValueError):
                self.service.configure_source(source_id, values)

    def test_same_url_preserves_original_source_and_all_observed_provenance(self):
        raw = article('a')
        self.service._store_result(SOURCES[0], dict(items=[raw]))
        self.service._store_result(SOURCES[1], dict(items=[raw]))
        data = self.service.briefing('all')['items'][0]
        self.assertEqual(self.service.search()['total'], 1)
        self.assertEqual(data['source_count'], 2)
        self.assertEqual(data['reports'][0]['source_id'], 'teaching')

    def test_event_state_changes_all_reports_beyond_preview_and_clears_nonrepresentative_bookmark(self):
        self.service._store_result(SOURCES[0], dict(items=[article(str(i)) for i in range(70)]))
        event = self.service.briefing('all')['items'][0]
        nonrepresentative = next(item for item in self.service.search(limit=100)['items'] if item['id'] != event['id'])
        self.service.set_item_state(nonrepresentative['id'], dict(bookmarked=True))
        self.assertTrue(self.service.briefing('all')['items'][0]['bookmarked'])
        result = self.service.set_event_state(event['id'], dict(bookmarked=False, read=True))
        self.assertEqual(result['affected_count'], 70)
        self.assertTrue(all(item['read'] and not item['bookmarked'] for item in self.service.search(limit=100)['items']))
        for item_id, values in [('bad', {'read': True}), ('0' * 64, {'read': True}), (event['id'], {'read': 1})]:
            with self.assertRaises(ValueError):
                self.service.set_event_state(item_id, values)

    def test_cache_precedes_http_and_can_recover_inside_persisted_http_cooldown(self):
        raw = article('cache', url='https://mp.weixin.qq.com/s/cache-fixture')
        self.service._store_result(SOURCES[0], dict(items=[raw]))
        item = self.service.search()['items'][0]
        with patch('integrations.campus.adapter.read_cached_wechat_article', return_value=dict(items=[])), \
             patch('integrations.campus.adapter.fetch_wechat_article', return_value=dict(items=[])) as fetch:
            self.service.ensure_full_text(item['id'], force=False)
            self.service.close()
            self.service = CampusService(Path(self.temp.name))
            self.service.ensure_full_text(item['id'], force=False)
            self.assertEqual(fetch.call_count, 1)
        cached = dict(items=[dict(raw, content='真实缓存正文', content_status='full_text', content_source='local_wechat_cache')])
        with patch('integrations.campus.adapter.read_cached_wechat_article', return_value=cached), \
             patch('integrations.campus.adapter.fetch_wechat_article') as fetch:
            saved = self.service.ensure_full_text(item['id'])
        fetch.assert_not_called()
        self.assertEqual(saved['content'], '真实缓存正文')
        self.assertEqual(saved['content_source'], 'local_wechat_cache')

    def test_missing_or_invalid_refresh_date_retains_known_publication(self):
        raw = article('a', date='2026-10-01')
        self.service._store_result(SOURCES[0], dict(items=[raw]))
        for value in ('', 'invalid-date'):
            self.service._store_result(SOURCES[0], dict(items=[dict(raw, published_at=value)]))
            self.assertEqual(self.service.search()['items'][0]['published_at'], '2026-10-01')
        self.assertIn('2026-10-01', self.service.briefing('all')['available_dates'])

    def test_manual_retry_and_real_body_date_update_both_index_and_material(self):
        raw = article('cache-date', date='', url='https://mp.weixin.qq.com/s/cache-date')
        self.service._store_result(SOURCES[0], dict(items=[raw]))
        item = self.service.search()['items'][0]
        with patch('integrations.campus.adapter.read_cached_wechat_article', return_value=dict(items=[])), \
             patch('integrations.campus.adapter.fetch_wechat_article', return_value=dict(items=[])) as fetch:
            self.service.ensure_full_text(item['id'])
            self.service.ensure_full_text(item['id'])
            self.assertEqual(fetch.call_count, 2)
        cached = dict(items=[dict(raw, published_at='2026-10-20', content='来自本机缓存的真实正文', content_status='full_text')])
        with patch('integrations.campus.adapter.read_cached_wechat_article', return_value=cached):
            saved = self.service.ensure_full_text(item['id'])
        self.assertEqual(saved['published_at'], '2026-10-20')
        row = self.service._db.execute('SELECT published_at FROM items WHERE id=?', (item['id'],)).fetchone()
        self.assertEqual(row[0], '2026-10-20')
        self.assertEqual(self.service.briefing('daily', date='2026-10-20')['total'], 1)


if __name__ == '__main__':
    unittest.main()
