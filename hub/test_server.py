import csv
import base64
import json
import hashlib
import http.client
import io
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from datetime import date
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from http.server import ThreadingHTTPServer
from zipfile import ZipFile

from hub.server import (_ConfiguredWeComBotSender, Handler, Hub, message_body,
                        _LazyWeChatSender, message_emoji_md5, message_location, message_quote,
                        qq_login_result)
from integrations.wecom.bot_sender import WeComBotDeliveryUnknown


class FakeSendAdapter:
    def __init__(self):
        self.calls = []

    def readiness(self, capability_enabled=True):
        return {'capability_enabled': bool(capability_enabled), 'state': 'synthetic'}

    def deliver(self, request):
        captured = dict(request)
        media = captured.get('_media_payload')
        if isinstance(media, dict) and media.get('stream') is not None:
            media = {**media, 'data': media['stream'].read()}
            media.pop('stream', None)
            captured['_media_payload'] = media
        self.calls.append(captured)
        return {'message_id': 'synthetic-message'}


class FakeWeChatSender:
    """Stand-in for the WeChat GUI sender: never touches a real Weixin window."""

    def __init__(self, state='ready'):
        self.calls = []
        self.state = state

    def deliver(self, request):
        self.calls.append(dict(request))
        return {'message_id': 'synthetic-message',
                'account_id': request.get('account_id')}

    def readiness(self, capability_enabled=True):
        return {'capability_enabled': bool(capability_enabled), 'window_found': True,
                'window_foreground': False, 'input_available': True,
                'account_id': 'wxid_self0001',
                'state': self.state if capability_enabled else 'capability_disabled'}


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.send_adapter = FakeSendAdapter()
        self.hub = Hub(Path(self.temp.name), send_adapter=self.send_adapter)
        self.hub.store.ingest('wechat', 'account', [{'id': 'room', 'title': 'Test'}], [
            {'id': 'room:1', 'conversation_id': 'room', 'timestamp': 100,
             'type': 'text', 'text': 'hello <script>', 'sender_name': 'Tester'}])
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.hub = self.hub
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.hub.close()
        self.temp.cleanup()

    def request(self, path, headers=None, body=None):
        return urlopen(Request(self.url + path, headers=headers or {}, data=body), timeout=3)

    def index_qq_send_target(self, account_id='12345678', conversation_id='87654321',
                             kind='private'):
        self.hub.store.ingest('qq', account_id, [{
            'id': conversation_id, 'title': 'Synthetic QQ target', 'kind': kind,
        }], [])

    @staticmethod
    def read_sse_block(response):
        lines = []
        while True:
            line = response.readline()
            if not line:
                return lines
            if line == b'\n':
                return lines
            lines.append(line)

    def seed_moments(self, account, posts):
        cache = Path(self.temp.name) / 'sns_cache'
        cache.mkdir(exist_ok=True)
        self.hub.store.replace_moments(account, posts, cache)
        from types import SimpleNamespace
        self.hub.providers['wechat'] = SimpleNamespace(
            reader=SimpleNamespace(wxid=account, account=''), sns_cache_dir=cache)
        return cache

    def test_lazy_wechat_sender_serializes_first_use_and_entire_delivery(self):
        from concurrent.futures import ThreadPoolExecutor

        guard = threading.Lock()
        factory_entered, release_factory = threading.Event(), threading.Event()
        first_entered, second_entered = threading.Event(), threading.Event()
        release_first, duplicate_factory = threading.Event(), threading.Event()
        state = {"factories": 0, "active": 0, "max_active": 0}

        class Sender:
            def deliver(self, request):
                with guard:
                    state["active"] += 1
                    state["max_active"] = max(state["max_active"], state["active"])
                try:
                    if request["request_id"] == "first":
                        first_entered.set()
                        release_first.wait(2)
                    else:
                        second_entered.set()
                    return {"message_id": request["request_id"]}
                finally:
                    with guard:
                        state["active"] -= 1

        def factory():
            with guard:
                state["factories"] += 1
                number = state["factories"]
            if number == 1:
                factory_entered.set()
                release_factory.wait(2)
            else:
                duplicate_factory.set()
            return Sender()

        sender = _LazyWeChatSender(factory)
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(sender.deliver, {"request_id": "first"})
            self.assertTrue(factory_entered.wait(1))
            second_started = threading.Event()

            def deliver_second():
                second_started.set()
                return sender.deliver({"request_id": "second"})

            second = workers.submit(deliver_second)
            try:
                self.assertTrue(second_started.wait(1))
                self.assertFalse(duplicate_factory.wait(0.1))
                release_factory.set()
                self.assertTrue(first_entered.wait(1))
                self.assertFalse(second_entered.wait(0.1))
            finally:
                release_factory.set()
                release_first.set()
            self.assertEqual(first.result(timeout=2), {"message_id": "first"})
            self.assertEqual(second.result(timeout=2), {"message_id": "second"})

        self.assertEqual(state["factories"], 1)
        self.assertEqual(state["max_active"], 1)

    def test_real_store_query_contract(self):
        with self.request('/api/search?q=hello') as response:
            data = json.load(response)
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['items'][0]['conversation_title'], 'Test')

    def test_qq_send_readiness_route_is_explicit_and_reports_disabled_capability(self):
        with patch.object(self.hub.qq_sender, 'readiness', return_value={
                'state': 'capability_disabled', 'capability_enabled': False,
                'endpoint_configured': False, 'endpoint_reachable': False,
                'account_online': None, 'account_id': None, 'nickname': None}) as readiness:
            with self.request('/api/qq/send-readiness') as response:
                result = json.load(response)
        self.assertEqual(result['state'], 'capability_disabled')
        self.assertIn('checked_at', result)
        readiness.assert_called_once_with(False)

    def test_qq_forward_resource_route_uses_exact_indexed_message_scope(self):
        from unittest.mock import Mock
        self.hub.store.ingest('qq', '10001', [{'id': 'peer', 'title': 'Peer'}], [{
            'id': 'peer:parent', 'conversation_id': 'peer', 'timestamp': 1000,
            'type': 'text', 'text': 'forward',
            'roaming_content': {'elements': [{'type': 'forward'}]},
        }])
        provider = SimpleNamespace(expand_forward_resources=Mock(return_value={
            'items': [{'handle': 'opaque_handle_1234567890', 'type': 'image',
                       'name': 'photo.png', 'size': 12, 'mime_type': 'image/*'}],
            'truncated': False,
        }))
        self.hub.providers['qq'] = provider
        request = Request(self.url + '/api/qq/forward-resources',
                          headers={'Content-Type': 'application/json'},
                          data=json.dumps({'account_id': '10001', 'conversation_id': 'peer',
                                           'message_id': 'peer:parent'}).encode())
        with urlopen(request, timeout=3) as response:
            result = json.load(response)
        self.assertEqual(result['items'][0]['handle'], 'opaque_handle_1234567890')
        provider.expand_forward_resources.assert_called_once()
        self.assertEqual(provider.expand_forward_resources.call_args.args[0]['id'], 'peer:parent')

    def test_mcp_qq_forward_attachment_lists_and_reads_scoped_handle(self):
        from unittest.mock import Mock
        self.hub.store.ingest('qq', '10001', [{'id': 'peer', 'title': 'Peer'}], [{
            'id': 'peer:parent', 'conversation_id': 'peer', 'timestamp': 1000,
            'type': 'text', 'roaming_content': {'elements': [{'type': 'forward'}]},
        }])
        media = Path(self.temp.name) / 'qq' / 'forward-cache' / 'photo.png'
        media.parent.mkdir(parents=True)
        media.write_bytes(b'\x89PNG\r\n\x1a\n')
        provider = SimpleNamespace(
            expand_forward_resources=Mock(return_value={'items': [
                {'handle': 'opaque_handle_1234567890', 'type': 'image',
                 'name': '../photo.png', 'size': 8, 'mime_type': 'image/png'}],
                'truncated': True}),
            download_forward_resource=Mock(return_value=(media, 'image/png')))
        self.hub.providers['qq'] = provider
        listing = self.hub.list_qq_forward_attachments('10001', 'peer', 'peer:parent')
        self.assertEqual(listing['items'][0]['file_name'], 'photo.png')
        self.assertEqual(listing['items'][0]['resource_handle'], 'opaque_handle_1234567890')
        self.assertTrue(listing['truncated'])
        with self.assertRaises(ValueError):
            self.hub.read_qq_forward_attachment('10001', 'other', 'peer:parent',
                                                'opaque_handle_1234567890')
        payload, mime, details = self.hub.read_qq_forward_attachment(
            '10001', 'peer', 'peer:parent', 'opaque_handle_1234567890')
        self.assertEqual(payload, b'\x89PNG\r\n\x1a\n')
        self.assertEqual((mime, details['file_name']), ('image/png', 'photo.png'))
        provider.download_forward_resource.assert_called_once_with(
            'opaque_handle_1234567890', self.hub.storage, account_id='10001', peer='peer',
            parent_id='parent', max_bytes=8 * 1024 * 1024)

    def test_qq_forward_resource_route_rejects_unindexed_or_mismatched_message(self):
        request = Request(self.url + '/api/qq/forward-resources',
                          headers={'Content-Type': 'application/json'},
                          data=json.dumps({'account_id': '10001', 'conversation_id': 'other',
                                           'message_id': 'peer:missing'}).encode())
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=3)
        self.assertEqual(error.exception.code, 400)

    def test_media_token_registry_is_bounded(self):
        from hub.server import MEDIA_TOKEN_LIMIT
        oldest = self.hub.register_media_path(Path('oldest.png'))
        for index in range(MEDIA_TOKEN_LIMIT):
            newest = self.hub.register_media_path(Path(f'{index}.png'))
        self.assertEqual(len(self.hub.media_paths), MEDIA_TOKEN_LIMIT)
        self.assertNotIn(oldest, self.hub.media_paths)
        self.assertEqual(self.hub.media_paths[newest], Path(f'{MEDIA_TOKEN_LIMIT - 1}.png'))

    def test_wecom_snapshot_released_only_after_successful_ingest(self):
        from unittest.mock import Mock
        import types
        import sys
        provider = Mock(engine=None)
        provider.list_conversations.return_value = []
        provider.iter_message_batches.return_value = [[{
            'message_id': 'synthetic', 'conversation_id': 'room',
            'send_time': 100, 'text': 'synthetic'}]]
        module = types.ModuleType('integrations.wecom.adapter')
        module.WeComAdapter = lambda: provider
        with patch.dict(sys.modules, {'integrations.wecom.adapter': module}):
            self.hub.sync_wecom()
        provider.release_message_cache.assert_called_once_with()
        self.assertEqual(self.hub.store.get_message('wecom', 'local', 'synthetic')['text'], 'synthetic')
        provider.release_message_cache.reset_mock()
        with patch.dict(sys.modules, {'integrations.wecom.adapter': module}), \
                patch.object(self.hub.store, 'ingest', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):
                self.hub.sync_wecom()
        provider.release_message_cache.assert_not_called()

    def test_source_polling_does_not_generate_reports(self):
        with patch.object(self.hub.store, 'stats', side_effect=AssertionError('expensive report')):
            sources = self.hub.source_info()['sources']
        self.assertEqual(sources[0]['message_count'], 1)
        self.assertEqual(sources[0]['conversation_count'], 1)
        self.assertEqual(sources[1]['message_count'], 0)

    def test_wechat_avatar_database_status_is_exposed_without_row_access(self):
        from unittest.mock import Mock
        import sys
        import types

        provider = Mock()
        reader = Mock()
        reader.wxid = 'synthetic'
        reader.account = 'synthetic_1234'
        provider.reader = reader
        provider.contacts.return_value = []
        provider.sessions.return_value = []
        provider.status.return_value = {
            'state': 'connected',
            'avatar_database': {'state': 'available',
                                'message': '头像数据库密钥可用；同步时读取本机头像'},
        }
        self.hub.providers['wechat'] = provider
        module = types.ModuleType('integrations.wechat.provider')
        module.WeChatProvider = lambda: provider
        with patch.dict(sys.modules, {'integrations.wechat.provider': module}):
            self.hub.sync_wechat()
        source = next(item for item in self.hub.source_info()['sources'] if item['id'] == 'wechat')
        self.assertEqual(source['avatar_database']['state'], 'available')
        self.assertEqual(source['avatar_database']['message'],
                         '头像数据库密钥可用；同步时读取本机头像')
        reader._open.assert_not_called()

    def test_wechat_local_avatar_overrides_remote_url_and_keeps_unmatched_url(self):
        import sys
        import types
        from types import SimpleNamespace

        local_url = 'hub-avatar://wechat/' + ('b' * 64)
        payload = b'\xff\xd8\xffsynthetic-local-avatar'
        requested = []

        class Provider:
            reader = SimpleNamespace(wxid='synthetic-account', account='')

            def contacts(self):
                return [
                    {'id': 'wxid-local', 'name': 'Local',
                     'avatar_url': 'https://wx.qlogo.cn/remote/local'},
                    {'id': 'wxid-remote', 'name': 'Remote',
                     'avatar_url': 'https://wx.qlogo.cn/remote/only'},
                ]

            def avatar_buffers(self, usernames):
                requested.extend(usernames)
                return {'wxid-local': payload}

            def local_avatar_url(self, _username):
                return local_url

            def sessions(self):
                return []

            def status(self):
                return {'state': 'connected'}

        module = types.ModuleType('integrations.wechat.provider')
        module.WeChatProvider = Provider
        with patch.dict(sys.modules, {'integrations.wechat.provider': module}):
            self.hub.sync_wechat()

        contacts = {item['id']: item for item in self.hub.store.contacts('wechat')['items']}
        self.assertEqual(requested, ['wxid-local', 'wxid-remote'])
        self.assertEqual(contacts['wxid-local']['avatar_url'], local_url)
        self.assertEqual(contacts['wxid-remote']['avatar_url'], 'https://wx.qlogo.cn/remote/only')
        self.assertEqual(self.hub.fetch_avatar(local_url), (payload, 'image/jpeg'))

    def test_media_date_bounds_reach_store(self):
        self.hub.store.ingest('wechat', 'account', [{'id': 'room', 'title': 'Test'}], [
            {'id': 'image:1', 'conversation_id': 'room', 'timestamp': 100,
             'type': 'image', 'text': ''},
            {'id': 'image:2', 'conversation_id': 'room', 'timestamp': 86401,
             'type': 'image', 'text': ''},
        ])
        with self.request('/api/media?platform=wechat&start=1970-01-01&end=1970-01-01') as response:
            data = json.load(response)
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['items'][0]['id'], 'image:1')
        self.assertEqual(data['items'][0]['media_entry'], {
            'resource_index': None, 'resource': None, 'type': 'image'})

    def test_media_api_filters_exact_conversation_id(self):
        self.hub.store.ingest('wechat', 'account', [
            {'id': 'room-a', 'title': 'Same title'}, {'id': 'room-b', 'title': 'Same title'}], [
            {'id': 'image-a', 'conversation_id': 'room-a', 'timestamp': 100, 'type': 'image'},
            {'id': 'image-b', 'conversation_id': 'room-b', 'timestamp': 100, 'type': 'image'},
        ])
        with self.request('/api/media?platform=wechat&conversation_id=room-a') as response:
            data = json.load(response)
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['items'][0]['id'], 'image-a')

    def test_media_api_scopes_account_and_exposes_local_account_inventory(self):
        self.hub.store.ingest('wechat', 'other-account', [{'id': 'room', 'title': 'Other'}], [{
            'id': 'other-image', 'conversation_id': 'room', 'timestamp': 100, 'type': 'image'}])
        with self.request('/api/media?platform=wechat&account_id=account') as response:
            data = json.load(response)
        self.assertEqual(data['total'], 0)
        with self.request('/api/media?platform=wechat&account_id=other-account') as response:
            data = json.load(response)
        self.assertEqual([item['id'] for item in data['items']], ['other-image'])
        with self.request('/api/accounts') as response:
            accounts = json.load(response)['items']
        self.assertIn({'platform': 'wechat', 'account_id': 'other-account'}, accounts)

    def test_media_api_paginates_wecom_resources_as_distinct_attachments(self):
        self.hub.store.ingest('wecom', 'account', [{'id': 'room', 'title': 'Mixed'}], [{
            'id': 'message', 'conversation_id': 'room', 'timestamp': 100, 'type': 'image',
            'text': '', 'attachments': [{'name': 'photo.png', 'type': 'image'},
                                        {'name': 'doc.pdf', 'type': 'file'}],
        }])
        with self.request('/api/media?platform=wecom&type=file&limit=1') as response:
            data = json.load(response)
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['items'][0]['media_entry']['resource_index'], 1)
        self.assertEqual(data['items'][0]['media_entry']['resource']['name'], 'doc.pdf')

    def test_qq_cache_gallery_maps_safe_metadata_and_resolves_opaque_resource(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        provider = Mock()
        provider.cached_resources.return_value = {
            'items': [{'resource_id': 'opaque-id', 'name': 'photo.png', 'type': 'image',
                       'mime_type': 'image/png', 'size': 10,
                       'modified_at': '2026-10-01T00:00:00Z'}],
            'total': 1, 'has_more': False, 'available': True, 'account_id': 'qq-account',
        }
        local = Path(self.temp.name) / 'qq-cache-photo.png'
        local.write_bytes(b'qq-photo')
        provider.resolve_cached_resource.return_value = (local, 'photo.png')
        self.hub.providers['qq'] = provider
        self.hub.set_capabilities({'media': True})

        with self.request('/api/media?source=qq-cache&platform=all&type=image&limit=20&q=photo') as response:
            gallery = json.load(response)
        provider.cached_resources.assert_called_once_with(kind='image', query='photo', offset=0, limit=20,
                                                            start_ms=None, end_ms=None)
        self.assertEqual(gallery['items'][0]['extra']['resource_id'], 'opaque-id')
        self.assertEqual(gallery['items'][0]['account_id'], 'qq-account')
        self.assertNotIn('/resources/', json.dumps(gallery))
        self.assertNotIn(str(local), json.dumps(gallery))

        with self.request('/api/media/resolve?resource_kind=qq_cache&platform=qq&account_id=qq-account&resource_id=opaque-id') as response:
            resolved = json.load(response)
        provider.resolve_cached_resource.assert_called_once_with('opaque-id', 'qq-account')
        self.assertRegex(resolved['media_url'], r'^/local-media/[A-Za-z0-9_-]{32,64}$')
        self.assertNotIn(str(local), json.dumps(resolved))

    def test_qq_cached_heic_keeps_image_mime_and_downloads_original_bytes(self):
        from unittest.mock import Mock

        source = Path(self.temp.name) / 'photo.heic'
        payload = b'\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic'
        source.write_bytes(payload)
        provider = Mock()
        provider.resolve_cached_resource.return_value = (source, 'photo.heic')
        self.hub.providers['qq'] = provider
        self.hub.set_capabilities({'media': True})

        with self.request('/api/media/resolve?resource_kind=qq_cache&platform=qq&account_id=qq-account&resource_id=opaque-heic') as response:
            resolved = json.load(response)
        self.assertEqual(resolved['mime_type'], 'image/heic')
        self.assertRegex(resolved['media_url'], r'^/local-media/[A-Za-z0-9_-]{32,64}$')

        with self.request(resolved['media_url']) as response:
            self.assertEqual(response.headers.get_content_type(), 'image/heic')
            self.assertIn('attachment;', response.headers['Content-Disposition'])
            self.assertIn('photo.heic', response.headers['Content-Disposition'])
            self.assertEqual(response.read(), payload)

    def test_qq_cache_gallery_applies_utc8_date_bounds_before_pagination(self):
        from unittest.mock import Mock
        from hub.store import parse_bound

        provider = Mock()
        provider.cached_resources.return_value = {
            'items': [], 'total': 4, 'has_more': False, 'available': True,
            'account_id': 'qq-account',
        }
        self.hub.providers['qq'] = provider
        self.hub.set_capabilities({'media': True})

        with self.request('/api/media?source=qq-cache&platform=qq&start=2026-10-01&end=2026-10-02&offset=2&limit=10') as response:
            result = json.load(response)

        self.assertEqual(result['total'], 4)
        provider.cached_resources.assert_called_once_with(
            kind='', query='', offset=2, limit=10,
            start_ms=parse_bound('2026-10-01') * 1000,
            end_ms=parse_bound('2026-10-02', end=True) * 1000 + 999,
        )

    def test_qq_cache_gallery_surfaces_unsupported_runtime_date_filter(self):
        from unittest.mock import Mock
        from integrations.qq.provider import QQProviderError

        provider = Mock()
        provider.cached_resources.side_effect = QQProviderError(
            'Bundled QCE does not support QQ cache date filtering; rebuild the local runtime')
        self.hub.providers['qq'] = provider
        self.hub.set_capabilities({'media': True})

        with self.assertRaises(HTTPError) as error:
            self.request('/api/media?source=qq-cache&platform=qq&start=2026-10-01')

        self.assertEqual(error.exception.code, 503)
        self.assertEqual(json.loads(error.exception.read())['error'],
                         'Bundled QCE does not support QQ cache date filtering; rebuild the local runtime')

    def test_media_api_unifies_cached_moments_and_issues_same_origin_preview(self):
        cache = Path(self.temp.name) / 'sns_cache'
        cache.mkdir(exist_ok=True)
        photo = cache / 'photo.png'
        photo.write_bytes(b'fixture')
        cache = self.seed_moments('account', [{
            'id': 'post-media', 'username': 'alice', 'nickname': 'Alice',
            'createTime': 300, 'contentDesc': 'local sunset',
            'media': [{'type': 'image', '_local_path': str(Path(self.temp.name) / 'sns_cache' / 'photo.png')}],
        }])
        self.hub.settings['capabilities']['moments'] = True
        with self.request('/api/media?source=all&platform=all&limit=10') as response:
            result = json.load(response)
        moment = next(item for item in result['items'] if item.get('resource_kind') == 'moment')
        media_url = moment['media_entry']['resource']['media_url']
        self.assertRegex(media_url, r'^/local-moment-media/[A-Za-z0-9_-]{32}$')
        self.assertNotIn('_cache_relpath', json.dumps(result))
        with self.request(media_url) as response:
            self.assertEqual(response.read(), b'fixture')

    def test_moments_media_requires_capability(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/media?source=moments')
        self.assertEqual(error.exception.code, 403)

    def test_telegram_is_queryable_but_never_started_as_native_source(self):
        self.hub.store.ingest('telegram', 'account', [{'id': 'tg-room', 'title': 'TG'}], [
            {'id': 'tg-1', 'conversation_id': 'tg-room', 'timestamp': 101,
             'type': 'text', 'text': 'telegram cross search'}])
        with self.request('/api/search?q=telegram%20cross%20search&platform=all') as response:
            result = json.load(response)
        self.assertEqual(result['items'][0]['platform'], 'telegram')
        with self.assertRaisesRegex(ValueError, '未知平台'):
            self.hub.start_sync('telegram')
        self.assertEqual([source['id'] for source in self.hub.source_info()['sources']], ['wechat', 'wecom', 'qq'])

    def test_telegram_http_preview_and_import_job(self):
        payload = json.dumps({
            'id': 7, 'name': 'Imported Telegram', 'type': 'personal_chat',
            'messages': [{'id': 1, 'type': 'message', 'date_unixtime': '1791604800',
                          'from': 'Tester', 'text': 'telegram http fixture'}],
        }).encode()
        headers = {'Content-Type': 'application/octet-stream'}
        with self.request('/api/imports/preview?format=telegram&platform=telegram&account=synthetic', headers, payload) as response:
            preview = json.load(response)
        self.assertEqual(preview['valid'], 1)
        self.assertEqual(preview['platforms'], ['telegram'])
        with self.request('/api/imports?format=telegram&platform=telegram&account=synthetic&filename=chat.json', headers, payload) as response:
            job = json.load(response)
        self.assertEqual(job['valid'], 1)
        for _ in range(100):
            current = self.hub.store.import_job(job['id'])
            if current['state'] in ('completed', 'failed'):
                break
            time.sleep(0.01)
        self.assertEqual(current['state'], 'completed')
        self.assertEqual(self.hub.store.search('telegram http fixture', platform='telegram')['total'], 1)

    def test_search_cursor_endpoint_rejects_filter_mismatch(self):
        rows = [
            {'id': f'cursor-{index}', 'conversation_id': 'room', 'timestamp': 100,
             'type': 'text', 'text': 'cursor endpoint'}
            for index in range(3)
        ]
        self.hub.store.ingest('wechat', 'account', [{'id': 'room', 'title': 'Test'}], rows)
        with self.request('/api/search?q=cursor%20endpoint&limit=1') as response:
            page = json.load(response)
        self.assertTrue(page['next_cursor'])
        with self.request('/api/search?q=cursor%20endpoint&limit=1&cursor=' + page['next_cursor']) as response:
            next_page = json.load(response)
        self.assertNotEqual(page['items'][0]['id'], next_page['items'][0]['id'])
        with self.assertRaises(HTTPError) as result:
            self.request('/api/search?q=different%20filter&limit=1&cursor=' + page['next_cursor'])
        self.assertEqual(result.exception.code, 400)

    def test_overview_reports_live_source_import_and_index_values(self):
        with self.request('/api/stats?platform=all') as response:
            overview = json.load(response)['overview']
        self.assertEqual([source['id'] for source in overview['sources']], ['wechat', 'wecom', 'qq'])
        self.assertEqual(overview['sources'][0]['message_count'], 1)
        self.assertGreater(overview['index_bytes'], 0)
        self.assertEqual(overview['recent_imports'], [])

    def test_private_analytics_and_reports_use_synthetic_index(self):
        self.hub.store.ingest('wechat', 'account', [
            {'id': 'direct-room', 'title': 'Synthetic person', 'kind': 'direct'}], [
            {'id': 'direct-1', 'conversation_id': 'direct-room', 'timestamp': 100,
             'type': 'text', 'text': 'synthetic first', 'sender_id': 'self', 'sender_name': 'Me', 'is_self': True},
            {'id': 'direct-2', 'conversation_id': 'direct-room', 'timestamp': 200,
             'type': 'text', 'text': 'synthetic second', 'sender_id': 'peer', 'sender_name': 'Peer'},
        ])
        with self.request('/api/analytics/private?platform=wechat') as response:
            rows = json.load(response)['items']
        self.assertEqual(rows[0]['conversation_id'], 'direct-room')
        self.assertEqual(rows[0]['message_count'], 2)
        with self.request('/api/reports/annual?platform=wechat&account_id=account&conversation_id=direct-room&year=1970') as response:
            report = json.load(response)
        self.assertEqual(report['total_messages'], 2)
        self.assertEqual(report['top_contacts'], [{'platform': 'wechat', 'name': 'Peer', 'count': 1}])
        self.assertNotIn('text', report['top_contacts'][0])
        self.assertNotIn('sender_id', report['top_contacts'][0])
        with self.request('/api/reports/annual?platform=wechat&account_id=account&conversation_id=direct-room') as response:
            all_time = json.load(response)
        self.assertIsNone(all_time['year'])
        self.assertEqual(all_time['total_messages'], 2)
        with self.request('/api/reports/pair?platform=wechat&account_id=account&conversation_id=direct-room') as response:
            pair = json.load(response)
        self.assertEqual(pair['counterpart_id'], 'peer')
        self.assertIsNone(pair['year'])
        with self.request('/api/reports/pair?platform=wechat&account_id=account&conversation_id=direct-room&year=1970') as response:
            yearly_pair = json.load(response)
        self.assertEqual(yearly_pair['year'], 1970)
        self.assertEqual(yearly_pair['total_messages'], 2)

    def test_self_sent_daily_endpoint_is_scoped_and_gated(self):
        self.hub.store.ingest('qq', 'fixture', [
            {'id': 'room', 'title': 'Synthetic peer', 'kind': 'direct'},
        ], [
            {'id': 'self-day-1', 'conversation_id': 'room', 'timestamp': 1704038400,
             'type': 'text', 'text': 'synthetic only', 'is_self': True},
        ])
        with self.request('/api/analytics/self-sent-daily?platform=qq&account_id=fixture') as response:
            result = json.load(response)
        self.assertEqual(result['total_messages'], 1)
        self.assertEqual(result['items'], [{'date': '2024-01-01', 'count': 1}])
        self.assertNotIn('text', result)
        self.hub.set_capabilities({'advanced_analytics': False})
        with self.assertRaises(HTTPError) as denied:
            self.request('/api/analytics/self-sent-daily?platform=qq&account_id=fixture')
        self.assertEqual(denied.exception.code, 403)

    def test_group_analytics_can_bound_member_payload_without_losing_observed_count(self):
        self.hub.store.ingest('wechat', 'account', [
            {'id': 'group-room', 'title': 'Synthetic group', 'kind': 'group'}], [
            {'id': 'g1', 'conversation_id': 'group-room', 'timestamp': 100,
             'type': 'text', 'text': 'one', 'sender_id': 'bob', 'sender_name': 'Bob'},
            {'id': 'g2', 'conversation_id': 'group-room', 'timestamp': 200,
             'type': 'text', 'text': 'two', 'sender_id': 'bob', 'sender_name': 'Bob'},
            {'id': 'g3', 'conversation_id': 'group-room', 'timestamp': 300,
             'type': 'text', 'text': 'three', 'sender_id': 'carol', 'sender_name': 'Carol'},
        ])
        with self.request('/api/analytics/groups?platform=wechat&member_limit=1') as response:
            result = json.load(response)['items'][0]
        self.assertEqual(len(result['members']), 1)
        self.assertEqual(result['members'][0]['sender_id'], 'bob')
        self.assertEqual(result['observed_member_count'], 2)
        self.assertEqual(result['member_count'], 2)
        self.assertEqual(len(self.hub.analytics.group_profiles('wechat', 'account')[0]['members']), 2)
        with self.request('/api/analytics/groups?platform=wechat&member_limit=0') as response:
            empty_members = json.load(response)['items'][0]
        self.assertEqual(empty_members['members'], [])
        self.assertEqual(empty_members['observed_member_count'], 2)
        with self.request('/api/analytics/groups?platform=wechat&account_id=account&conversation_id=group-room&member_limit=5000&limit=1') as response:
            selected = json.load(response)['items']
        self.assertEqual([item['conversation_id'] for item in selected], ['group-room'])
        self.assertEqual(len(selected[0]['members']), 2)
        with self.assertRaises(HTTPError) as unscoped:
            self.request('/api/analytics/groups?platform=all&conversation_id=group-room&member_limit=5000')
        self.assertEqual(unscoped.exception.code, 400)

    def test_relationship_api_is_independently_gated_and_content_free(self):
        self.hub.store.ingest('qq', 'fixture', [{'id': 'peer', 'title': 'Fixture peer', 'kind': 'direct'}], [
            {'id': 'r-1', 'conversation_id': 'peer', 'timestamp': 1, 'type': 'text',
             'text': 'synthetic secret body', 'sender_id': 'fixture-self', 'is_self': True},
            {'id': 'r-2', 'conversation_id': 'peer', 'timestamp': 2, 'type': 'image',
             'text': 'another synthetic body', 'sender_id': 'fixture-peer', 'is_self': False},
        ])
        with self.assertRaises(HTTPError) as disabled:
            self.request('/api/analytics/relationships?platform=qq&account_id=fixture')
        self.assertEqual(disabled.exception.code, 403)
        self.hub.set_capabilities({'relationship_footprint': True})
        with self.request('/api/analytics/relationships?platform=qq&account_id=fixture') as response:
            rows = json.load(response)['items']
        row = next(item for item in rows if item['conversation_id'] == 'peer')
        self.assertEqual((row['message_count'], row['sent_count'], row['received_count'], row['media_count']),
                         (2, 1, 1, 1))
        self.assertNotIn('text', row)
        with self.request('/api/analytics/relationships/timeline?platform=qq&account_id=fixture&conversation_id=peer') as response:
            timeline = json.load(response)['items']
        self.assertEqual(timeline[0]['message_count'], 2)
        self.assertNotIn('text', timeline[0])
        with self.request('/api/analytics/relationships/achievements?platform=qq&account_id=fixture&conversation_id=peer') as response:
            achievements = json.load(response)
        self.assertTrue(achievements['available'])
        self.assertEqual(achievements['coverage'], '仅基于当前本机已索引记录')
        self.assertNotIn('synthetic secret body', json.dumps(achievements))
        with self.assertRaises(HTTPError) as missing_scope:
            self.request('/api/analytics/relationships/achievements?platform=all&account_id=fixture&conversation_id=peer')
        self.assertEqual(missing_scope.exception.code, 400)

    def test_pair_report_unknown_conversation_is_404(self):
        with self.assertRaises(HTTPError) as result:
            self.request('/api/reports/pair?platform=wechat&account_id=account&conversation_id=missing')
        self.assertEqual(result.exception.code, 404)

    def test_pair_report_rejects_group_conversation_at_http_boundary(self):
        self.hub.store.ingest('wechat', 'account', [{'id': 'report-group', 'title': 'Synthetic group', 'kind': 'group'}], [
            {'id': 'group-message', 'conversation_id': 'report-group', 'timestamp': 101,
             'type': 'text', 'text': 'synthetic group body'},
        ])
        with self.assertRaises(HTTPError) as result:
            self.request('/api/reports/pair?platform=wechat&account_id=account&conversation_id=report-group')
        self.assertEqual(result.exception.code, 400)

    def test_qq_send_prepare_resolves_local_uid_conversation_to_onebot_target(self):
        headers = {'Content-Type': 'application/json'}
        # A local QQNT private conversation is keyed by uid; OneBot only
        # accepts the numeric uin captured when the message was indexed.
        self.hub.store.ingest('qq', '2680455179', [{
            'id': 'u_-PBswiplK-7J7bmaQLA-mA', 'title': 'Local peer', 'kind': 'private',
            'raw': {'peerUid': 'u_-PBswiplK-7J7bmaQLA-mA', 'peerUin': '2747277822'},
        }], [])
        self.hub.set_capabilities({'qq_send': True})
        with self.request('/api/sends/prepare', headers, json.dumps({
            'account_id': '2680455179', 'conversation_type': 'private',
            'conversation_id': 'u_-PBswiplK-7J7bmaQLA-mA', 'text': 'synthetic only',
            'request_id': 'local-uid-send-01'}).encode()) as response:
            prepared = json.load(response)
        self.assertEqual(prepared['status'], 'pending_approval')
        self.assertEqual(prepared['conversation_id'], '2747277822')
        # The indexed uid stays visible for review without becoming the target.
        self.assertEqual(prepared['source_conversation_id'], 'u_-PBswiplK-7J7bmaQLA-mA')
        self.assertEqual(self.send_adapter.calls, [])
        stored = self.hub.send_repository.get('local-uid-send-01')
        self.assertEqual(stored['conversation_id'], '2747277822')

    def test_qq_send_prepare_refuses_unresolvable_local_identity(self):
        headers = {'Content-Type': 'application/json'}
        self.hub.store.ingest('qq', '2680455179', [{
            'id': 'u_-noUin0000000000000', 'title': 'Unknown peer', 'kind': 'private',
            'local_database': True,
        }], [])
        self.hub.set_capabilities({'qq_send': True})
        with patch.object(self.hub, 'qq_local_provider') as provider:
            from integrations.qq.local_provider import LocalQQError
            provider.return_value.send_target.side_effect = LocalQQError(
                'send_target_private_unresolved', '该联系人只有本地 UID，未找到对应 QQ 号')
            with self.assertRaises(HTTPError) as rejected:
                self.request('/api/sends/prepare', headers, json.dumps({
                    'account_id': '2680455179', 'conversation_type': 'private',
                    'conversation_id': 'u_-noUin0000000000000', 'text': 'synthetic only',
                    'request_id': 'unresolved-send-01'}).encode())
        self.assertEqual(rejected.exception.code, 400)
        self.assertIn('QQ 号', json.loads(rejected.exception.read().decode('utf-8'))['error'])
        self.assertEqual(self.send_adapter.calls, [])
        self.assertIsNone(self.hub.send_repository.get('unresolved-send-01'))
    def test_qq_send_prepare_falls_back_to_the_local_uid_mapping(self):
        headers = {'Content-Type': 'application/json'}
        # A conversation indexed from the local database carries no peerUin,
        # so the reader's own uid mapping supplies the numeric target.
        self.hub.store.ingest('qq', '2680455179', [{
            'id': 'u_1v7dkRylD5gexBywVtZIzw', 'title': 'Local peer', 'kind': 'private',
            'local_database': True,
        }], [])
        self.hub.set_capabilities({'qq_send': True})
        provider = SimpleNamespace(send_target=lambda identity, kind: {'conversation_id': '2843004272'})
        with patch.object(self.hub, 'qq_local_provider', return_value=provider):
            with self.request('/api/sends/prepare', headers, json.dumps({
                'account_id': '2680455179', 'conversation_type': 'private',
                'conversation_id': 'u_1v7dkRylD5gexBywVtZIzw', 'text': 'synthetic only',
                'request_id': 'local-map-send-01'}).encode()) as response:
                prepared = json.load(response)
        self.assertEqual(prepared['conversation_id'], '2843004272')
        self.assertEqual(self.send_adapter.calls, [])

    def test_qq_send_prepare_refuses_an_unknown_conversation(self):
        headers = {'Content-Type': 'application/json'}
        self.hub.set_capabilities({'qq_send': True})
        with self.assertRaises(HTTPError) as rejected:
            self.request('/api/sends/prepare', headers, json.dumps({
                'account_id': '2680455179', 'conversation_type': 'private',
                'conversation_id': 'u_absent0000000000000', 'text': 'synthetic only',
                'request_id': 'absent-send-0001'}).encode())
        self.assertEqual(rejected.exception.code, 400)
        self.assertIn('索引', json.loads(rejected.exception.read().decode('utf-8'))['error'])
        self.assertEqual(self.send_adapter.calls, [])
        self.assertIsNone(self.hub.send_repository.get('absent-send-0001'))
    def test_qq_send_prepare_reports_an_unavailable_local_database(self):
        headers = {'Content-Type': 'application/json'}
        self.hub.store.ingest('qq', '2680455179', [{
            'id': 'u_lock0000000000000000', 'title': 'Locked peer', 'kind': 'private',
            'local_database': True,
        }], [])
        self.hub.set_capabilities({'qq_send': True})
        from integrations.qq.local_provider import LocalQQError
        with patch.object(self.hub, 'qq_local_provider') as provider:
            provider.return_value.send_target.side_effect = LocalQQError(
                'database_unavailable', 'locked')
            with self.assertRaises(HTTPError) as rejected:
                self.request('/api/sends/prepare', headers, json.dumps({
                    'account_id': '2680455179', 'conversation_type': 'private',
                    'conversation_id': 'u_lock0000000000000000', 'text': 'synthetic only',
                    'request_id': 'locked-send-0001'}).encode())
        self.assertEqual(rejected.exception.code, 400)
        # A LocalQQError from the reader becomes a 400 with its own message.
        self.assertEqual(json.loads(rejected.exception.read().decode('utf-8'))['error'], 'locked')
        self.assertEqual(self.send_adapter.calls, [])
        self.assertIsNone(self.hub.send_repository.get('locked-send-0001'))
    def test_send_requires_opt_in_then_prepare_requires_local_approval(self):
        headers = {'Content-Type': 'application/json'}
        self.index_qq_send_target()
        payload = {'platform': 'qq', 'account_id': '12345678', 'conversation_type': 'private',
                   'conversation_id': '87654321', 'text': 'synthetic only', 'request_id': 'http-send-0001'}
        with self.assertRaises(HTTPError) as disabled:
            self.request('/api/sends/prepare', headers, json.dumps(payload).encode())
        self.assertEqual(disabled.exception.code, 403)
        self.assertEqual(self.send_adapter.calls, [])
        self.hub.set_capabilities({'qq_send': True})
        with self.request('/api/sends/prepare', headers, json.dumps(payload).encode()) as response:
            pending = json.load(response)
        self.assertEqual(pending['status'], 'pending_approval')
        self.assertEqual(self.send_adapter.calls, [])
        with self.assertRaises(HTTPError) as unconfirmed:
            self.request('/api/sends/http-send-0001/approve', headers, b'{}')
        self.assertEqual(unconfirmed.exception.code, 400)
        self.assertEqual(self.send_adapter.calls, [])
        with self.assertRaises(HTTPError) as fixed_confirmation:
            self.request('/api/sends/http-send-0001/approve', headers,
                         b'{"confirmation":"APPROVE_SEND"}')
        self.assertEqual(fixed_confirmation.exception.code, 400)
        self.assertEqual(self.send_adapter.calls, [])
        with self.request('/api/sends/http-send-0001/approval-challenge') as response:
            challenge = json.load(response)
        with self.assertRaises(HTTPError) as invalid_token:
            self.request('/api/sends/http-send-0001/approve', headers,
                         b'{"approval_token":"wrong"}')
        self.assertEqual(invalid_token.exception.code, 400)
        self.assertEqual(self.send_adapter.calls, [])
        with self.request('/api/sends/http-send-0001/approve', headers,
                          json.dumps({'approval_token': challenge['token']}).encode()) as response:
            sent = json.load(response)
        self.assertEqual(sent['status'], 'sent')
        self.assertEqual(len(self.send_adapter.calls), 1)
        with self.assertRaises(HTTPError) as replay:
            self.request('/api/sends/http-send-0001/approve', headers,
                         json.dumps({'approval_token': challenge['token']}).encode())
        self.assertEqual(replay.exception.code, 400)
        self.assertEqual(len(self.send_adapter.calls), 1)

    def test_qq_send_prepare_requires_an_indexed_numeric_target_with_matching_kind(self):
        headers = {'Content-Type': 'application/json'}
        self.index_qq_send_target(conversation_id='87654321', kind='private')
        self.index_qq_send_target(conversation_id='76543210', kind='group')
        self.hub.set_capabilities({'qq_send': True})
        cases = (
            ('12345678', '99999999', 'private'),
            ('other-account', '87654321', 'private'),
            ('12345678', '87654321', 'group'),
            ('12345678', '76543210', 'private'),
        )
        for index, (account_id, conversation_id, conversation_type) in enumerate(cases):
            request_id = f'qq-indexed-{index:04d}'
            with self.subTest(account_id=account_id, conversation_id=conversation_id,
                              conversation_type=conversation_type):
                with self.assertRaises(HTTPError) as rejected:
                    self.request('/api/sends/prepare', headers, json.dumps({
                        'account_id': account_id, 'conversation_type': conversation_type,
                        'conversation_id': conversation_id, 'text': 'synthetic only',
                        'request_id': request_id,
                    }).encode())
                self.assertEqual(rejected.exception.code, 400)
                self.assertIsNone(self.hub.send_repository.get(request_id))
        self.assertEqual(self.send_adapter.calls, [])

    def test_wecom_bot_send_is_separate_opt_in_and_approval_route(self):
        headers = {'Content-Type': 'application/json'}
        bot = FakeSendAdapter()
        self.hub.wecom_bot_sender.sender = bot
        with patch.object(self.hub.wecom_bot_credentials, 'status') as identity_status, patch.object(
                self.hub.wecom_bot_credentials, 'set') as set_identity, patch.object(
                self.hub.wecom_bot_credentials, 'delete') as delete_identity:
            with self.assertRaises(HTTPError) as identity_disabled:
                self.request('/api/wecom-bot/identity')
            self.assertEqual(identity_disabled.exception.code, 403)
            for payload in (
                    {'bot_id': 'bot_123', 'secret': 'synthetic-secret'},
                    {'remove': True, 'confirmation': 'REMOVE_WECOM_BOT_CREDENTIALS'}):
                with self.assertRaises(HTTPError) as identity_write_disabled:
                    self.request('/api/wecom-bot/identity', headers,
                                 json.dumps(payload).encode())
                self.assertEqual(identity_write_disabled.exception.code, 403)
            identity_status.assert_not_called()
            set_identity.assert_not_called()
            delete_identity.assert_not_called()
        with patch.object(self.hub.wecom_bot_credentials, 'status', return_value={
                'configured': True, 'bot_id': 'bot_123',
                'identity': '企业微信智能机器人（非当前员工身份）'}), patch.object(
                self.hub.wecom_bot_credentials, 'get', return_value={
                    'bot_id': 'bot_123', 'secret': 'synthetic-secret'}):
            payload = {'account_id': 'bot_123', 'conversation_type': 'private',
                       'conversation_id': 'user-1', 'text': 'synthetic only',
                       'request_id': 'wecom-send-0001'}
            with self.assertRaises(HTTPError) as qq_disabled:
                self.request('/api/sends/prepare', headers,
                             json.dumps(payload).encode())
            self.assertEqual(qq_disabled.exception.code, 403)
            with self.assertRaises(HTTPError) as bot_disabled:
                self.request('/api/wecom-bot-sends/prepare', headers,
                             json.dumps(payload).encode())
            self.assertEqual(bot_disabled.exception.code, 403)
            self.hub.set_capabilities({'qq_send': True, 'wecom_bot_send': True})
            with self.request('/api/wecom-bot-sends/prepare', headers,
                              json.dumps(payload).encode()) as response:
                pending = json.load(response)
            self.assertEqual(pending['identity_label'], '企业微信智能机器人（非当前员工身份）')
            self.assertEqual(bot.calls, [])
            with self.assertRaises(HTTPError) as cross_platform:
                self.request('/api/sends/wecom-send-0001')
            self.assertEqual(cross_platform.exception.code, 404)
            with self.request('/api/wecom-bot-sends/wecom-send-0001/approval-challenge') as response:
                challenge = json.load(response)
            with self.request('/api/wecom-bot-sends/wecom-send-0001/approve', headers,
                              json.dumps({'approval_token': challenge['token']}).encode()) as response:
                sent = json.load(response)
            self.assertEqual(sent['status'], 'sent')
            self.assertNotIn('synthetic-secret', json.dumps(sent))
            self.assertEqual(len(bot.calls), 1)
            self.assertEqual(bot.calls[0]['_bot_secret'], 'synthetic-secret')
            self.assertEqual(self.send_adapter.calls, [])

    def test_wecom_bot_prepare_rejects_account_id_not_matching_configured_bot(self):
        headers = {'Content-Type': 'application/json'}
        self.hub.set_capabilities({'wecom_bot_send': True})
        payload = {'account_id': 'bot_other', 'conversation_type': 'private',
                   'conversation_id': 'user-1', 'text': 'synthetic only',
                   'request_id': 'wecom-id-bind-01'}
        with patch.object(self.hub.wecom_bot_credentials, 'status', return_value={
                'configured': True, 'bot_id': 'bot_123',
                'identity': '企业微信智能机器人（非当前员工身份）'}):
            with self.assertRaises(HTTPError) as rejected:
                self.request('/api/wecom-bot-sends/prepare', headers,
                             json.dumps(payload).encode())
        self.assertEqual(rejected.exception.code, 400)
        self.assertIsNone(self.hub.send_repository.get(payload['request_id']))

    def test_wecom_bot_delivery_fails_closed_after_credential_identity_changes(self):
        headers = {'Content-Type': 'application/json'}
        bot = FakeSendAdapter()
        self.hub.wecom_bot_sender.sender = bot
        self.hub.set_capabilities({'wecom_bot_send': True})
        payload = {'account_id': 'bot_123', 'conversation_type': 'private',
                   'conversation_id': 'user-1', 'text': 'synthetic only',
                   'request_id': 'wecom-id-rotate-01'}
        with patch.object(self.hub.wecom_bot_credentials, 'status', return_value={
                'configured': True, 'bot_id': 'bot_123',
                'identity': '企业微信智能机器人（非当前员工身份）'}), patch.object(
                self.hub.wecom_bot_credentials, 'get', return_value={
                    'bot_id': 'bot_123', 'secret': 'synthetic-secret'}) as get_credentials:
            with self.request('/api/wecom-bot-sends/prepare', headers,
                              json.dumps(payload).encode()) as response:
                self.assertEqual(json.load(response)['status'], 'pending_approval')
            with self.request('/api/wecom-bot-sends/wecom-id-rotate-01/approval-challenge') as response:
                challenge = json.load(response)
            get_credentials.return_value = {'bot_id': 'bot_999', 'secret': 'rotated-secret'}
            with self.request('/api/wecom-bot-sends/wecom-id-rotate-01/approve', headers,
                              json.dumps({'approval_token': challenge['token']}).encode()) as response:
                failed = json.load(response)
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['error_code'], 'delivery_error')
        self.assertEqual(bot.calls, [])

    def test_wecom_bot_credential_changes_revoke_existing_approval_tokens(self):
        headers = {'Content-Type': 'application/json'}
        self.index_qq_send_target()
        bot = FakeSendAdapter()
        self.hub.wecom_bot_sender.sender = bot
        self.hub.set_capabilities({'wecom_bot_send': True})
        self.hub.set_capabilities({'qq_send': True})
        qq_payload = {'account_id': '12345678', 'conversation_type': 'private',
                      'conversation_id': '87654321', 'text': 'synthetic only',
                      'request_id': 'qq-keep-on-rotate-01'}
        self.request('/api/sends/prepare', headers,
                     json.dumps(qq_payload).encode()).close()
        with self.request('/api/sends/qq-keep-on-rotate-01/approval-challenge') as response:
            qq_token = json.load(response)['token']
        with patch.object(self.hub.wecom_bot_credentials, 'status', return_value={
                'configured': True, 'bot_id': 'bot_123',
                'identity': '企业微信智能机器人（非当前员工身份）'}), patch.object(
                self.hub.wecom_bot_credentials, 'get', return_value={
                    'bot_id': 'bot_123', 'secret': 'synthetic-secret'}):
            for request_id, identity_body, operation in (
                    ('wecom-rotate-same-id', {'bot_id': 'bot_123', 'secret': 'replacement'}, 'set'),
                    ('wecom-remove-readd', {'remove': True,
                     'confirmation': 'REMOVE_WECOM_BOT_CREDENTIALS'}, 'delete')):
                payload = {'account_id': 'bot_123', 'conversation_type': 'private',
                           'conversation_id': 'user-1', 'text': 'synthetic only',
                           'request_id': request_id}
                self.request('/api/wecom-bot-sends/prepare', headers,
                             json.dumps(payload).encode()).close()
                with self.request(f'/api/wecom-bot-sends/{request_id}/approval-challenge') as response:
                    challenge = json.load(response)
                with patch.object(self.hub.wecom_bot_credentials, operation,
                                  return_value={'configured': operation == 'set',
                                                'bot_id': 'bot_123'}):
                    self.request('/api/wecom-bot/identity', headers,
                                 json.dumps(identity_body).encode()).close()
                with self.assertRaises(HTTPError) as stale:
                    self.request(f'/api/wecom-bot-sends/{request_id}/approve', headers,
                                 json.dumps({'approval_token': challenge['token']}).encode())
                self.assertEqual(stale.exception.code, 400)
            with self.assertRaisesRegex(ValueError, '令牌无效或已过期'):
                self.hub.consume_send_approval_challenge(
                    'qq-keep-on-rotate-01', qq_token, 'wecom_bot')
            self.hub.consume_send_approval_challenge(
                'qq-keep-on-rotate-01', qq_token, 'qq')
            self.assertEqual(bot.calls, [])

    def test_wechat_send_is_separate_opt_in_and_approval_route(self):
        headers = {'Content-Type': 'application/json'}
        wx = FakeWeChatSender()
        self.hub.wechat_sender = wx
        self.hub.send_service.adapters['wechat'] = wx
        payload = {'account_id': 'wxid_self0001', 'conversation_type': 'private',
                   'conversation_id': 'wxid_friend01', 'text': 'synthetic only',
                   'request_id': 'wechat-send-0001'}
        with self.assertRaises(HTTPError) as disabled:
            self.request('/api/wechat-sends/prepare', headers,
                         json.dumps(payload).encode())
        self.assertEqual(disabled.exception.code, 403)
        self.assertEqual(wx.calls, [])
        self.hub.set_capabilities({'wechat_send': True})
        with self.request('/api/wechat-sends/prepare', headers,
                          json.dumps(payload).encode()) as response:
            pending = json.load(response)
        self.assertEqual(pending['status'], 'pending_approval')
        self.assertEqual(pending['identity_label'], '当前登录的本机微信账号（GUI 拟人操作）')
        self.assertEqual(wx.calls, [])
        with self.request('/api/wechat-send-readiness') as response:
            readiness = json.load(response)
        self.assertEqual(readiness['capability_enabled'], True)
        with self.assertRaises(HTTPError) as cross_platform:
            self.request('/api/sends/wechat-send-0001')
        self.assertEqual(cross_platform.exception.code, 403)
        with self.request('/api/wechat-sends/wechat-send-0001') as response:
            self.assertEqual(json.load(response)['request_id'], 'wechat-send-0001')
        with self.request('/api/wechat-sends/wechat-send-0001/approval-challenge') as response:
            challenge = json.load(response)
        with self.assertRaises(HTTPError) as invalid_token:
            self.request('/api/wechat-sends/wechat-send-0001/approve', headers,
                         json.dumps({'approval_token': 'wrong'}).encode())
        self.assertEqual(invalid_token.exception.code, 400)
        self.assertEqual(wx.calls, [])
        with self.request('/api/wechat-sends/wechat-send-0001/approve', headers,
                          json.dumps({'approval_token': challenge['token']}).encode()) as response:
            sent = json.load(response)
        self.assertEqual(sent['status'], 'sent')
        self.assertEqual(sent['message_id'], 'synthetic-message')
        self.assertEqual(len(wx.calls), 1)
        self.assertEqual(wx.calls[0]['account_id'], 'wxid_self0001')
        with self.request('/api/wechat-sends?status=sent&limit=5') as response:
            self.assertIn('wechat-send-0001',
                          [item['request_id'] for item in json.load(response)['items']])
        self.assertEqual(self.send_adapter.calls, [])

    def test_wechat_group_conversation_must_use_the_chatroom_suffix(self):
        headers = {'Content-Type': 'application/json'}
        self.hub.wechat_sender = FakeWeChatSender()
        self.hub.set_capabilities({'wechat_send': True})
        for conversation_id, conversation_type in ((None, 'private'),
                                                  ('12345@chatroom', 'private'),
                                                  ('wxid_friend01', 'group')):
            with self.subTest(conversation_id=conversation_id):
                with self.assertRaises(HTTPError) as rejected:
                    self.request('/api/wechat-sends/prepare', headers,
                                 json.dumps({'account_id': 'wxid_self0001',
                                             'conversation_type': conversation_type,
                                             'conversation_id': conversation_id,
                                             'text': 'synthetic only',
                                             'request_id': 'wechat-chatroom-0001'}).encode())
                self.assertEqual(rejected.exception.code, 400)
        self.assertEqual(self.hub.wechat_sender.calls, [])
        self.assertIsNone(self.hub.send_repository.get('wechat-chatroom-0001'))

    def test_wechat_send_requests_are_isolated_from_other_platforms(self):
        headers = {'Content-Type': 'application/json'}
        self.index_qq_send_target()
        self.hub.set_capabilities({'qq_send': True, 'wechat_send': True})
        self.request('/api/sends/prepare', headers,
                     json.dumps({'platform': 'qq', 'account_id': '12345678',
                                 'conversation_type': 'private', 'conversation_id': '87654321',
                                 'text': 'synthetic only', 'request_id': 'wx-isolate-qq-0001'}).encode()).close()
        wx = FakeWeChatSender()
        self.hub.wechat_sender = wx
        self.request('/api/wechat-sends/prepare', headers,
                     json.dumps({'account_id': 'wxid_self0001', 'conversation_type': 'private',
                                 'conversation_id': 'wxid_friend01', 'text': 'synthetic only',
                                 'request_id': 'wx-isolate-wx-0001'}).encode()).close()
        with self.assertRaises(HTTPError) as qq_cannot_read_wechat:
            self.request('/api/sends/wx-isolate-wx-0001')
        self.assertEqual(qq_cannot_read_wechat.exception.code, 404)
        with self.request('/api/wechat-sends?status=all&limit=10') as response:
            items = json.load(response)['items']
        self.assertEqual([item['request_id'] for item in items], ['wx-isolate-wx-0001'])
        with self.assertRaisesRegex(ValueError, '发送请求不存在'):
            self.hub.send_service.approve_and_deliver('wx-isolate-wx-0001', 'qq')
        self.assertEqual(wx.calls, [])
        self.hub.send_service.reject('wx-isolate-wx-0001', 'wechat')
        self.assertEqual(wx.calls, [])
    def test_disabling_send_capability_revokes_pending_approval_challenges(self):
        headers = {'Content-Type': 'application/json'}
        bot = FakeSendAdapter()
        self.hub.wecom_bot_sender.sender = bot
        cases = (
            ('qq_send', '/api/sends', 'qq-revoke-0001', {
                'platform': 'qq', 'account_id': '12345678',
                'conversation_type': 'private', 'conversation_id': '87654321',
                'text': 'synthetic only',
            }, self.send_adapter),
            ('wecom_bot_send', '/api/wecom-bot-sends', 'bot-revoke-0001', {
                'account_id': 'bot_123', 'conversation_type': 'private',
                'conversation_id': 'user-1', 'text': 'synthetic only',
            }, bot),
        )
        for capability, route, request_id, payload, adapter in cases:
            with self.subTest(capability=capability):
                if capability == 'qq_send':
                    self.index_qq_send_target()
                credential_patches = (
                    patch.object(self.hub.wecom_bot_credentials, 'status', return_value={
                        'configured': True, 'bot_id': 'bot_123',
                        'identity': '企业微信智能机器人（非当前员工身份）'}),
                    patch.object(self.hub.wecom_bot_credentials, 'get', return_value={
                        'bot_id': 'bot_123', 'secret': 'synthetic-secret'}),
                ) if capability == 'wecom_bot_send' else ()
                for credential_patch in credential_patches:
                    credential_patch.start()
                try:
                    self.hub.set_capabilities({capability: True})
                    body = {**payload, 'request_id': request_id}
                    with self.request(f'{route}/prepare', headers,
                                      json.dumps(body).encode()) as response:
                        self.assertEqual(json.load(response)['status'], 'pending_approval')
                    with self.request(f'{route}/{request_id}/approval-challenge') as response:
                        stale = json.load(response)['token']
                    self.hub.set_capabilities({capability: False})
                    self.hub.set_capabilities({capability: True})
                    with self.assertRaises(HTTPError) as rejected:
                        self.request(f'{route}/{request_id}/approve', headers,
                                     json.dumps({'approval_token': stale}).encode())
                    self.assertEqual(rejected.exception.code, 400)
                    self.assertEqual(adapter.calls, [])
                    with self.request(f'{route}/{request_id}/approval-challenge') as response:
                        fresh = json.load(response)['token']
                    with self.request(f'{route}/{request_id}/approve', headers,
                                      json.dumps({'approval_token': fresh}).encode()) as response:
                        self.assertEqual(json.load(response)['status'], 'sent')
                    self.assertEqual(len(adapter.calls), 1)
                    adapter.calls.clear()
                finally:
                    for credential_patch in credential_patches:
                        credential_patch.stop()

    def test_send_approval_rechecks_capability_after_shared_route_gate(self):
        headers = {'Content-Type': 'application/json'}
        bot = FakeSendAdapter()
        cases = (
            ('qq', 'qq_send', '/api/sends', '12345678', '87654321', 'qq-race-approve-01'),
            ('wechat', 'wechat_send', '/api/wechat-sends', 'wxid_account', 'wxid_peer',
             'wechat-race-approve-01'),
            ('wecom_bot', 'wecom_bot_send', '/api/wecom-bot-sends', 'bot_123', 'user-1',
             'wecom-race-approve-01'),
        )
        for platform, capability, route, account_id, conversation_id, request_id in cases:
            self.hub.send_service.adapters[platform] = bot
            self.hub.send_service.prepare({
                'platform': platform, 'account_id': account_id,
                'conversation_type': 'private', 'conversation_id': conversation_id,
                'text': 'synthetic only', 'request_id': request_id,
            })
            self.hub.set_capabilities({capability: True})

            def revoke_after_shared_route_gate(name):
                nonlocal gate_checks
                gate_checks += 1
                if gate_checks == 1:
                    return True
                self.hub.set_capabilities({name: False})
                return False

            gate_checks = 0
            with patch.object(self.hub, 'capability_enabled', side_effect=revoke_after_shared_route_gate):
                with self.assertRaises(HTTPError) as challenge_denied:
                    self.request(f'{route}/{request_id}/approval-challenge')
            self.assertEqual(challenge_denied.exception.code, 403)

            self.hub.set_capabilities({capability: True})
            with self.request(f'{route}/{request_id}/approval-challenge') as response:
                token = json.load(response)['token']

            gate_checks = 0
            with patch.object(self.hub, 'capability_enabled', side_effect=revoke_after_shared_route_gate):
                with self.assertRaises(HTTPError) as approval_denied:
                    self.request(f'{route}/{request_id}/approve', headers,
                                 json.dumps({'approval_token': token}).encode())
            self.assertEqual(approval_denied.exception.code, 403)
            self.assertEqual(self.hub.send_service.status(request_id, platform)['status'], 'pending_approval')

        self.assertEqual(bot.calls, [])

    def test_wecom_bot_attachment_is_local_only_and_requires_review(self):
        bot = FakeSendAdapter()
        self.hub.wecom_bot_sender.sender = bot
        payload = b'\x89PNG\r\n\x1a\nsynthetic image'
        with patch.object(self.hub.wecom_bot_credentials, 'status', return_value={
                'configured': True, 'bot_id': 'bot_123',
                'identity': '企业微信智能机器人（非当前员工身份）'}), patch.object(
                self.hub.wecom_bot_credentials, 'get', return_value={
                    'bot_id': 'bot_123', 'secret': 'synthetic-secret'}):
            headers = {'Content-Type': 'application/octet-stream',
                       'X-Local-Filename': 'photo.png', 'X-Local-Media-Type': 'image'}
            with self.assertRaises(HTTPError) as disabled:
                self.request('/api/wecom-bot-sends/attachment', headers, payload)
            self.assertEqual(disabled.exception.code, 403)
            self.assertEqual(bot.calls, [])
            self.hub.set_capabilities({'wecom_bot_send': True})
            with self.request('/api/wecom-bot-sends/attachment', headers, payload) as response:
                attachment = json.load(response)
            self.assertNotIn('path', attachment)
            with self.request('/api/wecom-bot-sends/prepare', {'Content-Type': 'application/json'},
                              json.dumps({'account_id': 'bot_123', 'conversation_type': 'private',
                                'conversation_id': 'user-1', 'text': '', 'attachment': attachment,
                                'request_id': 'wecom-media-0001'}).encode()) as response:
                pending = json.load(response)
            self.assertEqual(pending['status'], 'pending_approval')
            self.assertEqual(pending['attachment']['filename'], 'photo.png')
            self.assertNotIn('attachment_id', json.dumps(pending))
            self.assertEqual(bot.calls, [])
            with self.request('/api/wecom-bot-sends/wecom-media-0001/attachment-preview') as response:
                self.assertEqual(response.headers.get_content_type(), 'image/png')
                self.assertEqual(response.headers.get('Cache-Control'), 'no-store')
                self.assertEqual(response.headers.get('Content-Disposition'), 'inline')
                self.assertEqual(response.read(), payload)
            with self.request('/api/wecom-bot-sends/wecom-media-0001/approval-challenge') as response:
                challenge = json.load(response)
            with self.request('/api/wecom-bot-sends/wecom-media-0001/approve',
                              {'Content-Type': 'application/json'},
                              json.dumps({'approval_token': challenge['token']}).encode()) as response:
                sent = json.load(response)
            self.assertEqual(sent['status'], 'sent')
            self.assertEqual(bot.calls[0]['_media_payload']['data'], payload)
            self.assertEqual(bot.calls[0]['_media_payload']['type'], 'image')
            self.assertEqual(self.send_adapter.calls, [])
            with self.assertRaises(HTTPError) as expired_preview:
                self.request('/api/wecom-bot-sends/wecom-media-0001/attachment-preview')
            self.assertEqual(expired_preview.exception.code, 404)

    def test_wecom_bot_preview_serves_supported_pending_attachments_safely(self):
        self.hub.set_capabilities({'wecom_bot_send': True})
        attachment = self.hub.wecom_bot_media.save(
            b'GIF89a synthetic image', 'photo.gif', 'image')
        text_request = {'platform': 'wecom_bot', 'account_id': 'bot_123',
                        'conversation_type': 'private', 'conversation_id': 'user-1',
                        'text': 'hello', 'request_id': 'wecom-preview-text-1'}
        self.hub.send_service.prepare(text_request)
        with self.assertRaises(HTTPError) as text_preview:
            self.request('/api/wecom-bot-sends/wecom-preview-text-1/attachment-preview')
        self.assertEqual(text_preview.exception.code, 404)

        media_request = {**text_request, 'text': '', 'attachment': attachment,
                         'request_id': 'wecom-preview-media-1'}
        self.hub.send_service.prepare(media_request)
        with self.request('/api/wecom-bot-sends/wecom-preview-media-1/attachment-preview') as response:
            self.assertEqual(response.headers.get_content_type(), 'image/gif')
            self.assertEqual(response.read(), b'GIF89a synthetic image')
        with patch.object(self.hub.wecom_bot_media, 'load', side_effect=ValueError('bad signature')):
            with self.assertRaises(HTTPError) as corrupt_preview:
                self.request('/api/wecom-bot-sends/wecom-preview-media-1/attachment-preview')
        self.assertEqual(corrupt_preview.exception.code, 400)

        media_cases = [
            ('video', 'movie.mp4', b'\x00\x00\x00\x18ftypisom', 'video/mp4', 'inline'),
            ('voice', 'voice.amr', b'#!AMR\nsynthetic voice', 'application/octet-stream', 'attachment'),
            ('file', '说明.txt', b'synthetic text', 'application/octet-stream', 'attachment'),
        ]
        for index, (media_type, filename, payload, mime, disposition) in enumerate(media_cases):
            request_id = f'wecom-preview-extra-{index}'
            attachment = self.hub.wecom_bot_media.save(payload, filename, media_type)
            self.hub.send_service.prepare({**text_request, 'text': '', 'attachment': attachment,
                                           'request_id': request_id})
            with self.request(f'/api/wecom-bot-sends/{request_id}/attachment-preview') as response:
                self.assertEqual(response.headers.get_content_type(), mime)
                header = response.headers.get('Content-Disposition')
                if disposition == 'inline':
                    self.assertEqual(header, 'inline')
                else:
                    self.assertTrue(header.startswith(disposition + ';'))
                self.assertEqual(response.headers.get('Cache-Control'), 'no-store')
                self.assertEqual(response.headers.get('X-Content-Type-Options'), 'nosniff')
                self.assertEqual(response.read(), payload)

    def test_wecom_bot_attachment_is_removed_when_prepare_fails_before_persistence(self):
        attachment = self.hub.wecom_bot_media.save(
            b'\x89PNG\r\n\x1a\nsynthetic', 'photo.png', 'image')
        path = self.hub.wecom_bot_media.root / (attachment['attachment_id'] + '.blob')
        self.hub.set_capabilities({'wecom_bot_send': True})
        payload = {'account_id': 'bot_123', 'conversation_type': 'private',
                   'conversation_id': 'user-1', 'text': '', 'attachment': attachment,
                   'request_id': 'wecom-prepare-fail-01'}

        with patch.object(self.hub.wecom_bot_credentials, 'status', return_value={
                'configured': True, 'bot_id': 'bot_123',
                'identity': '企业微信智能机器人（非当前员工身份）'}), patch.object(
                self.hub.send_service, 'prepare', side_effect=ValueError('synthetic reject')):
            with self.assertRaises(HTTPError) as rejected:
                self.request('/api/wecom-bot-sends/prepare',
                             {'Content-Type': 'application/json'}, json.dumps(payload).encode())

        self.assertEqual(rejected.exception.code, 400)
        self.assertFalse(path.exists())
        self.assertIsNone(self.hub.send_repository.get(payload['request_id']))

    def test_wecom_bot_attachment_survives_prepare_error_after_persistence(self):
        attachment = self.hub.wecom_bot_media.save(
            b'\x89PNG\r\n\x1a\nsynthetic', 'photo.png', 'image')
        path = self.hub.wecom_bot_media.root / (attachment['attachment_id'] + '.blob')
        self.hub.set_capabilities({'wecom_bot_send': True})
        payload = {'account_id': 'bot_123', 'conversation_type': 'private',
                   'conversation_id': 'user-1', 'text': '', 'attachment': attachment,
                   'request_id': 'wecom-prepare-commit-01'}
        prepare = self.hub.send_service.prepare

        def persist_then_fail(body):
            prepare(body)
            raise RuntimeError('synthetic post-commit failure')

        with patch.object(self.hub.wecom_bot_credentials, 'status', return_value={
                'configured': True, 'bot_id': 'bot_123',
                'identity': '企业微信智能机器人（非当前员工身份）'}), patch.object(
                self.hub.send_service, 'prepare', side_effect=persist_then_fail):
            with self.assertRaises(HTTPError) as failed:
                self.request('/api/wecom-bot-sends/prepare',
                             {'Content-Type': 'application/json'}, json.dumps(payload).encode())

        self.assertEqual(failed.exception.code, 500)
        self.assertTrue(path.is_file())
        self.assertEqual(self.hub.send_repository.get(payload['request_id'])['status'],
                         'pending_approval')

    def test_wecom_bot_attachment_is_removed_when_revalidation_fails(self):
        attachment = self.hub.wecom_bot_media.save(
            b'\x89PNG\r\n\x1a\nsynthetic', 'photo.png', 'image')
        path = self.hub.wecom_bot_media.root / (attachment['attachment_id'] + '.blob')
        path.write_bytes(b'corrupt')
        sender = _ConfiguredWeComBotSender(
            SimpleNamespace(get=lambda: {'bot_id': 'bot_123', 'secret': 'secret'}),
            FakeSendAdapter(), self.hub.wecom_bot_media)

        with self.assertRaises(ValueError):
            sender.deliver({'attachment': attachment, 'account_id': 'bot_123'})

        self.assertFalse(path.exists())

    def test_wecom_bot_attachment_is_removed_when_credentials_are_missing(self):
        attachment = self.hub.wecom_bot_media.save(
            b'\x89PNG\r\n\x1a\nsynthetic', 'photo.png', 'image')
        path = self.hub.wecom_bot_media.root / (attachment['attachment_id'] + '.blob')
        sender = _ConfiguredWeComBotSender(
            SimpleNamespace(get=lambda: None), FakeSendAdapter(), self.hub.wecom_bot_media)

        with self.assertRaisesRegex(RuntimeError, 'wecom_bot_credentials_missing'):
            sender.deliver({'attachment': attachment})

        self.assertFalse(path.exists())

    def test_wecom_media_cleanup_failure_preserves_delivery_outcome(self):
        class SyntheticBot:
            error = None

            def deliver(self, request):
                if self.error:
                    raise self.error
                return {'message_id': 'synthetic-message'}

        bot = SyntheticBot()
        self.hub.wecom_bot_sender.sender = bot
        outcomes = (('sent', None), ('unknown', WeComBotDeliveryUnknown('synthetic_unknown')))
        with patch.object(self.hub.wecom_bot_credentials, 'get', return_value={
                'bot_id': 'bot_123', 'secret': 'synthetic-secret'}), patch.object(
                self.hub.wecom_bot_media, 'remove', side_effect=OSError('synthetic cleanup failure')):
            for index, (expected, error) in enumerate(outcomes):
                bot.error = error
                attachment = self.hub.wecom_bot_media.save(
                    b'GIF89a synthetic image', 'photo.gif', 'image')
                request_id = f'wecom-cleanup-{index:04d}-0001'
                request = self.hub.send_service.prepare({
                    'platform': 'wecom_bot', 'account_id': 'bot_123',
                    'conversation_type': 'private', 'conversation_id': 'user-1',
                    'text': '', 'attachment': attachment, 'request_id': request_id,
                })
                result = self.hub.send_service.approve_and_deliver(request['request_id'], 'wecom_bot')
                self.assertEqual(result['status'], expected)

    def test_send_approval_challenge_expires(self):
        self.index_qq_send_target()
        self.hub.set_capabilities({'qq_send': True})
        headers = {'Content-Type': 'application/json'}
        body = json.dumps({'platform': 'qq', 'account_id': '12345678', 'conversation_type': 'private',
                           'conversation_id': '87654321', 'text': 'synthetic only',
                           'request_id': 'http-expire-0001'}).encode()
        self.request('/api/sends/prepare', headers, body).close()
        with self.request('/api/sends/http-expire-0001/approval-challenge') as response:
            challenge = json.load(response)
        self.hub.send_approval_challenges['http-expire-0001'] = (
            challenge['token'], time.monotonic() - 1, 'qq')
        with self.assertRaises(HTTPError) as expired:
            self.request('/api/sends/http-expire-0001/approve', headers,
                         json.dumps({'approval_token': challenge['token']}).encode())
        self.assertEqual(expired.exception.code, 400)
        self.assertEqual(self.send_adapter.calls, [])

    def test_send_approval_challenge_is_bound_to_request(self):
        self.index_qq_send_target()
        self.hub.set_capabilities({'qq_send': True})
        headers = {'Content-Type': 'application/json'}
        for request_id in ('http-bind-one-0001', 'http-bind-two-0002'):
            body = json.dumps({'platform': 'qq', 'account_id': '12345678',
                               'conversation_type': 'private', 'conversation_id': '87654321',
                               'text': 'synthetic only', 'request_id': request_id}).encode()
            self.request('/api/sends/prepare', headers, body).close()
        with self.request('/api/sends/http-bind-one-0001/approval-challenge') as response:
            challenge = json.load(response)
        with self.assertRaises(HTTPError) as wrong_request:
            self.request('/api/sends/http-bind-two-0002/approve', headers,
                         json.dumps({'approval_token': challenge['token']}).encode())
        self.assertEqual(wrong_request.exception.code, 400)
        self.assertEqual(self.send_adapter.calls, [])

    def test_send_request_can_be_rejected_without_delivery(self):
        self.index_qq_send_target(kind='group')
        self.hub.set_capabilities({'qq_send': True})
        headers = {'Content-Type': 'application/json'}
        body = json.dumps({'platform': 'qq', 'account_id': '12345678', 'conversation_type': 'group',
                           'conversation_id': '87654321', 'text': 'synthetic only',
                           'request_id': 'http-reject-0001'}).encode()
        self.request('/api/sends/prepare', headers, body).close()
        with self.request('/api/sends/http-reject-0001/reject', headers,
                          b'{"confirmation":"REJECT_SEND"}') as response:
            rejected = json.load(response)
        self.assertEqual(rejected['status'], 'rejected')
        self.assertEqual(self.send_adapter.calls, [])

    def test_mcp_settings_expose_read_only_client_config_and_recent_operations(self):
        self.hub.store.record_mcp_operation('search_messages', 'success')
        root = Path(self.temp.name) / '本地消息中心'
        (root / 'hub').mkdir(parents=True)
        (root / 'hub' / 'mcp_server.py').touch()
        with patch('hub.server.ROOT', root):
            with self.request('/api/mcp') as response:
                settings = json.load(response)
        self.assertTrue(settings['available'])
        self.assertEqual(settings['mode'], 'read-only')
        self.assertEqual(settings['transport'], 'stdio')
        self.assertIn('tools:\n      include:', settings['config_text'])
        self.assertIn('- list_media', settings['config_text'])
        self.assertIn('- get_attachment', settings['config_text'])
        self.assertNotIn('- get_moment_media', settings['config_text'])
        self.assertNotIn('- list_relationship_footprint', settings['config_text'])
        self.assertIn('  local-message-hub:', settings['config_text'])
        self.assertIn('    cwd: ', settings['config_text'])
        config_lines = dict(line.strip().split(': ', 1) for line in settings['config_text'].splitlines()
                            if line.strip().startswith(('command:', 'args:', 'cwd:')))
        self.assertEqual(json.loads(config_lines['command']), Path(sys.executable).resolve().as_posix())
        self.assertEqual(json.loads(config_lines['args'])[-1], self.hub.storage.resolve().as_posix())
        self.assertEqual(json.loads(config_lines['cwd']), root.as_posix())
        self.assertEqual(settings['operations'][0]['tool'], 'search_messages')

    def test_mcp_config_maps_global_search_and_source_list_capabilities(self):
        self.hub.set_capabilities({'chat': False, 'mcp': True, 'stats': True})
        with self.request('/api/mcp') as response:
            stats_enabled = json.load(response)['config_text']
        self.assertIn('- search_messages', stats_enabled)
        self.assertIn('- list_sources', stats_enabled)
        self.assertNotIn('- list_conversations', stats_enabled)

        self.hub.set_capabilities({'stats': False})
        with self.request('/api/mcp') as response:
            stats_disabled = json.load(response)['config_text']
        self.assertIn('- search_messages', stats_disabled)
        self.assertNotIn('- list_sources', stats_disabled)

    def test_mcp_write_setting_is_opt_in_and_updates_client_allowlist(self):
        with self.request('/api/mcp') as response:
            disabled = json.load(response)
        self.assertEqual(disabled['mode'], 'read-only')
        self.assertNotIn('update_conversation_metadata', disabled['config_text'])
        with self.request('/api/settings', {'Content-Type': 'application/json'},
                          b'{"mcp_write_enabled":true}') as response:
            settings = json.load(response)
        self.assertTrue(settings['mcp_write_enabled'])
        with self.request('/api/mcp') as response:
            enabled = json.load(response)
        self.assertEqual(enabled['mode'], 'metadata-write')
        self.assertIn('update_conversation_metadata', enabled['config_text'])
        self.assertIn('remove_contact_identity', enabled['config_text'])

    def test_mcp_client_config_exposes_wecom_send_only_with_capability_and_identity(self):
        names = ('prepare_wecom_bot_send', 'get_wecom_bot_send_status')
        self.hub.set_capabilities({'mcp': True, 'wecom_bot_send': True})
        with patch.object(self.hub.wecom_bot_credentials, 'status', return_value={
                'configured': True, 'bot_id': 'synthetic-bot'}):
            with self.request('/api/mcp') as response:
                configured = json.load(response)['config_text']
        for name in names:
            self.assertIn(f'- {name}', configured)

        cases = (
            ({'mcp': True, 'wecom_bot_send': False},
             {'configured': True, 'bot_id': 'synthetic-bot'}),
            ({'mcp': True, 'wecom_bot_send': True}, {'configured': False, 'bot_id': ''}),
        )
        for capabilities, identity in cases:
            with self.subTest(capabilities=capabilities, identity=identity):
                self.hub.set_capabilities(capabilities)
                with patch.object(self.hub.wecom_bot_credentials, 'status', return_value=identity):
                    with self.request('/api/mcp') as response:
                        config = json.load(response)['config_text']
                for name in names:
                    self.assertNotIn(f'- {name}', config)

        self.hub.set_capabilities({'mcp': True, 'wecom_bot_send': True})
        with patch.object(self.hub.wecom_bot_credentials, 'status', side_effect=RuntimeError('offline')):
            with self.request('/api/mcp') as response:
                unavailable = json.load(response)['config_text']
        for name in names:
            self.assertNotIn(f'- {name}', unavailable)

    def test_local_management_endpoints_never_modify_source_messages(self):
        headers = {'Content-Type': 'application/json'}
        payload = json.dumps({'platform': 'wechat', 'account_id': 'account', 'conversation_id': 'room',
                              'note': 'local note', 'tags': ['follow-up'], 'archived': True}).encode()
        with self.request('/api/conversation-metadata', headers, payload) as response:
            self.assertEqual(json.load(response), {'note': 'local note', 'tags': ['follow-up'], 'archived': True})
        with self.request('/api/conversations?platform=wechat') as response:
            conversation = json.load(response)['items'][0]
        self.assertEqual(conversation['note'], 'local note')
        self.assertTrue(conversation['archived'])
        self.assertEqual(self.hub.store.get_message('wechat', 'account', 'room:1')['text'], 'hello <script>')
        with self.request('/api/operations') as response:
            audit = json.load(response)['items']
        self.assertEqual(audit[0]['action'], 'conversation_metadata.update')
        self.assertNotIn('local note', str(audit))

    def test_mcp_image_read_uses_only_an_indexed_local_cache_path(self):
        from unittest.mock import Mock
        from integrations.qq.provider import QQProvider

        image = b'\x89PNG\r\n\x1a\nsynthetic-image'
        cache = Path(self.temp.name) / 'qq' / 'media-cache'
        cache.mkdir(parents=True)
        path = cache / 'cached.png'
        path.write_bytes(image)
        self.hub.store.ingest('qq', 'account', [{'id': 'room', 'title': 'Photos'}], [{
            'id': 'photo', 'conversation_id': 'room', 'timestamp': 100,
            'type': 'image', 'file_name': 'cached.png',
        }])
        provider = Mock()
        provider.cached_media.return_value = {'available': True, 'path': str(path)}
        self.hub.providers['qq'] = provider
        with patch.object(QQProvider, '_media_cache_dir', return_value=cache):
            payload, mime = self.hub.read_cached_image(
                self.hub.store.get_message('qq', 'account', 'photo'))
        self.assertEqual((payload, mime), (image, 'image/png'))
        provider.cached_media.assert_called_once()

    def test_mcp_media_read_rejects_cache_replacement_before_open(self):
        import os
        from unittest.mock import Mock
        from integrations.qq.provider import QQProvider

        image = b'\x89PNG\r\n\x1a\noriginal-image'
        cache = Path(self.temp.name) / 'qq' / 'media-cache'
        cache.mkdir(parents=True)
        path = cache / 'cached.png'
        path.write_bytes(image)
        self.hub.store.ingest('qq', 'account', [{'id': 'room', 'title': 'Photos'}], [{
            'id': 'photo', 'conversation_id': 'room', 'timestamp': 100,
            'type': 'image', 'file_name': 'cached.png',
        }])
        provider = Mock()
        provider.cached_media.return_value = {'available': True, 'path': str(path)}
        self.hub.providers['qq'] = provider
        original_open = Path.open

        def replace_before_open(candidate, mode='r', *args, **kwargs):
            if candidate == path and mode == 'rb':
                replacement = cache / 'replacement.png'
                with original_open(replacement, 'wb') as writer:
                    writer.write(b'\x89PNG\r\n\x1a\nreplacement-image')
                os.replace(replacement, path)
            return original_open(candidate, mode, *args, **kwargs)

        with patch.object(QQProvider, '_media_cache_dir', return_value=cache), \
                patch.object(Path, 'open', new=replace_before_open):
            with self.assertRaisesRegex(ValueError, '读取前发生变化'):
                self.hub.read_cached_image(self.hub.store.get_message('qq', 'account', 'photo'))

    def test_mcp_image_read_returns_cached_bmp_as_image_content(self):
        from unittest.mock import Mock
        from integrations.qq.provider import QQProvider
        from hub.mcp_server import MCPServer

        image = b'BM' + b'\x00' * 12 + b'synthetic-bmp'
        cache = Path(self.temp.name) / 'qq' / 'media-cache'
        cache.mkdir(parents=True)
        path = cache / 'cached.bmp'
        path.write_bytes(image)
        self.hub.store.ingest('qq', 'account', [{'id': 'room', 'title': 'Photos'}], [{
            'id': 'photo', 'conversation_id': 'room', 'timestamp': 100,
            'type': 'image', 'file_name': 'cached.bmp',
        }])
        provider = Mock()
        provider.cached_media.return_value = {'available': True, 'path': str(path)}
        self.hub.providers['qq'] = provider
        mcp = MCPServer(self.hub.store, media_reader=self.hub.read_cached_image)

        with patch.object(QQProvider, '_media_cache_dir', return_value=cache):
            payload, mime = self.hub.read_cached_image(
                self.hub.store.get_message('qq', 'account', 'photo'))
            result = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                'name': 'get_attachment', 'arguments': {
                    'platform': 'qq', 'account_id': 'account', 'message_id': 'photo'}}})['result']

        self.assertEqual((payload, mime), (image, 'image/bmp'))
        self.assertFalse(result['isError'])
        self.assertEqual(result['content'][1]['type'], 'image')
        self.assertEqual(result['content'][1]['mimeType'], 'image/bmp')

    def test_mcp_image_read_accepts_cached_avif_and_heic(self):
        from unittest.mock import Mock
        from integrations.qq.provider import QQProvider
        from hub.mcp_server import MCPServer

        cache = Path(self.temp.name) / 'qq' / 'media-cache'
        cache.mkdir(parents=True)
        fixtures = {
            'avif': (b'avif', 'image/avif'),
            'heic': (b'heic', 'image/heic'),
        }
        messages = []
        for name, (brand, _mime) in fixtures.items():
            payload = b'\x00\x00\x00\x14ftyp' + brand + b'\x00\x00\x00\x00' + brand
            (cache / f'{name}.img').write_bytes(payload)
            messages.append({'id': name, 'conversation_id': 'room', 'timestamp': 100,
                             'type': 'image', 'file_name': f'{name}.img'})
        self.hub.store.ingest('qq', 'account', [{'id': 'room', 'title': 'Photos'}], messages)
        provider = Mock()
        provider.cached_media.side_effect = lambda message, **_kwargs: {
            'available': True, 'path': str(cache / f"{message['id']}.img")}
        self.hub.providers['qq'] = provider
        mcp = MCPServer(self.hub.store, media_reader=self.hub.read_cached_image)

        with patch.object(QQProvider, '_media_cache_dir', return_value=cache):
            for message_id, (_brand, mime) in fixtures.items():
                result = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                    'name': 'get_attachment', 'arguments': {
                        'platform': 'qq', 'account_id': 'account', 'message_id': message_id}}})['result']
                self.assertFalse(result['isError'], result)
                self.assertEqual(result['content'][1]['type'], 'image')
                self.assertEqual(result['content'][1]['mimeType'], mime)

    def test_mcp_image_read_accepts_cached_tiff_and_rejects_lookalike(self):
        from unittest.mock import Mock
        from integrations.qq.provider import QQProvider
        from hub.mcp_server import MCPServer

        cache = Path(self.temp.name) / 'qq' / 'media-cache'
        cache.mkdir(parents=True)
        fixtures = {
            'little-endian': b'II*\x00synthetic-tiff',
            'big-endian': b'MM\x00*synthetic-tiff',
            'big-tiff': b'II+\x00synthetic-tiff',
            'lookalike': b'not-tiff-II*\x00',
        }
        messages = []
        for name, payload in fixtures.items():
            (cache / f'{name}.img').write_bytes(payload)
            messages.append({'id': name, 'conversation_id': 'room', 'timestamp': 100,
                             'type': 'image', 'file_name': f'{name}.img'})
        self.hub.store.ingest('qq', 'account', [{'id': 'room', 'title': 'Photos'}], messages)
        provider = Mock()
        provider.cached_media.side_effect = lambda message, **_kwargs: {
            'available': True, 'path': str(cache / f"{message['id']}.img")}
        self.hub.providers['qq'] = provider
        mcp = MCPServer(self.hub.store, media_reader=self.hub.read_cached_image)

        with patch.object(QQProvider, '_media_cache_dir', return_value=cache):
            for message_id in ('little-endian', 'big-endian', 'big-tiff'):
                result = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                    'name': 'get_attachment', 'arguments': {
                        'platform': 'qq', 'account_id': 'account', 'message_id': message_id}}})['result']
                self.assertFalse(result['isError'], result)
                self.assertEqual(result['content'][1]['mimeType'], 'image/tiff')
            with self.assertRaisesRegex(ValueError, '不是受支持的图片格式'):
                self.hub.read_cached_image(self.hub.store.get_message('qq', 'account', 'lookalike'))

    def test_mcp_image_read_rejects_lookalike_or_malformed_bmff_brands(self):
        from unittest.mock import Mock
        from integrations.qq.provider import QQProvider

        cache = Path(self.temp.name) / 'qq' / 'media-cache'
        cache.mkdir(parents=True)
        fixtures = {
            # The old substring scan mistook the minor-version field for a brand.
            'minor-version': b'\x00\x00\x00\x14ftypxxxxavifxxxx',
            # The ftyp box declares a non-four-byte-aligned compatible-brand tail.
            'misaligned': b'\x00\x00\x00\x11ftypavif\x00\x00\x00\x00xxxx',
        }
        messages = []
        for name, payload in fixtures.items():
            (cache / f'{name}.img').write_bytes(payload)
            messages.append({'id': name, 'conversation_id': 'room', 'timestamp': 100,
                             'type': 'image', 'file_name': f'{name}.img'})
        self.hub.store.ingest('qq', 'account', [{'id': 'room', 'title': 'Photos'}], messages)
        provider = Mock()
        provider.cached_media.side_effect = lambda message, **_kwargs: {
            'available': True, 'path': str(cache / f"{message['id']}.img")}
        self.hub.providers['qq'] = provider

        with patch.object(QQProvider, '_media_cache_dir', return_value=cache):
            for message_id in fixtures:
                with self.subTest(message_id=message_id), self.assertRaisesRegex(
                        ValueError, '不是受支持的图片格式'):
                    self.hub.read_cached_image(
                        self.hub.store.get_message('qq', 'account', message_id))

    def test_mcp_wechat_wxgf_fallback_returns_cached_container_as_resource(self):
        from unittest.mock import Mock
        from hub.mcp_server import MCPServer

        payload = b'wxgf' + bytes((0, 0, 0, 1)) + b'synthetic-hevc'
        runtime = Path(self.temp.name) / 'runtime'
        path = runtime / 'media' / 'fixture' / 'image.wxgf'
        path.parent.mkdir(parents=True)
        path.write_bytes(payload)
        provider = SimpleNamespace(
            reader=SimpleNamespace(wxid='other-account', account='other-account'),
            media=Mock(return_value={'available': True, 'path': str(path)}))
        self.hub.providers['wechat'] = provider
        self.hub.store.ingest('wechat', 'account', [{'id': 'room', 'title': 'Photos'}], [{
            'id': 'wxgf-image', 'conversation_id': 'room', 'timestamp': 100,
            'type': 'image', 'local_id': 7, 'local_type': 3,
            'shard': 'message_0.db', 'file_name': 'image.wxgf',
        }])
        mcp = MCPServer(self.hub.store, media_reader=self.hub.read_cached_image)

        with patch('integrations.wechat.provider.RUNTIME', runtime):
            message = self.hub.store.get_message('wechat', 'account', 'wxgf-image')
            payload_result = self.hub.read_cached_image(message)
            result = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                'name': 'get_attachment', 'arguments': {
                    'platform': 'wechat', 'account_id': 'account',
                    'message_id': 'wxgf-image'}}})['result']

        self.assertEqual(payload_result, (payload, 'application/x-wechat-wxgf'))
        self.assertFalse(result['isError'], result)
        self.assertEqual(result['content'][1]['type'], 'resource')
        attachment = result['content'][1]['resource']
        self.assertEqual(attachment['mimeType'], 'application/x-wechat-wxgf')
        self.assertEqual(base64.b64decode(attachment['blob']), payload)

    def test_mcp_audio_read_checks_local_cache_signature(self):
        from unittest.mock import Mock
        from integrations.qq.provider import QQProvider

        cache = Path(self.temp.name) / 'qq' / 'media-cache'
        cache.mkdir(parents=True)
        path = cache / 'cached.wav'
        wav = b'RIFF\x00\x00\x00\x00WAVE' + b'synthetic-audio'
        path.write_bytes(wav)
        self.hub.store.ingest('qq', 'account', [{'id': 'room', 'title': 'Voice'}], [{
            'id': 'voice', 'conversation_id': 'room', 'timestamp': 100,
            'type': 'voice', 'file_name': 'cached.wav',
        }])
        provider = Mock()
        provider.cached_media.return_value = {'available': True, 'path': str(path)}
        self.hub.providers['qq'] = provider
        with patch.object(QQProvider, '_media_cache_dir', return_value=cache):
            self.assertEqual(self.hub.read_cached_image(
                self.hub.store.get_message('qq', 'account', 'voice')), (wav, 'audio/wav'))
            path.write_bytes(b'not-a-wav')
            with self.assertRaisesRegex(ValueError, '音频格式'):
                self.hub.read_cached_image(self.hub.store.get_message('qq', 'account', 'voice'))

    def test_mcp_audio_read_returns_qce_amr_and_silk_cache_as_audio_content(self):
        from unittest.mock import Mock
        from integrations.qq.provider import QQProvider
        from hub.mcp_server import MCPServer

        cache = Path(self.temp.name) / 'qq' / 'media-cache'
        cache.mkdir(parents=True)
        path = cache / 'cached.voice'
        self.hub.store.ingest('qq', 'account', [{'id': 'room', 'title': 'Voice'}], [{
            'id': 'voice', 'conversation_id': 'room', 'timestamp': 100,
            'type': 'voice', 'file_name': 'cached.voice',
        }])
        provider = Mock()
        provider.cached_media.return_value = {'available': True, 'path': str(path)}
        self.hub.providers['qq'] = provider
        mcp = MCPServer(self.hub.store, media_reader=self.hub.read_cached_image)

        with patch.object(QQProvider, '_media_cache_dir', return_value=cache):
            for payload, mime in ((b'#!AMR\nsynthetic-amr', 'audio/amr'),
                                  (b'#!AMR-WB\nsynthetic-amr-wb', 'audio/amr'),
                                  (b'#!SILK_V3synthetic-silk', 'audio/silk'),
                                  (b'\x02#!SILK_V3synthetic-prefixed-silk', 'audio/silk'),
                                  (b'\xff\xf1\x50\x80\x00\x1f\xfcsynthetic-aac', 'audio/aac'),
                                  (b'\x00\x00\x00\x18ftypM4A \x00\x00\x02\x00M4A ', 'audio/mp4')):
                path.write_bytes(payload)
                response = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                    'name': 'get_attachment', 'arguments': {
                        'platform': 'qq', 'account_id': 'account', 'message_id': 'voice'}}})['result']
                self.assertFalse(response['isError'])
                self.assertEqual(response['content'][1]['type'], 'audio')
                self.assertEqual(response['content'][1]['mimeType'], mime)

            path.write_bytes(b'\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isom')
            response = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                'name': 'get_attachment', 'arguments': {
                    'platform': 'qq', 'account_id': 'account', 'message_id': 'voice'}}})['result']
            self.assertTrue(response['isError'])

    def test_mcp_qq_wma_requires_account_bound_audio_metadata_and_asf_signature(self):
        from unittest.mock import Mock
        from integrations.qq.provider import QQProvider
        from hub.mcp_server import MCPServer

        cache = Path(self.temp.name) / 'qq' / 'media-cache'
        cache.mkdir(parents=True)
        path = cache / 'cached.wma'
        path.write_bytes(bytes.fromhex('3026b2758e66cf11a6d900aa0062ce6c') + b'fixture')
        provider = Mock()
        provider.cached_resource_metadata.return_value = {'type': 'audio', 'name': 'voice.wma'}
        provider.resolve_cached_resource.return_value = (path, 'voice.wma')
        self.hub.providers['qq'] = provider
        mcp = MCPServer(self.hub.store, media_reader=self.hub.read_cached_image)

        with patch.object(QQProvider, '_media_cache_dir', return_value=cache):
            response = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                'name': 'get_qq_cache_resource', 'arguments': {
                    'account_id': 'qq-1', 'resource_id': 'opaque-id', 'type': 'voice'}}})['result']
            self.assertFalse(response['isError'])
            self.assertEqual(response['content'][1]['type'], 'audio')
            self.assertEqual(response['content'][1]['mimeType'], 'audio/x-ms-wma')

            forged = mcp.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
                'name': 'get_qq_cache_resource', 'arguments': {
                    'account_id': 'qq-1', 'resource_id': 'opaque-id', 'type': 'video'}}})['result']
            self.assertTrue(forged['isError'])
            provider.resolve_cached_resource.assert_called_once_with('opaque-id', 'qq-1')

            provider.cached_resource_metadata.return_value = {'type': 'audio', 'name': 'voice.asf'}
            wrong_extension = mcp.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
                'name': 'get_qq_cache_resource', 'arguments': {
                    'account_id': 'qq-1', 'resource_id': 'opaque-id', 'type': 'voice'}}})['result']
            self.assertTrue(wrong_extension['isError'])
        self.assertEqual(provider.resolve_cached_resource.call_count, 2)

    def test_mcp_file_read_detects_common_document_signatures(self):
        from unittest.mock import Mock
        from integrations.qq.provider import QQProvider

        cache = Path(self.temp.name) / 'qq' / 'media-cache'
        cache.mkdir(parents=True)
        path = cache / 'cached.bin'
        self.hub.store.ingest('qq', 'account', [{'id': 'room', 'title': 'Files'}], [{
            'id': 'doc', 'conversation_id': 'room', 'timestamp': 100,
            'type': 'file', 'file_name': 'cached.bin',
        }])
        provider = Mock()
        provider.cached_media.return_value = {'available': True, 'path': str(path)}
        self.hub.providers['qq'] = provider
        from hub.mcp_server import MCPServer
        mcp = MCPServer(self.hub.store, media_reader=self.hub.read_cached_image)
        with patch.object(QQProvider, '_media_cache_dir', return_value=cache):
            for payload, expected in ((b'%PDF-1.7 synthetic', 'application/pdf'),
                                      (b'PK\x03\x04synthetic', 'application/zip'),
                                      (b'{\\rtf1 synthetic', 'application/rtf')):
                path.write_bytes(payload)
                self.assertEqual(self.hub.read_cached_image(
                    self.hub.store.get_message('qq', 'account', 'doc')), (payload, expected))
                response = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                    'name': 'get_attachment', 'arguments': {
                        'platform': 'qq', 'account_id': 'account', 'message_id': 'doc'}}})['result']
                self.assertFalse(response['isError'])
                self.assertEqual(response['content'][1]['type'], 'resource')
                self.assertEqual(response['content'][1]['resource']['mimeType'], expected)

    def test_mcp_image_read_accepts_indexed_qq_roaming_cache_without_network(self):
        from integrations.qq.provider import QQProvider
        cache = Path(self.temp.name) / 'qq' / 'media-cache'
        cache.mkdir(parents=True)
        path = Path(self.temp.name) / 'qq-roaming' / 'fixture' / 'cached.png'
        path.parent.mkdir(parents=True)
        image = b'\x89PNG\r\n\x1a\nsynthetic-image'
        path.write_bytes(image)
        self.hub.store.ingest('qq', 'account', [{'id': 'room', 'title': 'Photos'}], [{
            'id': 'photo', 'conversation_id': 'room', 'timestamp': 100,
            'type': 'image', 'roaming_resources': [
                {'type': 'image', 'path': 'qq-roaming/fixture/cached.png'}]}])
        provider = QQProvider()
        self.hub.providers['qq'] = provider
        with patch.object(QQProvider, '_media_cache_dir', return_value=cache), \
                patch.object(provider, '_request', side_effect=AssertionError('network forbidden')), \
                patch.object(provider, '_download_to_cache', side_effect=AssertionError('network forbidden')):
            self.assertEqual(self.hub.read_cached_image(
                self.hub.store.get_message('qq', 'account', 'photo'), resource_index=0),
                (image, 'image/png'))

    def test_mcp_image_read_allows_configured_wecom_cache_root_outside_hub_storage(self):
        from types import ModuleType, SimpleNamespace
        from unittest.mock import Mock

        with tempfile.TemporaryDirectory() as cache_dir:
            cache = Path(cache_dir)
            image = cache / 'cached.png'
            image.write_bytes(b'\x89PNG\r\n\x1a\nsynthetic-image')
            self.hub.store.ingest('wecom', 'account', [{'id': 'room', 'title': 'Work'}], [{
                'id': 'photo', 'conversation_id': 'room', 'timestamp': 100, 'type': 'image',
            }])
            provider = Mock()
            provider.media.return_value = {'available': True, 'path': str(image)}
            self.hub.providers['wecom'] = provider
            engine = ModuleType('integrations.wecom.engine')
            engine.OUT = str(cache)
            with patch.dict('sys.modules', {'integrations.wecom.engine': engine}):
                payload, mime = self.hub.read_cached_image(
                    self.hub.store.get_message('wecom', 'account', 'photo'))
            self.assertEqual((payload, mime), (image.read_bytes(), 'image/png'))
            provider.media.assert_called_once()

    def test_mcp_image_read_rejects_paths_outside_local_hub_storage(self):
        from unittest.mock import Mock

        with tempfile.TemporaryDirectory() as external:
            path = Path(external) / 'cached.png'
            path.write_bytes(b'\x89PNG\r\n\x1a\nsynthetic-image')
            provider = Mock()
            provider.media.return_value = {'available': True, 'path': str(path)}
            self.hub.providers['qq'] = provider
            self.hub.store.ingest('qq', 'account', [{'id': 'room', 'title': 'Photos'}], [{
                'id': 'photo', 'conversation_id': 'room', 'timestamp': 100, 'type': 'image',
            }])
            from integrations.qq.provider import QQProvider
            provider = Mock()
            provider.cached_media.return_value = {'available': True, 'path': str(path)}
            self.hub.providers['qq'] = provider
            with patch.object(QQProvider, '_media_cache_dir', return_value=Path(self.temp.name) / 'qq-cache'):
                with self.assertRaisesRegex(ValueError, '本机缓存目录'):
                    self.hub.read_cached_image(self.hub.store.get_message('qq', 'account', 'photo'))

    def test_contact_identity_endpoints_link_and_unlink(self):
        self.hub.store.ingest('wechat', 'account', [], [], [
            {'id': 'contact', 'name': 'Contact'},
            {'id': 'gh-official', 'name': 'News', 'kind': 'official',
             'official_account_kind': 'subscription'},
        ])
        headers = {'Content-Type': 'application/json'}
        payload = json.dumps({'platform': 'wechat', 'account_id': 'account', 'contact_id': 'contact',
                              'identity_id': 'same-person'}).encode()
        with self.request('/api/contact-identities', headers, payload) as response:
            self.assertEqual(json.load(response)['identity_id'], 'same-person')
        with self.request('/api/contacts?platform=wechat') as response:
            self.assertEqual(json.load(response)['items'][0]['identity_id'], 'same-person')
        with self.request('/api/contacts?platform=wechat&kind=official') as response:
            filtered = json.load(response)
            self.assertEqual([item['id'] for item in filtered['items']], ['gh-official'])
            self.assertEqual(filtered['items'][0]['extra']['official_account_kind'], 'subscription')
        with self.request('/api/contact-identities/unlink', headers, payload) as response:
            self.assertIsNone(json.load(response)['identity_id'])

    def test_local_data_delete_removes_only_hub_owned_files_and_reopens_store(self):
        storage = Path(self.temp.name)
        (self.hub.import_dir / 'fixture.json').write_text('{}', encoding='utf-8')
        (storage / 'keep.txt').write_text('retain', encoding='utf-8')
        (self.hub.settings_path).write_text('{"mcp_write_enabled": false}', encoding='utf-8')
        roaming = storage / 'qq-roaming' / 'Abcdef0123456789'
        roaming.mkdir(parents=True)
        (roaming / 'image.bin').write_bytes(b'fixture')
        (storage / 'qq-roaming' / 'my-notes').mkdir()
        self.hub.create_backup()
        with self.assertRaisesRegex(ValueError, '确认'):
            self.hub.delete_local_index('no')
        self.hub.jobs.add('wechat')
        with self.assertRaisesRegex(ValueError, '正在运行'):
            self.hub.delete_local_index('DELETE_LOCAL_INDEX')
        self.hub.jobs.clear()
        result = self.hub.delete_local_index('DELETE_LOCAL_INDEX')
        self.assertTrue(result['deleted'])
        self.assertEqual(self.hub.store.count(), 0)
        self.assertEqual(self.hub.store.conversations()['total'], 0)
        self.assertFalse((self.hub.import_dir / 'fixture.json').exists())
        self.assertEqual(self.hub.list_backups()['items'], [])
        self.assertFalse(roaming.exists())
        self.assertTrue((storage / 'qq-roaming' / 'my-notes').exists())
        self.assertEqual((storage / 'keep.txt').read_text(encoding='utf-8'), 'retain')
        self.assertTrue(self.hub.settings_path.exists())
        self.assertFalse(self.hub.settings['auto_sync'])
        self.assertIs(self.hub.send_service.adapters['wecom_bot'], self.hub.wecom_bot_sender)
        prepared = self.hub.send_service.prepare({
            'platform': 'wecom_bot', 'account_id': 'bot_fixture',
            'conversation_type': 'private', 'conversation_id': 'user_fixture',
            'text': 'synthetic only', 'request_id': 'delete-index-bot-1',
        })
        self.assertEqual(prepared['status'], 'pending_approval')
        self.assertIn('平台原始数据库和缓存', result['retained'])

    def test_local_data_delete_http_confirmation_and_report(self):
        headers = {'Content-Type': 'application/json'}
        with self.assertRaises(HTTPError) as result:
            self.request('/api/data/delete', headers, b'{"confirmation":"wrong"}')
        self.assertEqual(result.exception.code, 400)
        with self.request('/api/data/delete', headers, b'{"confirmation":"DELETE_LOCAL_INDEX"}') as response:
            report = json.load(response)
        self.assertIn('平台原始数据库和缓存', report['retained'])
        self.assertEqual(self.hub.store.count(), 0)

    def test_management_endpoints_reject_cross_origin_requests(self):
        request = Request(self.url + '/api/conversation-metadata',
                          headers={'Content-Type': 'application/json', 'Origin': 'https://example.invalid'},
                          data=b'{}')
        with self.assertRaises(HTTPError) as result:
            urlopen(request, timeout=3)
        self.assertEqual(result.exception.code, 403)
        self.assertEqual(self.hub.store.operation_log(), [])
        metadata = self.hub.store.conversations('wechat')['items'][0]
        self.assertEqual((metadata['note'], metadata['tags'], metadata['archived']), ('', [], False))

    def test_backup_download_and_restore_endpoints(self):
        created = json.load(self.request('/api/backups', {'Content-Type': 'application/json'}, b'{}'))
        with self.request('/api/backups/' + created['id']) as response:
            snapshot = response.read()
        self.assertTrue(snapshot.startswith(b'SQLite format 3'))
        self.assertEqual(created['size'], len(snapshot))

        self.hub.store.ingest('qq', 'account', [{'id': 'other'}], [
            {'id': 'new', 'conversation_id': 'other', 'timestamp': 200, 'text': 'new'}
        ])
        restored = json.load(self.request('/api/backups/restore', {
            'Content-Type': 'application/octet-stream'
        }, snapshot))
        self.assertIn('lmh-backup-restored-', restored['id'])
        self.assertEqual(self.hub.store.count(), 1)
        self.assertIn(restored['id'], {item['id'] for item in self.hub.list_backups()['items']})

    def test_media_backup_bundle_download_and_restore(self):
        relative = 'qq-roaming/' + ('a' * 20) + '/fixture.png'
        media = self.hub.storage / relative
        media.parent.mkdir(parents=True)
        media.write_bytes(b'synthetic roaming image')
        self.hub.store.ingest('qq', 'fixture-account', [{'id': 'qq-room'}], [{
            'id': 'qq-media', 'conversation_id': 'qq-room', 'timestamp': 200,
            'type': 'image', 'roaming_resources': [
                {'type': 'image', 'name': 'fixture.png', 'path': relative}
            ],
        }])
        created = json.load(self.request('/api/backups', {'Content-Type': 'application/json'},
                                         b'{"include_media":true}'))
        self.assertEqual(created['kind'], 'media')
        self.assertEqual(created['media_count'], 1)
        with self.request('/api/backups/' + created['id']) as response:
            bundle = response.read()
            self.assertEqual(response.headers.get_content_type(), 'application/zip')
        with tempfile.TemporaryDirectory() as temp:
            source_bundle = Path(temp) / 'backup.zip'
            source_bundle.write_bytes(bundle)
            with ZipFile(source_bundle) as archive:
                self.assertEqual(set(archive.namelist()), {
                    'index.sqlite3', 'manifest.json', 'media/' + relative
                })

        media.unlink()
        self.hub.store.ingest('qq', 'fixture-account', [{'id': 'other-room'}], [{
            'id': 'after-backup', 'conversation_id': 'other-room', 'timestamp': 300,
            'text': 'not in restored snapshot',
        }])
        restored = json.load(self.request('/api/backups/restore', {
            'Content-Type': 'application/octet-stream'
        }, bundle))
        self.assertEqual(restored['kind'], 'media')
        self.assertTrue(restored['id'].endswith('.zip'))
        self.assertEqual(self.hub.store.count(), 2)
        self.assertEqual(media.read_bytes(), b'synthetic roaming image')

    def test_invalid_backup_upload_does_not_change_index(self):
        with self.assertRaises(HTTPError) as result:
            self.request('/api/backups/restore', {
                'Content-Type': 'application/octet-stream'
            }, b'not a database')
        self.assertEqual(result.exception.code, 400)
        self.assertEqual(self.hub.store.count(), 1)

    def test_restore_failure_rolls_back_to_pre_restore_snapshot(self):
        backup = self.hub.create_backup()
        self.hub.store.ingest('qq', 'account', [{'id': 'other'}], [
            {'id': 'new', 'conversation_id': 'other', 'timestamp': 200, 'text': 'new'}
        ])
        restore = self.hub.store.restore_from
        calls = []

        def restore_then_fail(path):
            restore(path)
            if not calls:
                calls.append(True)
                raise RuntimeError('simulated post-restore failure')

        with patch.object(self.hub.store, 'restore_from', side_effect=restore_then_fail):
            with self.assertRaisesRegex(RuntimeError, 'simulated post-restore failure'):
                self.hub.restore_backup(self.hub.backup_path(backup['id']))
        self.assertEqual(self.hub.store.count(), 2)

    def test_import_preview_and_commit_endpoints(self):
        payload = json.dumps([{
            'id': 'synthetic-1', 'platform': 'wechat', 'account_id': 'synthetic',
            'conversation_id': 'synthetic-room', 'conversation_title': 'Synthetic',
            'timestamp': 1800000000, 'sender_name': 'Tester', 'text': 'hello import'
        }]).encode('utf-8')
        query = '?format=json&platform=all'
        with self.request('/api/imports/preview' + query, {
            'Content-Type': 'application/octet-stream'
        }, payload) as response:
            preview = json.load(response)
        self.assertEqual(preview['valid'], 1)
        self.assertEqual(preview['sample'][0]['text'], 'hello import')

        with self.request('/api/imports' + query, {
            'Content-Type': 'application/octet-stream'
        }, payload) as response:
            self.assertEqual(response.status, 202)
            imported = json.load(response)
        deadline = time.monotonic() + 5
        while imported['state'] in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(0.01)
            with self.request('/api/imports/' + imported['id']) as response:
                imported = json.load(response)
        self.assertEqual(imported['state'], 'completed')
        self.assertEqual(imported['messages'], 1)
        self.assertEqual(len(imported['fingerprint']), 64)
        with self.request('/api/imports' + query, {
            'Content-Type': 'application/octet-stream'
        }, payload) as response:
            repeated = json.load(response)
        deadline = time.monotonic() + 5
        while repeated['state'] in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(0.01)
            with self.request('/api/imports/' + repeated['id']) as response:
                repeated = json.load(response)
        self.assertEqual(repeated['state'], 'completed')
        self.assertEqual(repeated['messages'], 1)
        self.assertEqual(self.hub.store.count(), 2)
        with self.request('/api/imports') as response:
            jobs = json.load(response)['items']
        self.assertEqual(len(jobs), 2)
        with self.request('/api/context?platform=wechat&account_id=synthetic&message_id=synthetic-1') as response:
            context = json.load(response)
        self.assertEqual(context['items'][0]['id'], 'synthetic-1')

    def test_import_job_resumes_after_hub_restart(self):
        storage = Path(self.temp.name) / 'restart-fixture'
        storage.mkdir()
        first = Hub(storage)
        job_id = 'resume-job-fixture'
        (storage / 'imports' / (job_id + '.json')).write_text(json.dumps({
            'conversations': [{'id': 'resume-room', 'title': 'Resume'}],
            'messages': [{'platform': 'wecom', 'account_id': 'fixture', 'id': 'resume-1',
                          'conversation_id': 'resume-room', 'timestamp': 1, 'type': 'text', 'text': 'resumed'}]
        }), encoding='utf-8')
        first.store.create_import_job({'id': job_id, 'fingerprint': 'a' * 64, 'filename': 'fixture.json',
            'staging_file': job_id + '.json', 'created_at': int(time.time()), 'total': 1,
            'valid': 1, 'invalid': 0, 'errors': []})
        first.close()
        restarted = Hub(storage)
        deadline = time.monotonic() + 5
        job = restarted.store.import_job(job_id)
        while job['state'] in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(0.01)
            job = restarted.store.import_job(job_id)
        self.assertEqual(job['state'], 'completed')
        self.assertEqual(restarted.store.search('resumed', platform='wecom')['total'], 1)
        restarted.close()

    def test_import_job_can_be_cancelled_before_first_batch(self):
        payload = json.dumps([{
            'id': f'cancel-{index}', 'platform': 'qq', 'account_id': 'fixture',
            'conversation_id': 'cancel-room', 'timestamp': index, 'text': 'synthetic'
        } for index in range(501)]).encode('utf-8')
        started, release = threading.Event(), threading.Event()
        run = self.hub._run_import_job

        def paused(job_id, cancel):
            started.set()
            release.wait(3)
            run(job_id, cancel)

        with patch.object(self.hub, '_run_import_job', side_effect=paused):
            with self.request('/api/imports?format=json&platform=all', {
                'Content-Type': 'application/octet-stream'
            }, payload) as response:
                job = json.load(response)
            self.assertTrue(started.wait(2))
            with self.request('/api/imports/' + job['id'] + '/cancel', {
                'Content-Type': 'application/json'
            }, b'{}') as response:
                self.assertIn(json.load(response)['state'], ('queued', 'running'))
            release.set()
        deadline = time.monotonic() + 5
        job = self.hub.store.import_job(job['id'])
        while job['state'] in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(0.01)
            job = self.hub.store.import_job(job['id'])
        self.assertEqual(job['state'], 'cancelled')
        self.assertEqual(job['processed'], 0)
        self.assertEqual(self.hub.store.count(), 1)

    def test_qq_local_database_unavailable_does_not_launch_login(self):
        from unittest.mock import patch
        with patch.object(self.hub, 'qq_local_provider') as provider, \
                patch('integrations.qq.provider.QQProvider') as online:
            provider.return_value.status.return_value = {'available': False,
                                                        'message': '数据库密钥不可用'}
            self.hub.sync('qq')
            online.assert_not_called()
        self.assertEqual(self.hub.sources['qq']['state'], 'error')
        self.assertEqual(self.hub.sources['qq']['error'], '数据库密钥不可用')
        self.assertIsNone(self.hub.sources['qq']['last_sync'])

    def test_qq_local_database_ready_does_not_require_online_account(self):
        from unittest.mock import patch
        with patch.object(self.hub, 'qq_local_provider') as provider:
            provider.return_value.status.return_value = {'available': True, 'online': False}
            provider.return_value.conversations.return_value = []
            provider.return_value.contacts.return_value = []
            self.hub.sync('qq')
        self.assertEqual(self.hub.sources['qq']['state'], 'connected')

    def test_qq_sync_resumes_offset_after_hub_restart(self):
        from types import SimpleNamespace

        storage = Path(self.temp.name) / 'qq-resume'
        first = Hub(storage)
        calls = []

        class Provider:
            def status(self):
                return {'available': True, 'online': True, 'account_id': 'qq-account'}

            def conversations(self):
                return [{'id': 'peer', 'title': 'Fixture'}]

            def contacts(self):
                return []

            def messages(self, _conversation, limit, offset):
                calls.append(offset)
                if offset == 0:
                    return [{'id': f'peer:{index}', 'conversation_id': 'peer',
                             'timestamp': index, 'text': 'fixture'} for index in range(limit)]
                if len(calls) == 2:
                    raise RuntimeError('simulated interruption')
                return [{'id': 'peer:last', 'conversation_id': 'peer',
                         'timestamp': 1000, 'text': 'fixture'}]

        try:
            with patch.object(first, 'qq_local_provider', return_value=Provider()):
                with self.assertRaisesRegex(RuntimeError, 'simulated interruption'):
                    first.sync_qq()
            checkpoint = first.store.sync_checkpoint('qq', 'qq-account', 'peer')
            self.assertEqual(checkpoint['next_offset'], 500)
            self.assertFalse(checkpoint['complete'])
            first.close()

            restarted = Hub(storage)
            with patch.object(restarted, 'qq_local_provider', return_value=Provider()):
                restarted.sync_qq()
            self.assertEqual(calls, [0, 500, 0, 500])
            self.assertEqual(restarted.store.count('qq'), 501)
            self.assertTrue(restarted.store.sync_checkpoint(
                'qq', 'qq-account', 'peer')['complete'])
            restarted.close()
        finally:
            if first.store._conn is not None:
                first.close()

    def test_qq_targeted_sync_validates_id_and_starts_only_qq(self):
        for platform in ('wechat', 'wecom', 'all'):
            with self.subTest(platform=platform), self.assertRaisesRegex(ValueError, '仅支持 QQ'):
                self.hub.start_sync(platform, 'peer')
        for conversation_id in ('', '   ', 'x' * 513, 123, [], {}):
            with self.subTest(conversation_id=type(conversation_id).__name__), \
                    self.assertRaisesRegex(ValueError, '会话标识无效'):
                self.hub.start_sync('qq', conversation_id)

        with patch('hub.server.threading.Thread') as thread:
            self.hub.start_sync('qq', 'peer')
        thread.assert_called_once()
        self.assertEqual(thread.call_args.kwargs['name'], 'sync-qq')
        self.assertEqual(thread.call_args.kwargs['target'], self.hub.sync)
        self.assertEqual(thread.call_args.kwargs['args'][0], 'qq')
        self.assertEqual(thread.call_args.kwargs['args'][2], 'peer')
        self.hub.jobs.clear()
        self.hub.sync_cancellations.clear()

    def test_sync_api_forwards_targeted_qq_conversation(self):
        with patch.object(self.hub, 'start_sync') as start_sync:
            with self.request('/api/sync', {'Content-Type': 'application/json'},
                              json.dumps({'platform': 'qq', 'conversation_id': 'peer'}).encode()) as response:
                self.assertEqual(json.load(response)['message'], '已开始读取本机记录')
        start_sync.assert_called_once_with('qq', 'peer')
        with self.assertRaises(HTTPError) as error:
            self.request('/api/sync', {'Content-Type': 'application/json'},
                         b'{"platform":"wechat","conversation_id":"peer"}')
        self.assertEqual(error.exception.code, 400)

    def test_qq_targeted_sync_matches_exact_online_conversation_only(self):
        calls = []

        class Provider:
            def status(self):
                return {'available': True, 'online': True, 'account_id': 'qq-account'}

            def conversations(self):
                return [{'id': 'peer', 'title': 'Target'},
                        {'id': 'peer-extra', 'title': 'Similar'}]

            def contacts(self):
                return []

            def messages(self, conversation, limit, offset):
                calls.append((conversation['id'], offset))
                return [{'id': f"{conversation['id']}:1",
                         'conversation_id': conversation['id'],
                         'timestamp': 1, 'text': 'fixture'}]

        with patch.object(self.hub, 'qq_local_provider', return_value=Provider()):
            self.hub.sync_qq(conversation_id='peer')

        self.assertEqual(calls, [('peer', 0)])
        self.assertEqual([row['id'] for row in self.hub.store.conversations('qq')['items']], ['peer'])
        self.assertEqual(self.hub.store.count('qq'), 1)

    def test_qq_targeted_sync_reports_conversation_outside_current_account(self):
        class Provider:
            def status(self):
                return {'available': True, 'online': True, 'account_id': 'qq-account'}

            def conversations(self):
                return [{'id': 'peer'}]

            def contacts(self):
                return []

        with patch.object(self.hub, 'qq_local_provider', return_value=Provider()):
            self.hub.sync('qq', conversation_id='peer-other')

        self.assertEqual(self.hub.sources['qq']['state'], 'error')
        self.assertEqual(self.hub.sources['qq']['error'],
                         '所选 QQ 会话不属于当前本地数据库账号或已不可用')
        self.assertEqual(self.hub.store.count('qq'), 0)

    def test_qq_sync_keeps_reading_after_one_conversation_provider_error(self):
        from integrations.qq.provider import QQProviderError

        calls = []

        class Provider:
            def status(self):
                return {'available': True, 'online': True, 'account_id': 'qq-account'}

            def conversations(self):
                return [{'id': peer, 'title': 'Fixture'} for peer in ('good-1', 'failed', 'good-2')]

            def contacts(self):
                return []

            def messages(self, conversation, limit, offset):
                peer = conversation['id']
                calls.append((peer, offset))
                if peer == 'failed':
                    raise QQProviderError('synthetic provider failure')
                if offset:
                    return []
                return [{'id': f'{peer}:1', 'conversation_id': peer,
                         'timestamp': 1, 'text': 'fixture'}]

        self.hub.store.ingest('qq', 'qq-account', [], [], [], checkpoint={
            'conversation_id': 'failed', 'next_offset': 37, 'complete': False})
        with patch.object(self.hub, 'qq_local_provider', return_value=Provider()):
            self.hub.sync('qq')

        self.assertEqual(calls, [('good-1', 0), ('failed', 0), ('good-2', 0)])
        self.assertEqual(self.hub.sources['qq']['state'], 'partial')
        self.assertEqual(self.hub.sources['qq']['error'], '1 个会话读取失败，其余会话已保留')
        self.assertEqual(self.hub.store.count('qq'), 2)
        checkpoint = self.hub.store.sync_checkpoint('qq', 'qq-account', 'failed')
        self.assertEqual(checkpoint['next_offset'], 37)
        self.assertFalse(checkpoint['complete'])

    def test_qq_completed_sync_restarts_at_zero_and_reconciles_new_messages(self):
        from unittest.mock import patch

        calls = []

        class Provider:
            def status(self):
                return {'available': True, 'online': True, 'account_id': 'qq-account'}

            def conversations(self):
                return [{'id': 'peer', 'title': 'Fixture'}]

            def contacts(self):
                return []

            def messages(self, _conversation, limit, offset):
                calls.append(offset)
                if offset:
                    return []
                round_number = calls.count(0)
                rows = [{'id': 'peer:stable', 'conversation_id': 'peer',
                         'timestamp': 10, 'text': 'original'}]
                if round_number > 1:
                    rows.append({'id': 'peer:new', 'conversation_id': 'peer',
                                 'timestamp': 20, 'text': 'arrived later'})
                return rows

        with patch.object(self.hub, 'qq_local_provider', return_value=Provider()):
            self.hub.sync_qq()
            self.hub.sync_qq()

        self.assertEqual(calls, [0, 0])
        self.assertEqual(self.hub.store.count('qq'), 2)
        self.assertEqual(self.hub.store.search('arrived later', platform='qq')['total'], 1)

    def test_wecom_snapshot_replay_upserts_without_deleting_absent_source_rows(self):
        import sys
        import types

        source = {'text': 'first snapshot', 'include_absent_row': True}

        class Provider:
            def __init__(self):
                self.engine = type('Engine', (), {
                    'get_content': lambda _self: {'members': [
                        {'user_id': 'member', 'avatar': 'https://wework.qpic.cn/member.png'}]}
                })()

            def refresh(self):
                return True

            def list_conversations(self, _limit):
                return [{'conversation_id': 'room', 'name': 'Fixture',
                         'participants': 3, 'last_time': 1}]

            def iter_message_batches(self, _batch_size):
                rows = [{'message_id': 'stable-id', 'conversation_id': 'room',
                         'sender_id': 'member', 'sender': 'Member', 'send_time': 1,
                         'content_type': 2, 'text': source['text'], 'me': False,
                         'media': [{'p': 'cache/image.png', 'n': 'picture.png', 'k': 'image', 'sz': 12}]}]
                if source['include_absent_row']:
                    rows.append({'message_id': 'source-retained', 'conversation_id': 'room',
                                 'sender_id': 'member', 'sender': 'Member', 'send_time': 2,
                                 'content_type': 1, 'text': 'retained after source omission', 'me': False})
                yield rows

        adapter_module = types.ModuleType('integrations.wecom.adapter')
        adapter_module.WeComAdapter = Provider
        with patch.dict(sys.modules, {'integrations.wecom.adapter': adapter_module}):
            self.hub.sync_wecom()
            source['text'] = 'updated snapshot'
            source['include_absent_row'] = False
            self.hub.sync_wecom()

        self.assertEqual(self.hub.store.count('wecom'), 2)
        self.assertEqual(self.hub.store.search('first snapshot', platform='wecom')['total'], 0)
        self.assertEqual(self.hub.store.search('updated snapshot', platform='wecom')['total'], 1)
        self.assertEqual(self.hub.store.search('retained after source omission', platform='wecom')['total'], 1)
        message = self.hub.store.search('updated snapshot', platform='wecom')['items'][0]
        self.assertEqual(message['sender_avatar_url'], 'https://wework.qpic.cn/member.png')
        self.assertEqual(message['file_name'], 'picture.png')
        self.assertEqual(message['extra']['attachments'][0]['name'], 'picture.png')
        self.assertEqual(self.hub.store.contacts('wecom')['items'][0]['avatar_url'],
                         'https://wework.qpic.cn/member.png')

    def test_wecom_snapshot_error_exposes_only_safe_diagnostic(self):
        import sys
        import types

        class WeComSnapshotError(RuntimeError):
            safe_message = '无法解密本机企业微信数据库；请核对密钥、账号槽位和客户端版本'

        class Provider:
            def refresh(self):
                raise WeComSnapshotError('secret path and SQL detail')

        module = types.ModuleType('integrations.wecom.adapter')
        module.WeComAdapter = Provider
        module.WeComSnapshotError = WeComSnapshotError
        with patch.dict(sys.modules, {'integrations.wecom.adapter': module}):
            self.hub.sync('wecom', threading.Event())

        self.assertEqual(self.hub.sources['wecom']['state'], 'error')
        self.assertEqual(self.hub.sources['wecom']['error'], WeComSnapshotError.safe_message)
        self.assertNotIn('secret path', str(self.hub.sources['wecom']))
        self.assertNotIn('SQL detail', str(self.hub.sources['wecom']))

    def test_wechat_sync_resumes_offset_after_hub_restart(self):
        import sys
        import types
        from types import SimpleNamespace

        storage = Path(self.temp.name) / 'wechat-resume'
        first = Hub(storage)
        calls = []

        class Provider:
            def __init__(self):
                self.reader = SimpleNamespace(wxid='wx-account', account='')

            def contacts(self):
                return []

            def sessions(self):
                return [{'id': 'room', 'message_count': 2001}]

            def message_count(self, _conversation_id):
                return 2001

            def messages(self, _conversation_id, limit, offset):
                calls.append(offset)
                if offset == 0:
                    return [{'id': f'wx:{index}', 'sender_id': 'user', 'content': 'fixture',
                             'local_type': 1, 'timestamp': index}
                            for index in range(limit)]
                if len(calls) == 2:
                    raise RuntimeError('simulated interruption')
                return [{'id': 'wx:last', 'sender_id': 'user',
                         'content': 'wxid-user:<msg><emoji md5="' + ('b2' * 16) + '"/></msg>',
                         'local_type': 11000, 'timestamp': 2001}]

            def status(self):
                return {'state': 'connected'}

        provider_module = types.ModuleType('integrations.wechat.provider')
        provider_module.WeChatProvider = Provider
        restarted = None

        try:
            with patch.dict(sys.modules, {'integrations.wechat.provider': provider_module}):
                with self.assertRaisesRegex(RuntimeError, 'simulated interruption'):
                    first.sync_wechat()
            checkpoint = first.store.sync_checkpoint('wechat', 'wx-account', 'room')
            self.assertEqual(checkpoint['next_offset'], 2000)
            self.assertFalse(checkpoint['complete'])
            first.close()

            restarted = Hub(storage)
            with patch.dict(sys.modules, {'integrations.wechat.provider': provider_module}):
                restarted.sync_wechat()
            self.assertEqual(calls, [0, 2000, 2000])
            self.assertEqual(restarted.store.count('wechat'), 2001)
            self.assertEqual(restarted.store.get_message('wechat', 'wx-account', 'wx:last')['extra']['emoji_md5'],
                             'b2' * 16)
            self.assertTrue(restarted.store.sync_checkpoint(
                'wechat', 'wx-account', 'room')['complete'])
            restarted.close()
        finally:
            if first.store._conn is not None:
                first.close()
            if restarted is not None and restarted.store._conn is not None:
                restarted.close()

    def test_wechat_sync_resumes_completed_source_cursor(self):
        import sys
        import types
        from types import SimpleNamespace

        provider_module = types.ModuleType('integrations.wechat.provider')
        calls = []

        class Provider:
            def __init__(self):
                self.reader = SimpleNamespace(wxid='wx-account', account='')

            def contacts(self):
                return []

            def sessions(self):
                return [{'id': 'room', 'message_count': 2}]

            def message_count(self, _conversation_id):
                return 2

            def messages_after(self, _conversation_id, cursor=None, limit=2000):
                calls.append(cursor)
                if cursor is None:
                    return {'messages': [
                        {'id': 'wx:1', 'sender_id': 'user', 'content': 'one', 'local_type': 1,
                         'timestamp': 1, 'sort_seq': 10, 'shard': 'message_1.db', 'local_id': 1},
                        {'id': 'wx:2', 'sender_id': 'user',
                         'content': 'wxid-user:<msg><emoji md5="' + ('a1' * 16) + '"/></msg>',
                         'local_type': 11000,
                         'timestamp': 2, 'sort_seq': 20, 'shard': 'message_1.db', 'local_id': 2},
                    ], 'next_cursor': {'version': 1, 'session_id': 'room', 'sort_seq': 20,
                                       'shard': 'message_1.db', 'local_id': 2}, 'has_more': False}
                return {'messages': [
                    {'id': 'wx:3', 'sender_id': 'user', 'content': 'three', 'local_type': 1,
                     'timestamp': 3, 'sort_seq': 30, 'shard': 'message_1.db', 'local_id': 3},
                ], 'next_cursor': {'version': 1, 'session_id': 'room', 'sort_seq': 30,
                                   'shard': 'message_1.db', 'local_id': 3}, 'has_more': False}

            def status(self):
                return {'state': 'connected'}

        provider_module.WeChatProvider = Provider
        storage = Path(self.temp.name) / 'wechat-keyset'
        hub = Hub(storage)
        try:
            with patch.dict(sys.modules, {'integrations.wechat.provider': provider_module}):
                hub.sync_wechat()
                hub.sync_wechat()
            self.assertEqual(calls[0], None)
            self.assertEqual(calls[1]['sort_seq'], 20)
            self.assertEqual(hub.store.count('wechat'), 3)
            self.assertEqual(hub.store.get_message('wechat', 'wx-account', 'wx:2')['extra']['emoji_md5'],
                             'a1' * 16)
            checkpoint = hub.store.sync_checkpoint('wechat', 'wx-account', 'room')
            self.assertEqual(checkpoint['cursor']['sort_seq'], 30)
            self.assertTrue(checkpoint['complete'])
        finally:
            hub.close()

    def test_wechat_sync_refreshes_moments_only_from_complete_catalog(self):
        import sys
        import types
        from types import SimpleNamespace

        cache = Path(self.temp.name) / 'moments-sync-cache'
        cache.mkdir()
        image = cache / 'image.png'
        image.write_bytes(b'fixture')
        source = {'account': 'wx-account', 'result': 'complete', 'calls': 0}
        old_posts = [{'id': 'old-post', 'username': 'author', 'contentDesc': 'preserve me'}]
        self.hub.store.replace_moments('wx-account', old_posts, cache)

        class Provider:
            def __init__(self):
                self.reader = SimpleNamespace(wxid=source['account'], account='')
                self.sns_cache_dir = cache

            def contacts(self):
                return []

            def sessions(self):
                return []

            def moments(self, *, limit, offset):
                self.assertion = (limit, offset)
                source['calls'] += 1
                mode = source['result']
                if mode == 'raise':
                    raise RuntimeError('synthetic reader failure')
                if mode == 'unavailable':
                    return {'available': False, 'truncated': False, 'items': []}
                if mode == 'truncated':
                    return {'available': True, 'truncated': True,
                            'items': [{'id': 'partial-post', 'contentDesc': 'must not replace'}]}
                return {'available': True, 'truncated': False, 'items': [{
                    'id': f"post-{source['account']}", 'username': 'author',
                    'contentDesc': 'indexed synthetic post', 'createTime': 123,
                    'source_path': str(image), 'media': [{
                        'type': 'image', 'url': 'https://private.invalid/token',
                        '_local_path': str(image),
                    }],
                }]}

            def status(self):
                return {'state': 'connected'}

        provider_module = types.ModuleType('integrations.wechat.provider')
        provider_module.WeChatProvider = Provider

        def sync():
            with patch.dict(sys.modules, {'integrations.wechat.provider': provider_module}):
                self.hub.sync_wechat()

        self.hub.set_capabilities({'moments': True})
        sync()
        account_posts = self.hub.store.moments('wx-account')['items']
        self.assertEqual([post['id'] for post in account_posts], ['post-wx-account'])
        self.assertEqual(account_posts[0]['media'][0]['local_available'], True)
        self.assertNotIn(str(image), json.dumps(account_posts))
        self.assertNotIn('private.invalid', json.dumps(account_posts))
        self.assertNotIn('source_path', json.dumps(account_posts))
        self.assertEqual(self.hub.store.moments('other-account')['total'], 0)

        source['account'] = 'other-account'
        sync()
        self.assertEqual(self.hub.store.moments('wx-account')['items'][0]['id'], 'post-wx-account')
        self.assertEqual(self.hub.store.moments('other-account')['items'][0]['id'], 'post-other-account')

        source['account'] = 'wx-account'
        for mode in ('unavailable', 'truncated', 'raise'):
            source['result'] = mode
            sync()
            retained = self.hub.store.moments('wx-account')['items']
            self.assertEqual([post['id'] for post in retained], ['post-wx-account'], mode)

        calls_before_disabled_sync = source['calls']
        self.hub.set_capabilities({'moments': False})
        sync()
        self.assertEqual(source['calls'], calls_before_disabled_sync)
        self.assertEqual(self.hub.store.moments('wx-account')['items'][0]['id'], 'post-wx-account')

    def test_partial_reader_stays_partial(self):
        from unittest.mock import patch
        with patch.object(self.hub, 'sync_wechat', return_value='partial'):
            self.hub.sync('wechat')
        self.assertEqual(self.hub.sources['wechat']['state'], 'partial')

    def test_qq_qr_is_locally_encoded_without_raw_url(self):
        import base64
        result = qq_login_result({'started': True, 'qr_url': 'https://ssl.ptlogin2.qq.com/test-local-qr'})
        self.assertNotIn('qr_url', result)
        self.assertTrue(base64.b64decode(result['qr_image'].split(',', 1)[1]).startswith(b'\x89PNG\r\n\x1a\n'))

    def test_foreign_qq_qr_rejected(self):
        with self.assertRaises(ValueError):
            qq_login_result({'qr_url': 'https://qq.com.attacker.example/login'})

    def test_qq_local_status_redacts_account_paths_and_key(self):
        status = {'mode': 'local_database', 'state': 'locked', 'available': False,
                  'error_code': 'key_required', 'message': '需要本机数据库密钥',
                  'database_count': 2, 'key': 'private', 'data_dir': 'private',
                  'account_id': 'private'}
        with patch.object(self.hub, 'qq_local_provider') as provider, \
                patch('integrations.qq.provider.QQProvider') as online:
            provider.return_value.status.return_value = status
            for route in ('/api/qq/local-status', '/api/qq/login-status'):
                with self.request(route) as response:
                    result = json.load(response)
                self.assertEqual(result['mode'], 'local_database')
                self.assertEqual(result['error_code'], 'key_required')
                self.assertNotIn('private', json.dumps(result))
                self.assertNotIn('qr_image', result)
            online.assert_not_called()

    def test_qq_key_discovery_requires_confirmation_and_sync_capability(self):
        with patch.object(self.hub, 'qq_local_provider') as provider:
            provider.return_value.status.return_value = {
                'available': False, 'error_code': 'key_required'}
            with self.assertRaises(ValueError):
                self.hub.start_qq_key_discovery(False)
            provider.assert_not_called()
            provider.return_value.status.return_value = {'available': True}
            with self.assertRaisesRegex(ValueError, '无需发现密钥'):
                self.hub.start_qq_key_discovery(True)
        self.hub.set_capabilities({'sync': False})
        with self.assertRaises(PermissionError):
            self.hub.start_qq_key_discovery(True)
        self.assertEqual(Handler.capability_for_path('/api/qq/key-discovery'), 'sync')

    def test_qq_key_discovery_saves_only_a_database_verified_key(self):
        secret = 'SyntheticKey1234'
        config_path = Path(self.temp.name) / 'qq-local.json'
        config_path.write_text(json.dumps({'data_dir': self.temp.name}))

        class Provider:
            def __init__(self):
                self.config_path = config_path

            def status(self):
                configured = json.loads(config_path.read_text()).get('key')
                return {'available': configured == 'dpapi:v1:verified-fixture',
                        'error_code': None if configured == 'dpapi:v1:verified-fixture' else 'key_required'}

            def _discover(self):
                return 'synthetic-database', None

            def _probe(self, source, key):
                if source != 'synthetic-database' or key != secret:
                    raise ValueError('invalid fixture candidate')

        provider = Provider()
        with patch.object(self.hub, 'qq_local_provider', return_value=provider), \
                patch('integrations.qq.key_store.protect_key', return_value='dpapi:v1:verified-fixture'), \
                patch('integrations.qq.key_reader.find_key', return_value=(secret, {'state': 'verified'})), \
                patch.object(self.hub, 'start_sync') as sync:
            self.assertEqual(self.hub.start_qq_key_discovery(True)['state'], 'running')
            deadline = time.monotonic() + 2
            while self.hub.qq_key_discovery_status()['state'] == 'running' and time.monotonic() < deadline:
                time.sleep(0.01)
            result = self.hub.qq_key_discovery_status()
        self.assertEqual(result['state'], 'completed')
        self.assertNotIn(secret, json.dumps(result))
        saved_key = json.loads(config_path.read_text())['key']
        self.assertEqual(saved_key, 'dpapi:v1:verified-fixture')
        self.assertNotIn(secret, saved_key)
        sync.assert_called_once_with('qq')

    def test_qq_key_discovery_rollback_does_not_overwrite_concurrent_configuration(self):
        config_path = Path(self.temp.name) / 'qq-local.json'
        old_directory = Path(self.temp.name) / 'old-account'
        new_directory = Path(self.temp.name) / 'new-account'
        old_directory.mkdir()
        new_directory.mkdir()
        original = {'data_dir': str(old_directory), 'marker': 'preserve'}
        config_path.write_text(json.dumps(original))
        status_started = threading.Event()
        release_status = threading.Event()

        class Provider:
            def __init__(self):
                self.config_path = config_path

            def _discover(self):
                return 'synthetic-database', None

            def _probe(self, source, key):
                if source != 'synthetic-database' or key != 'SyntheticKey1234':
                    raise ValueError('fixture mismatch')

            def status(self):
                status_started.set()
                if not release_status.wait(2):
                    raise TimeoutError('test status barrier expired')
                return {'available': False}

        provider = Provider()
        configure_lock_attempted = threading.Event()
        configure_done = threading.Event()
        original_lock = self.hub.lock

        class TrackingRLock:
            def __enter__(self):
                if threading.current_thread().name == 'qq-config-race':
                    configure_lock_attempted.set()
                original_lock.acquire()
                return self

            def __exit__(self, *_exc):
                original_lock.release()

        self.hub.lock = TrackingRLock()
        with patch.object(self.hub, 'qq_local_provider', return_value=provider), \
                patch('integrations.qq.key_store.protect_key', return_value='dpapi:v1:verified-fixture'), \
                patch('integrations.qq.key_reader.find_key', return_value=(
                    'SyntheticKey1234', {'state': 'verified'})), \
                patch.object(self.hub, 'start_sync'):
            discovery = threading.Thread(target=self.hub._run_qq_key_discovery,
                                         args=(provider,))
            discovery.start()
            self.assertTrue(status_started.wait(2))

            def configure_new_directory():
                try:
                    self.hub.configure('qq', {'data_dir': str(new_directory)})
                finally:
                    configure_done.set()

            configure = threading.Thread(target=configure_new_directory, name='qq-config-race')
            configure.start()
            self.assertTrue(configure_lock_attempted.wait(2))
            self.assertFalse(configure_done.is_set(),
                             'QQ configuration must wait through key verification/rollback')
            release_status.set()
            discovery.join(2)
            configure.join(2)
        self.hub.lock = original_lock

        self.assertFalse(discovery.is_alive())
        self.assertFalse(configure.is_alive())
        saved = json.loads(config_path.read_text())
        self.assertEqual(saved['data_dir'], str(new_directory))
        self.assertEqual(saved['marker'], 'preserve')
        self.assertNotIn('key', saved)
        self.assertEqual(self.hub.qq_key_discovery_status()['state'], 'failed')

    def test_qq_key_discovery_keeps_only_bounded_scanner_counts(self):
        original = {'data_dir': self.temp.name}
        config_path = Path(self.temp.name) / 'qq-local.json'
        config_path.write_text(json.dumps(original))

        class Provider:
            def __init__(self):
                self.config_path = config_path

            def status(self):
                return {'available': False, 'error_code': 'key_required'}

        provider = Provider()
        secret = 'SyntheticKey1234'
        with patch.object(self.hub, 'qq_local_provider', return_value=provider), \
                patch('integrations.qq.key_reader.find_key', return_value=(None, {
                    'state': 'limit_reached', 'limit_reason': 'memory_budget',
                    'candidate': secret, 'read_mib': 512, 'processes': 4, 'tested': 123,
                    'pid': 4567, 'path': 'C:/private/QQ.exe'})), \
                patch.object(self.hub, 'start_sync') as sync:
            self.hub.start_qq_key_discovery(True)
            deadline = time.monotonic() + 2
            while self.hub.qq_key_discovery_status()['state'] == 'running' and time.monotonic() < deadline:
                time.sleep(0.01)
            result = self.hub.qq_key_discovery_status()
        self.assertEqual(result['state'], 'limit_reached')
        self.assertEqual(result['limit_reason'], 'memory_budget')
        self.assertIn('512 MiB 读取预算', result['message'])
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual((result['processes'], result['tested'], result['read_mib']), (4, 123, 512))
        self.assertNotIn('pid', result)
        self.assertNotIn('path', result)
        self.assertEqual(json.loads(config_path.read_text()), original)
        sync.assert_not_called()

        self.hub.qq_key_discovery = {'state': 'running'}
        with patch('integrations.qq.key_reader.find_key', return_value=(None, {
                'state': 'not_found', 'processes': True, 'tested': 20001, 'read_mib': -1})):
            self.hub._run_qq_key_discovery(provider)
        rejected_counts = self.hub.qq_key_discovery_status()
        self.assertNotIn('processes', rejected_counts)
        self.assertNotIn('tested', rejected_counts)
        self.assertNotIn('read_mib', rejected_counts)

    def test_qq_local_configuration_is_private_and_validated(self):
        from integrations.qq import key_store
        with patch('integrations.qq.key_store._crypt', side_effect=lambda data, protect:
                   base64.b64encode(data) if protect else base64.b64decode(data)), \
                patch.object(self.hub, 'start_sync') as sync:
            self.hub.configure('qq', {'data_dir': self.temp.name, 'key': 'fixtureKey123456'})
            path = Path(self.temp.name) / 'qq-local.json'
            saved = json.loads(path.read_text())
            self.assertTrue(saved['key'].startswith('dpapi:v1:'))
            self.assertNotIn('fixtureKey123456', path.read_text())
            self.assertEqual(key_store.load_key(saved['key']), ('fixtureKey123456', False))
            self.hub.configure('qq', {'data_dir': '', 'key': ''})
            self.assertEqual(json.loads((Path(self.temp.name) / 'qq-local.json').read_text()), saved)
            sync.assert_called_with('qq')
            self.hub.configure('qq', {'key': 'Synthetic Key123'})
            protected = json.loads(path.read_text())['key']
            self.assertEqual(key_store.load_key(protected), ('Synthetic Key123', False))
            for values in ({'key': 'short'}, {'key': True}, {'data_dir': '../relative'}):
                with self.assertRaises(ValueError):
                    self.hub.configure('qq', values)

    def test_wechat_image_key_configuration_is_validated_private_and_not_echoed(self):
        secret = 'ImageKey12345678'
        config_path = Path(self.temp.name) / 'wechat.json'
        body = json.dumps({'image_key': secret}).encode()
        with patch('integrations.wechat.provider.CONFIG', config_path), \
                patch('integrations.wechat.provider.WeChatProvider') as provider, \
                patch.object(self.hub, 'start_sync') as sync:
            with self.request('/api/source/wechat/configure',
                              {'Content-Type': 'application/json'}, body) as response:
                result = json.load(response)
            provider.return_value.set_image_key.assert_called_once_with(secret)
            self.assertNotIn(secret, json.dumps(result))
            self.assertNotIn(secret, config_path.read_text(encoding='utf-8'))
            sync.assert_called_once_with('wechat')

    def test_local_qq_image_resolves_for_webui_mcp_and_export_without_online_provider(self):
        from unittest.mock import Mock
        media = Path(self.temp.name) / 'integrations' / 'qq-local' / 'media' / 'fixture.png'
        media.parent.mkdir(parents=True)
        payload = b'\x89PNG\r\n\x1a\nfixture'
        media.write_bytes(payload)
        self.hub.store.ingest('qq', 'local-account', [{'id': 'peer', 'kind': 'private'}], [{
            'id': 'peer:local', 'conversation_id': 'peer', 'timestamp': 100,
            'type': 'text', 'text': 'caption', 'local_database': True,
            'media': [{'name': 'fixture.png', 'type': 'image'}]}])
        provider = SimpleNamespace(media=Mock(return_value={'available': True, 'path': media}))
        self.hub.providers['qq_local'] = provider
        params = {'platform': 'qq', 'account_id': 'local-account', 'conversation_id': 'peer',
                  'message_id': 'peer:local', 'resource_index': 0}
        with patch('integrations.qq.provider.QQProvider') as online:
            result = self.hub.resolve_media(params)
            with self.request(result['media_url']) as response:
                self.assertEqual(response.read(), payload)
                self.assertEqual(response.headers.get_content_type(), 'image/png')
            path, name = self.hub.resolve_resource(params)
            self.assertEqual((path, name), (media, 'fixture.png'))
            message = self.hub.store.get_message('qq', 'local-account', 'peer:local')
            message['type'] = 'image'
            self.assertEqual(self.hub.read_cached_image(message, 0), (payload, 'image/png'))
            online.assert_not_called()
        provider.media.assert_called_with(unittest.mock.ANY, resource_index=0)
        with self.assertRaises(ValueError):
            self.hub.resolve_media({**params, 'resource_index': -1})

    def test_qq_history_status_starts_idle_and_rejects_invalid_range(self):
        self.assertEqual(self.hub.qq_history_status(), {'state': 'idle'})
        with self.assertRaises(HTTPError) as result:
            self.request('/api/qq/history-import')
        self.assertEqual(result.exception.code, 403)
        with self.assertRaises(HTTPError) as result:
            self.request('/api/qq/history-import', {'Content-Type': 'application/json'}, json.dumps({
                'conversation_id': 'peer-uid', 'account_id': 'account',
                'start': '2026-01-02', 'end': '2026-01-01'}).encode())
        self.assertEqual(result.exception.code, 403)

    def test_qq_history_import_rejects_non_integer_budgets(self):
        base = {'conversation_id': 'peer-uid', 'account_id': 'account',
                'start': '2026-01-01', 'end': '2026-01-02'}
        for key, value in (('max_messages', True), ('max_messages', 1.5),
                           ('max_queries', False), ('max_queries', '1.5')):
            with self.subTest(key=key, value=value), self.assertRaisesRegex(PermissionError, '本地数据库'):
                self.hub.start_qq_history_import({**base, key: value})
        self.assertEqual(self.hub.qq_history_status(), {'state': 'idle'})

    def test_qq_roaming_media_resolves_only_inside_local_storage(self):
        media = Path(self.temp.name) / 'qq-roaming' / 'fixture' / 'image.png'
        media.parent.mkdir(parents=True)
        media.write_bytes(b'fixture')
        self.hub.store.ingest('qq', 'account', [{'id': 'peer', 'title': 'QQ'}], [{
            'id': 'peer:1', 'conversation_id': 'peer', 'type': 'image', 'timestamp': 101,
            'roaming_resources': [{'type': 'image', 'name': 'image.png',
                                   'path': media.relative_to(Path(self.temp.name)).as_posix()}]}])
        result = self.hub.resolve_media({'platform': 'qq', 'account_id': 'account',
                                         'conversation_id': 'peer', 'message_id': 'peer:1',
                                         'resource_index': '0'})
        media_key = result['media_url'].rsplit('/', 1)[1]
        self.assertEqual(self.hub.media_paths[media_key], media.resolve())
        with self.assertRaises(ValueError):
            self.hub.resolve_media({'platform': 'qq', 'account_id': 'account',
                                    'conversation_id': 'peer', 'message_id': 'peer:1',
                                    'resource_index': '../outside'})

    def test_qq_roaming_resource_can_be_exported_from_a_text_message(self):
        media = Path(self.temp.name) / 'qq-roaming' / 'fixture' / 'image.png'
        media.parent.mkdir(parents=True)
        media.write_bytes(b'qq-image')
        self.hub.store.ingest('qq', 'account', [{'id': 'peer', 'title': 'QQ'}], [{
            'id': 'peer:resource', 'conversation_id': 'peer', 'type': 'text', 'timestamp': 101,
            'text': 'message with an image resource',
            'roaming_resources': [{'type': 'image', 'name': 'roaming.png',
                                   'path': media.relative_to(Path(self.temp.name)).as_posix()}]}])
        item = {'platform': 'qq', 'account_id': 'account', 'message_id': 'peer:resource',
                'conversation_id': 'peer', 'resource_index': 0}
        resolved, name = self.hub.resolve_resource(item)
        self.assertEqual(resolved, media)
        self.assertEqual(name, 'roaming.png')

    def test_qq_media_alias_resolves_and_exports_indexed_resource(self):
        media = Path(self.temp.name) / 'qq-roaming' / 'fixture' / 'alias-image.png'
        media.parent.mkdir(parents=True)
        media.write_bytes(b'qq-alias-image')
        self.hub.store.ingest('qq', 'account', [{'id': 'peer', 'title': 'QQ'}], [{
            'id': 'peer:media-alias', 'conversation_id': 'peer', 'type': 'text',
            'timestamp': 102, 'media': [{'type': 'image', 'name': 'alias-image.png',
                                         'path': media.relative_to(Path(self.temp.name)).as_posix()}],
        }])
        params = {'platform': 'qq', 'account_id': 'account', 'conversation_id': 'peer',
                  'message_id': 'peer:media-alias', 'resource_index': 0}

        resolved = self.hub.resolve_media(params)
        media_key = resolved['media_url'].rsplit('/', 1)[1]
        self.assertEqual(self.hub.media_paths[media_key], media.resolve())
        exported, name = self.hub.resolve_resource(params)
        self.assertEqual((exported, name), (media, 'alias-image.png'))

    def test_mcp_get_attachment_uses_qq_media_alias_for_selected_resource(self):
        from unittest.mock import Mock
        from hub.mcp_server import MCPServer

        self.hub.store.ingest('qq', 'account', [{'id': 'peer', 'title': 'QQ'}], [{
            'id': 'peer:media-alias-mcp', 'conversation_id': 'peer', 'type': 'text',
            'timestamp': 103, 'media': [{'name': 'image.png', 'type': 'image'},
                                        {'name': 'voice.silk', 'type': 'audio'}],
        }])
        reader = Mock(return_value=(b'synthetic audio', 'audio/silk'))
        mcp = MCPServer(self.hub.store, media_reader=reader)
        result = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
            'name': 'get_attachment', 'arguments': {
                'platform': 'qq', 'account_id': 'account', 'message_id': 'peer:media-alias-mcp',
                'resource_index': 1}}})['result']

        self.assertFalse(result['isError'], result)
        self.assertEqual(result['content'][1]['type'], 'audio')
        reader.assert_called_once()
        self.assertEqual(reader.call_args.args[0]['type'], 'voice')
        self.assertEqual(reader.call_args.args[1], 1)

    def test_qq_roaming_import_persists_message_payload_and_downloaded_media(self):
        from unittest.mock import patch
        from integrations.qq.provider import QQProvider
        job_id = 'roaming-fixture'
        output = Path(self.temp.name) / 'qq-roaming' / job_id
        image = output / 'images' / 'fixture.png'
        image.parent.mkdir(parents=True)
        image.write_bytes(b'png-fixture')
        export = output / 'messages.json'
        export.write_text(json.dumps({'messages': [{
            'id': 'peer:roaming-1', 'seq': '77', 'timestamp': 1791500000000,
            'time': '2026-10-10 10:00:00', 'type': 'image', 'recalled': True,
            'sender': {'uid': 'peer-uid', 'uin': 'account', 'name': 'Peer'},
            'content': {'text': 'caption', 'html': '<p>caption</p>',
                        'elements': [{'type': 'image', 'data': {'filename': 'fixture.png'}}],
                        'mentions': [{'uid': 'mentioned', 'name': 'Mentioned'}],
                        'resources': [{'type': 'image', 'filename': 'fixture.png',
                                       'localPath': 'images/fixture.png', 'size': 11}]}
        }]}), encoding='utf-8')
        provider = type('Provider', (), {
            'status': lambda _self: {'online': True, 'account_id': 'account'},
            'conversations': lambda _self: [{'id': 'peer-uid', 'title': 'Peer', 'kind': 'private', 'chatType': 1}],
            'start_roaming_export': lambda _self, *_args: {'taskId': 'task-fixture'},
            'roaming_task': lambda _self, _task_id: {'status': 'completed', 'filePath': str(export),
                                                       'roamingScan': {'partial': False, 'scannedDays': 30,
                                                                       'messageCount': 1}},
        })()
        job = {'id': job_id, 'state': 'starting', 'error': '', 'progress': '', 'message_count': 0,
               'partial': False, 'scan': {}, 'qce_task_id': '', 'cancel': threading.Event(),
               'conversation_id': 'peer-uid', 'account_id': 'account',
               'start': '2026-09-11', 'end': '2026-10-10'}
        with patch('integrations.qq.provider.QQProvider', return_value=provider), \
                patch.object(self.hub, 'capability_enabled', return_value=True):
            self.hub._run_qq_history_import(job, date(2026, 9, 11), date(2026, 10, 10), 50000, 50000)
        self.assertEqual(job['state'], 'completed')
        self.assertEqual(job['message_count'], 1)
        page = self.hub.store.messages(platform='qq', account_id='account', conversation_id='peer-uid',
                                       limit=10, offset=0)
        imported = page['items'][0]
        self.assertEqual(imported['text'], 'caption')
        self.assertTrue(imported['is_self'])
        self.assertEqual(imported['extra']['roaming_metadata']['seq'], '77')
        self.assertTrue(imported['extra']['roaming_metadata']['recalled'])
        self.assertEqual(imported['extra']['roaming_content']['mentions'][0]['uid'], 'mentioned')
        self.assertTrue(imported['extra']['roaming_resources'][0]['available'])
        self.assertEqual(imported['extra']['roaming_resources'][0]['size'], 11)
        resolved = self.hub.resolve_media({'platform': 'qq', 'account_id': 'account',
                                           'conversation_id': 'peer-uid', 'message_id': 'peer:roaming-1',
                                           'resource_index': '0'})
        key = resolved['media_url'].rsplit('/', 1)[1]
        self.assertEqual(self.hub.media_paths[key], image.resolve())

    def test_qq_start_passes_explicit_refresh_without_leaking_runtime_details(self):
        from unittest.mock import patch
        with patch('integrations.qq.provider.QQProvider') as provider, \
                patch.object(self.hub, 'start_sync') as sync:
            with self.request('/api/qq/start', {'Content-Type': 'application/json'}, b'{"refresh":true}') as response:
                result = json.load(response)
            provider.assert_not_called()
            sync.assert_called_once_with('qq')
        self.assertNotIn('qr_url', result)
        self.assertNotIn('log_file', result)
        self.assertNotIn('qr_image', result)
        self.assertEqual(result['mode'], 'local_database')

    def test_qq_start_returns_only_sanitized_local_runtime_error(self):
        from integrations.qq.provider import QQProviderError
        from unittest.mock import patch

        with patch.object(self.hub, 'start_sync', side_effect=QQProviderError(
                r'failed at C:\\Users\\private\\security.json token=secret')):
            with self.assertRaises(HTTPError) as error:
                self.request('/api/qq/start', {'Content-Type': 'application/json'},
                             b'{"refresh":true}')
        self.assertEqual(error.exception.code, 500)
        self.assertEqual(json.loads(error.exception.read())['error'], '本机操作失败，请检查数据源配置')

    def test_qq_silk_uses_bundled_voice_decoder(self):
        from unittest.mock import Mock, patch
        silk = Path(self.temp.name) / 'voice.silk'
        silk.write_bytes(b'\x02#!SILK_V3\x00')
        wav = Path(self.temp.name) / 'voice.wav'
        wav.write_bytes(b'RIFF-test-WAVE')
        self.hub.store.ingest('qq', 'account', [{'id': 'peer', 'title': 'QQ'}], [
            {'id': 'peer:voice', 'conversation_id': 'peer', 'type': 'voice', 'timestamp': 101}])
        self.hub.providers['qq'] = Mock()
        self.hub.providers['qq'].media.return_value = {'available': True, 'path': str(silk)}
        with patch('integrations.wechat.voice.voice_media_result', return_value={
                'available': True, 'playable': True, 'path': str(wav)}) as decoder:
            result = self.hub.resolve_media({'platform': 'qq', 'account_id': 'account',
                                             'conversation_id': 'peer', 'message_id': 'peer:voice'})
        decoder.assert_called_once_with(str(silk))
        self.assertEqual(self.hub.media_paths[result['media_url'].rsplit('/', 1)[1]], wav.resolve())

    def test_voice_decoder_fallback_downloads_message_bound_silk(self):
        from unittest.mock import Mock
        from integrations.wechat.provider import RUNTIME

        runtime = Path(self.temp.name) / 'wechat-runtime'
        silk = runtime / 'media' / 'cache' / 'voice.silk'
        silk.parent.mkdir(parents=True)
        payload = b'#!SILK_V3 synthetic voice bytes'
        silk.write_bytes(payload)
        self.hub.store.ingest('wechat', 'voice-account', [{'id': 'voice-room'}], [
            {'id': 'voice-1', 'conversation_id': 'voice-room', 'type': 'voice',
             'local_id': 7, 'shard': 'message_0.db', 'timestamp': 101}])
        provider = Mock()
        provider.media.return_value = {
            'available': True, 'playable': False, 'path': str(silk),
            'original_path': str(silk), 'reason': 'voice_decoder_unavailable',
            'mime_type': 'application/octet-stream',
        }
        self.hub.providers['wechat'] = provider
        self.hub.set_capabilities({'media': True})
        query = urlencode({'platform': 'wechat', 'account_id': 'voice-account',
                           'message_id': 'voice-1', 'conversation_id': 'voice-room'})
        with patch('integrations.wechat.provider.RUNTIME', runtime):
            with self.request('/api/media/resolve?' + query) as response:
                resolved = json.load(response)
            self.assertEqual(resolved['mime_type'], 'audio/silk')
            self.assertEqual(resolved['download_name'], 'voice.silk')
            provider.media.assert_called_once_with(
                'voice-room', 7, 'message_0.db', image_tier='mid')
            with self.request(resolved['media_url']) as response:
                self.assertEqual(response.read(), payload)
        with self.assertRaises(HTTPError) as mismatch:
            self.request('/api/media/resolve?' + urlencode({
                'platform': 'wechat', 'account_id': 'other-account',
                'message_id': 'voice-1', 'conversation_id': 'voice-room'}))
        self.assertEqual(mismatch.exception.code, 400)

    def test_voice_decoder_fallback_rejects_path_outside_platform_cache(self):
        from unittest.mock import Mock
        from integrations.wechat.provider import RUNTIME

        runtime = Path(self.temp.name) / 'wechat-runtime'
        (runtime / 'media').mkdir(parents=True)
        outside = Path(self.temp.name) / 'outside.silk'
        outside.write_bytes(b'#!SILK_V3 must not be exposed')
        self.hub.store.ingest('wechat', 'voice-account', [{'id': 'voice-room'}], [
            {'id': 'voice-1', 'conversation_id': 'voice-room', 'type': 'voice',
             'local_id': 7, 'shard': 'message_0.db', 'timestamp': 101}])
        provider = Mock()
        provider.media.return_value = {
            'available': True, 'playable': False, 'path': str(outside),
            'original_path': str(outside), 'reason': 'voice_decode_failed',
        }
        self.hub.providers['wechat'] = provider
        self.hub.set_capabilities({'media': True})
        query = urlencode({'platform': 'wechat', 'account_id': 'voice-account',
                           'message_id': 'voice-1', 'conversation_id': 'voice-room'})
        with patch('integrations.wechat.provider.RUNTIME', runtime):
            with self.request('/api/media/resolve?' + query) as response:
                result = json.load(response)
        self.assertIsNone(result['media_url'])
        self.assertEqual(result['reason'], 'unsafe_media_path')
        self.assertNotIn(outside.resolve(), self.hub.media_paths.values())

    def test_cross_origin_rejected(self):
        with self.assertRaises(HTTPError) as result:
            self.request('/api/search?q=hello', {'Origin': 'https://example.com'})
        self.assertEqual(result.exception.code, 403)

    def test_dns_rebinding_host_rejected(self):
        with self.assertRaises(HTTPError) as result:
            self.request('/api/sources', {'Host': 'attacker.example'})
        self.assertEqual(result.exception.code, 403)

    def test_form_posts_rejected(self):
        with self.assertRaises(HTTPError) as result:
            self.request('/api/settings', {'Content-Type': 'application/x-www-form-urlencoded'}, b'auto_sync=false')
        self.assertEqual(result.exception.code, 403)

    def test_settings_persist_without_secrets(self):
        with self.request('/api/settings', {'Content-Type': 'application/json'}, b'{"auto_sync":false,"sync_interval":3}') as response:
            settings = json.load(response)
            self.assertFalse(settings['auto_sync'])
        with self.request('/api/settings') as response:
            settings = json.load(response)
            self.assertIn('fts_enabled', settings)
        self.assertEqual(json.loads(self.hub.settings_path.read_text())['sync_interval'], 3)

    def test_account_lock_gates_home_api_and_local_media_until_unlock(self):
        headers = {'Content-Type': 'application/json'}
        with self.request('/api/settings', headers,
                          b'{"capabilities":{"account_safety":true}}') as response:
            self.assertTrue(json.load(response)['capabilities']['account_safety']['enabled'])
        with self.request('/api/account-safety/setup', headers,
                          b'{"passphrase":"a sufficiently long local passphrase"}') as response:
            setup = json.load(response)
            cookie = response.headers['Set-Cookie'].split(';', 1)[0]
        self.assertFalse(setup['locked'])
        stored = json.loads(self.hub.settings_path.read_text(encoding='utf-8'))
        self.assertNotIn('a sufficiently long local passphrase', str(stored))
        with self.assertRaises(HTTPError) as settings_denied:
            self.request('/api/settings')
        self.assertEqual(settings_denied.exception.code, 423)
        with self.request('/api/settings', {'Cookie': cookie}) as response:
            unlocked_settings = json.load(response)
        self.assertTrue(unlocked_settings['account_lock_enabled'])
        self.assertNotIn('account_lock', unlocked_settings)
        with self.assertRaises(HTTPError) as denied:
            self.request('/api/search?q=hello')
        self.assertEqual(denied.exception.code, 423)
        self.assertEqual(json.loads(denied.exception.read())['code'], 'account_locked')
        with self.request('/') as response:
            self.assertIn('本机档案已锁定', response.read().decode('utf-8'))
        self.hub.media_paths['locked-test'] = Path(self.temp.name) / 'voice.wav'
        with self.assertRaises(HTTPError) as media_denied:
            self.request('/local-media/locked-test')
        self.assertEqual(media_denied.exception.code, 423)
        with self.assertRaises(HTTPError) as bad_unlock:
            self.request('/api/account-safety/unlock', headers, b'{"passphrase":"wrong passphrase"}')
        self.assertEqual(bad_unlock.exception.code, 401)
        with self.request('/api/account-safety/unlock', headers,
                          b'{"passphrase":"a sufficiently long local passphrase"}') as response:
            cookie = response.headers['Set-Cookie'].split(';', 1)[0]
        with self.request('/api/search?q=hello', {'Cookie': cookie}) as response:
            self.assertEqual(json.load(response)['total'], 1)
        with self.request('/api/account-safety/lock', {**headers, 'Cookie': cookie}, b'{}') as response:
            self.assertIn('Max-Age=0', response.headers['Set-Cookie'])
        with self.assertRaises(HTTPError) as locked_again:
            self.request('/api/search?q=hello')
        self.assertEqual(locked_again.exception.code, 423)
        with self.request('/api/account-safety/unlock', headers,
                          b'{"passphrase":"a sufficiently long local passphrase"}') as response:
            cookie = response.headers['Set-Cookie'].split(';', 1)[0]
        with self.request('/api/account-safety/disable', {**headers, 'Cookie': cookie},
                          b'{"current_passphrase":"a sufficiently long local passphrase"}') as response:
            self.assertFalse(json.load(response)['configured'])
        with self.request('/api/settings') as response:
            settings = json.load(response)
        self.assertFalse(settings['account_lock_enabled'])
        self.assertTrue(settings['capabilities']['account_safety']['enabled'])

    def test_capability_settings_persist_and_disabled_module_preserves_index(self):
        headers = {'Content-Type': 'application/json'}
        with self.request('/api/settings', headers,
                          b'{"capabilities":{"media":false}}') as response:
            settings = json.load(response)
        self.assertFalse(settings['capabilities']['media']['enabled'])
        persisted = json.loads(self.hub.settings_path.read_text(encoding='utf-8'))
        self.assertFalse(persisted['capabilities']['media'])
        reopened = Hub(self.hub.storage, resume_imports=False)
        self.assertFalse(reopened.settings['capabilities']['media'])
        reopened.close()
        with self.assertRaises(HTTPError) as denied:
            self.request('/api/media?platform=wechat')
        self.assertEqual(denied.exception.code, 403)
        problem = json.loads(denied.exception.read())
        self.assertEqual(problem['code'], 'capability_disabled')
        self.assertEqual(problem['capability'], 'media')
        with self.assertRaises(HTTPError) as denied_accounts:
            self.request('/api/accounts')
        self.assertEqual(denied_accounts.exception.code, 403)
        with self.request('/api/search?q=hello') as response:
            self.assertEqual(json.load(response)['total'], 1)
        self.assertEqual(self.hub.store.count('wechat'), 1)

    def test_unsupported_capabilities_expose_reason_and_cannot_be_enabled(self):
        with self.request('/api/settings') as response:
            settings = json.load(response)
        item = settings['capabilities']['moments']
        self.assertTrue(item['available'])
        self.assertFalse(item['enabled'])
        self.assertIn('sns.db', item['reason'])
        self.assertTrue(settings['capabilities']['notifications']['available'])
        self.assertFalse(settings['capabilities']['notifications']['enabled'])
        self.assertTrue(settings['capabilities']['account_safety']['available'])
        self.assertFalse(settings['capabilities']['account_safety']['enabled'])
        for capability in ('anti_recall', 'qq_history'):
            self.assertFalse(settings['capabilities'][capability]['available'])
            self.assertFalse(settings['capabilities'][capability]['enabled'])
            self.assertTrue(settings['capabilities'][capability]['reason'])
        self.assertTrue(settings['capabilities']['auto_export']['available'])
        self.assertFalse(settings['capabilities']['auto_export']['enabled'])
        with self.request('/api/settings', {'Content-Type': 'application/json'},
                          b'{"capabilities":{"moments":true}}') as response:
            self.assertTrue(json.load(response)['capabilities']['moments']['enabled'])
        with self.request('/api/settings') as response:
            self.assertTrue(json.load(response)['capabilities']['moments']['enabled'])

        # Unavailable capabilities must stay disabled even when a caller submits
        # an explicit enable request; do not persist a value that could be
        # interpreted differently by a later code path or version.
        with self.assertRaises(HTTPError) as denied:
            self.request('/api/settings', {'Content-Type': 'application/json'},
                         b'{"capabilities":{"anti_recall":true,"qq_history":true}}')
        self.assertEqual(denied.exception.code, 400)
        denied.exception.read()
        with self.request('/api/settings') as response:
            settings = json.load(response)
        for capability in ('anti_recall', 'qq_history'):
            self.assertFalse(settings['capabilities'][capability]['available'])
            self.assertFalse(settings['capabilities'][capability]['enabled'])
        persisted = json.loads(self.hub.settings_path.read_text(encoding='utf-8'))
        self.assertFalse(persisted['capabilities']['anti_recall'])
        self.assertFalse(persisted['capabilities']['qq_history'])
        reopened = Hub(self.hub.storage, resume_imports=False)
        try:
            self.assertFalse(reopened.settings['capabilities']['anti_recall'])
            self.assertFalse(reopened.settings['capabilities']['qq_history'])
        finally:
            reopened.close()

    def test_moments_endpoint_is_capability_gated_and_uses_local_provider_contract(self):
        with self.assertRaises(HTTPError) as denied:
            self.request('/api/moments')
        self.assertEqual(denied.exception.code, 403)
        self.hub.set_capabilities({'moments': True})
        self.seed_moments('account', [{'id': 'synthetic-post', 'username': 'wxid',
            'createTime': 1791604800, 'contentDesc': 'hello from index'}])
        with self.request('/api/moments?q=hello&username=wxid&start=2026-10-10&limit=5') as response:
            result = json.load(response)
        self.assertEqual(result['items'][0]['id'], 'synthetic-post')
        self.assertEqual(result['total'], 1)

    def test_moments_endpoint_uses_persisted_catalog_without_rescanning_source(self):
        from types import SimpleNamespace
        self.hub.set_capabilities({'moments': True})
        cache = self.seed_moments('account', [{'id': 'indexed', 'contentDesc': 'persisted'}])
        class NoScan:
            reader = SimpleNamespace(wxid='account', account='')
            sns_cache_dir = cache
            def moments(self, **_filters):
                raise AssertionError('web paging must not rescan source database')
        self.hub.providers['wechat'] = NoScan()
        with self.request('/api/moments') as response:
            result = json.load(response)
        self.assertEqual(result['items'][0]['id'], 'indexed')

    def test_persisted_moments_remain_queryable_when_reader_is_unavailable(self):
        self.hub.set_capabilities({'moments': True})
        cache = Path(self.temp.name) / 'offline_sns_cache'
        cache.mkdir()
        image = cache / 'local.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        self.hub.store.replace_moments('account-a', [{
            'id': 'cached-post', 'username': 'author', 'contentDesc': 'cached text',
            'media': [{'_local_path': str(image), 'type': 'image', 'url': 'https://invalid.test/token'}],
        }], cache)
        self.hub.store.replace_moments('account-b', [{
            'id': 'other-post', 'username': 'other', 'contentDesc': 'other account',
        }], cache)
        self.hub.providers.pop('wechat', None)

        with patch('integrations.wechat.provider.WeChatProvider', side_effect=RuntimeError('offline')):
            ambiguous = self.hub.search_moments()
            selected = self.hub.search_moments(account_id='account-a')
            with self.request('/api/moments?account_id=account-a') as response:
                api_result = json.load(response)

        self.assertEqual(ambiguous['reason'], 'moments_account_required')
        self.assertEqual(ambiguous['accounts'], ['account-a', 'account-b'])
        self.assertEqual(selected['account_id'], 'account-a')
        self.assertEqual([post['id'] for post in selected['items']], ['cached-post'])
        media = selected['items'][0]['media'][0]
        self.assertIsNone(media['media_url'])
        self.assertFalse(media['local_available'])
        self.assertNotIn(str(image), json.dumps(selected))
        self.assertNotIn('invalid.test', json.dumps(selected))
        self.assertEqual(api_result['account_id'], 'account-a')
        self.assertEqual(api_result['items'][0]['id'], 'cached-post')

    def test_moments_export_is_filtered_full_and_strips_remote_media_credentials(self):
        self.seed_moments('account', [{
                    'id': 'synthetic-post', 'username': 'wxid_fixture', 'nickname': 'Fixture',
                    'createTime': 1_791_604_800, 'contentDesc': '<script>fixture</script>',
                    'type': 1, 'location': {'city': 'Test'}, 'likes': ['friend'],
                    'comments': [{'content': 'hello'}], 'media': [{
                        'url': 'https://media.invalid/image?token=private', 'thumb': 'private-thumb',
                        'token': 'private-token', 'key': 'private-key', 'encIdx': 'private-index',
                        'md5': 'private-md5', 'type': 'image', 'local_available': False,
                    }],
                }])
        with self.assertRaises(HTTPError) as denied:
            self.request('/api/moments/export')
        self.assertEqual(denied.exception.code, 403)
        self.hub.set_capabilities({'moments': True})
        with self.request('/api/moments/export?q=fixture&username=wxid_fixture&start=2026-10-10') as response:
            payload = response.read()
            self.assertIn('attachment;', response.headers['Content-Disposition'])
            self.assertIn('application/json', response.headers['Content-Type'])
        exported = json.loads(payload)
        self.assertEqual(exported['source'], {'platform': 'wechat', 'database': 'sns.db', 'scope': 'local'})
        self.assertEqual(exported['items'][0]['contentDesc'], '<script>fixture</script>')
        self.assertEqual(exported['items'][0]['comments'][0]['content'], 'hello')
        self.assertEqual(exported['items'][0]['location']['city'], 'Test')
        self.assertEqual(exported['items'][0]['media'], [{'type': 'image', 'local_available': False}])
        self.assertNotIn(b'private-token', payload)
        self.assertNotIn(b'media.invalid', payload)

    def test_moments_export_rejects_payload_over_output_limit(self):
        self.seed_moments('account', [{'id': 'large', 'contentDesc': 'large'}])
        self.hub.set_capabilities({'moments': True})
        with patch('hub.server.render_moments_export_bounded', return_value=(b'x' * (32 * 1024 * 1024 + 1), 'application/json', 'json')):
            with self.assertRaises(HTTPError) as too_large:
                self.request('/api/moments/export')
        self.assertEqual(too_large.exception.code, 413)
        self.assertIn('32 MiB', json.loads(too_large.exception.read())['error'])

    def test_moments_export_reads_all_records_across_small_pages(self):
        self.seed_moments('account', [
            {'id': f'post-{index:03}', 'username': 'fixture', 'contentDesc': f'body-{index}'}
            for index in range(53)
        ])
        self.hub.set_capabilities({'moments': True})
        with self.request('/api/moments/export') as response:
            exported = json.load(response)
        self.assertEqual(len(exported['items']), 53)
        ids = [item['id'] for item in exported['items']]
        self.assertEqual(set(ids), {f'post-{index:03}' for index in range(53)})
        self.assertEqual(len(set(ids)), len(ids))

    def test_moments_export_formats_are_gated_and_use_safe_content_types(self):
        self.seed_moments('account', [{
                    'id': 'post', 'username': '=SUM(1,1)', 'nickname': 'Fixture',
                    'contentDesc': '<script>unsafe</script>', 'createTime': 1,
                    'likes': ['=1+1'], 'comments': [{'nickname': 'A', 'content': '<b>hi</b>'}],
                    'location': {'city': 'HK'}, 'media': [{'url': 'https://private.invalid/?token=x',
                        'token': 'x', 'type': 'image', 'local_available': False}],
                }])
        self.hub.set_capabilities({'moments': True})
        for fmt, mime, extension in [('csv', 'text/csv', '.csv'), ('html', 'text/html', '.html'),
                                     ('md', 'text/markdown', '.md'), ('txt', 'text/plain', '.txt'),
                                     ('xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', '.xlsx')]:
            with self.subTest(fmt=fmt), self.request('/api/moments/export?format=' + fmt) as response:
                payload = response.read()
                self.assertIn(mime, response.headers['Content-Type'])
                self.assertIn(extension, response.headers['Content-Disposition'])
                self.assertNotIn(b'private.invalid', payload)
                self.assertNotIn(b'token=x', payload)
                if fmt == 'html':
                    self.assertIn(b'&lt;script&gt;', payload)
                    self.assertNotIn(b'<script>', payload)
                if fmt == 'xlsx':
                    import io
                    import zipfile
                    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                        self.assertEqual(archive.testzip(), None)
                        sheet = archive.read('xl/worksheets/sheet1.xml')
                    self.assertIn(b't="inlineStr"', sheet)
                    self.assertIn(b'&lt;script&gt;', sheet)
        with self.assertRaises(HTTPError) as unsupported:
            self.request('/api/moments/export?format=ods')
        self.assertEqual(unsupported.exception.code, 400)

    def test_mcp_client_config_hides_moments_tool_until_enabled(self):
        with self.request('/api/mcp') as response:
            disabled = json.load(response)['config_text']
        self.assertNotIn('search_moments', disabled)
        self.hub.set_capabilities({'moments': True})
        with self.request('/api/mcp') as response:
            enabled = json.load(response)['config_text']
        self.assertIn('search_moments', enabled)

    def test_mcp_client_config_exposes_each_capability_gated_tool(self):
        self.hub.set_capabilities({'moments': True, 'relationship_footprint': True})
        with self.request('/api/mcp') as response:
            config = json.load(response)['config_text']
        for name in ('get_moment_media', 'get_contact_avatar',
                     'list_qq_forward_attachments', 'get_qq_forward_attachment',
                     'list_relationship_footprint', 'get_relationship_timeline',
                     'get_relationship_achievements'):
            self.assertIn(f'- {name}', config)

    def test_mcp_client_config_gates_group_roster_with_chat(self):
        self.hub.set_capabilities({'chat': True})
        with self.request('/api/mcp') as response:
            enabled = json.load(response)['config_text']
        self.assertIn('- list_group_members', enabled)
        self.hub.set_capabilities({'chat': False})
        with self.request('/api/mcp') as response:
            disabled = json.load(response)['config_text']
        self.assertNotIn('- list_group_members', disabled)

    def test_message_event_stream_is_gated_filtered_and_body_free(self):
        with self.assertRaises(HTTPError) as denied:
            self.request('/api/events/messages')
        self.assertEqual(denied.exception.code, 403)
        self.hub.set_capabilities({'notifications': True})
        self.hub.settings['notification_filters'] = {
            'platforms': ['qq'],
            'mode': 'allowlist',
            'allowed_conversations': ['["qq","account","muted"]',
                                      '["qq","account","visible"]'],
            'muted_conversations': ['["qq","account","muted"]'],
        }
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        try:
            connection.request('GET', '/api/events/messages')
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn('text/event-stream', response.getheader('Content-Type'))
            checkpoint = self.read_sse_block(response)
            self.assertTrue(checkpoint[0].startswith(b'retry:'))
            self.assertTrue(any(line.startswith(b'event: checkpoint') for line in checkpoint))
            self.assertTrue(any(line.startswith(b'id: 1') for line in checkpoint))
            self.hub.store.ingest('wechat', 'account', [{'id': 'new-wechat', 'title': 'WeChat'}], [
                {'id': 'event-wechat', 'conversation_id': 'new-wechat', 'timestamp': 200,
                 'text': 'wechat private fixture'}])
            self.hub.store.ingest('qq', 'account', [{'id': 'muted', 'title': 'Muted'}], [
                {'id': 'event-muted', 'conversation_id': 'muted', 'timestamp': 201,
                 'text': 'muted private fixture'}])
            self.hub.store.ingest('qq', 'account', [{'id': 'not-allowed', 'title': 'Not allowed'}], [
                {'id': 'event-not-allowed', 'conversation_id': 'not-allowed', 'timestamp': 201,
                 'text': 'not allowed private fixture'}])
            self.hub.store.ingest('qq', 'account', [{'id': 'visible', 'title': 'Visible'}], [
                {'id': 'event-visible', 'conversation_id': 'visible', 'timestamp': 202,
                 'text': 'visible private fixture'}])
            message_block = []
            for _ in range(8):
                block = self.read_sse_block(response)
                if any(line.startswith(b'event: message') for line in block):
                    message_block = block
                    break
            payload_line = next(line for line in message_block if line.startswith(b'data: '))
            payload = json.loads(payload_line.removeprefix(b'data: '))
            self.assertEqual(payload['conversation_id'], 'visible')
            self.assertEqual(payload['platform'], 'qq')
            self.assertEqual(set(payload), {
                'platform', 'account_id', 'conversation_id',
                'conversation_title', 'conversation_kind'})
            self.assertNotIn('private fixture', payload_line.decode())
            self.assertNotIn('text', payload)
        finally:
            connection.close()

    def test_message_event_stream_reconnect_keeps_initial_cursor_before_first_message(self):
        self.hub.set_capabilities({'notifications': True})
        with self.hub.store._lock:
            self.hub.store._require_connection().execute('DELETE FROM messages')
        self.assertEqual(self.hub.store.message_rowid(), 0)
        first = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        try:
            first.request('GET', '/api/events/messages')
            response = first.getresponse()
            self.assertEqual(response.status, 200)
            checkpoint = self.read_sse_block(response)
            self.assertTrue(any(line.startswith(b'event: checkpoint') for line in checkpoint))
            self.assertTrue(any(line.startswith(b'id: 0') for line in checkpoint))
        finally:
            first.close()

        self.hub.store.ingest('qq', 'account', [{'id': 'after-disconnect', 'title': 'Fixture'}], [
            {'id': 'after-disconnect-event', 'conversation_id': 'after-disconnect',
             'timestamp': 204, 'text': 'synthetic message during reconnect gap'}])
        self.assertTrue(any(row['conversation_id'] == 'after-disconnect'
                            for row in self.hub.store.incoming_message_events(0)))

        resumed = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        try:
            resumed.request('GET', '/api/events/messages', headers={'Last-Event-ID': '0'})
            response = resumed.getresponse()
            self.assertEqual(response.status, 200)
            self.read_sse_block(response)
            message = []
            for _ in range(5):
                block = self.read_sse_block(response)
                message.extend(block)
                if any(line.startswith(b'event: message') for line in block):
                    break
            self.assertTrue(any(line.startswith(b'event: message') for line in message))
            payload = json.loads(next(line[6:] for line in message if line.startswith(b'data: ')))
            self.assertEqual(payload['conversation_id'], 'after-disconnect')
            self.assertNotIn('text', payload)
        finally:
            resumed.close()

    def test_open_message_event_stream_closes_when_notifications_are_disabled(self):
        self.hub.set_capabilities({'notifications': True})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        try:
            connection.request('GET', '/api/events/messages')
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            checkpoint = self.read_sse_block(response)
            self.assertTrue(checkpoint[0].startswith(b'retry:'))
            self.assertTrue(any(line.startswith(b'event: checkpoint') for line in checkpoint))
            keepalive = self.read_sse_block(response)
            self.assertTrue(keepalive[0].startswith(b': keep-alive'))

            self.hub.set_capabilities({'notifications': False})
            self.hub.store.ingest('qq', 'account', [{'id': 'revoked-room', 'title': 'Fixture'}], [
                {'id': 'revoked-event', 'conversation_id': 'revoked-room',
                 'timestamp': 203, 'text': 'must not reach the revoked stream'}])

            self.assertEqual(response.readline(), b'')
        finally:
            connection.close()

    def test_open_message_event_stream_closes_when_account_is_locked(self):
        passphrase = 'a sufficiently long local passphrase'
        self.hub.account_safety.set_passphrase(passphrase)
        token, error = self.hub.account_safety.unlock(passphrase)
        self.assertEqual(error, '')
        self.hub.set_capabilities({'notifications': True})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        try:
            connection.request('GET', '/api/events/messages', headers={
                'Cookie': f'lmh_session={token}'})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            checkpoint = self.read_sse_block(response)
            self.assertTrue(checkpoint[0].startswith(b'retry:'))
            self.assertTrue(any(line.startswith(b'event: checkpoint') for line in checkpoint))
            keepalive = self.read_sse_block(response)
            self.assertTrue(keepalive[0].startswith(b': keep-alive'))

            self.hub.account_safety.lock()
            self.assertEqual(response.readline(), b'')
        finally:
            connection.close()

    def test_notification_filter_settings_are_validated_and_persisted(self):
        headers = {'Content-Type': 'application/json'}
        filters = {'platforms': ['wechat', 'qq'],
                   'mode': 'allowlist',
                   'style': 'silent',
                   'allowed_conversations': ['["qq","account","room"]'],
                   'muted_conversations': ['["qq","account","room"]']}
        with self.request('/api/settings', headers,
                          json.dumps({'notification_filters': filters}).encode()) as response:
            saved = json.load(response)['notification_filters']
        self.assertEqual(saved, filters)
        self.assertEqual(json.loads(self.hub.settings_path.read_text(encoding='utf-8'))[
            'notification_filters'], filters)
        with self.assertRaises(HTTPError) as invalid:
            self.request('/api/settings', headers,
                         b'{"notification_filters":{"platforms":["all"]}}')
        self.assertEqual(invalid.exception.code, 400)
        with self.assertRaises(HTTPError) as invalid_mode:
            self.request('/api/settings', headers,
                         b'{"notification_filters":{"mode":"allow-everything"}}')
        self.assertEqual(invalid_mode.exception.code, 400)
        with self.assertRaises(HTTPError) as invalid_allowlist:
            self.request('/api/settings', headers,
                         b'{"notification_filters":{"allowed_conversations":["not-an-identity"]}}')
        self.assertEqual(invalid_allowlist.exception.code, 400)
        with self.assertRaises(HTTPError) as invalid_style:
            self.request('/api/settings', headers,
                         b'{"notification_filters":{"style":"html"}}')
        self.assertEqual(invalid_style.exception.code, 400)

    def test_auto_export_is_gated_configurable_and_runs_against_local_index(self):
        headers = {'Content-Type': 'application/json'}
        with self.assertRaises(HTTPError) as status_denied:
            self.request('/api/auto-export')
        self.assertEqual(status_denied.exception.code, 403)
        with self.assertRaises(HTTPError) as denied:
            self.request('/api/auto-export/run', headers, b'{}')
        self.assertEqual(denied.exception.code, 403)
        with self.request('/api/settings', headers,
                          b'{"capabilities":{"auto_export":true}}') as response:
            self.assertTrue(json.load(response)['capabilities']['auto_export']['enabled'])
        with self.request('/api/auto-export') as response:
            self.assertFalse(json.load(response)['running'])

        output = Path(self.temp.name) / 'scheduled-exports'
        config = json.dumps({'enabled': False, 'interval_hours': 3, 'format': 'json',
                             'platform': 'wechat', 'output_dir': str(output)}).encode()
        with self.request('/api/auto-export/configure', headers, config) as response:
            saved = json.load(response)
        self.assertEqual(saved['config']['interval_hours'], 3)
        with self.request('/api/auto-export/run', headers, b'{}') as response:
            self.assertTrue(json.load(response)['running'])
        deadline = time.monotonic() + 4
        status = None
        while time.monotonic() < deadline:
            with self.request('/api/auto-export') as response:
                status = json.load(response)
            if not status['running']:
                break
            time.sleep(0.02)
        self.assertEqual(status['state'], 'completed')
        exported = output / status['config']['last_file']
        records = json.loads(exported.read_text(encoding='utf-8'))
        self.assertEqual(records[0]['text'], 'hello <script>')

    def test_resource_export_is_capability_gated_and_downloads_local_archive(self):
        source = Path(self.temp.name) / 'synthetic-media.png'
        source.write_bytes(b'synthetic image bytes')
        self.hub.store.ingest('wechat', 'fixture', [{'id': 'media-room', 'title': 'Fixture'}], [
            {'id': 'media-1', 'conversation_id': 'media-room', 'timestamp': 1,
             'type': 'image', 'text': '', 'file_name': 'fixture.png'}])
        self.hub.resource_jobs.resolver = lambda _request: (source, 'fixture.png')
        headers = {'Content-Type': 'application/json'}
        payload = json.dumps({'items': [{
            'platform': 'wechat', 'account_id': 'fixture', 'message_id': 'media-1',
            'conversation_id': 'media-room'
        }]}).encode()
        with self.request('/api/settings', headers, b'{"capabilities":{"media":false}}'):
            pass
        with self.assertRaises(HTTPError) as denied:
            self.request('/api/resource-jobs', headers, payload)
        self.assertEqual(denied.exception.code, 403)
        self.assertEqual(self.hub.resource_jobs._jobs, {})

        with self.request('/api/settings', headers, b'{"capabilities":{"media":true}}'):
            pass
        with self.request('/api/resource-jobs', headers, payload) as response:
            job = json.load(response)
        deadline = time.monotonic() + 3
        state = job
        while state['state'] not in ('completed', 'failed', 'cancelled') and time.monotonic() < deadline:
            time.sleep(0.01)
            with self.request('/api/resource-jobs/' + job['id']) as response:
                state = json.load(response)
        self.assertEqual(state['state'], 'completed')
        self.assertEqual(state['exported'], 1)
        with self.request(state['download_url']) as response:
            self.assertEqual(response.headers.get_content_type(), 'application/zip')
            self.assertTrue(response.read().startswith(b'PK'))
        archive = self.hub.resource_jobs.download_path(job['id'])
        self.assertTrue(archive.is_file())
        (self.hub.resource_jobs.root / 'keep.txt').write_text('retain', encoding='utf-8')
        self.hub.delete_local_index('DELETE_LOCAL_INDEX')
        self.assertFalse(archive.exists())
        self.assertEqual((self.hub.resource_jobs.root / 'keep.txt').read_text(encoding='utf-8'), 'retain')

    def test_disabling_media_cancels_active_resource_export(self):
        entered = threading.Event()
        release = threading.Event()
        source = Path(self.temp.name) / 'slow-media.png'
        source.write_bytes(b'synthetic image bytes')

        def slow_resolver(_request):
            entered.set()
            release.wait(2)
            return source, source.name

        self.hub.set_capabilities({'media': True})
        self.hub.resource_jobs.resolver = slow_resolver
        job = self.hub.resource_jobs.start([{
            'platform': 'wechat', 'account_id': 'fixture', 'message_id': 'media-1',
            'conversation_id': 'media-room',
        }])
        self.assertTrue(entered.wait(1))
        worker = self.hub.resource_jobs._jobs[job['id']]
        disable = threading.Thread(target=self.hub.set_capabilities, args=({'media': False},))
        disable.start()
        self.assertTrue(worker['cancel'].wait(1))
        release.set()
        disable.join(2)

        self.assertFalse(disable.is_alive())
        self.assertEqual(self.hub.resource_jobs.status(job['id'])['state'], 'cancelled')
        self.assertIsNone(self.hub.resource_jobs.download_path(job['id']))
        self.hub.set_capabilities({'media': True})
        self.assertFalse(self.hub.resource_jobs._paused)

    def test_resource_zip_uses_each_platform_resolver_and_redacts_local_paths(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        sources = {
            'wechat': Path(self.temp.name) / 'wechat-mid.png',
            'wecom': Path(self.temp.name) / 'wecom-file.png',
            'qq': Path(self.temp.name) / 'qq-roaming' / 'fixture' / 'qq-file.png',
        }
        payloads = {'wechat': b'wechat-image', 'wecom': b'wecom-image', 'qq': b'qq-image'}
        for platform, path in sources.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payloads[platform])

        self.hub.store.ingest('wechat', 'fixture', [{'id': 'wx-room', 'title': 'WeChat'}], [{
            'id': 'wx-image', 'conversation_id': 'wx-room', 'timestamp': 1,
            'type': 'image', 'file_name': 'wechat.png', 'local_id': 7,
            'shard': 'message_0.db',
        }])
        self.hub.store.ingest('wecom', 'fixture', [{'id': 'wc-room', 'title': 'WeCom'}], [{
            'id': 'wc-image', 'conversation_id': 'wc-room', 'timestamp': 2,
            'type': 'text', 'text': 'caption with an attached image', 'attachments': [
                {'name': 'wecom.png', 'type': 'image'}],
        }])
        self.hub.store.ingest('qq', 'fixture', [{'id': 'qq-room', 'title': 'QQ'}], [{
            'id': 'qq-text', 'conversation_id': 'qq-room', 'timestamp': 3,
            'type': 'text', 'text': 'caption', 'roaming_resources': [{
                'name': 'qq.png', 'type': 'image',
                'path': sources['qq'].relative_to(Path(self.temp.name)).as_posix(),
            }],
        }])

        wechat = Mock()
        wechat.media.return_value = {'available': True, 'path': str(sources['wechat'])}
        wecom = Mock()
        wecom.media.return_value = {'available': True, 'path': str(sources['wecom']),
                                    'name': 'wecom.png'}
        self.hub.providers.update({'wechat': wechat, 'wecom': wecom})
        self.hub.set_capabilities({'media': True})

        items = [
            {'platform': 'wechat', 'account_id': 'fixture', 'message_id': 'wx-image',
             'conversation_id': 'wx-room'},
            {'platform': 'wecom', 'account_id': 'fixture', 'message_id': 'wc-image',
             'conversation_id': 'wc-room', 'resource_index': 0},
            {'platform': 'qq', 'account_id': 'fixture', 'message_id': 'qq-text',
             'conversation_id': 'qq-room', 'resource_index': 0},
        ]
        headers = {'Content-Type': 'application/json'}
        with self.request('/api/resource-jobs', headers,
                          json.dumps({'items': items}).encode()) as response:
            job = json.load(response)
        deadline = time.monotonic() + 3
        state = job
        while state['state'] not in ('completed', 'failed', 'cancelled') and time.monotonic() < deadline:
            time.sleep(0.01)
            with self.request('/api/resource-jobs/' + job['id']) as response:
                state = json.load(response)

        self.assertEqual(state['state'], 'completed')
        self.assertEqual(state['exported'], 3)
        wechat.media.assert_called_once_with('wx-room', 7, 'message_0.db', image_tier='best')
        wecom.media.assert_called_once_with('wc-image', resource_index=0)
        with self.request(state['download_url']) as response:
            with ZipFile(io.BytesIO(response.read())) as archive:
                manifest = json.loads(archive.read('manifest.json'))['items']
                self.assertEqual([row['platform'] for row in manifest],
                                 ['wechat', 'wecom', 'qq'])
                self.assertTrue(all(row['status'] == 'exported' for row in manifest))
                self.assertEqual([row['account_id'] for row in manifest], ['fixture'] * 3)
                self.assertEqual([row['message_id'] for row in manifest],
                                 ['wx-image', 'wc-image', 'qq-text'])
                self.assertEqual([archive.read('attachments/' + row['file']) for row in manifest],
                                 [payloads['wechat'], payloads['wecom'], payloads['qq']])
                serialized_manifest = json.dumps(manifest)
                self.assertNotIn(str(self.temp.name), serialized_manifest)
                self.assertNotIn('qq-roaming', serialized_manifest)
        self.assertEqual({platform: path.read_bytes() for platform, path in sources.items()}, payloads)

    def test_wechat_media_http_resolution_covers_cached_types_without_source_mutation(self):
        from urllib.parse import urlencode
        from unittest.mock import Mock, patch

        account = 'wechat-media-fixture'
        room = 'media-room'
        media = {
            'image': (3, 'image.png', b'\x89PNG\r\n\x1a\nwechat-image'),
            'voice': (34, 'voice.wav', b'RIFF\x04\x00\x00\x00WAVEwechat-voice'),
            'video': (43, 'video.mp4', b'\x00\x00\x00\x18ftypisomwechat-video'),
            'file': (49, 'document.pdf', b'%PDF-1.7\nwechat-file'),
        }
        files = {}
        messages = []
        for index, (kind, (local_type, filename, payload)) in enumerate(media.items(), start=1):
            source = Path(self.temp.name) / filename
            source.write_bytes(payload)
            files[kind] = (source, payload, hashlib.sha256(payload).hexdigest())
            messages.append({
                'id': f'{kind}-message', 'conversation_id': room, 'timestamp': index,
                'type': kind, 'local_id': index, 'local_type': local_type,
                'shard': 'message_0.db', 'file_name': filename,
            })
        self.hub.store.ingest('wechat', account, [{'id': room, 'title': 'Synthetic'}], messages)

        provider = Mock()

        def resolve(_room, local_id, shard, image_tier='mid'):
            self.assertEqual((shard, image_tier), ('message_0.db', 'mid'))
            # local ids are assigned in insertion order; map them back without inspecting file contents.
            kind = list(media)[local_id - 1]
            return {'available': True, 'path': str(files[kind][0])}

        provider.media.side_effect = resolve
        self.hub.providers['wechat'] = provider
        self.hub.set_capabilities({'media': True})
        voice_file = files['voice'][0]
        with patch('integrations.wechat.voice.voice_media_result', return_value={
                'available': True, 'playable': True, 'path': str(voice_file)}):
            for index, kind in enumerate(media, start=1):
                query = urlencode({'platform': 'wechat', 'account_id': account,
                                   'message_id': f'{kind}-message', 'conversation_id': room})
                with self.request('/api/media/resolve?' + query) as response:
                    result = json.load(response)
                self.assertRegex(result['media_url'], r'^/local-media/[A-Za-z0-9_-]{32,64}$')
                with self.request(result['media_url']) as response:
                    self.assertEqual(response.read(), files[kind][1])
                provider.media.assert_any_call(room, index, 'message_0.db', image_tier='mid')

        provider.media.reset_mock()
        provider.media.side_effect = None
        provider.media.return_value = {'available': False, 'path': None,
                                       'reason': 'not_cached_or_key_unavailable'}
        query = urlencode({'platform': 'wechat', 'account_id': account,
                           'message_id': 'image-message', 'conversation_id': room})
        with self.request('/api/media/resolve?' + query) as response:
            self.assertIsNone(json.load(response)['media_url'])
        provider.media.assert_called_once_with(room, 1, 'message_0.db', image_tier='mid')
        voice_query = urlencode({'platform': 'wechat', 'account_id': account,
                                 'message_id': 'voice-message', 'conversation_id': room})
        provider.media.reset_mock()
        provider.media.return_value = {
            'available': False, 'path': None, 'reason': 'audio_not_downloaded',
            'bytes': 0, 'server_id': 'must-not-leak',
        }
        with self.request('/api/media/resolve?' + voice_query) as response:
            voice_result = json.load(response)
        self.assertIsNone(voice_result['media_url'])
        self.assertEqual(voice_result['reason'], 'audio_not_downloaded')
        self.assertEqual(voice_result['message'], '微信本机尚未缓存这段语音')
        self.assertNotIn('server_id', voice_result)
        self.assertNotIn('path', voice_result)
        provider.media.assert_called_once_with(room, 2, 'message_0.db', image_tier='mid')
        for source, payload, digest in files.values():
            self.assertEqual(source.read_bytes(), payload)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)

    def test_moments_resource_resolver_exports_cached_media_without_paths(self):
        cache = self.seed_moments('wx-account', [])
        image = cache / 'moments-image.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\nsynthetic-moments-image')
        self.hub.store.replace_moments('wx-account', [{
            'id': 'post-1', 'username': 'author', 'media': [{
                'type': 'image', '_local_path': str(image),
                'url': 'https://media.invalid/private-token', 'token': 'private-token',
            }],
        }], cache)
        self.hub.set_capabilities({'media': True, 'moments': True})
        item = {'resource_kind': 'moment', 'platform': 'wechat', 'account_id': 'wx-account',
                'post_id': 'post-1', 'media_index': 0, 'variant': 'original'}
        headers = {'Content-Type': 'application/json'}
        with self.request('/api/resource-jobs', headers,
                          json.dumps({'items': [item]}).encode()) as response:
            job = json.load(response)
        deadline = time.monotonic() + 3
        state = job
        while state['state'] not in ('completed', 'failed', 'cancelled') and time.monotonic() < deadline:
            time.sleep(0.01)
            with self.request('/api/resource-jobs/' + job['id']) as response:
                state = json.load(response)
        self.assertEqual((state['state'], state['exported']), ('completed', 1))
        with self.request(state['download_url']) as response:
            with ZipFile(io.BytesIO(response.read())) as archive:
                manifest = json.loads(archive.read('manifest.json'))['items']
                self.assertEqual(archive.read('attachments/001-moments-image.png'), image.read_bytes())
        self.assertEqual(manifest, [{**item, 'status': 'exported', 'file': '001-moments-image.png',
                                     'bytes': image.stat().st_size,
                                     'sha256': hashlib.sha256(image.read_bytes()).hexdigest()}])
        self.assertNotIn(str(self.temp.name), json.dumps(manifest))
        self.assertNotIn('private-token', json.dumps(manifest))
        self.assertEqual(image.read_bytes(), b'\x89PNG\r\n\x1a\nsynthetic-moments-image')

        with self.assertRaises(HTTPError) as rejected:
            self.request('/api/resource-jobs', headers,
                         json.dumps({'items': [{**item, 'path': str(image)}]}).encode())
        self.assertEqual(rejected.exception.code, 400)

    def test_qq_cache_resource_resolver_exports_by_opaque_identity(self):
        from unittest.mock import Mock

        source = Path(self.temp.name) / 'qce-cache' / 'image.png'
        source.parent.mkdir()
        payload = b'\x89PNG\r\n\x1a\nsynthetic-qq-cache-image'
        source.write_bytes(payload)
        provider = Mock()
        provider.resolve_cached_resource.return_value = (source, 'image.png')
        self.hub.providers['qq'] = provider
        self.hub.set_capabilities({'media': True})

        item = {'resource_kind': 'qq_cache', 'platform': 'qq',
                'account_id': 'qq-account', 'resource_id': 'opaque-resource-id'}
        headers = {'Content-Type': 'application/json'}
        with self.request('/api/resource-jobs', headers,
                          json.dumps({'items': [item]}).encode()) as response:
            job = json.load(response)
        deadline = time.monotonic() + 3
        state = job
        while state['state'] not in ('completed', 'failed', 'cancelled') and time.monotonic() < deadline:
            time.sleep(0.01)
            with self.request('/api/resource-jobs/' + job['id']) as response:
                state = json.load(response)

        self.assertEqual((state['state'], state['exported']), ('completed', 1))
        provider.resolve_cached_resource.assert_called_once_with('opaque-resource-id', 'qq-account')
        with self.assertRaisesRegex(ValueError, '图片质量选项仅适用于微信聊天图片'):
            self.hub.resolve_media({**item, 'image_tier': 'best'})
        with self.request(state['download_url']) as response:
            with ZipFile(io.BytesIO(response.read())) as archive:
                manifest = json.loads(archive.read('manifest.json'))['items']
                self.assertEqual(archive.read('attachments/001-image.png'), payload)
        self.assertEqual(manifest, [{**item, 'status': 'exported',
                                     'file': '001-image.png', 'bytes': len(payload),
                                     'sha256': hashlib.sha256(payload).hexdigest()}])
        self.assertNotIn(str(self.temp.name), json.dumps(manifest))
        self.assertNotIn('qce-cache', json.dumps(manifest))
        self.assertNotIn('https://', json.dumps(manifest))
        self.assertEqual(source.read_bytes(), payload)

        with self.assertRaises(HTTPError) as rejected:
            self.request('/api/resource-jobs', headers,
                         json.dumps({'items': [{**item, 'url': 'http://127.0.0.1/private',
                                                'path': str(source)}]}).encode())
        self.assertEqual(rejected.exception.code, 400)

    def test_resource_export_can_be_cancelled_over_http(self):
        source = Path(self.temp.name) / 'slow-media.png'
        source.write_bytes(b'synthetic image bytes')
        started = threading.Event()
        release = threading.Event()
        def slow_resolver(_request):
            started.set()
            release.wait(2)
            return source, source.name
        self.hub.resource_jobs.resolver = slow_resolver
        headers = {'Content-Type': 'application/json'}
        payload = json.dumps({'items': [{'platform': 'wechat', 'account_id': 'fixture',
            'message_id': 'room:1', 'conversation_id': 'room'}]}).encode()
        with self.request('/api/resource-jobs', headers, payload) as response:
            job = json.load(response)
        self.assertTrue(started.wait(1))
        cancel_request = Request(self.url + '/api/resource-jobs/' + job['id'] + '/cancel', data=b'')
        with urlopen(cancel_request, timeout=3) as response:
            self.assertEqual(json.load(response)['state'], 'cancelling')
        release.set()
        deadline = time.monotonic() + 3
        state = None
        while time.monotonic() < deadline:
            with self.request('/api/resource-jobs/' + job['id']) as response:
                state = json.load(response)
            if state['state'] in ('cancelled', 'failed', 'completed'):
                break
            time.sleep(0.01)
        self.assertEqual(state['state'], 'cancelled')
        self.assertIsNone(self.hub.resource_jobs.download_path(job['id']))

    def test_disabled_organization_denies_direct_metadata_api_without_side_effects(self):
        headers = {'Content-Type': 'application/json'}
        with self.request('/api/settings', headers,
                          b'{"capabilities":{"organization":false}}'):
            pass
        body = json.dumps({'platform': 'wechat', 'account_id': 'account',
                           'conversation_id': 'room', 'note': 'must not persist'}).encode()
        with self.assertRaises(HTTPError) as result:
            self.request('/api/conversation-metadata', headers, body)
        self.assertEqual(result.exception.code, 403)
        self.assertEqual(self.hub.store.conversations('wechat')['items'][0]['note'], '')
        self.assertEqual(self.hub.store.operation_log(), [])

    def test_disabled_sync_is_rejected_by_task_entry_point(self):
        self.hub.set_capabilities({'sync': False})
        with self.assertRaisesRegex(PermissionError, '已在设置中关闭'):
            self.hub.start_sync('wechat')
        self.assertEqual(self.hub.jobs, set())

    def test_disabling_sync_cancels_inflight_snapshot_before_index_write(self):
        import sys
        import types

        entered_refresh = threading.Event()
        release_refresh = threading.Event()
        ingests = []

        class BlockingProvider:
            def refresh(self):
                entered_refresh.set()
                self_outer.assertTrue(release_refresh.wait(3))

            def list_conversations(self, _limit):
                return []

        self_outer = self
        module = types.ModuleType('integrations.wecom.adapter')
        module.WeComAdapter = BlockingProvider
        with patch.dict(sys.modules, {'integrations.wecom.adapter': module}), \
                patch.object(self.hub.store, 'ingest', side_effect=lambda *args, **kwargs: ingests.append(args)):
            try:
                self.hub.start_sync('wecom')
                self.assertTrue(entered_refresh.wait(2), 'sync should reach the blocked snapshot refresh')
                self.hub.set_capabilities({'sync': False})
                release_refresh.set()
                deadline = time.monotonic() + 3
                while self.hub.jobs and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertFalse(self.hub.jobs, 'cancelled sync should terminate')
                self.assertEqual(ingests, [])
            finally:
                release_refresh.set()

    def test_disabling_imports_serializes_with_inflight_index_write(self):
        entered = threading.Event()
        release = threading.Event()
        ingests = []
        cancel = threading.Event()
        original_ingest = self.hub.store.ingest

        def blocking_ingest(*args, **kwargs):
            ingests.append(args)
            entered.set()
            self.assertTrue(release.wait(3), 'in-flight import should be released')
            return original_ingest(*args, **kwargs)

        self.hub.import_cancellations['fixture-import'] = cancel
        with patch.object(self.hub.store, 'ingest', side_effect=blocking_ingest):
            worker = threading.Thread(target=self.hub._import_ingest, args=(
                cancel, 'wecom', 'fixture', [{'id': 'room'}],
                [{'id': 'message', 'conversation_id': 'room', 'type': 'text', 'text': 'fixture'}]))
            worker.start()
            try:
                self.assertTrue(entered.wait(2), 'import should reach its indexed write')
                disable = threading.Thread(target=self.hub.set_capabilities, args=({'imports': False},))
                disable.start()
                deadline = time.monotonic() + 2
                while not disable.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertTrue(disable.is_alive(), 'capability change must wait for the in-flight write')
                self.assertTrue(self.hub.capability_enabled('imports'))
                release.set()
                worker.join(3)
                disable.join(3)
                self.assertFalse(worker.is_alive())
                self.assertFalse(disable.is_alive())
                self.assertFalse(self.hub.capability_enabled('imports'))
                self.assertTrue(cancel.is_set())
                self.assertIsNone(self.hub._import_ingest(
                    cancel, 'wecom', 'fixture', [{'id': 'room'}],
                    [{'id': 'late', 'conversation_id': 'room', 'type': 'text', 'text': 'late'}]))
                self.assertEqual(len(ingests), 1)
            finally:
                release.set()
                worker.join(3)
                self.hub.import_cancellations.pop('fixture-import', None)

    def test_disabled_qq_history_cannot_write_late_batch(self):
        cancel = threading.Event()
        self.assertFalse(self.hub.capability_enabled('qq_history'))
        with patch.object(self.hub.store, 'ingest') as ingest:
            self.assertFalse(self.hub._qq_history_ingest(
                cancel, 'qq', 'fixture', [{'id': 'room'}],
                [{'id': 'late', 'conversation_id': 'room', 'type': 'text', 'text': 'late'}]))
        ingest.assert_not_called()

    def test_disabling_sync_during_moments_catalog_prevents_catalog_write(self):
        import sys
        import types

        entered_moments = threading.Event()
        release_moments = threading.Event()
        replacements = []

        class Reader:
            wxid = 'fixture-account'
            account = ''

        class BlockingProvider:
            reader = Reader()
            sns_cache_dir = None

            def contacts(self):
                return []

            def sessions(self):
                return []

            def moments(self, **_kwargs):
                entered_moments.set()
                self_outer.assertTrue(release_moments.wait(3))
                return {'available': True, 'truncated': False, 'items': [{'id': 'late-post'}]}

            def status(self):
                return {'state': 'connected'}

        self_outer = self
        module = types.ModuleType('integrations.wechat.provider')
        module.WeChatProvider = BlockingProvider
        cancel = threading.Event()
        with patch.dict(sys.modules, {'integrations.wechat.provider': module}), \
                patch.object(self.hub.store, 'replace_moments',
                             side_effect=lambda *args, **kwargs: replacements.append(args)):
            self.hub.set_capabilities({'moments': True})
            worker = threading.Thread(target=self.hub.sync_wechat, args=(cancel,), daemon=True)
            worker.start()
            try:
                self.assertTrue(entered_moments.wait(2), 'sync should reach the blocked Moments catalog')
                self.hub.set_capabilities({'sync': False})
                cancel.set()
                release_moments.set()
                worker.join(3)
                self.assertFalse(worker.is_alive(), 'cancelled sync should terminate')
                self.assertEqual(replacements, [])
            finally:
                release_moments.set()
                cancel.set()
                worker.join(3)

    def test_disabling_moments_during_catalog_read_prevents_catalog_write(self):
        import sys
        import types

        entered = threading.Event()
        release = threading.Event()
        replacements = []

        class Reader:
            wxid = 'fixture-account'
            account = ''

        class BlockingProvider:
            reader = Reader()
            sns_cache_dir = None

            def contacts(self):
                return []

            def sessions(self):
                return []

            def moments(self, **_kwargs):
                entered.set()
                self_outer.assertTrue(release.wait(3))
                return {'available': True, 'truncated': False, 'items': [{'id': 'late-post'}]}

            def status(self):
                return {'state': 'connected'}

        self_outer = self
        module = types.ModuleType('integrations.wechat.provider')
        module.WeChatProvider = BlockingProvider
        with patch.dict(sys.modules, {'integrations.wechat.provider': module}), \
                patch.object(self.hub.store, 'replace_moments',
                             side_effect=lambda *args, **kwargs: replacements.append(args)):
            self.hub.set_capabilities({'moments': True})
            worker = threading.Thread(target=self.hub.sync_wechat, args=(threading.Event(),), daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(2), 'sync should reach the blocked Moments catalog')
                self.hub.set_capabilities({'moments': False})
                release.set()
                worker.join(3)
                self.assertFalse(worker.is_alive(), 'sync should terminate')
                self.assertEqual(replacements, [])
            finally:
                release.set()
                worker.join(3)

    def test_export_html_is_attachment_and_escaped(self):
        with self.request('/api/export?platform=wechat&account_id=account&conversation_id=room&format=html') as response:
            self.assertIn('attachment', response.headers['Content-Disposition'])
            self.assertIn(b'&lt;script&gt;', response.read())

    def test_contact_export_is_gated_provenance_safe_and_formula_safe(self):
        self.hub.store.ingest('qq', 'account', [{'id': 'group-1', 'title': 'Fixture Group'}], [],
                              contacts=[{
                                  'id': 'group-1', 'name': '=formula', 'kind': 'group',
                                  'avatar_url': '/api/avatar?token=ephemeral-secret',
                                  'nickname': '群友', 'uin': '10001',
                                  'raw': {'avatarUrl': 'https://qlogo.invalid/a?token=raw-secret',
                                          'access_token': 'provider-secret'},
                                  'extra': {'member_count': 3},
                              }])
        self.hub.set_capabilities({'export': False})
        with self.assertRaises(HTTPError) as denied:
            self.request('/api/export/contacts?platform=qq&format=csv')
        self.assertEqual(denied.exception.code, 403)
        self.hub.set_capabilities({'export': True})

        with self.request('/api/export/contacts?platform=qq&format=csv') as response:
            self.assertIn('contacts.csv', response.headers['Content-Disposition'])
            csv_payload = response.read().decode('utf-8-sig')
        csv_rows = list(csv.reader(csv_payload.splitlines()))
        self.assertEqual(csv_rows[1][1:4], ['qq', 'account', "'=formula"])
        self.assertNotIn('ephemeral-secret', csv_payload)
        self.assertNotIn('raw-secret', csv_payload)
        self.assertNotIn('provider-secret', csv_payload)

        with self.request('/api/export/contacts?platform=qq&format=json') as response:
            record = json.loads(response.read())[0]
        self.assertEqual((record['platform'], record['account_id'], record['kind']),
                         ('qq', 'account', 'group'))
        self.assertEqual(record['extra'], {'member_count': 3, 'nickname': '群友', 'uin': '10001'})
        self.assertNotIn('avatar_url', record)

    def test_contact_export_preserves_selected_account_and_kind(self):
        for account in ('first', 'second'):
            self.hub.store.ingest('qq', account, [], [], contacts=[
                {'id': 'private', 'name': 'Person', 'kind': 'private'},
                {'id': 'group', 'name': 'Group', 'kind': 'group'},
            ])
        path = '/api/export/contacts?platform=qq&account_id=first&kind=group'
        with self.request(path + '&format=json') as response:
            records = json.load(response)
        self.assertEqual([(row['account_id'], row['id']) for row in records], [('first', 'group')])
        with self.request(path + '&format=csv') as response:
            records = list(csv.reader(io.StringIO(response.read().decode('utf-8-sig'))))
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1][:3], ['group', 'qq', 'first'])

    def test_message_export_cursor_pages_preserve_all_rows_and_filters(self):
        self.hub.store.ingest('wechat', 'account', [], [
            {'id': str(index), 'conversation_id': 'room', 'timestamp': index // 2,
             'type': 'text', 'text': 'cursor export fixture'} for index in range(7)
        ])
        for method, suffix in (
            ('messages', '&account_id=account&conversation_id=room'), ('search', ''),
        ):
            original = getattr(self.hub.store, method)
            def small_page(*args, **kwargs):
                self.assertNotIn('offset', kwargs)
                kwargs['limit'] = 2
                return original(*args, **kwargs)
            with self.subTest(method=method), patch.object(self.hub.store, method, side_effect=small_page) as reads:
                with self.request('/api/export?platform=wechat&q=cursor%20export&format=json' + suffix) as response:
                    records = json.load(response)
                self.assertEqual(reads.call_count, 4)
                self.assertIsNotNone(reads.call_args_list[1].kwargs['cursor'])
                self.assertEqual(len(records), 7)
                self.assertEqual({row['id'] for row in records}, {str(i) for i in range(7)})
                self.assertEqual([row['timestamp'] for row in records], sorted(row['timestamp'] for row in records))

    def test_manual_message_export_streams_projected_rows_from_disk_spool(self):
        from hub.exports import render_export, write_export

        self.hub.store.ingest('wechat', 'account', [{'id': 'room', 'title': 'Room'}], [
            {'id': str(index), 'conversation_id': 'room', 'timestamp': index,
             'type': 'text', 'text': f'message {index}'} for index in range(5)
        ])
        observed = []

        def capture(messages, stream, *args, **kwargs):
            observed.append((messages, kwargs.copy()))
            return write_export(messages, stream, *args, **kwargs)

        with patch('hub.server.render_export', wraps=render_export) as metadata, \
                patch('hub.server.write_export', side_effect=capture):
            with self.request('/api/export?platform=wechat&q=message&format=json') as response:
                records = json.load(response)

        self.assertEqual([row['id'] for row in records], ['0', '1', '2', '3', '4'])
        self.assertEqual(len(observed), 1)
        self.assertFalse(isinstance(observed[0][0], (list, tuple)))
        self.assertEqual(observed[0][1]['count'], 5)
        self.assertEqual(metadata.call_count, 1)
        self.assertEqual(metadata.call_args.args[0], [])

    def test_manual_chatlab_export_still_rejects_mixed_conversation_kinds(self):
        self.hub.store.ingest('wechat', 'account', [
            {'id': 'private', 'title': 'Private', 'kind': 'private'},
            {'id': 'group', 'title': 'Group', 'kind': 'group'},
        ], [
            {'id': 'private-1', 'conversation_id': 'private', 'timestamp': 1,
             'type': 'text', 'text': 'mixed export'},
            {'id': 'group-1', 'conversation_id': 'group', 'timestamp': 2,
             'type': 'text', 'text': 'mixed export'},
        ])

        with self.assertRaises(HTTPError) as error:
            self.request('/api/export?platform=wechat&q=mixed%20export&format=chatlab')

        self.assertEqual(error.exception.code, 400)
        self.assertIn('mixed group and private', error.exception.read().decode())

    def test_traversal_rejected(self):
        with self.assertRaises(HTTPError) as result:
            self.request('/%2e%2e/hub/server.py')
        self.assertEqual(result.exception.code, 404)

    def test_no_filesystem_media_query(self):
        with self.assertRaises(HTTPError) as result:
            self.request('/local-media/arbitrary-path')
        self.assertEqual(result.exception.code, 404)

    def test_xml_message_normalization(self):
        kind, text, filename = message_body('<msg><appmsg><type>6</type><title>a.txt</title></appmsg></msg>', 49)
        self.assertEqual((kind, text, filename), ('file', 'a.txt', 'a.txt'))

    def test_wechat_emoji_is_preserved_as_text_not_misclassified_as_image(self):
        kind, text, filename = message_body('<msg><emoji md5="private-token"/></msg>', 47)
        self.assertEqual((kind, text, filename), ('image', '[表情]', ''))
        self.assertEqual(message_body('', 11000), ('image', '[表情]', ''))

    def test_wechat_location_xml_is_bounded_and_normalized_offline(self):
        xml = 'sender:<msg><location x="22.3" y="114.2" label="Address" poiname="Place"/></msg>'
        self.assertEqual(message_body(xml, 48), ('location', '[位置]', ''))
        self.assertEqual(message_location(xml), {
            'title': 'Place', 'address': 'Address', 'latitude': 22.3, 'longitude': 114.2})
        self.assertEqual(message_location('<msg><location x="999" y="NaN"/></msg>'), None)
        self.assertIsNone(message_location('<!DOCTYPE msg [<!ENTITY x "value">]><msg><location/></msg>'))
        self.assertIsNone(message_location('<msg><location/></msg>'))
        self.assertIsNone(message_location('x' * 65537))

    def test_wechat_quote_message_extracts_bounded_sender_and_content(self):
        xml = ('<msg><appmsg><type>57</type><refermsg><type>1</type>'
               '<svrid>6116895530414915131</svrid>'
               '<displayname>Alice</displayname><content>quoted &amp; safe</content>'
               '</refermsg></appmsg></msg>')
        self.assertEqual(message_quote(xml, 49), {
            'sender': 'Alice', 'content': 'quoted & safe',
            'target_server_id': '6116895530414915131'})
        no_id = xml.replace('<svrid>6116895530414915131</svrid>', '<svrid>0</svrid>')
        self.assertNotIn('target_server_id', message_quote(no_id, 49))
        self.assertEqual(message_quote(
            '<msg><appmsg><type>57</type><refermsg><type>3</type></refermsg></appmsg></msg>', 49),
            {'sender': '', 'content': '[图片]'})
        self.assertIsNone(message_quote('<msg><appmsg><type>5</type></appmsg></msg>', 49))
        self.assertIsNone(message_quote('<!DOCTYPE msg [<!ENTITY x "a">]><msg/>', 49))
        self.assertIsNone(message_quote('x' * 65537, 49))

    def test_wechat_quote_context_requires_one_exact_source_id_in_same_scope(self):
        self.hub.store.ingest('wechat', 'account', [
            {'id': 'room', 'title': 'Room'}, {'id': 'other-room', 'title': 'Other'}], [
            {'id': 'room:target', 'conversation_id': 'room', 'timestamp': 1,
             'text': 'quoted target', '_hub_wechat_server_id': '123456'},
            {'id': 'room:quote', 'conversation_id': 'room', 'timestamp': 2,
             'text': 'reply', 'wechat_quote': {'target_server_id': '123456'}},
            {'id': 'other:target', 'conversation_id': 'other-room', 'timestamp': 1,
             'text': 'other target', '_hub_wechat_server_id': '123456'},
        ])
        params = {'platform': 'wechat', 'account_id': 'account',
                  'conversation_id': 'room', 'source_message_id': '123456',
                  'reference_message_id': 'room:quote',
                  'before': 0, 'after': 0}
        with self.request('/api/context?' + urlencode(params)) as response:
            result = json.load(response)
        self.assertEqual(result['target_message_id'], 'room:target')
        self.assertEqual([row['id'] for row in result['items']], ['room:target'])
        self.assertNotIn('_hub_wechat_server_id', str(result))

        wrong_scope = urlencode({**params, 'conversation_id': 'other-room'})
        with self.assertRaises(HTTPError) as error:
            self.request('/api/context?' + wrong_scope)
        self.assertEqual(error.exception.code, 404)
        wrong_account = urlencode({**params, 'account_id': 'other-account'})
        with self.assertRaises(HTTPError) as error:
            self.request('/api/context?' + wrong_account)
        self.assertEqual(error.exception.code, 404)

        self.hub.store.ingest('wechat', 'account', [{'id': 'room'}], [{
            'id': 'room:duplicate', 'conversation_id': 'room', 'timestamp': 2,
            'text': 'duplicate source id', '_hub_wechat_server_id': '123456'}])
        with self.assertRaises(HTTPError) as error:
            self.request('/api/context?' + urlencode(params))
        self.assertEqual(error.exception.code, 404)

        wrong_platform = urlencode({**params, 'platform': 'qq'})
        with self.assertRaises(HTTPError) as error:
            self.request('/api/context?' + wrong_platform)
        self.assertEqual(error.exception.code, 400)

    def test_wechat_quote_context_uses_canonical_reference_identity(self):
        self.hub.store.ingest('wechat', 'account', [{'id': 'quote-room'}], [
            {'id': 'quote-room:target', 'conversation_id': 'quote-room', 'timestamp': 1,
             'type': 'image', 'text': '[图片]', '_hub_wechat_server_id': '987654'},
            {'id': 'quote-room:reply', 'conversation_id': 'quote-room', 'timestamp': 2,
             'text': 'reply', 'wechat_quote': {'sender': 'Alice', 'content': '[图片]',
                                               'target_server_id': '987654'}},
        ])
        with self.request('/api/quoted-message?' + urlencode({
                'platform': 'wechat', 'account_id': 'account',
                'conversation_id': 'quote-room', 'reference_message_id': 'quote-room:reply',
                'before': 0, 'after': 0})) as response:
            result = json.load(response)
        self.assertEqual(result['target_message_id'], 'quote-room:target')
        self.assertEqual(result['items'][0]['id'], 'quote-room:target')
        self.assertNotIn('target_server_id', json.dumps(result))
        with self.request('/api/messages?' + urlencode({
                'platform': 'wechat', 'account_id': 'account',
                'conversation_id': 'quote-room', 'limit': 10})) as response:
            page = json.load(response)
        quote_row = next(row for row in page['items'] if row['id'] == 'quote-room:reply')
        self.assertEqual(quote_row['extra']['wechat_quote']['target_message_id'], 'quote-room:target')
        self.assertNotIn('target_server_id', json.dumps(page))

        with self.assertRaises(HTTPError) as error:
            self.request('/api/quoted-message?' + urlencode({
                'platform': 'wechat', 'account_id': 'account',
                'conversation_id': 'different-room', 'reference_message_id': 'quote-room:reply'}))
        self.assertEqual(error.exception.code, 404)

        self.hub.store.ingest('wechat', 'account', [{'id': 'quote-room'}], [{
            'id': 'quote-room:duplicate', 'conversation_id': 'quote-room', 'timestamp': 3,
            'text': 'duplicate', '_hub_wechat_server_id': '987654'}])
        with self.assertRaises(HTTPError) as error:
            self.request('/api/quoted-message?' + urlencode({
                'platform': 'wechat', 'account_id': 'account',
                'conversation_id': 'quote-room', 'reference_message_id': 'quote-room:reply'}))
        self.assertEqual(error.exception.code, 404)

    def test_quoted_message_route_is_denied_when_chat_capability_is_disabled(self):
        self.hub.set_capabilities({'chat': False})
        with self.assertRaises(HTTPError) as error:
            self.request('/api/quoted-message?' + urlencode({
                'platform': 'wechat', 'account_id': 'account',
                'conversation_id': 'room', 'reference_message_id': 'room:1'}))
        self.assertEqual(error.exception.code, 403)
        problem = json.load(error.exception)
        self.assertEqual(problem['code'], 'capability_disabled')
        self.assertEqual(problem['capability'], 'chat')

    def test_core_search_remains_available_when_chat_capability_is_disabled(self):
        self.hub.set_capabilities({'chat': False})
        with self.request('/api/search?q=hello') as response:
            result = json.load(response)
        self.assertEqual([item['id'] for item in result['items']], ['room:1'])
        self.assertEqual(Handler.capability_for_path('/api/search'), 'search')
        with self.assertRaises(HTTPError) as error:
            self.request('/api/messages?platform=wechat&account_id=account&conversation_id=room')
        self.assertEqual(error.exception.code, 403)

    def test_wechat_quote_media_preview_uses_only_exact_local_target(self):
        from unittest.mock import Mock
        self.hub.store.ingest('wechat', 'account', [{'id': 'quote-room'}], [
            {'id': 'quote-room:cached-image', 'conversation_id': 'quote-room',
             'timestamp': 1, 'type': 'image', '_hub_wechat_server_id': '5678'},
            {'id': 'quote-room:reply', 'conversation_id': 'quote-room', 'timestamp': 2,
             'text': 'reply', 'wechat_quote': {'sender': '<Alice>',
                 'content': '<quoted>', 'target_server_id': '5678'}},
            {'id': 'quote-room:cross-account', 'conversation_id': 'quote-room', 'timestamp': 3,
             'text': 'reply', 'wechat_quote': {'sender': 'Bob', 'content': '[图片]',
                 'target_server_id': '9999'}},
        ])
        self.hub.store.ingest('wechat', 'other-account', [{'id': 'quote-room'}], [{
            'id': 'other:cached-image', 'conversation_id': 'quote-room', 'timestamp': 1,
            'type': 'image', '_hub_wechat_server_id': '9999',
        }])
        resolver = Mock(return_value={'media_url': '/local-media/abcdefghijklmnopqrstuvwxyzABCDEF'})
        with patch.object(self.hub, 'resolve_media', resolver):
            with self.request('/api/messages?' + urlencode({
                    'platform': 'wechat', 'account_id': 'account',
                    'conversation_id': 'quote-room', 'limit': 10})) as response:
                page = json.load(response)
            quote = next(row['extra']['wechat_quote'] for row in page['items']
                         if row['id'] == 'quote-room:reply')
            self.assertEqual(quote['preview'], {
                'type': 'image', 'media_url': '/local-media/abcdefghijklmnopqrstuvwxyzABCDEF'})
            resolver.assert_called_once_with({
                'platform': 'wechat', 'account_id': 'account',
                'conversation_id': 'quote-room', 'message_id': 'quote-room:cached-image'})
            self.assertNotIn('5678', json.dumps(page))
            self.assertNotIn('other-account', json.dumps(page))
            self.assertNotIn('C:/', json.dumps(page))
            self.assertNotIn('https://', json.dumps(page))
            cross_account_quote = next(row['extra']['wechat_quote'] for row in page['items']
                                       if row['id'] == 'quote-room:cross-account')
            self.assertNotIn('preview', cross_account_quote)

        resolver.reset_mock(return_value=True)
        resolver.return_value = {'media_url': None, 'message': '本机未缓存'}
        with patch.object(self.hub, 'resolve_media', resolver):
            with self.request('/api/messages?' + urlencode({
                    'platform': 'wechat', 'account_id': 'account',
                    'conversation_id': 'quote-room', 'limit': 10})) as response:
                page = json.load(response)
        quote = next(row['extra']['wechat_quote'] for row in page['items']
                     if row['id'] == 'quote-room:reply')
        self.assertNotIn('preview', quote)

    def test_wechat_emoji_md5_is_allowlisted_and_invalid_xml_is_ignored(self):
        value = 'a1' * 16
        self.assertEqual(message_emoji_md5(f'wxid-sender:<msg><emoji md5="{value}"/></msg>'), value)
        self.assertIsNone(message_emoji_md5('<msg><emoji md5="../secret"/></msg>'))
        self.assertIsNone(message_emoji_md5('<msg><emoji'))
        self.assertIsNone(message_emoji_md5('x' * 65537))

    def test_media_metadata_roundtrip(self):
        from unittest.mock import Mock
        image = Path(self.temp.name) / 'image.png'
        image.write_bytes(b'cached-image')
        self.hub.store.ingest('wechat', 'account', [], [
            {'id': 'room:img', 'conversation_id': 'room', 'type': 'image',
             'shard': 'message_0.db', 'local_id': 7}])
        provider = Mock()
        provider.media.return_value = {'available': True, 'path': str(image)}
        self.hub.providers['wechat'] = provider
        result = self.hub.resolve_media({'platform': 'wechat', 'account_id': 'account',
                                         'conversation_id': 'room', 'message_id': 'room:img'})
        self.assertTrue(result['media_url'].startswith('/local-media/'))
        provider.media.assert_called_once_with('room', 7, 'message_0.db', image_tier='mid')

    def test_wechat_video_poster_variant_is_validated_and_resolved_locally(self):
        from unittest.mock import Mock
        poster = Path(self.temp.name) / 'poster.jpg'
        poster.write_bytes(b'\xff\xd8\xffsynthetic')
        self.hub.store.ingest('wechat', 'account', [{'id': 'room', 'title': 'Chat'}], [{
            'id': 'room:video', 'conversation_id': 'room', 'type': 'video',
            'local_id': 7, 'shard': 'message_0.db',
        }])
        provider = Mock()
        provider.media.return_value = {'available': True, 'path': str(poster)}
        self.hub.providers['wechat'] = provider

        resolved = self.hub.resolve_media({
            'platform': 'wechat', 'account_id': 'account', 'conversation_id': 'room',
            'message_id': 'room:video', 'video_variant': 'poster',
        })

        self.assertTrue(resolved['media_url'].startswith('/local-media/'))
        provider.media.assert_called_once_with('room', 7, 'message_0.db', image_tier='mid',
                                               video_variant='poster')
        with self.assertRaisesRegex(ValueError, '视频预览选项无效'):
            self.hub.resolve_media({
                'platform': 'wechat', 'account_id': 'account', 'conversation_id': 'room',
                'message_id': 'room:video', 'video_variant': 'remote',
            })
        with self.assertRaisesRegex(ValueError, '仅适用于微信聊天视频'):
            self.hub.resolve_media({
                'platform': 'wechat', 'account_id': 'account', 'conversation_id': 'room',
                'message_id': 'room:video', 'resource_index': 0, 'video_variant': 'poster',
            })
        provider.media.assert_called_once()

    def test_wechat_emoji_media_is_reported_unsupported_without_provider_access(self):
        from unittest.mock import Mock
        self.hub.store.ingest('wechat', 'account', [{'id': 'room', 'title': 'Chat'}], [{
            'id': 'room:emoji', 'conversation_id': 'room', 'type': 'image',
            'raw_type': '47', 'local_id': 7, 'shard': 'message_0.db',
        }])
        provider = Mock()
        self.hub.providers['wechat'] = provider
        result = self.hub.resolve_media({'platform': 'wechat', 'account_id': 'account',
                                         'conversation_id': 'room', 'message_id': 'room:emoji'})
        self.assertIsNone(result['media_url'])
        self.assertEqual(result['reason'], 'unsupported_sticker_cache')
        self.assertIn('表情', result['message'])
        provider.media.assert_not_called()
        with self.assertRaises(FileNotFoundError):
            self.hub.resolve_resource({'platform': 'wechat', 'account_id': 'account',
                                       'conversation_id': 'room', 'message_id': 'room:emoji'})

    def test_wechat_image_key_unavailable_is_preserved_as_media_status(self):
        from unittest.mock import Mock
        self.hub.store.ingest('wechat', 'account', [{'id': 'room', 'title': 'Chat'}], [{
            'id': 'room:image', 'conversation_id': 'room', 'type': 'image',
            'local_id': 7, 'shard': 'message_0.db',
        }])
        provider = Mock()
        provider.media.return_value = {
            'available': False, 'path': None, 'reason': 'image_key_unavailable',
            'message': '缺少微信图片密钥，无法解密此图片',
        }
        self.hub.providers['wechat'] = provider

        result = self.hub.resolve_media({'platform': 'wechat', 'account_id': 'account',
                                         'conversation_id': 'room', 'message_id': 'room:image'})

        self.assertIsNone(result['media_url'])
        self.assertEqual(result['reason'], 'image_key_unavailable')
        self.assertEqual(result['message'], '缺少微信图片密钥，无法解密此图片')

    def test_wechat_media_without_local_locator_returns_unavailable(self):
        from unittest.mock import Mock
        self.hub.store.ingest('wechat', 'account', [{'id': 'room', 'title': 'Chat'}], [{
            'id': 'room:image-without-locator', 'conversation_id': 'room', 'type': 'image',
        }])
        provider = Mock()
        self.hub.providers['wechat'] = provider
        self.hub.set_capabilities({'media': True})

        query = urlencode({'platform': 'wechat', 'account_id': 'account',
                           'message_id': 'room:image-without-locator', 'conversation_id': 'room'})
        with self.request('/api/media/resolve?' + query) as response:
            result = json.load(response)

        self.assertEqual(result['reason'], 'local_media_identity_unavailable')
        self.assertIn('缺少媒体定位信息', result['message'])
        self.assertIsNone(result['media_url'])
        provider.media.assert_not_called()

    def test_wechat_wxgf_resolution_preserves_original_download_mime(self):
        from unittest.mock import Mock
        original = Path(self.temp.name) / 'original.wxgf'
        original.write_bytes(b'wxgf' + bytes((0, 0, 0, 1)) + b'fixture')
        self.hub.store.ingest('wechat', 'account', [{'id': 'room', 'title': 'Chat'}], [{
            'id': 'room:wxgf', 'conversation_id': 'room', 'type': 'image',
            'local_id': 7, 'shard': 'message_0.db',
        }])
        provider = Mock()
        provider.media.return_value = {'available': True, 'path': str(original)}
        self.hub.providers['wechat'] = provider

        result = self.hub.resolve_media({'platform': 'wechat', 'account_id': 'account',
                                         'conversation_id': 'room', 'message_id': 'room:wxgf'})

        self.assertTrue(result['media_url'].startswith('/local-media/'))
        self.assertEqual(result['mime_type'], 'application/x-wechat-wxgf')
        provider.media.assert_called_once()

    def test_wechat_cached_sticker_preview_and_export_are_account_bound(self):
        from types import SimpleNamespace
        md5 = 'e' * 32
        account_cache = Path(self.temp.name) / 'emoji-account'
        account_cache.mkdir()
        sticker = account_cache / f'{md5}.gif'
        sticker.write_bytes(b'GIF89a')
        self.hub.store.ingest('wechat', 'account', [{'id': 'room', 'title': 'Chat'}], [{
            'id': 'room:sticker', 'conversation_id': 'room', 'type': 'image', 'raw_type': '47',
            'emoji_md5': md5, 'timestamp': 100, 'local_id': 7, 'shard': 'message_0.db',
        }])
        provider = SimpleNamespace(
            reader=SimpleNamespace(wxid='account', account='account'),
            resolve_cached_emoji=lambda requested: sticker if requested == md5 else None,
            emoji_cache_account_dir=lambda: account_cache)
        self.hub.providers['wechat'] = provider

        resolved = self.hub.resolve_media({'platform': 'wechat', 'account_id': 'account',
                                           'conversation_id': 'room', 'message_id': 'room:sticker'})
        self.assertTrue(resolved['media_url'].startswith('/local-media/'))
        payload, mime = self.hub.read_cached_image(
            self.hub.store.get_message('wechat', 'account', 'room:sticker'))
        self.assertEqual((payload, mime), (b'GIF89a', 'image/gif'))
        exported, name = self.hub.resolve_resource({
            'platform': 'wechat', 'account_id': 'account', 'conversation_id': 'room',
            'message_id': 'room:sticker',
        })
        self.assertEqual(exported, sticker)
        self.assertTrue(name.startswith('image-'))

        self.hub.store.ingest('wechat', 'another-account', [{'id': 'room', 'title': 'Chat'}], [{
            'id': 'room:sticker', 'conversation_id': 'room', 'type': 'image', 'raw_type': '47',
            'emoji_md5': md5, 'timestamp': 100, 'local_id': 7, 'shard': 'message_0.db',
        }])
        mismatch = self.hub.resolve_media({'platform': 'wechat', 'account_id': 'another-account',
                                           'conversation_id': 'room', 'message_id': 'room:sticker'})
        self.assertIsNone(mismatch['media_url'])

    def test_wechat_resource_export_prefers_best_cached_image_tier(self):
        from unittest.mock import Mock
        image = Path(self.temp.name) / 'original.jpg'
        image.write_bytes(b'fixture')
        self.hub.store.ingest('wechat', 'account', [{'id': 'room', 'title': 'Chat'}], [{
            'id': 'room:image', 'conversation_id': 'room', 'type': 'image', 'timestamp': 100,
            'local_id': 7, 'shard': 'message_0.db',
        }])
        provider = Mock()
        provider.media.return_value = {'available': True, 'path': str(image), 'name': 'original.jpg'}
        self.hub.providers['wechat'] = provider

        resolved, name = self.hub.resolve_resource({
            'platform': 'wechat', 'account_id': 'account', 'conversation_id': 'room',
            'message_id': 'room:image',
        })

        self.assertEqual(resolved, image)
        self.assertEqual(name, 'image-room:image')
        provider.media.assert_called_once_with('room', 7, 'message_0.db', image_tier='best')

    def test_wechat_resource_export_honors_only_supported_image_tiers(self):
        from unittest.mock import Mock
        image = Path(self.temp.name) / 'tiered.jpg'
        image.write_bytes(b'fixture')
        self.hub.store.ingest('wechat', 'account', [{'id': 'room', 'title': 'Chat'}], [{
            'id': 'room:image', 'conversation_id': 'room', 'type': 'image', 'timestamp': 100,
            'local_id': 7, 'shard': 'message_0.db',
        }])
        provider = Mock()
        provider.media.return_value = {'available': True, 'path': str(image), 'name': 'tiered.jpg'}
        self.hub.providers['wechat'] = provider

        for tier in ('mid', 'thumb', 'original', 'best'):
            with self.subTest(tier=tier):
                self.hub.resolve_resource({
                    'platform': 'wechat', 'account_id': 'account', 'conversation_id': 'room',
                    'message_id': 'room:image', 'image_tier': tier,
                })
                provider.media.assert_called_with('room', 7, 'message_0.db', image_tier=tier)

        with self.assertRaisesRegex(ValueError, '图片质量选项无效'):
            self.hub.resolve_resource({
                'platform': 'wechat', 'account_id': 'account', 'conversation_id': 'room',
                'message_id': 'room:image', 'image_tier': 'remote',
            })
        with self.assertRaisesRegex(ValueError, '图片质量选项仅适用于微信聊天图片'):
            self.hub.resolve_resource({
                'platform': 'wechat', 'account_id': 'account', 'conversation_id': 'room',
                'message_id': 'room:image', 'resource_index': 0, 'image_tier': 'mid',
            })
        provider.media.reset_mock()
        self.hub.store.ingest('wechat', 'account', [], [{
            'id': 'room:file', 'conversation_id': 'room', 'type': 'file', 'timestamp': 100,
            'local_id': 8, 'shard': 'message_0.db',
        }])
        with self.assertRaisesRegex(ValueError, '图片质量选项仅适用于微信聊天图片'):
            self.hub.resolve_resource({
                'platform': 'wechat', 'account_id': 'account', 'conversation_id': 'room',
                'message_id': 'room:file', 'image_tier': 'thumb',
            })
        provider.media.assert_not_called()

    def test_wechat_image_tiers_flow_through_real_resolver_into_zip(self):
        from integrations.wechat.provider import WeChatProvider
        from integrations.wechat.vendor.media import MediaDownloader

        root = Path(self.temp.name)
        account_dir = root / 'synthetic-wechat-account'
        image_dir = (account_dir / 'msg' / 'attach' /
                     MediaDownloader(SimpleNamespace())._chat_md5('room') / '2026-10' / 'Img')
        image_dir.mkdir(parents=True)
        cache = {
            'original': b'\xff\xd8\xff' + b'o' * 1100 + b'\xff\xd9',
            'mid': b'\xff\xd8\xffsynthetic-mid-image-data\xff\xd9',
            'thumb': b'\xff\xd8\xffsynthetic-thumb-image-data\xff\xd9',
        }
        digests = {'original': 'a' * 32, 'mid': 'b' * 32, 'thumb': 'c' * 32,
                   'missing': 'd' * 32}
        source_paths = {}
        source_payloads = {
            'original': cache['original'], 'original-mid': cache['mid'],
            'mid': cache['mid'], 'mid-thumb': cache['thumb'], 'thumb': cache['thumb'],
            'missing-mid': cache['mid'],
        }
        for key, payload in source_payloads.items():
            md5 = digests[key.split('-')[0]]
            suffix = '_h.dat' if key == 'original' else '_t.dat' if key in ('thumb', 'mid-thumb') else '.dat'
            path = image_dir / f'{md5}{suffix}'
            path.write_bytes(bytes(byte ^ 0x88 for byte in payload))
            source_paths[key] = path

        records = {
            1: {'local_type': 3, 'packed_info': digests['original'].encode()},
            2: {'local_type': 3, 'packed_info': digests['mid'].encode()},
            3: {'local_type': 3, 'packed_info': digests['thumb'].encode()},
            4: {'local_type': 3, 'packed_info': digests['missing'].encode()},
        }
        reader = SimpleNamespace(
            wxid='account', account='account', account_dir=str(account_dir),
            get_message_row=lambda _conversation, local_id, **_kwargs: records.get(local_id),
        )
        provider = WeChatProvider.__new__(WeChatProvider)
        provider.reader = reader
        self.hub.providers['wechat'] = provider
        self.hub.store.ingest('wechat', 'account', [{'id': 'room', 'title': 'Synthetic'}], [
            {'id': f'room:{name}', 'conversation_id': 'room', 'type': 'image',
             'timestamp': 100 + index, 'local_id': index, 'shard': 'message_0.db'}
            for index, name in enumerate(('original', 'mid', 'thumb', 'missing'), start=1)
        ])
        source_hashes = {key: hashlib.sha256(path.read_bytes()).hexdigest()
                         for key, path in source_paths.items()}
        requests = [
            {'platform': 'wechat', 'account_id': 'account', 'conversation_id': 'room',
             'message_id': 'room:original'},
            {'platform': 'wechat', 'account_id': 'account', 'conversation_id': 'room',
             'message_id': 'room:mid'},
            {'platform': 'wechat', 'account_id': 'account', 'conversation_id': 'room',
             'message_id': 'room:thumb'},
            {'platform': 'wechat', 'account_id': 'account', 'conversation_id': 'room',
             'message_id': 'room:missing', 'image_tier': 'original'},
        ]

        with patch('integrations.wechat.provider.RUNTIME', root / 'runtime'):
            started = self.hub.resource_jobs.start(requests)
            status = self.hub.resource_jobs.status(started['id'])
            deadline = time.monotonic() + 5
            while status['state'] in ('queued', 'running') and time.monotonic() < deadline:
                time.sleep(0.01)
                status = self.hub.resource_jobs.status(started['id'])

        self.assertEqual((status['state'], status['exported'], status['failed']),
                         ('completed', 3, 1),
                         self.hub.resource_jobs._jobs[started['id']]['manifest'])
        archive_path = self.hub.resource_jobs.download_path(started['id'])
        with ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            entries = {item['message_id']: item for item in manifest['items']}
            expected = {
                'room:original': cache['original'],
                'room:mid': cache['mid'],
                'room:thumb': cache['thumb'],
            }
            for message_id, payload in expected.items():
                item = entries[message_id]
                self.assertEqual(item['status'], 'exported')
                self.assertEqual(archive.read('attachments/' + item['file']), payload)
                self.assertEqual(item['sha256'], hashlib.sha256(payload).hexdigest())
            self.assertEqual(entries['room:missing']['status'], 'failed')
            self.assertEqual(entries['room:missing']['error'], 'FileNotFoundError')
        self.assertEqual(source_hashes, {
            key: hashlib.sha256(path.read_bytes()).hexdigest()
            for key, path in source_paths.items()
        })

    def test_wecom_resource_index_selects_and_names_the_requested_attachment(self):
        from unittest.mock import Mock
        image = Path(self.temp.name) / 'selected.png'
        image.write_bytes(b'fixture')
        self.hub.store.ingest('wecom', 'account', [{'id': 'room', 'title': 'Work'}], [{
            'id': 'room:1', 'conversation_id': 'room', 'type': 'image', 'timestamp': 100,
            'attachments': [{'name': 'first.png', 'type': 'image'},
                            {'name': 'selected.png', 'type': 'image'}],
        }])
        provider = Mock()
        provider.media.return_value = {'available': True, 'path': str(image), 'name': 'selected.png'}
        self.hub.providers['wecom'] = provider

        resolved, name = self.hub.resolve_resource({
            'platform': 'wecom', 'account_id': 'account', 'conversation_id': 'room',
            'message_id': 'room:1', 'resource_index': 1,
        })

        self.assertEqual((resolved, name), (image, 'selected.png'))
        provider.media.assert_called_once_with('room:1', resource_index=1)

    def test_wecom_cached_media_flows_through_adapter_into_zip_without_source_changes(self):
        import importlib
        import hashlib
        import sys
        import time
        import types
        from zipfile import ZipFile

        engine_module = types.ModuleType('integrations.wecom.engine')
        engine_module.OUT = ''
        engine_module.SyncEngine = object
        engine_module.compute_stats = lambda *_args: {}
        engine_module.strip_internal = lambda row: row
        with patch.dict(sys.modules, {engine_module.__name__: engine_module}):
            adapter_module = importlib.import_module('integrations.wecom.adapter')

        cache_root = Path(self.temp.name) / 'wecom-cache'
        first = cache_root / 'Cache' / 'Image' / 'first.png'
        selected = cache_root / 'Cache' / 'Image' / 'selected.png'
        first.parent.mkdir(parents=True)
        first.write_bytes(b'\x89PNG\r\n\x1a\nfirst synthetic image')
        selected.write_bytes(b'\x89PNG\r\n\x1a\nselected synthetic image')
        source_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in (first, selected)}

        provider = object.__new__(adapter_module.WeComAdapter)
        provider._loaded = True
        provider._messages_released = False
        provider.engine = SimpleNamespace(cache_root=str(cache_root))
        provider._messages = lambda: [{
            'message_id': 'room:cached-media', 'conversation_id': 'room',
            'media': [
                {'_local_cache_path': 'Cache/Image/first.png', 'n': 'first.png'},
                {'_local_cache_path': 'Cache/Image/selected.png', 'n': 'selected.png'},
            ],
        }]
        self.hub.store.ingest('wecom', 'synthetic-account',
                              [{'id': 'room', 'title': 'Synthetic room'}], [{
            'id': 'room:cached-media', 'conversation_id': 'room', 'type': 'image',
            'timestamp': 100,
            'attachments': [{'name': 'first.png', 'type': 'image'},
                            {'name': 'selected.png', 'type': 'image'}],
        }])
        self.hub.providers['wecom'] = provider

        with patch.object(adapter_module, 'OUT', str(cache_root)):
            job = self.hub.resource_jobs.start([{
                'platform': 'wecom', 'account_id': 'synthetic-account',
                'conversation_id': 'room', 'message_id': 'room:cached-media',
                'resource_index': 1,
            }])
            status = self.hub.resource_jobs.status(job['id'])
            deadline = time.monotonic() + 5
            while status['state'] in ('queued', 'running') and time.monotonic() < deadline:
                time.sleep(0.01)
                status = self.hub.resource_jobs.status(job['id'])

        self.assertEqual((status['state'], status['exported'], status['failed']),
                         ('completed', 1, 0))
        archive_path = self.hub.resource_jobs.download_path(job['id'])
        with ZipFile(archive_path) as archive:
            manifest_text = archive.read('manifest.json').decode('utf-8')
            manifest = json.loads(manifest_text)
            self.assertEqual(len(manifest['items']), 1)
            item = manifest['items'][0]
            self.assertEqual(item['resource_index'], 1)
            self.assertEqual(item['status'], 'exported')
            self.assertEqual(archive.read('attachments/' + item['file']), selected.read_bytes())
            self.assertEqual(item['sha256'], hashlib.sha256(selected.read_bytes()).hexdigest())
        self.assertNotIn(str(cache_root), manifest_text)
        self.assertNotIn('Cache/Image', manifest_text)
        self.assertEqual(source_hashes, {
            path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (first, selected)
        })

    def test_wecom_resource_index_is_bounded_before_provider_lookup(self):
        from unittest.mock import Mock
        self.hub.store.ingest('wecom', 'account', [{'id': 'room', 'title': 'Work'}], [{
            'id': 'room:1', 'conversation_id': 'room', 'type': 'image', 'timestamp': 100,
        }])
        provider = Mock()
        self.hub.providers['wecom'] = provider

        with self.assertRaisesRegex(ValueError, '附件序号无效'):
            self.hub.resolve_media({
                'platform': 'wecom', 'account_id': 'account', 'conversation_id': 'room',
                'message_id': 'room:1', 'resource_index': 50,
            })
        provider.media.assert_not_called()

    def test_boolean_attachment_index_is_rejected_before_platform_resolution(self):
        from unittest.mock import Mock
        self.hub.store.ingest('wecom', 'account', [{'id': 'work', 'title': 'Work'}], [{
            'id': 'work:1', 'conversation_id': 'work', 'type': 'image', 'timestamp': 100,
            'attachments': [{'name': 'first.png'}, {'name': 'second.png'}],
        }])
        self.hub.store.ingest('qq', 'account', [{'id': 'room', 'title': 'QQ'}], [{
            'id': 'room:1', 'conversation_id': 'room', 'type': 'image', 'timestamp': 100,
            'roaming_resources': [{'name': 'first.png'}, {'name': 'second.png'}],
        }])
        wecom = Mock()
        qq = Mock()
        self.hub.providers.update({'wecom': wecom, 'qq': qq})
        cases = (
            {'platform': 'wecom', 'account_id': 'account', 'conversation_id': 'work',
             'message_id': 'work:1', 'resource_index': True},
            {'platform': 'qq', 'account_id': 'account', 'conversation_id': 'room',
             'message_id': 'room:1', 'resource_index': True},
        )

        for params in cases:
            with self.subTest(platform=params['platform']), self.assertRaisesRegex(
                    ValueError, '附件序号无效'):
                self.hub.resolve_media(params)

        wecom.media.assert_not_called()
        qq.resolve_cached_resource.assert_not_called()

    def test_qq_local_resource_index_100_reaches_provider_and_101_is_rejected(self):
        from unittest.mock import Mock
        media = [{'kind': 'image', 'file_name': f'{index}.png'} for index in range(101)]
        self.hub.store.ingest('qq', 'account', [{'id': 'room', 'title': 'QQ'}], [{
            'id': 'room:1', 'conversation_id': 'room', 'type': 'image',
            'timestamp': 100, 'local_database': True, 'media': media,
        }])
        provider = Mock()
        provider.media.return_value = {'available': False, 'path': None}
        self.hub.providers['qq_local'] = provider
        params = {'platform': 'qq', 'account_id': 'account',
                  'conversation_id': 'room', 'message_id': 'room:1'}

        self.hub.resolve_media({**params, 'resource_index': 100})

        self.assertEqual(provider.media.call_args.kwargs['resource_index'], 100)
        with self.assertRaisesRegex(ValueError, '附件序号无效'):
            self.hub.resolve_media({**params, 'resource_index': 101})

    def test_wechat_group_member_api_reads_bounded_roster_from_connected_reader(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        group_id = 'fixture@chatroom'
        self.hub.store.ingest('wechat', 'account', [{'id': group_id, 'title': 'Fixture group', 'kind': 'group'}], [])
        self.hub.store.ingest('wechat', 'account', [], [], [
            {'id': 'wxid-member', 'name': 'Teammate', 'avatar_url': 'hub-avatar://wechat/' + ('a' * 64)},
        ])
        self.hub.store.ingest('wechat', 'other-account', [], [], [
            {'id': 'wxid-member', 'name': 'Other account', 'avatar_url': 'hub-avatar://wechat/' + ('b' * 64)},
        ])
        reader = Mock()
        reader.account = 'account'
        reader.get_group_members.return_value = [
            {'username': 'wxid-owner', 'nick_name': 'Owner', 'remark': '', 'is_owner': True},
            {'username': 'wxid-member', 'nick_name': 'Member', 'remark': 'Teammate', 'is_owner': False},
        ]
        self.hub.providers['wechat'] = SimpleNamespace(reader=reader)

        with self.request('/api/conversation-members?platform=wechat&account_id=account'
                          '&conversation_id=fixture%40chatroom&q=team&limit=1') as response:
            result = json.load(response)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'], [{
            'id': 'wxid-member', 'name': 'Teammate', 'nickname': 'Member',
            'remark': 'Teammate', 'is_owner': False, 'role': 'member',
            'avatar_url': 'hub-avatar://wechat/' + ('a' * 64),
        }])
        reader.get_group_members.assert_called_once_with(group_id)

    def test_wecom_group_member_api_uses_exact_indexed_local_account_and_pages(self):
        from unittest.mock import Mock

        self.hub.store.ingest('wecom', 'local', [{'id': 'room', 'title': 'Fixture group', 'kind': 'group'}], [])
        provider = Mock()
        provider.members.return_value = {
            'items': [
                {'id': 'a', 'name': 'Alpha', 'role': 'owner'},
                {'id': 'b', 'name': 'Beta', 'role': 'member'},
            ],
            'total': 3,
        }
        self.hub.providers['wecom'] = provider

        with self.request('/api/conversation-members?platform=wecom&account_id=local'
                          '&conversation_id=room&offset=1&limit=1') as response:
            result = json.load(response)

        self.assertEqual(result['items'], [{'id': 'b', 'name': 'Beta', 'role': 'member'}])
        self.assertEqual((result['total'], result['offset'], result['limit']), (3, 1, 1))
        provider.members.assert_called_once_with('room', account_id='local', limit=2, query='')

    def test_qq_group_member_api_is_account_and_index_scoped(self):
        from unittest.mock import Mock

        self.hub.store.ingest('qq', '12345', [{'id': '98765', 'title': 'Fixture group', 'kind': 'group'}], [])
        provider = Mock()
        provider.members.return_value = {
            'items': [{'id': 'member', 'name': 'QQ Member', 'role': 'admin'}], 'total': 1,
        }
        self.hub.providers['qq'] = provider

        with self.request('/api/conversation-members?platform=qq&account_id=12345'
                          '&conversation_id=98765&q=qq') as response:
            result = json.load(response)

        self.assertEqual(result['items'][0]['role'], 'admin')
        provider.members.assert_called_once_with('98765', account_id='12345', limit=100, query='qq')
        with self.assertRaises(HTTPError) as error:
            self.request('/api/conversation-members?platform=qq&account_id=99999'
                         '&conversation_id=98765')
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(provider.members.call_count, 1)

    def test_wechat_group_member_api_rejects_unindexed_or_wrong_account(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        group_id = 'fixture@chatroom'
        self.hub.store.ingest('wechat', 'account', [{'id': group_id, 'title': 'Fixture group', 'kind': 'group'}], [])
        reader = Mock()
        reader.account = 'other-account'
        self.hub.providers['wechat'] = SimpleNamespace(reader=reader)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/conversation-members?platform=wechat&account_id=account'
                         '&conversation_id=fixture%40chatroom')
        self.assertEqual(error.exception.code, 400)
        reader.get_group_members.assert_not_called()

        with self.assertRaises(HTTPError) as error:
            self.request('/api/conversation-members?platform=wechat&account_id=account'
                         '&conversation_id=unknown%40chatroom')
        self.assertEqual(error.exception.code, 400)

    def test_group_roster_export_is_scoped_safe_and_capability_gated(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        group_id = 'fixture@chatroom'
        self.hub.store.ingest('wechat', 'account', [{'id': group_id, 'title': '本地群聊', 'kind': 'group'}], [])
        reader = Mock()
        reader.account = 'account'
        reader.get_group_members.return_value = [
            {'username': 'wxid-owner', 'nick_name': 'Owner', 'remark': '=formula', 'is_owner': True,
             'avatar_url': '/api/avatar?token=temporary-secret', 'extra': {'secret': True}},
        ]
        self.hub.providers['wechat'] = SimpleNamespace(reader=reader)
        path = '/api/export/group-members?platform=wechat&account_id=account&conversation_id=fixture%40chatroom'

        self.hub.set_capabilities({'export': False})
        with self.assertRaises(HTTPError) as error:
            self.request(path + '&format=json')
        self.assertEqual(error.exception.code, 403)
        reader.get_group_members.assert_not_called()

        self.hub.set_capabilities({'export': True})
        with self.request(path + '&format=json') as response:
            payload = response.read()
            self.assertIn('group-members.json', response.headers['Content-Disposition'])
        record = json.loads(payload)[0]
        self.assertEqual((record['platform'], record['account_id'], record['conversation_id']),
                         ('wechat', 'account', group_id))
        self.assertNotIn('avatar_url', record)
        self.assertNotIn(b'temporary-secret', payload)

        with self.request(path + '&format=csv') as response:
            rows = list(csv.reader(io.StringIO(response.read().decode('utf-8-sig'))))
        self.assertEqual(rows[1][7], "'=formula")

    def test_group_roster_export_rejects_reader_results_over_1000(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        group_id = 'fixture@chatroom'
        self.hub.store.ingest('wechat', 'account', [{'id': group_id, 'title': 'Fixture group', 'kind': 'group'}], [])
        reader = Mock()
        reader.account = 'account'
        reader.get_group_members.return_value = [
            {'username': f'wxid-{index}', 'nick_name': 'Member'} for index in range(1001)
        ]
        self.hub.providers['wechat'] = SimpleNamespace(reader=reader)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/export/group-members?platform=wechat&account_id=account'
                         '&conversation_id=fixture%40chatroom&format=json')
        self.assertEqual(error.exception.code, 413)

    def test_message_identity_lookup(self):
        self.assertEqual(self.hub.store.get_message('wechat', 'account', 'room:1')['text'], 'hello <script>')
        self.assertIsNone(self.hub.store.get_message('wecom', 'account', 'room:1'))

    def test_untrusted_attachment_forced_download(self):
        payload = Path(self.temp.name) / 'chat.svg'
        payload.write_text('<svg><script>evil()</script></svg>')
        self.hub.media_paths['test-capability'] = payload
        with self.request('/local-media/test-capability') as response:
            self.assertIn('attachment', response.headers['Content-Disposition'])

    def test_wechat_bmp_is_inline_only_with_bmp_signature(self):
        cache = Path(self.temp.name) / 'wechat' / 'media'
        cache.mkdir(parents=True)
        valid_bmp = cache / 'image.bmp'
        valid_content = b'BM' + b'fixture-bitmap-data'
        valid_bmp.write_bytes(valid_content)
        invalid_bmp = cache / 'not-really-bmp.bmp'
        invalid_content = b'NO' + b'fixture-data'
        invalid_bmp.write_bytes(invalid_content)
        self.hub.media_paths['wechat-bmp-valid'] = valid_bmp
        self.hub.media_paths['wechat-bmp-invalid'] = invalid_bmp

        with self.request('/local-media/wechat-bmp-valid') as response:
            self.assertEqual(response.headers.get_content_type(), 'image/bmp')
            self.assertEqual(response.headers.get('Content-Disposition'), None)
            self.assertEqual(response.read(), valid_content)

        with self.request('/local-media/wechat-bmp-invalid') as response:
            self.assertEqual(response.headers.get_content_type(), 'image/bmp')
            self.assertIn('attachment', response.headers['Content-Disposition'])
            self.assertEqual(response.read(), invalid_content)

    def test_avatar_reads_only_previously_cached_allowlisted_images(self):
        payload = b'\x89PNG\r\n\x1a\nfixture'
        urls = (
            'https://q.qlogo.cn/g?b=qq&nk=10001&s=100',
            'https://q1.qlogo.cn/g?b=qq&nk=10001&s=640',
            'https://p.qlogo.cn/gh/10001/10001/640/',
        )
        cache = self.hub.storage / 'avatars'
        cache.mkdir()
        for url in urls:
            digest = hashlib.sha256(url.encode('utf-8')).hexdigest()
            (cache / (digest + '.png')).write_bytes(payload)
        with patch('urllib.request.urlopen', side_effect=AssertionError('avatar lookup attempted network access')):
            for url in urls:
                with self.subTest(url=url):
                    self.assertEqual(self.hub.fetch_avatar(url), (payload, 'image/png'))
            with self.assertRaisesRegex(ValueError, '不在本机缓存'):
                self.hub.fetch_avatar('https://q.qlogo.cn/g?b=qq&nk=10002&s=100')
        with self.assertRaises(ValueError):
            self.hub.fetch_avatar('https://attacker.example/avatar.png')
        with self.assertRaises(ValueError):
            self.hub.fetch_avatar('https://p.qlogo.cn.attacker.example/avatar')

    def test_avatar_image_endpoint_serves_only_local_cache(self):
        payload = b'\xff\xd8\xfffixture'
        url = 'https://wework.qpic.cn/avatar/synthetic'
        cache = self.hub.storage / 'avatars'
        cache.mkdir()
        digest = hashlib.sha256(url.encode('utf-8')).hexdigest()
        (cache / (digest + '.jpg')).write_bytes(payload)
        with patch('urllib.request.urlopen', side_effect=AssertionError('avatar endpoint attempted network access')):
            with self.request('/api/avatar?url=https%3A%2F%2Fwework.qpic.cn%2Favatar%2Fsynthetic') as result:
                self.assertEqual(result.headers.get_content_type(), 'image/jpeg')
                self.assertEqual(result.read(), payload)
            with self.assertRaises(HTTPError) as error:
                self.request('/api/avatar?url=https%3A%2F%2Fwework.qpic.cn%2Favatar%2Fmissing')
            self.assertEqual(error.exception.code, 400)

    def test_local_wechat_avatar_is_atomically_cached_and_served_offline(self):
        url = 'hub-avatar://wechat/' + ('a' * 64)
        payload = b'\xff\xd8\xffsynthetic-jpeg'
        self.assertTrue(self.hub._cache_local_avatar(url, payload))
        self.assertEqual(self.hub.fetch_avatar(url), (payload, 'image/jpeg'))
        for invalid in ('hub-avatar://unsupported/' + ('a' * 64),
                        'hub-avatar://wechat/../' + ('a' * 64),
                        'hub-avatar://wechat/' + ('A' * 64)):
            with self.subTest(invalid=invalid):
                self.assertFalse(self.hub._cache_local_avatar(invalid, payload))
                with self.assertRaises(ValueError):
                    self.hub.fetch_avatar(invalid)

    def test_local_qq_avatar_png_is_cached_without_network(self):
        url = 'hub-avatar://qq/' + ('a' * 64)
        payload = b'\x89PNG\r\n\x1a\nsynthetic-png'
        self.assertTrue(self.hub._cache_local_avatar(url, payload))
        self.assertEqual(self.hub.fetch_avatar(url), (payload, 'image/png'))
        self.assertFalse(self.hub._cache_local_avatar(url, b'not an image'))

    def test_media_range(self):
        payload = Path(self.temp.name) / 'voice.wav'
        payload.write_bytes(b'RIFFtestWAVE')
        self.hub.media_paths['test-voice'] = payload
        with self.request('/local-media/test-voice', {'Range': 'bytes=0-3'}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), b'RIFF')
            self.assertEqual(response.headers['Content-Range'], 'bytes 0-3/12')
        with self.request('/local-media/test-voice', {'Range': 'bytes=-4'}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), b'WAVE')
            self.assertEqual(response.headers['Content-Range'], 'bytes 8-11/12')
        for range_header in ('bytes=12-', 'bytes=0-1,4-5'):
            with self.subTest(range_header=range_header):
                with self.assertRaises(HTTPError) as error:
                    self.request('/local-media/test-voice', {'Range': range_header})
                self.assertEqual(error.exception.code, 416)
                self.assertEqual(error.exception.headers['Content-Range'], 'bytes */12')

    def test_local_wma_is_inline_with_audio_mime(self):
        payload = Path(self.temp.name) / 'voice.wma'
        content = bytes.fromhex('3026b2758e66cf11a6d900aa0062ce6c') + b'fixture'
        payload.write_bytes(content)
        self.hub.media_paths['test-wma'] = payload

        with self.request('/local-media/test-wma') as response:
            self.assertEqual(response.headers.get_content_type(), 'audio/x-ms-wma')
            self.assertNotIn('attachment', response.headers.get('Content-Disposition', ''))
            self.assertEqual(response.read(), content)

    def test_moments_cached_media_is_same_origin_and_never_returns_source_credentials(self):
        self.hub.set_capabilities({'moments': True})
        cache = Path(self.temp.name) / 'sns_cache'
        cache.mkdir()
        image = cache / 'fixture.jpg'
        image.write_bytes(b'\xff\xd8\xffcached-moments-image')
        video = cache / 'fixture-live.mp4'
        video.write_bytes(b'\x00\x00\x00\x18ftypisomcached-live-video')

        self.seed_moments('account', [{
                    'id': 'post', 'media': [{
                        '_local_path': str(image), 'mime_type': 'image/jpeg', 'type': 'image',
                        'url': 'https://media.invalid/photo?token=secret', 'token': 'secret',
                        'key': 'private-key', 'local_available': True,
                        'livePhoto': {'_local_path': str(video), 'mime_type': 'video/mp4',
                                      'type': 'video', 'url': 'https://media.invalid/live?token=live-secret',
                                      'token': 'live-secret', 'key': 'live-key', 'local_available': True},
                    }],
                }])
        with self.request('/api/moments') as response:
            payload = response.read()
            result = json.loads(payload)
        item = result['items'][0]['media'][0]
        self.assertTrue(item['local_available'])
        self.assertRegex(item['media_url'], r'^/local-moment-media/[A-Za-z0-9_-]{32}$')
        live_photo = item['livePhoto']
        self.assertTrue(live_photo['local_available'])
        self.assertEqual(live_photo['type'], 'video')
        self.assertRegex(live_photo['media_url'], r'^/local-moment-media/[A-Za-z0-9_-]{32}$')
        for private in (b'media.invalid', b'token', b'secret', b'private-key', b'live-key', b'_local_path'):
            self.assertNotIn(private, payload)
        with self.request(item['media_url'], {'Range': 'bytes=0-2'}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers.get_content_type(), 'image/jpeg')
            self.assertEqual(response.read(), b'\xff\xd8\xff')
        with self.request(live_photo['media_url'], {'Range': 'bytes=4-7'}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers.get_content_type(), 'video/mp4')
            self.assertEqual(response.read(), b'ftyp')
        self.hub.set_capabilities({'moments': False})
        with self.assertRaises(HTTPError) as error:
            self.request(live_photo['media_url'])
        self.assertEqual(error.exception.code, 403)

    def test_moment_image_reader_accepts_only_bounded_cached_images(self):
        self.hub.set_capabilities({'moments': True})
        cache = Path(self.temp.name) / 'sns_cache'
        cache.mkdir()
        image = cache / 'fixture.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\nsmall-image')

        self.seed_moments('account', [{'id': 'post', 'media': [{
                    '_local_path': str(image), 'mime_type': 'image/png', 'type': 'image',
                }]}])
        result = self.hub.search_moments()
        media_url = result['items'][0]['media'][0]['media_url']
        payload, mime = self.hub.read_moment_image(media_url)
        self.assertEqual((payload, mime), (image.read_bytes(), 'image/png'))
        with self.assertRaisesRegex(ValueError, 'invalid|无效'):
            self.hub.read_moment_image('https://example.invalid/image.png')
        token = media_url.rsplit('/', 1)[-1]
        with self.hub.lock:
            path, root, content_type, _expiry = self.hub.moment_media_paths[token]
            self.hub.moment_media_paths[token] = (path, root, content_type, time.monotonic() - 1)
        with self.assertRaisesRegex(ValueError, '过期'):
            self.hub.read_moment_image(media_url)
        result = self.hub.search_moments()
        media_url = result['items'][0]['media'][0]['media_url']
        image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'x' * (8 * 1024 * 1024))
        with self.assertRaisesRegex(ValueError, '大小限制'):
            self.hub.read_moment_image(media_url)

    def test_moment_media_reader_accepts_weflow_bmp_cache(self):
        self.hub.set_capabilities({'moments': True})
        cache = Path(self.temp.name) / 'sns_cache'
        cache.mkdir()
        image = cache / 'fixture.bmp'
        payload = b'BM' + b'\x00' * 12 + b'synthetic-bmp'
        image.write_bytes(payload)
        self.seed_moments('account', [{'id': 'bmp-post', 'media': [{
            '_local_path': str(image), 'mime_type': 'image/bmp', 'type': 'image',
        }]}])

        result = self.hub.search_moments()
        media_url = result['items'][0]['media'][0]['media_url']

        self.assertEqual(self.hub.read_moment_media(media_url), (payload, 'image/bmp'))
        self.assertEqual(self.hub.read_moment_image(media_url), (payload, 'image/bmp'))

    def test_moment_mcp_media_reader_accepts_bounded_signature_checked_video(self):
        self.hub.set_capabilities({'moments': True})
        cache = Path(self.temp.name) / 'sns_cache'
        cache.mkdir()
        video = cache / 'fixture.mp4'
        video.write_bytes(b'\x00\x00\x00\x18ftypisomcached-video')
        webm = cache / 'fixture.webm'
        webm.write_bytes(b'\x1a\x45\xdf\xa3cached-webm-video')
        self.seed_moments('account', [
            {'id': 'post', 'media': [{
                '_local_path': str(video), 'mime_type': 'video/mp4', 'type': 'video',
            }]},
            {'id': 'webm-post', 'media': [{
                '_local_path': str(webm), 'mime_type': 'video/webm', 'type': 'video',
            }]},
        ])
        result = self.hub.search_moments()
        items = {item['id']: item for item in result['items']}
        media_url = items['post']['media'][0]['media_url']
        self.assertEqual(self.hub.read_moment_media(media_url), (video.read_bytes(), 'video/mp4'))
        with self.assertRaisesRegex(ValueError, '不是图片'):
            self.hub.read_moment_image(media_url)
        webm_url = items['webm-post']['media'][0]['media_url']
        self.assertEqual(self.hub.read_moment_media(webm_url), (webm.read_bytes(), 'video/webm'))

        video.write_bytes(b'not an mp4')
        with self.assertRaisesRegex(ValueError, '签名有效'):
            self.hub.read_moment_media(media_url)

    def test_moment_media_reader_accepts_avif_heic_and_rejects_lookalike_ftyp(self):
        self.hub.set_capabilities({'moments': True})
        cache = Path(self.temp.name) / 'sns_cache'
        cache.mkdir()
        avif = cache / 'fixture.avif'
        avif.write_bytes(b'\x00\x00\x00\x14ftypavif\x00\x00\x00\x00avis')
        heic = cache / 'fixture.heic'
        heic.write_bytes(b'\x00\x00\x00\x14ftypheic\x00\x00\x00\x00mif1')
        self.seed_moments('account', [
            {'id': 'avif', 'media': [{'_local_path': str(avif), 'type': 'image'}]},
            {'id': 'heic', 'media': [{'_local_path': str(heic), 'type': 'image'}]},
        ])
        items = {post['id']: post for post in self.hub.search_moments()['items']}
        avif_url = items['avif']['media'][0]['media_url']
        heic_url = items['heic']['media'][0]['media_url']
        self.assertEqual(self.hub.read_moment_media(avif_url), (avif.read_bytes(), 'image/avif'))
        self.assertEqual(self.hub.read_moment_media(heic_url), (heic.read_bytes(), 'image/heic'))

        avif.write_bytes(b'\x00\x00\x00\x14ftypnope\x00\x00\x00\x00xxxx')
        with self.assertRaisesRegex(ValueError, '签名有效'):
            self.hub.read_moment_media(avif_url)

    def test_moments_author_avatar_is_resolved_by_exact_wechat_account(self):
        from types import SimpleNamespace

        self.hub.store.ingest('wechat', 'wx-account', [], [], [
            {'id': 'wxid_author', 'name': 'Author',
             'avatar_url': 'https://wx.qlogo.cn/cache/correct'},
        ])
        self.hub.store.ingest('wechat', 'other-account', [], [], [
            {'id': 'wxid_author', 'name': 'Other',
             'avatar_url': 'https://wx.qlogo.cn/cache/wrong'},
            {'id': 'wxid_no_avatar', 'name': 'No avatar'},
        ])

        cache = Path(self.temp.name) / 'sns_cache'
        cache.mkdir(exist_ok=True)
        self.hub.store.replace_moments('wx-account', [
                    {'id': 'author', 'username': 'wxid_author'},
                    {'id': 'no-avatar', 'username': 'wxid_no_avatar'},
                    {'id': 'unknown', 'username': 'wxid_unknown'},
                ], cache)
        self.hub.providers['wechat'] = SimpleNamespace(
            reader=SimpleNamespace(wxid='wx-account', account='ignored'),
            sns_cache_dir=cache)
        posts = self.hub.search_moments()['items']
        avatars = {post['username']: post['avatar_url'] for post in posts}
        self.assertEqual(avatars['wxid_author'], 'https://wx.qlogo.cn/cache/correct')
        self.assertIsNone(avatars['wxid_no_avatar'])
        self.assertIsNone(avatars['wxid_unknown'])


if __name__ == '__main__':
    unittest.main()
