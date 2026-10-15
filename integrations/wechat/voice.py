"""Decode cached Tencent SILK into a browser-playable local WAV."""

from pathlib import Path
import hashlib
import os
import tempfile
import wave

MAX_SILK_BYTES = 16 * 1024 * 1024
MAX_PCM_BYTES = 64 * 1024 * 1024


def voice_media_result(silk_path: str, output_dir: str | Path | None = None) -> dict:
    source = Path(silk_path)
    try:
        import pilk_nogil
    except ImportError:
        return {"available": True, "path": str(source), "original_path": str(source),
                "playable": False, "mime_type": "application/octet-stream",
                "reason": "voice_decoder_unavailable"}
    temporary = None
    try:
        if source.stat().st_size > MAX_SILK_BYTES:
            raise OverflowError("SILK input exceeds decode limit")
        with source.open("rb") as input_file:
            silk = input_file.read(MAX_SILK_BYTES + 1)
        if len(silk) > MAX_SILK_BYTES:
            raise OverflowError("SILK input exceeds decode limit")
        pcm = pilk_nogil.decode(silk, None, pcm_rate=24000)
        if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2:
            raise ValueError("Invalid decoded PCM")
        if len(pcm) > MAX_PCM_BYTES:
            raise OverflowError("decoded PCM exceeds decode limit")
        directory = Path(output_dir) if output_dir is not None else (
            Path(os.environ.get("LMH_STORAGE_DIR", Path(__file__).resolve().parents[2] / ".local"))
            / "media" / "voice")
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / (hashlib.sha256(silk).hexdigest() + ".wav")
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".wav.tmp", delete=False) as temp_file:
            temporary = Path(temp_file.name)
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(pcm)
        os.replace(temporary, destination)
    except OverflowError:
        if temporary:
            temporary.unlink(missing_ok=True)
        return {"available": True, "path": str(source), "original_path": str(source),
                "playable": False, "mime_type": "application/octet-stream",
                "reason": "voice_decode_limit_exceeded"}
    except Exception:
        if temporary:
            temporary.unlink(missing_ok=True)
        return {"available": True, "path": str(source), "original_path": str(source),
                "playable": False, "mime_type": "application/octet-stream",
                "reason": "voice_decode_failed"}
    return {"available": True, "path": str(destination), "original_path": str(source),
            "playable": True, "mime_type": "audio/wav", "reason": None}
