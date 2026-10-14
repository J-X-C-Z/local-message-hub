"""Text delivery as a distinct WeCom bot identity using the official WS protocol.

Protocol reference: WecomTeam/aibot-node-sdk README (MIT), used as documentation only;
no SDK code is copied. The small RFC 6455 transport below uses Python's stdlib only.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import ssl
import struct
import time

from .bot_media import WeComBotMediaStore


_HOST = "openws.work.weixin.qq.com"
_PORT = 443
_PATH = "/"
_MAX_FRAME = 1024 * 1024
_HEARTBEAT_INTERVAL = 25.0


class WeComBotSendError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class WeComBotDeliveryUnknown(WeComBotSendError):
    """The send may have reached WeCom, but no definite correlated receipt arrived."""

    delivery_unknown = True


class _WebSocket:
    """Minimal client WebSocket for a fixed TLS endpoint; redirects are never followed."""

    def __init__(self, timeout):
        self.timeout = timeout
        raw = socket.create_connection((_HOST, _PORT), timeout=timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=_HOST)
        self.sock.settimeout(timeout)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (f"GET {_PATH} HTTP/1.1\r\nHost: {_HOST}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                   "Sec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(request.encode("ascii"))
        response = self._read_headers()
        expected = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
        headers = {}
        for line in response.split(b"\r\n")[1:]:
            if b":" in line:
                name, value = line.split(b":", 1)
                name = name.strip().lower()
                if not name or name in headers:
                    self.close()
                    raise WeComBotSendError("wecom_ws_handshake_rejected")
                headers[name] = value.strip()
        connection_tokens = {token.strip().lower() for token in
                             headers.get(b"connection", b"").split(b",")}
        if (not response.startswith(b"HTTP/1.1 101 ") or
                headers.get(b"upgrade", b"").lower() != b"websocket" or
                b"upgrade" not in connection_tokens or
                headers.get(b"sec-websocket-accept") != expected.encode("ascii") or
                b"sec-websocket-extensions" in headers or
                b"sec-websocket-protocol" in headers):
            self.close()
            raise WeComBotSendError("wecom_ws_handshake_rejected")

    def _read_headers(self):
        data = bytearray()
        while not data.endswith(b"\r\n\r\n"):
            if len(data) >= 16384:
                raise WeComBotSendError("wecom_ws_handshake_invalid")
            chunk = self.sock.recv(1)
            if not chunk:
                raise WeComBotSendError("wecom_ws_handshake_invalid")
            data.extend(chunk)
        return bytes(data)

    def send(self, frame):
        payload = json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(payload) > _MAX_FRAME:
            raise WeComBotSendError("wecom_ws_frame_too_large")
        mask = secrets.token_bytes(4)
        size = len(payload)
        header = bytearray([0x81])
        if size < 126:
            header.append(0x80 | size)
        elif size <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", size))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", size))
        header.extend(mask)
        self.sock.sendall(header + bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))

    def recv(self, timeout):
        deadline = time.monotonic() + timeout
        message = bytearray()
        fragmented = False
        while True:
            first, second = self._read_exact(2, deadline)
            final, opcode, masked = bool(first & 0x80), first & 0x0F, bool(second & 0x80)
            if first & 0x70 or masked:
                raise WeComBotSendError("wecom_ws_protocol_error")
            size = second & 0x7F
            if size == 126:
                size = struct.unpack("!H", self._read_exact(2, deadline))[0]
                if size < 126:
                    raise WeComBotSendError("wecom_ws_protocol_error")
            elif size == 127:
                encoded_size = self._read_exact(8, deadline)
                if encoded_size[0] & 0x80:
                    raise WeComBotSendError("wecom_ws_protocol_error")
                size = struct.unpack("!Q", encoded_size)[0]
                if size < 65536:
                    raise WeComBotSendError("wecom_ws_protocol_error")
            if size > _MAX_FRAME:
                raise WeComBotSendError("wecom_ws_frame_too_large")
            if opcode >= 0x8 and (not final or size > 125):
                raise WeComBotSendError("wecom_ws_protocol_error")
            payload = self._read_exact(size, deadline)
            if opcode == 0x8 and size == 1:
                raise WeComBotSendError("wecom_ws_protocol_error")
            if opcode == 0x9:
                self._send_control(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                raise ConnectionError("WebSocket closed")
            if opcode == 0x1 and not fragmented:
                message.extend(payload)
                fragmented = not final
            elif opcode == 0x0 and fragmented:
                message.extend(payload)
                fragmented = not final
            else:
                raise WeComBotSendError("wecom_ws_protocol_error")
            if len(message) > _MAX_FRAME:
                raise WeComBotSendError("wecom_ws_frame_too_large")
            if fragmented:
                continue
            try:
                value = json.loads(message.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise WeComBotSendError("wecom_ws_invalid_json") from None
            message.clear()
            if not isinstance(value, dict):
                raise WeComBotSendError("wecom_ws_invalid_frame")
            return value

    def _read_exact(self, count, deadline=None):
        data = bytearray()
        while len(data) < count:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout()
                self.sock.settimeout(remaining)
            chunk = self.sock.recv(count - len(data))
            if not chunk:
                raise ConnectionError("WebSocket closed")
            data.extend(chunk)
        return bytes(data)

    def _send_control(self, opcode, payload):
        mask = secrets.token_bytes(4)
        masked = bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
        self.sock.sendall(bytes((0x80 | opcode, 0x80 | len(payload))) + mask + masked)

    def close(self):
        try:
            self.sock.close()
        except (AttributeError, OSError):
            pass

    def settimeout(self, timeout):
        self.sock.settimeout(timeout)


class WeComBotTextSender:
    """Deliver explicitly approved text/media as a separate WeCom bot identity.

    transport_factory is a local test seam returning an object with send(dict),
    recv(timeout)->dict and close(); production uses the stdlib TLS WebSocket above.
    """

    def __init__(self, timeout=8.0, media_timeout=300.0, transport_factory=None):
        self.timeout = min(max(float(timeout), 1.0), 15.0)
        self.media_timeout = min(max(float(media_timeout), self.timeout), 300.0)
        self.transport_factory = transport_factory or (lambda _endpoint, limit: _WebSocket(limit))

    def deliver(self, request):
        bot_id, secret = request.get("_bot_id"), request.get("_bot_secret")
        chat_id, content = request.get("conversation_id"), request.get("text")
        request_id = request.get("request_id")
        if not all(isinstance(value, str) and value.strip() for value in
                   (bot_id, secret, chat_id, request_id)):
            raise WeComBotSendError("wecom_bot_request_invalid")
        attachment = request.get("_media_payload")
        if attachment is None:
            if not isinstance(content, str) or not content.strip():
                raise WeComBotSendError("wecom_bot_request_invalid")
            if len(content.encode("utf-8")) > 20480:
                raise WeComBotSendError("wecom_bot_text_too_large")
        elif not isinstance(attachment, dict) or attachment.get("type") not in (
                "image", "file", "voice", "video") or not isinstance(attachment.get("filename"), str):
            raise WeComBotSendError("wecom_bot_media_invalid")
        else:
            data = attachment.get("data")
            stream = attachment.get("stream")
            size = len(data) if isinstance(data, bytes) else attachment.get("size")
            is_stream = stream is not None and callable(getattr(stream, "read", None))
            if (isinstance(size, bool) or not isinstance(size, int) or
                    not 1 <= size <= 50 * 1024 * 1024 or
                    not (isinstance(data, bytes) or is_stream) or
                    (is_stream and (not isinstance(attachment.get("md5"), str) or
                                    len(attachment["md5"]) != 32))):
                raise WeComBotSendError("wecom_bot_media_invalid") from None
            if not is_stream:
                try:
                    WeComBotMediaStore.validate(data, attachment["filename"], attachment["type"])
                except (TypeError, ValueError):
                    raise WeComBotSendError("wecom_bot_media_invalid") from None
        if request.get("conversation_type") not in ("private", "group"):
            raise WeComBotSendError("wecom_bot_conversation_type_invalid")

        deadline = time.monotonic() + (self.media_timeout if attachment else self.timeout)
        transport = None
        heartbeat = {"last_sent_at": None}
        send_attempted = False
        subscribe_id, send_id = secrets.token_urlsafe(18), secrets.token_urlsafe(18)
        try:
            transport = self.transport_factory(f"wss://{_HOST}:{_PORT}{_PATH}", self._remaining(deadline))
            self._send(transport, {"cmd": "aibot_subscribe", "headers": {"req_id": subscribe_id},
                                   "body": {"bot_id": bot_id, "secret": secret}}, deadline, heartbeat)
            self._receipt(transport, subscribe_id, deadline, sent=False)
            if attachment is None:
                body = {"chatid": chat_id, "msgtype": "markdown", "markdown": {"content": content}}
            else:
                media_id = self._upload_media(transport, attachment, deadline, heartbeat)
                kind = attachment["type"]
                media = {"media_id": media_id}
                body = {"chatid": chat_id, "msgtype": kind, kind: media}
            # A partial socket write can fail after WeCom has received the message.
            def mark_send_attempted():
                nonlocal send_attempted
                send_attempted = True

            self._send(transport, {"cmd": "aibot_send_msg", "headers": {"req_id": send_id},
                                   "body": body}, deadline, heartbeat,
                       on_send_start=mark_send_attempted)
            receipt = self._receipt(transport, send_id, deadline, sent=True)
            body = receipt.get("body")
            message_id = body.get("msgid") if isinstance(body, dict) else None
            if not isinstance(message_id, (str, int)) or isinstance(message_id, bool) or not str(message_id):
                raise WeComBotDeliveryUnknown("wecom_bot_receipt_missing_message_id")
            return {"message_id": str(message_id), "identity": "wecom_bot"}
        except WeComBotSendError:
            raise
        except (TimeoutError, socket.timeout):
            if send_attempted:
                raise WeComBotDeliveryUnknown("wecom_bot_delivery_unknown") from None
            raise WeComBotSendError("wecom_bot_auth_timeout") from None
        except (OSError, ConnectionError):
            if send_attempted:
                raise WeComBotDeliveryUnknown("wecom_bot_delivery_unknown") from None
            raise WeComBotSendError("wecom_bot_transport_error") from None
        finally:
            if transport is not None:
                transport.close()

    def _upload_media(self, transport, attachment, deadline, heartbeat):
        data, stream = attachment.get("data"), attachment.get("stream")
        size = len(data) if isinstance(data, bytes) else attachment["size"]
        kind, filename = attachment["type"], attachment["filename"]
        chunk_size = 512 * 1024
        chunks = (size + chunk_size - 1) // chunk_size
        if not 1 <= chunks <= 100:
            raise WeComBotSendError("wecom_bot_media_too_large")
        md5 = hashlib.md5(data).hexdigest() if isinstance(data, bytes) else attachment["md5"]
        init_id = secrets.token_urlsafe(18)
        self._send(transport, {"cmd": "aibot_upload_media_init", "headers": {"req_id": init_id},
                               "body": {"type": kind, "filename": filename, "total_size": size,
                                        "total_chunks": chunks, "md5": md5}}, deadline, heartbeat)
        init = self._receipt(transport, init_id, deadline, sent=False)
        upload_id = (init.get("body") or {}).get("upload_id")
        if not isinstance(upload_id, str) or not upload_id:
            raise WeComBotSendError("wecom_bot_media_upload_rejected")
        upload_digest = hashlib.md5()
        remaining = size
        for index in range(chunks):
            expected_size = min(chunk_size, remaining)
            if isinstance(data, bytes):
                chunk = data[index * chunk_size:(index + 1) * chunk_size]
            else:
                parts = bytearray()
                while len(parts) < expected_size:
                    part = stream.read(expected_size - len(parts))
                    if not part:
                        break
                    if not isinstance(part, bytes) or len(part) > expected_size - len(parts):
                        raise WeComBotSendError("wecom_bot_media_invalid")
                    parts.extend(part)
                chunk = bytes(parts)
            if len(chunk) != expected_size:
                raise WeComBotSendError("wecom_bot_media_invalid")
            remaining -= expected_size
            upload_digest.update(chunk)
            req_id = secrets.token_urlsafe(18)
            self._send(transport, {"cmd": "aibot_upload_media_chunk", "headers": {"req_id": req_id},
                                   "body": {"upload_id": upload_id, "chunk_index": index,
                                            "base64_data": base64.b64encode(chunk).decode("ascii")}}, deadline, heartbeat)
            self._receipt(transport, req_id, deadline, sent=False)
        if remaining or upload_digest.hexdigest() != md5:
            raise WeComBotSendError("wecom_bot_media_invalid")
        finish_id = secrets.token_urlsafe(18)
        self._send(transport, {"cmd": "aibot_upload_media_finish", "headers": {"req_id": finish_id},
                               "body": {"upload_id": upload_id}}, deadline, heartbeat)
        finish = self._receipt(transport, finish_id, deadline, sent=False)
        media_id = (finish.get("body") or {}).get("media_id")
        if not isinstance(media_id, str) or not media_id:
            raise WeComBotSendError("wecom_bot_media_upload_rejected")
        return media_id

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("WeCom bot request timed out")
        return remaining

    def _send(self, transport, frame, deadline, heartbeat, on_send_start=None):
        now = time.monotonic()
        remaining = self._remaining(deadline)
        set_timeout = getattr(transport, "settimeout", None)
        if set_timeout is not None:
            set_timeout(min(remaining, self.media_timeout))
        if heartbeat["last_sent_at"] is None:
            heartbeat["last_sent_at"] = now
        elif now - heartbeat["last_sent_at"] >= _HEARTBEAT_INTERVAL:
            ping = {"cmd": "ping", "headers": {"req_id": secrets.token_urlsafe(18)}}
            transport.send(ping)
            heartbeat["last_sent_at"] = now
        if on_send_start is not None:
            on_send_start()
        transport.send(frame)

    def _receipt(self, transport, req_id, deadline, sent):
        while True:
            try:
                frame = transport.recv(self._remaining(deadline))
            except (TimeoutError, socket.timeout):
                if sent:
                    raise WeComBotDeliveryUnknown("wecom_bot_delivery_unknown") from None
                raise WeComBotSendError("wecom_bot_auth_timeout") from None
            except (OSError, ConnectionError):
                if sent:
                    raise WeComBotDeliveryUnknown("wecom_bot_delivery_unknown") from None
                raise WeComBotSendError("wecom_bot_auth_transport_error") from None
            except WeComBotSendError as exc:
                if sent:
                    raise WeComBotDeliveryUnknown("wecom_bot_invalid_receipt") from exc
                raise WeComBotSendError("wecom_bot_auth_protocol_error") from exc
            if not isinstance(frame, dict):
                raise WeComBotDeliveryUnknown("wecom_bot_invalid_receipt") if sent else WeComBotSendError("wecom_bot_auth_protocol_error")
            headers = frame.get("headers")
            if not isinstance(headers, dict) or not isinstance(headers.get("req_id"), str):
                raise WeComBotDeliveryUnknown("wecom_bot_invalid_receipt") if sent else WeComBotSendError("wecom_bot_auth_protocol_error")
            if headers["req_id"] != req_id:
                continue
            errcode = frame.get("errcode")
            if isinstance(errcode, bool) or not isinstance(errcode, int):
                raise WeComBotDeliveryUnknown("wecom_bot_invalid_receipt") if sent else WeComBotSendError("wecom_bot_auth_protocol_error")
            if errcode != 0:
                raise WeComBotSendError("wecom_bot_rejected")
            return frame
