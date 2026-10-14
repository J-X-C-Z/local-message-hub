"""Local WebUI passphrase gate with an in-memory idle-expiring session."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time


_ITERATIONS = 310_000
_MIN_PASSPHRASE = 12
_MAX_SESSIONS = 64


class AccountSafety:
    def __init__(self, settings, persist, idle_seconds=900):
        self.settings = settings
        self.persist = persist
        self.idle_seconds = idle_seconds
        self._lock = threading.RLock()
        self._sessions = {}
        self._failures = []
        self._blocked_until = 0.0

    @property
    def configured(self):
        record = self.settings.get('account_lock')
        return (isinstance(record, dict) and isinstance(record.get('salt'), str) and
                isinstance(record.get('digest'), str))

    def status(self, token=None):
        return {'configured': self.configured, 'locked': self.configured and not self.authenticated(token),
                'idle_timeout_seconds': self.idle_seconds,
                'scope': '本机 WebUI 与 HTTP API；独立 MCP stdio 客户端不受此锁控制'}

    def set_passphrase(self, value):
        if not isinstance(value, str) or not _MIN_PASSPHRASE <= len(value) <= 256:
            raise ValueError('口令长度须为 12 至 256 个字符')
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac('sha256', value.encode('utf-8'), salt, _ITERATIONS)
        with self._lock:
            self.settings['account_lock'] = {'salt': salt.hex(), 'digest': digest.hex(),
                                             'iterations': _ITERATIONS}
            self._sessions.clear()
            self.persist(self.settings)

    def setup(self, value):
        """Atomically configure the first passphrase and issue its session."""
        with self._lock:
            if self.configured:
                return None
            self.set_passphrase(value)
            token, error = self.unlock(value)
            return token if not error else None

    def verify(self, value):
        record = self.settings.get('account_lock')
        if not isinstance(value, str) or not isinstance(record, dict):
            return False
        try:
            salt = bytes.fromhex(record['salt'])
            expected = bytes.fromhex(record['digest'])
            iterations = record['iterations']
            if len(salt) != 16 or len(expected) != 32 or iterations != _ITERATIONS:
                return False
        except (KeyError, TypeError, ValueError):
            return False
        actual = hashlib.pbkdf2_hmac('sha256', value.encode('utf-8'), salt, iterations)
        return hmac.compare_digest(actual, expected)

    def unlock(self, value):
        now = time.monotonic()
        with self._lock:
            if now < self._blocked_until:
                return None, 'rate_limited'
            if not self.verify(value):
                self._failures = [stamp for stamp in self._failures if now - stamp < 60]
                self._failures.append(now)
                if len(self._failures) >= 5:
                    self._blocked_until = now + 60
                    self._failures.clear()
                return None, 'invalid_passphrase'
            self._failures.clear()
            self._sessions = {key: expiry for key, expiry in self._sessions.items()
                              if expiry > now}
            if len(self._sessions) >= _MAX_SESSIONS:
                oldest_key = min(self._sessions, key=self._sessions.get)
                self._sessions.pop(oldest_key, None)
            token = secrets.token_urlsafe(32)
            self._sessions[self._digest_token(token)] = now + self.idle_seconds
            return token, ''

    def authenticated(self, token, touch=False):
        if not self.configured:
            return True
        if not isinstance(token, str) or not token:
            return False
        now = time.monotonic()
        key = self._digest_token(token)
        with self._lock:
            expiry = self._sessions.get(key)
            if expiry is None:
                return False
            if expiry <= now:
                self._sessions.pop(key, None)
                return False
            if touch:
                self._sessions[key] = now + self.idle_seconds
            return True

    def lock(self):
        with self._lock:
            self._sessions.clear()

    def disable(self, current_passphrase):
        with self._lock:
            if not self.verify(current_passphrase):
                raise ValueError('当前口令不正确')
            self.settings.pop('account_lock', None)
            self._sessions.clear()
            self.persist(self.settings)

    @staticmethod
    def _digest_token(token):
        return hashlib.sha256(token.encode('utf-8')).digest()
