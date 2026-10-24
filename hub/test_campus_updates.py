"""Synthetic update tests: persistent cursors, body retention and scheduler lifecycle."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from hub.campus import CampusService


CONTACT = dict(account_id='fixture-account', id='gh_0123456789ab', name='Fixture official')
ARTICLE = dict(title='Article', url='https://mp.weixin.qq.com/s/fixture', summary='Card', content_status='summary_only')


class CampusUpdatesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = CampusService(Path(self.temp.name))
        self.sources = patch('hub.campus.SOURCES', [])
        self.sources.start()

    def tearDown(self):
        self.service.close()
        self.sources.stop()
        self.temp.cleanup()

    def seed(self):
        self.service.import_local_wechat(CONTACT, dict(items=[ARTICLE]))
        return self.service.search()['items'][0]

    def test_fulltext_retains_identity_state_and_survives_card_reimport(self):
        item = self.seed()
        self.service.set_item_state(item['id'], dict(read=True, bookmarked=True))
        with patch('integrations.campus.adapter.fetch_wechat_article', return_value=dict(
                status='ok', items=[dict(ARTICLE, content='Complete searchable body', content_status='full_text')])) as fetch:
            self.service.ensure_full_text(item['id'])
            self.service.import_local_wechat(CONTACT, dict(items=[ARTICLE]))
            saved = self.service.ensure_full_text(item['id'])
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(saved['content'], 'Complete searchable body')
        self.assertEqual(saved['source_id'], item['source_id'])
        self.assertTrue(saved['read'] and saved['bookmarked'])
        self.assertEqual(self.service.search(q='searchable')['total'], 1)

    def test_failure_has_cooldown_and_no_remote_details(self):
        item = self.seed()
        with patch('integrations.campus.adapter.fetch_wechat_article', side_effect=RuntimeError('secret')) as fetch:
            self.service.ensure_full_text(item['id'])
            with patch('integrations.wechat.official_accounts.get_cached_official_articles', return_value=dict(items=[ARTICLE], next_cursor={}, has_more=False)):
                status = self.service.refresh()
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(status['body_failed'], 0)
        self.assertEqual(status['backlog'], 1)
        self.assertNotIn('secret', str(self.service.get_item(item['id'])))

    def test_cursor_checkpoint_waits_for_store_and_retries_page(self):
        self.seed()
        self.service.configure_updates(dict(fetch_full_text=False))
        page = dict(items=[ARTICLE], next_cursor={'shard': [3, 7]}, has_more=False)
        original = self.service._store_result
        failed = [False]
        def fail_once(source, result, **kwargs):
            if result.get('items') and not failed[0]:
                failed[0] = True
                original(source, result, **kwargs)
                raise RuntimeError('interrupted')
            return original(source, result, **kwargs)
        with patch('integrations.wechat.official_accounts.get_cached_official_articles', return_value=page) as reader:
            with patch.object(self.service, '_store_result', side_effect=fail_once):
                self.service.refresh()
            self.service.refresh()
            self.assertEqual([c.kwargs['cursor'] for c in reader.call_args_list], [{}, {}])
        source = self.service.sources()['items'][0]
        self.assertEqual(self.service._setting('cursor:' + source['id'], None), page['next_cursor'])
        self.assertEqual(self.service.search()['total'], 1)

    def test_settings_restart_due_and_disabled_scheduler(self):
        self.assertTrue(self.service._updates_due())
        self.service.configure_updates(dict(auto_updates_enabled=False, interval_minutes=31))
        self.service.close()
        self.service = CampusService(Path(self.temp.name))
        self.assertFalse(self.service._updates_due())
        with patch.object(self.service, 'refresh') as refresh:
            self.service.start_updates()
            self.service.stop_updates()
        refresh.assert_not_called()
        self.assertEqual(self.service.update_settings()['interval_minutes'], 31)
        self.assertFalse(self.service.update_settings()['running'])
        self.service.configure_updates(dict(auto_updates_enabled=True))
        with patch('integrations.wechat.official_accounts.get_cached_official_articles', return_value=dict(items=[], next_cursor={}, has_more=False)):
            self.service.refresh()
        self.service.close()
        self.service = CampusService(Path(self.temp.name))
        self.assertFalse(self.service._updates_due())

    def test_update_setting_validation(self):
        for values in ({'batch_limit': 21}, {'interval_minutes': True}, {'auto_updates_enabled': 1}, {'password': 'x'}):
            with self.assertRaises(ValueError):
                self.service.configure_updates(values)

    def test_batch_is_bounded_and_scheduler_respects_revoked_capability(self):
        articles = [dict(ARTICLE, url=f'https://mp.weixin.qq.com/s/batch-{i}') for i in range(5)]
        self.service.import_local_wechat(CONTACT, dict(items=articles))
        self.service.configure_updates(dict(batch_limit=2))
        with patch('integrations.wechat.official_accounts.get_cached_official_articles', return_value=dict(items=[], next_cursor={}, has_more=False)), \
             patch('integrations.campus.adapter.fetch_wechat_article', return_value=dict(status='error', items=[])) as fetch:
            status = self.service.refresh()
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(status['body_failed'], 2)
        called = threading.Event()
        def revoked():
            called.set()
            return False
        self.service.configure_updates(dict(auto_updates_enabled=True))
        with patch.object(self.service, 'refresh') as refresh:
            self.service.start_updates(revoked)
            self.assertTrue(called.wait(2))
            self.service.stop_updates()
        refresh.assert_not_called()
