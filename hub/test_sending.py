import tempfile
import threading
import unittest
from pathlib import Path

from hub.sending import SendRepository, SendService
from integrations.qq.sender import QQDeliveryUnknown


class FakeAdapter:
    def __init__(self, result=None, error=None):
        self.result = result or {"message_id": "synthetic-1"}
        self.error = error
        self.calls = 0

    def deliver(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class BlockingAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def deliver(self, request):
        self.calls += 1
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError()
        return self.result


class SendServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repository = SendRepository(Path(self.temp.name) / "hub.sqlite3")
        self.adapter = FakeAdapter()
        self.service = SendService(self.repository, self.adapter)
        self.payload = {"platform": "qq", "account_id": "12345678",
                        "conversation_type": "private", "conversation_id": "87654321",
                        "text": "synthetic outbound message", "request_id": "test-send-0001"}

    def tearDown(self):
        self.repository.close()
        self.temp.cleanup()

    def test_prepare_is_durable_and_never_delivers(self):
        prepared = self.service.prepare(self.payload)
        self.assertEqual(prepared["status"], "pending_approval")
        self.assertEqual(prepared["audit"][0]["action"], "prepared")
        self.assertEqual(self.adapter.calls, 0)
        self.assertEqual(self.service.status(prepared["request_id"])["text"], self.payload["text"])

    def test_same_request_id_is_idempotent_and_conflicting_payload_is_rejected(self):
        first = self.service.prepare(self.payload)
        duplicate = self.service.prepare(self.payload)
        self.assertTrue(first["created"])
        self.assertFalse(duplicate["created"])
        with self.assertRaisesRegex(ValueError, "不同消息"):
            self.service.prepare({**self.payload, "text": "different"})
        self.assertEqual(self.adapter.calls, 0)

    def test_approval_delivers_once_and_audits(self):
        prepared = self.service.prepare(self.payload)
        sent = self.service.approve_and_deliver(prepared["request_id"])
        again = self.service.approve_and_deliver(prepared["request_id"])
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(sent["message_id"], "synthetic-1")
        self.assertEqual(again["status"], "sent")
        self.assertEqual(self.adapter.calls, 1)

    def test_source_conversation_id_is_review_only_and_never_a_target(self):
        # A resolved local uid is reported for review but is neither stored nor
        # replayed as a different request, so a replay cannot retarget a send.
        source = "u_-PBswiplK-7J7bmaQLA-mA"
        prepared = self.service.prepare({**self.payload, "conversation_id": "2747277822",
                                         "source_conversation_id": source})
        self.assertEqual(prepared["source_conversation_id"], source)
        self.assertNotIn(source, prepared["text"])
        self.assertEqual(self.adapter.calls, 0)
        stored = self.repository.get(self.payload["request_id"])
        self.assertEqual(stored["conversation_id"], "2747277822")
        self.assertNotIn("source_conversation_id", stored)
        # The same request id with only the source spelling changed stays the
        # same durable request instead of colliding as a different message.
        duplicate = self.service.prepare({**self.payload, "conversation_id": "2747277822",
                                          "source_conversation_id": "u_1v7dkRylD5gexBywVtZIzw"})
        self.assertFalse(duplicate["created"])
        self.assertEqual(duplicate["request_id"], prepared["request_id"])
        self.assertEqual(self.repository.get(prepared["request_id"])["payload_hash"],
                         stored["payload_hash"])
        # A non-string source is ignored rather than echoed into the record.
        ignored = self.service.prepare({**self.payload, "request_id": "test-send-0002",
                                        "source_conversation_id": {"uid": source}})
        self.assertNotIn("source_conversation_id", ignored)
        self.assertNotIn("source_conversation_id",
                         self.repository.get("test-send-0002"))
        # Status reads keep the resolved number as the delivered target.
        self.assertEqual(self.service.status(prepared["request_id"])["conversation_id"],
                         "2747277822")
        self.assertNotIn("source_conversation_id",
                         self.service.status(prepared["request_id"]))

    def test_timeout_is_unknown_and_is_not_retried(self):
        self.adapter.error = TimeoutError()
        prepared = self.service.prepare(self.payload)
        result = self.service.approve_and_deliver(prepared["request_id"])
        again = self.service.approve_and_deliver(prepared["request_id"])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["error_code"], "timeout")
        self.assertEqual(again["status"], "unknown")
        self.assertEqual(self.adapter.calls, 1)

    def test_ambiguous_delivery_is_unknown_and_is_not_retried(self):
        self.adapter.error = QQDeliveryUnknown("onebot_receipt_missing")
        prepared = self.service.prepare(self.payload)
        result = self.service.approve_and_deliver(prepared["request_id"])
        again = self.service.approve_and_deliver(prepared["request_id"])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["error_code"], "onebot_receipt_missing")
        self.assertEqual(again["status"], "unknown")
        self.assertEqual(self.adapter.calls, 1)

    def test_validation_rejects_non_qq_targets_and_non_text_payloads(self):
        with self.assertRaisesRegex(ValueError, "request_id 格式无效"):
            self.service.prepare({key: value for key, value in self.payload.items() if key != "request_id"})
        with self.assertRaisesRegex(ValueError, "平台不支持"):
            self.service.prepare({**self.payload, "platform": "telegram"})
        with self.assertRaisesRegex(ValueError, "会话标识"):
            self.service.prepare({**self.payload, "conversation_id": "https://example.test"})
        with self.assertRaisesRegex(ValueError, "文本不能为空"):
            self.service.prepare({**self.payload, "text": "  "})

    def test_wechat_uses_its_separate_adapter_and_is_identity_labeled(self):
        wechat = FakeAdapter({"message_id": "wxid_friend:message_1.db:12"})
        service = SendService(self.repository, {"qq": self.adapter, "wechat": wechat})
        request = service.prepare({**self.payload, "platform": "wechat",
                                   "account_id": "wxid_self0001",
                                   "conversation_id": "wxid_friend001"})
        self.assertEqual(request["identity_label"], "当前登录的本机微信账号（GUI 拟人操作）")
        self.assertEqual(service.list(platform="wechat")["items"][0]["request_id"],
                         request["request_id"])
        self.assertIsNone(service.status(request["request_id"], "qq"))
        self.assertIsNone(service.status(request["request_id"], "wecom_bot"))
        delivered = service.approve_and_deliver(request["request_id"], "wechat")
        self.assertEqual(delivered["status"], "sent")
        self.assertEqual(wechat.calls, 1)
        self.assertEqual(self.adapter.calls, 0)

    def test_wechat_rejects_identifiers_that_are_not_wechat_identities(self):
        service = SendService(self.repository, {"qq": self.adapter, "wechat": FakeAdapter()})
        for identifier in (None, 12, "", "no spaces", "a" * 65, "wxid/../../etc"):
            with self.assertRaisesRegex(ValueError, "标识无效"):
                service.prepare({**self.payload, "platform": "wechat",
                                 "account_id": "wxid_self0001", "conversation_id": identifier})


    def test_ambiguous_wechat_delivery_is_unknown_and_is_not_retried(self):
        from integrations.wechat.sender import WeChatDeliveryUnknown
        wechat = FakeAdapter(error=WeChatDeliveryUnknown("wechat_receipt_missing"))
        service = SendService(self.repository, {"qq": self.adapter, "wechat": wechat})
        request = service.prepare({**self.payload, "platform": "wechat",
                                  "account_id": "wxid_self0001",
                                  "conversation_id": "wxid_friend001"})
        result = service.approve_and_deliver(request["request_id"])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["error_code"], "wechat_receipt_missing")
        self.assertEqual(service.approve_and_deliver(request["request_id"])["status"], "unknown")
        self.assertEqual(wechat.calls, 1)

    def test_wecom_bot_uses_its_separate_adapter_and_is_identity_labeled(self):
        bot = FakeAdapter({"message_id": "bot-receipt-1", "identity": "wecom_bot"})
        service = SendService(self.repository, {"qq": self.adapter, "wecom_bot": bot})
        request = service.prepare({**self.payload, "platform": "wecom_bot",
                                   "account_id": "bot_123", "conversation_id": "user-1"})
        self.assertEqual(request["identity_label"], "企业微信智能机器人（非当前员工身份）")
        self.assertEqual(service.list(platform="wecom_bot")["items"][0]["request_id"],
                         request["request_id"])
        self.assertIsNone(service.status(request["request_id"], "qq"))
        delivered = service.approve_and_deliver(request["request_id"], "wecom_bot")
        self.assertEqual(delivered["status"], "sent")
        self.assertEqual(bot.calls, 1)
        self.assertEqual(self.adapter.calls, 0)

    def test_wecom_media_attachment_can_only_be_bound_to_one_request(self):
        attachment = {"attachment_id": "a" * 48, "filename": "photo.png",
                      "media_type": "image", "size": 12, "sha256": "b" * 64}
        payload = {**self.payload, "platform": "wecom_bot", "account_id": "bot_123",
                   "conversation_id": "user-1", "text": "", "attachment": attachment}
        first = self.service.prepare({**payload, "request_id": "media-send-0001"})
        self.assertEqual(first["attachment"], {"filename": "photo.png", "media_type": "image", "size": 12})
        with self.assertRaisesRegex(ValueError, "重新选择"):
            self.service.prepare({**payload, "request_id": "media-send-0002"})

    def test_wecom_media_request_rejects_unreviewed_text(self):
        attachment = {"attachment_id": "a" * 48, "filename": "photo.png",
                      "media_type": "image", "size": 12, "sha256": "b" * 64}
        payload = {**self.payload, "platform": "wecom_bot", "account_id": "bot_123",
                   "conversation_id": "user-1", "text": "not shown in approval", "attachment": attachment}
        with self.assertRaisesRegex(ValueError, "不能附带文本"):
            self.service.prepare(payload)

    def test_only_live_requests_retain_staged_media(self):
        base = {**self.payload, "platform": "wecom_bot", "account_id": "bot_123",
                "conversation_id": "user-1", "text": ""}
        requests = {}
        for name in ("pending", "sending", "rejected", "sent", "unknown"):
            attachment = {"attachment_id": (str(len(requests) + 1) * 48)[:48],
                          "filename": "photo.png", "media_type": "image", "size": 12,
                          "sha256": "b" * 64}
            request_id = f"media-{name}-0001"
            requests[name] = self.service.prepare({**base, "request_id": request_id,
                                                    "attachment": attachment})["request_id"]

        self.repository.claim(requests["sending"])
        self.service.reject(requests["rejected"])
        self.service.approve_and_deliver(requests["sent"])
        self.adapter.error = TimeoutError()
        self.service.approve_and_deliver(requests["unknown"])

        self.assertEqual(self.repository.attachment_ids(), {
            "1" * 48, "2" * 48,
        })

    def test_repository_survives_reopen(self):
        request_id = self.service.prepare(self.payload)["request_id"]
        self.repository.close()
        self.repository = SendRepository(Path(self.temp.name) / "hub.sqlite3")
        self.service = SendService(self.repository, self.adapter)
        self.assertEqual(self.service.status(request_id)["status"], "pending_approval")

    def test_restart_recovers_inflight_request_as_unknown_without_retry(self):
        request_id = self.service.prepare(self.payload)["request_id"]
        _, claimed = self.repository.claim(request_id)
        self.assertTrue(claimed)
        self.assertEqual(self.adapter.calls, 0)

        self.repository.close()
        self.repository = SendRepository(Path(self.temp.name) / "hub.sqlite3")
        self.service = SendService(self.repository, self.adapter, recover_inflight=True)

        recovered = self.service.status(request_id)
        self.assertEqual(recovered["status"], "unknown")
        self.assertEqual(recovered["error_code"], "interrupted")
        self.assertEqual([item["action"] for item in recovered["audit"]],
                         ["prepared", "approved", "recovered_unknown"])
        self.assertEqual(recovered["audit"][-1]["status"], "unknown")
        self.assertEqual(self.service.approve_and_deliver(request_id)["status"], "unknown")
        self.assertEqual(self.adapter.calls, 0)

    def test_concurrent_approvals_claim_once_and_call_adapter_once(self):
        adapter = BlockingAdapter()
        service = SendService(self.repository, adapter)
        request_id = service.prepare(self.payload)["request_id"]
        first_result = []
        first = threading.Thread(target=lambda: first_result.append(
            service.approve_and_deliver(request_id)))
        first.start()
        self.assertTrue(adapter.started.wait(timeout=2), "first approval should reach adapter")
        second = service.approve_and_deliver(request_id)
        self.assertEqual(second["status"], "sending")
        self.assertEqual(adapter.calls, 1)
        adapter.release.set()
        first.join(timeout=2)
        self.assertFalse(first.is_alive(), "first approval should finish after adapter release")
        self.assertEqual(first_result[0]["status"], "sent")
        self.assertEqual(adapter.calls, 1)


    def test_wechat_rejects_identifiers_that_are_not_wechat_identities(self):
        service = SendService(self.repository, {"qq": self.adapter, "wechat": FakeAdapter()})
        for identifier in ("", "no spaces", "a" * 65, "wxid/../../etc"):
            with self.assertRaisesRegex(ValueError, "标识无效"):
                service.prepare({**self.payload, "platform": "wechat",
                                 "account_id": "wxid_self0001", "conversation_id": identifier})

    def test_wechat_conversation_type_must_match_the_chatroom_suffix(self):
        service = SendService(self.repository, {"qq": self.adapter, "wechat": FakeAdapter()})
        base = {**self.payload, "platform": "wechat", "account_id": "wxid_self0001"}
        with self.assertRaisesRegex(ValueError, "会话类型与微信会话标识不匹配"):
            service.prepare({**base, "conversation_type": "private",
                             "conversation_id": "44766057488@chatroom"})
        with self.assertRaisesRegex(ValueError, "会话类型与微信会话标识不匹配"):
            service.prepare({**base, "conversation_type": "group",
                             "conversation_id": "wxid_friend001"})
        service.prepare({**base, "conversation_type": "group",
                         "conversation_id": "44766057488@chatroom",
                         "request_id": "wechat_group_001"})
        service.prepare({**base, "conversation_type": "private",
                         "conversation_id": "wxid_friend001",
                         "request_id": "wechat_private_001"})

    def test_wechat_media_requests_are_rejected(self):
        wechat = FakeAdapter()
        service = SendService(self.repository, {"qq": self.adapter, "wechat": wechat})
        with self.assertRaisesRegex(ValueError, "附件信息无效"):
            service.prepare({**self.payload, "platform": "wechat",
                             "account_id": "wxid_self0001",
                             "conversation_id": "wxid_friend001", "text": "",
                             "attachment": {"attachment_id": "a" * 48, "filename": "a.png",
                                            "media_type": "image", "size": 12, "sha256": "b" * 64}})
        self.assertEqual(wechat.calls, 0)


if __name__ == "__main__":
    unittest.main()
