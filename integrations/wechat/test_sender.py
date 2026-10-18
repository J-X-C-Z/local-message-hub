"""Synthetic WeChat GUI sender tests; never touch a real Weixin window.

Every test drives :class:`WeChatGUISender` through injected doubles, so no test
can type into, or screenshot, the user's live WeChat client.
"""

import tempfile
import unittest
import ctypes

from .sender import (MAIN_WINDOW_TITLES, WINDOW_CLASS, _INPUT, _Win32, _is_chat_composer,
                     WeChatDeliveryUnknown, WeChatGUISender, WeChatSendError)


class FakeGrab:
    def __init__(self, dark=0.5):
        self.dark_value = dark

    def dark_ratio(self, ratios):
        return self.dark_value


class FakeWin32:
    """Stand-in for the Win32 layer that records every input it was asked to send."""

    def __init__(self, windows=(), grab=None, locked=False, login_handle=None):
        self.windows = list(windows)
        self.sizes = {9: (760, 560), 10: (760, 560), 11: (760, 560)}
        self.grab_object = grab or FakeGrab()
        self.locked = locked
        self.login_handle = login_handle
        self.clipboard = None
        self.unicode_inputs = []
        self.keys = []
        self.clicks = []
        self.foreground = self.windows[0][0] if self.windows else 1

    def enumerate_visible(self):
        return list(self.windows)

    def main_window(self):
        for handle, _cls, title in self.windows:
            if _cls == WINDOW_CLASS and title in MAIN_WINDOW_TITLES:
                return handle
        return None

    def login_window(self, handles):
        """Synthetic stand-in for the read-only UI Automation login probe."""
        return self.login_handle if self.login_handle in handles else None

    def size_of(self, handle):
        return self.sizes.get(handle, (0, 0))

    def rect_of(self, handle):
        return (0, 0) + self.size_of(handle)

    def is_foreground(self, handle):
        return handle == self.foreground

    def set_foreground(self, handle):
        self.foreground = handle

    def input_desktop_locked(self):
        return self.locked

    def key(self, virtual, ctrl=False):
        self.keys.append((virtual, ctrl))

    def click_at(self, x, y):
        self.clicks.append((x, y))

    def unicode_text(self, text):
        self.unicode_inputs.append(text)

    def grab(self, handle):
        return self.grab_object


class FakeReader:
    """Read-only stand-in that reports the newest stored rows."""

    def __init__(self, rows=None, nickname="Synthetic Friend"):
        self.rows = list(rows or [])
        self.nickname = nickname
        self.wxid = "wxid_self0001"
        self.account = "wxid_self0001"
        self.before = None

    def get_messages(self, username, limit=20, offset=0):
        if self.before is not None:
            return list(self.before)
        return list(self.rows)

    def get_nickname(self, username):
        return self.nickname

    def usernames_for_display_name(self, display_name):
        return ["wxid_friend001"]


class UnconfirmedReader(FakeReader):
    """Rows only appear after the sender has already given up waiting."""

    def get_messages(self, username, limit=20, offset=0):
        return list(self.rows)


class WeChatSenderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.win32 = FakeWin32(windows=[(7, "Qt51514QWindowIcon", "Weixin")])
        self.sleeps = []
        self.reader = FakeReader()
        self.composer_calls = []

        def set_composer(handle, text, names, can_commit, send_enter):
            self.composer_calls.append((handle, text, tuple(names)))
            if not can_commit():
                raise WeChatSendError("wechat_conversation_changed")
            try:
                send_enter()
            except Exception:
                raise WeChatDeliveryUnknown("wechat_send_input_unknown") from None

        self.sender = WeChatGUISender(reader_factory=lambda: self.reader,
                                      win32=self.win32,
                                      timeout=2.0, sleep=self.sleeps.append,
                                      uia_set_text=set_composer)
        self.request = {"platform": "wechat", "account_id": "wxid_self0001",
                        "conversation_type": "private",
                        "conversation_id": "wxid_friend001",
                        "text": "synthetic outbound message", "request_id": "wx-send-0001",
                        "attachment": None}
        self.sent_rows = [{"sender_id": "2", "is_self": True, "sender_username": "wxid_self0001",
                           "content": "synthetic outbound message",
                           "sort_seq": 20, "local_id": 5, "shard": "message_1.db"}]

    def tearDown(self):
        self.temp.cleanup()

    def open_chat_window(self, title="Synthetic Friend", handle=9):
        self.win32.windows.append((handle, "Qt51514QWindowIcon", title))
        return handle

    def use_reader(self, rows):
        reader = FakeReader(rows)
        self.sender.reader = lambda: reader
        return reader

    def use_reader_with_exchange(self, before, after):
        """Serve the pre-send snapshot once, then the post-send rows.

        This is what the real client does: the watermark is read before typing and
        the receipt is read after, so a snapshot cannot fake a delivery.
        """
        reader = FakeReader(before)
        reader.before = before
        reader.after = after
        reader.exchanged = False

        def get_messages(username, limit=20, offset=0):
            if reader.exchanged:
                return list(after)
            reader.exchanged = True
            return list(before)

        reader.get_messages = get_messages
        self.sender.reader = lambda: reader
        return reader

    def test_unicode_input_structure_matches_windows_pointer_size(self):
        self.assertEqual(ctypes.sizeof(_INPUT), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)

    def test_chat_composer_requires_exact_identity_enabled_edit_and_known_name(self):
        self.assertTrue(_is_chat_composer("chat_input_field", 50004, True,
                                          "Synthetic Friend", ["Synthetic Friend"]))
        self.assertTrue(_is_chat_composer("chat_view.chat_input_field", 50004, True,
                                          "Synthetic Friend", ["Synthetic Friend"]))
        self.assertFalse(_is_chat_composer("search_input", 50004, True,
                                           "Synthetic Friend", ["Synthetic Friend"]))
        self.assertFalse(_is_chat_composer("chat_input_field", 50004, False,
                                           "Synthetic Friend", ["Synthetic Friend"]))
        self.assertFalse(_is_chat_composer("chat_input_field", 50004, True,
                                           "Other Friend", ["Synthetic Friend"]))

    def test_unicode_input_sends_utf16_units_as_balanced_key_events(self):
        sent_events = []

        def send_input(count, events, size):
            self.assertEqual(size, ctypes.sizeof(_INPUT))
            sent_events.extend((events[index].value.ki.wScan, events[index].value.ki.dwFlags)
                               for index in range(count))
            return count

        win32 = _Win32.__new__(_Win32)
        win32.user32 = type("User32", (), {"SendInput": staticmethod(send_input)})()
        win32.unicode_text("A中😀")
        self.assertEqual(sent_events, [(unit, flags) for unit in (0x41, 0x4E2D, 0xD83D, 0xDE00)
                                       for flags in (0x0004, 0x0006)])

    def test_input_desktop_probe_uses_the_module_that_owns_close_desktop(self):
        """A locked-desktop false positive would block every real send.

        ``CloseDesktop`` lives on user32, not kernel32. Closing it through the
        wrong module raises, and the broad ``except`` in ``input_desktop_locked``
        then reports the desktop as locked even when it is not.
        """
        closed = []

        class FakeUser32:
            @staticmethod
            def OpenInputDesktop(a, b, c):
                return 4321

            @staticmethod
            def GetUserObjectInformationW(desktop, index, buffer, size, needed):
                buffer.value = "Default"
                return 1

            @staticmethod
            def CloseDesktop(desktop):
                closed.append(desktop)
                return 1

        win32 = _Win32.__new__(_Win32)
        win32.user32 = FakeUser32()
        win32.kernel32 = type("Kernel32", (), {})()
        self.assertFalse(win32.input_desktop_locked())
        self.assertEqual(closed, [4321])

    def test_main_window_accepts_exact_localized_title_and_class(self):
        win32 = _Win32.__new__(_Win32)
        win32.enumerate_visible = lambda: [
            (7, WINDOW_CLASS, "微信"),
            (8, "OtherWindowClass", "微信"),
            (9, WINDOW_CLASS, "微信 企业版"),
        ]
        win32.size_of = lambda handle: {7: (1024, 768), 8: (1280, 900), 9: (1400, 950)}[handle]

        self.assertEqual(win32.main_window(), 7)

    def test_rejects_delivery_when_no_window_proves_the_target(self):
        with self.assertRaises(WeChatSendError) as missing:
            self.sender.deliver(self.request)
        self.assertEqual(missing.exception.code, "wechat_conversation_not_open")
        self.assertEqual(self.win32.clipboard, None)
        self.assertEqual(self.win32.keys, [])

    def test_delivers_only_through_a_window_whose_title_proves_the_target(self):
        self.open_chat_window()
        clipboard_before = {"text": "preserve me", "private_format": b"\x01"}
        self.win32.clipboard = clipboard_before
        self.use_reader_with_exchange(
            [{"sender_id": "1", "content": "earlier inbound", "sort_seq": 19,
              "local_id": 4, "shard": "message_1.db"}],
            [{"sender_id": "2", "is_self": True, "sender_username": "wxid_self0001",
              "content": "synthetic outbound message", "sort_seq": 20,
              "local_id": 5, "shard": "message_1.db"}])
        result = self.sender.deliver(self.request)
        self.assertEqual(result["account_id"], "wxid_self0001")
        self.assertEqual(result["message_id"], "wxid_friend001:message_1.db:5")
        self.assertEqual(self.composer_calls,
                         [(9, "synthetic outbound message", ("Synthetic Friend", "wxid_friend001"))])
        self.assertEqual(self.win32.unicode_inputs, [])
        self.assertEqual(self.win32.clipboard, clipboard_before)
        self.assertEqual(self.win32.clicks, [])
        self.assertNotIn((0x56, True), self.win32.keys)
        self.assertEqual(self.win32.keys, [(0x0D, False)])

    def test_ambiguous_windows_fail_closed_without_typing(self):
        self.open_chat_window("Synthetic Friend", 9)
        self.open_chat_window("Synthetic Friend", 10)
        with self.assertRaises(WeChatSendError) as ambiguous:
            self.sender.deliver(self.request)
        self.assertEqual(ambiguous.exception.code, "wechat_conversation_ambiguous")
        self.assertEqual(self.win32.keys, [])

    def test_duplicate_contact_names_fail_closed_without_typing(self):
        self.open_chat_window()
        self.reader.usernames_for_display_name = lambda _name: [
            "wxid_friend001", "wxid_other001"]
        with self.assertRaises(WeChatSendError) as ambiguous:
            self.sender.deliver(self.request)
        self.assertEqual(ambiguous.exception.code, "wechat_conversation_ambiguous")
        self.assertEqual(self.win32.keys, [])

    def test_duplicate_contact_name_equal_to_target_id_fails_closed(self):
        self.reader.nickname = "wxid_friend001"
        self.reader.usernames_for_display_name = lambda _name: [
            "wxid_friend001", "wxid_other001"]
        self.open_chat_window("wxid_friend001")
        with self.assertRaises(WeChatSendError) as ambiguous:
            self.sender.deliver(self.request)
        self.assertEqual(ambiguous.exception.code, "wechat_conversation_ambiguous")
        self.assertEqual(self.win32.keys, [])

    def test_window_with_a_similar_title_is_not_treated_as_the_target(self):
        self.open_chat_window("Synthetic Friend (2)", 10)
        with self.assertRaises(WeChatSendError) as similar:
            self.sender.deliver(self.request)
        self.assertEqual(similar.exception.code, "wechat_conversation_not_open")
        self.assertEqual(self.win32.clipboard, None)

    def test_history_marker_title_requires_an_exact_identity_match(self):
        from .sender import HISTORY_MARKER

        self.assertTrue(WeChatGUISender._title_proves_target(
            "Synthetic Friend" + HISTORY_MARKER, ["Synthetic Friend"]))
        self.assertFalse(WeChatGUISender._title_proves_target(
            "Synthetic Friend" + HISTORY_MARKER + " unrelated", ["Synthetic Friend"]))
        self.assertFalse(WeChatGUISender._title_proves_target(
            HISTORY_MARKER + "Synthetic Friend" + " unrelated", ["Synthetic Friend"]))

    def test_unrelated_window_is_never_used(self):
        self.open_chat_window("Someone Else", 11)
        with self.assertRaises(WeChatSendError) as wrong:
            self.sender.deliver(self.request)
        self.assertEqual(wrong.exception.code, "wechat_conversation_not_open")
        self.assertEqual(self.win32.clipboard, None)

    def test_locked_desktop_blocks_delivery(self):
        self.open_chat_window()
        self.win32.locked = True
        with self.assertRaises(WeChatSendError) as locked:
            self.sender.deliver(self.request)
        self.assertEqual(locked.exception.code, "wechat_desktop_locked")
        self.assertEqual(self.win32.keys, [])

    def test_uia_composer_verification_failure_stops_before_enter(self):
        self.open_chat_window()
        clipboard_before = {"text": "draft", "private_format": b"\x02"}
        self.win32.clipboard = clipboard_before
        self.sender.uia_set_text = lambda *_args: (_ for _ in ()).throw(
            WeChatSendError("wechat_uia_readback_mismatch"))
        self.use_reader(self.sent_rows)
        with self.assertRaises(WeChatSendError) as unverified:
            self.sender.deliver(self.request)
        self.assertEqual(unverified.exception.code, "wechat_uia_readback_mismatch")
        self.assertEqual(self.win32.clipboard, clipboard_before)
        self.assertEqual(self.win32.clicks, [])
        self.assertEqual([key for key in self.win32.keys if key[0] == 0x0D], [])

    def test_changed_window_title_stops_after_composing_without_enter(self):
        self.open_chat_window()
        self.use_reader(self.sent_rows)

        def compose(_handle, _text, _names, can_commit, _send_enter):
            self.win32.windows[-1] = (9, WINDOW_CLASS, "Different Friend")
            if not can_commit():
                raise WeChatSendError("wechat_conversation_changed")

        self.sender.uia_set_text = compose
        with self.assertRaises(WeChatSendError) as changed:
            self.sender.deliver(self.request)
        self.assertEqual(changed.exception.code, "wechat_conversation_changed")
        self.assertEqual(self.win32.keys, [])

    def test_lost_foreground_stops_before_enter(self):
        self.open_chat_window()
        self.use_reader(self.sent_rows)

        def compose(_handle, _text, _names, can_commit, _send_enter):
            self.win32.foreground = 7
            if not can_commit():
                raise WeChatSendError("wechat_conversation_changed")

        self.sender.uia_set_text = compose
        with self.assertRaises(WeChatSendError) as changed:
            self.sender.deliver(self.request)
        self.assertEqual(changed.exception.code, "wechat_conversation_changed")
        self.assertEqual(self.win32.keys, [])

    def test_uncertain_enter_failure_is_not_classified_as_safe_to_retry(self):
        self.open_chat_window()
        self.use_reader(self.sent_rows)
        self.win32.key = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("synthetic input interruption"))
        with self.assertRaises(WeChatDeliveryUnknown) as unknown:
            self.sender.deliver(self.request)
        self.assertEqual(unknown.exception.code, "wechat_send_input_unknown")
        self.assertTrue(unknown.exception.delivery_unknown)

    def test_unconfirmed_delivery_is_unknown_and_is_not_retried(self):
        self.open_chat_window()
        self.use_reader([])
        with self.assertRaises(WeChatDeliveryUnknown) as unknown:
            self.sender.deliver(self.request)
        self.assertTrue(unknown.exception.delivery_unknown)
        self.assertEqual(unknown.exception.code, "wechat_receipt_missing")
        self.assertEqual(len([key for key in self.win32.keys if key[0] == 0x0D]), 1)

    def test_earlier_duplicate_is_not_accepted_as_a_receipt(self):
        self.open_chat_window()
        self.use_reader([{"sender_id": "2", "is_self": True, "sender_username": "wxid_self0001",
                          "content": "synthetic outbound message",
                          "sort_seq": 9, "local_id": 4, "shard": "message_1.db"}])
        with self.assertRaises(WeChatDeliveryUnknown) as stale:
            self.sender.deliver(self.request)
        self.assertEqual(stale.exception.code, "wechat_receipt_stale")

    def test_failed_pre_send_baseline_stops_before_input(self):
        self.open_chat_window()
        reader = FakeReader([{"sender_id": "2", "is_self": True, "sender_username": "wxid_self0001",
                              "content": "synthetic outbound message",
                              "sort_seq": 9, "local_id": 4, "shard": "message_1.db"}])
        original_get_messages = reader.get_messages
        calls = 0

        def fail_first_read(username, limit=20, offset=0):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("synthetic reader failure")
            return original_get_messages(username, limit, offset)

        reader.get_messages = fail_first_read
        self.sender.reader = lambda: reader
        with self.assertRaises(WeChatSendError) as unavailable:
            self.sender.deliver(self.request)
        self.assertEqual(unavailable.exception.code, "wechat_baseline_reader_unavailable")
        self.assertEqual(self.composer_calls, [])
        self.assertEqual([key for key in self.win32.keys if key[0] == 0x0D], [])

    def test_receipt_ignores_rows_from_other_senders(self):
        self.open_chat_window()
        self.use_reader_with_exchange(
            [{"sender_id": "1", "content": "unrelated earlier reply", "sort_seq": 30,
              "local_id": 7, "shard": "message_1.db"},
             {"sender_id": "1", "content": "synthetic outbound message", "sort_seq": 31,
              "local_id": 8, "shard": "message_1.db"}],
            [{"sender_id": "1", "content": "unrelated earlier reply", "sort_seq": 30,
              "local_id": 7, "shard": "message_1.db"},
             {"sender_id": "1", "content": "synthetic outbound message", "sort_seq": 31,
              "local_id": 8, "shard": "message_1.db"},
             {"sender_id": "2", "is_self": True, "sender_username": "wxid_self0001",
              "content": "synthetic outbound message", "sort_seq": 32,
              "local_id": 9, "shard": "message_1.db"}])
        result = self.sender.deliver(self.request)
        self.assertEqual(result["message_id"], "wxid_friend001:message_1.db:9")

    def test_new_row_sharing_sort_seq_with_existing_rows_is_accepted(self):
        """WeChat persists rows in batches, so a real receipt can tie on sort_seq."""
        self.open_chat_window()
        self.use_reader_with_exchange(
            [{"sender_id": "1", "content": "earlier inbound", "sort_seq": 44,
              "local_id": 12, "shard": "message_1.db"}],
            [{"sender_id": "1", "content": "earlier inbound", "sort_seq": 44,
              "local_id": 12, "shard": "message_1.db"},
             {"sender_id": "2", "is_self": True, "sender_username": "wxid_self0001",
              "content": "synthetic outbound message", "sort_seq": 44,
              "local_id": 13, "shard": "message_1.db"}])
        result = self.sender.deliver(self.request)
        self.assertEqual(result["message_id"], "wxid_friend001:message_1.db:13")

    def test_receipt_uses_resolved_self_identity_instead_of_numeric_sender_id(self):
        self.open_chat_window()
        self.use_reader_with_exchange([], [{**self.sent_rows[0], "sender_id": "73"}])
        result = self.sender.deliver(self.request)
        self.assertEqual(result["message_id"], "wxid_friend001:message_1.db:5")

    def test_numeric_two_cannot_prove_a_self_receipt(self):
        self.sender.timeout = 0.01
        for identity in ({}, {"is_self": False, "sender_username": "wxid_friend001"},
                         {"is_self": True, "sender_username": "wxid_other_account"}):
            with self.subTest(identity=identity):
                self.use_reader([{"sender_id": "2", "content": self.request["text"],
                                  "local_id": 5, "shard": "message_1.db", **identity}])
                with self.assertRaises(WeChatDeliveryUnknown) as unknown:
                    self.sender._verify(self.request["conversation_id"], self.request["text"],
                                        set(), self.request["account_id"])
                self.assertEqual(unknown.exception.code, "wechat_receipt_missing")

    def test_changed_account_cannot_supply_the_receipt(self):
        reader = self.use_reader(self.sent_rows)
        reader.wxid = "wxid_other_account"
        with self.assertRaises(WeChatDeliveryUnknown) as unknown:
            self.sender._verify(self.request["conversation_id"], self.request["text"],
                                set(), self.request["account_id"])
        self.assertEqual(unknown.exception.code, "wechat_receipt_account_mismatch")

    def test_account_mismatch_blocks_delivery(self):
        self.open_chat_window()
        self.use_reader([])
        with self.assertRaises(WeChatSendError) as mismatch:
            self.sender.deliver({**self.request, "account_id": "wxid_someone_else"})
        self.assertEqual(mismatch.exception.code, "wechat_account_mismatch")
        self.assertEqual(self.win32.clipboard, None)

    def test_media_attachments_are_rejected_before_the_gui(self):
        self.open_chat_window()
        request = {**self.request, "text": "",
                   "attachment": {"attachment_id": "a" * 48}}
        with self.assertRaises(WeChatSendError) as media:
            self.sender.deliver(request)
        self.assertEqual(media.exception.code, "wechat_send_media_unsupported")
        self.assertEqual(self.win32.keys, [])

    def test_oversized_text_is_rejected_before_the_gui(self):
        self.open_chat_window()
        with self.assertRaises(WeChatSendError) as toolong:
            self.sender.deliver({**self.request, "text": "x" * 4001})
        self.assertEqual(toolong.exception.code, "wechat_send_text_too_long")
        self.assertEqual(self.win32.keys, [])

    def test_invalid_conversation_identifier_is_rejected(self):
        self.open_chat_window()
        for identifier in ("", "not/valid", "x" * 65, 123):
            with self.assertRaises(WeChatSendError):
                self.sender.deliver({**self.request, "conversation_id": identifier})

    def test_readiness_reports_disabled_capability_without_probing(self):
        result = self.sender.readiness(capability_enabled=False)
        self.assertEqual(result["state"], "capability_disabled")
        self.assertIsNone(result["account_id"])

    def test_readiness_reports_window_and_account_state(self):
        result = self.sender.readiness(capability_enabled=True)
        self.assertTrue(result["window_found"])
        self.assertEqual(result["account_id"], "wxid_self0001")
        self.assertEqual(result["state"], "ready")
        self.win32.windows = []
        self.assertEqual(self.sender.readiness()["state"], "window_not_found")

    def test_readiness_fails_closed_while_the_client_is_signed_out(self):
        """A signed-out client keeps its window and cached keys, so cached
        account identity must not be reported as a usable send client."""
        self.win32.login_handle = 7
        result = self.sender.readiness()
        self.assertEqual(result["state"], "client_logged_out")
        self.assertTrue(result["window_found"])
        self.assertIsNone(result["account_id"])

    def test_locked_desktop_reports_not_ready(self):
        self.win32.locked = True
        result = self.sender.readiness()
        self.assertFalse(result["input_available"])
        self.assertEqual(result["state"], "account_unavailable")

    def test_windows_only_platform_is_rejected(self):
        from unittest.mock import patch
        with patch("integrations.wechat.sender.sys.platform", "linux"):
            with self.assertRaises(WeChatSendError) as unsupported:
                self.sender.deliver(self.request)
        self.assertEqual(unsupported.exception.code, "wechat_send_windows_only")


if __name__ == "__main__":
    unittest.main()
