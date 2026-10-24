"""HTTP and fresh stdio acceptance against a disposable campus index."""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from hub.server import Hub, Handler
from hub.mcp_server import MCPServer


@contextmanager
def fixture():
    with tempfile.TemporaryDirectory() as storage:
        hub = Hub(storage, resume_imports=False)
        service = hub.campus_service()
        source = dict(id='fixture', name='电子学院', category='student')
        service._store_result(source, {'status': 'ok', 'items': [dict(
            url='https://seit.sysu.edu.cn/article/123', title='本科奖学金申请通知',
            published_at='2026-10-23', summary='合成测试，不是真实申请公告', campus='南校园')]})
        item = service.search()['items'][0]
        try:
            yield hub, item
        finally:
            hub.close()


class CampusIntegrationTest(unittest.TestCase):
    def test_graduate_level_matches_without_a_keyword_or_faculty(self):
        from hub.campus import CampusService
        profile = dict(campus='', next_campus='', faculty='', level='硕士', keywords=[])
        reasons = CampusService._relevance({'title': '研究生课程通知', 'level': '研究生'}, profile)
        self.assertIn('培养层次: 研究生', reasons)

    def test_http_queries_profile_state_and_capability(self):
        with fixture() as (hub, item):
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.hub = hub
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            def request(path, body=None):
                payload = json.dumps(body).encode() if body is not None else None
                req = Request(f'http://127.0.0.1:{server.server_port}' + path,
                              data=payload, headers={'Content-Type': 'application/json'})
                with urlopen(req, timeout=3) as response:
                    return json.load(response)
            try:
                self.assertEqual(request('/api/campus/items?relevant=1')['total'], 1)
                briefing = request('/api/campus/briefing?section=all')
                self.assertEqual(briefing['total'], 1)
                self.assertEqual(briefing['items'][0]['representative_id'], item['id'])
                source_id = next(s['id'] for s in request('/api/campus/sources')['items'] if s.get('kind') == 'feed')
                result = request('/api/campus/sources/' + source_id, {'enabled': False})
                self.assertFalse(next(s for s in request('/api/campus/sources')['items'] if s['id'] == source_id)['enabled'])
                with self.assertRaises(HTTPError):
                    request('/api/campus/sources/' + source_id, {'enabled': 'false'})
                with self.assertRaises(HTTPError):
                    request('/api/campus/briefing?section=unknown')
                self.assertEqual(request('/api/campus/items/' + item['id'])['title'], item['title'])
                state = request('/api/campus/items/' + item['id'] + '/state', {'bookmarked': True})
                self.assertTrue(state['bookmarked'])
                event_state = request('/api/campus/events/' + item['id'] + '/state', {'read': True})
                self.assertTrue(request('/api/campus/items/' + item['id'])['read'])
                with self.assertRaises(HTTPError):
                    request('/api/campus/events/' + item['id'] + '/state', {'read': 'yes'})
                self.assertEqual(request('/api/campus/items?bookmarked=1')['total'], 1)
                self.assertEqual(request('/api/campus/profile', {'next_campus': '东校园'})['next_campus'], '东校园')
                self.assertTrue(request('/api/campus/updates')['auto_updates_enabled'])
                self.assertFalse(request('/api/campus/updates', {'auto_updates_enabled': False})['auto_updates_enabled'])
                with self.assertRaises(HTTPError) as error:
                    request('/api/campus/updates', {'interval_minutes': 0})
                self.assertEqual(error.exception.code, 400)
                with patch.object(hub.campus_service(), 'ensure_full_text', return_value=item) as fetched:
                    self.assertEqual(request('/api/campus/items/' + item['id'] + '/fetch', {})['id'], item['id'])
                    fetched.assert_called_once_with(item['id'])
                    with self.assertRaises(HTTPError):
                        request('/api/campus/items/' + item['id'] + '/fetch', {'unknown': True})
                with patch.object(hub.campus_service(), 'import_wechat', return_value=item) as imported:
                    request('/api/campus/wechat', {'url': 'https://mp.weixin.qq.com/s/test'})
                    imported.assert_called_once()
                hub.settings['capabilities']['campus'] = False
                for path, body in [('/api/campus/items', None), ('/api/campus/refresh', {}),
                                   ('/api/campus/updates', None), ('/api/campus/updates', {}),
                                   ('/api/campus/briefing', None),
                                   ('/api/campus/sources/' + source_id, {'enabled': True}),
                                   ('/api/campus/events/' + item['id'] + '/state', {'read': False}),
                                   ('/api/campus/items/' + item['id'] + '/fetch', {})]:
                    with self.assertRaises(HTTPError) as error:
                        request(path, body)
                    self.assertEqual(error.exception.code, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_mcp_fresh_process_reads_same_campus_index(self):
        with fixture() as (hub, item):
            messages = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'campus-test', 'version': '1'}}},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'search_campus_information', 'arguments': {'query': '奖学金', 'relevant': True}}},
                {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'get_campus_information', 'arguments': {'item_id': item['id']}}},
                {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call', 'params': {'name': 'list_campus_sources', 'arguments': {}}},
                {'jsonrpc': '2.0', 'id': 6, 'method': 'tools/call', 'params': {'name': 'get_campus_briefing', 'arguments': {'section': 'all', 'query': '奖学金'}}},
            ]
            process = subprocess.run([sys.executable, '-m', 'hub.mcp_server', '--storage', str(hub.storage)],
                input=''.join(json.dumps(m, ensure_ascii=False) + '\n' for m in messages),
                encoding='utf-8', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=Path(__file__).resolve().parents[1], timeout=15)
            self.assertEqual(process.returncode, 0, process.stderr)
            responses = [json.loads(line) for line in process.stdout.splitlines()]
            self.assertEqual(len(responses), 6)
            tools = {t['name'] for t in responses[1]['result']['tools']}
            self.assertIn('search_campus_information', tools)
            self.assertIn('get_campus_briefing', tools)
            for response in responses[2:]:
                self.assertNotIn('error', response)
                self.assertFalse(response['result'].get('isError', False))
            self.assertIn('本科奖学金申请通知', json.dumps(responses[2], ensure_ascii=False))
            server = MCPServer(hub.store, campus_reader=hub.campus_service,
                               capability_reader=lambda: hub.settings['capabilities'])
            with self.assertRaises(ValueError):
                server.call_tool('search_campus_information', {'relevant': 'yes'})
            hub.settings['capabilities']['campus'] = False
            with self.assertRaises((ValueError, PermissionError)):
                server.call_tool('list_campus_sources', {})
            with self.assertRaises((ValueError, PermissionError)):
                server.call_tool('get_campus_briefing', {'section': 'all'})


if __name__ == '__main__':
    unittest.main()
