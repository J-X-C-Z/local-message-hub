from __future__ import annotations

import hashlib
import io
import json
import tempfile
import threading
import unittest
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hub.server import Handler, Hub
from hub.voice_transcription import VoiceTranscriber, decode_pcm_wav


def wav_fixture():
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        target.writeframes(b"\x10\x00" * 1600)
    return output.getvalue()


class FakeTranscriber:
    def __init__(self, text="测试语音内容"):
        self.text = text
        self.calls = []

    def status(self):
        return {"available": True, "reason": None, "message": "测试识别器已就绪。"}

    def transcribe(self, payload):
        rate, pcm = decode_pcm_wav(payload)
        self.calls.append((rate, pcm))
        return {"available": True, "transcript": self.text, "reason": None}


class FakeVoiceProvider:
    def __init__(self, path=None):
        self.path = path
        self.calls = []

    def media(self, row):
        self.calls.append(row["id"])
        if self.path is None:
            return {"available": False, "path": None, "reason": "cache_missing"}
        return {"available": True, "path": str(self.path), "reason": None}


class VoiceTranscriptionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.audio_path = self.root / "voice.wav"
        self.audio_path.write_bytes(wav_fixture())
        self.transcriber = FakeTranscriber()
        self.hub = Hub(self.root / "storage", resume_imports=False,
                       voice_transcriber=self.transcriber)
        self.hub.store.ingest("qq", "fixture-account", [
            {"id": "voice-room", "title": "Synthetic voice"}], [
            {"id": "voice-1", "conversation_id": "voice-room", "timestamp": 10,
             "type": "voice", "text": "", "file_name": "fixture.wav"},
            {"id": "text-1", "conversation_id": "voice-room", "timestamp": 11,
             "type": "text", "text": "unchanged source message"},
        ])
        self.provider = FakeVoiceProvider(self.audio_path)
        self.hub.providers["qq"] = self.provider
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.hub = self.hub
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.hub.close()
        self.temp.cleanup()

    def request(self, path, body=None):
        headers = {"Content-Type": "application/json"} if body is not None else {}
        request = Request(self.url + path, headers=headers,
                          data=json.dumps(body).encode() if body is not None else None)
        return urlopen(request, timeout=3)

    def enable(self):
        with self.request("/api/settings", {"capabilities": {"voice_transcription": True}}) as response:
            return json.load(response)

    def payload(self, **changes):
        return {"platform": "qq", "account_id": "fixture-account", "message_id": "voice-1",
                "conversation_id": "voice-room", **changes}

    def test_default_off_capability_and_status(self):
        self.assertFalse(self.hub.settings["capabilities"]["voice_transcription"])
        with self.assertRaises(HTTPError) as denied:
            self.request("/api/voice/transcribe", self.payload())
        self.assertEqual(denied.exception.code, 403)
        self.enable()
        with self.request("/api/voice/transcription-status") as response:
            self.assertTrue(json.load(response)["available"])

    def test_api_uses_only_provider_resolved_audio_and_does_not_persist_text(self):
        self.enable()
        before = hashlib.sha256(self.audio_path.read_bytes()).hexdigest()
        with self.request("/api/voice/transcribe", self.payload()) as response:
            result = json.load(response)
        self.assertEqual(result["transcript"], self.transcriber.text)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(len(self.transcriber.calls), 1)
        self.assertEqual(hashlib.sha256(self.audio_path.read_bytes()).hexdigest(), before)
        self.assertEqual(self.hub.store.get_message("qq", "fixture-account", "text-1")["text"],
                         "unchanged source message")
        self.assertNotIn(self.transcriber.text.encode(), (self.root / "storage" / "hub.sqlite3").read_bytes())
        self.assertFalse(self.hub.media_paths, "transcription must consume its short-lived media token")

    def test_identity_binding_rejects_wrong_conversation_and_arbitrary_paths(self):
        self.enable()
        with self.assertRaises(HTTPError) as wrong_conversation:
            self.request("/api/voice/transcribe", self.payload(conversation_id="another-room"))
        self.assertEqual(wrong_conversation.exception.code, 400)
        with self.assertRaises(HTTPError) as wrong_account:
            self.request("/api/voice/transcribe", self.payload(account_id="other-account"))
        self.assertEqual(wrong_account.exception.code, 400)
        with self.assertRaises(HTTPError) as arbitrary_path:
            self.request("/api/voice/transcribe", self.payload(path=str(self.audio_path)))
        self.assertEqual(arbitrary_path.exception.code, 400)
        with self.assertRaises(HTTPError) as non_voice:
            self.request("/api/voice/transcribe", self.payload(message_id="text-1"))
        self.assertEqual(non_voice.exception.code, 400)
        self.assertEqual(self.provider.calls, [])

    def test_cache_miss_is_explicit_and_never_calls_recognizer(self):
        self.enable()
        self.hub.providers["qq"] = FakeVoiceProvider()
        with self.request("/api/voice/transcribe", self.payload()) as response:
            result = json.load(response)
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "audio_not_cached")
        self.assertFalse(self.transcriber.calls)

    def test_invalid_audio_is_rejected_before_recognition(self):
        self.enable()
        self.audio_path.write_bytes(b"not a wav")
        with self.assertRaises(HTTPError) as invalid:
            self.request("/api/voice/transcribe", self.payload())
        self.assertEqual(invalid.exception.code, 400)
        self.assertFalse(self.transcriber.calls)

    def test_missing_optional_model_is_explicit_and_never_starts_runtime(self):
        transcriber = VoiceTranscriber(model_dir=self.root / "missing-model", node_path="unused")
        with patch("hub.voice_transcription.subprocess.run", side_effect=AssertionError("runtime started")), \
             patch("urllib.request.urlopen", side_effect=AssertionError("network requested")):
            status = transcriber.status()
        self.assertFalse(status["available"])
        self.assertEqual(status["reason"], "model_missing")

    def test_pcm_parser_downmixes_stereo_and_rejects_invalid_input(self):
        output = io.BytesIO()
        with wave.open(output, "wb") as target:
            target.setnchannels(2)
            target.setsampwidth(2)
            target.setframerate(8000)
            target.writeframes(b"\x01\x00\xff\x00" * 8)
        rate, pcm = decode_pcm_wav(output.getvalue())
        self.assertEqual(rate, 8000)
        self.assertEqual(len(pcm), 16)
        with self.assertRaises(ValueError):
            decode_pcm_wav(b"bad")


if __name__ == "__main__":
    unittest.main()
