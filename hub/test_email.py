"""Synthetic mail acceptance through the shared index, HTTP and MCP."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen

from hub.imports import parse_import
from hub.mcp_server import MCPServer
from hub.server import Handler, Hub


MAIL = (b'From: Sender <sender@example.test>\r\nTo: me@example.test\r\n'
        b'Subject: archive acceptance\r\nMessage-ID: <acceptance@example.test>\r\n'
        b'Date: Fri, 23 Oct 2026 10:00:00 +0800\r\n\r\nsearchable mail fixture')


class EmailIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hub = Hub(Path(self.temp.name), resume_imports=False)

    def tearDown(self):
        self.hub.close()
        self.temp.cleanup()

    def test_eml_http_preview_job_search_export_and_mcp(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.hub = self.hub
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        def upload(path):
            request = Request(base + path, data=MAIL,
                              headers={'Content-Type': 'application/octet-stream'})
            with urlopen(request, timeout=3) as response:
                return json.load(response)
        try:
            query = '?format=eml&platform=email&account=me%40example.test'
            preview = upload('/api/imports/preview' + query)
            self.assertEqual(preview['valid'], 1)
            self.assertEqual(preview['platforms'], ['email'])
            for _ in range(2):
                job = upload('/api/imports' + query)
                deadline = time.monotonic() + 3
                while self.hub.store.import_job(job['id'])['state'] in ('queued', 'running') and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(self.hub.store.import_job(job['id'])['state'], 'completed')
            with urlopen(base + '/api/search?q=searchable&platform=email', timeout=3) as response:
                self.assertEqual(json.load(response)['total'], 1)
            with urlopen(base + '/api/export?format=json&platform=email', timeout=3) as response:
                self.assertIn(b'searchable mail fixture', response.read())
            mcp = MCPServer(self.hub.store)
            result = mcp.call_tool('search_messages', {'platform': 'email', 'query': 'searchable'})
            self.assertEqual(result['total'], 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_sync_commits_only_after_successful_ingest(self):
        records = parse_import(MAIL, 'eml', 'email', 'me@example.test')
        provider = Mock()
        provider.status.return_value = {'configured': True}
        provider.sync.return_value = {'records': records, 'checkpoint': {'uid': 1}}
        self.hub.providers['email'] = provider
        with patch.object(self.hub.store, 'ingest', side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):
                self.hub.sync_email()
        provider.commit_checkpoint.assert_not_called()
        self.hub.sync_email()
        provider.commit_checkpoint.assert_called_once_with({'uid': 1})
        self.assertEqual(self.hub.store.search('searchable', platform='email')['total'], 1)

    def test_unconfigured_and_cancelled_sync_never_advances_checkpoint(self):
        provider = Mock()
        provider.status.return_value = {'configured': False}
        self.hub.providers['email'] = provider
        self.assertEqual(self.hub.sync_email(), 'unavailable')
        provider.sync.assert_not_called()
        provider.status.return_value = {'configured': True}
        provider.sync.return_value = {'records': parse_import(MAIL, 'eml', 'email'), 'checkpoint': {}}
        cancel = threading.Event()
        cancel.set()
        self.hub.sync_email(cancel)
        provider.commit_checkpoint.assert_not_called()

    def test_mail_import_rejects_other_platform(self):
        with self.assertRaises(ValueError):
            parse_import(MAIL, 'eml', 'wechat')

    def test_configure_protects_secrets_and_exposes_only_redacted_status(self):
        with patch.object(self.hub, 'start_sync') as start:
            self.hub.configure('email', {
                'provider': 'gmail', 'email': 'me@example.test',
                'username': 'me@example.test', 'auth': 'oauth2',
                'access_token': 'synthetic-mail-token', 'folder': 'INBOX', 'port': 993,
            })
        start.assert_called_once_with('email')
        source = next(s for s in self.hub.source_info()['sources'] if s['id'] == 'email')
        self.assertEqual(source['email_config']['host'], 'imap.gmail.com')
        self.assertTrue(source['email_config']['configured'])
        self.assertNotIn('synthetic-mail-token', json.dumps(source))
        self.assertNotIn(b'synthetic-mail-token', (Path(self.temp.name) / 'email.credentials').read_bytes())


if __name__ == '__main__':
    unittest.main()
