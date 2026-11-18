"""Synthetic IMAP -> Hub -> WebUI/MCP attachment acceptance."""
import json
import hashlib
from email.message import EmailMessage
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from urllib.parse import urlencode

from hub.mcp_server import MCPServer
from hub.exports import message_resource_requests
from hub.server import Handler, Hub


class AttachmentDownloadTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hub = Hub(Path(self.temp.name), resume_imports=False)
        self.addCleanup(self.hub.close)
        crypt = patch('integrations.email.provider._crypt', side_effect=lambda value, _: value)
        crypt.start()
        self.addCleanup(crypt.stop)
        self.provider = self.hub.email_provider()
        self.provider.configure({'email': 'me@example.test', 'host': 'imap.example.test',
                                 'password': 'synthetic'})
        message = EmailMessage()
        message['From'] = 'sender@example.test'
        message['To'] = 'me@example.test'
        message['Message-ID'] = '<attachment@example.test>'
        message['Date'] = 'Mon, 26 Oct 2026 12:00:00 +0800'
        message.set_content('received file')
        message.add_attachment(b'%PDF-1.4 synthetic', maintype='application', subtype='pdf',
                               filename='report.pdf')
        self.mail = message.as_bytes()
        self.client = MagicMock()
        self.client.select.return_value = ('OK', [b'1'])
        self.client.response.return_value = ('UIDVALIDITY', [b'123'])
        self.client.uid.side_effect = self.uid
        connection = patch('integrations.email.provider.imaplib.IMAP4_SSL', return_value=self.client)
        self.connection = connection.start()
        self.addCleanup(connection.stop)

    def uid(self, command, *args):
        if command == 'SEARCH':
            return 'OK', [b'1']
        if args[1] == '(RFC822.SIZE)':
            return 'OK', [f'1 (RFC822.SIZE {len(self.mail)})'.encode()]
        return 'OK', [(b'1 (BODY[]', self.mail), b')']

    def identity(self):
        row = self.hub.store.search('received file', platform='email')['items'][0]
        return {key: row[key] for key in ('platform', 'account_id', 'conversation_id')} | {
            'message_id': row['id'], 'resource_index': 0}

    def enable(self):
        self.hub.set_capabilities({'attachment_download': True})

    def test_explicit_download_then_cache_only_preview_export_and_mcp(self):
        self.hub.sync_email()
        identity = self.identity()
        with self.assertRaises(PermissionError):
            self.hub.download_attachment(identity)
        self.connection.reset_mock()
        self.assertFalse(self.hub.resolve_media(identity).get('available'))
        self.connection.assert_not_called()
        self.enable()
        downloaded = self.hub.download_attachment(identity)
        self.assertTrue(downloaded['available'])
        self.assertEqual(downloaded['download_name'], 'report.pdf')
        refreshed = self.hub.store.get_message('email', identity['account_id'],
                                               identity['message_id'])
        self.assertTrue(refreshed['extra']['attachments'][0]['available'])
        self.connection.reset_mock()
        path, name = self.hub.resolve_resource(identity)
        self.assertEqual(path.read_bytes(), b'%PDF-1.4 synthetic')
        self.assertEqual(name, 'report.pdf')
        mcp = MCPServer(self.hub.store, media_reader=self.hub.read_cached_image,
                        attachment_downloader=self.hub.download_attachment,
                        capability_reader=lambda: self.hub.settings['capabilities'])
        tools = {tool['name']: tool for tool in mcp.tools()}
        self.assertIn('download_attachment', tools)
        self.assertIn('wechat', tools['download_attachment']['inputSchema']['properties']['platform']['enum'])
        self.assertEqual(mcp.call_tool('download_attachment', identity), {
            'available': True, 'download_name': 'report.pdf'})
        result = mcp.call_tool('get_attachment', {key: identity[key] for key in
                              ('platform', 'account_id', 'message_id', 'resource_index')})
        payload, mime = self.hub.read_cached_image(result['message'], result['resource_index'])
        self.assertEqual((payload, mime), (b'%PDF-1.4 synthetic', 'application/pdf'))
        self.connection.assert_not_called()
        self.assertEqual(message_resource_requests(result['message']), [identity])
        for source in ('chat', 'all'):
            page = self.hub.store.media('email', 'file', source=source, expand_attachments=True)
            self.assertEqual(page['total'], 1)
            self.assertEqual(page['items'][0]['media_entry']['resource_index'], 0)
        with self.assertRaises(ValueError):
            self.hub.download_attachment({**identity, 'conversation_id': 'wrong'})
        with self.assertRaises(ValueError):
            self.hub.download_attachment({**identity, 'resource_index': True})

    def test_email_with_multiple_attachments_requires_an_explicit_index(self):
        message = EmailMessage()
        message['From'] = 'sender@example.test'
        message['To'] = 'me@example.test'
        message['Message-ID'] = '<multiple@example.test>'
        message['Date'] = 'Mon, 26 Oct 2026 12:00:00 +0800'
        message.set_content('received files')
        for name in ('one.pdf', 'two.pdf'):
            message.add_attachment(b'%PDF-1.4 ' + name.encode(), maintype='application',
                                   subtype='pdf', filename=name)
        self.mail = message.as_bytes()
        self.hub.sync_email()
        identity = self.identity()
        identity.pop('resource_index')
        self.enable()
        self.connection.reset_mock()
        with self.assertRaisesRegex(ValueError, '指定'):
            self.hub.download_attachment(identity)
        self.connection.assert_not_called()

    def test_qq_private_and_group_download_stays_readable_without_online_bridge(self):
        payload = b'%PDF-1.4 QQ synthetic'
        source = Path(self.temp.name) / 'synthetic-source'
        source.write_bytes(payload)
        bridge = MagicMock()
        bridge.status.return_value = {'online': True, 'account_id': '12345'}
        bridge.media.return_value = {'available': True, 'path': str(source)}
        self.hub.providers['qq'] = bridge
        self.hub.providers['qq_local'] = MagicMock()
        self.enable()
        for kind in ('private', 'group'):
            cid = 'uid-peer' if kind == 'private' else '67890'
            self.hub.store.ingest('qq', '12345', [{'id': cid, 'kind': kind}], [{
                'id': kind, 'conversation_id': cid, 'timestamp': 1793000000, 'type': 'file',
                'file_name': 'report.pdf', 'local_database': True,
                'media': [{'type': 'file', 'kind': 'file', 'file_name': 'report.pdf',
                           'size': len(payload), 'md5': hashlib.md5(payload).hexdigest()}]}])
            identity = {'platform': 'qq', 'account_id': '12345', 'conversation_id': cid,
                        'message_id': kind, 'resource_index': 0}
            self.assertTrue(self.hub.download_attachment(identity)['available'])
            self.assertEqual(bridge.media.call_args.args[0]['chatType'], 2 if kind == 'group' else 1)
            bridge.reset_mock()
            path, _ = self.hub.resolve_resource(identity)
            self.assertEqual(path.read_bytes(), payload)
            row = self.hub.store.get_message('qq', '12345', kind)
            self.assertEqual(self.hub.read_cached_image(row, 0)[0], payload)
            bridge.status.assert_not_called()
            bridge.media.assert_not_called()

    def test_wechat_file_download_uses_exact_message_and_conversation_title(self):
        payload = b"%PDF-1.4 WeChat synthetic"
        source = Path(self.temp.name) / "wechat-source.pdf"
        source.write_bytes(payload)
        self.hub.providers['wechat'] = MagicMock()
        self.enable()
        content = (
            '<msg><appmsg><title>report.pdf</title><type>6</type><appattach>'
            f'<totallen>{len(payload)}</totallen></appattach>'
            f'<md5>{hashlib.md5(payload).hexdigest()}</md5></appmsg></msg>'
        )
        self.hub.store.ingest(
            'wechat', 'wxid_fixture',
            [{'id': 'fixture@chatroom', 'title': 'Fixture group', 'kind': 'group'}],
            [{'id': 'wechat-file', 'conversation_id': 'fixture@chatroom',
              'timestamp': 1793000000, 'type': 'file', 'raw_type': '49',
              'file_name': 'report.pdf', 'content': content,
              'shard': 'message_0.db', 'local_id': 7}])
        identity = {'platform': 'wechat', 'account_id': 'wxid_fixture',
                    'conversation_id': 'fixture@chatroom',
                    'message_id': 'wechat-file'}
        result = {'available': True, 'path': str(source), 'name': 'report.pdf',
                  'mime_type': 'application/pdf'}
        with patch('integrations.wechat.downloads.download_attachment',
                   return_value=result) as download:
            response = self.hub.download_attachment(identity)
        self.assertTrue(response['available'])
        self.assertEqual(response['download_name'], 'report.pdf')
        self.assertIn('download_name=report.pdf', response['media_url'])
        args, kwargs = download.call_args
        self.assertEqual(args[1]['id'], 'wechat-file')
        self.assertEqual(kwargs['conversation_title'], 'Fixture group')
        self.assertEqual(kwargs['resource_index'], 0)

    def test_wechat_hub_cache_survives_client_cache_removal(self):
        payload = b'%PDF-1.4 WeChat restart cache'
        content = (
            '<msg><appmsg><title>persisted.pdf</title><type>6</type><appattach>'
            f'<totallen>{len(payload)}</totallen></appattach>'
            f'<md5>{hashlib.md5(payload).hexdigest()}</md5></appmsg></msg>'
        )
        timestamp = 1793000000
        self.hub.store.ingest(
            'wechat', 'wxid_fixture',
            [{'id': 'fixture@chatroom', 'title': 'Fixture group', 'kind': 'group'}],
            [{'id': 'wechat-persisted', 'conversation_id': 'fixture@chatroom',
              'timestamp': timestamp, 'type': 'file', 'raw_type': '49',
              'file_name': 'persisted.pdf', 'content': content,
              'shard': 'message_0.db', 'local_id': 8}])
        account_dir = Path(self.temp.name) / 'wechat-account'
        month = time.strftime('%Y-%m', time.localtime(timestamp))
        source = account_dir / 'msg' / 'file' / month / 'persisted.pdf'
        source.parent.mkdir(parents=True)
        source.write_bytes(payload)
        source_row = {'create_time': timestamp, 'local_type': 49,
                      'server_id': '123456', 'content': content,
                      'shard': 'message_0.db'}
        provider = SimpleNamespace(
            reader=SimpleNamespace(
                wxid='wxid_fixture', account='wxid_fixture',
                account_dir=str(account_dir),
                get_message_row=MagicMock(return_value=source_row)),
            emoji_cache_account_dir=lambda: Path(self.temp.name) / 'emoji-cache')
        self.hub.providers['wechat'] = provider
        identity = {'platform': 'wechat', 'account_id': 'wxid_fixture',
                    'conversation_id': 'fixture@chatroom',
                    'message_id': 'wechat-persisted'}
        self.enable()
        self.assertTrue(self.hub.download_attachment(identity)['available'])
        source.unlink()
        resolved = self.hub.resolve_media(identity)
        self.assertTrue(resolved['available'])
        row = self.hub.store.get_message('wechat', 'wxid_fixture', 'wechat-persisted')
        self.assertEqual(self.hub.read_cached_image(row, 0)[0], payload)

    def test_auto_save_requires_both_switch_and_capability(self):
        self.hub.settings['auto_download_attachments'] = True
        self.hub.sync_email()
        identity = self.identity()
        self.assertFalse(self.hub.resolve_media(identity).get('available'))
        self.provider.reset_checkpoint()
        self.enable()
        self.hub.sync_email()
        self.assertTrue(self.hub.resolve_media(identity)['available'])
        self.hub.set_capabilities({'attachment_download': False})
        self.assertTrue(self.hub.resolve_media(identity)['available'])
        mcp = MCPServer(self.hub.store, attachment_downloader=self.hub.download_attachment)
        self.assertNotIn('download_attachment', [tool['name'] for tool in mcp.tools()])
        with self.assertRaises(ValueError):
            mcp.call_tool('download_attachment', identity)

    def test_http_download_settings_and_identity_errors(self):
        self.hub.sync_email()
        identity = self.identity()
        self.enable()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.hub = self.hub
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        opener = build_opener(ProxyHandler({}))
        def post(path, body):
            return opener.open(Request(base + path, data=json.dumps(body).encode(),
                                       headers={'Content-Type': 'application/json'}), timeout=5)
        try:
            with post('/api/attachments/download', identity) as response:
                result = json.load(response)
            with opener.open(base + '/api/media/resolve?' + urlencode(identity), timeout=5) as response:
                self.assertTrue(json.load(response)['available'])
            with opener.open(base + result['media_url'], timeout=5) as response:
                self.assertEqual(response.read(), b'%PDF-1.4 synthetic')
                self.assertIn('attachment', response.headers['Content-Disposition'])
            with post('/api/settings', {'auto_download_attachments': True}) as response:
                self.assertTrue(json.load(response)['auto_download_attachments'])
            with self.assertRaises(HTTPError):
                post('/api/settings', {'auto_download_attachments': 'yes'})
            self.hub.set_capabilities({'attachment_download': False})
            with self.assertRaises(HTTPError) as error:
                post('/api/attachments/download', identity)
            self.assertEqual(error.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
