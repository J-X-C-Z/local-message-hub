"""GUI text delivery for the locally logged-in personal WeChat client (Windows).

This module drives the real Weixin desktop window so that one approved, auditable
text message can be delivered from the logged-in personal account. It is a
separate opt-in path: the Hub's read-only database provider never sends.

Rules this implementation keeps:

* Read-only everywhere except the GUI itself. Local databases are only read to
  prove that a message really landed; nothing is ever written to them.
* Never guess. If the target conversation cannot be *proven* to be the one on
  screen, delivery fails closed instead of typing into a window that merely has
  focus.
* Never re-send on ambiguity. An unconfirmed delivery raises
  :class:`WeChatDeliveryUnknown` so the Hub records ``unknown`` and never retries.
"""
from __future__ import annotations

import ctypes
import re
import sys
import time
from ctypes import wintypes


WINDOW_CLASS = "Qt51514QWindowIcon"
MAIN_WINDOW_TITLES = ("Weixin",)
HISTORY_MARKER = "chat history"
MAX_TEXT = 4000
_WXID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_RECEIPT_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
_RHYTHM = None

# Composer box as a share of the client rect. Weixin keeps the composer and the
# tool bar above the bottom edge; only the message area changes height.
_INPUT_RATIO = (0.30, 0.78, 0.80, 0.94)


class WeChatSendError(RuntimeError):
    """Delivery was not attempted; nothing was typed into WeChat."""

    def __init__(self, code):
        self.code = str(code)[:64]
        super().__init__(self.code)


class WeChatDeliveryUnknown(WeChatSendError):
    """Text may have reached WeChat, but no trustworthy receipt was obtained."""

    delivery_unknown = True


def _rhythm():
    """Optional vendor pacing module; absent from the thin runtime, so optional.

    The bundled upstream copy lives under a gitignored runtime directory, so it is
    imported lazily and only when present. Importing the ``wechatauto`` package
    itself is avoided on purpose: its ``__init__`` pulls in win32 GUI modules this
    sender never needs.
    """
    global _RHYTHM
    if _RHYTHM is not None:
        return _RHYTHM or None
    module = None
    try:
        from pathlib import Path
        vendor = Path(__file__).resolve().parent / "runtime" / "upstream"
        if (vendor / "wechatauto" / "rhythm.py").is_file():
            import importlib
            sys.path.insert(0, str(vendor))
            module = importlib.import_module("wechatauto.rhythm")
    except Exception:
        module = None
    _RHYTHM = module
    return module


def _pace(kind, floor_seconds):
    """Human-scale pause before an outbound action, never shorter than the floor."""
    module = _rhythm()
    delay = floor_seconds
    if module is not None:
        try:
            delay = float(module.gate(kind))
        except Exception:
            delay = floor_seconds
    time.sleep(max(delay, floor_seconds))


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("value", _INPUTUNION)]


class _Grab:
    """Top-down BGRA pixel buffer rasterised from a window."""

    def __init__(self, data, width, height):
        self.data = data
        self.width = width
        self.height = height

    def _band(self, ratios):
        x0 = max(0, min(int(self.width * ratios[0]), self.width - 1))
        y0 = max(0, min(int(self.height * ratios[1]), self.height - 1))
        x1 = max(x0 + 1, min(int(self.width * ratios[2]), self.width))
        y1 = max(y0 + 1, min(int(self.height * ratios[3]), self.height))
        return x0, y0, x1, y1

    def dark_ratio(self, ratios):
        """Share of dark pixels in a band, used to prove text reached the composer."""
        x0, y0, x1, y1 = self._band(ratios)
        data, stride = self.data, self.width * 4
        dark = total = 0
        for y in range(y0, y1, 2):
            row = y * stride
            for x in range(x0, x1, 2):
                total += 1
                offset = row + x * 4
                if data[offset] + data[offset + 1] + data[offset + 2] < 600:
                    dark += 1
        return (dark / total) if total else 0.0


class _Win32:
    """The handful of user32/gdi32 calls the GUI layer needs."""

    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32
        self.kernel32 = ctypes.windll.kernel32

    def enumerate_visible(self):
        """All visible top-level windows as (handle, class, title)."""
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(handle, _lparam):
            if not self.user32.IsWindowVisible(handle) or self.user32.IsIconic(handle):
                return True
            cls = ctypes.create_unicode_buffer(256)
            self.user32.GetClassNameW(handle, cls, 256)
            title = ctypes.create_unicode_buffer(512)
            self.user32.GetWindowTextW(handle, title, 512)
            found.append((handle, cls.value, title.value.strip()))
            return True

        self.user32.EnumWindows(ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(callback), 0)
        return found

    def main_window(self):
        """Widest visible Weixin window, which is the one that owns the sidebar."""
        candidates = [item for item in self.enumerate_visible()
                      if item[1] == WINDOW_CLASS and item[2] in MAIN_WINDOW_TITLES]
        if not candidates:
            return None
        return max(candidates, key=lambda item: self.size_of(item[0])[0])[0]

    def rect_of(self, handle):
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(handle, ctypes.byref(rect)):
            raise WeChatSendError("wechat_window_rect_unavailable")
        return (rect.left, rect.top, rect.right, rect.bottom)

    def size_of(self, handle):
        left, top, right, bottom = self.rect_of(handle)
        return (right - left, bottom - top)

    def is_foreground(self, handle):
        return self.user32.GetForegroundWindow() == handle

    def set_foreground(self, handle):
        self.user32.SetForegroundWindow(handle)

    def input_desktop_locked(self):
        """True when the interactive desktop cannot accept input (locked or UAC)."""
        try:
            desktop = self.user32.OpenInputDesktop(0, True, 0x0100)
            if not desktop:
                return True
            try:
                name = ctypes.create_unicode_buffer(256)
                if not self.user32.GetUserObjectInformationW(
                        desktop, 2, name, ctypes.sizeof(name), None):
                    return True
                return name.value.strip().lower() != "default"
            finally:
                self.user32.CloseDesktop(desktop)
        except Exception:
            return True

    def key(self, virtual, ctrl=False):
        if ctrl:
            self.user32.keybd_event(0x11, 0, 0, 0)
        self.user32.keybd_event(virtual, 0, 0, 0)
        self.user32.keybd_event(virtual, 0, 2, 0)
        if ctrl:
            self.user32.keybd_event(0x11, 0, 2, 0)

    def click_at(self, x, y):
        self.user32.SetCursorPos(int(x), int(y))
        time.sleep(0.05)
        self.key(0x01)
        time.sleep(0.05)
        self.key(0x02)

    def unicode_text(self, text):
        """Type UTF-16 code units directly so sending never replaces the clipboard."""
        try:
            encoded = text.encode("utf-16-le")
        except UnicodeEncodeError:
            raise WeChatSendError("wechat_text_invalid") from None
        units = [int.from_bytes(encoded[index:index + 2], "little")
                 for index in range(0, len(encoded), 2)]
        events = (_INPUT * (len(units) * 2))()
        for index, unit in enumerate(units):
            for keyup in (False, True):
                event = events[index * 2 + keyup]
                event.type = 1
                event.value.ki = _KEYBDINPUT(0, unit, 0x0004 | (0x0002 if keyup else 0), 0, 0)
        user32 = self.user32
        user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
        user32.SendInput.restype = wintypes.UINT
        sent = user32.SendInput(len(events), events, ctypes.sizeof(_INPUT))
        if sent != len(events):
            if sent % 2:
                unit = units[sent // 2]
                release = (_INPUT * 1)()
                release[0].type = 1
                release[0].value.ki = _KEYBDINPUT(0, unit, 0x0004 | 0x0002, 0, 0)
                user32.SendInput(1, release, ctypes.sizeof(_INPUT))
            raise WeChatSendError("wechat_keyboard_input_failed")

    def grab(self, handle):
        """Rasterise a window with PrintWindow so it works while unfocused."""
        left, top, right, bottom = self.rect_of(handle)
        width, height = max(1, right - left), max(1, bottom - top)
        info = _BITMAPINFOHEADER()
        info.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.biWidth = width
        info.biHeight = -height
        info.biPlanes = 1
        info.biBitCount = 32
        info.biCompression = 0
        bits = ctypes.c_void_p()
        bitmap = None
        screen_dc = self.user32.GetDC(0)
        memory_dc = self.gdi32.CreateCompatibleDC(screen_dc)
        try:
            bitmap = self.gdi32.CreateDIBSection(
                memory_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
            if not bitmap or not bits.value:
                raise WeChatSendError("wechat_screenshot_unavailable")
            self.gdi32.SelectObject(memory_dc, bitmap)
            if not self.user32.PrintWindow(handle, memory_dc, 2):
                raise WeChatSendError("wechat_screenshot_failed")
            data = ctypes.string_at(bits, width * height * 4)
        finally:
            if bitmap:
                self.gdi32.DeleteObject(bitmap)
            self.gdi32.DeleteDC(memory_dc)
            self.user32.ReleaseDC(0, screen_dc)
        return _Grab(data, width, height)


class WeChatGUISender:
    """Deliver exactly one approved text request through a real Weixin window."""

    def __init__(self, provider_factory=None, reader_factory=None, timeout=45.0,
                 win32=None, sleep=time.sleep):
        self._provider_factory = provider_factory
        self._reader_factory = reader_factory
        self.timeout = min(max(float(timeout), 5.0), 120.0)
        self.win32 = win32 or _Win32()
        self.sleep = sleep
        self._provider = None

    def reader(self):
        if self._reader_factory is not None:
            return self._reader_factory()
        if self._provider_factory is not None:
            return self._provider_factory().reader
        if self._provider is None:
            from integrations.wechat.provider import WeChatProvider
            self._provider = WeChatProvider()
        return self._provider.reader

    def _pace(self, kind, floor_seconds):
        """Pause like a human before an outbound action, never below the floor."""
        module = _rhythm()
        delay = floor_seconds
        if module is not None:
            try:
                delay = float(module.gate(kind))
            except Exception:
                delay = floor_seconds
        self.sleep(max(delay, floor_seconds))

    def display_names(self, conversation_id):
        """Window titles that may legitimately denote this conversation."""
        names = []
        try:
            reader = self.reader()
        except Exception:
            raise WeChatSendError("wechat_account_unavailable") from None
        try:
            name = reader.get_nickname(conversation_id)
        except Exception:
            name = None
        for candidate in (name, conversation_id):
            if isinstance(candidate, str) and candidate.strip() and candidate not in names:
                names.append(candidate.strip())
        return names

    def deliver(self, request):
        if sys.platform != "win32":
            raise WeChatSendError("wechat_send_windows_only")
        if request.get("attachment"):
            raise WeChatSendError("wechat_send_media_unsupported")
        text = request.get("text")
        if not isinstance(text, str) or not text.strip():
            raise WeChatSendError("wechat_send_text_empty")
        if len(text) > MAX_TEXT:
            raise WeChatSendError("wechat_send_text_too_long")
        conversation_id = request.get("conversation_id")
        if not isinstance(conversation_id, str) or not _WXID_RE.fullmatch(conversation_id):
            raise WeChatSendError("wechat_send_conversation_invalid")

        handle, proof = self.resolve_conversation(conversation_id)
        account_id = self._require_account(request.get("account_id"))
        self._pace("send", 1.0)
        watermark = self._watermark(conversation_id)
        self._compose_and_send(handle, text)
        receipt = self._verify(conversation_id, text, watermark)
        return {"message_id": receipt, "account_id": account_id}

    def resolve_conversation(self, conversation_id):
        """Find a dedicated chat window whose title proves the target conversation.

        Only a dedicated chat window can have its identity proven without OCR or
        UI Automation, so a send is only ever attempted there. A title that merely
        contains the name is not evidence: WeChat appends group members, counts and
        history suffixes to window titles. So the title must equal the resolved
        display name, or be exactly ``<display name><history marker>``.
        """
        titles = self.display_names(conversation_id)
        if not titles:
            raise WeChatSendError("wechat_conversation_unknown")
        matches = []
        for handle, cls, title in self.win32.enumerate_visible():
            if cls != WINDOW_CLASS or not title or title in MAIN_WINDOW_TITLES:
                continue
            if self._title_proves_target(title, titles):
                matches.append((handle, title))
        if not matches:
            raise WeChatSendError("wechat_conversation_not_open")
        if len(matches) > 1:
            raise WeChatSendError("wechat_conversation_ambiguous")
        handle, proof = matches[0]
        width, height = self.win32.size_of(handle)
        if width < 480 or height < 400:
            raise WeChatSendError("wechat_window_too_small")
        if self.win32.input_desktop_locked():
            raise WeChatSendError("wechat_desktop_locked")
        return handle, proof

    @staticmethod
    def _title_proves_target(title, titles):
        """Exact-title match only: WeChat appends members, counts and suffixes."""
        if title in titles:
            return True
        return any(title in (name + HISTORY_MARKER, HISTORY_MARKER + name)
                   for name in titles)

    def _require_account(self, requested):
        try:
            current = self.reader()
        except Exception:
            raise WeChatSendError("wechat_account_unavailable") from None
        account_id = str(getattr(current, "wxid", "") or
                         getattr(current, "account", "") or "")
        if not account_id:
            raise WeChatSendError("wechat_account_unavailable")
        if isinstance(requested, str) and requested and requested != account_id:
            raise WeChatSendError("wechat_account_mismatch")
        return account_id

    def _focus(self, handle):
        self.win32.set_foreground(handle)
        for _ in range(10):
            if self.win32.is_foreground(handle):
                return
            self.sleep(0.2)
        raise WeChatSendError("wechat_window_not_foreground")

    def _compose_and_send(self, handle, text):
        self._focus(handle)
        left, top, right, bottom = self.win32.rect_of(handle)
        width, height = max(1, right - left), max(1, bottom - top)
        inset_x, inset_y = max(1, int(width * 0.006)), max(1, int(height * 0.006))
        span_x, span_y = width - 2 * inset_x, height - 2 * inset_y
        ratios = _INPUT_RATIO
        centre_x = left + inset_x + int(span_x * (ratios[0] + ratios[2]) / 2)
        centre_y = top + inset_y + int(span_y * (ratios[1] + ratios[3]) / 2)
        self.win32.click_at(centre_x, centre_y)
        self.sleep(0.3)
        # Clear any draft first so exactly the approved text ends up in the
        # composer. Text is entered with Unicode key events, so the user's
        # clipboard is never read from or written to.
        self.win32.key(0x41, ctrl=True)
        self.sleep(0.15)
        self.win32.key(0x2E)
        self.sleep(0.15)
        self.win32.unicode_text(text)
        self.sleep(0.9)
        if self.win32.grab(handle).dark_ratio(ratios) < 0.02:
            raise WeChatSendError("wechat_input_not_confirmed")
        self._pace("send", 1.0)
        self.win32.key(0x0D)
        self.sleep(1.5)

    def _watermark(self, conversation_id):
        """Snapshot of every stored row identity before sending.

        WeChat persists rows in batches, so several rows of one conversation often
        share the same ``sort_seq`` and a new row is not always ranked above every
        existing one. Identity is therefore what matters: a row that already
        existed before the send cannot be its receipt.
        """
        try:
            reader = self.reader()
            rows = reader.get_messages(conversation_id, limit=5)
        except Exception:
            raise WeChatSendError("wechat_baseline_reader_unavailable") from None
        identities = set()
        for row in rows or []:
            try:
                local_id = int(row.get("local_id") or 0)
            except (TypeError, ValueError):
                continue
            identities.add((str(row.get("shard") or ""), local_id))
        return identities

    def _verify(self, conversation_id, text, watermark):
        """Read the local database back; only a brand-new self-sent exact match counts."""
        try:
            reader = self.reader()
        except Exception:
            raise WeChatDeliveryUnknown("wechat_receipt_reader_unavailable") from None
        deadline = time.monotonic() + self.timeout
        stale = False
        while time.monotonic() < deadline:
            try:
                rows = reader.get_messages(conversation_id, limit=5)
            except Exception:
                rows = []
            for row in rows or []:
                if str(row.get("sender_id")) != "2":
                    continue
                if str(row.get("content") or "") != text:
                    continue
                try:
                    local_id = int(row.get("local_id") or 0)
                except (TypeError, ValueError):
                    continue
                identity = (str(row.get("shard") or ""), local_id)
                if identity in watermark:
                    stale = True
                    continue
                receipt = f"{conversation_id}:{identity[0]}:{local_id}"
                if not _RECEIPT_RE.fullmatch(receipt):
                    raise WeChatDeliveryUnknown("wechat_receipt_invalid")
                return receipt
            self.sleep(1.0)
        raise WeChatDeliveryUnknown("wechat_receipt_stale" if stale else "wechat_receipt_missing")

    def readiness(self, capability_enabled=True):
        """Read-only probe: window presence, input desktop and account identity."""
        result = {"capability_enabled": bool(capability_enabled), "window_found": False,
                  "window_foreground": False, "input_available": False,
                  "account_id": None, "state": "unavailable"}
        if not capability_enabled:
            result["state"] = "capability_disabled"
            return result
        handle = self.win32.main_window()
        if not handle:
            result["state"] = "window_not_found"
            return result
        result["window_found"] = True
        result["window_foreground"] = bool(self.win32.is_foreground(handle))
        result["input_available"] = not self.win32.input_desktop_locked()
        try:
            result["account_id"] = str(getattr(self.reader(), "wxid", "") or
                                       getattr(self.reader(), "account", "") or "") or None
        except Exception:
            result["account_id"] = None
        result["state"] = ("ready" if result["account_id"] and result["input_available"]
                           else "account_unavailable")
        return result
