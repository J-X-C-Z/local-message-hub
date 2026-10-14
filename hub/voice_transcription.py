"""Offline-only SenseVoice adapter for already-resolved local audio."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import wave


ROOT = Path(__file__).resolve().parents[1]
MAX_AUDIO_BYTES = 32 * 1024 * 1024
MAX_AUDIO_SECONDS = 10 * 60

_NODE_RUNNER = r"""
const fs = require('fs');
const sherpa = require('sherpa-onnx-node');
const model = process.argv[1];
const tokens = process.argv[2];
const sampleRate = Number(process.argv[3]);
const bytes = fs.readFileSync(0);
if (!bytes.length || bytes.length % 2) throw new Error('invalid_pcm');
const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
const samples = new Float32Array(bytes.length / 2);
for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
const recognizer = new sherpa.OfflineRecognizer({modelConfig: {senseVoice: {
  model, useInverseTextNormalization: 1
}, tokens, numThreads: 2, debug: 0}});
const stream = recognizer.createStream();
stream.acceptWaveform({sampleRate, samples});
recognizer.decode(stream);
const result = recognizer.getResult(stream);
const text = String(result?.text || '').replace(/<\|[^|>]+\|>/g, '').replace(/\s+/g, ' ').trim();
process.stdout.write(JSON.stringify({text}));
"""


def decode_pcm_wav(payload: bytes) -> tuple[int, bytes]:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_AUDIO_BYTES:
        raise ValueError("语音文件为空或超过 32 MiB 限制")
    try:
        with wave.open(io.BytesIO(payload), "rb") as source:
            channels = source.getnchannels()
            width = source.getsampwidth()
            rate = source.getframerate()
            frames = source.getnframes()
            if (source.getcomptype() != "NONE" or channels not in (1, 2) or width != 2
                    or not 8000 <= rate <= 48000 or frames <= 0
                    or frames / rate > MAX_AUDIO_SECONDS):
                raise ValueError
            data = source.readframes(frames)
    except (wave.Error, EOFError, OSError, ValueError):
        raise ValueError("语音格式无效，仅支持 16-bit PCM WAV") from None
    expected = frames * channels * width
    if len(data) != expected:
        raise ValueError("语音数据不完整")
    if channels == 1:
        return rate, data
    mono = bytearray(frames * 2)
    for index in range(frames):
        left = int.from_bytes(data[index * 4:index * 4 + 2], "little", signed=True)
        right = int.from_bytes(data[index * 4 + 2:index * 4 + 4], "little", signed=True)
        sample = max(-32768, min(32767, (left + right) // 2))
        mono[index * 2:index * 2 + 2] = sample.to_bytes(2, "little", signed=True)
    return rate, bytes(mono)


class VoiceTranscriber:
    """Use only an installed Sherpa runtime and the existing WeFlow model directory."""

    def __init__(self, recognizer=None, node_path=None, model_dir=None):
        self._recognizer = recognizer
        self._node_path = node_path
        configured_documents = os.environ.get("WEFLOW_DOCUMENTS_PATH", "").strip()
        documents = Path(configured_documents).expanduser() if configured_documents else Path.home() / "Documents"
        self.model_dir = Path(model_dir) if model_dir else documents / "WeFlow" / "models" / "sensevoice"

    def _runtime(self):
        node = self._node_path or shutil.which("node")
        system = {"windows": "win", "darwin": "darwin", "linux": "linux"}.get(platform.system().lower())
        machine = platform.machine().lower()
        arch = "x64" if machine in ("amd64", "x86_64") else "arm64" if machine in ("arm64", "aarch64") else None
        if not system or not arch:
            return None, "speech_runtime_missing"
        native_name = f"sherpa-onnx-{system}-{arch}"
        modules = ROOT / "node_modules"
        package = modules / "sherpa-onnx-node" / "package.json"
        native = modules / native_name / "package.json"
        if not node:
            return None, "node_runtime_missing"
        if not package.is_file() or not native.is_file():
            return None, "speech_runtime_missing"
        return node, None

    def status(self):
        model = self.model_dir / "model.int8.onnx"
        tokens = self.model_dir / "tokens.txt"
        if not model.is_file() or not tokens.is_file():
            return {"available": False, "reason": "model_missing",
                    "message": "本机未安装 SenseVoice 模型；转写不会自动下载模型。"}
        _node, reason = self._runtime()
        if reason:
            message = ("本机未安装 Node.js 运行时；转写不会安装额外组件。" if reason == "node_runtime_missing"
                       else "本机未安装语音识别运行时；请在设置中确认可选组件状态。")
            return {"available": False, "reason": reason, "message": message}
        return {"available": True, "reason": None, "message": "本机 SenseVoice 已就绪。"}

    def transcribe(self, payload: bytes):
        sample_rate, pcm = decode_pcm_wav(payload)
        if self._recognizer is not None:
            text = self._recognizer(sample_rate, pcm)
        else:
            status = self.status()
            if not status["available"]:
                return status
            node, _reason = self._runtime()
            model = self.model_dir / "model.int8.onnx"
            tokens = self.model_dir / "tokens.txt"
            env = {**os.environ, "NODE_PATH": str(ROOT / "node_modules")}
            try:
                completed = subprocess.run(
                    [node, "-e", _NODE_RUNNER, str(model), str(tokens), str(sample_rate)],
                    cwd=ROOT, env=env, input=pcm, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, timeout=180, check=False,
                )
                if completed.returncode:
                    return {"available": False, "reason": "recognition_failed",
                            "message": "本机语音识别失败。"}
                result = json.loads(completed.stdout.decode("utf-8"))
                text = result.get("text")
            except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
                return {"available": False, "reason": "recognition_failed",
                        "message": "本机语音识别失败。"}
        if not isinstance(text, str):
            return {"available": False, "reason": "recognition_failed",
                    "message": "本机语音识别未返回有效文本。"}
        return {"available": True, "transcript": text[:16000], "reason": None}
