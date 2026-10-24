"""Loopback-only WebUI and local provider orchestration."""
from __future__ import annotations

import argparse
import base64
import hashlib
import heapq
import io
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import sys
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit, unquote, quote
import xml.etree.ElementTree as ET

from .store import ChatStore, PLATFORMS, parse_bound
from .exports import (render_contact_export, render_group_member_export, render_export,
                     message_resource_requests, resolve_chatlab_conversation_type, write_export)
from .moments_exports import MomentsExportTooLarge, render_moments_export_bounded
from .analytics import AnalyticsService
from .capabilities import describe_capabilities, normalize_capabilities, unavailable_reason
from .sending import SendRepository, SendService
from .auto_export import AutoExportManager
from .resources import ResourceJobManager
from .backups import create_media_backup, restore_media_backup
from .account_safety import AccountSafety
from .voice_transcription import VoiceTranscriber
from .streaming_json import iter_export_messages
from integrations.qq.provider import DEFAULT_BUNDLE, QQProvider, QQProviderError
from integrations.qq.sender import NapCatOneBotSender, QQAccountBoundSender
from integrations.qq.send_target import is_uin
from integrations.wecom.bot_credentials import WeComBotCredentials
from integrations.wecom.bot_sender import WeComBotTextSender
from integrations.wecom.bot_media import MAX_MEDIA_SIZE, WeComBotMediaStore
from integrations.wechat.sender import WeChatGUISender, WeChatSendError
from .windows_notifications import show_notification

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local'
NATIVE_PLATFORMS = ('wechat', 'wecom', 'qq')
SYNC_PLATFORMS = (*NATIVE_PLATFORMS, 'email')
MOMENT_MEDIA_TOKEN_TTL_SECONDS = 15 * 60
MEDIA_TOKEN_LIMIT = 4096
MAX_SYNC_CONVERSATION_ID_LENGTH = 512
MCP_MEDIA_MAX_BYTES = 8 * 1024 * 1024


def _avatar_format(payload):
    if payload.startswith(b'\xff\xd8\xff'):
        return '.jpg', 'image/jpeg'
    if payload.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png', 'image/png'
    if payload.startswith((b'GIF87a', b'GIF89a')):
        return '.gif', 'image/gif'
    if payload.startswith(b'RIFF') and payload[8:12] == b'WEBP':
        return '.webp', 'image/webp'
    if len(payload) >= 26 and payload.startswith(b'BM'):
        pixel_offset = int.from_bytes(payload[10:14], 'little')
        header_size = int.from_bytes(payload[14:18], 'little')
        if header_size >= 12 and 14 + header_size <= pixel_offset <= len(payload):
            return '.bmp', 'image/bmp'
    return None


def _read_cached_snapshot(path, limit, limit_error='附件超过 MCP 单次读取的 8 MiB 限制'):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('本机缓存内容不是普通文件')
    signature = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if before.st_size > limit:
        raise ValueError(limit_error)
    with path.open('rb') as source:
        opened = os.fstat(source.fileno())
        if signature(opened) != signature(before):
            raise ValueError('本机缓存内容在读取前发生变化')
        payload = source.read(limit + 1)
        if signature(os.fstat(source.fileno())) != signature(opened):
            raise ValueError('本机缓存内容在读取过程中发生变化')
    if len(payload) > limit:
        raise ValueError(limit_error)
    if len(payload) != opened.st_size:
        raise ValueError('本机缓存内容在读取过程中发生变化')
    return payload


class QQConversationNotFound(ValueError):
    pass


class _ConfiguredWeComBotSender:
    def __init__(self, credentials, sender, media_store):
        self.credentials = credentials
        self.sender = sender
        self.media_store = media_store

    def deliver(self, request):
        attachment = request.get("attachment") or None
        try:
            value = self.credentials.get()
            if value is None:
                raise RuntimeError("wecom_bot_credentials_missing")
            if request.get("account_id") != value["bot_id"]:
                raise RuntimeError("wecom_bot_identity_mismatch")
            if attachment:
                with self.media_store.open_for_send(attachment) as media_payload:
                    return self.sender.deliver({**request, "_bot_id": value["bot_id"],
                                                "_bot_secret": value["secret"],
                                                "_media_payload": media_payload})
            return self.sender.deliver({**request, "_bot_id": value["bot_id"],
                                        "_bot_secret": value["secret"],
                                        "_media_payload": None})
        finally:
            if attachment:
                try:
                    self.media_store.remove(attachment)
                except Exception:
                    pass  # Cleanup must not turn a sent/unknown delivery into a retryable failure.


class _LazyWeChatSender:
    """Defer the WeChat reader until a send is actually attempted.

    The GUI sender only needs the local databases to prove a delivery, so the
    provider is created on first use. That keeps Hub start-up and the other
    delivery platforms working when WeChat is closed or its keys are missing.
    """

    def __init__(self, sender_factory=None):
        self._sender_factory = sender_factory or WeChatGUISender
        self._sender = None
        self._lock = threading.Lock()

    def _resolve(self):
        if self._sender is None:
            self._sender = self._sender_factory()
        return self._sender

    def deliver(self, request):
        with self._lock:
            return self._resolve().deliver(request)

    def readiness(self, capability_enabled=True):
        try:
            with self._lock:
                return self._resolve().readiness(capability_enabled)
        except Exception:
            return {"capability_enabled": bool(capability_enabled), "window_found": False,
                    "window_foreground": False, "input_available": False,
                    "account_id": None, "state": "unavailable"}


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
            10002: 'system', 47: 'image', 48: 'location'}.get(raw_type, 'text')
    if raw_type in (47, 11000):
        return 'image', '[表情]', ''
    if raw_type == 48:
        return 'location', '[位置]', ''
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


def message_quote(content, raw_type):
    if raw_type != 49:
        return None
    source = str(content or '')
    if len(source) > 65536 or '<!DOCTYPE' in source.upper() or '<!ENTITY' in source.upper():
        return None
    start = source.find('<')
    if start < 0:
        return None
    try:
        root = ET.fromstring(source[start:])
    except ET.ParseError:
        return None
    app = root.find('.//appmsg')
    refer = root.find('.//refermsg')
    if app is None or app.findtext('type') != '57' or refer is None:
        return None
    kind = (refer.findtext('type') or '').strip()
    quoted = (refer.findtext('content') or '').strip()
    placeholders = {'3': '[图片]', '34': '[语音]', '43': '[视频]', '47': '[表情]',
                    '48': '[位置]', '42': '[名片]'}
    if kind == '49':
        try:
            nested = ET.fromstring(quoted[quoted.find('<'):]) if '<' in quoted else None
            quoted = (nested.findtext('.//appmsg/title') if nested is not None else '') or '[链接]'
        except ET.ParseError:
            quoted = '[链接]'
    elif kind in placeholders:
        quoted = placeholders[kind]
    quoted = re.sub(r'[\x00-\x1f\x7f]', ' ', quoted)[:512].strip()
    sender = re.sub(r'[\x00-\x1f\x7f]', ' ', refer.findtext('displayname') or '')[:128].strip()
    if not quoted and not sender:
        return None
    result = {'sender': sender, 'content': quoted or '[引用消息]'}
    target_id = (refer.findtext('svrid') or '').strip()
    if re.fullmatch(r'[1-9][0-9]{0,19}', target_id):
        result['target_server_id'] = target_id
    return result


def source_message_id(value):
    value = str(value or '').strip()
    return value if re.fullmatch(r'[1-9][0-9]{0,19}', value) else ''


def message_location(content):
    text = str(content or '')
    if len(text) > 65536 or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        return None
    start = text.find('<')
    if start < 0:
        return None
    try:
        root = ET.fromstring(text[start:])
    except ET.ParseError:
        return None
    node = root if root.tag == 'location' else root.find('.//location')
    if node is None:
        return None

    def label(value):
        return re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or '')).strip()[:512]

    def coordinate(value, minimum, maximum):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and minimum <= number <= maximum else None

    title = label(node.get('poiname'))
    if title in {'[位置]', '[Location]'}:
        title = ''
    has_place = bool(title or label(node.get('label')))
    latitude = coordinate(node.get('x'), -90, 90)
    longitude = coordinate(node.get('y'), -180, 180)
    if not has_place and latitude is None and longitude is None:
        return None
    return {
        'title': title or '位置',
        'address': label(node.get('label')),
        'latitude': latitude,
        'longitude': longitude,
    }


def message_emoji_md5(content):
    text = str(content or '')
    if len(text) > 65536:
        return None
    start = text.find('<')
    if start < 0:
        return None
    try:
        emoji = ET.fromstring(text[start:]).find('.//emoji')
    except ET.ParseError:
        return None
    value = emoji.get('md5', '') if emoji is not None else ''
    return value.lower() if re.fullmatch(r'[a-fA-F0-9]{32}', value) else None


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


def qq_login_status_error_code(error):
    message = str(error)
    if isinstance(error, ValueError):
        return 'local_config_invalid'
    if message == 'NapCat WebUI authentication failed':
        return 'napcat_authentication_failed'
    if message == 'NapCat WebUI configuration is unavailable':
        return 'napcat_config_unavailable'
    if message == 'NapCat WebUI is not configured for authenticated loopback access':
        return 'napcat_loopback_auth_required'
    if message == 'NapCat local API rejected the request':
        return 'napcat_api_rejected'
    if re.fullmatch(r'NapCat local API request failed \(\d{3}\)', message):
        return 'napcat_api_http_error'
    if message.startswith('NapCat WebUI is unavailable:'):
        return 'napcat_unavailable'
    return 'unavailable'


class Hub:
    def __init__(self, storage=LOCAL, store=None, resume_imports=True, send_adapter=None,
                 voice_transcriber=None, wecom_bot_sender=None, wechat_sender=None):
        self.storage = Path(storage)
        self.storage.mkdir(parents=True, exist_ok=True)
        self.store = store or ChatStore(self.storage / 'hub.sqlite3')
        self.analytics = AnalyticsService(self.store)
        self.send_repository = SendRepository(self.store.path)
        self.qq_sender = NapCatOneBotSender(DEFAULT_BUNDLE / 'config' / 'onebot11.json')
        self.send_adapter = send_adapter or QQAccountBoundSender(
            self.qq_sender, lambda: QQProvider().status(),
        )
        # Lazily resolves the WeChat reader so a missing database key cannot
        # break Hub start-up or unrelated delivery platforms.
        self.wechat_sender = wechat_sender or _LazyWeChatSender()
        self.wecom_bot_credentials = WeComBotCredentials(self.storage / 'wecom-bot.credentials')
        self.wecom_bot_media = WeComBotMediaStore(self.storage)
        self.wecom_bot_sender = _ConfiguredWeComBotSender(
            self.wecom_bot_credentials, wecom_bot_sender or WeComBotTextSender(),
            self.wecom_bot_media)
        self.send_service = SendService(self.send_repository, {
            'qq': self.send_adapter, 'wecom_bot': self.wecom_bot_sender,
            'wechat': self.wechat_sender,
        },
                                        recover_inflight=resume_imports)
        self.wecom_bot_media.remove_orphans(self.send_repository.attachment_ids())
        self.backup_dir = self.storage / 'backups'
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.import_dir = self.storage / 'imports'
        self.import_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.storage / 'settings.json'
        self.settings = {'auto_sync': True, 'sync_interval': 5, 'mcp_write_enabled': False,
                         'notification_filters': {'platforms': list(NATIVE_PLATFORMS),
                                                  'muted_conversations': []},
                         **read_json(self.settings_path)}
        self.settings['notification_filters'] = self._normalize_notification_filters(
            self.settings.get('notification_filters'))
        self.settings['capabilities'] = normalize_capabilities(self.settings.get('capabilities'))
        self.account_safety = AccountSafety(
            self.settings, lambda settings: save_json(self.settings_path, settings))
        self.auto_exports = AutoExportManager(
            self.store, self.storage, self.settings,
            lambda settings: save_json(self.settings_path, settings),
            lambda: self.capability_enabled('auto_export'),
        )
        self.lock = threading.RLock()
        self.sync_gate_lock = threading.RLock()
        self.send_approval_challenges = {}
        self.providers = {}
        self._campus_service = None
        self.media_paths = {}
        self.media_preview_lock = threading.Lock()
        self.moment_media_paths = {}
        self.resource_jobs = ResourceJobManager(self.storage, self.resolve_resource)
        self.avatar_cache = {}
        self.voice_transcriber = voice_transcriber or VoiceTranscriber()
        self.jobs = set()
        self.sync_cancellations = {}
        self.attempts = {}
        self.qq_history_job = None
        self.qq_key_discovery = {'state': 'idle', 'message': ''}
        self.import_cancellations = {}
        self.stop = threading.Event()
        self.notified_event_ids = set()
        self.pending_notification_ids = {}
        self.notification_targets = {}
        self.notification_lock = threading.Lock()
        self.sources = {p: {'id': p, 'state': 'disconnected', 'version': v,
                           'last_sync': None, 'error': '', 'progress': ''}
                        for p, v in [('wechat', '4.x / WeFlow 5.0.0'),
                                     ('wecom', '本地快照'), ('qq', 'QQNT / 本地数据库'),
                                     ('email', 'IMAP / Gmail / Outlook')]}
        if resume_imports:
            self._resume_import_jobs()

    def capability_enabled(self, name):
        details = describe_capabilities(self.settings.get('capabilities')).get(name)
        return bool(details and details['available'] and details['enabled'])

    def close(self):
        self.stop.set()
        if self._campus_service is not None:
            self._campus_service.close()
        self.auto_exports.close()
        self.resource_jobs.close()
        self.send_repository.close()
        if self.store._conn is not None:
            self.store.close()

    def capability_error(self, name):
        return {'error': unavailable_reason(name), 'code': 'capability_disabled',
                'capability': name}

    def issue_send_approval_challenge(self, request_id, platform=None):
        request = self.send_service.status(request_id, platform)
        if not request or request['status'] != 'pending_approval':
            raise ValueError('发送请求不存在或已处理')
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self.lock:
            self.send_approval_challenges = {
                key: value for key, value in self.send_approval_challenges.items()
                if value[1] > now
            }
            if len(self.send_approval_challenges) >= 256:
                oldest = min(self.send_approval_challenges,
                             key=lambda key: self.send_approval_challenges[key][1])
                self.send_approval_challenges.pop(oldest, None)
            self.send_approval_challenges[request_id] = (token, now + 60, platform)
        return {'token': token, 'expires_in': 60}

    def consume_send_approval_challenge(self, request_id, token, platform=None):
        # Verify the one-time challenge before any state lookup so a token that
        # was issued for another platform is always reported as an invalid
        # token, then consume it only after every precondition has passed: a
        # not-ready WeChat client must not burn the challenge.
        if not isinstance(token, str):
            raise ValueError('发送确认令牌无效或已过期')
        now = time.monotonic()
        with self.lock:
            challenge = self.send_approval_challenges.get(request_id)
            if (not challenge or challenge[1] <= now or challenge[2] != platform
                    or not secrets.compare_digest(challenge[0], token)):
                if challenge and challenge[1] <= now:
                    self.send_approval_challenges.pop(request_id, None)
                raise ValueError('发送确认令牌无效或已过期')
            verified = challenge
        pending = self.send_service.status(request_id, platform)
        if not pending or pending['status'] != 'pending_approval':
            raise ValueError('发送请求不存在或已处理')
        if platform == 'wechat':
            readiness = self.wechat_sender.readiness(True)
            if (not isinstance(readiness, dict) or readiness.get('state') != 'ready'
                    or not isinstance(readiness.get('account_id'), str)
                    or not readiness['account_id']):
                raise ValueError('本机微信客户端不可用；请在已登录的 Windows 主机上打开微信')
            if readiness['account_id'] != pending['account_id']:
                raise ValueError('account_id must match the currently logged-in WeChat account')
        with self.lock:
            if self.send_approval_challenges.get(request_id) is not verified:
                raise ValueError('发送确认令牌无效或已过期')
            self.send_approval_challenges.pop(request_id, None)
        return pending

    def require_capability(self, name):
        if not self.capability_enabled(name):
            raise PermissionError(unavailable_reason(name))

    def notify_message_event(self, event_id, activation_base_url=''):
        if not isinstance(event_id, int) or isinstance(event_id, bool) or event_id < 1:
            raise ValueError('通知事件编号无效')
        with self.notification_lock:
            if event_id in self.notified_event_ids:
                return {'submitted': False, 'duplicate': True}
            now = time.monotonic()
            self.pending_notification_ids = {
                key: created for key, created in self.pending_notification_ids.items()
                if now - created <= 60
            }
            if event_id not in self.pending_notification_ids:
                raise ValueError('通知事件未由本机消息流发出或已过期')
            self.pending_notification_ids.pop(event_id, None)
            rows = self.store.incoming_message_events(event_id - 1, 1)
            if not rows or rows[0]['event_id'] != event_id:
                raise ValueError('通知事件已不存在')
            row = rows[0]
            filters = self.settings.get('notification_filters', {})
            identity = json.dumps([row['platform'], row['account_id'], row['conversation_id']],
                                  ensure_ascii=False, separators=(',', ':'))
            if (row['platform'] not in filters.get('platforms', NATIVE_PLATFORMS) or
                    identity in filters.get('muted_conversations', []) or
                    (filters.get('mode') == 'allowlist' and
                     identity not in filters.get('allowed_conversations', []))):
                return {'submitted': False, 'filtered': True}
            label = {'wechat': '微信', 'wecom': '企业微信', 'qq': 'QQ'}[row['platform']]
            token = secrets.token_urlsafe(24)
            target = {key: row[key] for key in (
                'platform', 'account_id', 'conversation_id', 'conversation_title',
                'conversation_kind')}
            now = time.monotonic()
            self.notification_targets = {
                key: value for key, value in self.notification_targets.items()
                if now - value[0] <= 600
            }
            self.notification_targets[token] = (now, target)
            while len(self.notification_targets) > 512:
                oldest = min(self.notification_targets, key=lambda key: self.notification_targets[key][0])
                self.notification_targets.pop(oldest, None)
            try:
                open_url = (f'{activation_base_url}/#notification={token}'
                            if activation_base_url else None)
                show_notification(f'{label} · 收到一条新消息', open_url)
            except Exception:
                self.notification_targets.pop(token, None)
                raise
            self.notified_event_ids.add(event_id)
            if len(self.notified_event_ids) > 512:
                self.notified_event_ids.clear()
                self.notified_event_ids.add(event_id)
            return {'submitted': True}

    def activate_notification_target(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32}', token):
            raise ValueError('通知会话令牌无效')
        with self.notification_lock:
            entry = self.notification_targets.pop(token, None)
        if not entry or time.monotonic() - entry[0] > 600:
            raise ValueError('通知会话令牌已失效或已使用')
        return dict(entry[1])

    def queue_notification_event(self, event_id):
        with self.notification_lock:
            self.pending_notification_ids[event_id] = time.monotonic()
            if len(self.pending_notification_ids) > 512:
                oldest = min(self.pending_notification_ids, key=self.pending_notification_ids.get)
                self.pending_notification_ids.pop(oldest, None)

    @staticmethod
    def _normalize_notification_filters(value):
        value = value if isinstance(value, dict) else {}
        platforms = value.get('platforms', list(NATIVE_PLATFORMS))
        muted = value.get('muted_conversations', [])
        allowed = value.get('allowed_conversations', [])
        mode = value.get('mode', 'all')
        style = value.get('style', 'normal')
        if not isinstance(platforms, list) or any(p not in NATIVE_PLATFORMS for p in platforms):
            raise ValueError('通知平台筛选无效')
        if mode not in ('all', 'allowlist'):
            raise ValueError('通知模式无效')
        if style not in ('normal', 'silent'):
            raise ValueError('通知样式无效')

        def normalize_conversations(items, label):
            if not isinstance(items, list) or len(items) > 500:
                raise ValueError(f'通知{label}列表无效')
            normalized = []
            for key in items:
                if not isinstance(key, str) or len(key) > 4096:
                    raise ValueError(f'通知{label}会话标识无效')
                try:
                    identity = json.loads(key)
                except ValueError as exc:
                    raise ValueError(f'通知{label}会话标识无效') from exc
                if (not isinstance(identity, list) or len(identity) != 3 or
                        identity[0] not in NATIVE_PLATFORMS or
                        any(not isinstance(part, str) or len(part) > 1024 for part in identity)):
                    raise ValueError(f'通知{label}会话标识无效')
                normalized.append(json.dumps(identity, ensure_ascii=False, separators=(',', ':')))
            return list(dict.fromkeys(normalized))

        return {'platforms': list(dict.fromkeys(platforms)),
                'mode': mode,
                'style': style,
                'allowed_conversations': normalize_conversations(allowed, '白名单'),
                'muted_conversations': normalize_conversations(muted, '静音')}

    def set_capabilities(self, changes):
        if not isinstance(changes, dict) or not changes:
            raise ValueError('功能开关无效')
        with self.sync_gate_lock:
            updated = dict(self.settings['capabilities'])
            for name, enabled in changes.items():
                details = describe_capabilities(updated).get(name)
                if details is None or not details['available'] or details.get('core'):
                    raise ValueError(unavailable_reason(name))
                if not isinstance(enabled, bool):
                    raise ValueError('功能开关必须为布尔值')
                if name == 'account_safety' and not enabled and self.account_safety.configured:
                    raise ValueError('请先关闭口令锁，再关闭此功能模块')
                updated[name] = enabled
            previous = self.settings['capabilities']
            normalized = normalize_capabilities(updated)
            disabling_sync = previous.get('sync', True) and not normalized['sync']
            disabling_imports = previous.get('imports', True) and not normalized['imports']
            if any(previous.get(name, False) and not normalized[name]
                   for name in ('qq_send', 'wecom_bot_send', 'wechat_send')):
                with self.lock:
                    self.send_approval_challenges.clear()
            if previous.get('notifications', True) and not normalized['notifications']:
                with self.notification_lock:
                    self.notification_targets.clear()
            self.settings['capabilities'] = normalized
            if disabling_sync:
                self.settings['auto_sync'] = False
                with self.lock:
                    for cancel in self.sync_cancellations.values():
                        cancel.set()
            if disabling_imports:
                with self.lock:
                    for cancel in self.import_cancellations.values():
                        cancel.set()
            save_json(self.settings_path, self.settings)
        if previous.get('media', True) and not self.settings['capabilities']['media']:
            self.resource_jobs.pause()
        elif not previous.get('media', True) and self.settings['capabilities']['media']:
            self.resource_jobs.resume()
        if previous.get('qq_history', True) and not self.settings['capabilities']['qq_history']:
            try:
                self.cancel_qq_history_import()
            except ValueError:
                pass
        if previous.get('auto_export', False) and not self.settings['capabilities']['auto_export']:
            self.auto_exports.cancel()
        return describe_capabilities(self.settings['capabilities'])

    def update(self, platform, **values):
        with self.lock:
            self.sources[platform].update(values)

    def campus_service(self):
        from .campus import CampusService
        with self.lock:
            if self._campus_service is None:
                self._campus_service = CampusService(self.storage)
            return self._campus_service

    def campus_wechat_accounts(self, query=''):
        if not isinstance(query, str) or len(query) > 100:
            raise ValueError('公众号查询条件无效')
        selected = {(s.get('account_id'), s.get('contact_id')): s
                    for s in self.campus_service().sources()['items'] if s.get('kind') == 'local_wechat'}
        contacts = {}
        for term in ('中山大学', '中大学工', '康乐解学'):
            for item in self.store.contacts('wechat', term, limit=200, kind='official')['items']:
                if query and query.casefold() not in (item['name'] + ' ' + str(item.get('extra', {}).get('alias', ''))).casefold():
                    continue
                key = (item['account_id'], item['id'])
                source = selected.get(key)
                contacts[key] = dict(id=item['id'], account_id=item['account_id'], name=item['name'],
                    alias=item.get('extra', {}).get('alias', ''), selected=source is not None,
                    article_count=source.get('item_count') if source else None)
        return {'items': list(contacts.values()), 'total': len(contacts)}

    def import_campus_wechat_accounts(self, values):
        if not isinstance(values, dict) or set(values) != {'account_id', 'contact_ids'}:
            raise ValueError('公众号导入参数无效')
        account, ids = values['account_id'], values['contact_ids']
        if (not isinstance(account, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', account)
                or not isinstance(ids, list) or not 1 <= len(ids) <= 30
                or any(not isinstance(cid, str) or not re.fullmatch(r'gh_[A-Za-z0-9_-]{1,100}', cid) for cid in ids)):
            raise ValueError('请选择有效的本机公众号')
        allowed = {(c['account_id'], c['id']) for c in self.campus_wechat_accounts()['items']}
        if any((account, cid) not in allowed for cid in ids):
            raise ValueError('公众号必须属于当前索引中已识别的中大相关账号')
        from integrations.wechat.official_accounts import get_cached_official_articles
        reports = []
        for cid in dict.fromkeys(ids):
            contact = next(c for c in self.store.contacts('wechat', cid, limit=200, account_id=account, kind='official')['items'] if c['id'] == cid)
            report = get_cached_official_articles(account, cid, limit=500)
            if report.get('warning'):
                raise ValueError('该公众号本机缓存暂不可读；请核对微信账号和已缓存的数据库密钥')
            reports.append(self.campus_service().import_local_wechat(contact, report))
        return {'accounts': reports, 'added': sum(r['added'] for r in reports),
                'total': sum(r['total'] for r in reports), 'invalid': sum(r['invalid'] for r in reports)}

    def source_status_snapshot(self):
        with self.lock:
            return [dict(source) for source in self.sources.values()]

    def source_info(self):
        with self.lock:
            result = [dict(s) for s in self.sources.values()]
        counts = self.store.source_counts()
        for source in result:
            if source['id'] == 'email':
                source['email_config'] = self.email_provider().status()
            stats = counts.get(source['id'], {})
            source.update(message_count=stats.get('messages', 0),
                          conversation_count=stats.get('conversations', 0))
        return {'sources': result}

    def list_backups(self):
        items = []
        for pattern in ('lmh-backup-*.sqlite3', 'lmh-backup-*.zip'):
            for path in self.backup_dir.glob(pattern):
                if path.is_file() and not path.is_symlink():
                    stat = path.stat()
                    items.append({'id': path.name, 'size': stat.st_size,
                                  'created_at': int(stat.st_mtime),
                                  'kind': 'media' if path.suffix == '.zip' else 'index'})
        return {'items': sorted(items, key=lambda item: item['created_at'], reverse=True)}

    def create_backup(self, include_media=False):
        if not isinstance(include_media, bool):
            raise ValueError('媒体备份选项无效')
        extension = '.zip' if include_media else '.sqlite3'
        name = time.strftime('lmh-backup-%Y%m%dT%H%M%SZ-', time.gmtime()) + secrets.token_hex(4) + extension
        path = self.backup_dir / name
        if include_media:
            from integrations.wechat.provider import RUNTIME
            return create_media_backup(self.store, self.storage, path, Path(RUNTIME) / 'media')
        self.store.backup_to(path)
        stat = path.stat()
        return {'id': name, 'size': stat.st_size, 'created_at': int(stat.st_mtime), 'kind': 'index'}

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
            owned_dirs = [self.backup_dir, self.import_dir, self.storage / 'qq-roaming',
                          self.resource_jobs.root]
            for path in (*owned_files, *owned_dirs):
                try:
                    path.resolve().relative_to(self.storage.resolve())
                except ValueError as exc:
                    raise ValueError('本地清理目标超出消息中枢存储目录') from exc
                if path.is_symlink():
                    raise ValueError('检测到符号链接；为避免删除外部数据，已停止清理')

            self.resource_jobs.pause()
            self.store.close()
            self.send_repository.close()
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
                        patterns = ('lmh-backup-*.sqlite3', 'lmh-backup-*.zip') if directory == self.backup_dir else ('*.json', '*.tmp')
                        for pattern in patterns:
                            for child in directory.glob(pattern):
                                if child.is_file() and not child.is_symlink():
                                    child.unlink()
                                    deleted.append(child.relative_to(self.storage).as_posix())
                    else:
                        for child in directory.iterdir():
                            valid_child = (
                                re.fullmatch(r'[A-Za-z0-9_-]{16}', child.name)
                                if directory == self.storage / 'qq-roaming'
                                else re.fullmatch(r'[A-Fa-f0-9]{20}', child.name)
                            )
                            if valid_child and child.is_dir() and not child.is_symlink():
                                shutil.rmtree(child)
                                deleted.append(child.relative_to(self.storage).as_posix())
            finally:
                self.store = ChatStore(database)
                self.email_provider().reset_checkpoint()
                self.analytics = AnalyticsService(self.store)
                self.send_repository = SendRepository(self.store.path)
                self.send_service = SendService(self.send_repository, {
                    'qq': self.send_adapter, 'wecom_bot': self.wecom_bot_sender,
                    'wechat': self.wechat_sender,
                })
                for source in self.sources.values():
                    source.update(state='disconnected', last_sync=None, error='', progress='', message_count=0, conversation_count=0)
                if self.capability_enabled('media'):
                    self.resource_jobs.resume()
            return {'deleted': deleted, 'retained': ['平台原始数据库和缓存', '账号/密钥配置', '非消息中枢管理的同目录文件']}

    def backup_path(self, backup_id):
        if not isinstance(backup_id, str) or not re.fullmatch(r'lmh-backup-(?:restored-)?[0-9TZ-]+-[a-f0-9]{8}\.(?:sqlite3|zip)', backup_id):
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
                if zipfile.is_zipfile(path):
                    from integrations.wechat.provider import RUNTIME
                    restore_media_backup(self.store, self.storage, path, Path(RUNTIME) / 'media')
                else:
                    self.store.restore_from(path)
                self.email_provider().reset_checkpoint()
            except Exception:
                try:
                    self.store.restore_from(rollback)
                except Exception as rollback_error:
                    raise RuntimeError('恢复失败，且自动回滚失败；请使用恢复快照 ' + rollback.name) from rollback_error
                raise

    def _resume_import_jobs(self):
        if not self.capability_enabled('imports'):
            return
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
            conversations_by_id = {}
            for index, conversation in enumerate(conversations):
                conversations_by_id.setdefault(conversation['id'], []).append((index, conversation))
            offset = int(job['processed'])
            written_messages = int(job['messages'])
            written_conversations = int(job['conversations'])
            self.store.update_import_job(job_id, state='running', updated_at=int(time.time()), error='')
            while offset < len(messages):
                if cancel.is_set() or not self.capability_enabled('imports'):
                    self.store.update_import_job(job_id, state='cancelled', updated_at=int(time.time()))
                    return
                batch = messages[offset:offset + 500]
                groups = {}
                for message in batch:
                    groups.setdefault((message['platform'], message['account_id']), []).append(message)
                for (platform, account), group in groups.items():
                    conv_ids = {item['conversation_id'] for item in group}
                    selected = [item for cid in conv_ids for item in conversations_by_id.get(cid, ())]
                    convs = [item for _, item in sorted(selected, key=lambda item: item[0])]
                    report = self._import_ingest(cancel, platform, account, convs, group)
                    if report is None:
                        self.store.update_import_job(job_id, state='cancelled', updated_at=int(time.time()))
                        return
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

    def start_sync(self, platform='all', conversation_id=None):
        self.require_capability('sync')
        if platform not in (*SYNC_PLATFORMS, 'all'):
            raise ValueError('未知平台')
        if conversation_id is not None:
            if platform != 'qq':
                raise ValueError('conversation_id 仅支持 QQ 定向同步')
            if (not isinstance(conversation_id, str) or not conversation_id.strip() or
                    len(conversation_id) > MAX_SYNC_CONVERSATION_ID_LENGTH):
                raise ValueError('QQ 会话标识无效')
        for p in SYNC_PLATFORMS if platform == 'all' else [platform]:
            with self.lock:
                history = self.qq_history_job
                if p in self.jobs or (p == 'qq' and history and history['state'] in ('starting', 'running', 'cancel_requested')):
                    continue
                self.jobs.add(p)
                cancel = threading.Event()
                self.sync_cancellations[p] = cancel
                self.attempts[p] = time.time()
            threading.Thread(target=self.sync, args=(p, cancel, conversation_id),
                             daemon=True, name='sync-' + p).start()

    def _sync_cancelled(self, cancel=None):
        return self.stop.is_set() or (cancel is not None and cancel.is_set()) or not self.capability_enabled('sync')

    def _sync_ingest(self, cancel, *args, **kwargs):
        with self.sync_gate_lock:
            if self._sync_cancelled(cancel):
                return False
            self.store.ingest(*args, **kwargs)
            return True

    def _import_ingest(self, cancel, *args, **kwargs):
        with self.sync_gate_lock:
            if cancel.is_set() or not self.capability_enabled('imports'):
                return None
            return self.store.ingest(*args, **kwargs)

    def _qq_history_ingest(self, cancel, *args, **kwargs):
        with self.sync_gate_lock:
            if cancel.is_set() or not self.capability_enabled('qq_history'):
                return False
            self.store.ingest(*args, **kwargs)
            return True

    def _sync_replace_moments(self, cancel, account, items, cache_dir):
        with self.sync_gate_lock:
            if self._sync_cancelled(cancel) or not self.capability_enabled('moments'):
                return False
            self.store.replace_moments(account, items, cache_dir)
            return True

    def sync(self, platform, cancel=None, conversation_id=None):
        if cancel is None:
            cancel = self.sync_cancellations.get(platform)
        if self._sync_cancelled(cancel):
            self.update(platform, state='disconnected', progress='', error='同步功能已在设置中关闭')
            with self.lock:
                self.jobs.discard(platform)
                if self.sync_cancellations.get(platform) is cancel:
                    self.sync_cancellations.pop(platform, None)
            return
        self.update(platform, state='syncing', error='', progress='正在读取本机快照')
        try:
            if platform == 'qq':
                result = self.sync_qq(cancel, conversation_id=conversation_id)
            else:
                result = getattr(self, 'sync_' + platform)(cancel)
            if self._sync_cancelled(cancel):
                self.update(platform, state='disconnected', progress='', error='同步功能已在设置中关闭')
            elif result == 'unavailable':
                pass
            else:
                self.update(platform, state='partial' if result == 'partial' else 'connected',
                            last_sync=int(time.time()), progress='')
        except LoginRequired:
            self.update(platform, state='error', progress='', error='本地 QQ 数据库尚未解锁，请检查数据源配置')
        except QQConversationNotFound as exc:
            self.update(platform, state='error', progress='', error=str(exc))
        except Exception as exc:
            # Provider exceptions may contain paths or SQL values; never send them to the UI/log.
            error_message = {
                'wechat': '本机微信读取失败，请保持微信登录并检查数据库目录',
                'wecom': '企业微信快照读取失败，请检查本机目录和密钥',
                'qq': 'QQ 本地数据库读取失败，请检查目录、密钥与数据库版本',
                'email': '邮件同步失败，请检查服务器、授权凭据与邮箱 IMAP 权限'
            }[platform]
            if platform == 'wecom':
                try:
                    from integrations.wecom.adapter import WeComSnapshotError
                    if isinstance(exc, WeComSnapshotError):
                        error_message = exc.safe_message
                except ImportError:
                    pass
            if platform == 'email':
                from integrations.email.provider import EmailProviderError
                if isinstance(exc, EmailProviderError):
                    error_message = str(exc)
            self.update(platform, state='error', progress='', error=error_message)
            print('Source failure:', platform, type(exc).__name__, flush=True)
        finally:
            if platform == 'qq':
                provider = self.providers.get('qq_local')
                close = getattr(provider, 'close', None)
                if callable(close):
                    close()
            with self.lock:
                self.jobs.discard(platform)
                if self.sync_cancellations.get(platform) is cancel:
                    self.sync_cancellations.pop(platform, None)

    def email_provider(self):
        from integrations.email.provider import EmailProvider
        with self.lock:
            if 'email' not in self.providers:
                self.providers['email'] = EmailProvider(self.storage)
            return self.providers['email']

    def sync_email(self, cancel=None):
        provider = self.email_provider()
        if not provider.status().get('configured'):
            self.update('email', state='disconnected', progress='', error='请先配置邮箱账号')
            return 'unavailable'
        result = provider.sync(cancel=lambda: self._sync_cancelled(cancel))
        records = result['records']
        messages = records['messages']
        if messages:
            account = messages[0]['account_id']
            if not self._sync_ingest(cancel, 'email', account, records['conversations'], messages):
                return
        with self.sync_gate_lock:
            if self._sync_cancelled(cancel):
                return
            provider.commit_checkpoint(result['checkpoint'])
        if records.get('invalid') or records.get('warnings') or result.get('oversized'):
            self.update('email', error='部分邮件暂未完整归档，请检查邮件大小或格式')
            return 'partial'
        if result.get('has_more'):
            self.update('email', error='本批邮件已归档；仍有历史邮件待同步，可再次同步继续')
            return 'partial'

    def sync_wechat(self, cancel=None):
        from integrations.wechat.provider import WeChatProvider
        if self._sync_cancelled(cancel):
            return
        provider = WeChatProvider()
        with self.lock:
            self.providers['wechat'] = provider
        if self._sync_cancelled(cancel):
            return
        account = str(provider.reader.wxid or provider.reader.account or 'local')
        contacts = provider.contacts()
        avatar_buffers = getattr(provider, 'avatar_buffers', None)
        local_avatar_url = getattr(provider, 'local_avatar_url', None)
        if callable(avatar_buffers) and callable(local_avatar_url):
            contacts_by_id = {contact['id']: contact for contact in contacts if contact.get('id')}
            buffers = avatar_buffers(list(contacts_by_id))
            for username, payload in (buffers.items() if isinstance(buffers, dict) else ()):
                contact = contacts_by_id.get(username)
                if contact is None or not isinstance(payload, bytes) or len(payload) > 2 * 1024 * 1024:
                    continue
                url = local_avatar_url(username)
                if self._cache_local_avatar(url, payload):
                    contact['avatar_url'] = url
        names = {c['id']: c['name'] for c in contacts}
        if self._sync_cancelled(cancel):
            return
        if not self._sync_ingest(cancel, 'wechat', account, [], [], contacts):
            return
        sessions = provider.sessions()
        for index, session in enumerate(sessions):
            if self._sync_cancelled(cancel):
                return
            self.update('wechat', state='indexing', progress=f'{index + 1} / {len(sessions)} 个会话')
            cid = session['id']
            conv = dict(session, account_id=account)
            total = session.get('message_count') or provider.message_count(cid)
            if callable(getattr(provider, 'messages_after', None)):
                checkpoint = self.store.sync_checkpoint('wechat', account, cid)
                cursor = checkpoint.get('cursor') if checkpoint else None
                while not self._sync_cancelled(cancel):
                    page = provider.messages_after(cid, cursor=cursor, limit=2000)
                    rows = page.get('messages', [])
                    next_cursor = page.get('next_cursor')
                    complete = not page.get('has_more', False)
                    messages = []
                    for row in rows:
                        raw_type = int(row.get('local_type') or 1) & 0xffffffff
                        source_id = source_message_id(row.get('server_id'))
                        kind, body, filename = message_body(row.get('content'), raw_type)
                        emoji_md5 = message_emoji_md5(row.get('content')) if raw_type in (47, 11000) else None
                        location = message_location(row.get('content')) if raw_type == 48 else None
                        quote = message_quote(row.get('content'), raw_type)
                        messages.append({**row, 'conversation_id': cid, 'type': kind, 'text': body,
                                         'sender_name': names.get(row['sender_id'], row['sender_id']),
                                         'file_name': filename, 'raw_type': str(raw_type),
                                         **({'_hub_wechat_server_id': source_id} if source_id else {}),
                                         **({'emoji_md5': emoji_md5} if emoji_md5 else {}),
                                         **({'wechat_quote': quote} if quote else {}),
                                         **({'location': location} if location else {})})
                    if not rows and not complete and next_cursor == cursor:
                        raise RuntimeError('WeChat source cursor did not advance')
                    if self._sync_cancelled(cancel):
                        return
                    if not self._sync_ingest(cancel, 'wechat', account, [conv], messages, checkpoint={
                        'conversation_id': cid, 'next_offset': 0, 'cursor': next_cursor,
                        'complete': complete}):
                        return
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
                if not self._sync_ingest(cancel, 'wechat', account, [conv], [], checkpoint={
                    'conversation_id': cid, 'next_offset': 0, 'complete': True}):
                    return
            while offset < total:
                if self._sync_cancelled(cancel):
                    return
                rows = provider.messages(cid, limit=2000, offset=offset)
                if not rows:
                    break
                messages = []
                for row in rows:
                    raw_type = int(row.get('local_type') or 1) & 0xffffffff
                    source_id = source_message_id(row.get('server_id'))
                    kind, body, filename = message_body(row.get('content'), raw_type)
                    emoji_md5 = message_emoji_md5(row.get('content')) if raw_type in (47, 11000) else None
                    location = message_location(row.get('content')) if raw_type == 48 else None
                    quote = message_quote(row.get('content'), raw_type)
                    messages.append({**row, 'conversation_id': cid, 'type': kind, 'text': body,
                                     'sender_name': names.get(row['sender_id'], row['sender_id']),
                                     'file_name': filename, 'raw_type': str(raw_type),
                                     **({'_hub_wechat_server_id': source_id} if source_id else {}),
                                     **({'emoji_md5': emoji_md5} if emoji_md5 else {}),
                                     **({'wechat_quote': quote} if quote else {}),
                                     **({'location': location} if location else {})})
                next_offset = offset + len(rows)
                complete = next_offset >= total or len(rows) < 2000
                if self._sync_cancelled(cancel):
                    return
                if not self._sync_ingest(cancel, 'wechat', account, [conv], messages, checkpoint={
                    'conversation_id': cid, 'next_offset': next_offset,
                    'complete': complete}):
                    return
                offset = next_offset
                if complete:
                    break
        if not self._sync_cancelled(cancel) and self.capability_enabled('moments'):
            self.update('wechat', state='indexing', progress='正在更新朋友圈本地索引')
            try:
                catalog = provider.moments(limit=100_000, offset=0)
                if catalog.get('available') and not catalog.get('truncated'):
                    self._sync_replace_moments(cancel, account, catalog.get('items', []),
                                               getattr(provider, 'sns_cache_dir', None))
            except Exception as exc:
                print('Moments catalog failure:', type(exc).__name__, flush=True)
        source_status = provider.status()
        self.update('wechat', avatar_database=source_status.get('avatar_database'),
                    image_key_configured=source_status.get('image_key_configured', False))
        if source_status['state'] == 'partial':
            self.update('wechat', error='部分数据库未解锁；已显示可读记录')
            return 'partial'

    def sync_wecom(self, cancel=None):
        from integrations.wecom.adapter import WeComAdapter
        if self._sync_cancelled(cancel):
            return
        provider = WeComAdapter(voice_cache_dir=self.storage / 'media' / 'decoded-voice')
        provider.refresh()
        if self._sync_cancelled(cancel):
            return
        with self.lock:
            self.providers['wecom'] = provider
        account = 'local'
        engine = getattr(provider, 'engine', None)
        directory = engine.get_content().get('members', []) if engine else []
        avatars = {str(item.get('user_id') or ''): item.get('avatar')
                   for item in directory if item.get('user_id') and item.get('avatar')}
        convs = [{'id': c['conversation_id'], 'title': c['name'],
                  'kind': 'group' if c['participants'] > 2 else 'direct',
                  'last_timestamp': c['last_time']} for c in provider.list_conversations(100000)]
        avatar_buffers = getattr(provider, 'conversation_avatar_buffers', None)
        local_avatar_url = getattr(provider, 'local_avatar_url', None)
        if callable(avatar_buffers) and callable(local_avatar_url):
            buffers = avatar_buffers([str(conv['id']) for conv in convs])
            for conv in convs:
                payload = buffers.get(str(conv['id'])) if isinstance(buffers, dict) else None
                url = local_avatar_url(conv['id'])
                if self._cache_local_avatar(url, payload):
                    conv['avatar_url'] = url
        if self._sync_cancelled(cancel):
            return
        if not self._sync_ingest(cancel, 'wecom', account, convs, []):
            return
        # WeCom currently exposes only a fully materialized local snapshot; its
        # batch iterator does not provide a resumable source cursor.
        for batch in provider.iter_message_batches(1000):
            if self._sync_cancelled(cancel):
                return
            messages, contacts = [], {}
            for row in batch:
                raw = int(row.get('content_type') or 0)
                kind = {2: 'image', 4: 'file', 23: 'voice', 36: 'video', 13: 'link',
                        14: 'link', 15: 'link'}.get(raw, 'system' if row.get('kind') == 'sys' else 'text')
                attachments = row.get('media') if isinstance(row.get('media'), list) else []
                attachment_info = [{'name': str(item.get('n') or ''),
                                    'type': str(item.get('k') or ''),
                                    'size': item.get('sz')}
                                   for item in attachments if isinstance(item, dict)]
                messages.append({'id': str(row['message_id']), 'conversation_id': str(row['conversation_id']),
                                 'sender_id': str(row.get('sender_id') or ''), 'sender_name': row.get('sender') or '',
                                 'timestamp': row.get('send_time'), 'text': row.get('text') or '',
                                 'type': kind, 'is_self': row.get('me', False), 'raw_type': str(raw),
                                 'file_name': attachment_info[0]['name'] if attachment_info else '',
                                 'attachments': attachment_info})
                sid = str(row.get('sender_id') or '')
                if sid:
                    contacts[sid] = {'id': sid, 'name': row.get('sender') or sid,
                                     'avatar_url': avatars.get(sid)}
            if not self._sync_ingest(cancel, 'wecom', account, [], messages, contacts.values()):
                return
        release = getattr(provider, 'release_message_cache', None)
        if callable(release):
            release()

    def qq_local_provider(self):
        from integrations.qq.local_provider import LocalQQProvider
        return LocalQQProvider(config_path=self.storage / 'qq-local.json',
                               cache_dir=self.storage / 'integrations' / 'qq-local')

    def resolve_qq_send_target(self, account_id, conversation_type, conversation_id):
        """Translate an indexed QQ conversation into a numeric OneBot target.

        Local QQNT conversations are keyed by ``uid`` for private chats, while
        OneBot 11 only accepts a numeric uin/group code. The conversion is done
        from the indexed record itself: the indexed extra carries the numeric
        uin that the online sync captured, and the local reader's own uid
        mapping covers conversations synced from the local database. Anything
        that stays non-numeric is refused before a send request is created.
        """
        platform = 'qq'
        account_id = str(account_id or '')
        conversation_id = str(conversation_id or '')
        if conversation_type not in ('private', 'group'):
            raise ValueError('QQ 会话类型无效')
        row = self.store.get_conversation(platform, account_id, conversation_id)
        if row is None or row.get('platform') != platform:
            raise ValueError('该 QQ 会话不在本机索引中，请先同步后再试')
        indexed_kind = row.get('kind')
        indexed_type = 'group' if indexed_kind == 'group' else (
            'private' if indexed_kind in ('private', 'direct') else None)
        if indexed_type != conversation_type:
            raise ValueError('QQ 会话类型与本机索引不匹配')
        if is_uin(conversation_id):
            return {'conversation_id': conversation_id}
        extra = row.get('extra') if isinstance(row.get('extra'), dict) else {}
        # Prefer the uin the online sync recorded; fall back to the local
        # reader's own uid mapping for conversations synced from the database.
        indexed = extra.get('uin') or (extra.get('raw') or {}).get('peerUin')
        if is_uin(indexed if isinstance(indexed, str) else ''):
            # Keep the indexed spelling so the reviewer sees what was resolved.
            return {'conversation_id': indexed,
                    'source_conversation_id': conversation_id}
        provider = self.providers.get('qq_local') or self.qq_local_provider()
        from integrations.qq.local_provider import LocalQQError
        try:
            resolved = provider.send_target(conversation_id, conversation_type)
        except LocalQQError as exc:
            raise ValueError(str(exc)) from None
        except Exception:
            raise ValueError('QQ 本地数据库暂不可用，无法解析发送目标') from None
        # Keep the indexed spelling so the reviewer sees what was resolved.
        return {'conversation_id': resolved['conversation_id'],
                'source_conversation_id': conversation_id}

    def qq_local_status(self):
        status = self.qq_local_provider().status()
        # Deliberately omit account identifiers, source paths and encryption material.
        result = {key: status[key] for key in ('mode', 'available', 'state', 'error_code',
                                               'message', 'database_count') if key in status}
        result['key_discovery'] = self.qq_key_discovery_status()
        return result

    def qq_key_discovery_status(self):
        with self.lock:
            return dict(self.qq_key_discovery)

    def start_qq_key_discovery(self, confirmed):
        self.require_capability('sync')
        if confirmed is not True:
            raise ValueError('请先确认仅只读扫描当前用户运行中的 QQ')
        provider = self.qq_local_provider()
        status = provider.status()
        if status.get('available'):
            raise ValueError('QQ 本地数据库已可读取，无需发现密钥')
        if status.get('error_code') not in ('key_required', 'key_invalid'):
            raise ValueError('当前 QQ 本地数据库状态不支持密钥发现')
        with self.lock:
            if self.qq_key_discovery['state'] == 'running':
                return dict(self.qq_key_discovery)
            job = {'state': 'running', 'message': '正在只读扫描；最多 180 秒，不会调试或关闭 QQ'}
            self.qq_key_discovery = job
        threading.Thread(target=self._run_qq_key_discovery, args=(provider,),
                         daemon=True, name='qq-key-discovery').start()
        return dict(job)

    def _run_qq_key_discovery(self, provider):
        try:
            from integrations.qq.key_reader import (MAX_KEY_CANDIDATES, MAX_PROCESS_CANDIDATES,
                                                     MAX_SCAN_BYTES, find_key)
            key, result = find_key(provider)
            if not key or not isinstance(result, dict) or result.get('state') != 'verified':
                state = result.get('state') if isinstance(result, dict) else None
                messages = {
                    'not_found': '未找到可验证的密钥；请保持 QQ 打开并完成登录后重试。',
                    'no_accessible_qq': '未找到当前 Windows 用户可读取的 QQ 进程。',
                    'limit_reached': '只读扫描达到安全上限，未确认密钥。',
                }
                limit_messages = {
                    'time_limit': '180 秒时限',
                    'memory_budget': '512 MiB 读取预算',
                    'process_limit': '进程枚举上限',
                    'candidate_limit': '候选验证次数上限',
                }
                limit_reason = result.get('limit_reason') if state == 'limit_reached' else None
                message = messages.get(state, '未发现可验证的密钥。')
                if limit_reason in limit_messages:
                    message = f'只读扫描达到{limit_messages[limit_reason]}，未确认密钥。'
                discovery = {'state': state if state in messages else 'failed',
                             'message': message}
                if limit_reason in limit_messages:
                    discovery['limit_reason'] = limit_reason
                for key, maximum in (('processes', MAX_PROCESS_CANDIDATES),
                                     ('tested', MAX_KEY_CANDIDATES),
                                     ('read_mib', MAX_SCAN_BYTES // (1024 * 1024))):
                    value = result.get(key)
                    if type(value) is int and 0 <= value <= maximum:
                        discovery[key] = value
                with self.lock:
                    self.qq_key_discovery = discovery
                return

            config_path = provider.config_path
            with self.lock:
                source, _ = provider._discover()
                provider._probe(source, key)
                self.require_capability('sync')
                old_config = read_json(config_path)
                config = dict(old_config)
                from integrations.qq.key_store import persist_key
                persist_key(config_path, config, key)
                verified = self.qq_local_provider().status().get('available')
                if not verified:
                    if old_config.get('key') and not str(old_config['key']).startswith('dpapi:v1:'):
                        from integrations.qq.key_store import protect_key
                        old_config['key'] = protect_key(old_config['key'])
                    save_json(config_path, old_config)
                    self.qq_key_discovery = {'state': 'failed',
                                             'message': '密钥复核未通过；本机配置已恢复。'}
                    return
            with self.lock:
                self.qq_key_discovery = {'state': 'completed',
                                         'message': '密钥已在本机验证并保存，正在同步本地记录。'}
            try:
                self.start_sync('qq')
            except PermissionError:
                with self.lock:
                    self.qq_key_discovery['message'] = '密钥已在本机验证并保存；同步功能已关闭。'
        except Exception:
            with self.lock:
                self.qq_key_discovery = {'state': 'failed',
                                         'message': '只读密钥发现失败；未向界面返回密钥，请检查 QQ 状态后重试。'}

    def sync_qq(self, cancel=None, conversation_id=None):
        from integrations.qq.provider import QQProviderError
        if self._sync_cancelled(cancel):
            return
        provider = self.qq_local_provider()
        with self.lock:
            self.providers['qq_local'] = provider
        status = provider.status()
        if self._sync_cancelled(cancel):
            return
        if not status.get('available'):
            self.update('qq', state='error', progress='', error=status.get('message') or
                        'QQ 本地数据库尚未就绪，请检查目录和密钥')
            return 'unavailable'
        account = str(status.get('account_id') or 'local')
        convs = provider.conversations()
        if conversation_id is not None:
            convs = [conv for conv in convs
                     if str(conv.get('id') or conv.get('conversation_id') or '') == conversation_id]
            if not convs:
                raise QQConversationNotFound('所选 QQ 会话不属于当前本地数据库账号或已不可用')
        if self._sync_cancelled(cancel):
            return
        contacts = provider.contacts()
        if self._sync_cancelled(cancel):
            return
        avatar_buffers = getattr(provider, 'avatar_buffers', None)
        local_avatar_url = getattr(provider, 'local_avatar_url', None)
        if callable(avatar_buffers) and callable(local_avatar_url):
            for offset in range(0, len(contacts), 8):
                if self._sync_cancelled(cancel):
                    return
                batch = contacts[offset:offset + 8]
                buffers = avatar_buffers(batch)
                for contact in batch:
                    identity = contact.get('id')
                    payload = buffers.get(identity) if isinstance(buffers, dict) else None
                    scope = 'group' if contact.get('kind') == 'group' else 'user'
                    url = local_avatar_url(identity, scope)
                    if self._cache_local_avatar(url, payload):
                        contact['avatar_url'] = url
        if not self._sync_ingest(cancel, 'qq', account, convs, [], contacts):
            return
        failed_conversations = 0
        for index, conv in enumerate(convs):
            if self._sync_cancelled(cancel):
                return
            self.update('qq', state='indexing', progress=f'{index + 1} / {len(convs)} 个会话')
            cid = str(conv.get('id') or conv.get('conversation_id') or '')
            messages_page = getattr(provider, 'messages_page', None)
            if callable(messages_page):
                checkpoint = self.store.sync_checkpoint('qq', account, cid)
                resume = checkpoint if checkpoint and not checkpoint['complete'] else None
                cursor = resume.get('cursor') if resume else None
                offset = resume['next_offset'] if resume else 0
                while not self._sync_cancelled(cancel):
                    try:
                        page = messages_page(conv, limit=500, cursor=cursor)
                    except QQProviderError:
                        failed_conversations += 1
                        break
                    if not isinstance(page, dict) or not isinstance(page.get('items'), list):
                        raise RuntimeError('QQ source returned an invalid keyset page')
                    rows = page['items']
                    next_cursor = page.get('next_cursor')
                    complete = not page.get('has_more', False)
                    if rows and next_cursor == cursor:
                        raise RuntimeError('QQ source cursor did not advance')
                    if not rows and not complete and next_cursor == cursor:
                        raise RuntimeError('QQ source cursor did not advance')
                    if self._sync_cancelled(cancel):
                        return
                    next_offset = offset + len(rows)
                    if not self._sync_ingest(cancel, 'qq', account, [conv], rows, checkpoint={
                            'conversation_id': cid, 'next_offset': next_offset,
                            'cursor': next_cursor, 'complete': complete}):
                        return
                    cursor, offset = next_cursor, next_offset
                    if complete:
                        break
                continue

            # Compatibility for local QQ readers that expose offset paging only.
            offset = 0
            while not self._sync_cancelled(cancel):
                try:
                    page = provider.messages(conv, limit=500, offset=offset)
                except QQProviderError:
                    failed_conversations += 1
                    break
                if self._sync_cancelled(cancel):
                    return
                rows = page.get('items', []) if isinstance(page, dict) else page
                if not rows:
                    if not self._sync_ingest(cancel, 'qq', account, [conv], [], checkpoint={
                        'conversation_id': cid, 'next_offset': offset, 'complete': True}):
                        return
                    break
                next_offset = offset + len(rows)
                complete = ((isinstance(page, dict) and not page.get('has_more', False))
                            or len(rows) < 500)
                if not self._sync_ingest(cancel, 'qq', account, [conv], rows, checkpoint={
                    'conversation_id': cid, 'next_offset': next_offset,
                    'complete': complete}):
                    return
                offset = next_offset
                if complete:
                    break
        if failed_conversations:
            self.update('qq', error=f'{failed_conversations} 个会话读取失败，其余会话已保留')
            return 'partial'

    def start_qq_history_import(self, values):
        self.require_capability('qq_history')
        raise ValueError('QQ 已改为本地数据库读取；请使用同步读取本机历史，不再使用在线漫游下载')

    def _legacy_start_qq_history_import(self, values):
        # Retained only for historical export compatibility, never routed by HTTP/MCP.
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
        def bounded_integer(name, default):
            value = values.get(name, default)
            if type(value) is int:
                return value
            if isinstance(value, str) and value.isascii() and value.isdecimal():
                return int(value)
            raise ValueError('消息数和查询预算必须是整数')

        max_messages = bounded_integer('max_messages', 50000)
        max_queries = bounded_integer('max_queries', 50000)
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
        if not self.capability_enabled('qq_history'):
            with self.lock:
                job['state'] = 'cancelled'
                job['progress'] = ''
            return
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
            rows = iter_export_messages(export_path)
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
                    if not self._qq_history_ingest(job['cancel'], 'qq', account, [conv], batch):
                        with self.lock:
                            job['state'] = 'cancelled'
                            job['progress'] = '已停止；已导入的记录仍保留在本机'
                        return
                    imported += len(batch)
                    batch.clear()
                    with self.lock:
                        job['message_count'] = imported
            if batch:
                if not self._qq_history_ingest(job['cancel'], 'qq', account, [conv], batch):
                    with self.lock:
                        job['state'] = 'cancelled'
                        job['progress'] = '已停止；已导入的记录仍保留在本机'
                    return
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
        if self.capability_enabled('sync'):
            self.start_sync()
        while not self.stop.wait(10):
            now = time.time()
            if self.capability_enabled('sync') and self.settings.get('auto_sync', True):
                for p, source in list(self.sources.items()):
                    interval = 30 if p == 'qq' and source['state'] != 'connected' else self.settings.get('sync_interval', 5) * 60
                    if now - self.attempts.get(p, 0) >= interval:
                        # Retry login-dependent sources without interrupting the logged-in client.
                        self.start_sync(p)

    def configure(self, platform, values):
        if platform == 'email':
            with self.sync_gate_lock, self.lock:
                if 'email' in self.jobs:
                    raise ValueError('邮件同步正在运行，请稍后修改配置')
                self.email_provider().configure(values)
            self.start_sync('email')
            return
        if platform not in NATIVE_PLATFORMS:
            raise ValueError('未知平台')
        if platform == 'wecom':
            from integrations.wecom import configure
            configure(data_dir=values.get('data_dir', ''), key=values.get('key', ''),
                      user_slot_id=values.get('user_slot_id', ''))
        elif platform == 'wechat':
            from integrations.wechat.provider import CONFIG, WeChatProvider
            config = read_json(CONFIG)
            directory = values.get('data_dir', '').strip()
            if directory:
                path = Path(directory)
                if not path.is_absolute() or not path.is_dir():
                    raise ValueError('请选择存在的本机绝对目录')
                config['data_dir'] = str(path)
            image_key = values.get('image_key', '')
            if not isinstance(image_key, str):
                raise ValueError('微信图片密钥格式无效')
            if image_key:
                provider = WeChatProvider(data_dir=config.get('data_dir'), account=config.get('account'))
                provider.set_image_key(image_key)
            save_json(CONFIG, config)
        else:
            config_path = self.storage / 'qq-local.json'
            with self.lock:
                config = read_json(config_path)
                for field in ('data_dir', 'key'):
                    if not isinstance(values.get(field, ''), str):
                        raise ValueError('QQ 本地配置参数无效')
                directory = values.get('data_dir', '').strip()
                if directory:
                    path = Path(directory)
                    if not path.is_absolute() or not path.is_dir():
                        raise ValueError('请选择存在的本机绝对目录')
                    config['data_dir'] = str(path.resolve())
                key = values.get('key', '')
                if key:
                    if not re.fullmatch(r'[\x20-\x7e]{16}|hex:[a-fA-F0-9]{64}', key):
                        raise ValueError('QQNT 密钥应为 16 位 ASCII 字符或 hex: 开头的 64 位十六进制派生密钥')
                    from integrations.qq.key_store import protect_key
                    config['key'] = protect_key(key)
                save_json(config_path, config)
        self.start_sync(platform)

    def resolve_media(self, params):
        platform = params.get('platform')
        if params.get('resource_kind') == 'wechat_source':
            allowed = {'resource_kind', 'platform', 'account_id', 'conversation_id',
                       'shard', 'local_id', 'image_tier', 'video_variant', 'media_type'}
            if (set(params) - allowed or platform != 'wechat' or
                    not all(isinstance(params.get(key), str) and params[key] and len(params[key]) <= 512
                            for key in ('account_id', 'conversation_id', 'shard'))):
                raise ValueError('微信源媒体身份参数无效')
            shard = params['shard']
            local_id = params.get('local_id')
            local_id_text = str(local_id) if type(local_id) is int else local_id
            if (Path(shard).name != shard or not isinstance(local_id_text, str) or
                    not local_id_text.isascii() or not local_id_text.isdecimal() or len(local_id_text) > 20):
                raise ValueError('微信源媒体身份参数无效')
            image_tier = params.get('image_tier', 'mid')
            video_variant = params.get('video_variant')
            media_type = params.get('media_type')
            if (image_tier not in ('best', 'mid', 'thumb', 'original') or
                    video_variant not in (None, 'poster') or media_type not in (None, 'sticker') or
                    (media_type == 'sticker' and (video_variant is not None or 'image_tier' in params))):
                raise ValueError('微信媒体预览选项无效')
            provider = self.providers.get('wechat')
            if provider is None:
                from integrations.wechat.provider import WeChatProvider
                provider = WeChatProvider()
                with self.lock:
                    self.providers['wechat'] = provider
            reader = getattr(provider, 'reader', None)
            current_account = str(getattr(reader, 'wxid', None) or
                                  getattr(reader, 'account', None) or '')
            if not reader or current_account != params['account_id']:
                raise ValueError('微信源媒体账号已变化，请刷新列表')
            if media_type == 'sticker':
                row = reader.get_message_row(params['conversation_id'], int(local_id_text), shard=shard)
                if row is None or (int(row.get('local_type') or 0) & 0xFFFFFFFF) not in (47, 11000):
                    raise ValueError('微信源媒体类型已变化，请刷新列表')
                emoji_md5 = message_emoji_md5(row.get('content'))
                path = provider.resolve_cached_emoji(emoji_md5) if emoji_md5 else None
                if not path:
                    return {'media_url': None, 'available': False,
                            'message': '本机没有该微信表情的已缓存文件'}
                mime = mimetypes.guess_type(path.name)[0]
                return {'media_url': '/local-media/' + self.register_media_path(path),
                        'available': True, 'mime_type': mime,
                        'download_name': 'wechat-sticker' + path.suffix.lower()}
            result = provider.media(params['conversation_id'], int(local_id_text), shard,
                                    image_tier=image_tier, video_variant=video_variant)
            path = result.get('path')
            if not result.get('available') or not path:
                return {'media_url': None, 'available': False,
                        'message': '本机缓存中没有可读取的此媒体'}
            mime = mimetypes.guess_type(Path(path).name)[0]
            return {'media_url': '/local-media/' + self.register_media_path(Path(path).resolve()),
                    'available': True, 'mime_type': mime,
                    'download_name': 'wechat-' + str(result.get('type') or 'media')}
        if 'image_tier' in params and platform != 'wechat':
            raise ValueError('图片质量选项仅适用于微信聊天图片')
        video_variant = params.get('video_variant')
        if video_variant is not None and (platform not in ('wechat', 'qq') or
                                          video_variant not in ('poster', 'thumbnail') or
                                          platform == 'qq' and video_variant != 'poster'):
            raise ValueError('视频预览选项无效')
        if params.get('resource_kind') == 'qq_cache':
            if platform != 'qq' or not self.capability_enabled('media'):
                raise ValueError('QQ 缓存资源不可用')
            provider = self.providers.get('qq')
            if provider is None:
                from integrations.qq.provider import QQProvider
                provider = QQProvider()
                with self.lock:
                    self.providers['qq'] = provider
            path, name = provider.resolve_cached_resource(
                str(params.get('resource_id') or ''), str(params.get('account_id') or ''))
            mime = mimetypes.guess_type(str(name or path.name))[0]
            if Path(str(name or path.name)).suffix.lower() in ('.heic', '.heif'):
                mime = 'image/heic'
            result = {'media_url': '/local-media/' + self.register_media_path(path)}
            if name:
                result['download_name'] = Path(str(name)).name
            if mime:
                result['mime_type'] = mime
            return result
        image_tier = params.get('image_tier', 'mid')
        if image_tier not in ('mid', 'best', 'thumb', 'original'):
            raise ValueError('图片质量选项无效')
        row = self.store.get_message(platform, params.get('account_id', ''), params.get('message_id', ''))
        if row is None or row['conversation_id'] != params.get('conversation_id'):
            raise ValueError('消息不存在')
        row = {**row.get('extra', {}), **row}
        if platform == 'wechat' and str(row.get('raw_type') or '') in ('47', '11000'):
            provider = self.providers.get('wechat')
            reader = getattr(provider, 'reader', None)
            reader_account = str(getattr(reader, 'wxid', None) or
                                 getattr(reader, 'account', None) or '')
            if provider and params.get('account_id') == reader_account:
                path = provider.resolve_cached_emoji(row.get('emoji_md5'))
                if path:
                    return {'media_url': '/local-media/' + self.register_media_path(path),
                            'available': True}
            return {'media_url': None, 'available': False,
                    'reason': 'unsupported_sticker_cache',
                    'message': '本机没有该微信表情的已缓存文件'}
        if 'image_tier' in params and (platform != 'wechat' or row.get('type') != 'image'
                                      or params.get('resource_index') is not None):
            raise ValueError('图片质量选项仅适用于微信聊天图片')
        if video_variant is not None and platform == 'wechat' and (row.get('type') != 'video' or
                                          params.get('resource_index') is not None):
            raise ValueError('视频预览选项仅适用于微信聊天视频')
        resource_index = params.get('resource_index')
        if platform in ('qq', 'wecom') and isinstance(resource_index, bool):
            raise ValueError('附件序号无效')
        qq_local = platform == 'qq' and row.get('local_database') is True
        if video_variant is not None and platform == 'qq' and not qq_local:
            raise ValueError('视频封面仅适用于 QQ 本地数据库视频')
        if platform == 'qq' and resource_index is not None and not qq_local:
            try:
                index = int(resource_index)
                resources = row.get('roaming_resources')
                if not isinstance(resources, list):
                    resources = row.get('media')
                if not isinstance(resources, list):
                    resources = []
                resource = resources[index] if 0 <= index < len(resources) else None
                media_root = self.storage.resolve()
                path = (media_root / str(resource.get('path') or '')).resolve() if resource else None
                if path is None or not path.is_relative_to(media_root) or not path.is_file():
                    raise ValueError('漫游媒体文件不存在')
            except (TypeError, ValueError, IndexError):
                raise ValueError('漫游媒体文件不存在') from None
            key = self.register_media_path(path)
            return {'media_url': '/local-media/' + key}
        provider = self.providers.get('qq_local' if qq_local else platform)
        if qq_local and provider is None:
            provider = self.qq_local_provider()
            with self.lock:
                self.providers['qq_local'] = provider
        if provider is None:
            return {'media_url': None, 'message': '请先同步该平台'}
        if platform == 'wechat':
            try:
                local_id = int(row.get('local_id'))
            except (TypeError, ValueError):
                local_id = None
            shard = row.get('shard')
            if local_id is None or not isinstance(shard, str) or not shard or Path(shard).name != shard:
                return {'media_url': None, 'available': False,
                        'reason': 'local_media_identity_unavailable',
                        'message': '本机索引缺少媒体定位信息，无法读取缓存'}
            media_args = {'image_tier': image_tier}
            if video_variant is not None:
                media_args['video_variant'] = video_variant
            result = provider.media(row['conversation_id'], local_id, shard, **media_args)
        elif platform == 'wecom':
            if resource_index is not None:
                try:
                    index = int(resource_index)
                except (TypeError, ValueError):
                    raise ValueError('附件序号无效') from None
                if not 0 <= index < 50:
                    raise ValueError('附件序号无效')
                result = provider.media(row['id'], resource_index=index)
            else:
                result = provider.media(row['id'])
        elif qq_local:
            if resource_index is not None:
                try:
                    if isinstance(resource_index, bool):
                        raise ValueError
                    resource_index = int(resource_index)
                    if not 0 <= resource_index < 101:
                        raise ValueError
                except (ValueError, TypeError):
                    raise ValueError('附件序号无效') from None
            media_args = {'resource_index': resource_index}
            if video_variant is not None:
                entries = row.get('media') or []
                index = resource_index if resource_index is not None else 0
                if (not isinstance(entries, list) or not 0 <= index < len(entries) or
                        not isinstance(entries[index], dict) or
                        (entries[index].get('kind') or entries[index].get('type')) != 'video'):
                    raise ValueError('视频封面仅适用于 QQ 本地数据库视频')
                media_args['video_variant'] = video_variant
            result = provider.media(row, **media_args)
        else:
            result = provider.media(row)
        if (platform == 'wechat' and row['type'] == 'voice' and result.get('reason')
                and result.get('playable') is not False):
            voice_messages = {
                'ok': '本机已缓存语音',
                'no_server_id': '消息缺少语音索引，无法定位本机缓存',
                'session_not_in_media_index': '本机语音索引中没有此会话',
                'audio_not_downloaded': '微信本机尚未缓存这段语音',
                'audio_missing_from_media_db': '语音索引显示已下载，但本机媒体库中缺少音频',
                'voice_message_not_found': '本机语音记录已不可用，请重新同步',
            }
            return {'media_url': None,
                    'message': voice_messages.get(result['reason'], '本机语音暂不可用'),
                    'reason': result['reason'], 'bytes': result.get('bytes', 0)}
        path = result.get('path')
        if platform == 'qq' and row['type'] == 'voice' and result.get('available') and path and Path(path).is_file():
            with Path(path).open('rb') as audio:
                header = audio.read(12)
            if header.startswith((b'#!SILK', b'\x02#!SILK')):
                from integrations.wechat.voice import voice_media_result
                result = voice_media_result(path, self.storage / 'media' / 'decoded-voice')
                path = result.get('path')
        silk_fallback = result.get('playable') is False and row['type'] == 'voice'
        if silk_fallback:
            path = result.get('original_path') or path
            if platform == 'wechat':
                from integrations.wechat.provider import RUNTIME
                media_root = Path(RUNTIME) / 'media'
            elif platform == 'wecom':
                from integrations.wecom.engine import OUT
                media_root = Path(OUT)
            elif platform == 'qq':
                if qq_local:
                    media_root = self.storage / 'integrations' / 'qq-local' / 'media'
                else:
                    from integrations.qq.provider import QQProvider
                    media_root = QQProvider._media_cache_dir()
            else:
                media_root = None
            try:
                source = Path(path) if path else None
                if source is None or source.is_symlink():
                    raise ValueError
                candidate = source.resolve(strict=True)
                root = media_root.resolve(strict=True) if media_root else None
                if (candidate is None or root is None or not candidate.is_file()
                        or not candidate.is_relative_to(root)):
                    raise ValueError
                with candidate.open('rb') as audio:
                    if not audio.read(12).startswith((b'#!SILK', b'\x02#!SILK')):
                        raise ValueError
                path = candidate
            except (OSError, RuntimeError, ValueError, TypeError):
                return {'media_url': None, 'message': '本机语音文件不可用',
                        'reason': 'unsafe_media_path'}
        if platform == 'wecom' and path and not Path(path).is_absolute():
            from integrations.wecom.engine import OUT
            path = Path(OUT) / path
        if not result.get('available') or not path or not Path(path).is_file():
            unavailable = {'media_url': None,
                           'message': result.get('message') or '本机未缓存此媒体，或暂不支持此格式'}
            if result.get('reason'):
                unavailable['reason'] = result['reason']
            return unavailable
        key = self.register_media_path(Path(path).resolve())
        resolved = {'media_url': '/local-media/' + key}
        if qq_local and video_variant == 'poster':
            resolved.update(video_variant='poster', preview_only=True,
                            mime_type=result.get('content_type'))
        if silk_fallback:
            resolved['mime_type'] = 'audio/silk'
            resolved['download_name'] = 'voice.silk'
        if platform == 'wechat' and row['type'] == 'image' and Path(path).suffix.lower() == '.wxgf':
            resolved['mime_type'] = 'application/x-wechat-wxgf'
        if platform == 'wecom' and result.get('name'):
            resolved['name'] = result['name']
        return resolved

    def register_media_path(self, path):
        key = secrets.token_urlsafe(32)
        with self.lock:
            self.media_paths[key] = path
            while len(self.media_paths) > MEDIA_TOKEN_LIMIT:
                self.media_paths.pop(next(iter(self.media_paths)))
        return key

    def conversation_members(self, params):
        platform = params.get('platform', '')
        account_id = str(params.get('account_id') or '')
        conversation_id = str(params.get('conversation_id') or '')
        if platform not in NATIVE_PLATFORMS or not account_id or not conversation_id:
            raise ValueError('平台、账号或会话标识无效')
        indexed = self.store.get_conversation(platform, account_id, conversation_id)
        if not indexed or indexed['kind'] != 'group':
            raise ValueError('群聊不存在或不属于指定账号')
        try:
            limit = min(max(int(params.get('limit', 100)), 1), 1000)
            offset = min(max(int(params.get('offset', 0)), 0), 1_000_000)
        except (TypeError, ValueError):
            raise ValueError('分页参数无效') from None
        query = str(params.get('q') or '').strip()[:256]

        provider = self.providers.get(platform)
        if provider is None:
            # A stdio client has its own Hub; open the configured reader on demand.
            if platform == 'wechat':
                from integrations.wechat.provider import WeChatProvider
                provider = WeChatProvider()
            elif platform == 'wecom':
                from integrations.wecom.adapter import WeComAdapter
                provider = WeComAdapter(voice_cache_dir=self.storage / 'media' / 'decoded-voice')
            else:
                from integrations.qq.provider import QQProvider
                provider = QQProvider()
            with self.lock:
                provider = self.providers.setdefault(platform, provider)
        if platform == 'wechat':
            reader = getattr(provider, 'reader', None)
            if reader is None:
                raise RuntimeError('请先连接并同步微信数据源')
            source_account = getattr(reader, 'wxid', None)
            if not isinstance(source_account, str) or not source_account:
                source_account = str(getattr(reader, 'account', '') or '')
            if source_account and source_account != account_id:
                raise ValueError('微信数据源账号与会话不匹配')
            rows = reader.get_group_members(conversation_id)
            needle = query.casefold()
            if needle:
                rows = [row for row in rows if any(
                    needle in str(row.get(field) or '').casefold()
                    for field in ('username', 'nick_name', 'remark'))]
            normalized = [{
                'id': str(row.get('username') or ''),
                'name': str(row.get('remark') or row.get('nick_name') or row.get('username') or ''),
                'nickname': str(row.get('nick_name') or ''),
                'remark': str(row.get('remark') or ''),
                'is_owner': bool(row.get('is_owner')),
                'role': 'owner' if row.get('is_owner') else 'member',
            } for row in rows if isinstance(row, dict)]
            normalized = [row for row in normalized if row['id']]
            total = len(normalized)
            items = normalized[offset:offset + limit]
            avatars = self.store.contact_avatars(
                platform, account_id, [row['id'] for row in items])
            for row in items:
                if row['id'] in avatars:
                    row['avatar_url'] = avatars[row['id']]
        else:
            source_limit = min(offset + limit, 1000)
            result = provider.members(conversation_id, account_id=account_id,
                                      limit=max(1, source_limit), query=query)
            total = int(result.get('total') or 0)
            items = result.get('items') if isinstance(result.get('items'), list) else []
            items = items[offset:offset + limit]
        return {'items': items, 'total': total, 'limit': limit, 'offset': offset}

    def voice_transcription_status(self):
        return self.voice_transcriber.status()

    def transcribe_voice(self, params):
        allowed = {'platform', 'account_id', 'message_id', 'conversation_id'}
        if not isinstance(params, dict) or set(params) != allowed:
            raise ValueError('语音消息身份参数无效')
        platform, account_id = params['platform'], params['account_id']
        message_id, conversation_id = params['message_id'], params['conversation_id']
        if (platform not in PLATFORMS or not all(isinstance(value, str) and value and len(value) <= 256
                                                 for value in (account_id, message_id, conversation_id))):
            raise ValueError('语音消息身份参数无效')
        row = self.store.get_message(platform, account_id, message_id)
        if row is None or row['conversation_id'] != conversation_id:
            raise ValueError('语音消息不存在')
        if row['type'] != 'voice':
            raise ValueError('只能转写语音消息')
        resolved = self.resolve_media(params)
        token = (resolved.get('media_url') or '').rsplit('/', 1)[-1]
        if not token:
            return {'available': False, 'reason': 'audio_not_cached',
                    'message': resolved.get('message') or '本机未缓存此语音。'}
        with self.lock:
            path = self.media_paths.pop(token, None)
        if path is None:
            return {'available': False, 'reason': 'audio_not_cached', 'message': '本机未缓存此语音。'}
        try:
            source = Path(path)
            if source.is_symlink() or not source.is_file() or source.stat().st_size > 32 * 1024 * 1024:
                return {'available': False, 'reason': 'audio_invalid', 'message': '本机语音文件无效或超过 32 MiB。'}
            audio = source.read_bytes()
        except OSError:
            return {'available': False, 'reason': 'audio_not_cached', 'message': '本机未缓存此语音。'}
        return self.voice_transcriber.transcribe(audio)

    def resolve_resource(self, params):
        if 'image_tier' in params and (params.get('resource_kind') or 'message') not in ('message', 'wechat_source'):
            raise ValueError('图片质量选项仅适用于微信聊天图片')
        if params.get('resource_kind') == 'qq_cache':
            self.require_capability('media')
            provider = self.providers.get('qq')
            if provider is None:
                from integrations.qq.provider import QQProvider
                provider = QQProvider()
                with self.lock:
                    self.providers['qq'] = provider
            path, name = provider.resolve_cached_resource(
                str(params.get('resource_id') or ''), str(params.get('account_id') or ''))
            return path, name
        if params.get('resource_kind') == 'wechat_source':
            self.require_capability('media')
            resolved = self.resolve_media(params)
            token = (resolved.get('media_url') or '').rsplit('/', 1)[-1]
            if not token:
                raise FileNotFoundError
            with self.lock:
                path = self.media_paths.pop(token, None)
            if path is None or not Path(path).is_file():
                raise FileNotFoundError
            return Path(path), resolved.get('download_name') or 'wechat-media'
        if params.get('resource_kind') == 'moment':
            if not self.capability_enabled('moments'):
                raise ValueError('朋友圈功能已关闭')
            account = str(params['account_id'])
            locator = self.store.moment_media_locator(account, params['post_id'],
                                                       params['media_index'], params.get('variant', 'original'))
            if not locator:
                raise FileNotFoundError
            provider = self.providers.get('wechat')
            if provider is None:
                from integrations.wechat.provider import WeChatProvider
                provider = WeChatProvider()
                with self.lock:
                    self.providers['wechat'] = provider
            cache_dir = getattr(provider, 'sns_cache_dir', None)
            if not cache_dir:
                raise FileNotFoundError
            root = Path(cache_dir).expanduser().resolve(strict=True)
            relative = Path(str(locator))
            if relative.is_absolute() or any(part in ('', '.', '..') for part in relative.parts):
                raise ValueError('朋友圈缓存路径无效')
            path = root
            for part in relative.parts:
                path = path / part
                if path.is_symlink():
                    raise ValueError('朋友圈缓存路径无效')
            resolved = path.resolve(strict=True)
            if not resolved.is_file() or not resolved.is_relative_to(root):
                raise ValueError('朋友圈缓存路径无效')
            return resolved, resolved.name
        row = self.store.get_message(params['platform'], params['account_id'], params['message_id'])
        if not row or row.get('conversation_id') != params['conversation_id']:
            raise ValueError('消息不存在')
        row = {**row.get('extra', {}), **row}
        if params.get('_local_only') and params['platform'] == 'qq':
            provider = self.providers.get('qq')
            if provider is None:
                provider = QQProvider()
                with self.lock:
                    self.providers['qq'] = provider
            resolved = provider.cached_media(row, params.get('resource_index'))
            if not resolved.get('available') or not resolved.get('path'):
                raise FileNotFoundError('QQ 媒体未在本机缓存')
            return Path(resolved['path']), resolved.get('name') or row.get('file_name') or 'qq-media'
        if params['platform'] == 'wechat' and str(row.get('raw_type') or '') in ('47', '11000'):
            if not row.get('emoji_md5'):
                raise FileNotFoundError('本机没有该微信表情的已缓存文件')
        if 'image_tier' in params and (params['platform'] != 'wechat' or row.get('type') != 'image'
                                      or params.get('resource_index') is not None):
            raise ValueError('图片质量选项仅适用于微信聊天图片')
        if params['platform'] == 'qq' and params.get('resource_index') is not None:
            extra = row.get('extra') if isinstance(row.get('extra'), dict) else {}
            resources = extra.get('roaming_resources')
            if not isinstance(resources, list):
                resources = extra.get('media')
            if not isinstance(resources, list):
                resources = []
            try:
                resource = resources[params['resource_index']]
            except (IndexError, TypeError):
                raise ValueError('漫游附件不存在') from None
            resolved = self.resolve_media(params)
            token = (resolved.get('media_url') or '').rsplit('/', 1)[-1]
            if not token:
                raise FileNotFoundError
            with self.lock:
                path = self.media_paths.pop(token, None)
            if path is None or not Path(path).is_file():
                raise FileNotFoundError
            return Path(path), resource.get('name') or f"attachment-{row['id']}"
        wecom_index = params.get('resource_index') if params['platform'] == 'wecom' else None
        if wecom_index is not None:
            try:
                index = int(wecom_index)
                attachments = row.get('extra', {}).get('attachments') or []
                if not isinstance(attachments, list) or not 0 <= index < len(attachments):
                    raise ValueError
                if not isinstance(attachments[index], dict):
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError('附件序号无效') from None
        elif row.get('type') not in ('image', 'video', 'voice', 'file'):
            raise ValueError('此消息没有可导出的附件')
        resolve_params = dict(params)
        if params['platform'] == 'wechat' and row.get('type') == 'image':
            resolve_params.setdefault('image_tier', 'best')
        resolved = self.resolve_media(resolve_params)
        token = (resolved.get('media_url') or '').rsplit('/', 1)[-1]
        if not token:
            raise FileNotFoundError
        with self.lock:
            path = self.media_paths.pop(token, None)
        if path is None or not Path(path).is_file():
            raise FileNotFoundError
        name = resolved.get('name') or row.get('file_name') or f"{row.get('type') or 'attachment'}-{row['id']}"
        return Path(path), name

    def read_cached_image(self, message, resource_index=None):
        """Return one bounded attachment from an already-local cache for MCP."""
        platform = message.get('platform')
        media_type = message.get('type')
        if platform not in NATIVE_PLATFORMS or media_type not in ('image', 'video', 'voice', 'file'):
            raise ValueError('仅支持读取已索引的图片、视频、语音或文件附件')
        indexed = (self.store.get_message('qq', message.get('account_id', ''),
                                          message.get('id', ''))
                   if platform == 'qq' and message.get('resource_kind') != 'qq_cache' else None)
        qq_local = bool(indexed and indexed.get('extra', {}).get('local_database') is True)
        provider_key = 'qq_local' if qq_local else platform
        provider = self.providers.get(provider_key)
        if provider is None:
            if platform == 'wechat':
                from integrations.wechat.provider import WeChatProvider
                provider = WeChatProvider()
            elif platform == 'wecom':
                from integrations.wecom.adapter import WeComAdapter
                provider = WeComAdapter(voice_cache_dir=self.storage / 'media' / 'decoded-voice')
                provider.refresh()
            elif qq_local:
                provider = self.qq_local_provider()
            else:
                from integrations.qq.provider import QQProvider
                provider = QQProvider()
            with self.lock:
                self.providers[provider_key] = provider
        if platform == 'qq' and not qq_local:
            if message.get('resource_kind') == 'qq_cache':
                resource = provider.cached_resource_metadata(
                    str(message.get('resource_id') or ''), str(message.get('account_id') or ''))
                resource_type = resource['type']
                expected_type = {'image': 'image', 'video': 'video', 'audio': 'voice', 'file': 'file'}.get(resource_type)
                if media_type != expected_type:
                    raise ValueError('QQ 缓存资源类型不匹配')
                message['file_name'] = resource['name']
                path, _name = provider.resolve_cached_resource(
                    str(message.get('resource_id') or ''), str(message.get('account_id') or ''))
                resolved = {'available': True, 'path': path}
            else:
                resolved = provider.cached_media(message, resource_index=resource_index)
            if not resolved.get('available') or not resolved.get('path'):
                raise ValueError('本机没有可读取的图片缓存')
            path = Path(resolved['path'])
        else:
            if message.get('resource_kind') == 'wechat_source':
                params = {'resource_kind': 'wechat_source', 'platform': 'wechat',
                          'account_id': message.get('account_id'),
                          'conversation_id': message.get('conversation_id'),
                          'shard': message.get('shard'), 'local_id': message.get('local_id')}
                source_type = message.get('source_type')
                if source_type == 'sticker':
                    params['media_type'] = 'sticker'
            else:
                params = {'platform': platform, 'account_id': message.get('account_id'),
                          'message_id': message.get('id'), 'conversation_id': message.get('conversation_id')}
            if resource_index is not None:
                params['resource_index'] = resource_index
            resolved = self.resolve_media(params)
            media_url = resolved.get('media_url')
            if not media_url:
                raise ValueError(resolved.get('message') or '本机没有可读取的图片缓存')
            token = media_url.rsplit('/', 1)[-1]
            with self.lock:
                path = self.media_paths.pop(token, None)
            if path is None:
                raise ValueError('本机图片缓存不可用')
        source_path = Path(path)
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError('图片不在本机缓存目录中')
        path = source_path.resolve()
        if platform == 'wechat':
            from integrations.wechat.provider import RUNTIME
            cache_roots = [Path(RUNTIME) / 'media']
            provider = self.providers.get('wechat')
            reader = getattr(provider, 'reader', None)
            reader_account = str(getattr(reader, 'wxid', None) or
                                 getattr(reader, 'account', None) or '')
            if provider and message.get('account_id') == reader_account:
                cache_roots.append(provider.emoji_cache_account_dir())
        elif platform == 'wecom':
            from integrations.wecom.engine import OUT
            cache_roots = [Path(OUT)]
        else:
            from integrations.qq.provider import QQProvider
            cache_roots = [QQProvider._media_cache_dir(), self.storage / 'qq-roaming',
                           self.storage / 'integrations' / 'qq-local' / 'media']
        if not any(path.is_relative_to(root.resolve()) for root in cache_roots):
            raise ValueError('图片不在本机缓存目录中')
        payload = _read_cached_snapshot(path, MCP_MEDIA_MAX_BYTES)
        signatures = {
            'image/jpeg': payload.startswith(b'\xff\xd8\xff'),
            'image/png': payload.startswith(b'\x89PNG\r\n\x1a\n'),
            'image/gif': payload.startswith((b'GIF87a', b'GIF89a')),
            'image/webp': payload.startswith(b'RIFF') and payload[8:12] == b'WEBP',
            'image/bmp': payload.startswith(b'BM'),
            'image/tiff': payload.startswith((b'II*\x00', b'MM\x00*', b'II+\x00', b'MM\x00+')),
            'image/avif': False,
            'image/heic': False,
            'application/x-wechat-wxgf': platform == 'wechat' and media_type == 'image' and
                                         payload.startswith(b'wxgf'),
            'audio/mp4': (len(payload) >= 12 and payload[4:8] == b'ftyp' and
                          payload[8:12] in (b'M4A ', b'M4B ', b'M4P ', b'F4A ', b'F4B ')),
            'audio/x-ms-wma': (platform == 'qq' and message.get('resource_kind') == 'qq_cache' and
                               media_type == 'voice' and
                               Path(str(message.get('file_name') or '')).suffix.lower() == '.wma' and
                               payload.startswith(bytes.fromhex('3026b2758e66cf11a6d900aa0062ce6c'))),
            'video/mp4': len(payload) >= 12 and payload[4:8] == b'ftyp',
            'video/webm': payload.startswith(b'\x1a\x45\xdf\xa3'),
            'audio/wav': payload.startswith(b'RIFF') and payload[8:12] == b'WAVE',
            'audio/ogg': payload.startswith(b'OggS'),
            'audio/aac': len(payload) >= 2 and payload[0] == 0xff and payload[1] & 0xf6 == 0xf0,
            'audio/mpeg': payload.startswith(b'ID3') or (len(payload) > 1 and payload[0] == 0xff and payload[1] & 0xe0 == 0xe0),
            'audio/flac': payload.startswith(b'fLaC'),
            'audio/amr': payload.startswith((b'#!AMR\n', b'#!AMR-WB\n')),
            'audio/silk': payload.startswith((b'#!SILK_V3', b'\x02#!SILK_V3')),
            'application/pdf': payload.startswith(b'%PDF-'),
            'application/zip': payload.startswith(b'PK\x03\x04'),
            'application/rtf': payload.startswith(b'{\\rtf'),
            'application/x-ole-storage': payload.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'),
        }
        if len(payload) >= 16 and payload[4:8] == b'ftyp':
            box_size = int.from_bytes(payload[:4], 'big')
            if box_size >= 16 and box_size <= len(payload) and (box_size - 16) % 4 == 0:
                brands = {payload[8:12]}
                brands.update(payload[offset:offset + 4] for offset in range(16, box_size, 4))
                signatures['image/avif'] = bool(brands.intersection((b'avif', b'avis')))
                signatures['image/heic'] = bool(brands.intersection(
                    (b'heic', b'heix', b'hevc', b'hevx', b'mif1', b'msf1')))
        mime = next((kind for kind, valid in signatures.items() if valid), None)
        if media_type == 'image' and not (mime and
                                           (mime.startswith('image/') or mime == 'application/x-wechat-wxgf')):
            raise ValueError('本机缓存内容不是受支持的图片格式')
        if media_type == 'video' and not (mime and mime.startswith('video/')):
            raise ValueError('本机缓存内容不是受支持的视频格式')
        if media_type == 'voice' and not (mime and mime.startswith('audio/')):
            raise ValueError('本机缓存内容不是受支持的音频格式')
        if media_type == 'file':
            mime = mime or 'application/octet-stream'
        return payload, mime

    def read_wechat_source_media(self, identity):
        """Read one exact source-only WeChat attachment through the shared cache verifier."""
        if not isinstance(identity, dict):
            raise ValueError('微信源媒体身份无效')
        account_id = identity.get('account_id')
        conversation_id = identity.get('conversation_id')
        shard = identity.get('shard')
        local_id = identity.get('local_id')
        media_type = identity.get('type')
        if (identity.get('platform') != 'wechat' or
                not all(isinstance(value, str) and value and len(value) <= maximum
                        for value, maximum in ((account_id, 256), (conversation_id, 512),
                                               (shard, 512), (local_id, 20))) or
                not local_id.isascii() or not local_id.isdecimal() or
                Path(shard).name != shard or
                media_type not in ('image', 'sticker', 'video', 'voice', 'file')):
            raise ValueError('微信源媒体身份无效')
        provider = self.providers.get('wechat')
        if provider is None:
            from integrations.wechat.provider import WeChatProvider
            provider = WeChatProvider()
            with self.lock:
                self.providers['wechat'] = provider
        reader = getattr(provider, 'reader', None)
        reader_account = str(getattr(reader, 'wxid', None) or getattr(reader, 'account', None) or '')
        if reader is None or reader_account != account_id:
            raise ValueError('微信源媒体账号已变化，请刷新列表')
        row = reader.get_message_row(conversation_id, int(local_id), shard=shard)
        if row is None:
            raise ValueError('微信源媒体记录已变化，请刷新列表')
        local_type = int(row.get('local_type') or 0) & 0xFFFFFFFF
        actual_type = {3: 'image', 34: 'voice', 43: 'video', 47: 'sticker',
                       11000: 'sticker', 49: 'file'}.get(local_type)
        if actual_type != media_type:
            raise ValueError('微信源媒体类型已变化，请刷新列表')
        message = {'platform': 'wechat', 'account_id': account_id,
                   'conversation_id': conversation_id, 'id': '',
                   'type': 'image' if media_type == 'sticker' else media_type,
                   'resource_kind': 'wechat_source', 'source_type': media_type,
                   'shard': shard, 'local_id': local_id}
        return self.read_cached_image(message)

    def list_qq_cache_resources(self, *, kind='', query='', offset=0, limit=60,
                                start_ms=None, end_ms=None):
        self.require_capability('media')
        provider = self.providers.get('qq')
        if provider is None:
            from integrations.qq.provider import QQProvider
            provider = QQProvider()
            with self.lock:
                self.providers['qq'] = provider
        return provider.cached_resources(kind=kind, query=query, offset=offset, limit=limit,
                                         start_ms=start_ms, end_ms=end_ms)

    def _qq_forward_source(self, account_id, conversation_id, message_id):
        self.require_capability('media')
        row = self.store.get_message('qq', account_id, message_id)
        if not row or row.get('conversation_id') != conversation_id:
            raise ValueError('索引中的 QQ 消息不存在或会话不匹配')
        row = {**row, 'platform': 'qq', 'account_id': account_id}
        provider = self.providers.get('qq')
        if provider is None:
            from integrations.qq.provider import QQProvider
            provider = QQProvider()
            with self.lock:
                self.providers['qq'] = provider
        return row, provider

    def list_qq_forward_attachments(self, account_id, conversation_id, message_id):
        row, provider = self._qq_forward_source(account_id, conversation_id, message_id)
        expanded = provider.expand_forward_resources(row, account_id, self.storage)
        items = []
        for index, item in enumerate(expanded.get('items', [])):
            if not isinstance(item, dict):
                continue
            name = Path(str(item.get('name') or 'attachment').replace('\\', '/')).name
            name = ''.join(char for char in name if ord(char) >= 32)[:200] or 'attachment'
            items.append({'resource_index': index, 'resource_handle': item.get('handle'),
                          'type': item.get('type'), 'file_name': name,
                          'size': item.get('size'), 'mime_type': item.get('mime_type')})
        return {'items': items, 'truncated': bool(expanded.get('truncated'))}

    def read_qq_forward_attachment(self, account_id, conversation_id, message_id, resource_handle):
        _row, provider = self._qq_forward_source(account_id, conversation_id, message_id)
        parent_id = str(message_id)[len(str(conversation_id)) + 1:]
        path, mime = provider.download_forward_resource(
            resource_handle, self.storage, account_id=account_id, peer=conversation_id,
            parent_id=parent_id, max_bytes=8 * 1024 * 1024)
        path = Path(path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('附件超过 MCP 单次读取的 8 MiB 限制或缓存无效')
        with path.open('rb') as source:
            payload = source.read(8 * 1024 * 1024 + 1)
        if len(payload) > 8 * 1024 * 1024:
            raise ValueError('附件超过 MCP 单次读取的 8 MiB 限制')
        return payload, mime, {'file_name': path.name,
                               'bytes': len(payload)}

    def search_moments(self, *, limit=50, offset=0, username='', keyword='', start='', end='', account_id=''):
        provider = self.providers.get('wechat')
        if provider is None:
            try:
                from integrations.wechat.provider import WeChatProvider
                provider = WeChatProvider()
            except Exception:
                provider = None
            if provider is not None:
                with self.lock:
                    self.providers['wechat'] = provider

        reader_account = (str(getattr(getattr(provider, 'reader', None), 'wxid', None) or
                              getattr(getattr(provider, 'reader', None), 'account', None) or 'local')
                          if provider is not None else '')
        catalog_accounts = self.store.moment_accounts()
        account = str(account_id or reader_account or '')
        if not account:
            if len(catalog_accounts) == 1:
                account = catalog_accounts[0]
            elif len(catalog_accounts) > 1:
                return {'available': False, 'reason': 'moments_account_required',
                        'accounts': catalog_accounts, 'items': [], 'total': 0}
            else:
                return {'available': False, 'reason': 'wechat_source_unavailable', 'items': [], 'total': 0}
        if account_id and account not in catalog_accounts:
            return {'available': False, 'reason': 'moments_not_indexed', 'account_id': account,
                    'accounts': catalog_accounts, 'items': [], 'total': 0}
        requested_limit = min(max(int(limit), 1), 100_000)
        first_page = self.store.moments(account, limit=min(requested_limit, 500), offset=offset,
                                        username=username, keyword=keyword, start=start, end=end)
        if not first_page.get('catalogued'):
            return {'available': False, 'reason': 'moments_not_indexed', 'account_id': account,
                    'accounts': catalog_accounts, 'items': [], 'total': 0}
        result = {**first_page, 'account_id': account, 'accounts': catalog_accounts}
        if requested_limit > 500:
            items = list(first_page['items'])
            while len(items) < requested_limit and offset + len(items) < first_page['total']:
                page = self.store.moments(account, limit=min(500, requested_limit - len(items)),
                    offset=offset + len(items), username=username, keyword=keyword, start=start, end=end)
                items.extend(page['items'])
                if not page['items']:
                    break
            result = {**first_page, 'items': items}
        if result.get('available'):
            for post in result.get('items', []):
                post['avatar_url'] = (self.store.contact_avatar(
                    'wechat', account, post.get('username', '')) if post.get('username') else None)

            same_reader_account = provider is not None and account == reader_account
            configured_cache = getattr(provider, 'sns_cache_dir', None) if same_reader_account else None
            try:
                cache_root = Path(configured_cache).expanduser().resolve(strict=True) if configured_cache else None
            except (OSError, RuntimeError):
                cache_root = None

            def register_cached_moment_media(media):
                local_path = media.pop('_local_path', None)
                cache_relpath = media.pop('_cache_relpath', None)
                media_url = None
                if (local_path or cache_relpath) and cache_root:
                    if cache_relpath:
                        relative = Path(str(cache_relpath))
                        if relative.is_absolute() or '..' in relative.parts:
                            media['media_url'] = None
                            media['local_available'] = False
                            return
                        candidate = cache_root / relative
                    else:
                        candidate = Path(local_path)
                    try:
                        resolved = candidate.resolve(strict=True)
                        if (not candidate.is_symlink() and resolved.is_file() and
                                resolved.is_relative_to(cache_root)):
                            media_url = self._issue_moment_media_url(resolved, cache_root,
                                                                     media.get('mime_type'))
                    except (OSError, RuntimeError, ValueError):
                        pass
                media['media_url'] = media_url
                media['local_available'] = bool(media_url)
                for field in ('url', 'thumb', 'token', 'thumbToken', 'key', 'encIdx', 'md5', '_local_path'):
                    media.pop(field, None)

            for post in result.get('items', []):
                for media in post.get('media', []):
                    register_cached_moment_media(media)
                    live_photo = media.get('livePhoto')
                    if isinstance(live_photo, dict):
                        register_cached_moment_media(live_photo)
        return result

    def list_wechat_source_media(self, params):
        account_id = params.get('account_id', '')
        conversation_id = params.get('conversation_id', '')
        media_type = params.get('type', '')
        if (params.get('platform') != 'wechat' or not account_id or
                not isinstance(account_id, str) or not isinstance(conversation_id, str) or
                len(account_id) > 256 or len(conversation_id) > 512):
            raise ValueError('请先选择准确的微信账号')
        if params.get('q'):
            raise ValueError('微信源媒体视图按媒体类型和日期筛选')
        if media_type not in ('', 'image', 'video', 'voice', 'sticker', 'file'):
            raise ValueError('此来源视图不支持该媒体类型')
        try:
            offset, limit = int(params.get('offset', 0)), int(params.get('limit', 60))
        except (TypeError, ValueError):
            raise ValueError('媒体分页参数无效') from None
        if offset < 0 or not 1 <= limit <= 60:
            raise ValueError('媒体分页参数无效')
        encoded_cursor = params.get('cursor')
        if encoded_cursor and offset:
            raise ValueError('媒体分页参数无效')
        start = parse_bound(params.get('start'))
        end = parse_bound(params.get('end'), end=True)
        if start is not None and end is not None and start > end:
            raise ValueError('开始日期不能晚于结束日期')
        provider = self.providers.get('wechat')
        if provider is None:
            from integrations.wechat.provider import WeChatProvider
            provider = WeChatProvider()
            with self.lock:
                self.providers['wechat'] = provider
        reader = getattr(provider, 'reader', None)
        current_account = str(getattr(reader, 'wxid', None) or
                              getattr(reader, 'account', None) or '')
        if not reader or current_account != account_id:
            raise ValueError('微信源媒体账号已变化，请先同步并重新选择账号')
        if not conversation_id:
            return self._list_wechat_account_source_media(
                params, provider, account_id, media_type, start, end, limit, offset,
                encoded_cursor)
        selected_types = (provider._SOURCE_MEDIA_TYPES.get(media_type) if media_type else
                          set().union(*provider._SOURCE_MEDIA_TYPES.values()))
        raw_types = sorted({str(int(value) & 0xFFFFFFFF) for value in selected_types})
        scope = hashlib.sha256(json.dumps(
            [account_id, conversation_id, media_type, start, end],
            ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()[:16]
        after_cursor, total = None, None
        if encoded_cursor:
            if (not isinstance(encoded_cursor, str) or
                    not re.fullmatch(r'[A-Za-z0-9_-]{1,1024}', encoded_cursor)):
                raise ValueError('媒体游标无效')
            try:
                decoded = json.loads(base64.urlsafe_b64decode(
                    encoded_cursor + '=' * (-len(encoded_cursor) % 4)))
                after_cursor, total, cursor_scope = decoded
                if (cursor_scope != scope or type(total) is not int or total < 0 or
                        not isinstance(after_cursor, dict)):
                    raise ValueError
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError('媒体游标无效') from None
            page = provider.source_media(conversation_id, media_type=media_type,
                                         start_time=start, end_time=end, offset=0,
                                         limit=500, after_cursor=after_cursor,
                                         include_total=False)
        else:
            page = provider.source_media(conversation_id, media_type=media_type,
                                         start_time=start, end_time=end, offset=0, limit=500)
            raw_total = int(page.get('total') or 0)
            indexed_total = self.store.wechat_indexed_source_media_count(
                account_id, conversation_id, raw_types, start, end)
            total = max(0, raw_total - indexed_total)
        if offset >= total:
            return {'items': [], 'total': total, 'limit': limit, 'offset': offset,
                    'next_cursor': None}
        items, remaining_skip = [], offset
        last_returned_cursor, has_more = after_cursor, False
        while True:
            candidates = page.get('items') or []
            if not candidates:
                break
            indexed = self.store.wechat_source_media_ids(
                account_id, conversation_id, [row['id'] for row in candidates])
            for row in candidates:
                row_cursor = row.pop('_source_cursor', None)
                if row['id'] in indexed:
                    continue
                if remaining_skip:
                    remaining_skip -= 1
                    last_returned_cursor = row_cursor or last_returned_cursor
                    continue
                if len(items) >= limit:
                    has_more = True
                    break
                items.append(row)
                last_returned_cursor = row_cursor or last_returned_cursor
            if has_more:
                break
            next_source_cursor = page.get('next_cursor')
            if next_source_cursor is None or next_source_cursor == after_cursor:
                break
            after_cursor = next_source_cursor
            page = provider.source_media(conversation_id, media_type=media_type,
                                         start_time=start, end_time=end,
                                         offset=0, limit=500, after_cursor=after_cursor,
                                         include_total=False)
        next_cursor = None
        if has_more and last_returned_cursor is not None:
            payload = json.dumps([last_returned_cursor, total, scope],
                                 ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            next_cursor = base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')
        conversation = self.store.get_conversation('wechat', account_id, conversation_id)
        title = conversation.get('title') if conversation else provider.reader.get_nickname(conversation_id)
        return {'items': [{**row, 'account_id': account_id, 'conversation_title': title,
                           'conversation_kind': conversation.get('kind') if conversation else
                           ('group' if conversation_id.endswith('@chatroom') else 'direct')}
                          for row in items], 'total': total, 'limit': limit, 'offset': offset,
                'next_cursor': next_cursor}

    def _list_wechat_account_source_media(self, params, provider, account_id,
                                           media_type, start, end, limit, offset,
                                           encoded_cursor):
        if offset:
            raise ValueError('账号级微信源媒体请使用游标分页')
        reader = provider.reader
        sessions = reader.get_sessions(limit=100001)
        if len(sessions) > 100000:
            raise ValueError('微信会话数量超过账号级媒体浏览上限')
        session_ids = sorted({str(row.get('username') or '') for row in sessions
                              if row.get('username') and
                              (not params.get('conversation_id') or
                               str(row['username']) == params.get('conversation_id'))})
        if not session_ids:
            return {'items': [], 'total': 0, 'limit': limit, 'offset': 0,
                    'next_cursor': None}
        session_hashes = {hashlib.sha256(session_id.encode('utf-8')).hexdigest(): session_id
                          for session_id in session_ids}
        raw_types = sorted({str(int(value) & 0xFFFFFFFF) for value in
                            (provider._SOURCE_MEDIA_TYPES.get(media_type) if media_type else
                             set().union(*provider._SOURCE_MEDIA_TYPES.values()))})
        scope = hashlib.sha256(json.dumps(
            [account_id, 'account-wide', params.get('conversation_id', ''),
             media_type, start, end], ensure_ascii=False,
            separators=(',', ':')).encode('utf-8')).hexdigest()[:16]
        global_cursor = None
        total = None
        if encoded_cursor:
            if (not isinstance(encoded_cursor, str) or
                    not re.fullmatch(r'[A-Za-z0-9_-]{1,1024}', encoded_cursor)):
                raise ValueError('媒体游标无效')
            try:
                decoded = json.loads(base64.urlsafe_b64decode(
                    encoded_cursor + '=' * (-len(encoded_cursor) % 4)))
                global_cursor, total, cursor_scope = decoded
                if (cursor_scope != scope or type(total) is not int or total < 0 or
                        not isinstance(global_cursor, list) or len(global_cursor) != 2 or
                        not isinstance(global_cursor[0], str) or
                        global_cursor[0] not in session_hashes or
                        not isinstance(global_cursor[1], dict) or
                        type(global_cursor[1].get('sort_seq')) is not int):
                    raise ValueError
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError('媒体游标无效') from None

        cursor_conversation = session_hashes.get(global_cursor[0]) if global_cursor else None
        cursor_row = global_cursor[1] if global_cursor else None
        class _WorstFirst:
            __slots__ = ('key', 'row')

            def __init__(self, key, row):
                self.key, self.row = key, row

            def __lt__(self, other):
                return self.key > other.key

        merged = []
        total_count = 0
        for session_id in session_ids:
            first = True
            source_cursor = cursor_row if session_id == cursor_conversation else None
            threshold = None
            threshold_inclusive = False
            if cursor_conversation and session_id != cursor_conversation:
                threshold = cursor_row['sort_seq']
                threshold_inclusive = session_id > cursor_conversation
            page_items = 0
            seen = set()
            session_order = 0
            scanned_to_end = False
            while True:
                page = provider.source_media(
                    session_id, media_type=media_type, start_time=start,
                    end_time=end, offset=0, limit=500,
                    after_cursor=source_cursor,
                    after_sort_seq=threshold,
                    after_sort_seq_inclusive=threshold_inclusive,
                    include_total=(not encoded_cursor and first))
                if not encoded_cursor and first:
                    raw_total = int(page.get('total') or 0)
                    indexed_total = self.store.wechat_indexed_source_media_count(
                        account_id, session_id, raw_types, start, end)
                    total_count += max(0, raw_total - indexed_total)
                first = False
                rows = page.get('items') or []
                if not rows:
                    scanned_to_end = True
                    break
                indexed_ids = self.store.wechat_source_media_ids(
                    account_id, session_id, [row['id'] for row in rows])
                for row in rows:
                    row_cursor = row.get('_source_cursor')
                    if not isinstance(row_cursor, dict):
                        continue
                    identity = row.get('id')
                    if not isinstance(identity, str) or identity in seen or identity in indexed_ids:
                        continue
                    seen.add(identity)
                    row['conversation_id'] = session_id
                    row['_source_cursor'] = row_cursor
                    key = (-int(row.get('sort_seq') or 0), session_id, session_order)
                    session_order += 1
                    candidate = _WorstFirst(key, row)
                    if len(merged) < limit + 1:
                        heapq.heappush(merged, candidate)
                    elif key < merged[0].key:
                        heapq.heapreplace(merged, candidate)
                    page_items += 1
                    if page_items >= limit + 1:
                        break
                if page_items >= limit + 1:
                    break
                next_source_cursor = page.get('next_cursor')
                if next_source_cursor is None or next_source_cursor == source_cursor:
                    scanned_to_end = True
                    break
                source_cursor = next_source_cursor
                threshold = None
            if not encoded_cursor:
                estimate = max(0, raw_total - indexed_total)
                difference = page_items - estimate
                # shortcut: early-stopped sessions keep a count estimate; scan all rows when exact totals are required.
                total_count += difference if scanned_to_end else max(0, difference)
            if not encoded_cursor:
                total = total_count

        merged.sort(key=lambda candidate: candidate.key)
        has_more = len(merged) > limit
        items = [candidate.row for candidate in merged[:limit]]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            session_hash = hashlib.sha256(last['conversation_id'].encode('utf-8')).hexdigest()
            payload = json.dumps([[session_hash, last['_source_cursor']],
                                  total, scope], ensure_ascii=False,
                                 separators=(',', ':')).encode('utf-8')
            next_cursor = base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')
        output = []
        for row in items:
            row.pop('_source_cursor', None)
            conversation_id = row['conversation_id']
            output.append({**row, 'account_id': account_id,
                           'conversation_title': reader.get_nickname(conversation_id) or conversation_id,
                           'conversation_kind': ('group' if conversation_id.endswith('@chatroom')
                                                 else 'direct')})
        return {'items': output, 'total': total, 'limit': limit, 'offset': 0,
                'next_cursor': next_cursor}

    def _issue_moment_media_url(self, candidate, cache_root, mime=None):
        root = Path(cache_root).resolve(strict=True)
        path = Path(candidate).resolve(strict=True)
        if Path(candidate).is_symlink() or not path.is_file() or not path.is_relative_to(root):
            return None
        token = secrets.token_urlsafe(24)
        expires_at = time.monotonic() + MOMENT_MEDIA_TOKEN_TTL_SECONDS
        content_type = mime or mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
        with self.lock:
            self.moment_media_paths[token] = (path, root, content_type, expires_at)
            for old_token, old_entry in list(self.moment_media_paths.items()):
                if old_entry[3] <= time.monotonic():
                    self.moment_media_paths.pop(old_token, None)
            while len(self.moment_media_paths) > 4096:
                self.moment_media_paths.pop(next(iter(self.moment_media_paths)))
        return '/local-moment-media/' + token

    def read_moment_media(self, media_url):
        """Read one explicitly requested image or video from the local Moments cache."""
        match = re.fullmatch(r'/local-moment-media/([A-Za-z0-9_-]{32})', str(media_url or ''))
        if not match:
            raise ValueError('朋友圈图片引用无效')
        with self.lock:
            entry = self.moment_media_paths.get(match.group(1))
            if entry and entry[3] <= time.monotonic():
                self.moment_media_paths.pop(match.group(1), None)
                entry = None
        if not entry:
            raise ValueError('朋友圈图片缓存引用已过期')
        path, cache_root, mime, _expires_at = entry
        try:
            resolved_root = Path(cache_root).resolve(strict=True)
            resolved = Path(path).resolve(strict=True)
            if (Path(path).is_symlink() or not resolved.is_file() or
                    not resolved.is_relative_to(resolved_root)):
                raise ValueError('朋友圈图片缓存引用无效')
            if resolved.stat().st_size <= 0:
                raise ValueError('朋友圈媒体超过 MCP 返回大小限制（8 MiB）')
            payload = _read_cached_snapshot(
                resolved, MCP_MEDIA_MAX_BYTES, '朋友圈媒体超过 MCP 返回大小限制（8 MiB）')
        except (OSError, RuntimeError) as exc:
            raise ValueError('朋友圈图片缓存不可用') from exc
        signatures = {
            'image/jpeg': payload.startswith(b'\xff\xd8\xff'),
            'image/png': payload.startswith(b'\x89PNG\r\n\x1a\n'),
            'image/gif': payload.startswith((b'GIF87a', b'GIF89a')),
            'image/webp': payload.startswith(b'RIFF') and payload[8:12] == b'WEBP',
            'image/bmp': payload.startswith(b'BM'),
            'video/mp4': len(payload) >= 16 and payload[4:8] == b'ftyp' and
                         16 <= int.from_bytes(payload[:4], 'big') <= len(payload),
            'video/webm': payload.startswith(b'\x1a\x45\xdf\xa3'),
        }
        valid_ftyp = (len(payload) >= 16 and payload[4:8] == b'ftyp' and
                      16 <= int.from_bytes(payload[:4], 'big') <= len(payload))
        brands = payload[8:int.from_bytes(payload[:4], 'big')].lower() if valid_ftyp else b''
        signatures['image/avif'] = valid_ftyp and any(brand in brands for brand in (b'avif', b'avis'))
        signatures['image/heic'] = valid_ftyp and any(
            brand in brands for brand in (b'heic', b'heix', b'hevc', b'hevx', b'mif1', b'msf1'))
        if not signatures.get(mime):
            raise ValueError('仅支持通过 MCP 返回签名有效的本机缓存图片或视频')
        return payload, mime

    def read_moment_image(self, media_url):
        """Backward-compatible alias for the former image-only MCP reader."""
        payload, mime = self.read_moment_media(media_url)
        if not mime.startswith('image/'):
            raise ValueError('该 Moments 媒体不是图片')
        return payload, mime

    def fetch_avatar(self, raw_url):
        """Read a previously cached profile avatar without network access."""
        if not isinstance(raw_url, str) or len(raw_url) > 2048:
            raise ValueError('头像地址无效')
        parsed = urlsplit(raw_url)
        allowed_hosts = {'q.qlogo.cn', 'q1.qlogo.cn', 'p.qlogo.cn', 'thirdqq.qlogo.cn', 'wx.qlogo.cn', 'wework.qpic.cn'}
        local_avatar = (parsed.scheme == 'hub-avatar' and parsed.netloc in ('wechat', 'wecom', 'qq') and
                        re.fullmatch(r'/[a-f0-9]{64}', parsed.path) and
                        not parsed.query and not parsed.fragment)
        remote_avatar = (parsed.scheme == 'https' and parsed.hostname in allowed_hosts and
                         not parsed.username and not parsed.password and
                         parsed.port in (None, 443) and parsed.path.startswith('/'))
        if not (local_avatar or remote_avatar):
            raise ValueError('头像来源不受支持')
        digest = hashlib.sha256(raw_url.encode('utf-8')).hexdigest()
        cache_dir = self.storage / 'avatars'
        if cache_dir.is_symlink():
            raise ValueError('头像缓存目录无效')
        cache_dir.mkdir(parents=True, exist_ok=True)
        if not cache_dir.resolve().is_relative_to(self.storage.resolve()):
            raise ValueError('头像缓存目录超出本机存储范围')
        for ext, mime in (('.jpg', 'image/jpeg'), ('.png', 'image/png'),
                          ('.bmp', 'image/bmp'), ('.gif', 'image/gif'),
                          ('.webp', 'image/webp')):
            path = cache_dir / (digest + ext)
            if path.is_file() and not path.is_symlink():
                with path.open('rb') as source:
                    payload = source.read(2 * 1024 * 1024 + 1)
                if len(payload) > 2 * 1024 * 1024:
                    raise ValueError('本机头像缓存超过 2 MiB 限制')
                if _avatar_format(payload) != (ext, mime):
                    raise ValueError('本机头像缓存内容校验失败')
                return payload, mime
        raise ValueError('头像不在本机缓存中')

    def read_contact_avatar(self, platform, account_id, contact_id):
        avatar_url = self.store.contact_avatar(platform, account_id, contact_id)
        if not avatar_url:
            raise ValueError('联系人没有已缓存头像')
        return self.fetch_avatar(avatar_url)

    def _cache_local_avatar(self, raw_url, payload):
        parsed = urlsplit(raw_url) if isinstance(raw_url, str) else None
        if (parsed is None or parsed.scheme != 'hub-avatar' or parsed.netloc not in ('wechat', 'wecom', 'qq') or
                not re.fullmatch(r'/[a-f0-9]{64}', parsed.path) or parsed.query or parsed.fragment or
                not isinstance(payload, bytes) or
                len(payload) > 2 * 1024 * 1024):
            return False
        image_format = _avatar_format(payload)
        if image_format is None:
            return False
        extension, _mime = image_format
        cache_dir = self.storage / 'avatars'
        temporary = None
        try:
            if cache_dir.is_symlink():
                return False
            cache_dir.mkdir(parents=True, exist_ok=True)
            if not cache_dir.resolve().is_relative_to(self.storage.resolve()):
                return False
            digest = hashlib.sha256(raw_url.encode('utf-8')).hexdigest()
            destination = cache_dir / (digest + extension)
            temporary = cache_dir / (digest + '.' + secrets.token_hex(8) + '.tmp')
            with temporary.open('xb') as target:
                target.write(payload)
            os.replace(temporary, destination)
            for stale_extension in ('.jpg', '.png', '.bmp', '.gif', '.webp'):
                stale = cache_dir / (digest + stale_extension)
                if stale_extension != extension:
                    try:
                        stale.unlink(missing_ok=True)
                    except OSError:
                        pass
            return True
        except OSError:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            return False


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
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def json(self, payload, status=200, extra=None):
        self.send_bytes(json.dumps(payload, ensure_ascii=False).encode(),
                        'application/json; charset=utf-8', status, extra)

    def _build_export_bundle(self, query, work, cancel):
        format_id = query.get('format', 'json')
        mime, filename = render_export([], format_id, '本地消息档案')[1:]
        normalized_format = format_id.strip().lower().lstrip('.')
        if normalized_format in ('excel', 'xls'):
            normalized_format = 'xlsx'
        export_query = {key: query.get(key, '') for key in ('platform', 'q', 'type', 'start', 'end')}
        export_query = {key: value for key, value in export_query.items() if value != ''}
        conversation_id = query.get('conversation_id', '')
        account_id = query.get('account_id', '')
        spool_path = work / 'messages.sqlite3'
        spool = sqlite3.connect(spool_path)
        requests, cursor, sequence = [], None, 0
        def bundle_safe_value(value, key=''):
            if isinstance(value, dict):
                safe = {}
                for child_key, child_value in value.items():
                    lowered = str(child_key).lower()
                    if any(marker in lowered for marker in
                           ('path', 'url', 'token', 'secret', 'password', 'authorization',
                            'cookie', 'ticket', 'credential')):
                        continue
                    safe[child_key] = bundle_safe_value(child_value, lowered)
                return safe
            if isinstance(value, list):
                return [bundle_safe_value(item, key) for item in value]
            if isinstance(value, str) and key in {'name', 'file_name', 'filename'}:
                if '://' in value:
                    return ''
                return Path(value).name
            return value
        try:
            spool.execute('''CREATE TABLE export_rows (
                seq INTEGER PRIMARY KEY, timestamp INTEGER NOT NULL, platform TEXT,
                account_id TEXT, conversation_kind TEXT, sender_id TEXT,
                sender_name TEXT, payload TEXT NOT NULL)''')
            while True:
                if cancel.is_set():
                    raise InterruptedError
                if conversation_id:
                    page = self.hub.store.messages(account_id=account_id,
                        conversation_id=conversation_id, **export_query,
                        limit=5000, cursor=cursor)
                else:
                    page = self.hub.store.search(**{**export_query, 'q': export_query.get('q', '')},
                                                 limit=5000, cursor=cursor)
                payload_rows = []
                for index, row in enumerate(page['items']):
                    if cancel.is_set():
                        raise InterruptedError
                    row = bundle_safe_value(row)
                    row['media_url'] = None
                    if row.get('file_name'):
                        row['file_name'] = Path(str(row['file_name'])).name
                    payload_rows.append((sequence + index, int(row.get('timestamp') or 0),
                        row.get('platform'), row.get('account_id'), row.get('conversation_kind'),
                        row.get('sender_id'), row.get('sender_name'),
                        json.dumps(row, ensure_ascii=False, separators=(',', ':'))))
                    requests.extend(message_resource_requests(row))
                    if len(requests) > 100:
                        raise ValueError('会话导出附件超过单次 100 项上限，请缩小筛选范围')
                spool.executemany('''INSERT INTO export_rows
                    (seq, timestamp, platform, account_id, conversation_kind, sender_id,
                     sender_name, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', payload_rows)
                sequence += len(page['items'])
                cursor = page.get('next_cursor')
                if not cursor or not page['items']:
                    break
            media_type = export_query.get('type', '')
            provider = self.hub.providers.get('wechat')
            reader = getattr(provider, 'reader', None)
            reader_account = str(getattr(reader, 'wxid', None) or
                                 getattr(reader, 'account', None) or '')
            if (query.get('platform') == 'wechat' and account_id and conversation_id and
                    not export_query.get('q') and reader and reader_account == account_id and
                    media_type in ('', 'image', 'video', 'voice', 'sticker', 'file')):
                source_cursor = None
                while True:
                    if cancel.is_set():
                        raise InterruptedError
                    source_page = self.hub.list_wechat_source_media({
                        'platform': 'wechat', 'account_id': account_id,
                        'conversation_id': conversation_id, 'type': media_type,
                        'start': export_query.get('start', ''),
                        'end': export_query.get('end', ''), 'limit': 60,
                        **({'cursor': source_cursor} if source_cursor else {}),
                    })
                    for item in source_page['items']:
                        extra = item.get('extra') or {}
                        request = {'resource_kind': 'wechat_source', 'platform': 'wechat',
                                   'account_id': account_id, 'conversation_id': conversation_id,
                                   'shard': extra.get('shard'), 'local_id': extra.get('local_id')}
                        if item.get('type') == 'sticker':
                            request['media_type'] = 'sticker'
                        else:
                            request['image_tier'] = 'best'
                        requests.append(request)
                        if len(requests) > 100:
                            raise ValueError('会话导出附件超过单次 100 项上限，请缩小筛选范围')
                    next_source_cursor = source_page.get('next_cursor')
                    if not next_source_cursor:
                        break
                    if source_cursor == next_source_cursor:
                        raise ValueError('微信源媒体游标无效')
                    source_cursor = next_source_cursor
            spool.commit()
            count = sequence
            kind = (resolve_chatlab_conversation_type(row[0] for row in spool.execute(
                'SELECT DISTINCT conversation_kind FROM export_rows'))
                if normalized_format in ('chatlab', 'chatlab-jsonl') else None)
            source_platforms = [row[0] for row in spool.execute(
                'SELECT DISTINCT platform FROM export_rows ORDER BY platform')]
            def export_rows():
                for row in spool.execute('SELECT payload FROM export_rows ORDER BY timestamp, seq'):
                    if cancel.is_set():
                        raise InterruptedError
                    yield json.loads(row[0])
            def export_members():
                rows = spool.execute('''WITH ranked AS (
                    SELECT platform, account_id, sender_id, sender_name, timestamp, seq,
                           ROW_NUMBER() OVER (PARTITION BY platform, account_id, sender_id
                                              ORDER BY timestamp, seq) AS rank
                    FROM export_rows WHERE sender_id IS NOT NULL AND sender_id != '')
                    SELECT platform, account_id, sender_id, sender_name FROM ranked
                    WHERE rank=1 ORDER BY timestamp, seq''')
                for platform, member_account, sender_id, sender_name in rows:
                    if cancel.is_set():
                        raise InterruptedError
                    yield {'platformId': str(sender_id),
                           'accountName': str(sender_name or sender_id or '未知'),
                           'platform': str(platform or ''),
                           'accountId': str(member_account or '')}
            output = work / filename
            with output.open('w+b') as stream:
                write_export(export_rows(), stream, format_id, '本地消息档案', count=count,
                             source_platforms=source_platforms, members=export_members(),
                             conversation_type=kind)
        finally:
            spool.close()
            spool_path.unlink(missing_ok=True)
        return {'path': str(output), 'filename': filename, 'format': normalized_format,
                'mime': mime, 'items': requests}

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
            token = self.session_token()
            if parsed.path == '/api/account-safety/status':
                self.json(self.hub.account_safety.status(token))
                return
            if self.hub.account_safety.configured and not self.hub.account_safety.authenticated(token):
                if parsed.path in ('/', '/index.html'):
                    self.static('/lock.html')
                elif parsed.path in ('/lock.html', '/lock.js', '/lock.css', '/assets/hub.svg',
                                     '/vendor/lucide.js'):
                    self.static(parsed.path)
                else:
                    self.json({'error': '应用已锁定', 'code': 'account_locked'}, 423)
                return
            capability = self.capability_for_path(parsed.path)
            if capability and not self.hub.capability_enabled(capability):
                self.json(self.hub.capability_error(capability), 403)
                return
            if parsed.path == '/api/events/messages':
                self.message_events()
                return
            if parsed.path == '/api/avatar':
                payload, mime = self.hub.fetch_avatar(params.get('url', ''))
                self.send_bytes(payload, mime)
                return
            match = re.fullmatch(r'/api/sends/([A-Za-z0-9_-]{8,80})/approval-challenge', parsed.path)
            if match:
                self.hub.require_capability('qq_send')
                self.json(self.hub.issue_send_approval_challenge(match.group(1), 'qq'))
                return
            match = re.fullmatch(r'/api/wechat-sends/([A-Za-z0-9_-]{8,80})/approval-challenge',
                                 parsed.path)
            if match:
                self.hub.require_capability('wechat_send')
                self.json(self.hub.issue_send_approval_challenge(match.group(1), 'wechat'))
                return
            match = re.fullmatch(r'/api/wecom-bot-sends/([A-Za-z0-9_-]{8,80})/attachment-preview', parsed.path)
            if match:
                record = self.hub.send_service.repository.get(match.group(1))
                if (not record or record.get('platform') != 'wecom_bot' or
                        record.get('status') != 'pending_approval'):
                    self.json({'error': '待审批附件不存在'}, 404)
                    return
                try:
                    attachment = json.loads(record.get('attachment_json') or '{}')
                except (TypeError, ValueError):
                    attachment = {}
                if not isinstance(attachment, dict) or attachment.get('media_type') not in (
                        'image', 'video', 'voice', 'file'):
                    self.json({'error': '待审批附件类型无效'}, 404)
                    return
                loaded = self.hub.wecom_bot_media.load(attachment)
                extension = Path(attachment['filename']).suffix.lower()
                media_type = attachment['media_type']
                mime = ({'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                         '.gif': 'image/gif', '.webp': 'image/webp'}.get(extension)
                        if media_type == 'image' else
                        {'.mp4': 'video/mp4', '.m4v': 'video/mp4', '.mov': 'video/quicktime',
                         '.webm': 'video/webm'}.get(extension)
                        if media_type == 'video' else 'application/octet-stream'
                        if media_type in ('voice', 'file') else None)
                if not mime:
                    self.json({'error': '附件格式无效'}, 400)
                    return
                disposition = (f"attachment; filename*=UTF-8''{quote(attachment['filename'])}"
                               if media_type in ('voice', 'file') else 'inline')
                self.send_bytes(loaded['data'], mime, extra={
                    'Content-Disposition': disposition,
                    'Content-Security-Policy': "default-src 'none'; sandbox",
                })
                return
            match = re.fullmatch(r'/api/resource-jobs/([A-Fa-f0-9]{20})/download', parsed.path)
            if match:
                path = self.hub.resource_jobs.download_path(match.group(1))
                if path is None:
                    self.json({'error': '资源导出文件不存在'}, 404)
                    return
                self.send_file(path, 'application/zip',
                               'attachment; filename="local-message-hub-attachments.zip"')
                return
            match = re.fullmatch(r'/api/qq/forward-resources/([A-Za-z0-9_-]{24,64})', parsed.path)
            if match:
                from integrations.qq.provider import QQProvider
                provider = self.hub.providers.get('qq') or QQProvider()
                with self.hub.lock:
                    self.hub.providers['qq'] = provider
                path, mime = provider.download_forward_resource(match.group(1), self.hub.storage)
                self.send_local_media(path, mime, self.headers.get('Range'), {
                    'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                    'Content-Disposition': 'inline',
                })
                return
            if parsed.path.startswith('/api/'):
                match = re.fullmatch(r'/api/backups/([^/]+)', parsed.path)
                if match:
                    backup = self.hub.backup_path(match.group(1))
                    mime = 'application/zip' if backup.suffix == '.zip' else 'application/vnd.sqlite3'
                    self.send_file(backup, mime,
                                   'attachment; filename="' + backup.name + '"')
                    return
                self.get_api(parsed.path, params)
            else:
                self.static(parsed.path, params)
        except ValueError as exc:
            self.json({'error': str(exc)}, 400)
        except FileNotFoundError:
            self.json({'error': 'QQ 转发资源已过期或不可用'}, 404)
        except PermissionError:
            self.json({'error': 'QQ 登录账号已变化'}, 403)
        except QQProviderError as exc:
            self.json({'error': str(exc)}, 503)
        except Exception as exc:
            print('Request failure:', type(exc).__name__, flush=True)
            self.json({'error': '本机服务处理失败，请检查数据源状态'}, 500)

    def message_events(self):
        last_event_id = self.headers.get('Last-Event-ID')
        try:
            cursor = int(last_event_id) if last_event_id is not None else None
        except ValueError:
            self.json({'error': '事件游标无效'}, 400)
            return
        high_water = self.hub.store.message_rowid()
        if cursor is None or cursor < 0 or cursor > high_water:
            cursor = high_water
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()
        try:
            self.wfile.write(
                f'retry: 2000\nid: {cursor}\nevent: checkpoint\ndata: {{}}\n\n'.encode('ascii'))
            self.wfile.flush()
            while not self.hub.stop.is_set():
                if not self.hub.capability_enabled('notifications'):
                    self.close_connection = True
                    return
                if self.hub.account_safety.configured and not self.hub.account_safety.authenticated(
                        self.session_token(), touch=False):
                    self.close_connection = True
                    return
                filters = self.hub.settings.get('notification_filters', {})
                platforms = set(filters.get('platforms', NATIVE_PLATFORMS))
                muted = set(filters.get('muted_conversations', []))
                allowed = set(filters.get('allowed_conversations', []))
                allowlist_only = filters.get('mode', 'all') == 'allowlist'
                rows = self.hub.store.incoming_message_events(cursor)
                if rows:
                    cursor = max(cursor, max(int(row['event_id']) for row in rows))
                for row in rows:
                    identity = json.dumps([row['platform'], row['account_id'],
                                           row['conversation_id']], ensure_ascii=False,
                                          separators=(',', ':'))
                    if (row['platform'] not in platforms or identity in muted or
                            (allowlist_only and identity not in allowed)):
                        continue
                    payload = {key: row[key] for key in (
                        'platform', 'account_id', 'conversation_id',
                        'conversation_title', 'conversation_kind')}
                    data = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
                    self.hub.queue_notification_event(int(row['event_id']))
                    self.wfile.write(f"id: {row['event_id']}\nevent: message\ndata: {data}\n\n".encode('utf-8'))
                if rows:
                    self.wfile.write(
                        f'id: {cursor}\nevent: checkpoint\ndata: {{}}\n\n'.encode('ascii'))
                    self.wfile.flush()
                else:
                    self.wfile.write(b': keep-alive\n\n')
                    self.wfile.flush()
                self.hub.stop.wait(1)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return

    def get_api(self, path, params):
        query = {k: v for k, v in params.items() if k in ('platform', 'q', 'type', 'start', 'end', 'cursor')}
        query['platform'] = query.get('platform', 'all')
        if query['platform'] not in (*PLATFORMS, 'all'):
            raise ValueError('未知平台')
        paging = {'limit': min(max(int(params.get('limit', 100)), 1), 1000),
                  'offset': max(int(params.get('offset', 0)), 0)}
        store = self.hub.store
        if path == '/api/campus/wechat-accounts':
            result = self.hub.campus_wechat_accounts(params.get('q', ''))
        elif path == '/api/campus/briefing':
            result = self.hub.campus_service().briefing(
                section=params.get('section', 'selected'), q=params.get('q', ''),
                topic=params.get('topic', ''), date=params.get('date', ''), **paging)
        elif path == '/api/campus/items':
            result = self.hub.campus_service().search(
                q=params.get('q', ''), category=params.get('category', ''),
                source_id=params.get('source_id', ''), campus=params.get('campus', ''),
                relevant=params.get('relevant', '') == '1',
                bookmarked=params.get('bookmarked', '') == '1', **paging)
        elif path == '/api/campus/sources':
            result = self.hub.campus_service().sources()
        elif path == '/api/campus/profile':
            result = self.hub.campus_service().profile()
        elif path == '/api/campus/refresh':
            result = self.hub.campus_service().refresh_status()
        elif path == '/api/campus/updates':
            result = self.hub.campus_service().update_settings()
        elif path == '/api/campus/scoring':
            result = self.hub.campus_service().scoring_status()
        elif re.fullmatch(r'/api/campus/items/[a-f0-9]{64}', path):
            result = self.hub.campus_service().get_item(path.rsplit('/', 1)[1])
        elif path == '/api/sources':
            result = self.hub.source_info()
        elif path == '/api/accounts':
            result = {'items': store.account_scopes()}
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
            result = {key: value for key, value in self.hub.settings.items()
                      if key != 'account_lock'}
            result.update({'account_lock_enabled': self.hub.account_safety.configured,
                      'account_lock_idle_timeout_seconds': self.hub.account_safety.idle_seconds,
                      'storage_dir': str(self.hub.storage),
                      'fts_enabled': self.hub.store._fts_enabled,
                      'auto_export_status': self.hub.auto_exports.status(),
                      'capabilities': describe_capabilities(self.hub.settings.get('capabilities'))})
        elif path == '/api/wecom-bot/identity':
            result = self.hub.wecom_bot_credentials.status()
        elif path == '/api/wecom-bot-sends':
            result = self.hub.send_service.list(params.get('status', 'pending_approval'),
                                                int(params.get('limit', 50)), 'wecom_bot')
        elif re.fullmatch(r'/api/wecom-bot-sends/[A-Za-z0-9_-]{8,80}', path):
            result = self.hub.send_service.status(path.rsplit('/', 1)[1], 'wecom_bot')
            if result is None:
                self.json({'error': '发送请求不存在'}, 404)
                return
        elif re.fullmatch(r'/api/wecom-bot-sends/[A-Za-z0-9_-]{8,80}/approval-challenge', path):
            self.hub.require_capability('wecom_bot_send')
            self.json(self.hub.issue_send_approval_challenge(path.split('/')[-2], 'wecom_bot'))
            return
        elif path == '/api/auto-export':
            result = self.hub.auto_exports.status()
        elif path in ('/api/moments', '/api/moments/export'):
            try:
                exporting = path.endswith('/export')
                limit = 50 if exporting else min(max(int(params.get('limit', 50)), 1), 200)
                offset = max(int(params.get('offset', 0)), 0)
            except (TypeError, ValueError):
                raise ValueError('朋友圈分页参数无效') from None
            if len(params.get('q', '')) > 1000 or len(params.get('username', '')) > 256:
                raise ValueError('朋友圈筛选文本过长')
            if len(params.get('account_id', '')) > 256:
                raise ValueError('朋友圈账号标识过长')
            result = self.hub.search_moments(limit=limit, offset=offset,
                username=params.get('username', ''), keyword=params.get('q', ''),
                start=params.get('start', ''), end=params.get('end', ''),
                account_id=params.get('account_id', ''))
            if not result.get('available'):
                if result.get('reason') == 'moments_account_required' and not exporting:
                    self.json(result)
                else:
                    status = 409 if result.get('reason') == 'moments_account_required' else 503
                    self.json({'error': '本机朋友圈数据暂不可用', **result}, status)
                return
            if exporting:
                result = {'source': {'platform': 'wechat', 'database': 'sns.db', 'scope': 'local'}, **result}
                first_page = result['items']
                export_offset = offset + len(first_page)
                end_offset = min(int(result.get('total', export_offset)), offset + 100_000)

                def export_items():
                    yield from first_page
                    next_offset = export_offset
                    while next_offset < end_offset:
                        page = self.hub.store.moments(result['account_id'],
                            limit=min(50, end_offset - next_offset), offset=next_offset,
                            username=params.get('username', ''), keyword=params.get('q', ''),
                            start=params.get('start', ''), end=params.get('end', ''))
                        rows = page.get('items', [])
                        if not rows:
                            break
                        yield from rows
                        next_offset += len(rows)

                result['items'] = export_items()
                try:
                    payload, content_type, extension = render_moments_export_bounded(
                        result, params.get('format', 'json').casefold())
                except MomentsExportTooLarge:
                    self.json({'error': '朋友圈导出超过 32 MiB 限制'}, 413)
                    return
                if len(payload) > 32 * 1024 * 1024:
                    self.json({'error': '朋友圈导出超过 32 MiB 限制'}, 413)
                    return
                filename = f"moments-{datetime.now().strftime('%Y%m%d')}.{extension}"
                self.send_bytes(payload, content_type, extra={
                    'Content-Disposition': f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
                })
                return
        elif path == '/api/mcp':
            root = ROOT.as_posix()
            storage = self.hub.storage.resolve().as_posix()
            python = Path(sys.executable).resolve().as_posix()
            from .mcp_server import MCPServer
            mcp = MCPServer(
                self.hub.store, allow_writes=self.hub.settings.get('mcp_write_enabled') is True,
                source_status_reader=self.hub.source_status_snapshot,
                campus_reader=self.hub.campus_service,
                media_reader=self.hub.read_cached_image,
                capabilities=self.hub.settings.get('capabilities'),
                capability_reader=lambda: self.hub.settings.get('capabilities'),
                send_service=self.hub.send_service,
                moments_reader=self.hub.search_moments,
                moments_media_reader=self.hub.read_moment_media,
                qq_gallery_reader=self.hub.list_qq_cache_resources,
                avatar_reader=self.hub.read_contact_avatar,
                group_members_reader=self.hub.conversation_members,
                qq_forward_reader=self.hub.read_qq_forward_attachment,
                qq_forward_lister=self.hub.list_qq_forward_attachments,
                qq_send_target_resolver=self.hub.resolve_qq_send_target,
                wecom_bot_identity_reader=self.hub.wecom_bot_credentials.status,
                wechat_readiness_reader=lambda: self.hub.wechat_sender.readiness(True),
                wechat_source_media_lister=self.hub.list_wechat_source_media,
                wechat_source_media_reader=self.hub.read_wechat_source_media,
            )
            tools = tuple(tool['name'] for tool in mcp.tools())
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
                      'enabled': self.hub.capability_enabled('mcp'),
                      'lifecycle': '客户端按需启动', 'transport': 'stdio',
                      'mode': 'metadata-write' if self.hub.settings.get('mcp_write_enabled') is True else 'read-only',
                      'write_enabled': self.hub.settings.get('mcp_write_enabled') is True,
                      'config_text': config, 'operations': self.hub.store.mcp_operations(20)}
        elif path == '/api/operations':
            result = {'items': store.operation_log(min(int(params.get('limit', 50)), 200))}
        elif path == '/api/conversations':
            result = getattr(store, path.rsplit('/', 1)[1])(query['platform'], query.get('q', ''), **paging)
        elif path == '/api/contacts':
            result = store.contacts(query['platform'], query.get('q', ''),
                                    account_id=params.get('account_id', ''),
                                    kind=params.get('kind', ''), **paging)
        elif path == '/api/conversation-members':
            result = self.hub.conversation_members(params)
        elif path == '/api/messages':
            result = store.messages(account_id=params.get('account_id', ''),
                                    conversation_id=params.get('conversation_id', ''), **query, **paging)
            store.attach_quoted_message_previews(result.get('items', []))
            if self.hub.capability_enabled('media'):
                for item in result.get('items', []):
                    quote_data = item.get('extra', {}).get('wechat_quote')
                    target_id = quote_data.get('target_message_id') if isinstance(quote_data, dict) else None
                    if not target_id:
                        continue
                    target = store.get_message('wechat', item['account_id'], target_id)
                    if not target or target.get('conversation_id') != item.get('conversation_id'):
                        continue
                    raw_type = str(target.get('raw_type') or '')
                    media_type = 'image' if raw_type in ('47', '11000') else target.get('type')
                    if media_type not in ('image', 'video', 'voice'):
                        continue
                    try:
                        resolved = self.hub.resolve_media({
                            'platform': 'wechat', 'account_id': item['account_id'],
                            'conversation_id': item['conversation_id'], 'message_id': target_id,
                        })
                    except (OSError, RuntimeError, ValueError, KeyError):
                        continue
                    media_url = resolved.get('media_url') if isinstance(resolved, dict) else None
                    if isinstance(media_url, str) and re.fullmatch(r'/local-media/[A-Za-z0-9_-]{32,64}', media_url):
                        quote_data['preview'] = {'type': media_type, 'media_url': media_url}
        elif path == '/api/context':
            platform = params.get('platform', 'all')
            account_id = params.get('account_id', '')
            conversation_id = params.get('conversation_id')
            source_id = params.get('source_message_id')
            message_id = params.get('message_id', '')
            if source_id is not None:
                if platform != 'wechat' or not account_id or not conversation_id:
                    raise ValueError('引用目标范围无效')
                message_id = store.quoted_message_target(
                    platform, account_id, conversation_id,
                    params.get('reference_message_id', ''), source_id)
                if message_id is None:
                    self.json({'error': '引用的消息未在该会话中唯一匹配'}, 404)
                    return
            result = store.message_context(
                platform, account_id, message_id, params.get('before', 5),
                params.get('after', 5), conversation_id=conversation_id)
            if result is None:
                self.json({'error': '消息不存在'}, 404)
                return
            if source_id is not None:
                result['target_message_id'] = message_id
        elif path == '/api/quoted-message':
            platform = params.get('platform', 'wechat')
            account_id = params.get('account_id', '')
            conversation_id = params.get('conversation_id', '')
            reference_id = params.get('reference_message_id', '')
            if platform != 'wechat' or not account_id or not conversation_id or not reference_id:
                raise ValueError('引用消息范围无效')
            message_id = store.quoted_message_target_for_reference(
                platform, account_id, conversation_id, reference_id)
            if message_id is None:
                self.json({'error': '引用目标未能在该会话中唯一匹配'}, 404)
                return
            result = store.message_context(
                platform, account_id, message_id, params.get('before', 5),
                params.get('after', 5), conversation_id=conversation_id)
            if result is None:
                self.json({'error': '引用消息不存在'}, 404)
                return
            result['target_message_id'] = message_id
        elif path == '/api/search':
            result = store.search(**{**query, 'q': query.get('q', '')},
                                  preview_chars=1200, **paging)
        elif path == '/api/media':
            media_source = params.get('source', 'all')
            if media_source == 'qq-cache':
                if query['platform'] not in ('all', 'qq'):
                    result = {'items': [], 'total': 0, 'has_more': False}
                else:
                    from integrations.qq.provider import QQProvider
                    provider = self.hub.providers.get('qq')
                    if provider is None:
                        provider = QQProvider()
                        with self.hub.lock:
                            self.hub.providers['qq'] = provider
                    start_ts = parse_bound(params.get('start'))
                    end_ts = parse_bound(params.get('end'), end=True)
                    gallery = provider.cached_resources(kind=query.get('type', ''),
                                                        query=query.get('q', ''),
                                                        offset=paging['offset'],
                                                        limit=min(paging['limit'], 60),
                                                        start_ms=start_ts * 1000 if start_ts is not None else None,
                                                        end_ms=end_ts * 1000 + 999 if end_ts is not None else None)
                    if params.get('account_id') and params['account_id'] != gallery.get('account_id'):
                        gallery = {**gallery, 'items': [], 'total': 0, 'has_more': False}
                    items = []
                    for item in gallery.get('items', []):
                        modified = item.get('modified_at', '')
                        try:
                            value = datetime.fromisoformat(modified.replace('Z', '+00:00'))
                            if value.tzinfo is None:
                                value = value.replace(tzinfo=timezone.utc)
                            timestamp = int(value.timestamp())
                        except (ValueError, OverflowError):
                            timestamp = 0
                        resource_id = item['resource_id']
                        items.append({'id': resource_id, 'platform': 'qq',
                                      'account_id': gallery.get('account_id', ''),
                                      'conversation_id': 'qq-cache-gallery',
                                      'conversation_title': 'QQ 缓存资源库',
                                      'conversation_kind': 'private',
                                      'type': {'audio': 'voice'}.get(item['type'], item['type']),
                                      'timestamp': timestamp, 'file_name': item['name'], 'text': '',
                                      'extra': {'resource_kind': 'qq_cache', 'resource_id': resource_id,
                                                'mime_type': item['mime_type'], 'size': item['size']}})
                    result = {'items': items, 'total': gallery['total'],
                              'has_more': gallery['has_more'], 'available': gallery['available']}
                self.json(result)
                return
            if media_source == 'wechat-source':
                result = self.hub.list_wechat_source_media({**params, 'platform': query['platform']})
                self.json(result)
                return
            if media_source == 'moments' and not self.hub.capability_enabled('moments'):
                self.json(self.hub.capability_error('moments'), 403)
                return
            if media_source == 'all' and not self.hub.capability_enabled('moments'):
                media_source = 'chat'
            result = store.media(**{k: v for k, v in query.items() if k in ('platform', 'type', 'q', 'start', 'end')},
                                 account_id=params.get('account_id', ''), **paging,
                                 expand_attachments=True, source=media_source,
                                 conversation_id=params.get('conversation_id', ''))
            if media_source != 'chat':
                provider = self.hub.providers.get('wechat')
                if provider is None:
                    try:
                        from integrations.wechat.provider import WeChatProvider
                        provider = WeChatProvider()
                        with self.hub.lock:
                            self.hub.providers['wechat'] = provider
                    except Exception:
                        provider = None
                cache_dir = getattr(provider, 'sns_cache_dir', None) if provider else None
                for item in result.get('items', []):
                    if item.get('resource_kind') != 'moment':
                        continue
                    locator = item.pop('_cache_relpath', None)
                    resource = item.get('media_entry', {}).get('resource') or {}
                    media_url = None
                    if locator and cache_dir:
                        try:
                            resolved, _name = self.hub.resolve_resource({
                                'resource_kind': 'moment', 'platform': 'wechat',
                                'account_id': item['account_id'], 'post_id': item['post_id'],
                                'media_index': item['media_index'], 'variant': item['variant']})
                            media_url = self.hub._issue_moment_media_url(resolved, cache_dir,
                                                                         resource.get('mime_type'))
                        except (OSError, ValueError, RuntimeError, FileNotFoundError):
                            media_url = None
                    resource['media_url'] = media_url
                    resource['local_available'] = bool(media_url)
                    item['media_entry']['resource'] = resource
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
        elif path == '/api/sends':
            result = self.hub.send_service.list(params.get('status', 'pending_approval'),
                                                int(params.get('limit', 50)), 'qq')
        elif path == '/api/qq/send-readiness':
            result = self.hub.qq_sender.readiness(self.hub.capability_enabled('qq_send'))
            result['checked_at'] = int(time.time())
        elif path == '/api/wechat-send-readiness':
            result = self.hub.wechat_sender.readiness(self.hub.capability_enabled('wechat_send'))
            result['checked_at'] = int(time.time())
        elif path == '/api/wechat-sends':
            result = self.hub.send_service.list(params.get('status', 'pending_approval'),
                                                int(params.get('limit', 50)), 'wechat')
        elif re.fullmatch(r'/api/wechat-sends/[A-Za-z0-9_-]{8,80}', path):
            result = self.hub.send_service.status(path.rsplit('/', 1)[1], 'wechat')
            if result is None:
                self.json({'error': '发送请求不存在'}, 404)
                return
        elif re.fullmatch(r'/api/sends/[A-Za-z0-9_-]{8,80}', path):
            result = self.hub.send_service.status(path.rsplit('/', 1)[1], 'qq')
            if result is None:
                self.json({'error': '发送请求不存在'}, 404)
                return
        elif path == '/api/analytics/private':
            self.hub.require_capability('advanced_analytics')
            result = {'items': [item.to_dict() for item in self.hub.analytics.private_profiles(
                query['platform'], params.get('account_id'), params.get('start'), params.get('end'),
                paging['limit'], paging['offset'])]}
        elif path == '/api/analytics/self-sent-daily':
            self.hub.require_capability('advanced_analytics')
            result = self.hub.analytics.self_sent_daily_distribution(
                query['platform'], params.get('account_id'), params.get('start'), params.get('end'))
        elif path == '/api/analytics/groups':
            self.hub.require_capability('advanced_analytics')
            member_limit = params.get('member_limit')
            conversation_id = params.get('conversation_id')
            account_id = params.get('account_id')
            if conversation_id and (query['platform'] == 'all' or not account_id):
                raise ValueError('完整成员排行需要指定平台和账号')
            try:
                member_limit = None if member_limit is None else min(max(int(member_limit), 0), 5000)
            except (TypeError, ValueError):
                raise ValueError('成员数量上限无效') from None
            result = {'items': self.hub.analytics.group_profiles(
                query['platform'], account_id, params.get('start'), params.get('end'),
                paging['limit'], paging['offset'], member_limit=member_limit,
                conversation_id=conversation_id)}
        elif path == '/api/analytics/relationships':
            self.hub.require_capability('relationship_footprint')
            result = {'items': self.hub.analytics.relationship_footprint(
                query['platform'], params.get('account_id'), params.get('start'), params.get('end'),
                paging['limit'], paging['offset'])}
        elif path == '/api/analytics/relationships/timeline':
            self.hub.require_capability('relationship_footprint')
            if query['platform'] == 'all' or not params.get('account_id') or not params.get('conversation_id'):
                raise ValueError('关系时间线需要指定平台、账号和会话')
            result = {'items': self.hub.analytics.relationship_timeline(
                query['platform'], params['account_id'], params['conversation_id'],
                params.get('start'), params.get('end'), paging['limit'], paging['offset'])}
        elif path == '/api/analytics/relationships/achievements':
            self.hub.require_capability('relationship_footprint')
            if query['platform'] == 'all' or not params.get('account_id') or not params.get('conversation_id'):
                raise ValueError('关系成就需要指定平台、账号和会话')
            result = self.hub.analytics.relationship_achievements(
                query['platform'], params['account_id'], params['conversation_id'])
        elif path == '/api/reports/annual':
            self.hub.require_capability('annual_reports')
            result = self.hub.analytics.annual_report(
                params.get('year'), query['platform'], params.get('account_id'),
                params.get('conversation_id')).to_dict()
        elif path == '/api/reports/pair':
            self.hub.require_capability('annual_reports')
            if query['platform'] == 'all':
                raise ValueError('双人报告需要选择具体平台')
            try:
                result = self.hub.analytics.pair_report(
                    query['platform'], params.get('account_id', ''), params.get('conversation_id', ''),
                    params.get('year'), params.get('counterpart_id')).to_dict()
            except KeyError:
                self.json({'error': '私聊会话不存在'}, 404)
                return
        elif path in ('/api/qq/local-status', '/api/qq/login-status'):
            result = self.hub.qq_local_status()
            if path.endswith('/login-status'):
                result.update(phase=None, online=False)
        elif path == '/api/qq/key-discovery':
            result = self.hub.qq_key_discovery_status()
        elif path == '/api/qq/history-import':
            result = self.hub.qq_history_status()
        elif path == '/api/media/resolve':
            result = self.hub.resolve_media(params)
        elif path == '/api/voice/transcription-status':
            result = self.hub.voice_transcription_status()
        elif re.fullmatch(r'/api/resource-jobs/[A-Fa-f0-9]{20}', path):
            result = self.hub.resource_jobs.status(path.rsplit('/', 1)[-1])
            if result is None:
                self.json({'error': '资源任务不存在'}, 404)
                return
        elif path == '/api/export':
            query.pop('cursor', None)
            format_id = params.get('format', 'json')
            mime, filename = render_export([], format_id, '本地消息档案')[1:]
            with tempfile.TemporaryDirectory(prefix='lmh-export-') as temporary:
                spool = sqlite3.connect(Path(temporary) / 'messages.sqlite3')
                try:
                    spool.execute('''CREATE TABLE export_rows (
                        seq INTEGER PRIMARY KEY, timestamp INTEGER NOT NULL, platform TEXT,
                        account_id TEXT, conversation_kind TEXT, sender_id TEXT,
                        sender_name TEXT, payload TEXT NOT NULL)''')
                    cursor, sequence = None, 0
                    while True:
                        if params.get('conversation_id'):
                            page = store.messages(account_id=params.get('account_id', ''),
                                conversation_id=params['conversation_id'], **query,
                                limit=5000, cursor=cursor)
                        else:
                            page = store.search(**{**query, 'q': query.get('q', '')},
                                                limit=5000, cursor=cursor)
                        spool.executemany('''INSERT INTO export_rows
                            (seq, timestamp, platform, account_id, conversation_kind,
                             sender_id, sender_name, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (
                            (sequence + index, int(row.get('timestamp') or 0), row.get('platform'),
                             row.get('account_id'), row.get('conversation_kind'), row.get('sender_id'),
                             row.get('sender_name'), json.dumps(row, ensure_ascii=False,
                                                               separators=(',', ':')))
                            for index, row in enumerate(page['items'])
                        ))
                        sequence += len(page['items'])
                        cursor = page.get('next_cursor')
                        if not cursor or not page['items']:
                            break
                    spool.commit()
                    count = int(spool.execute('SELECT COUNT(*) FROM export_rows').fetchone()[0])
                    normalized_format = format_id.strip().lower().lstrip('.')
                    if normalized_format in ('excel', 'xls'):
                        normalized_format = 'xlsx'
                    kind = (resolve_chatlab_conversation_type(
                        row[0] for row in spool.execute(
                            'SELECT DISTINCT conversation_kind FROM export_rows'))
                            if normalized_format in ('chatlab', 'chatlab-jsonl') else None)
                    source_platforms = [row[0] for row in spool.execute(
                        'SELECT DISTINCT platform FROM export_rows ORDER BY platform')]

                    def export_rows():
                        for row in spool.execute(
                                'SELECT payload FROM export_rows ORDER BY timestamp, seq'):
                            yield json.loads(row[0])

                    def export_members():
                        rows = spool.execute('''WITH ranked AS (
                            SELECT platform, account_id, sender_id, sender_name, timestamp, seq,
                                   ROW_NUMBER() OVER (PARTITION BY platform, account_id, sender_id
                                                      ORDER BY timestamp, seq) AS rank
                            FROM export_rows WHERE sender_id IS NOT NULL AND sender_id != '')
                            SELECT platform, account_id, sender_id, sender_name FROM ranked
                            WHERE rank=1 ORDER BY timestamp, seq''')
                        for platform, account_id, sender_id, sender_name in rows:
                            yield {'platformId': str(sender_id),
                                   'accountName': str(sender_name or sender_id or '未知'),
                                   'platform': str(platform or ''),
                                   'accountId': str(account_id or '')}

                    output = Path(temporary) / 'export.bin'
                    with output.open('w+b') as stream:
                        write_export(export_rows(), stream, format_id, '本地消息档案',
                                     count=count, source_platforms=source_platforms,
                                     members=export_members(), conversation_type=kind)
                finally:
                    spool.close()
                self.send_file(output, mime,
                               f'attachment; filename="{filename}"')
            return
        elif path == '/api/export/contacts':
            contacts = store.contacts(query['platform'], query.get('q', ''), limit=50_001,
                                      account_id=params.get('account_id', ''),
                                      kind=params.get('kind', ''))
            if contacts['total'] > 50_000:
                self.json({'error': '联系人数量超过单次导出上限（50,000）'}, 413)
                return
            data, mime, filename = render_contact_export(
                contacts['items'], params.get('format', 'csv'))
            self.send_bytes(data, mime, extra={
                'Content-Disposition': f'attachment; filename="{filename}"'})
            return
        elif path == '/api/export/group-members':
            roster = self.hub.conversation_members({
                'platform': params.get('platform'), 'account_id': params.get('account_id'),
                'conversation_id': params.get('conversation_id'), 'limit': 1000, 'offset': 0,
            })
            if roster['total'] > 1000:
                self.json({'error': '群成员数量超过单次导出上限（1,000）'}, 413)
                return
            conversation = store.get_conversation(
                params.get('platform', ''), params.get('account_id', ''),
                params.get('conversation_id', ''))
            data, mime, filename = render_group_member_export(
                roster['items'], conversation, params.get('format', 'csv'))
            self.send_bytes(data, mime, extra={
                'Content-Disposition': f'attachment; filename="{filename}"'})
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
        if path in ('/api/account-safety/setup', '/api/account-safety/unlock',
                    '/api/account-safety/lock', '/api/account-safety/disable',
                    '/api/account-safety/activity'):
            self.account_safety_action(path)
            return
        if self.hub.account_safety.configured and not self.hub.account_safety.authenticated(
                self.session_token()):
            self._discard_small_request_body()
            self.json({'error': '应用已锁定', 'code': 'account_locked'}, 423)
            return
        capability = self.capability_for_path(path, method='POST')
        if capability and not self.hub.capability_enabled(capability):
            self._discard_small_request_body()
            self.json(self.hub.capability_error(capability), 403)
            return
        if path == '/api/wecom-bot-sends/attachment':
            self.upload_wecom_bot_attachment()
            return
        if path == '/api/backups/restore':
            self.restore_upload()
            return
        if path in ('/api/imports/preview', '/api/imports'):
            self.import_upload(preview=path.endswith('/preview'))
            return
        match = re.fullmatch(r'/api/resource-jobs/([A-Fa-f0-9]{20})/cancel', path)
        if match:
            result = self.hub.resource_jobs.cancel(match.group(1))
            if result is None:
                self.json({'error': '资源任务不存在'}, 404)
            else:
                self.json(result)
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
            if path == '/api/campus/wechat-accounts/import':
                result = self.hub.import_campus_wechat_accounts(body)
            elif path == '/api/campus/profile':
                result = self.hub.campus_service().configure_profile(body)
            elif path == '/api/campus/updates':
                result = self.hub.campus_service().configure_updates(body)
            elif path == '/api/campus/scoring':
                result = self.hub.campus_service().configure_scoring(body)
            elif path == '/api/campus/scoring/run':
                if body:
                    raise ValueError('评分请求无需参数')
                result = self.hub.campus_service().start_scoring()
            elif re.fullmatch(r'/api/campus/sources/[A-Za-z0-9_-]{1,128}', path):
                result = self.hub.campus_service().configure_source(path.rsplit('/', 1)[1], body)
            elif path == '/api/campus/refresh':
                if body:
                    raise ValueError('刷新请求无需参数')
                result = self.hub.campus_service().start_refresh()
            elif path == '/api/campus/wechat':
                if set(body) != {'url'}:
                    raise ValueError('请提供公众号文章链接')
                result = self.hub.campus_service().import_wechat(body['url'])
            elif re.fullmatch(r'/api/campus/items/[a-f0-9]{64}/state', path):
                result = self.hub.campus_service().set_item_state(path.split('/')[-2], body)
            elif re.fullmatch(r'/api/campus/events/[a-f0-9]{64}/state', path):
                result = self.hub.campus_service().set_event_state(path.split('/')[-2], body)
            elif re.fullmatch(r'/api/campus/items/[a-f0-9]{64}/fetch', path):
                if body:
                    raise ValueError('获取全文无需参数')
                result = self.hub.campus_service().ensure_full_text(path.split('/')[-2])
            elif path == '/api/notifications/test':
                self.hub.require_capability('notifications')
                show_notification('这是一条测试通知，不含聊天内容。')
                result = {'submitted': True}
            elif path == '/api/notifications/message':
                self.hub.require_capability('notifications')
                if set(body) != {'event_id'}:
                    raise ValueError('通知事件参数无效')
                result = self.hub.notify_message_event(
                    body.get('event_id'), f'http://127.0.0.1:{self.server.server_port}')
            elif path == '/api/notifications/activate':
                self.hub.require_capability('notifications')
                if set(body) != {'token'}:
                    raise ValueError('通知会话参数无效')
                result = self.hub.activate_notification_target(body.get('token'))
            elif path == '/api/voice/transcribe':
                result = self.hub.transcribe_voice(body)
            elif path == '/api/qq/forward-resources':
                required = {'account_id', 'conversation_id', 'message_id'}
                if set(body) != required or any(not isinstance(body[key], str) or not body[key]
                                                 for key in required):
                    raise ValueError('合并转发查询参数无效')
                if not self.hub.capability_enabled('media'):
                    raise PermissionError('媒体附件功能已关闭')
                account_id = body['account_id']
                message = self.hub.store.get_message('qq', account_id, body['message_id'])
                if not message or message.get('conversation_id') != body['conversation_id']:
                    raise ValueError('索引中的 QQ 消息不存在或会话不匹配')
                message = {**message, 'platform': 'qq', 'account_id': account_id}
                from integrations.qq.provider import QQProvider
                provider = self.hub.providers.get('qq') or QQProvider()
                with self.hub.lock:
                    self.hub.providers['qq'] = provider
                result = provider.expand_forward_resources(message, account_id, self.hub.storage)
            elif path == '/api/sends/prepare':
                payload = {**body, 'platform': 'qq', **self.hub.resolve_qq_send_target(
                    body.get('account_id'), body.get('conversation_type'),
                    body.get('conversation_id'))}
                result = self.hub.send_service.prepare(payload)
            elif re.fullmatch(r'/api/sends/[A-Za-z0-9_-]{8,80}/approve', path):
                self.hub.require_capability('qq_send')
                request_id = path.split('/')[-2]
                self.hub.consume_send_approval_challenge(request_id, body.get('approval_token'), 'qq')
                result = self.hub.send_service.approve_and_deliver(request_id, 'qq')
            elif re.fullmatch(r'/api/sends/[A-Za-z0-9_-]{8,80}/reject', path):
                if body.get('confirmation') != 'REJECT_SEND':
                    raise ValueError('缺少拒绝确认')
                result = self.hub.send_service.reject(path.split('/')[-2], 'qq')
            elif path == '/api/wechat-sends/prepare':
                readiness = self.hub.wechat_sender.readiness(True)
                if (not isinstance(readiness, dict) or readiness.get('state') != 'ready'
                        or not isinstance(readiness.get('account_id'), str)
                        or not readiness['account_id']):
                    raise ValueError('本机微信客户端不可用；请在已登录的 Windows 主机上打开微信')
                if body.get('account_id') != readiness['account_id']:
                    raise ValueError('account_id must match the currently logged-in WeChat account')
                result = self.hub.send_service.prepare({**body, 'platform': 'wechat'})
            elif re.fullmatch(r'/api/wechat-sends/[A-Za-z0-9_-]{8,80}/approve', path):
                self.hub.require_capability('wechat_send')
                request_id = path.split('/')[-2]
                self.hub.consume_send_approval_challenge(
                    request_id, body.get('approval_token'), 'wechat')
                result = self.hub.send_service.approve_and_deliver(request_id, 'wechat')
            elif re.fullmatch(r'/api/wechat-sends/[A-Za-z0-9_-]{8,80}/reject', path):
                if body.get('confirmation') != 'REJECT_SEND':
                    raise ValueError('缺少拒绝确认')
                result = self.hub.send_service.reject(path.split('/')[-2], 'wechat')
            elif path == '/api/wecom-bot/identity':
                if body.get('remove') is True:
                    if body.get('confirmation') != 'REMOVE_WECOM_BOT_CREDENTIALS':
                        raise ValueError('缺少移除机器人凭据确认')
                    result = self.hub.wecom_bot_credentials.delete()
                else:
                    result = self.hub.wecom_bot_credentials.set(body.get('bot_id'), body.get('secret'))
                with self.hub.lock:
                    self.hub.send_approval_challenges = {
                        key: value for key, value in self.hub.send_approval_challenges.items()
                        if value[2] != 'wecom_bot'
                    }
            elif path == '/api/wecom-bot/identity/remove':
                if body.get('confirmation') != 'REMOVE_WECOM_BOT_CREDENTIALS':
                    raise ValueError('缺少移除机器人凭据确认')
                result = self.hub.wecom_bot_credentials.delete()
                with self.hub.lock:
                    self.hub.send_approval_challenges = {
                        key: value for key, value in self.hub.send_approval_challenges.items()
                        if value[2] != 'wecom_bot'
                    }
            elif path == '/api/wecom-bot-sends/prepare':
                identity = self.hub.wecom_bot_credentials.status()
                if not identity['configured']:
                    raise ValueError('请先在设置中配置企微机器人凭据')
                if body.get('account_id') != identity['bot_id']:
                    raise ValueError('企微机器人账号标识与已配置身份不匹配')
                attachment = body.get('attachment')
                try:
                    if attachment is not None:
                        self.hub.wecom_bot_media.load(attachment)
                    result = self.hub.send_service.prepare({**body, 'platform': 'wecom_bot'})
                except Exception:
                    attachment_id = attachment.get('attachment_id') if isinstance(attachment, dict) else None
                    if attachment_id not in self.hub.send_repository.attachment_ids():
                        self.hub.wecom_bot_media.remove(attachment)
                    raise
            elif re.fullmatch(r'/api/wecom-bot-sends/[A-Za-z0-9_-]{8,80}/approve', path):
                self.hub.require_capability('wecom_bot_send')
                request_id = path.split('/')[-2]
                self.hub.consume_send_approval_challenge(
                    request_id, body.get('approval_token'), 'wecom_bot')
                result = self.hub.send_service.approve_and_deliver(request_id, 'wecom_bot')
            elif re.fullmatch(r'/api/wecom-bot-sends/[A-Za-z0-9_-]{8,80}/reject', path):
                if body.get('confirmation') != 'REJECT_SEND':
                    raise ValueError('缺少拒绝确认')
                request_id = path.split('/')[-2]
                stored = self.hub.send_repository.get(request_id) or {}
                result = self.hub.send_service.reject(request_id, 'wecom_bot')
                if result.get('status') == 'rejected':
                    try:
                        self.hub.wecom_bot_media.remove(json.loads(stored.get('attachment_json') or '{}'))
                    except (ValueError, TypeError):
                        pass
            elif path == '/api/sync':
                if 'conversation_id' in body and not isinstance(body['conversation_id'], str):
                    raise ValueError('QQ 会话标识无效')
                self.hub.start_sync(body.get('platform', 'all'), body.get('conversation_id'))
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
                    capability_changes = body.get('capabilities')
                    if capability_changes is not None:
                        self.hub.set_capabilities(capability_changes)
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
                    if 'notification_filters' in body:
                        self.hub.settings['notification_filters'] = self.hub._normalize_notification_filters(
                            body['notification_filters'])
                    save_json(self.hub.settings_path, self.hub.settings)
                    result = {key: value for key, value in self.hub.settings.items()
                              if key != 'account_lock'}
                    result.update({'account_lock_enabled': self.hub.account_safety.configured,
                              'account_lock_idle_timeout_seconds': self.hub.account_safety.idle_seconds,
                              'storage_dir': str(self.hub.storage),
                              'auto_export_status': self.hub.auto_exports.status(),
                              'capabilities': describe_capabilities(
                                  self.hub.settings.get('capabilities'))})
            elif path == '/api/auto-export/configure':
                self.hub.require_capability('auto_export')
                result = self.hub.auto_exports.configure(body)
            elif path == '/api/auto-export/run':
                self.hub.require_capability('auto_export')
                result = self.hub.auto_exports.run_now()
            elif path == '/api/auto-export/cancel':
                self.hub.require_capability('auto_export')
                result = self.hub.auto_exports.cancel()
            elif path == '/api/resource-jobs':
                items = body.get('items')
                if isinstance(items, list) and any(isinstance(item, dict) and item.get('resource_kind') == 'moment'
                                                   for item in items) and not self.hub.capability_enabled('moments'):
                    raise PermissionError('朋友圈功能已关闭')
                result = self.hub.resource_jobs.start(items)
            elif path == '/api/export/bundle':
                self.hub.require_capability('export')
                self.hub.require_capability('media')
                query = body.get('query')
                if not isinstance(query, dict):
                    raise ValueError('会话导出筛选参数无效')
                allowed = {'format', 'platform', 'account_id', 'conversation_id',
                           'q', 'type', 'start', 'end'}
                if set(query) - allowed or any(not isinstance(value, str) for value in query.values()):
                    raise ValueError('会话导出筛选参数无效')
                if query.get('platform', 'all') not in (*PLATFORMS, 'all'):
                    raise ValueError('会话导出平台无效')
                result = self.hub.resource_jobs.start_bundle(
                    lambda work, cancel: self._build_export_bundle(query, work, cancel))
            elif path.startswith('/api/source/') and path.endswith('/configure'):
                self.hub.configure(path.split('/')[3], body)
                result = {'message': '配置已保存，正在同步'}
            elif path == '/api/qq/start':
                self.hub.start_sync('qq')
                result = {'mode': 'local_database', 'message': '正在读取 QQ 本地数据库，无需扫码'}
            elif path == '/api/qq/key-discovery':
                result = self.hub.start_qq_key_discovery(body.get('confirm'))
            elif path == '/api/qq/history-import':
                result = self.hub.start_qq_history_import(body)
            elif path == '/api/qq/history-import/cancel':
                result = self.hub.cancel_qq_history_import()
            elif path == '/api/backups':
                result = self.hub.create_backup(body.get('include_media', False))
            else:
                self.json({'error': '接口不存在'}, 404)
                return
            self.json(result)
        except PermissionError as exc:
            self.json({'error': str(exc), 'code': 'capability_disabled'}, 403)
        except (ValueError, TypeError) as exc:
            self.json({'error': str(exc)}, 400)
        except RuntimeError as exc:
            if path in ('/api/notifications/test', '/api/notifications/message'):
                safe_errors = {'Windows 桌面通知仅支持 Windows 本机 Hub',
                               'Windows 未接受桌面通知'}
                message = str(exc)
                self.json({'error': message if message in safe_errors
                           else 'Windows 未接受桌面通知'}, 503)
                return
            print('Action failure:', type(exc).__name__, flush=True)
            self.json({'error': '本机操作失败，请检查数据源配置'}, 500)
        except Exception as exc:
            print('Action failure:', type(exc).__name__, flush=True)
            self.json({'error': '本机操作失败，请检查数据源配置'}, 500)

    def upload_wecom_bot_attachment(self):
        if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/octet-stream':
            self.close_connection = True
            self.json({'error': '附件必须使用本机文件选择器上传'}, 415)
            return
        try:
            if not self.hub.wecom_bot_credentials.status()['configured']:
                self.close_connection = True
                raise ValueError('请先在设置中配置企微机器人凭据')
            length = int(self.headers.get('Content-Length', 0))
            if length <= 0 or length > MAX_MEDIA_SIZE:
                self.close_connection = True
                raise ValueError('附件为空或超过 50 MiB 限制')
            filename = unquote(self.headers.get('X-Local-Filename', ''), errors='strict')
            media_type = self.headers.get('X-Local-Media-Type', '')
            result = self.hub.wecom_bot_media.save_stream(self.rfile, length, filename, media_type)
            self.json(result)
        except (ValueError, TypeError, OSError) as exc:
            self.json({'error': str(exc)}, 400)

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

    def session_token(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            return cookie['lmh_session'].value if 'lmh_session' in cookie else None
        except Exception:
            return None

    def account_safety_action(self, path):
        if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
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
            safety = self.hub.account_safety
            token = self.session_token()
            if path.endswith('/setup'):
                if not self.hub.capability_enabled('account_safety'):
                    raise ValueError('请先启用账号安全模块')
                token = safety.setup(body.get('passphrase'))
                if token is None:
                    self.json({'error': '口令锁已经设置'}, 409)
                    return
                self.json(safety.status(token), extra={
                    'Set-Cookie': f'lmh_session={token}; Path=/; HttpOnly; SameSite=Strict'
                })
            elif path.endswith('/unlock'):
                if not safety.configured:
                    self.json({'error': '尚未设置口令锁'}, 409)
                    return
                token, error = safety.unlock(body.get('passphrase'))
                if token is None:
                    status = 429 if error == 'rate_limited' else 401
                    message = '尝试过于频繁，请稍后重试' if status == 429 else '口令不正确'
                    self.json({'error': message}, status)
                    return
                self.json(safety.status(token), extra={
                    'Set-Cookie': f'lmh_session={token}; Path=/; HttpOnly; SameSite=Strict'
                })
            elif path.endswith('/activity'):
                if not safety.configured or not safety.authenticated(token, touch=True):
                    self.json({'error': '应用已锁定', 'code': 'account_locked'}, 423)
                    return
                self.json({'active': True})
            elif path.endswith('/lock'):
                if not safety.configured or not safety.authenticated(token):
                    self.json({'error': '当前会话未解锁'}, 423)
                    return
                safety.lock()
                self.json({'configured': True, 'locked': True}, extra={
                    'Set-Cookie': 'lmh_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'
                })
            else:
                if not safety.configured or not safety.authenticated(token):
                    self.json({'error': '当前会话未解锁'}, 423)
                    return
                safety.disable(body.get('current_passphrase'))
                self.json({'configured': False, 'locked': False}, extra={
                    'Set-Cookie': 'lmh_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0'
                })
        except (ValueError, TypeError) as exc:
            self.json({'error': str(exc)}, 400)

    def restore_upload(self):
        if self.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/octet-stream':
            self.json({'error': '请上传 SQLite 备份或本机媒体 ZIP 备份'}, 415)
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
            extension = '.zip' if zipfile.is_zipfile(temp_path) else '.sqlite3'
            name = time.strftime('lmh-backup-restored-%Y%m%dT%H%M%SZ-', time.gmtime()) + secrets.token_hex(4) + extension
            saved_path = self.hub.backup_dir / name
            os.replace(temp_path, saved_path)
            stat = saved_path.stat()
            self.json({'id': name, 'size': stat.st_size, 'created_at': int(stat.st_mtime),
                       'kind': 'media' if extension == '.zip' else 'index'})
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

    def send_local_media(self, path, mime, range_header, headers):
        try:
            source = path.open('rb')
            size = os.fstat(source.fileno()).st_size
        except OSError:
            self.json({'error': '本机媒体文件不可用'}, 404)
            return
        status, start, end = 200, 0, size - 1
        response_headers = {**headers, 'Accept-Ranges': 'bytes'}
        if range_header:
            try:
                unit, value = range_header.split('=', 1)
                if unit.strip().lower() != 'bytes' or ',' in value:
                    raise ValueError
                first, last = value.strip().split('-', 1)
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                    if start >= size or end < start:
                        raise ValueError
                    end = min(end, size - 1)
                else:
                    suffix = int(last)
                    if suffix <= 0 or size == 0:
                        raise ValueError
                    start = max(0, size - suffix)
                    end = size - 1
                if size == 0:
                    raise ValueError
                status = 206
                response_headers['Content-Range'] = f'bytes {start}-{end}/{size}'
            except (ValueError, TypeError):
                source.close()
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                return
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(length))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        for key, value in response_headers.items():
            self.send_header(key, value)
        self.end_headers()
        try:
            source.seek(start)
            remaining = length
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            source.close()

    def preview_local_image(self, path, mime):
        if not self.hub.media_preview_lock.acquire(timeout=60):
            raise ValueError('本机图片预览排队超时，仍可下载原件')
        try:
            path = Path(path)
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError
            payload = _read_cached_snapshot(path, 64 * 1024 * 1024,
                                            '图片超过本机预览的 64 MiB 限制')
            if mime == 'image/tiff':
                valid = payload.startswith((b'II*\x00', b'MM\x00*', b'II+\x00', b'MM\x00+'))
            else:
                box_size = int.from_bytes(payload[:4], 'big') if len(payload) >= 16 else 0
                valid = False
                if (payload[4:8] == b'ftyp' and 16 <= box_size <= min(len(payload), 4096)
                        and (box_size - 16) % 4 == 0):
                    brands = [payload[8:12]] + [payload[offset:offset + 4]
                                                 for offset in range(16, box_size, 4)]
                    valid = any(brand in (b'heic', b'heix', b'hevc', b'hevx', b'mif1', b'msf1')
                                for brand in brands)
            if not valid:
                raise ValueError('本机图片格式无效')
            from integrations.wechat.vendor.media import MediaDownloader
            ffmpeg = MediaDownloader._ffmpeg_exe()
            if not ffmpeg:
                raise ValueError('本机图片预览转换器不可用')
            with tempfile.TemporaryDirectory(prefix='lmh-preview-') as temp_dir:
                source = Path(temp_dir) / ('source.tiff' if mime == 'image/tiff' else 'source.heic')
                output = Path(temp_dir) / 'preview.jpg'
                source.write_bytes(payload)
                del payload
                result = subprocess.run([
                    ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-threads', '1',
                    '-max_pixels', '24000000', '-i', str(source), '-frames:v', '1', '-vf',
                    "scale=w='min(1600,iw)':h='min(1600,ih)':force_original_aspect_ratio=decrease",
                    '-q:v', '4', '-fs', '16M', '-y', str(output),
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
                if result.returncode or not output.is_file() or output.stat().st_size > 16 * 1024 * 1024:
                    raise ValueError('本机无法预览此图片，仍可下载原件')
                preview = output.read_bytes()
                if not preview.startswith(b'\xff\xd8\xff') or not preview.endswith(b'\xff\xd9'):
                    raise ValueError('本机无法预览此图片，仍可下载原件')
                return preview
        except subprocess.TimeoutExpired as exc:
            raise ValueError('本机图片预览转换超时，仍可下载原件') from exc
        finally:
            self.hub.media_preview_lock.release()

    def static(self, urlpath, params=None):
        params = params or {}
        if urlpath.startswith('/local-moment-media/'):
            if not self.hub.capability_enabled('moments'):
                self.json(self.hub.capability_error('moments'), 403)
                return
            token = urlpath.rsplit('/', 1)[-1]
            if not re.fullmatch(r'[A-Za-z0-9_-]{32}', token):
                self.json({'error': '媒体链接无效'}, 404)
                return
            with self.hub.lock:
                media_entry = self.hub.moment_media_paths.get(token)
                if media_entry and media_entry[3] <= time.monotonic():
                    self.hub.moment_media_paths.pop(token, None)
                    media_entry = None
            if media_entry is None:
                self.json({'error': '本机媒体链接已过期，请重新打开'}, 404)
                return
            path, cache_root, mime, _expires_at = media_entry
            try:
                if path.is_symlink() or not path.resolve(strict=True).is_relative_to(cache_root):
                    raise OSError
            except (OSError, RuntimeError):
                self.json({'error': '本机媒体文件不可用'}, 404)
                return
            self.send_local_media(path, mime, self.headers.get('Range'), {})
            return
        if urlpath.startswith('/local-media/'):
            if not self.hub.capability_enabled('media'):
                self.json(self.hub.capability_error('media'), 403)
                return
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
        if path.suffix.lower() in ('.heic', '.heif'):
            mime = 'image/heic'
        if urlpath.startswith('/local-media/'):
            if params.get('preview') == 'jpeg' and mime in ('image/heic', 'image/tiff'):
                try:
                    preview = self.preview_local_image(path, mime)
                except ValueError as exc:
                    self.json({'error': str(exc)}, 422)
                    return
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    self.json({'error': '本机无法预览此图片，仍可下载原件'}, 422)
                    return
                self.send_bytes(preview, 'image/jpeg')
                return
            headers = {}
            safe_inline = {'image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/avif',
                           'audio/wav', 'audio/x-wav', 'audio/mpeg', 'audio/ogg', 'audio/mp4',
                           'audio/aac', 'audio/flac', 'audio/x-ms-wma', 'video/mp4', 'video/webm'}
            if mime == 'image/tiff':
                try:
                    with path.open('rb') as source:
                        is_tiff = source.read(4) in (b'II*\x00', b'MM\x00*', b'II+\x00', b'MM\x00+')
                except OSError:
                    is_tiff = False
                if is_tiff:
                    safe_inline.add(mime)
            if mime == 'image/bmp':
                try:
                    with path.open('rb') as source:
                        is_bmp = source.read(2) == b'BM'
                except OSError:
                    is_bmp = False
                if is_bmp:
                    safe_inline.add(mime)
            if mime not in safe_inline:
                headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(path.name)
            self.send_local_media(path, mime, self.headers.get('Range'), headers)
            return
        data = path.read_bytes()
        headers = {'Accept-Ranges': 'bytes'}
        self.send_bytes(data, mime, extra=headers)

    @staticmethod
    def capability_for_path(path, method='GET'):
        if path.startswith('/api/campus/'):
            return 'campus'
        if path in ('/api/events/messages', '/api/notifications/test',
                    '/api/notifications/message', '/api/notifications/activate'):
            return 'notifications'
        if path in ('/api/avatar', '/api/contacts'):
            return 'contacts'
        if path == '/api/search':
            return 'search'
        if path in ('/api/conversations', '/api/messages', '/api/context',
                    '/api/quoted-message', '/api/conversation-members'):
            return 'chat'
        if path in ('/api/accounts', '/api/media', '/api/media/resolve', '/api/resource-jobs') or path.startswith('/api/resource-jobs/'):
            return 'media'
        if path == '/api/qq/forward-resources' or path.startswith('/api/qq/forward-resources/'):
            return 'media'
        if path in ('/api/voice/transcribe', '/api/voice/transcription-status'):
            return 'voice_transcription'
        if path == '/api/stats':
            return 'stats'
        if path in ('/api/moments', '/api/moments/export'):
            return 'moments'
        if path.startswith('/local-moment-media/'):
            return 'moments'
        if path in ('/api/analytics/private', '/api/analytics/groups', '/api/analytics/self-sent-daily'):
            return 'advanced_analytics'
        if path in ('/api/analytics/relationships', '/api/analytics/relationships/timeline',
                    '/api/analytics/relationships/achievements'):
            return 'relationship_footprint'
        if path in ('/api/reports/annual', '/api/reports/pair'):
            return 'annual_reports'
        if path == '/api/sends' or path.startswith('/api/sends/'):
            return 'qq_send'
        if path == '/api/wechat-sends' or path.startswith('/api/wechat-sends/'):
            return 'wechat_send'
        if path == '/api/qq/send-readiness':
            return None
        if path == '/api/wechat-send-readiness':
            return 'wechat_send'
        if path == '/api/wecom-bot/identity':
            return 'wecom_bot_send'
        if path == '/api/wecom-bot/identity/remove' and method == 'POST':
            return None
        if path == '/api/wecom-bot-sends' or path.startswith('/api/wecom-bot-sends/'):
            return 'wecom_bot_send'
        if path in ('/api/export', '/api/export/contacts', '/api/export/group-members'):
            return 'export'
        if path == '/api/export/bundle':
            return 'media' if method == 'POST' else 'export'
        if path == '/api/sync' or (path.startswith('/api/source/') and path.endswith('/configure')):
            return 'sync'
        if path.startswith('/api/auto-export'):
            return 'auto_export'
        if path.startswith('/api/imports'):
            return 'imports'
        if path in ('/api/qq/history-import', '/api/qq/history-import/cancel'):
            return 'qq_history'
        if path in ('/api/qq/start', '/api/qq/local-status', '/api/qq/login-status',
                    '/api/qq/key-discovery'):
            return 'sync'
        if path in ('/api/conversation-metadata', '/api/contact-identities', '/api/contact-identities/unlink'):
            return 'organization'
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--storage', default=os.environ.get('LMH_STORAGE_DIR', str(LOCAL)))
    parser.add_argument('--no-autosync', action='store_true')
    args = parser.parse_args()
    hub = Hub(args.storage)
    hub.auto_exports.start()
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
        hub.campus_service().start_updates(lambda: hub.capability_enabled('campus'))
    print(f'LocalChatHub http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop.set()
        server.server_close()
        hub.close()


if __name__ == '__main__':
    main()
