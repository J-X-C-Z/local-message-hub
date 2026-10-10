"""WeFlow-Corp · 企业微信聊天记录本地实时查看服务。

纯 Python 标准库实现（零第三方依赖），提供：
  · Web UI（实时聊天 / 媒体库 / 统计分析 / 搜索 / 导出）
  · SSE 实时推送，新消息到达自动出现在页面
  · REST API，便于二次开发与自动化

用法
  python server.py                # 默认 http://127.0.0.1:5031
  python server.py --port 8080
  python server.py --interval 15  # 同步间隔（秒）
  python server.py --no-sync      # 不启动自动同步，只读已有数据
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import os
import posixpath
import queue
import sqlite3
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import ThreadingMixIn

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from .config import local_data_dir  # noqa: E402
from .engine import (  # noqa: E402
    SyncEngine, compute_stats, strip_internal, OUT, SYNC_DIR, WORK,
    MYVID, CTYPE, EXTRA, JSONL, WXROOT,
)

STATIC = os.path.join(HERE, 'static')
MEDIA_ROOT = os.path.abspath(OUT)
FULL_ROOT = os.path.abspath(WXROOT)
EXPORT_ROOT = os.path.join(local_data_dir(), 'exports')
REVOKE_SRC = os.path.join(local_data_dir(), 'revoked.json')

ENGINE: SyncEngine | None = None
ARGS = None


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
def log(*a):
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode('utf-8')


def safe_join(root: str, rel: str) -> str | None:
    """把 URL 里的相对路径安全地拼到 root 下，防目录穿越。"""
    rel = urllib.parse.unquote(rel or '').replace('\\', '/')
    rel = posixpath.normpath('/' + rel).lstrip('/')
    p = os.path.abspath(os.path.join(root, rel))
    if not p.startswith(os.path.abspath(root)):
        return None
    return p


def find_table(dbpath: str, name_hint: str):
    """在库里找包含 name_hint 的第一张表。"""
    try:
        c = sqlite3.connect('file:' + dbpath + '?mode=ro', uri=True)
        tables = [r[0] for r in c.execute(
            "select name from sqlite_master where type='table'")]
        c.close()
    except Exception:
        return None
    for t in tables:
        if name_hint.lower() in t.lower():
            return t
    return None


def collect_revoked() -> list:
    """从 message_revoke_record_table_v2 汇总撤回记录（撤回的消息仍留档）。"""
    import glob as _glob
    roots = []
    # 优先用最新一轮快照的解密结果，回退到全量解密目录
    snaps = sorted(_glob.glob(os.path.join(WORK, 'sync_snap', 'run_*')),
                   key=os.path.getmtime, reverse=True)
    for s in snaps[:1]:
        d = os.path.join(s, '__dec__')
        if os.path.isdir(d):
            roots.append(d)
    if not roots:
        roots.append(FULL_ROOT)

    out = []
    for root in roots:
        for dirpath, _d, files in os.walk(root):
            for f in files:
                if not f.endswith('.db'):
                    continue
                p = os.path.join(dirpath, f)
                t = find_table(p, 'message_revoke_record')
                if not t:
                    continue
                try:
                    c = sqlite3.connect('file:' + p + '?mode=ro', uri=True)
                    cols = [r[1] for r in c.execute(f'pragma table_info("{t}")')]
                    for row in c.execute(f'select * from "{t}"'):
                        d = dict(zip(cols, row))
                        out.append({
                            k: (v.decode('utf-8', 'replace')
                                if isinstance(v, (bytes, bytearray))
                                else (str(v) if v is not None else ''))
                            for k, v in d.items()})
                    c.close()
                except Exception:
                    pass
    # 去重（同一记录可能在多份快照里）
    seen, uniq = set(), []
    for r in out:
        k = json.dumps(r, sort_keys=True, ensure_ascii=False)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return uniq


# --------------------------------------------------------------------------
# 导出
# --------------------------------------------------------------------------
def do_export(fmt: str, conv: str | None) -> tuple[str, bytes, str]:
    """返回 (文件名, 内容, MIME)。"""
    msgs = ENGINE.snapshot_messages()
    if conv:
        msgs = [m for m in msgs if m['conversation'] == conv]
    msgs.sort(key=lambda m: m['send_time'] or 0)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    tag = (conv or '全部会话').replace('/', '_').replace('\\', '_')
    base = f"企微_{tag}_{stamp}"

    if fmt == 'json':
        data = json.dumps([strip_internal(m) for m in msgs],
                          ensure_ascii=False, indent=2).encode('utf-8')
        return base + '.json', data, 'application/json'

    if fmt == 'txt':
        buf = io.StringIO()
        for m in msgs:
            buf.write(f"[{m['time']}] ({m['conversation']}) "
                      f"{m['sender']}: {m['text']}\n")
        return base + '.txt', buf.getvalue().encode('utf-8'), 'text/plain'

    if fmt == 'md':
        buf = io.StringIO()
        buf.write(f"# 企业微信聊天记录 · {tag}\n\n")
        buf.write(f"> 导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}　"
                  f"共 {len(msgs)} 条消息\n\n")
        cur_c = None
        for m in msgs:
            if m['conversation'] != cur_c:
                cur_c = m['conversation']
                buf.write(f"\n## {cur_c}\n\n")
            who = '**我**' if m['me'] else f"**{m['sender']}**"
            buf.write(f"- `{m['time']}` {who}：{m['text']}\n")
        return base + '.md', buf.getvalue().encode('utf-8'), 'text/markdown'

    if fmt == 'csv':
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['时间', '会话', '发送者', '是否本人', '类型', '内容', '附件数'])
        for m in msgs:
            w.writerow([m['time'], m['conversation'], m['sender'],
                        '是' if m['me'] else '否',
                        CTYPE.get(m['content_type'], str(m['content_type'])),
                        m['text'], len(m.get('media') or [])])
        return base + '.csv', buf.getvalue().encode('utf-8-sig'), 'text/csv'

    if fmt == 'html':
        esc = lambda s: (str(s).replace('&', '&amp;').replace('<', '&lt;')
                         .replace('>', '&gt;'))
        rows = []
        cur_c = None
        for m in msgs:
            if m['conversation'] != cur_c:
                cur_c = m['conversation']
                rows.append(f'<h2>{esc(cur_c)}</h2>')
            cls = 'me' if m['me'] else ''
            rows.append(
                f'<div class="r {cls}"><div class="h">{esc(m["time"])} · '
                f'{esc(m["sender"])}</div><div class="b">{esc(m["text"])}</div></div>')
        html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>企业微信聊天记录 · {esc(tag)}</title><style>
body{{font:14px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
max-width:900px;margin:0 auto;padding:24px;background:#f6f7f9}}
h1{{font-size:20px}} h2{{font-size:15px;color:#07c160;margin-top:28px;
border-bottom:1px solid #e5e7eb;padding-bottom:6px}}
.r{{margin:8px 0}} .r .h{{font-size:11px;color:#6b7280}}
.r .b{{display:inline-block;background:#fff;border:1px solid #e5e7eb;
border-radius:8px;padding:7px 11px;white-space:pre-wrap;max-width:78%}}
.r.me{{text-align:right}} .r.me .b{{background:#95ec69;border-color:#86d95c}}
</style></head><body><h1>企业微信聊天记录 · {esc(tag)}</h1>
<p>导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}　共 {len(msgs)} 条消息</p>
{''.join(rows)}</body></html>"""
        return base + '.html', html.encode('utf-8'), 'text/html'

    raise ValueError('unsupported format')


# --------------------------------------------------------------------------
# 请求处理
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'WeFlowCorp/1.0'

    def log_message(self, fmt, *args):    # 静音默认日志
        pass

    # ---- 基础响应 ----
    def _send(self, code, body: bytes, ctype='application/json; charset=utf-8',
              extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json_bytes(obj))

    def _err(self, code, msg):
        self._json({'error': msg}, code)

    # ---- 路由 ----
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        qs = urllib.parse.parse_qs(u.query)
        try:
            if path.startswith('/api/'):
                return self.api_get(path, qs)
            if path == '/events':
                return self.sse()
            if path.startswith('/media/'):
                return self.serve_media(path[len('/media/'):])
            if path.startswith('/export/'):
                return self.serve_export(path[len('/export/'):])
            return self.serve_static(path)
        except BrokenPipeError:
            pass
        except Exception as e:      # noqa: BLE001
            log('ERR', path, type(e).__name__, e)
            try:
                self._err(500, f'{type(e).__name__}: {e}')
            except Exception:
                pass

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b'{}'
        try:
            body = json.loads(raw.decode('utf-8') or '{}')
        except Exception:
            body = {}
        try:
            if u.path == '/api/sync/now':
                th = threading.Thread(target=ENGINE.run_once, daemon=True)
                th.start()
                return self._json({'ok': True, 'msg': '已触发同步'})
            if u.path == '/api/engine':
                return self._engine_config(body)
            return self._err(404, 'not found')
        except Exception as e:      # noqa: BLE001
            return self._err(500, f'{type(e).__name__}: {e}')

    # ---- API ----
    def api_get(self, path, qs):
        g = lambda k, d=None: (qs.get(k) or [d])[0]      # noqa: E731

        if path == '/api/info':
            return self._json(ENGINE.info())

        if path == '/api/messages':
            msgs = ENGINE.snapshot_messages()
            conv = g('conv')
            if conv:
                msgs = [m for m in msgs if m['conversation'] == conv]
            sender = g('sender')
            if sender:
                msgs = [m for m in msgs if m['sender'] == sender]
            kw = (g('q') or '').strip().lower()
            if kw:
                msgs = [m for m in msgs if kw in m['text'].lower()
                        or kw in m['sender'].lower()
                        or kw in m['conversation'].lower()
                        or any(kw in (x.get('n') or '').lower()
                               for x in (m.get('media') or []))]
            only_media = g('media')
            if only_media in ('1', 'true'):
                msgs = [m for m in msgs if m.get('media')]
            order = g('order', 'asc')
            if order == 'desc':
                msgs = list(reversed(msgs))
            total = len(msgs)
            try:
                offset = int(g('offset', 0))
                limit = int(g('limit', 0)) or total
            except Exception:
                offset, limit = 0, total
            page = msgs[offset:offset + limit] if limit else msgs
            return self._json({
                'total': total, 'offset': offset, 'limit': limit,
                'count': len(page),
                'messages': [strip_internal(m) for m in page],
            })

        if path == '/api/conversations':
            msgs = ENGINE.snapshot_messages()
            d = {}
            for m in msgs:
                e = d.setdefault(m['conversation'], {
                    'name': m['conversation'], 'messages': 0, 'media': 0,
                    'people': set(), 'last': None, 'last_time': None,
                })
                e['messages'] += 1
                e['media'] += len(m.get('media') or [])
                e['people'].add(m['sender'])
                if e['last'] is None or (m['send_time'] or 0) > (e['last'] or 0):
                    e['last'] = m['send_time']
                    e['last_time'] = m['time']
            out = []
            for e in d.values():
                e['people'] = len(e['people'])
                out.append(e)
            out.sort(key=lambda x: -(x['last'] or 0))
            return self._json({'total': len(out), 'conversations': out})

        if path == '/api/media':
            media = ENGINE.snapshot_media()
            kw = (g('q') or '').strip().lower()
            if kw:
                media = [m for m in media
                         if kw in str(m.get('文件名', '')).lower()
                         or kw in str(m.get('会话', '')).lower()
                         or kw in str(m.get('发送者', '')).lower()]
            kind = g('kind')
            if kind:
                media = [m for m in media if m.get('类型') == kind]
            conv = g('conv')
            if conv:
                media = [m for m in media if m.get('会话') == conv]
            try:
                offset = int(g('offset', 0))
                limit = int(g('limit', 0)) or len(media)
            except Exception:
                offset, limit = 0, len(media)
            out = [{
                'name': m.get('文件名', ''), 'kind': m.get('类型', ''),
                'conv': m.get('会话', ''), 'sender': m.get('发送者', ''),
                'time': m.get('时间', ''), 'size': m.get('大小', 0),
                'path': m.get('rel', ''), 'md5': m.get('MD5', ''),
                'linked': m.get('关联消息', ''),
            } for m in media[offset:offset + limit]]
            return self._json({'total': len(media), 'count': len(out),
                               'media': out})

        if path == '/api/stats':
            if ENGINE.stats_cache is None:
                ENGINE.stats_cache = compute_stats(
                    ENGINE.snapshot_messages(), ENGINE.snapshot_media())
            return self._json(ENGINE.stats_cache)

        if path == '/api/revoked':
            return self._json({'revoked': collect_revoked()})

        # ---- 公告 / 应用 / 通讯录 / 群成员 ----
        if path == '/api/content':
            # 全量概览（含计数）
            try:
                c = ENGINE.get_content(force=(g('refresh') in ('1', 'true')))
            except Exception as e:      # noqa: BLE001
                return self._err(500, f'{type(e).__name__}: {e}')
            return self._json({
                'counts': c['counts'],
                'elapsed': c['elapsed'],
                'corps': c['corps'],
                'departments': c['departments'],
            })

        if path == '/api/notices':
            c = ENGINE.get_content()
            items = c['notices']
            kw = (g('q') or '').strip().lower()
            if kw:
                items = [x for x in items
                         if kw in x['text'].lower()
                         or kw in x['conversation'].lower()
                         or kw in x['sender'].lower()
                         or kw in x['kind'].lower()]
            kind = g('kind')
            if kind:
                items = [x for x in items if x['kind'] == kind]
            conv = g('conv')
            if conv:
                items = [x for x in items if x['conversation'] == conv]
            return self._json({'total': len(items), 'notices': items})

        if path == '/api/apps':
            c = ENGINE.get_content()
            items = c['apps']
            kw = (g('q') or '').strip().lower()
            if kw:
                items = [x for x in items
                         if kw in (x['name'] or '').lower()
                         or kw in (x['desc'] or '').lower()]
            try:
                offset = int(g('offset', 0))
                limit = int(g('limit', 0)) or len(items)
            except Exception:
                offset, limit = 0, len(items)
            return self._json({'total': len(items),
                               'apps': items[offset:offset + limit]})

        if path == '/api/directory':
            c = ENGINE.get_content()
            items = c['members']
            kw = (g('q') or '').strip().lower()
            if kw:
                items = [x for x in items
                         if kw in (x['name'] or '').lower()
                         or kw in (x['english_name'] or '').lower()
                         or kw in (x['mobile'] or '').lower()
                         or kw in (x['email'] or '').lower()
                         or kw in (x['position'] or '').lower()
                         or kw in (x['user_id'] or '').lower()]
            try:
                offset = int(g('offset', 0))
                limit = int(g('limit', 0)) or len(items)
            except Exception:
                offset, limit = 0, len(items)
            return self._json({
                'total': len(items), 'offset': offset, 'limit': limit,
                'count': len(items[offset:offset + limit]),
                'departments': c['departments'],
                'corps': c['corps'],
                'members': items[offset:offset + limit],
            })

        if path == '/api/group-members':
            c = ENGINE.get_content()
            conv = g('conv')
            by = c['group_members']
            if conv:
                return self._json({'conversation': conv,
                                   'total': len(by.get(conv, [])),
                                   'members': by.get(conv, [])})
            return self._json({
                'total': len(by),
                'conversations': [{'name': k, 'count': len(v)}
                                  for k, v in sorted(by.items(),
                                                     key=lambda x: -len(x[1]))],
            })

        if path == '/api/export':
            fmt = (g('format') or 'json').lower()
            conv = g('conv')
            try:
                name, data, ctype = do_export(fmt, conv)
            except ValueError:
                return self._err(400, 'format 仅支持 json/md/txt/csv/html')
            return self._send(200, data, ctype, {
                'Content-Disposition':
                    f"attachment; filename*=UTF-8''{urllib.parse.quote(name)}"})

        if path == '/api/search':
            kw = (g('q') or '').strip().lower()
            if not kw:
                return self._json({'query': '', 'count': 0, 'results': []})
            out = []
            for m in ENGINE.snapshot_messages():
                pos = m['text'].lower().find(kw)
                if pos < 0:
                    continue
                s = max(0, pos - 60)
                out.append({
                    'conversation': m['conversation'], 'sender': m['sender'],
                    'time': m['time'],
                    'excerpt': m['text'][s:s + 200],
                    'match': m['text'][pos:pos + len(kw)],
                })
                if len(out) >= 200:
                    break
            return self._json({'query': kw, 'count': len(out), 'results': out})

        return self._err(404, 'not found')

    def _engine_config(self, body):
        if 'interval' in body:
            try:
                v = max(3, int(body['interval']))
                ENGINE.interval = v
            except Exception:
                pass
        if body.get('action') == 'stop':
            ENGINE.stop()
        elif body.get('action') == 'start':
            ENGINE.start()
        elif body.get('action') == 'once':
            threading.Thread(target=ENGINE.run_once, daemon=True).start()
        return self._json(ENGINE.info())

    # ---- SSE ----
    def sse(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()

        q: queue.Queue = queue.Queue(maxsize=500)

        def push(ev):
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass

        ENGINE.subscribe(push)
        try:
            self._write_sse({'type': 'hello', 'ts': time.strftime('%H:%M:%S'),
                             'info': ENGINE.info()})
            last = time.time()
            while True:
                try:
                    ev = q.get(timeout=1.0)
                    self._write_sse(ev)
                except queue.Empty:
                    if time.time() - last > 20:
                        self.wfile.write(b': ping\n\n')
                        self.wfile.flush()
                        last = time.time()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
                OSError):
            pass
        finally:
            ENGINE.unsubscribe(push)
        return

    def _write_sse(self, ev):
        payload = json.dumps(ev, ensure_ascii=False)
        self.wfile.write(f"data: {payload}\n\n".encode('utf-8'))
        self.wfile.flush()

    # ---- 静态 / 媒体 ----
    def serve_static(self, path):
        if path in ('/', '/index.html'):
            rel = 'index.html'
        else:
            rel = path.lstrip('/')
        p = safe_join(STATIC, rel)
        if not p or not os.path.isfile(p):
            return self._send(404, b'not found', 'text/plain; charset=utf-8')
        ctype = mimetypes.guess_type(p)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype in (
                'application/javascript', 'application/json'):
            ctype += '; charset=utf-8'
        with open(p, 'rb') as f:
            body = f.read()
        self._send(200, body, ctype)

    def serve_media(self, rel):
        p = safe_join(MEDIA_ROOT, rel)
        if not p or not os.path.isfile(p):
            return self._send(404, b'not found', 'text/plain; charset=utf-8')
        return self._send_file(p)

    def serve_export(self, rel):
        p = safe_join(MEDIA_ROOT, rel)
        if not p or not os.path.isfile(p):
            return self._send(404, b'not found', 'text/plain; charset=utf-8')
        return self._send_file(p, download=True)

    def _send_file(self, p, download=False):
        size = os.path.getsize(p)
        ctype = mimetypes.guess_type(p)[0] or 'application/octet-stream'
        rng = self.headers.get('Range')
        start, end = 0, size - 1
        code = 200
        if rng and rng.startswith('bytes='):
            try:
                s = rng[6:].split(',')[0]
                a, _, b = s.partition('-')
                if a:
                    start = int(a)
                if b:
                    end = min(int(b), size - 1)
                code = 206
            except Exception:
                start, end, code = 0, size - 1, 200
        length = end - start + 1
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(length))
        self.send_header('Accept-Ranges', 'bytes')
        if code == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        if download:
            self.send_header(
                'Content-Disposition',
                f"attachment; filename*=UTF-8''"
                f"{urllib.parse.quote(os.path.basename(p))}")
        self.end_headers()
        with open(p, 'rb') as f:
            f.seek(start)
            left = length
            while left > 0:
                chunk = f.read(min(262144, left))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except Exception:
                    return
                left -= len(chunk)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    global ENGINE, ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=5032)
    ap.add_argument('--interval', type=int, default=30)
    ap.add_argument('--no-sync', action='store_true')
    ap.add_argument('--once', action='store_true', help='先跑一轮同步再启动服务')
    ARGS = ap.parse_args()

    ENGINE = SyncEngine(interval=ARGS.interval, logger=log)

    if ARGS.once or not ARGS.no_sync:
        log('执行首轮同步…')
        try:
            ENGINE.run_once()
        except Exception as e:      # noqa: BLE001
            log('首轮同步失败:', e)

    if not ARGS.no_sync:
        ENGINE.start()
        log(f'自动同步已启动，间隔 {ARGS.interval}s')

    srv = Server((ARGS.host, ARGS.port), Handler)
    url = f'http://{ARGS.host}:{ARGS.port}/'
    log('=' * 58)
    log(f'  WeFlow-Corp 已就绪  ->  {url}')
    log(f'  消息 {len(ENGINE.messages)} 条 · 媒体 {len(ENGINE.media)} 个')
    log('  Ctrl+C 停止')
    log('=' * 58)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log('已停止')
        ENGINE.stop()
        srv.shutdown()


if __name__ == '__main__':
    main()
