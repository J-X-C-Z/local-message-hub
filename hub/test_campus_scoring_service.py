"""Persistent Westlake scores stay fresh without model calls on read paths."""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hub.campus import CampusService
from hub.campus_scoring import MODELS, PROMPT_VERSION, fingerprint
from hub.server import Hub, Handler


def completed(item, profile):
    return dict(status='completed', fingerprint=fingerprint(item, profile),
                models=[dict(model=model, returned_model=model.rsplit('/', 1)[-1], score=90,
                             eligible=True, reasons=['本科生可以办理']) for model in MODELS],
                score=90, selected=True, scored_at='2026-10-23T01:00:00+00:00',
                material_basis='summary_only', prompt_version=PROMPT_VERSION)


def populate(service, count=1, source=None):
    source = source or next(s for s in service.sources()['items'] if s.get('enabled') and s.get('kind') == 'feed')
    service._store_result(source, dict(status='ok', items=[dict(
        url=f'https://jwb.sysu.edu.cn/info/1001/{i}.htm', title=f'本科奖学金申请通知 {i}',
        summary=f'本科生请在10月30日前申请奖学金，材料编号 {i}。',
        published_at='2026-10-23', content_status='summary_only') for i in range(count)]))
    return source


@contextmanager
def fixture(count=1):
    with tempfile.TemporaryDirectory() as storage:
        service = CampusService(Path(storage))
        source = populate(service, count)
        service._save_setting('scoring', {'enabled': True})
        try:
            yield service, source
        finally:
            service.close()


class CampusScoringServiceTest(unittest.TestCase):
    def test_disabling_source_during_a_batch_stops_further_requests(self):
        with fixture(3) as (service, source):
            def disable(item, profile):
                service.configure_source(source['id'], {'enabled': False})
                return completed(item, profile)
            with patch('hub.campus_scoring.score_pair', side_effect=disable) as remote:
                service._score_pending()
            self.assertEqual(remote.call_count, 1)

    def test_selected_group_shows_the_report_whose_model_scores_selected_it(self):
        from hub.campus_briefing import build_briefing
        source = dict(id='official', name='学校', url='https://jwb.sysu.edu.cn/')
        other = dict(id='wechat', name='公众号', kind='local_wechat', account_id='a', contact_id='b')
        common = dict(title='本科奖学金申请通知', summary='本科生请申请奖学金', published_at='2026-10-23')
        rejected = dict(common, id='a', source_id='official', url='https://jwb.sysu.edu.cn/a')
        accepted = dict(common, id='b', source_id='wechat', url='https://mp.weixin.qq.com/s/b')
        rejected['scoring'] = dict(status='completed', score=40, selected=False)
        accepted['scoring'] = dict(status='completed', score=90, selected=True)
        result = build_briefing([rejected, accepted], [source, other], {}, {}, scoring={'enabled': True})
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['representative_id'], 'b')
        self.assertEqual(result['items'][0]['score'], 90)

    def test_completed_scores_survive_reopen_and_reads_never_call_models(self):
        with tempfile.TemporaryDirectory() as storage:
            service = CampusService(Path(storage))
            populate(service)
            service._save_setting('scoring', {'enabled': True})
            with patch('hub.campus_scoring.score_pair', side_effect=completed) as remote:
                service._score_pending()
                self.assertEqual(remote.call_count, 1)
            item_id = service.search()['items'][0]['id']
            service.set_item_state(item_id, {'bookmarked': True, 'read': True})
            service.close()
            with patch('hub.campus_scoring.score_pair', side_effect=AssertionError('read attempted network')) as remote:
                service = CampusService(Path(storage))
                try:
                    item = service.get_item(item_id)
                    self.assertTrue(item['bookmarked'])
                    self.assertTrue(item['read'])
                    self.assertEqual(item['scoring']['status'], 'completed')
                    self.assertEqual(service.briefing()['items'][0]['score'], 90)
                    self.assertEqual(service.scoring_status()['completed'], 1)
                    remote.assert_not_called()
                finally:
                    service.close()

    def test_content_profile_and_beijing_date_invalidate_cached_scores(self):
        with fixture() as (service, source), patch('hub.campus_scoring._today', return_value='2026-10-23'):
            with patch('hub.campus_scoring.score_pair', side_effect=completed):
                service._score_pending()
            item = service.search()['items'][0]
            self.assertEqual(item['scoring']['status'], 'completed')
            service.configure_profile({'campus': '东校园'})
            self.assertEqual(service.get_item(item['id'])['scoring']['status'], 'pending')
            with patch('hub.campus_scoring.score_pair', side_effect=completed):
                service._score_pending()
            with patch('hub.campus_scoring._today', return_value='2026-10-24'):
                self.assertEqual(service.scoring_status()['pending'], 1)
            service._store_result(source, dict(status='ok', items=[dict(item, summary='变更后奖学金申请材料要求')]))
            self.assertEqual(service.get_item(item['id'])['scoring']['status'], 'pending')
            self.assertEqual(service.briefing()['total'], 0)

    def test_disabled_sources_are_skipped_and_batch_has_at_most_five_articles(self):
        with fixture(8) as (service, source):
            service.configure_source(source['id'], {'enabled': False})
            with patch('hub.campus_scoring.score_pair', side_effect=completed) as remote:
                service._score_pending()
                remote.assert_not_called()
                service.configure_source(source['id'], {'enabled': True})
                service._score_pending()
                self.assertEqual(remote.call_count, 5)
                self.assertEqual(service.scoring_status()['completed'], 5)
                self.assertEqual(service.scoring_status()['pending'], 3)

    def test_profile_changed_during_remote_call_discards_result(self):
        with fixture() as (service, source):
            def changed(item, profile):
                result = completed(item, profile)
                service.configure_profile({'campus': '东校园'})
                return result
            with patch('hub.campus_scoring.score_pair', side_effect=changed):
                service._score_pending()
            self.assertEqual(service._scoring_cache(), {})
            self.assertEqual(service.scoring_status()['pending'], 1)
            self.assertEqual(service.briefing()['total'], 0)

    def test_content_changed_during_remote_call_discards_result(self):
        with fixture() as (service, source):
            def changed(item, profile):
                result = completed(item, profile)
                service._store_result(source, dict(status='ok', items=[dict(item, summary='更新后报名条件')]))
                return result
            with patch('hub.campus_scoring.score_pair', side_effect=changed):
                service._score_pending()
            self.assertEqual(service._scoring_cache(), {})
            self.assertEqual(service.scoring_status()['pending'], 1)

    def test_pair_failure_stays_unselected_and_automatic_retry_obeys_cooldown(self):
        with fixture() as (service, source):
            with patch('hub.campus_scoring.score_pair', side_effect=ValueError('secret provider body')) as remote:
                service._score_pending()
                self.assertEqual(remote.call_count, 1)
                service._score_pending(automatic=True)
                self.assertEqual(remote.call_count, 1)
            item = service.briefing(section='all')['items'][0]
            self.assertEqual(item['scoring']['status'], 'error')
            self.assertEqual(item['score'], 0)
            self.assertFalse(item['selected'])
            self.assertNotIn('secret', json.dumps(service.scoring_status()))
            self.assertNotIn('secret', json.dumps(item['scoring']))

    def test_fresh_stdio_mcp_reads_cached_scores_without_network_or_jobs(self):
        with fixture() as (service, source):
            with patch('hub.campus_scoring.score_pair', side_effect=completed):
                service._score_pending()
            storage = str(Path(service._db.execute('PRAGMA database_list').fetchone()[2]).parent)
            messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'scoring-test', 'version': '1'}}},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
                    'name': 'get_campus_briefing', 'arguments': {'section': 'selected'}}}]
            script = '''import sys
from unittest.mock import patch
from hub.mcp_server import serve
with patch('hub.campus_scoring.score_pair', side_effect=AssertionError('no network')), patch('hub.campus.CampusService.start_scoring', side_effect=AssertionError('no jobs')), patch('urllib.request.urlopen', side_effect=AssertionError('no network')):
    serve(storage=sys.argv[1])
'''
            process = subprocess.run([sys.executable, '-c', script, storage],
                input=''.join(json.dumps(message) + '\n' for message in messages),
                encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=Path(__file__).resolve().parents[1], timeout=15)
            self.assertEqual(process.returncode, 0, process.stderr)
            responses = [json.loads(line) for line in process.stdout.splitlines()]
            self.assertEqual(len(responses), 2)
            self.assertFalse(responses[1]['result'].get('isError', False))
            text = json.dumps(responses[1], ensure_ascii=False)
            self.assertIn('westlake-hpc/deepseek-flash', text)
            self.assertIn('westlake-hpc/glm-flash', text)
            self.assertIn('completed', text)

    def test_http_scoring_routes_validate_parameters_and_capability(self):
        with tempfile.TemporaryDirectory() as storage:
            hub = Hub(storage, resume_imports=False)
            service = hub.campus_service()
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.hub = hub
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def request(path, body=None):
                payload = json.dumps(body).encode() if body is not None else None
                with urlopen(Request(f'http://127.0.0.1:{server.server_port}' + path,
                                     data=payload, headers={'Content-Type': 'application/json'}), timeout=3) as response:
                    return json.load(response)
            try:
                self.assertFalse(request('/api/campus/scoring')['enabled'])
                for body in ({}, {'enabled': 1}, {'enabled': True, 'model': 'other'}):
                    with self.assertRaises(HTTPError) as error:
                        request('/api/campus/scoring', body)
                    self.assertEqual(error.exception.code, 400)
                with self.assertRaises(HTTPError) as error:
                    request('/api/campus/scoring/run', {})
                self.assertEqual(error.exception.code, 400)
                with patch.object(service, 'start_scoring', return_value={'running': True}) as start:
                    self.assertTrue(request('/api/campus/scoring', {'enabled': True})['enabled'])
                    self.assertTrue(request('/api/campus/scoring/run', {})['running'])
                    self.assertEqual(start.call_count, 2)
                    with self.assertRaises(HTTPError) as error:
                        request('/api/campus/scoring/run', {'force': True})
                    self.assertEqual(error.exception.code, 400)
                hub.settings['capabilities']['campus'] = False
                for path, body in [('/api/campus/scoring', None), ('/api/campus/scoring', {'enabled': False}), ('/api/campus/scoring/run', {})]:
                    with self.assertRaises(HTTPError) as error:
                        request(path, body)
                    self.assertEqual(error.exception.code, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                hub.close()


if __name__ == '__main__':
    unittest.main()
