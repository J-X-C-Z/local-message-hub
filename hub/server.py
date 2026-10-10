"""Loopback-only WebUI and local provider orchestration."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit, unquote, quote
import xml.etree.ElementTree as ET

from .store import ChatStore, PLATFORMS, parse_bound
from .exports import render_export

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local'
NATIVE_PLATFORMS = ('wechat', 'wecom', 'qq')


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default if default is not None else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def message_body(content, raw_type):
    text = str(content or '')
    kind = {3: 'image', 34: 'voice', 43: 'video', 49: 'link', 10000: 'system',
            10002: 'system', 47: 'image'}.get(raw_type, 'text')
    filename = ''
    # Group messages can prepend a sender ID before the structured payload.
    structured = text[text.find('<'):].strip() if '<' in text else ''
    if structured and kind != 'text':
        try:
            node = ET.fromstring(structured)
            app = node.find('.//appmsg')
            if app is not None:
                if app.findtext('type') == '6':
                    kind = 'file'
                filename = app.findtext('title') or ''
                text = '\n'.join(v for v in (app.findtext('title'), app.findtext('des')) if v)
            else:
                text = node.findtext('.//replacemsg') or node.findtext('.//content') or ''
        except ET.ParseError:
            if kind != 'system':
                text = ''
    return kind, text, filename


def qq_login_result(result):
    payload = result.get('qr_url')
    result = {key: result.get(key) for key in ('started', 'login_phase')}
    result['message'] = 'QQ 正在连接本机记录服务'
    if payload:
        parsed = urlsplit(payload)
        if (parsed.scheme != 'https' or not parsed.hostname or
                not (parsed.hostname == 'qq.com' or parsed.hostname.endswith('.qq.com')) or
                len(payload) > 4096):
            raise ValueError('QQ 登录二维码格式无效')
        import qrcode
        from qrcode.image.pure import PyPNGImage
        image = qrcode.make(payload, image_factory=PyPNGImage, box_size=6, border=4)
        output = io.BytesIO()
        image.save(output)
        result['qr_image'] = 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')
    return result


class Hub:
    def __init__(self, storage=LOCAL):
        self.storage = Path(storage)
        self.storage.mkdir(parents=True, exist_ok=True)
        self.store = ChatStore(self.storage / 'hub.sqlite3')
        self.backup_dir = self.storage / 'backups'
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.import_dir = self.storage / 'imports'
        self.import_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.storage / 'settings.json'
        self.settings = {'auto_sync': True, 'sync_interval': 5, 'mcp_write_enabled': False,
                         **read_json(self.settings_path)}
        self.lock = threading.RLock()
        self.providers = {}
        self.media_paths = {}
        self.jobs = set()
        self.attempts = {}
        self.qq_history_job = None
        self.import_cancellations = {}
        self.stop = threading.Event()
        self.sources = {p: {'id': p, 'state': 'disconnected', 'version': v,
                           'last_sync': None, 'error': '', 'progress': ''}
                        for p, v in [('wechat', '4.x / WeFlow 5.0.0'),
                                     ('wecom', '本地快照'), ('qq', '6.3.2')]}
        self._resume_import_jobs()

    def update(self, platform, **values):
        with self.lock:
            self.sources[platform].update(values)

    def source_info(self):
        with self.lock:
            result = [dict(s) for s in self.sources.values()]
        for source in result:
            stats = self.store.stats(source['id'])
            source.update(message_count=stats['message_count'],
                          conversation_count=stats['conversation_count'])
        return {'sources': result}

    def list_backups(self):
        items = []
        for path in self.backup_dir.glob('lmh-backup-*.sqlite3'):
            if path.is_file() and not path.is_symlink():
                stat = path.stat()
                items.append({'id': path.name, 'size': stat.st_size,
                              'created_at': int(stat.st_mtime)})
        return {'items': sorted(items, key=lambda item: item['created_at'], reverse=True)}

    def create_backup(self):
        name = time.strftime('lmh-backup-%Y%m%dT%H%M%SZ-', time.gmtime()) + secrets.token_hex(4) + '.sqlite3'
        path = self.backup_dir / name
        self.store.backup_to(path)
        stat = path.stat()
        return {'id': name, 'size': stat.st_size, 'created_at': int(stat.st_mtime)}

    def delete_local_index(self, confirmation):
        if confirmation != 'DELETE_LOCAL_INDEX':
            raise ValueError('请明确确认清除本地索引')
        with self.lock:
            if self.jobs:
                raise ValueError('本地同步正在运行；请等待任务结束后重试')
            self.settings['auto_sync'] = False
            save_json(self.settings_path, self.settings)
            active_imports = self.store.active_import_jobs()
            if active_imports:
                for job in active_imports:
                    try:
                        self.cancel_import_job(job['id'])
                    except ValueError:
                        pass
                raise ValueError('已请求停止导入任务；等待任务结束后再次确认清理')
            history = self.qq_history_job
            if history and history.get('state') in ('starting', 'running', 'cancel_requested'):
                try:
                    self.cancel_qq_history_import()
                except ValueError:
                    pass
                raise ValueError('已请求停止 QQ 漫游任务；等待任务结束后再次确认清理')

            database = self.storage / 'hub.sqlite3'
            owned_files = [database, Path(str(database) + '-wal'), Path(str(database) + '-shm')]
            owned_dirs = [self.backup_dir, self.import_dir, self.storage / 'qq-roaming']
            for path in (*owned_files, *owned_dirs):
                try:
                    path.resolve().relative_to(self.storage.resolve())
                except ValueError as exc:
                    raise ValueError('本地清理目标超出消息中枢存储目录') from exc
                if path.is_symlink():
                    raise ValueError('检测到符号链接；为避免删除外部数据，已停止清理')

            self.store.close()
            deleted = []
            try:
                for path in owned_files:
                    if path.is_file():
                        path.unlink()
                        deleted.append(path.name)
                for directory in owned_dirs:
                    if not directory.exists():
                        continue
                    if directory in (self.backup_dir, self.import_dir):
                        pattern = 'lmh-backup-*.sqlite3' if directory == self.backup_dir else '*.json'
                        for child in directory.glob(pattern):
                            if child.is_file() and not child.is_symlink():
                                child.unlink()
                                deleted.append(child.relative_to(self.storage).as_posix())
                        if directory == self.import_dir:
                            for child in directory.glob('*.tmp'):
                                if child.is_file() and not child.is_symlink():
                                    child.unlink()
                                    deleted.append(child.relative_to(self.storage).as_posix())
                    else:
                        for child in directory.iterdir():
                            if re.fullmatch(r'[A-Za-z0-9_-]{16}', child.name) and child.is_dir() and not child.is_symlink():
                                shutil.rmtree(child)
                                deleted.append(child.relative_to(self.storage).as_posix())
            finally:
                self.store = ChatStore(database)
                for source in self.sources.values():
                    source.update(state='disconnected', last_sync=None, error='', progress='', message_count=0, conversation_count=0)
            return {'deleted': deleted, 'retained': ['平台原始数据库和缓存', '账号/密钥配置', '非消息中枢管理的同目录文件']}

    def backup_path(self, backup_id):
        if not isinstance(backup_id, str) or not re.fullmatch(r'lmh-backup-(?:restored-)?[0-9TZ-]+-[a-f0-9]{8}\.sqlite3', backup_id):
            raise ValueError('备份标识无效')
        path = self.backup_dir / backup_id
        if not path.is_file() or path.is_symlink() or path.resolve().parent != self.backup_dir.resolve():
            raise ValueError('备份文件不存在')
        return path

    def restore_backup(self, path):
        with self.lock:
            if (self.jobs or self.store.active_import_jobs() or
                    (self.qq_history_job and self.qq_history_job.get('state') in ('starting', 'running', 'cancel_requested'))):
                raise ValueError('请等待当前同步或历史导入结束后再恢复')
            rollback = self.backup_dir / self.create_backup()['id']
            try:
                self.store.restore_from(path)
            except Exception:
                try:
                    self.store.restore_from(rollback)
                except Exception as rollback_error:
                    raise RuntimeError('恢复失败，且自动回滚失败；请使用恢复快照 ' + rollback.name) from rollback_error
                raise

    def _resume_import_jobs(self):
        for job in self.store.active_import_jobs():
            stage = self.import_dir / (job['id'] + '.json')
            if not stage.is_file():
                self.store.update_import_job(job['id'], state='failed', error='暂存文件缺失，无法恢复任务', updated_at=int(time.time()))
                continue
            self.store.update_import_job(job['id'], state='queued', updated_at=int(time.time()), error='')
            self._launch_import_job(job['id'])

    def _launch_import_job(self, job_id):
        with self.lock:
            if job_id in self.import_cancellations:
                return
            cancel = threading.Event()
            self.import_cancellations[job_id] = cancel
        threading.Thread(target=self._run_import_job, args=(job_id, cancel),
                         daemon=True, name='import-' + job_id).start()

    def _run_import_job(self, job_id, cancel):
        stage = self.import_dir / (job_id + '.json')
        try:
            job = self.store.import_job(job_id)
            if not job:
                return
            payload = json.loads(stage.read_text(encoding='utf-8'))
            messages = payload['messages']
            conversations = payload['conversations']
            offset = int(job['processed'])
            written_messages = int(job['messages'])
            written_conversations = int(job['conversations'])
            self.store.update_import_job(job_id, state='running', updated_at=int(time.time()), error='')
            while offset < len(messages):
                if cancel.is_set():
                    self.store.update_import_job(job_id, state='cancelled', updated_at=int(time.time()))
                    return
                batch = messages[offset:offset + 500]
                groups = {}
                for message in batch:
                    groups.setdefault((message['platform'], message['account_id']), []).append(message)
                for (platform, account), group in groups.items():
                    conv_ids = {item['conversation_id'] for item in group}
                    convs = [item for item in conversations if item['id'] in conv_ids]
                    report = self.store.ingest(platform, account, convs, group)
                    written_messages += report['messages']
                    written_conversations += report['conversations']
                offset += len(batch)
                self.store.update_import_job(job_id, state='running', processed=offset,
                                             messages=written_messages,
                                             conversations=written_conversations,
                                             updated_at=int(time.time()))
            if cancel.is_set():
                self.store.update_import_job(job_id, state='cancelled', updated_at=int(time.time()))
                return
            self.store.update_import_job(job_id, state='completed', updated_at=int(time.time()))
            stage.unlink(missing_ok=True)
        except Exception as exc:
            print('Import job failure:', type(exc).__name__, flush=True)
            try:
                self.store.update_import_job(job_id, state='failed', error='导入失败，已保留可恢复暂存数据',
                                             updated_at=int(time.time()))
            except Exception:
                pass
        finally:
            with self.lock:
                self.import_cancellations.pop(job_id, None)

    def cancel_import_job(self, job_id):
        with self.lock:
            job = self.store.import_job(job_id)
            if not job:
                raise ValueError('导入任务不存在')
            if job['state'] not in ('queued', 'running'):
                raise ValueError('此导入任务已结束')
            cancel = self.import_cancellations.get(job_id)
            if cancel:
                cancel.set()
            else:
                self.store.update_import_job(job_id, state='cancelled', updated_at=int(time.time()))
            return self.store.import_job(job_id)

    def start_sync(self, platform='all'):
        if platform not in (*NATIVE_PLATFORMS, 'all'):
            raise ValueError('未知平台')
        for p in NATIVE_PLATFORMS if platform == 'all' else [platform]:
            with self.lock:
                history = self.qq_history_job
                if p in self.jobs or (p == 'qq' and history and history['state'] in ('starting', 'running', 'cancel_requested')):
                    continue
                self.jobs.add(p)
                self.attempts[p] = time.time()
            threading.Thread(target=self.sync, args=(p,), daemon=True, name='sync-' + p).start()

    def sync(self, platform):
        self.update(platform, state='syncing', error='', progress='正在读取本机快照')
        try:
            result = getattr(self, 'sync_' + platform)()
            self.update(platform, state='partial' if result == 'partial' else 'connected',
                        last_sync=int(time.time()), progress='')
        except LoginRequired:
            self.update(platform, state='login_required', progress='', error='请在内置 QQ 页面完成本机登录')
        except Exception as exc:
            # Provider exceptions may contain paths or SQL values; never send them to the UI/log.
            self.update(platform, state='error', progress='', error={
                'wechat': '本机微信读取失败，请保持微信登录并检查数据库目录',
                'wecom': '企业微信快照读取失败，请检查本机目录和密钥',
                'qq': '内置 QQ 服务尚未就绪，请点击登录启动后再同步'
            }[platform])
            print('Source failure:', platform, type(exc).__name__, flush=True)
        finally:
            with self.lock:
                self.jobs.discard(platform)

    def sync_wechat(self):
        from integrations.wechat.provider import WeChatProvider
        provider = WeChatProvider()
        with self.lock:
            self.providers['wechat'] = provider
        account = str(provider.reader.wxid or provider.reader.account or 'local')
        contacts = provider.contacts()
        names = {c['id']: c['name'] for c in contacts}
        self.store.ingest('wechat', account, [], [], contacts)
        sessions = provider.sessions()
        for index, session in enumerate(sessions):
            if self.stop.is_set():
                return
            self.update('wechat', state='indexing', progress=f'{index + 1} / {len(sessions)} 个会话')
            cid = session['id']
            conv = dict(session, account_id=account)
            total = session.get('message_count') or provider.message_count(cid)
            if callable(getattr(provider, 'messages_after', None)):
                checkpoint = self.store.sync_checkpoint('wechat', account, cid)
                cursor = checkpoint.get('cursor') if checkpoint else None
                while not self.stop.is_set():
                    page = provider.messages_after(cid, cursor=cursor, limit=2000)
                    rows = page.get('messages', [])
                    next_cursor = page.get('next_cursor')
                    complete = not page.get('has_more', False)
                    messages = []
                    for row in rows:
                        raw_type = int(row.get('local_type') or 1) & 0xffffffff
                        kind, body, filename = message_body(row.get('content'), raw_type)
                        messages.append({**row, 'conversation_id': cid, 'type': kind, 'text': body,
                                         'sender_name': names.get(row['sender_id'], row['sender_id']),
                                         'file_name': filename, 'raw_type': str(raw_type)})
                    if not rows and not complete and next_cursor == cursor:
                        raise RuntimeError('WeChat source cursor did not advance')
                    self.store.ingest('wechat', account, [conv], messages, checkpoint={
                        'conversation_id': cid, 'next_offset': 0, 'cursor': next_cursor,
                        'complete': complete})
                    cursor = next_cursor
                    if complete:
                        break
                continue
            # Offset checkpoints resume interrupted scans only. Completed scans
            # restart at zero because the reader has no stable source cursor.
            checkpoint = self.store.sync_checkpoint('wechat', account, cid)
            offset = (checkpoint['next_offset']
                      if checkpoint and not checkpoint['complete'] else 0)
            if offset >= total:
                offset = 0
            if not total:
                self.store.ingest('wechat', account, [conv], [], checkpoint={
                    'conversation_id': cid, 'next_offset': 0, 'complete': True})
            while offset < total:
                rows = provider.messages(cid, limit=2000, offset=offset)
                if not rows:
                    break
                messages = []
                for row in rows:
                    raw_type = int(row.get('local_type') or 1) & 0xffffffff
                    kind, body, filename = message_body(row.get('content'), raw_type)
                    messages.append({**row, 'conversation_id': cid, 'type': kind, 'text': body,
                                     'sender_name': names.get(row['sender_id'], row['sender_id']),
                                     'file_name': filename, 'raw_type': str(raw_type)})
                next_offset = offset + len(rows)
                complete = next_offset >= total or len(rows) < 2000
                self.store.ingest('wechat', account, [conv], messages, checkpoint={
                    'conversation_id': cid, 'next_offset': next_offset,
                    'complete': complete})
                offset = next_offset
                if complete:
                    break
        if provider.status()['state'] == 'partial':
            self.update('wechat', error='部分数据库未解锁；已显示可读记录')
            return 'partial'

    def sync_wecom(self):
        from integrations.wecom.adapter import WeComAdapter
        provider = WeComAdapter()
        provider.refresh()
        with self.lock:
            self.providers['wecom'] = provider
        account = 'local'
        convs = [{'id': c['conversation_id'], 'title': c['name'],
                  'kind': 'group' if c['participants'] > 2 else 'direct',
                  'last_timestamp': c['last_time']} for c in provider.list_conversations(100000)]
        self.store.ingest('wecom', account, convs, [])
        # WeCom currently exposes only a fully materialized local snapshot; its
        # batch iterator does not provide a resumable source cursor.
        for batch in provider.iter_message_batches(1000):
            messages, contacts = [], {}
            for row in batch:
                raw = int(row.get('content_type') or 0)
                kind = {2: 'image', 4: 'file', 23: 'voice', 36: 'video', 13: 'link',
                        14: 'link', 15: 'link'}.get(raw, 'system' if row.get('kind') == 'sys' else 'text')
                messages.append({'id': str(row['message_id']), 'conversation_id': str(row['conversation_id']),
                                 'sender_id': str(row.get('sender_id') or ''), 'sender_name': row.get('sender') or '',
                                 'timestamp': row.get('send_time'), 'text': row.get('text') or '',
                                 'type': kind, 'is_self': row.get('me', False), 'raw_type': str(raw)})
                sid = str(row.get('sender_id') or '')
                if sid:
                    contacts[sid] = {'id': sid, 'name': row.get('sender') or sid}
            self.store.ingest('wecom', account, [], messages, contacts.values())

    def sync_qq(self):
        from integrations.qq.provider import QQProvider
        provider = QQProvider()
        with self.lock:
            self.providers['qq'] = provider
        status = provider.status()
        if not status.get('available'):
            raise RuntimeError('Bundled QQ service is not ready')
        if not status.get('online'):
            raise LoginRequired()
        account = str(status.get('account_id') or 'local')
        convs = provider.conversations()
        self.store.ingest('qq', account, convs, [], provider.contacts())
        for index, conv in enumerate(convs):
            self.update('qq', state='indexing', progress=f'{index + 1} / {len(convs)} 个会话')
            cid = str(conv.get('id') or conv.get('conversation_id') or '')
            # QQ exposes offset pages only; a completed scan must be refreshed
            # from zero on the next sync rather than treated as incremental.
            checkpoint = self.store.sync_checkpoint('qq', account, cid)
            offset = (checkpoint['next_offset']
                      if checkpoint and not checkpoint['complete'] else 0)
            while not self.stop.is_set():
                page = provider.messages(conv, limit=500, offset=offset)
                rows = page.get('items', []) if isinstance(page, dict) else page
                if not rows:
                    self.store.ingest('qq', account, [conv], [], checkpoint={
                        'conversation_id': cid, 'next_offset': offset, 'complete': True})
                    break
                next_offset = offset + len(rows)
                complete = ((isinstance(page, dict) and not page.get('has_more', False))
                            or len(rows) < 500)
                self.store.ingest('qq', account, [conv], rows, checkpoint={
                    'conversation_id': cid, 'next_offset': next_offset,
                    'complete': complete})
                offset = next_offset
                if complete:
                    break

    def start_qq_history_import(self, values):
        conversation_id = str(values.get('conversation_id') or '').strip()
        account_id = str(values.get('account_id') or '').strip()
        start = str(values.get('start') or '').strip()
        end = str(values.get('end') or '').strip()
        try:
            start_date = date.fromisoformat(start)
            end_date = date.fromisoformat(end)
        except ValueError:
            raise ValueError('请选择有效的开始和结束日期') from None
        if end_date < start_date or (end_date - start_date).days + 1 > 1461:
            raise ValueError('QQ 漫游历史范围最多 1461 天，且结束日期不能早于开始日期')
        try:
            max_messages = int(values.get('max_messages', 50000))
            max_queries = int(values.get('max_queries', 50000))
        except (TypeError, ValueError):
            raise ValueError('消息数和查询预算必须是整数') from None
        if not 1 <= max_messages <= 100000 or not 1 <= max_queries <= 100000:
            raise ValueError('消息数和查询预算必须在 1 到 100000 之间')
        if not conversation_id or not account_id:
            raise ValueError('请先选择一个 QQ 私聊会话')
        if conversation_id.isdecimal():
            raise ValueError('此会话缺少漫游查询所需的 QQ peer UID')
        with self.lock:
            if 'qq' in self.jobs:
                raise ValueError('QQ 正在同步，请稍后再导入历史消息')
            if self.qq_history_job and self.qq_history_job['state'] in ('starting', 'running', 'cancel_requested'):
                raise ValueError('已有 QQ 历史导入任务正在运行')
            job_id = secrets.token_urlsafe(12)
            job = {'id': job_id, 'state': 'starting', 'error': '', 'progress': '准备漫游历史任务',
                   'message_count': 0, 'partial': False, 'scan': {}, 'qce_task_id': '',
                   'cancel': threading.Event(), 'conversation_id': conversation_id,
                   'account_id': account_id, 'start': start, 'end': end}
            self.qq_history_job = job
            self.attempts['qq'] = time.time()
        threading.Thread(target=self._run_qq_history_import,
                         args=(job, start_date, end_date, max_messages, max_queries),
                         daemon=True, name='qq-roaming-' + job_id).start()
        return self._qq_history_snapshot(job)

    @staticmethod
    def _qq_history_snapshot(job):
        return {key: job[key] for key in ('id', 'state', 'error', 'progress', 'message_count',
                                          'partial', 'scan', 'start', 'end')}

    def qq_history_status(self):
        with self.lock:
            return self._qq_history_snapshot(self.qq_history_job) if self.qq_history_job else {'state': 'idle'}

    def cancel_qq_history_import(self):
        with self.lock:
            job = self.qq_history_job
            if not job or job['state'] not in ('starting', 'running', 'cancel_requested'):
                raise ValueError('没有正在运行的 QQ 历史导入任务')
            job['state'] = 'cancel_requested'
            job['cancel'].set()
            provider = self.providers.get('qq')
            qce_task_id = job['qce_task_id']
        if provider and qce_task_id:
            try:
                provider.cancel_roaming_task(qce_task_id)
            except Exception:
                pass
        return {'message': '已请求取消 QQ 历史导入'}

    def _run_qq_history_import(self, job, start_date, end_date, max_messages, max_queries):
        from integrations.qq.provider import QQProvider
        provider = QQProvider()
        with self.lock:
            self.providers['qq'] = provider
        try:
            status = provider.status()
            if not status.get('online'):
                raise RuntimeError('请先完成 QQ 登录')
            if str(status.get('account_id') or 'local') != job['account_id']:
                raise RuntimeError('QQ 登录账号已变化，请刷新会话列表后重试')
            if job['cancel'].is_set():
                with self.lock:
                    job['state'] = 'cancelled'
                    job['progress'] = '已停止；未启动漫游扫描'
                return
            conv = next((item for item in provider.conversations()
                         if str(item.get('id') or '') == job['conversation_id']), None)
            if not conv or conv.get('kind') == 'group':
                raise RuntimeError('QQ 漫游历史目前仅支持列表中已有的私聊会话')
            conv['title'] = conv.get('title') or job['conversation_id']
            output_dir = (self.storage / 'qq-roaming' / job['id']).resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            start_time = parse_bound(job['start'])
            end_time = parse_bound(job['end'], end=True)
            task = provider.start_roaming_export(conv, start_time, end_time, output_dir,
                                                  max_messages, max_queries)
            with self.lock:
                job['qce_task_id'] = task['taskId']
                if job['cancel'].is_set():
                    provider.cancel_roaming_task(task['taskId'])
                else:
                    job['state'] = 'running'
                    job['progress'] = 'QQ 正在扫描并导出漫游历史'
            while not job['cancel'].is_set():
                task = provider.roaming_task(job['qce_task_id'])
                scan = task.get('roamingScan') or {}
                progress = task.get('message') or task.get('status') or 'QQ 正在扫描漫游历史'
                if scan:
                    progress = f"扫描 {scan.get('scannedDays', 0)} 天 · {scan.get('messageCount', 0)} 条消息"
                with self.lock:
                    job['scan'] = scan
                    job['progress'] = str(progress)[:180]
                if task.get('status') in ('completed', 'failed', 'cancelled'):
                    break
                time.sleep(1)
            if job['cancel'].is_set():
                with self.lock:
                    job['state'] = 'cancelled'
                    job['progress'] = '已停止；已导入的记录仍保留在本机'
                return
            if task.get('status') != 'completed':
                error = task.get('error') or {}
                code = error.get('code') if isinstance(error, dict) else ''
                raise RuntimeError(f"QQ 漫游历史任务失败{f' ({code})' if code else ''}")
            if task.get('roamingScan'):
                job['scan'] = task['roamingScan']
            export_path = Path(task.get('filePath') or '').resolve()
            if not export_path.is_relative_to(output_dir) or not export_path.is_file():
                raise RuntimeError('QQ 漫游导出文件无效')
            if export_path.stat().st_size > 300 * 1024 * 1024:
                raise RuntimeError('QQ 漫游导出文件超过本机导入大小限制')
            document = json.loads(export_path.read_text(encoding='utf-8-sig'))
            rows = document.get('messages') if isinstance(document, dict) else None
            if not isinstance(rows, list):
                raise RuntimeError('QQ 漫游导出内容格式不兼容')
            account = job['account_id']
            imported = 0
            batch = []
            output_root = output_dir.resolve()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if job['cancel'].is_set():
                    break
                sender = row.get('sender') if isinstance(row.get('sender'), dict) else {}
                content = row.get('content') if isinstance(row.get('content'), dict) else {}
                content_text = content.get('text') or ''
                resources = []
                for resource in content.get('resources', []) if isinstance(content.get('resources'), list) else []:
                    if not isinstance(resource, dict):
                        continue
                    raw_path = resource.get('localPath') or resource.get('local_path') or ''
                    candidate = None
                    if raw_path:
                        raw_candidate = Path(raw_path)
                        candidate = raw_candidate.resolve() if raw_candidate.is_absolute() else (output_root / raw_candidate).resolve()
                        if not candidate.is_relative_to(output_root) or not candidate.is_file():
                            candidate = None
                    resource_name = str(resource.get('filename') or (candidate.name if candidate else '附件'))
                    resources.append({'type': str(resource.get('type') or 'file'),
                                      'name': resource_name,
                                      'path': candidate.relative_to(self.storage.resolve()).as_posix() if candidate else '',
                                      'available': candidate is not None,
                                      'size': resource.get('size'),
                                      'width': resource.get('width'),
                                      'height': resource.get('height'),
                                      'duration': resource.get('duration')})
                kind = 'system' if row.get('system') else 'text'
                elements = content.get('elements') if isinstance(content.get('elements'), list) else []
                element_types = {str(item.get('type') or '').lower() for item in elements if isinstance(item, dict)}
                resource_types = {item['type'].lower() for item in resources}
                types = element_types | resource_types
                if 'image' in types:
                    kind = 'image'
                elif 'video' in types:
                    kind = 'video'
                elif types & {'audio', 'voice'}:
                    kind = 'voice'
                elif 'file' in types:
                    kind = 'file'
                elif 'link' in types:
                    kind = 'link'
                elif not row.get('system'):
                    kind = 'text'
                stamp = row.get('timestamp') or 0
                try:
                    stamp = int(stamp)
                    if abs(stamp) >= 100_000_000_000:
                        stamp //= 1000
                except (TypeError, ValueError):
                    stamp = 0
                sender_uid = str(sender.get('uid') or '')
                sender_uin = str(sender.get('uin') or '')
                sender_id = sender_uid or sender_uin
                seq = str(row.get('seq') or '')
                message_id = str(row.get('id') or (f"{job['conversation_id']}:{seq}" if seq else ''))
                if not message_id:
                    continue
                batch.append({'id': message_id, 'conversation_id': job['conversation_id'],
                              'sender_id': sender_id, 'sender_name': sender.get('name') or sender.get('nickname') or sender_id,
                              'timestamp': stamp, 'type': kind, 'text': str(content_text),
                              'is_self': account in (sender_uid, sender_uin), 'file_name': resources[0]['name'] if resources else '',
                              'raw_type': str(row.get('type') or ''), 'roaming_resources': resources,
                              'roaming_content': {'html': content.get('html') or '',
                                                  'elements': content.get('elements') or [],
                                                  'mentions': content.get('mentions') or []},
                              'roaming_metadata': {'seq': seq, 'time': row.get('time') or '',
                                                   'message_type': row.get('type') or '',
                                                   'recalled': bool(row.get('recalled')),
                                                   'sender': sender}})
                if len(batch) >= 500:
                    self.store.ingest('qq', account, [conv], batch)
                    imported += len(batch)
                    batch.clear()
                    with self.lock:
                        job['message_count'] = imported
            if batch:
                self.store.ingest('qq', account, [conv], batch)
                imported += len(batch)
            partial = bool((job.get('scan') or {}).get('partial'))
            with self.lock:
                job['message_count'] = imported
                job['partial'] = partial
                job['state'] = 'cancelled' if job['cancel'].is_set() else ('partial' if partial else 'completed')
                job['progress'] = ('已停止；已导入的记录仍保留在本机' if job['cancel'].is_set() else
                                   '本次扫描完成，已导入当前可获取记录' if not partial else
                                   '已导入当前可获取记录，结果可能不完整')
        except Exception as exc:
            print('QQ history import failure:', type(exc).__name__, flush=True)
            with self.lock:
                job['state'] = 'failed'
                job['error'] = str(exc) if isinstance(exc, ValueError) else 'QQ 历史导入失败，请检查登录状态和 QCE 任务'
                job['progress'] = ''

    def autosync(self):
        self.start_sync()
        while not self.stop.wait(10):
            now = time.time()
            if self.settings.get('auto_sync', True):
                for p, source in list(self.sources.items()):
                    interval = 30 if p == 'qq' and source['state'] != 'connected' else self.settings.get('sync_interval', 5) * 60
                    if now - self.attempts.get(p, 0) >= interval:
                        # Retry login-dependent sources without interrupting the logged-in client.
                        if p != 'qq' or 'qq' in self.providers:
                            self.start_sync(p)

    def configure(self, platform, values):
        if platform not in NATIVE_PLATFORMS:
            raise ValueError('未知平台')
        if platform == 'wecom':
            from integrations.wecom import configure
            configure(data_dir=values.get('data_dir', ''), key=values.get('key', ''),
                      user_slot_id=values.get('user_slot_id', ''))
        elif platform == 'wechat':
            from integrations.wechat.provider import CONFIG
            config = read_json(CONFIG)
            directory = values.get('data_dir', '').strip()
            if directory:
                path = Path(directory)
                if not path.is_absolute() or not path.is_dir():
                    raise ValueError('请选择存在的本机绝对目录')
                config['data_dir'] = str(path)
            if values.get('key', '').strip():
                raise ValueError('微信使用逐库密钥自动读取，请保持微信登录；不支持单一密钥覆盖')
            save_json(CONFIG, config)
        else:
            if values.get('data_dir', '').strip():
                raise ValueError('QQ 使用随工具提供的内置运行环境，无需数据目录')
        self.start_sync(platform)

    def resolve_media(self, params):
        platform = params.get('platform')
        row = self.store.get_message(platform, params.get('account_id', ''), params.get('message_id', ''))
        if row is None or row['conversation_id'] != params.get('conversation_id'):
            raise ValueError('消息不存在')
        row = {**row.get('extra', {}), **row}
        resource_index = params.get('resource_index')
        if platform == 'qq' and resource_index is not None:
            try:
                index = int(resource_index)
                resources = row.get('roaming_resources') or []
                resource = resources[index] if 0 <= index < len(resources) else None
                media_root = self.storage.resolve()
                path = (media_root / str(resource.get('path') or '')).resolve() if resource else None
                if path is None or not path.is_relative_to(media_root) or not path.is_file():
                    raise ValueError('漫游媒体文件不存在')
            except (TypeError, ValueError, IndexError):
                raise ValueError('漫游媒体文件不存在') from None
            key = secrets.token_urlsafe(32)
            with self.lock:
                self.media_paths[key] = path
            return {'media_url': '/local-media/' + key}
        provider = self.providers.get(platform)
        if provider is None:
            return {'media_url': None, 'message': '请先同步该平台'}
        if platform == 'wechat':
            result = provider.media(row['conversation_id'], int(row['local_id']), row['shard'])
        elif platform == 'wecom':
            result = provider.media(row['id'])
        else:
            result = provider.media(row)
        path = result.get('path')
        if platform == 'qq' and row['type'] == 'voice' and result.get('available') and path and Path(path).is_file():
            with Path(path).open('rb') as audio:
                header = audio.read(12)
            if header.startswith((b'#!SILK', b'\x02#!SILK')):
                from integrations.wechat.voice import voice_media_result
                result = voice_media_result(path)
                path = result.get('path')
        if result.get('playable') is False and row['type'] == 'voice':
            return {'media_url': None, 'message': '此语音暂时无法在本机解码'}
        if not result.get('available') or not path or not Path(path).is_file():
            return {'media_url': None, 'message': '本机未缓存此媒体，或暂不支持此格式'}
        key = secrets.token_urlsafe(32)
        with self.lock:
            self.media_paths[key] = Path(path).resolve()
        return {'media_url': '/local-media/' + key}


class LoginRequired(Exception):
    pass


class Handler(BaseHTTPRequestHandler):
    server_version = 'LocalChatHub/1'

    def log_message(self, *_args):
        pass

    @property
    def hub(self):
        return self.server.hub

    def send_bytes(self, payload, mime, status=200, extra=None):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        if mime.startswith('text/html') and not (extra or {}).get('Content-Disposition'):
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json(self, payload, status=200):
        self.send_bytes(json.dumps(payload, ensure_ascii=False).encode(), 'application/json; charset=utf-8', status)

    def allowed(self):
        host = self.headers.get('Host', '')
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        origin = self.headers.get('Origin')
        return host in allowed and (not origin or origin in {'http://' + h for h in allowed}) and self.headers.get('Sec-Fetch-Site') not in ('cross-site',)

    def do_GET(self):
        if not self.allowed():
            self.json({'error': '仅允许本机同源访问'}, 403)
            return
        parsed = urlsplit(self.path)
        params = {k: v[-1] for k, v in parse_qs(parsed.query).items()}
        try:
            if parsed.path.startswith('/api/'):
                match = re.fullmatch(r'/api/backups/([^/]+)', parsed.path)
                if match:
                    backup = self.hub.backup_path(match.group(1))
                    self.send_file(backup, 'application/vnd.sqlite3',
                                   'attachment; filename="' + backup.name + '"')
                    return
                self.get_api(parsed.path, params)
            else:
                self.static(parsed.path)
        except ValueError as exc:
            self.json({'error': str(exc)}, 400)
        except Exception as exc:
            print('Request failure:', type(exc).__name__, flush=True)
            self.json({'error': '本机服务处理失败，请检查数据源状态'}, 500)

    def get_api(self, path, params):
        query = {k: v for k, v in params.items() if k in ('platform', 'q', 'type', 'start', 'end', 'cursor')}
        query['platform'] = query.get('platform', 'all')
        if query['platform'] not in (*PLATFORMS, 'all'):
            raise ValueError('未知平台')
        paging = {'limit': min(max(int(params.get('limit', 100)), 1), 1000),
                  'offset': max(int(params.get('offset', 0)), 0)}
        store = self.hub.store
        if path == '/api/sources':
            result = self.hub.source_info()
        elif path == '/api/backups':
            result = self.hub.list_backups()
        elif path == '/api/imports':
            result = {'items': self.hub.store.import_jobs()}
        elif re.fullmatch(r'/api/imports/[A-Za-z0-9_-]{8,40}', path):
            result = self.hub.store.import_job(path.rsplit('/', 1)[1])
            if result is None:
                self.json({'error': '导入任务不存在'}, 404)
                return
        elif path == '/api/settings':
            result = {**self.hub.settings, 'storage_dir': str(self.hub.storage),
                      'fts_enabled': self.hub.store._fts_enabled}
        elif path == '/api/mcp':
            root = ROOT.as_posix()
            storage = self.hub.storage.resolve().as_posix()
            python = Path(sys.executable).resolve().as_posix()
            tools = ('search_messages', 'list_conversations', 'list_messages',
                     'get_message_context', 'list_contacts', 'list_sources', 'list_imports')
            write_tools = ('update_conversation_metadata', 'set_contact_identity', 'remove_contact_identity')
            if self.hub.settings.get('mcp_write_enabled') is True:
                tools += write_tools
            config = '\n'.join((
                'mcp_servers:',
                '  local-message-hub:',
                f'    command: {json.dumps(python, ensure_ascii=False)}',
                '    args: ["-m", "hub.mcp_server", "--storage", ' + json.dumps(storage, ensure_ascii=False) + ']',
                f'    cwd: {json.dumps(root, ensure_ascii=False)}',
                '    tools:',
                '      include:',
                *[f'        - {name}' for name in tools],
            ))
            result = {'available': (ROOT / 'hub' / 'mcp_server.py').is_file(),
                      'lifecycle': '客户端按需启动', 'transport': 'stdio',
                      'mode': 'metadata-write' if self.hub.settings.get('mcp_write_enabled') is True else 'read-only',
                      'write_enabled': self.hub.settings.get('mcp_write_enabled') is True,
                      'config_text': config, 'operations': self.hub.store.mcp_operations(20)}
        elif path == '/api/operations':
            result = {'items': store.operation_log(min(int(params.get('limit', 50)), 200))}
        elif path in ('/api/conversations', '/api/contacts'):
            result = getattr(store, path.rsplit('/', 1)[1])(query['platform'], query.get('q', ''), **paging)
        elif path == '/api/messages':
            result = store.messages(account_id=params.get('account_id', ''),
                                    conversation_id=params.get('conversation_id', ''), **query, **paging)
        elif path == '/api/context':
            result = store.message_context(
                params.get('platform', 'all'), params.get('account_id', ''),
                params.get('message_id', ''), params.get('before', 5), params.get('after', 5))
            if result is None:
                self.json({'error': '消息不存在'}, 404)
                return
        elif path == '/api/search':
            result = store.search(**{**query, 'q': query.get('q', '')}, **paging)
        elif path == '/api/media':
            result = store.media(**{k: v for k, v in query.items() if k in ('platform', 'type', 'q')}, **paging)
        elif path == '/api/stats':
            result = store.stats(query['platform'])
            db_path = Path(self.hub.store.path)
            index_bytes = sum(path.stat().st_size for path in
                              (db_path, Path(str(db_path) + '-wal')) if path.is_file())
            result['overview'] = {
                'sources': self.hub.source_info()['sources'],
                'recent_imports': self.hub.store.import_jobs(limit=5),
                'index_bytes': index_bytes,
            }
        elif path == '/api/qq/login-status':
            from integrations.qq.provider import QQProvider
            status = QQProvider().login_status()
            result = {'phase': status.get('loginPhase'), 'online': bool(status.get('coreReady'))}
        elif path == '/api/qq/history-import':
            result = self.hub.qq_history_status()
        elif path == '/api/media/resolve':
            result = self.hub.resolve_media(params)
        elif path == '/api/export':
            query.pop('cursor', None)
            rows, offset = [], 0
            while True:
                if params.get('conversation_id'):
                    page = store.messages(account_id=params.get('account_id', ''),
                                          conversation_id=params['conversation_id'], **query, limit=5000, offset=offset)
                else:
                    page = store.search(**{**query, 'q': query.get('q', '')}, limit=5000, offset=offset)
                rows.extend(page['items'])
                offset += len(page['items'])
                if offset >= page['total'] or not page['items']:
                    break
            rows.sort(key=lambda r: r['timestamp'] or 0)
            data, mime, filename = render_export(rows, params.get('format', 'json'), '本地消息档案')
            self.send_bytes(data, mime, extra={'Content-Disposition': f'attachment; filename="{filename}"'})
            return
        else:
            self.json({'error': '接口不存在'}, 404)
            return
        self.json(result)

    def do_POST(self):
        if not self.allowed():
            self._discard_small_request_body()
            self.json({'error': '仅允许本机同源访问'}, 403)
            return
        path = urlsplit(self.path).path
        if path == '/api/backups/restore':
            self.restore_upload()
            return
        if path in ('/api/imports/preview', '/api/imports'):
            self.import_upload(preview=path.endswith('/preview'))
            return
        match = re.fullmatch(r'/api/imports/([A-Za-z0-9_-]{8,40})/cancel', path)
        if match:
            try:
                self.json(self.hub.cancel_import_job(match.group(1)))
            except ValueError as exc:
                self.json({'error': str(exc)}, 400)
            return
        if not self.headers.get('Content-Type', '').startswith('application/json'):
            self._discard_small_request_body()
            self.json({'error': '仅允许本机同源 JSON 请求'}, 403)
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length < 0 or length > 16384:
                raise ValueError('请求过大')
            body = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('JSON 对象无效')
            if path == '/api/sync':
                self.hub.start_sync(body.get('platform', 'all'))
                result = {'message': '已开始读取本机记录'}
            elif path == '/api/data/delete':
                result = self.hub.delete_local_index(body.get('confirmation'))
            elif path == '/api/conversation-metadata':
                result = self.hub.store.update_conversation_metadata(
                    body.get('platform'), body.get('account_id'), body.get('conversation_id'),
                    note=body.get('note'), tags=body.get('tags'), archived=body.get('archived'))
            elif path == '/api/contact-identities':
                result = self.hub.store.set_contact_identity(
                    body.get('platform'), body.get('account_id'), body.get('contact_id'), body.get('identity_id'))
            elif path == '/api/contact-identities/unlink':
                result = self.hub.store.remove_contact_identity(
                    body.get('platform'), body.get('account_id'), body.get('contact_id'))
            elif path == '/api/settings':
                with self.hub.lock:
                    if 'auto_sync' in body:
                        self.hub.settings['auto_sync'] = bool(body['auto_sync'])
                    if 'mcp_write_enabled' in body:
                        if not isinstance(body['mcp_write_enabled'], bool):
                            raise ValueError('MCP 写入开关无效')
                        self.hub.settings['mcp_write_enabled'] = body['mcp_write_enabled']
                    if 'sync_interval' in body:
                        interval = int(body['sync_interval'])
                        if interval not in (1, 3, 5, 10, 30):
                            raise ValueError('无效同步间隔')
                        self.hub.settings['sync_interval'] = interval
                    save_json(self.hub.settings_path, self.hub.settings)
                    result = {**self.hub.settings, 'storage_dir': str(self.hub.storage)}
            elif path.startswith('/api/source/') and path.endswith('/configure'):
                self.hub.configure(path.split('/')[3], body)
                result = {'message': '配置已保存，正在同步'}
            elif path == '/api/qq/start':
                from integrations.qq.provider import QQProvider
                refresh = body.get('refresh', False)
                if not isinstance(refresh, bool):
                    raise ValueError('二维码刷新参数无效')
                result = qq_login_result(QQProvider().launch(refresh=refresh))
                self.hub.start_sync('qq')
            elif path == '/api/qq/history-import':
                result = self.hub.start_qq_history_import(body)
            elif path == '/api/qq/history-import/cancel':
                result = self.hub.cancel_qq_history_import()
            elif path == '/api/backups':
                result = self.hub.create_backup()
            else:
                self.json({'error': '接口不存在'}, 404)
                return
            self.json(result)
        except (ValueError, TypeError) as exc:
            self.json({'error': str(exc)}, 400)
        except Exception as exc:
            print('Action failure:', type(exc).__name__, flush=True)
            self.json({'error': '本机操作失败，请检查数据源配置'}, 500)

    def _discard_small_request_body(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
        except (TypeError, ValueError):
            self.close_connection = True
            return
        if 0 <= length <= 16384:
            if length:
                self.rfile.read(length)
        else:
            self.close_connection = True

    def restore_upload(self):
        if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/octet-stream':
            self.json({'error': '请上传 SQLite 备份文件'}, 415)
            return
        temp_path = self.hub.backup_dir / ('.restore-' + secrets.token_hex(12) + '.part')
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length <= 0 or length > 8 * 1024 * 1024 * 1024:
                raise ValueError('备份文件为空或超过 8 GiB 限制')
            remaining = length
            with temp_path.open('xb') as output:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError('备份上传不完整')
                    output.write(chunk)
                    remaining -= len(chunk)
            self.hub.restore_backup(temp_path)
            name = time.strftime('lmh-backup-restored-%Y%m%dT%H%M%SZ-', time.gmtime()) + secrets.token_hex(4) + '.sqlite3'
            saved_path = self.hub.backup_dir / name
            os.replace(temp_path, saved_path)
            stat = saved_path.stat()
            self.json({'id': name, 'size': stat.st_size, 'created_at': int(stat.st_mtime)})
        except (ValueError, TypeError, sqlite3.DatabaseError) as exc:
            self.json({'error': str(exc) if isinstance(exc, ValueError) else '备份数据库无效'}, 400)
        except Exception as exc:
            print('Backup restore failure:', type(exc).__name__, flush=True)
            self.json({'error': '无法恢复此数据库备份'}, 500)
        finally:
            temp_path.unlink(missing_ok=True)

    def import_upload(self, preview=False):
        if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/octet-stream':
            self.json({'error': '请上传 JSON 或 CSV 文件'}, 415)
            return
        stage = temporary = None
        job_saved = False
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length <= 0 or length > 256 * 1024 * 1024:
                raise ValueError('导入文件为空或超过 256 MiB 限制')
            payload = self.rfile.read(length)
            if len(payload) != length:
                raise ValueError('文件上传不完整')
            params = parse_qs(urlsplit(self.path).query)
            value = lambda key, default='': params.get(key, [default])[-1]
            from .imports import parse_import
            result = parse_import(payload, value('format'), value('platform', 'all'), value('account'))
            if preview:
                sample = result['messages'][:5]
                self.json({key: result[key] for key in ('total', 'valid', 'invalid', 'errors', 'warnings') if key in result} | {
                    'platforms': sorted({item['platform'] for item in result['messages']}),
                    'accounts': sorted({item['account_id'] for item in result['messages']}),
                    'sample': [{key: item[key] for key in ('platform', 'account_id', 'conversation_id', 'sender_name', 'timestamp', 'type', 'text')} for item in sample],
                })
                return
            job_id = secrets.token_urlsafe(12)
            stage = self.hub.import_dir / (job_id + '.json')
            temporary = stage.with_suffix('.tmp')
            staged = json.dumps({'messages': result['messages'], 'conversations': result['conversations']},
                                ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            temporary.write_bytes(staged)
            os.replace(temporary, stage)
            filename = Path(value('filename') or 'import.' + value('format')).name
            filename = re.sub(r'[^\w.() -]', '_', filename, flags=re.UNICODE)[:120] or 'import'
            job = self.hub.store.create_import_job({
                'id': job_id, 'fingerprint': hashlib.sha256(payload).hexdigest(),
                'filename': filename, 'staging_file': stage.name,
                'created_at': int(time.time()), 'total': result['total'],
                'valid': result['valid'], 'invalid': result['invalid'], 'errors': result['errors'],
            })
            job_saved = True
            self.hub._launch_import_job(job_id)
            self.json(job, 202)
        except (ValueError, TypeError) as exc:
            if temporary:
                temporary.unlink(missing_ok=True)
            if stage and not job_saved:
                stage.unlink(missing_ok=True)
            self.json({'error': str(exc)}, 400)
        except Exception as exc:
            if temporary:
                temporary.unlink(missing_ok=True)
            if stage and not job_saved:
                stage.unlink(missing_ok=True)
            print('Import failure:', type(exc).__name__, flush=True)
            self.json({'error': '导入失败，请检查文件格式'}, 500)

    def send_file(self, path, mime, disposition):
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(path.stat().st_size))
        self.send_header('Content-Disposition', disposition)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        try:
            with path.open('rb') as source:
                while chunk := source.read(1024 * 1024):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def static(self, urlpath):
        if urlpath.startswith('/local-media/'):
            with self.hub.lock:
                path = self.hub.media_paths.get(urlpath.split('/')[-1])
            if path is None:
                self.json({'error': '媒体链接已过期，请重新打开'}, 404)
                return
        elif urlpath == '/vendor/lucide.js':
            path = ROOT / 'webui/node_modules/lucide/dist/umd/lucide.js'
        else:
            base = (ROOT / 'webui').resolve()
            path = (base / unquote(urlpath).lstrip('/')).resolve() if urlpath != '/' else base / 'index.html'
            if not path.is_relative_to(base) or 'node_modules' in path.parts:
                self.json({'error': '路径无效'}, 404)
                return
        if not path.is_file():
            self.json({'error': '文件不存在'}, 404)
            return
        mime = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
        data = path.read_bytes()
        headers = {'Accept-Ranges': 'bytes'}
        safe_inline = {'image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/avif',
                       'audio/wav', 'audio/x-wav', 'audio/mpeg', 'audio/ogg', 'audio/mp4',
                       'audio/aac', 'audio/flac', 'video/mp4', 'video/webm'}
        if urlpath.startswith('/local-media/') and mime not in safe_inline:
            headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(path.name)
        range_header = self.headers.get('Range')
        if range_header and urlpath.startswith('/local-media/') and range_header.startswith('bytes='):
            try:
                start, end = range_header[6:].split('-', 1)
                start = int(start or 0)
                end = min(int(end) if end else len(data) - 1, len(data) - 1)
                if not 0 <= start <= end:
                    raise ValueError()
                self.send_bytes(data[start:end + 1], mime, 206,
                                {**headers, 'Content-Range': f'bytes {start}-{end}/{len(data)}'})
                return
            except ValueError:
                self.send_bytes(b'', mime, 416, {'Content-Range': f'bytes */{len(data)}'})
                return
        self.send_bytes(data, mime, extra=headers)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--storage', default=os.environ.get('LMH_STORAGE_DIR', str(LOCAL)))
    parser.add_argument('--no-autosync', action='store_true')
    args = parser.parse_args()
    hub = Hub(args.storage)
    for port in range(args.port, args.port + 20):
        try:
            server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
            break
        except OSError:
            continue
    else:
        raise RuntimeError('No loopback port available')
    server.hub = hub
    save_json(hub.storage / 'server.json', {'pid': os.getpid(), 'url': f'http://127.0.0.1:{server.server_port}'})
    if not args.no_autosync:
        threading.Thread(target=hub.autosync, daemon=True).start()
    print(f'LocalChatHub http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
