import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from datetime import date
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from hub.server import Handler, Hub, message_body, qq_login_result


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hub = Hub(Path(self.temp.name))
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
        self.hub.store.close()
        self.temp.cleanup()

    def request(self, path, headers=None, body=None):
        return urlopen(Request(self.url + path, headers=headers or {}, data=body), timeout=3)

    def test_real_store_query_contract(self):
        with self.request('/api/search?q=hello') as response:
            data = json.load(response)
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['items'][0]['conversation_title'], 'Test')

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

    def test_mcp_settings_expose_read_only_client_config_and_recent_operations(self):
        self.hub.store.record_mcp_operation('search_messages', 'success')
        with self.request('/api/mcp') as response:
            settings = json.load(response)
        self.assertTrue(settings['available'])
        self.assertEqual(settings['mode'], 'read-only')
        self.assertEqual(settings['transport'], 'stdio')
        self.assertIn('tools:\n      include:', settings['config_text'])
        self.assertIn('本地消息整合中枢', settings['config_text'])
        self.assertNotIn('\\u672c', settings['config_text'])
        self.assertEqual(settings['operations'][0]['tool'], 'search_messages')

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

    def test_contact_identity_endpoints_link_and_unlink(self):
        self.hub.store.ingest('wechat', 'account', [], [], [{'id': 'contact', 'name': 'Contact'}])
        headers = {'Content-Type': 'application/json'}
        payload = json.dumps({'platform': 'wechat', 'account_id': 'account', 'contact_id': 'contact',
                              'identity_id': 'same-person'}).encode()
        with self.request('/api/contact-identities', headers, payload) as response:
            self.assertEqual(json.load(response)['identity_id'], 'same-person')
        with self.request('/api/contacts?platform=wechat') as response:
            self.assertEqual(json.load(response)['items'][0]['identity_id'], 'same-person')
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
        first.store.close()
        restarted = Hub(storage)
        deadline = time.monotonic() + 5
        job = restarted.store.import_job(job_id)
        while job['state'] in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(0.01)
            job = restarted.store.import_job(job_id)
        self.assertEqual(job['state'], 'completed')
        self.assertEqual(restarted.store.search('resumed', platform='wecom')['total'], 1)
        restarted.store.close()

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

    def test_qq_service_not_ready_is_not_login_ready(self):
        from unittest.mock import patch
        with patch('integrations.qq.provider.QQProvider') as provider:
            provider.return_value.status.return_value = {'available': False, 'online': False}
            self.hub.sync('qq')
        self.assertEqual(self.hub.sources['qq']['state'], 'error')
        self.assertIsNone(self.hub.sources['qq']['last_sync'])

    def test_qq_ready_but_offline_requires_login(self):
        from unittest.mock import patch
        with patch('integrations.qq.provider.QQProvider') as provider:
            provider.return_value.status.return_value = {'available': True, 'online': False}
            self.hub.sync('qq')
        self.assertEqual(self.hub.sources['qq']['state'], 'login_required')

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
            with patch('integrations.qq.provider.QQProvider', Provider):
                with self.assertRaisesRegex(RuntimeError, 'simulated interruption'):
                    first.sync_qq()
            checkpoint = first.store.sync_checkpoint('qq', 'qq-account', 'peer')
            self.assertEqual(checkpoint['next_offset'], 500)
            self.assertFalse(checkpoint['complete'])
            first.store.close()

            restarted = Hub(storage)
            with patch('integrations.qq.provider.QQProvider', Provider):
                restarted.sync_qq()
            self.assertEqual(calls, [0, 500, 500])
            self.assertEqual(restarted.store.count('qq'), 501)
            self.assertTrue(restarted.store.sync_checkpoint(
                'qq', 'qq-account', 'peer')['complete'])
            restarted.store.close()
        finally:
            if first.store._conn is not None:
                first.store.close()

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

        with patch('integrations.qq.provider.QQProvider', Provider):
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
                pass

            def refresh(self):
                return True

            def list_conversations(self, _limit):
                return [{'conversation_id': 'room', 'name': 'Fixture',
                         'participants': 3, 'last_time': 1}]

            def iter_message_batches(self, _batch_size):
                rows = [{'message_id': 'stable-id', 'conversation_id': 'room',
                         'sender_id': 'member', 'sender': 'Member', 'send_time': 1,
                         'content_type': 1, 'text': source['text'], 'me': False}]
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
                return [{'id': 'wx:last', 'sender_id': 'user', 'content': 'fixture',
                         'local_type': 1, 'timestamp': 2001}]

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
            first.store.close()

            restarted = Hub(storage)
            with patch.dict(sys.modules, {'integrations.wechat.provider': provider_module}):
                restarted.sync_wechat()
            self.assertEqual(calls, [0, 2000, 2000])
            self.assertEqual(restarted.store.count('wechat'), 2001)
            self.assertTrue(restarted.store.sync_checkpoint(
                'wechat', 'wx-account', 'room')['complete'])
            restarted.store.close()
        finally:
            if first.store._conn is not None:
                first.store.close()
            if restarted is not None and restarted.store._conn is not None:
                restarted.store.close()

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
                        {'id': 'wx:2', 'sender_id': 'user', 'content': 'two', 'local_type': 1,
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
            checkpoint = hub.store.sync_checkpoint('wechat', 'wx-account', 'room')
            self.assertEqual(checkpoint['cursor']['sort_seq'], 30)
            self.assertTrue(checkpoint['complete'])
        finally:
            hub.store.close()

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

    def test_qq_login_status_does_not_return_credentials_or_qr(self):
        from unittest.mock import patch
        with patch('integrations.qq.provider.QQProvider') as provider:
            provider.return_value.login_status.return_value = {
                'loginPhase': 'waiting_qrcode', 'coreReady': False,
                'Credential': 'private', 'qrcodeurl': 'private', 'token': 'private'}
            with self.request('/api/qq/login-status') as response:
                result = json.load(response)
        self.assertEqual(result, {'phase': 'waiting_qrcode', 'online': False})

    def test_qq_history_status_starts_idle_and_rejects_invalid_range(self):
        with self.request('/api/qq/history-import') as response:
            self.assertEqual(json.load(response), {'state': 'idle'})
        with self.assertRaises(HTTPError) as result:
            self.request('/api/qq/history-import', {'Content-Type': 'application/json'}, json.dumps({
                'conversation_id': 'peer-uid', 'account_id': 'account',
                'start': '2026-01-02', 'end': '2026-01-01'}).encode())
        self.assertEqual(result.exception.code, 400)

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
        with patch('integrations.qq.provider.QQProvider', return_value=provider):
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
        with patch('integrations.qq.provider.QQProvider') as provider, patch.object(self.hub, 'start_sync'):
            provider.return_value.launch.return_value = {
                'started': True, 'login_phase': 'waiting_qrcode',
                'qr_url': 'https://ssl.ptlogin2.qq.com/test-local-qr', 'log_file': 'private'}
            with self.request('/api/qq/start', {'Content-Type': 'application/json'}, b'{"refresh":true}') as response:
                result = json.load(response)
            provider.return_value.launch.assert_called_once_with(refresh=True)
        self.assertNotIn('qr_url', result)
        self.assertNotIn('log_file', result)
        self.assertIn('qr_image', result)

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

    def test_export_html_is_attachment_and_escaped(self):
        with self.request('/api/export?platform=wechat&account_id=account&conversation_id=room&format=html') as response:
            self.assertIn('attachment', response.headers['Content-Disposition'])
            self.assertIn(b'&lt;script&gt;', response.read())

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
        provider.media.assert_called_once_with('room', 7, 'message_0.db')

    def test_message_identity_lookup(self):
        self.assertEqual(self.hub.store.get_message('wechat', 'account', 'room:1')['text'], 'hello <script>')
        self.assertIsNone(self.hub.store.get_message('wecom', 'account', 'room:1'))

    def test_untrusted_attachment_forced_download(self):
        payload = Path(self.temp.name) / 'chat.svg'
        payload.write_text('<svg><script>evil()</script></svg>')
        self.hub.media_paths['test-capability'] = payload
        with self.request('/local-media/test-capability') as response:
            self.assertIn('attachment', response.headers['Content-Disposition'])

    def test_media_range(self):
        payload = Path(self.temp.name) / 'voice.wav'
        payload.write_bytes(b'RIFFtestWAVE')
        self.hub.media_paths['test-voice'] = payload
        with self.request('/local-media/test-voice', {'Range': 'bytes=0-3'}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), b'RIFF')


if __name__ == '__main__':
    unittest.main()
