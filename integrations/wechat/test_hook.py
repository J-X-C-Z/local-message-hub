"""Synthetic tests for the optional loopback WeChat Hook sender."""

import unittest

from .hook import WeChatHookTextSender
from .sender import WeChatDeliveryUnknown, WeChatSendError


CONFIG = {
    "base_url": "http://127.0.0.1:30011",
    "token": "a" * 64,
    "expected_version": "4.1.10.27",
    "request_timeout_seconds": 5,
    "receipt_timeout_seconds": 1,
}


class FakeReader:
    def __init__(self, account="account", new_receipt=True):
        self.wxid = account
        self.account = ""
        self.new_receipt = new_receipt
        self.calls = 0

    def get_messages(self, _conversation_id, limit=5):
        self.calls += 1
        old = {
            "local_id": 1, "shard": "message_0.db", "is_self": True,
            "sender_username": self.wxid, "content": "old",
        }
        if self.calls == 1 or not self.new_receipt:
            return [old]
        return [{
            "local_id": 2, "shard": "message_0.db", "is_self": True,
            "sender_username": self.wxid, "content": "hello",
        }, old]


class HookSenderTests(unittest.TestCase):
    def sender(self, reader=None, transport=None):
        return WeChatHookTextSender(
            reader_factory=lambda: reader or FakeReader(),
            timeout=1,
            sleep=lambda _seconds: None,
            config_loader=lambda: dict(CONFIG),
            transport=transport,
        )

    def test_readiness_distinguishes_logged_out_and_ready(self):
        def transport(_config, method, path, payload=None):
            self.assertEqual((method, path), ("GET", "/WeChatAgent/health"))
            return {
                "backend": "wechat-hook", "startupState": "ready",
                "version": "4.1.10.27", "databaseAccounts": [],
            }

        self.assertEqual(self.sender(transport=transport).readiness()["state"],
                         "client_logged_out")

        def ready(_config, _method, _path, _payload=None):
            return {
                "backend": "wechat-hook", "startupState": "ready",
                "version": "4.1.10.27", "databaseAccounts": ["account"],
            }

        result = self.sender(reader=FakeReader(), transport=ready).readiness()
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["account_id"], "account")
        self.assertEqual(result["transport"], "wechat-hook")

    def test_version_mismatch_fails_closed(self):
        def transport(_config, _method, _path, _payload=None):
            return {
                "backend": "wechat-hook", "startupState": "ready",
                "version": "4.1.15.13", "databaseAccounts": ["account"],
            }

        result = self.sender(transport=transport).readiness()
        self.assertEqual(result["state"], "wechat_hook_version_mismatch")

    def test_send_uses_native_hook_then_requires_new_database_receipt(self):
        calls = []

        def transport(_config, method, path, payload=None):
            calls.append((method, path, payload))
            if path == "/WeChatAgent/health":
                return {
                    "backend": "wechat-hook", "startupState": "ready",
                    "version": "4.1.10.27", "databaseAccounts": ["account"],
                }
            return {"ret": 0, "retmsg": "accepted"}

        result = self.sender(transport=transport).deliver({
            "account_id": "account", "conversation_id": "friend", "text": "hello",
        })

        self.assertEqual(result["message_id"], "friend:message_0.db:2")
        self.assertEqual(calls[-1], (
            "POST", "/SendTextMsg", {"wxidorgid": "friend", "msg": "hello"}))

    def test_missing_receipt_is_unknown_not_a_retryable_error(self):
        def transport(_config, _method, path, _payload=None):
            if path == "/WeChatAgent/health":
                return {
                    "backend": "wechat-hook", "startupState": "ready",
                    "version": "4.1.10.27", "databaseAccounts": ["account"],
                }
            return {"ret": 0}

        sender = self.sender(reader=FakeReader(new_receipt=False), transport=transport)
        with self.assertRaises(WeChatDeliveryUnknown):
            sender.deliver({
                "account_id": "account", "conversation_id": "friend", "text": "hello",
            })

    def test_account_mismatch_does_not_submit(self):
        calls = []

        def transport(_config, _method, path, payload=None):
            calls.append(path)
            return {
                "backend": "wechat-hook", "startupState": "ready",
                "version": "4.1.10.27", "databaseAccounts": ["account"],
            }

        with self.assertRaises(WeChatSendError):
            self.sender(transport=transport).deliver({
                "account_id": "other", "conversation_id": "friend", "text": "hello",
            })
        self.assertNotIn("/SendTextMsg", calls)


if __name__ == "__main__":
    unittest.main()
