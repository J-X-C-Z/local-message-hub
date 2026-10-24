"""Selected official accounts only; synthetic index and no remote requests."""
from contextlib import closing
import tempfile
import unittest
from unittest.mock import patch

from hub.server import Hub


class LocalCampusWeChatTest(unittest.TestCase):
    def test_discovery_import_dedup_and_source_persistence(self):
        with tempfile.TemporaryDirectory() as storage, closing(Hub(storage, resume_imports=False)) as hub:
            hub.store.ingest('wechat', 'synthetic', [], [], contacts=[
                {'id':'gh_official', 'name':'中山大学', 'kind':'official', 'alias':'synthetic-public'},
                {'id':'friend', 'name':'中山大学私人联系人', 'kind':'contact'}])
            accounts = hub.campus_wechat_accounts()['items']
            self.assertEqual([a['id'] for a in accounts], ['gh_official'])
            self.assertFalse(accounts[0]['selected'])
            article = {'url':'https://mp.weixin.qq.com/s/test', 'title':'选课通知',
                       'published_at':'', 'received_at':1792713600, 'summary':'推送摘要',
                       'content_status':'summary_only'}
            with patch('integrations.wechat.official_accounts.get_cached_official_articles',
                       return_value={'items':[article], 'total':1, 'invalid':0}) as reader:
                result = hub.import_campus_wechat_accounts({'account_id':'synthetic','contact_ids':['gh_official']})
                self.assertEqual(result['added'], 1)
                result = hub.import_campus_wechat_accounts({'account_id':'synthetic','contact_ids':['gh_official']})
                self.assertEqual(result['added'], 0)
                reader.assert_called_with('synthetic','gh_official',limit=500)
            item = hub.campus_service().search(q='选课')['items'][0]
            self.assertEqual(item['content_status'], 'summary_only')
            self.assertEqual(item['published_at'], '')
            accounts = hub.campus_wechat_accounts()['items']
            self.assertTrue(accounts[0]['selected'])
            self.assertEqual(accounts[0]['article_count'],1)

    def test_reject_other_account_private_contact_and_unknown_before_source_access(self):
        with tempfile.TemporaryDirectory() as storage, closing(Hub(storage, resume_imports=False)) as hub:
            hub.store.ingest('wechat','synthetic',[],[], contacts=[{'id':'gh_ok','name':'康乐解学','kind':'official'}])
            for body in ({'account_id':'other','contact_ids':['gh_ok']},
                         {'account_id':'synthetic','contact_ids':['gh_missing']},
                         {'account_id':'synthetic','contact_ids':['friend']},
                         {'account_id':'synthetic','contact_ids':[]}):
                with self.assertRaises(ValueError):
                    hub.import_campus_wechat_accounts(body)


if __name__ == '__main__':
    unittest.main()
