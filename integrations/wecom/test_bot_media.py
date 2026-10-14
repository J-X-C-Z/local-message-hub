import base64
import hashlib
import io
import os
import tempfile
import time
import unittest
from pathlib import Path

from .bot_media import MAX_MEDIA_SIZE, WeComBotMediaStore
from .bot_sender import WeComBotSendError, WeComBotTextSender


class MediaProtocolTransport:
    def __init__(self):
        self.sent = []
        self.frames = []

    def send(self, frame):
        self.sent.append(frame)
        body = {}
        if frame["cmd"] == "aibot_upload_media_init":
            body = {"upload_id": "upload-synthetic"}
        elif frame["cmd"] == "aibot_upload_media_finish":
            body = {"media_id": "media-synthetic"}
        elif frame["cmd"] == "aibot_send_msg":
            body = {"msgid": "message-synthetic"}
        self.frames.append({"headers": frame["headers"], "errcode": 0, "body": body})

    def recv(self, _timeout):
        return self.frames.pop(0)

    def close(self):
        pass


class RejectChunkTransport(MediaProtocolTransport):
    def send(self, frame):
        self.sent.append(frame)
        errcode = 93000 if frame["cmd"] == "aibot_upload_media_chunk" else 0
        body = {"upload_id": "upload-synthetic"} if frame["cmd"] == "aibot_upload_media_init" else {}
        self.frames.append({"headers": frame["headers"], "errcode": errcode, "body": body})


class WeComBotMediaTest(unittest.TestCase):
    def test_stream_upload_is_chunked_hashed_and_revalidated(self):
        data = b"\x89PNG\r\n\x1a\n" + b"x" * 100

        class RecordingStream(io.BytesIO):
            def __init__(self, value):
                super().__init__(value)
                self.read_sizes = []

            def read(self, size=-1):
                self.read_sizes.append(size)
                return super().read(size)

        with tempfile.TemporaryDirectory() as root:
            store = WeComBotMediaStore(root)
            source = RecordingStream(data)
            attachment = store.save_stream(source, len(data), "photo.png", "image", chunk_size=13)

            self.assertEqual(max(source.read_sizes), 13)
            self.assertEqual(attachment["size"], len(data))
            self.assertEqual(attachment["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(store.load(attachment)["data"], data)
            self.assertEqual(list(store.root.glob("*.part")), [])

    def test_stream_upload_rejects_truncation_and_invalid_text_without_artifacts(self):
        cases = ((b"partial", 20, "note.txt", "file", "不完整"),
                 (b"bad-\xff-text", 10, "note.txt", "file", "格式不支持"))
        with tempfile.TemporaryDirectory() as root:
            store = WeComBotMediaStore(root)
            for data, length, filename, media_type, message in cases:
                with self.subTest(filename=filename, data=data):
                    with self.assertRaisesRegex(ValueError, message):
                        store.save_stream(io.BytesIO(data), length, filename, media_type, chunk_size=3)
                    self.assertEqual(list(store.root.glob("*.part")), [])
                    self.assertEqual(list(store.root.glob("*.blob")), [])

    def test_stream_upload_rejects_source_that_overreads_declared_length(self):
        with tempfile.TemporaryDirectory() as root:
            store = WeComBotMediaStore(root)

            class OverreadingStream:
                def read(self, _size):
                    return b"%PDF-1.7 extra"

            with self.assertRaisesRegex(ValueError, "长度无效"):
                store.save_stream(OverreadingStream(), len(b"%PDF-1.7"),
                                  "note.pdf", "file", chunk_size=50)
            self.assertEqual(list(store.root.glob("*.part")), [])
            self.assertEqual(list(store.root.glob("*.blob")), [])

    def test_only_selected_supported_signatures_are_staged_and_revalidated(self):
        cases = [("image", "photo.png", b"\x89PNG\r\n\x1a\nsynthetic"),
                 ("voice", "voice.amr", b"#!AMR\nsynthetic"),
                 ("video", "clip.mp4", b"\x00\x00\x00\x18ftypisomsynthetic"),
                 ("file", "note.txt", b"synthetic text")]
        with tempfile.TemporaryDirectory() as root:
            store = WeComBotMediaStore(root)
            for media_type, filename, data in cases:
                attachment = store.save(data, filename, media_type)
                payload = store.load(attachment)
                self.assertEqual(payload, {"type": media_type, "filename": filename, "data": data})
                self.assertNotIn("path", attachment)
                store.remove(attachment)
                with self.assertRaises((OSError, ValueError)):
                    store.load(attachment)

    def test_rejects_extension_signature_mismatch_and_arbitrary_paths(self):
        with tempfile.TemporaryDirectory() as root:
            store = WeComBotMediaStore(root)
            for args in ((b"not png", "photo.png", "image"),
                         (b"#!AMR\n", "voice.mp3", "voice"),
                         (b"%PDF-1.7", "../secret.pdf", "file"),
                         (b"media", "x.mp4", "video")):
                with self.assertRaises(ValueError):
                    store.save(*args)
            with self.assertRaises(ValueError):
                store.load({"attachment_id": "C:/secret"})

    def test_oversized_cached_blob_is_rejected_before_reading(self):
        with tempfile.TemporaryDirectory() as root:
            store = WeComBotMediaStore(root)
            attachment = store.save(b"synthetic text", "note.txt", "file")
            blob = store.root / (attachment["attachment_id"] + ".blob")
            with blob.open("wb") as stream:
                stream.truncate(MAX_MEDIA_SIZE + 1)

            with self.assertRaisesRegex(ValueError, "50 MiB"):
                store.load(attachment)

    def test_refuses_redirected_cache_root_before_and_after_initialization(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            target = base / "outside"
            target.mkdir()
            redirected = base / "wecom-bot-outbox"
            try:
                redirected.symlink_to(target, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")
            with self.assertRaises(RuntimeError):
                WeComBotMediaStore(base)
            redirected.unlink()
            store = WeComBotMediaStore(base)
            redirected.rename(base / "original-outbox")
            redirected.symlink_to(target, target_is_directory=True)
            with self.assertRaises(RuntimeError):
                store.save(b"%PDF-1.7", "note.pdf", "file")
            self.assertEqual(list(target.iterdir()), [])

    def test_orphan_cleanup_reclaims_stale_partial_uploads(self):
        with tempfile.TemporaryDirectory() as root:
            store = WeComBotMediaStore(root)
            stale_part = store.root / ("a" * 48 + ".part")
            recent_part = store.root / ("b" * 48 + ".part")
            retained_blob = store.root / ("c" * 48 + ".blob")
            orphan_blob = store.root / ("d" * 48 + ".blob")
            unrelated = store.root / "not-an-upload.part"
            for path in (stale_part, recent_part, retained_blob, orphan_blob, unrelated):
                path.write_bytes(b"synthetic")
            old = time.time() - 3600
            os.utime(stale_part, (old, old))
            os.utime(orphan_blob, (old, old))

            store.remove_orphans({"c" * 48}, max_age=60)

            self.assertFalse(stale_part.exists())
            self.assertFalse(orphan_blob.exists())
            self.assertTrue(recent_part.exists())
            self.assertTrue(retained_blob.exists())
            self.assertTrue(unrelated.exists())

    def test_uploads_matching_type_then_sends_media_id_once(self):
        payloads = {
            "image": ("photo.png", b"\x89PNG\r\n\x1a\n" + b"x" * 16),
            "file": ("note.txt", b"synthetic text"),
            "voice": ("voice.amr", b"#!AMR\nsynthetic"),
            "video": ("clip.mp4", b"\x00\x00\x00\x18ftypisomsynthetic"),
        }
        for media_type in ("image", "file", "voice", "video"):
            with self.subTest(media_type=media_type):
                filename, data = payloads[media_type]
                transport = MediaProtocolTransport()
                timeouts = []
                sender = WeComBotTextSender(transport_factory=lambda _endpoint, timeout:
                                            timeouts.append(timeout) or transport)
                result = sender.deliver({"_bot_id": "bot", "_bot_secret": "secret",
                    "conversation_type": "private", "conversation_id": "target",
                    "request_id": "synthetic-request", "text": "", "_media_payload": {
                        "type": media_type, "filename": filename, "data": data}})
                self.assertEqual(result["message_id"], "message-synthetic")
                self.assertGreaterEqual(timeouts[0], 299)
                self.assertEqual([frame["cmd"] for frame in transport.sent], [
                    "aibot_subscribe", "aibot_upload_media_init", "aibot_upload_media_chunk",
                    "aibot_upload_media_finish", "aibot_send_msg"])
                init = transport.sent[1]["body"]
                self.assertEqual((init["type"], init["filename"], init["total_size"], init["total_chunks"]),
                                 (media_type, filename, len(data), 1))
                self.assertEqual(init["md5"], hashlib.md5(data).hexdigest())
                self.assertEqual(base64.b64decode(transport.sent[2]["body"]["base64_data"]), data)
                self.assertEqual(transport.sent[-1]["body"], {
                    "chatid": "target", "msgtype": media_type,
                    media_type: {"media_id": "media-synthetic"}})

    def test_upload_chunks_over_boundary_in_order_then_sends_once(self):
        data = b"x" * (512 * 1024) + b"y"
        transport = MediaProtocolTransport()
        sender = WeComBotTextSender(transport_factory=lambda *_: transport)

        result = sender.deliver({"_bot_id": "bot", "_bot_secret": "secret",
            "conversation_type": "group", "conversation_id": "group-target",
            "request_id": "synthetic-request", "text": "", "_media_payload": {
                "type": "file", "filename": "selected.txt", "data": data}})

        chunks = [frame for frame in transport.sent
                  if frame["cmd"] == "aibot_upload_media_chunk"]
        self.assertEqual([frame["body"]["chunk_index"] for frame in chunks], [0, 1])
        self.assertEqual(b"".join(base64.b64decode(frame["body"]["base64_data"])
                                   for frame in chunks), data)
        self.assertEqual(transport.sent[1]["body"]["total_chunks"], 2)
        sends = [frame for frame in transport.sent if frame["cmd"] == "aibot_send_msg"]
        self.assertEqual(len(sends), 1)
        self.assertEqual(sends[0]["body"], {
            "chatid": "group-target", "msgtype": "file",
            "file": {"media_id": "media-synthetic"}})
        self.assertEqual(result, {"message_id": "message-synthetic", "identity": "wecom_bot"})

    def test_staged_attachment_is_validated_and_uploaded_without_whole_file_buffer(self):
        data = b"synthetic text " + b"x" * (512 * 1024 + 31)
        transport = MediaProtocolTransport()
        sender = WeComBotTextSender(transport_factory=lambda *_: transport)
        with tempfile.TemporaryDirectory() as root:
            store = WeComBotMediaStore(root)
            attachment = store.save(data, "selected.txt", "file")
            with store.open_for_send(attachment) as payload:
                original = payload["stream"]

                class RecordingStream:
                    def __init__(self, stream):
                        self.stream = stream
                        self.read_sizes = []

                    def read(self, size=-1):
                        self.read_sizes.append(size)
                        return self.stream.read(size)

                stream = RecordingStream(original)
                request = {"_bot_id": "bot", "_bot_secret": "secret",
                    "conversation_type": "group", "conversation_id": "target",
                    "request_id": "synthetic-request", "text": "",
                    "_media_payload": {**payload, "stream": stream}}
                result = sender.deliver(request)
                self.assertEqual(result["message_id"], "message-synthetic")
                self.assertNotIn("data", request["_media_payload"])
                self.assertLessEqual(max(stream.read_sizes), 512 * 1024)
                chunks = [frame for frame in transport.sent
                          if frame["cmd"] == "aibot_upload_media_chunk"]
                self.assertEqual(b"".join(base64.b64decode(frame["body"]["base64_data"])
                                          for frame in chunks), data)

    def test_staged_file_change_during_upload_never_finishes_or_sends(self):
        data = b"synthetic text"
        transport = MediaProtocolTransport()
        sender = WeComBotTextSender(transport_factory=lambda *_: transport)
        with tempfile.TemporaryDirectory() as root:
            store = WeComBotMediaStore(root)
            attachment = store.save(data, "selected.txt", "file")
            with store.open_for_send(attachment) as payload:
                (store.root / (attachment["attachment_id"] + ".blob")).write_bytes(b"")
                with self.assertRaisesRegex(WeComBotSendError, "wecom_bot_media_invalid"):
                    sender.deliver({"_bot_id": "bot", "_bot_secret": "secret",
                        "conversation_type": "private", "conversation_id": "target",
                        "request_id": "synthetic-request", "text": "",
                        "_media_payload": payload})
            commands = [frame["cmd"] for frame in transport.sent]
            self.assertNotIn("aibot_upload_media_finish", commands)
            self.assertNotIn("aibot_send_msg", commands)

    def test_rejected_chunk_never_finishes_or_sends_message(self):
        transport = RejectChunkTransport()
        sender = WeComBotTextSender(transport_factory=lambda *_: transport)

        with self.assertRaisesRegex(WeComBotSendError, "wecom_bot_rejected"):
            sender.deliver({"_bot_id": "bot", "_bot_secret": "secret",
                "conversation_type": "private", "conversation_id": "target",
                "request_id": "synthetic-request", "text": "", "_media_payload": {
                    "type": "file", "filename": "note.txt", "data": b"x"}})

        self.assertEqual([frame["cmd"] for frame in transport.sent], [
            "aibot_subscribe", "aibot_upload_media_init", "aibot_upload_media_chunk"])


if __name__ == "__main__":
    unittest.main()
