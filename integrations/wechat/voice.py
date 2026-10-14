"""Decode cached Tencent SILK into a browser-playable local WAV."""

from pathlib import Path
import os
import wave

MAX_SILK_BYTES = 16 * 1024 * 1024
MAX_PCM_BYTES = 64 * 1024 * 1024


def voice_media_result(silk_path: str) -> dict:
    source = Path(silk_path)
    try:
        import pilk_nogil
    except ImportError:
        return {"available": True, "path": str(source), "original_path": str(source),
                "playable": False, "mime_type": "application/octet-stream",
                "reason": "voice_decoder_unavailable"}
    destination = source.with_suffix(".wav")
    temporary = destination.with_suffix(".wav.tmp")
    try:
        if source.stat().st_size > MAX_SILK_BYTES:
            raise OverflowError("SILK input exceeds decode limit")
        silk = source.read_bytes()
        if len(silk) > MAX_SILK_BYTES:
            raise OverflowError("SILK input exceeds decode limit")
        pcm = pilk_nogil.decode(silk, None, pcm_rate=24000)
        if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2:
            raise ValueError("Invalid decoded PCM")
        if len(pcm) > MAX_PCM_BYTES:
            raise OverflowError("decoded PCM exceeds decode limit")
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(pcm)
        os.replace(temporary, destination)
    except OverflowError:
        temporary.unlink(missing_ok=True)
        return {"available": True, "path": str(source), "original_path": str(source),
                "playable": False, "mime_type": "application/octet-stream",
                "reason": "voice_decode_limit_exceeded"}
    except Exception:
        temporary.unlink(missing_ok=True)
        return {"available": True, "path": str(source), "original_path": str(source),
                "playable": False, "mime_type": "application/octet-stream",
                "reason": "voice_decode_failed"}
    return {"available": True, "path": str(destination), "original_path": str(source),
            "playable": True, "mime_type": "audio/wav", "reason": None}
