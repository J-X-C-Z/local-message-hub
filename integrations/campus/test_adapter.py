"""Offline fixtures for public source parsing and network boundaries."""
import io
import gzip
import unittest
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from integrations.campus.adapter import (
    MAX_BYTES, MAX_ITEMS, _Redirects, _fetch_html, fetch_source,
    fetch_wechat_article, is_official_url, is_wechat_article_url, parse_source,
    public_wechat_url,
)
from integrations.campus.catalog import CATEGORIES, SOURCES

SOURCE = next(s for s in SOURCES if s["id"] == "seit-student")


class CampusAdapterTest(unittest.TestCase):
    def test_catalog_official_unique_and_covers_faculties(self):
        self.assertEqual(len(SOURCES), len({s["id"] for s in SOURCES}))
        self.assertTrue(all(is_official_url(s["url"]) for s in SOURCES))
        self.assertTrue(all(s["category"] in CATEGORIES for s in SOURCES))
        self.assertGreaterEqual(sum(s["category"] == "faculty" for s in SOURCES), 65)
        self.assertTrue(all(s["kind"] in {"feed", "portal", "directory"} for s in SOURCES))

    def test_dated_nested_notice_and_safe_link_dedup(self):
        html = '''<nav><a href="/article/1">导航里的文章标题</a></nav>
        <ul><li><span><time datetime="2026-10-22T10:10:00+08:00">22</time><b>2026-10</b></span>
        <p><a href="http://seit.sysu.edu.cn/article/3108#top" title="本科生专项奖学金通知">摘要</a></p></li>
        <li><a href="/article/3108">重复本科生专项奖学金通知</a></li>
        <li><a href="https://evil.test/article/9">外部正文链接</a></li>
        <li><a href="/sites/files/award.pdf">奖学金附件PDF</a></li>
        <li><a href="/student/notice">查看所有学生通知</a></li></ul>'''
        items = parse_source(html, SOURCE)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://seit.sysu.edu.cn/article/3108")
        self.assertEqual(items[0]["published_at"], "2026-10-22")
        self.assertEqual(items[0]["faculty"], "电子与信息学院")

    def test_no_date_invented_and_no_cross_row_dates(self):
        html = '<ul><li>2026-10-22<a href="/article/5">第一条奖助学金通知</a></li><li><a href="/article/6">第二条竞赛信息通知</a></li></ul>'
        items = parse_source(html, SOURCE)
        self.assertEqual([i["published_at"] for i in items], ["2026-10-22", ""])

    def test_date_styles_and_invalid_dates(self):
        for day, expected in [("2026/10/23", "2026-10-23"), ("2026年10月3日", "2026-10-03"), ("2026-02-31", "")]:
            self.assertEqual(parse_source(f'<li>{day}<a href="/article/1">本科竞赛活动通知</a></li>', SOURCE)[0]["published_at"], expected)

    def test_official_wechat_link_and_career(self):
        html = '<li>2026-10-23<a href="https://mp.weixin.qq.com/s/abc">学校公众号公开文章</a></li><li><a href="https://career.sysu.edu.cn/campus/view/id/997717">校园招聘公开通知</a></li>'
        self.assertEqual(len(parse_source(html, SOURCE)), 2)

    def test_item_cap(self):
        html = ''.join(f'<li><a href="/article/{i}">本科竞赛活动通知{i}</a></li>' for i in range(MAX_ITEMS + 10))
        self.assertEqual(len(parse_source(html, SOURCE)), MAX_ITEMS)

    def test_url_boundaries(self):
        for url in ["http://jwb.sysu.edu.cn/", "https://sysu.edu.cn.evil.test/", "https://sysu.edu.cn@evil.test/", "https://jwb.sysu.edu.cn:444/", "https://user@jwb.sysu.edu.cn/"]:
            self.assertFalse(is_official_url(url), url)
        for url in ["https://mp.weixin.qq.com/", "https://mp.weixin.qq.com/sso", "http://mp.weixin.qq.com/s/abc", "https://mp.weixin.qq.com.evil.test/s/abc", "https://user@mp.weixin.qq.com/s/abc"]:
            self.assertFalse(is_wechat_article_url(url), url)
        self.assertTrue(is_wechat_article_url("https://mp.weixin.qq.com/s?__biz=abc&mid=1"))

    def test_redirect_rejected_before_request(self):
        with self.assertRaises(ValueError):
            _Redirects(is_official_url).redirect_request(Request(SOURCE["url"]), None, 302, "", {}, "https://evil.test/")

    def test_portals_never_fetched(self):
        with patch("integrations.campus.adapter._fetch_html") as fetch:
            self.assertEqual(fetch_source(next(s for s in SOURCES if s["id"] == "portal"))["status"], "portal")
            fetch.assert_not_called()

    def test_errors_redacted_and_isolated(self):
        with patch("integrations.campus.adapter._fetch_html", side_effect=HTTPError(SOURCE["url"], 403, "private token", {}, io.BytesIO())):
            result = fetch_source(SOURCE)
        self.assertEqual(result["status"], "error")
        self.assertIn("403", result["error"])
        self.assertNotIn("private", result["error"])

    def test_response_size_limit(self):
        class Response(io.BytesIO):
            headers = Message()
            headers["Content-Type"] = "text/html; charset=utf-8"
            def geturl(self):
                return SOURCE["url"]
        with patch("integrations.campus.adapter.build_opener") as opener:
            opener.return_value.open.return_value = Response(b"x" * (MAX_BYTES + 1))
            with self.assertRaises(ValueError):
                _fetch_html(SOURCE["url"], is_official_url)

    def test_wechat_public_article_and_challenge(self):
        html = '''<meta property="og:title" content="竞赛报名通知"><meta property="article:published_time" content="2026-10-23">
        <a id="js_name">中山大学教务部</a><div id="js_content"><p>报名截至10月30日。</p></div>'''
        with patch("integrations.campus.adapter._fetch_html", return_value=html):
            result = fetch_wechat_article("https://mp.weixin.qq.com/s/abc")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["items"][0]["source_name"], "中山大学教务部")
        self.assertEqual(result["items"][0]["published_at"], "2026-10-23")
        self.assertEqual(result["items"][0]["content_status"], "full_text")
        self.assertEqual(result["items"][0]["content_source"], "public_http")
        with patch("integrations.campus.adapter._fetch_html", return_value="<h1>环境异常，请在微信中打开</h1>"):
            self.assertEqual(fetch_wechat_article("https://mp.weixin.qq.com/s/abc")["status"], "blocked")
        with patch("integrations.campus.adapter._fetch_html") as fetch:
            self.assertEqual(fetch_wechat_article("https://evil.test/")["status"], "error")
            fetch.assert_not_called()

    def test_wechat_only_public_identifiers_sent_and_title_fallback(self):
        url = 'https://mp.weixin.qq.com/s?mid=1&__biz=abc&sn=xyz&pass_ticket=secret&key=secret#part'
        html = '<h1 id="activity-name">公众号正文</h1><div id="js_content"><p>正文内容</p><script>secret</script></div>'
        with patch('integrations.campus.adapter._fetch_html', return_value=html) as fetch:
            result = fetch_wechat_article(url)
        fetch.assert_called_once_with('https://mp.weixin.qq.com/s?__biz=abc&mid=1&sn=xyz', is_wechat_article_url)
        self.assertEqual(result['status'], 'ok')
        self.assertNotIn('secret', result['items'][0]['content'])
        self.assertNotIn('secret', result['items'][0]['url'])
        for invalid in ('https://mp.weixin.qq.com/s?key=secret',
                        'https://mp.weixin.qq.com/s?__biz=abc&mid=1&mid=2&sn=xyz'):
            with self.assertRaises(ValueError):
                public_wechat_url(invalid)

    def test_wechat_verification_redirect_has_blocked_status(self):
        with patch('integrations.campus.adapter.build_opener') as opener:
            opener.return_value.open.side_effect = lambda req, **kw: _Redirects(is_wechat_article_url).redirect_request(
                req, None, 302, '', {}, 'https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?secret=hidden')
            result = fetch_wechat_article('https://mp.weixin.qq.com/s/abc')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['items'], [])
        self.assertNotIn('secret', result['error'])

    def test_gzip_response_bounded_after_decompression(self):
        class Response(io.BytesIO):
            headers = Message()
            headers['Content-Type'] = 'text/html; charset=utf-8'
            headers['Content-Encoding'] = 'gzip'
            def geturl(self):
                return SOURCE['url']
        with patch('integrations.campus.adapter.build_opener') as opener:
            opener.return_value.open.return_value = Response(gzip.compress(b'<html>notice</html>'))
            self.assertEqual(_fetch_html(SOURCE['url'], is_official_url), '<html>notice</html>')
            opener.return_value.open.return_value = Response(gzip.compress(b'x' * (MAX_BYTES + 1)))
            with self.assertRaises(ValueError):
                _fetch_html(SOURCE['url'], is_official_url)


if __name__ == "__main__":
    unittest.main()
