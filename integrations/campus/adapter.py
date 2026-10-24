"""Bounded public HTML collection from fixed official sources and WeChat articles."""
from __future__ import annotations

import hashlib
import gzip
import io
import re
import socket
from datetime import date
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 12
MAX_ITEMS = 100
_ARTICLE = re.compile(r"/(?:article|node)/\d+|/info/\d+/\d+\.html?$|/\d{4}[-/]\d{2}/[^/]+\.html?$|/content/[^/]+|/(?:campus|teachin|jobfair|news)/view/(?:id|aid)/\d+", re.I)
_DATE = re.compile(r"(?<!\d)(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})(?:日)?(?!\d)")
_VOID = {"br", "img", "meta", "link", "input", "hr", "source", "wbr", "embed", "area", "base", "col", "param", "track"}


class _ArticleBlocked(ValueError):
    pass


def public_wechat_url(url):
    """Retain public article identity only, never forward login/share credentials."""
    if not isinstance(url, str) or len(url) > 8192 or not is_wechat_article_url(url):
        raise ValueError("unsupported public article")
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=False)
    query = [(key, value) for key, value in pairs
             if key in {"__biz", "mid", "idx", "sn"} and len(value) <= 512]
    if len({key for key, _ in query}) != len(query):
        raise ValueError("duplicate article identifier")
    if parts.path == "/s" and not {key for key, _ in query} >= {"__biz", "mid", "sn"}:
        raise ValueError("missing article identity")
    return urlunsplit(("https", "mp.weixin.qq.com", parts.path, urlencode(sorted(query)), ""))


def is_official_url(url):
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and (p.hostname == "sysu.edu.cn" or
                (p.hostname or "").endswith(".sysu.edu.cn")) and
                not p.username and not p.password and p.port in {None, 443})
    except (ValueError, TypeError):
        return False


def is_wechat_article_url(url):
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and p.hostname == "mp.weixin.qq.com" and
                p.path.startswith("/s") and (p.path == "/s" or p.path.startswith("/s/")) and
                not p.username and not p.password and p.port in {None, 443})
    except (ValueError, TypeError):
        return False


class _Redirects(HTTPRedirectHandler):
    def __init__(self, allowed):
        self.allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.allowed(newurl):
            if self.allowed is is_wechat_article_url and urlsplit(newurl).hostname == "mp.weixin.qq.com":
                raise _ArticleBlocked("public article requires verification")
            raise ValueError("redirect outside allowed public source")
        if self.allowed is is_wechat_article_url:
            newurl = public_wechat_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_html(url, allowed):
    if not allowed(url):
        raise ValueError("unsupported public source")
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 LocalMessageHub/1.0 (public notices)",
                                    "Accept": "text/html,application/xhtml+xml"})
    with build_opener(_Redirects(allowed)).open(request, timeout=TIMEOUT) as response:
        if not allowed(response.geturl()):
            raise ValueError("response outside allowed public source")
        if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("response is not HTML")
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("response too large")
        encoding = response.headers.get("Content-Encoding", "identity").lower()
        if encoding == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
                data = compressed.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("response too large")
        elif encoding != "identity":
            raise ValueError("unsupported response encoding")
        charset = response.headers.get_content_charset()
        if not charset:
            match = re.search(rb"charset\s*=\s*[\"']?([\w-]+)", data[:8192], re.I)
            charset = match.group(1).decode("ascii") if match else "utf-8"
        try:
            return data.decode(charset, errors="replace")
        except LookupError:
            return data.decode("utf-8", errors="replace")


class _Node:
    def __init__(self, tag="", attrs=None, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs or []), parent
        self.children = []

    def text(self):
        value = " ".join(c if isinstance(c, str) else c.text() for c in self.children)
        return self.attrs.get("datetime", "") + " " + value if self.tag == "time" else value


class _Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.current = self.root
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        # HTMLParser does not apply the browser's implicit li/p closing rules.
        if tag == "li":
            cursor = self.current
            while cursor.parent and cursor.tag not in {"ul", "ol"}:
                if cursor.tag == "li":
                    self.current = cursor.parent
                    break
                cursor = cursor.parent
        node = _Node(tag, attrs, self.current)
        self.current.children.append(node)
        self.nodes.append(node)
        if tag not in _VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        cursor = self.current
        while cursor.parent:
            if cursor.tag == tag:
                self.current = cursor.parent
                break
            cursor = cursor.parent

    def handle_data(self, data):
        if self.current.tag not in {"script", "style"}:
            self.current.children.append(data)


def _date(text):
    match = _DATE.search(text)
    if match:
        try:
            return date(*map(int, match.groups())).isoformat()
        except ValueError:
            pass
    return ""


def _article_url(href, base):
    url = urljoin(base, href)
    p = urlsplit(url)
    # Official sites still emit HTTP links; store the equivalent secure link.
    if p.scheme == "http":
        url = urlunsplit(("https", p.netloc, p.path, p.query, ""))
    if not (is_official_url(url) or is_wechat_article_url(url)):
        return ""
    p = urlsplit(url)
    if not (is_wechat_article_url(url) or _ARTICLE.search(p.path)):
        return ""
    return urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))


def parse_source(html, source):
    """Extract public article links; keep unknown publication dates empty."""
    doc = _Document()
    doc.feed(html)
    items, seen = [], set()
    for node in doc.nodes:
        if node.tag != "a":
            continue
        url = _article_url(node.attrs.get("href", ""), source["url"])
        title = " ".join((node.attrs.get("title") or node.text()).split())
        if not url or url in seen or len(title) < 5:
            continue
        parent, published, hidden, date_scope = node, "", False, True
        while parent:
            if parent.tag in {"nav", "header", "footer", "script", "style"}:
                hidden = True
                break
            if not published and date_scope:
                text = parent.text()
                if len(text) < 1000:
                    published = _date(text)
            if parent.tag in {"li", "tr", "article"} or "newslist" in parent.attrs.get("class", "").split():
                date_scope = False
            parent = parent.parent
        if hidden:
            continue
        seen.add(url)
        items.append(dict(id="campus-" + hashlib.sha256(url.encode()).hexdigest(),
                          url=url, title=title[:500], published_at=published, summary="",
                          category=source["category"], source_id=source["id"],
                          source_name=source["name"], faculty="电子与信息学院" if source["id"].startswith("seit-") else "",
                          campus="" if source.get("scope") == "all" else source.get("scope", "")))
        if len(items) >= MAX_ITEMS:
            break
    return items


def _error(exc):
    if isinstance(exc, HTTPError):
        return f"公开来源返回 HTTP {exc.code}，未绕过访问限制"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "公开来源请求超时"
    if isinstance(exc, URLError):
        return "公开来源网络或证书连接失败"
    return "公开来源响应不受支持或解析失败"


def fetch_source(source):
    if source.get("kind") != "feed":
        return dict(items=[], status="portal", error="")
    try:
        items = parse_source(_fetch_html(source["url"], is_official_url), source)
        return dict(items=items, status="ok" if items else "empty", error="")
    except (OSError, ValueError, UnicodeError, RecursionError, EOFError) as exc:
        return dict(items=[], status="error", error=_error(exc))


def parse_wechat_article(html, url, content_source="public_http"):
    """Parse article content only; scripts and verification pages are not text."""
    url = public_wechat_url(url)
    doc = _Document()
    doc.feed(html)
    metas = {n.attrs.get("property", n.attrs.get("name", "")): n.attrs.get("content", "")
             for n in doc.nodes if n.tag == "meta"}
    body = next((n for n in doc.nodes if n.attrs.get("id") == "js_content"), None)
    title = metas.get("og:title", "").strip() or next(
        (n.text().strip() for n in doc.nodes if n.attrs.get("id") == "activity-name"), "")
    if not title or body is None or not body.text().strip():
        return dict(items=[], status="blocked", error="文章未公开可读，可能已删除或需要在微信完成访问验证")
    account = next((n.text().strip() for n in doc.nodes if n.attrs.get("id") == "js_name"), "微信公众号")
    published = _date(metas.get("article:published_time", ""))
    if not published:
        match = re.search(r"(?:var\s+)?(?:publish_time|ct)\s*=\s*[\"']?(\d{10})", html)
        if match:
            from datetime import datetime, timezone, timedelta
            published = datetime.fromtimestamp(int(match.group(1)), timezone(timedelta(hours=8))).date().isoformat()
    text = " ".join(body.text().split())
    item = dict(id="campus-" + hashlib.sha256(url.encode()).hexdigest(), url=url,
                title=title[:500], published_at=published, summary=text[:500], content=text,
                category="wechat", source_id="wechat", source_name=account[:100], campus="",
                content_status="full_text", content_source=content_source)
    return dict(items=[item], status="ok", error="")


def read_cached_wechat_article(url):
    """Read an exact public article from WeChat's local web cache, without network."""
    url = public_wechat_url(url)
    from integrations.wechat.article_cache import read_cached_article_html
    try:
        html = read_cached_article_html(url)
        if html is None:
            return dict(items=[], status="not_cached", error="本机微信网页缓存未命中，请先在微信打开该文章")
        return parse_wechat_article(html, url, content_source="local_wechat_cache")
    except (OSError, ValueError, UnicodeError, RecursionError, EOFError):
        return dict(items=[], status="error", error="本机微信文章缓存暂时不可读")


def fetch_wechat_article(url):
    """Read one user-supplied public article; never enumerate an account's history."""
    if not is_wechat_article_url(url):
        return dict(items=[], status="error", error="仅支持 HTTPS 微信公众号文章链接")
    try:
        url = public_wechat_url(url)
        return parse_wechat_article(_fetch_html(url, is_wechat_article_url), url)
    except _ArticleBlocked:
        return dict(items=[], status="blocked", error="微信要求访问验证，请在微信打开原文；保留现有摘要")
    except (OSError, ValueError, UnicodeError, RecursionError, EOFError) as exc:
        return dict(items=[], status="error", error=_error(exc))
