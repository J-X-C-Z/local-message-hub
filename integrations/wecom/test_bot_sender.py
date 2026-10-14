import base64
import hashlib
import socket
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hub.sending import SendRepository, SendService
from hub.server import _ConfiguredWeComBotSender
from .bot_sender import (_WebSocket, WeComBotDeliveryUnknown, WeComBotSendError,
                         WeComBotTextSender)


def receipt(req_id, **values):
    return {"headers": {"req_id": req_id}, "errcode": 0, **values}


class FakeTransport:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []
        self.closed = False

    def send(self, frame):
        self.sent.append(frame)

    def recv(self, timeout):
        if not self.frames:
            raise TimeoutError()
        value = self.frames.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.closed = True


class HandshakeSocket:
    def __init__(self, connection=b"Upgrade", extra_headers=()):
        self.request = b""
        self.response = b""
        self.connection = connection
        self.extra_headers = extra_headers

    def sendall(self, payload):
        self.request += payload
        if b"\r\n\r\n" in self.request:
            key = next(line.split(b":", 1)[1].strip() for line in self.request.split(b"\r\n")
                       if line.lower().startswith(b"sec-websocket-key:"))
            accept = base64.b64encode(hashlib.sha1(
                key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
            headers = [b"HTTP/1.1 101 Switching Protocols", b"Upgrade: WebSocket"]
            if self.connection is not None:
                headers.append(b"Connection: " + self.connection)
            headers.append(b"Sec-WebSocket-Accept: " + accept)
            self.response = b"\r\n".join(headers + list(self.extra_headers)) + b"\r\n\r\n"

    def recv(self, size):
        value, self.response = self.response[:size], self.response[size:]
        return value

    def settimeout(self, _):
        pass

    def close(self):
        pass


class FrameSocket:
    def __init__(self, frame):
        self.frame = frame
        self.sent = []

    def recv(self, size):
        value, self.frame = self.frame[:size], self.frame[size:]
        return value

    def sendall(self, value):
        self.sent.append(value)

    def settimeout(self, _):
        pass


class EndlessPingSocket:
    def __init__(self):
        self.sent = []

    def recv(self, size):
        return b"\x89\x00"[:size]

    def sendall(self, value):
        self.sent.append(value)

    def settimeout(self, timeout):
        self.timeout = timeout


class FakeTLSContext:
    def __init__(self, wire):
        self.wire = wire

    def wrap_socket(self, _raw, server_hostname):
        if server_hostname != "openws.work.weixin.qq.com":
            raise AssertionError("unexpected TLS host")
        return self.wire


class WeComBotSenderTest(unittest.TestCase):
    def setUp(self):
        self.request = {"_bot_id": "bot-id", "_bot_secret": "bot-secret",
                        "conversation_type": "private", "conversation_id": "user-1",
                        "text": "hello", "request_id": "approved-send-1"}

    def sender_with(self, frames):
        transport = FakeTransport(frames)
        endpoints = []
        sender = WeComBotTextSender(transport_factory=lambda endpoint, timeout:
                                    endpoints.append((endpoint, timeout)) or transport)
        return sender, transport, endpoints

    def test_websocket_handshake_preserves_case_sensitive_accept_value(self):
        wire = HandshakeSocket(connection=b"keep-alive, uPgRaDe")
        with patch("integrations.wecom.bot_sender.socket.create_connection",
                   return_value=wire), patch(
                "integrations.wecom.bot_sender.ssl.create_default_context",
                return_value=FakeTLSContext(wire)):
            connection = _WebSocket(1)
            connection.close()

    def test_websocket_handshake_rejects_incomplete_or_unrequested_negotiation(self):
        cases = (
            HandshakeSocket(connection=None),
            HandshakeSocket(extra_headers=(b"Sec-WebSocket-Extensions: permessage-deflate",)),
            HandshakeSocket(extra_headers=(b"Sec-WebSocket-Protocol: unsolicited",)),
            HandshakeSocket(extra_headers=(b"Sec-WebSocket-Accept: conflicting-value",)),
            HandshakeSocket(extra_headers=(b"Upgrade: websocket",)),
        )
        for wire in cases:
            with self.subTest(response=wire.connection, headers=wire.extra_headers):
                with patch("integrations.wecom.bot_sender.socket.create_connection",
                           return_value=wire), patch(
                        "integrations.wecom.bot_sender.ssl.create_default_context",
                        return_value=FakeTLSContext(wire)):
                    with self.assertRaises(WeComBotSendError) as error:
                        _WebSocket(1)
                self.assertEqual(error.exception.code, "wecom_ws_handshake_rejected")

    def test_websocket_rejects_unnegotiated_reserved_bits(self):
        for reserved_bit in (0x40, 0x20, 0x10):
            with self.subTest(reserved_bit=reserved_bit):
                connection = _WebSocket.__new__(_WebSocket)
                connection.sock = FrameSocket(bytes((0x81 | reserved_bit, 2)) + b"{}")
                with self.assertRaises(WeComBotSendError) as error:
                    connection.recv(1)
                self.assertEqual(error.exception.code, "wecom_ws_protocol_error")

    def test_websocket_ignores_pong_before_text_receipt(self):
        connection = _WebSocket.__new__(_WebSocket)
        connection.sock = FrameSocket(b"\x8a\x00\x81\x02{}")
        self.assertEqual(connection.recv(1), {})

    def test_websocket_control_frames_cannot_extend_the_receive_deadline(self):
        connection = _WebSocket.__new__(_WebSocket)
        connection.sock = EndlessPingSocket()
        with patch("integrations.wecom.bot_sender.time.monotonic", side_effect=(0, 0, 0, 2)):
            with self.assertRaises(socket.timeout):
                connection.recv(1)
        self.assertEqual(len(connection.sock.sent), 2)

    def test_websocket_reassembles_text_fragments_around_ping(self):
        connection = _WebSocket.__new__(_WebSocket)
        connection.sock = FrameSocket(b"\x01\x01{\x89\x01p\x80\x01}")
        self.assertEqual(connection.recv(1), {})
        self.assertEqual(len(connection.sock.sent), 1)
        self.assertEqual(connection.sock.sent[0][0] & 0x0F, 0xA)

    def test_websocket_rejects_control_payload_over_125_bytes(self):
        connection = _WebSocket.__new__(_WebSocket)
        connection.sock = FrameSocket(b"\x89\x7e\x00\x7e")
        with self.assertRaises(WeComBotSendError) as error:
            connection.recv(1)
        self.assertEqual(error.exception.code, "wecom_ws_protocol_error")

    def test_websocket_rejects_close_frame_with_one_byte_payload(self):
        connection = _WebSocket.__new__(_WebSocket)
        connection.sock = FrameSocket(b"\x88\x01\x03")
        with self.assertRaises(WeComBotSendError) as error:
            connection.recv(1)
        self.assertEqual(error.exception.code, "wecom_ws_protocol_error")

    def test_websocket_rejects_nonminimal_or_invalid_extended_lengths(self):
        frames = (
            b"\x81\x7e\x00\x7d",
            b"\x81\x7f" + struct.pack("!Q", 65535),
            b"\x81\x7f\x80\x00\x00\x00\x00\x00\x00\x00",
        )
        for frame in frames:
            with self.subTest(frame=frame):
                connection = _WebSocket.__new__(_WebSocket)
                connection.sock = FrameSocket(frame)
                with self.assertRaises(WeComBotSendError) as error:
                    connection.recv(1)
                self.assertEqual(error.exception.code, "wecom_ws_protocol_error")

    def test_websocket_accepts_canonical_extended_lengths(self):
        for marker, size in ((126, 126), (127, 65536)):
            with self.subTest(marker=marker):
                payload = b"{}" + b" " * (size - 2)
                length = (bytes((marker,)) + struct.pack("!H", size) if marker == 126
                          else bytes((marker,)) + struct.pack("!Q", size))
                connection = _WebSocket.__new__(_WebSocket)
                connection.sock = FrameSocket(b"\x81" + length + payload)
                self.assertEqual(connection.recv(1), {})

    def test_authenticates_then_sends_and_returns_correlated_message_id(self):
        sender, transport, endpoints = self.sender_with([
            receipt("subscribe-id"),
            receipt("send-id", body={"msgid": "server-message-1"}),
        ])
        # req_ids are generated at call time; build replies from the emitted frames.
        def deliver():
            transport.frames = []
            original_send = transport.send

            def capture(frame):
                original_send(frame)
                if frame["cmd"] == "aibot_subscribe":
                    transport.frames.append(receipt(frame["headers"]["req_id"]))
                else:
                    transport.frames.append(receipt(frame["headers"]["req_id"],
                                                    body={"msgid": "server-message-1"}))
            transport.send = capture
            return sender.deliver(self.request)

        result = deliver()
        self.assertEqual(result, {"message_id": "server-message-1", "identity": "wecom_bot"})
        self.assertEqual([frame["cmd"] for frame in transport.sent],
                         ["aibot_subscribe", "aibot_send_msg"])
        self.assertEqual(transport.sent[0]["body"], {"bot_id": "bot-id", "secret": "bot-secret"})
        self.assertEqual(transport.sent[1]["body"],
                         {"chatid": "user-1", "msgtype": "markdown", "markdown": {"content": "hello"}})
        self.assertEqual(endpoints[0][0], "wss://openws.work.weixin.qq.com:443/")
        self.assertGreater(endpoints[0][1], 7.9)
        self.assertLessEqual(endpoints[0][1], 8.0)
        self.assertTrue(transport.closed)

    def test_ignores_unrelated_receipts_and_requires_message_id(self):
        transport = FakeTransport([])
        sender = WeComBotTextSender(transport_factory=lambda *_: transport)

        def capture(frame):
            transport.sent.append(frame)
            rid = frame["headers"]["req_id"]
            transport.frames.append(receipt("unrelated"))
            transport.frames.append(receipt(rid, body={"msgid": "m-1"} if frame["cmd"] == "aibot_send_msg" else {}))

        transport.send = capture
        self.assertEqual(sender.deliver(self.request)["message_id"], "m-1")

        transport = FakeTransport([])
        sender = WeComBotTextSender(transport_factory=lambda *_: transport)

        def missing_id(frame):
            transport.sent.append(frame)
            transport.frames.append(receipt(frame["headers"]["req_id"], body={}))

        transport.send = missing_id
        with self.assertRaises(WeComBotDeliveryUnknown) as error:
            sender.deliver(self.request)
        self.assertEqual(error.exception.code, "wecom_bot_receipt_missing_message_id")

    def test_each_protocol_write_refreshes_transport_timeout(self):
        transport = FakeTransport([])
        timeout_updates = []
        transport.settimeout = timeout_updates.append

        def respond(frame):
            transport.sent.append(frame)
            command = frame["cmd"]
            body = {"aibot_upload_media_init": {"upload_id": "upload-synthetic"},
                    "aibot_upload_media_finish": {"media_id": "media-synthetic"},
                    "aibot_send_msg": {"msgid": "message-synthetic"}}.get(command, {})
            transport.frames.append(receipt(frame["headers"]["req_id"], body=body))

        transport.send = respond
        sender = WeComBotTextSender(transport_factory=lambda *_: transport)
        result = sender.deliver({**self.request, "text": "", "_media_payload": {
            "type": "file", "filename": "note.txt", "data": b"synthetic"}})

        self.assertEqual(result["message_id"], "message-synthetic")
        self.assertEqual(len(timeout_updates), len(transport.sent))
        self.assertTrue(all(0 < timeout <= 300 for timeout in timeout_updates))

    def test_media_upload_fills_each_chunk_after_short_stream_reads(self):
        data = b"streamed media payload"
        transport = FakeTransport([])

        class ShortReader:
            def __init__(self, value):
                self.value = value

            def read(self, size):
                value, self.value = self.value[:min(size, 3)], self.value[min(size, 3):]
                return value

        def respond(frame):
            transport.sent.append(frame)
            command = frame["cmd"]
            body = {"aibot_upload_media_init": {"upload_id": "upload"},
                    "aibot_upload_media_finish": {"media_id": "media"},
                    "aibot_send_msg": {"msgid": "message"}}.get(command, {})
            transport.frames.append(receipt(frame["headers"]["req_id"], body=body))

        transport.send = respond
        sender = WeComBotTextSender(transport_factory=lambda *_: transport)
        result = sender.deliver({**self.request, "text": "", "_media_payload": {
            "type": "file", "filename": "note.txt", "stream": ShortReader(data),
            "size": len(data), "md5": hashlib.md5(data).hexdigest()}})

        chunks = [frame for frame in transport.sent
                  if frame["cmd"] == "aibot_upload_media_chunk"]
        self.assertEqual(result["message_id"], "message")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(base64.b64decode(chunks[0]["body"]["base64_data"]), data)

    def test_websocket_applies_remaining_timeout_to_underlying_socket(self):
        class RecordingSocket:
            def __init__(self):
                self.timeouts = []

            def settimeout(self, timeout):
                self.timeouts.append(timeout)

        transport = object.__new__(_WebSocket)
        transport.sock = RecordingSocket()

        transport.settimeout(0.375)

        self.assertEqual(transport.sock.timeouts, [0.375])

    def test_sender_sends_application_heartbeat_before_next_frame_after_idle_interval(self):
        transport = FakeTransport([])
        transport.timeouts = []
        transport.settimeout = transport.timeouts.append
        sender = WeComBotTextSender(transport_factory=lambda *_: transport)
        heartbeat = {"last_sent_at": 0}

        with patch("integrations.wecom.bot_sender.time.monotonic", return_value=26):
            sender._send(transport, {"cmd": "aibot_upload_media_chunk"}, 100, heartbeat)

        self.assertEqual([frame["cmd"] for frame in transport.sent], ["ping", "aibot_upload_media_chunk"])
        self.assertTrue(transport.sent[0]["headers"]["req_id"])
        self.assertEqual(transport.timeouts, [74])
        self.assertEqual(heartbeat["last_sent_at"], 26)

    def test_reused_sender_starts_each_new_connection_with_subscription_not_stale_ping(self):
        transports = []

        def create_transport(*_):
            transport = FakeTransport([])

            def respond(frame):
                transport.sent.append(frame)
                body = {"msgid": "synthetic-message"} if frame["cmd"] == "aibot_send_msg" else {}
                transport.frames.append(receipt(frame["headers"]["req_id"], body=body))

            transport.send = respond
            transports.append(transport)
            return transport

        sender = WeComBotTextSender(transport_factory=create_transport)
        clock = [0]
        with patch("integrations.wecom.bot_sender.time.monotonic", side_effect=lambda: clock[0]):
            sender.deliver(self.request)
            clock[0] = 30
            sender.deliver(self.request)

        self.assertEqual(len(transports), 2)
        self.assertEqual([frame["cmd"] for frame in transports[0].sent],
                         ["aibot_subscribe", "aibot_send_msg"])
        self.assertEqual([frame["cmd"] for frame in transports[1].sent],
                         ["aibot_subscribe", "aibot_send_msg"])

    def test_send_timeout_is_unknown_and_never_retried(self):
        transport = FakeTransport([])
        sender = WeComBotTextSender(transport_factory=lambda *_: transport)

        def send_then_timeout(frame):
            transport.sent.append(frame)
            if frame["cmd"] == "aibot_subscribe":
                transport.frames.append(receipt(frame["headers"]["req_id"]))

        transport.send = send_then_timeout
        with self.assertRaises(WeComBotDeliveryUnknown) as error:
            sender.deliver(self.request)
        self.assertEqual(error.exception.code, "wecom_bot_delivery_unknown")
        self.assertEqual([item["cmd"] for item in transport.sent],
                         ["aibot_subscribe", "aibot_send_msg"])

    def test_deadline_before_send_write_is_retryable_auth_failure(self):
        transport = FakeTransport([])

        def capture_subscribe(frame):
            transport.sent.append(frame)
            transport.frames.append(receipt(frame["headers"]["req_id"]))

        transport.send = capture_subscribe
        sender = WeComBotTextSender(transport_factory=lambda *_: transport)
        with patch("integrations.wecom.bot_sender.time.monotonic",
                   side_effect=(0, 0, 0, 0, 0, 8, 8)):
            with self.assertRaises(WeComBotSendError) as error:
                sender.deliver(self.request)

        self.assertNotIsInstance(error.exception, WeComBotDeliveryUnknown)
        self.assertEqual(error.exception.code, "wecom_bot_auth_timeout")
        self.assertEqual([frame["cmd"] for frame in transport.sent], ["aibot_subscribe"])
        self.assertTrue(transport.closed)

    def test_partial_send_connection_reset_stays_unknown_across_reapproval(self):
        transport = FakeTransport([])
        send_attempts = []

        def partial_write(frame):
            transport.sent.append(frame)
            if frame["cmd"] == "aibot_subscribe":
                transport.frames.append(receipt(frame["headers"]["req_id"]))
            elif frame["cmd"] == "aibot_send_msg":
                send_attempts.append(frame)
                raise ConnectionResetError("synthetic partial write")

        transport.send = partial_write
        sender = WeComBotTextSender(transport_factory=lambda *_: transport)
        class Credentials:
            def get(self):
                return {"bot_id": "bot_123", "secret": "synthetic-secret"}

        class MediaStore:
            pass

        adapter = _ConfiguredWeComBotSender(Credentials(), sender, MediaStore())
        with tempfile.TemporaryDirectory() as directory:
            repository = SendRepository(Path(directory) / "hub.sqlite3")
            try:
                service = SendService(repository, {"wecom_bot": adapter})
                prepared = service.prepare({"platform": "wecom_bot", "account_id": "bot_123",
                    "conversation_type": "private", "conversation_id": "user-1", "text": "hello",
                    "request_id": "wecom-partial-write-1"})
                first = service.approve_and_deliver(prepared["request_id"], "wecom_bot")
                again = service.approve_and_deliver(prepared["request_id"], "wecom_bot")
                self.assertEqual(first["status"], "unknown")
                self.assertEqual(first["error_code"], "wecom_bot_delivery_unknown")
                self.assertEqual(again["status"], "unknown")
                self.assertEqual(len(send_attempts), 1)
                self.assertEqual(sum(frame["cmd"] == "aibot_send_msg" for frame in transport.sent), 1)
            finally:
                repository.close()

    def test_rejection_and_invalid_request_are_classified(self):
        transport = FakeTransport([])
        sender = WeComBotTextSender(transport_factory=lambda *_: transport)

        def reject(frame):
            transport.sent.append(frame)
            transport.frames.append(receipt(frame["headers"]["req_id"], errcode=93000))

        transport.send = reject
        with self.assertRaisesRegex(WeComBotSendError, "wecom_bot_rejected"):
            sender.deliver(self.request)
        self.assertEqual(len(transport.sent), 1)

        with self.assertRaisesRegex(WeComBotSendError, "wecom_bot_request_invalid"):
            sender.deliver({**self.request, "_bot_secret": ""})
        with self.assertRaisesRegex(WeComBotSendError, "wecom_bot_request_invalid"):
            sender.deliver({**self.request, "text": " \t\n"})
        with self.assertRaisesRegex(WeComBotSendError, "wecom_bot_conversation_type_invalid"):
            sender.deliver({**self.request, "conversation_type": "broadcast"})

    def test_invalid_media_signature_or_filename_is_rejected_before_connecting(self):
        connected = []
        sender = WeComBotTextSender(transport_factory=lambda *_: connected.append(True))
        for attachment in (
                {"type": "image", "filename": "photo.png", "data": b"not a PNG"},
                {"type": "file", "filename": "../note.txt", "data": b"plain text"}):
            with self.subTest(attachment=attachment), self.assertRaisesRegex(
                    WeComBotSendError, "wecom_bot_media_invalid"):
                sender.deliver({**self.request, "text": "", "_media_payload": attachment})
        self.assertEqual(connected, [])


if __name__ == "__main__":
    unittest.main()
