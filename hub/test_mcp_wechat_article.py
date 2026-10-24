"""MCP local article reads: exact URL, capability gates and complete bounded pages."""
from contextlib import closing
import json
import unittest
from unittest.mock import patch

from hub.mcp_server import MCPServer
from hub.store import ChatStore

URL = 'https://mp.weixin.qq.com/s/fixture'
TOOL = 'get_wechat_article_full_text'


class MCPWeChatArticleTests(unittest.TestCase):
    def test_complete_plain_text_pages_without_network_or_archive_writes(self):
        body = '合成测试正文' * 2000
        html = f'<h1 id="activity-name">合成文章</h1><div id="js_content">{body}<script>private_script</script></div>'
        with closing(ChatStore(':memory:')) as store, \
                patch('integrations.wechat.article_cache.read_cached_article_html', return_value=html) as cache, \
                patch('integrations.campus.adapter._fetch_html') as network:
            server = MCPServer(store)
            tool = next(t for t in server.tools() if t['name'] == TOOL)
            self.assertTrue(tool['annotations']['readOnlyHint'])
            self.assertFalse(tool['annotations']['openWorldHint'])
            pages, offset = [], 0
            while offset is not None:
                response = server.handle(dict(jsonrpc='2.0', id=1, method='tools/call',
                    params=dict(name=TOOL, arguments=dict(url=URL, offset=offset, max_chars=6000))))
                result = response['result']
                self.assertFalse(result['isError'])
                page = result['structuredContent']
                self.assertEqual(page['status'], 'full_text')
                self.assertEqual(page['item']['content_source'], 'local_wechat_cache')
                self.assertEqual(page['total_characters'], len(body))
                self.assertEqual(json.loads(result['content'][0]['text']), page)
                self.assertLessEqual(len(page['content']), 6000)
                pages.append(page['content'])
                offset = page['next_offset']
            self.assertEqual(''.join(pages), body)
            cache.assert_called_with(URL)
            network.assert_not_called()
            self.assertEqual(store.count('wechat'), 0)

    def test_miss_blocked_and_cache_error_are_not_full_text(self):
        with closing(ChatStore(':memory:')) as store:
            server = MCPServer(store)
            for value, status in ((None, 'not_cached'), ('<h1>环境异常</h1>', 'blocked')):
                with patch('integrations.wechat.article_cache.read_cached_article_html', return_value=value):
                    result = server.call_tool(TOOL, dict(url=URL))
                    self.assertEqual(result['status'], status)
                    self.assertIsNone(result['item'])
                    self.assertIsNone(result['next_offset'])
            with patch('integrations.wechat.article_cache.read_cached_article_html', side_effect=OSError('secret-path')):
                result = server.call_tool(TOOL, dict(url=URL))
                self.assertEqual(result['status'], 'error')
                self.assertNotIn('secret-path', str(result))

    def test_rejected_arguments_and_revoked_capabilities_do_not_open_cache(self):
        state = dict(campus=True, mcp=True)
        with closing(ChatStore(':memory:')) as store, \
                patch('integrations.wechat.article_cache.read_cached_article_html') as cache:
            server = MCPServer(store, capability_reader=lambda: state)
            for args in ({}, {'url': 'file:///private'}, {'url': 'https://example.com/'},
                         {'url': 'https://mp.weixin.qq.com/mp/captcha'}, {'url': 1},
                         {'url': URL, 'path': 'private'}, {'url': URL, 'max_chars': True},
                         {'url': URL, 'max_chars': 6001}, {'url': URL, 'offset': -1},
                         {'url': URL, 'offset': True}, {'url': URL, 'offset': 4194305}):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    server.call_tool(TOOL, args)
            for capability in ('campus', 'mcp'):
                state[capability] = False
                self.assertNotIn(TOOL, {t['name'] for t in server.tools()})
                with self.assertRaises(ValueError):
                    server.call_tool(TOOL, dict(url=URL))
                state[capability] = True
            cache.assert_not_called()

    def test_url_credentials_are_removed_before_local_matching_and_output(self):
        url = 'https://mp.weixin.qq.com/s?__biz=abc&mid=1&sn=xyz&key=private&pass_ticket=private'
        html = '<meta property="og:title" content="Fixture"><div id="js_content">Body</div>'
        with closing(ChatStore(':memory:')) as store, \
                patch('integrations.wechat.article_cache.read_cached_article_html', return_value=html) as cache:
            result = MCPServer(store).call_tool(TOOL, dict(url=url))
            cache.assert_called_once_with('https://mp.weixin.qq.com/s?__biz=abc&mid=1&sn=xyz')
            self.assertNotIn('private', str(result))


if __name__ == '__main__':
    unittest.main()
