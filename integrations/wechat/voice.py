"""Decode cached Tencent SILK into a browser-playable local WAV."""

from pathlib import Path
import os
import wave


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
        pcm = pilk_nogil.decode(source.read_bytes(), None, pcm_rate=24000)
        if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2:
            raise ValueError("Invalid decoded PCM")
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(pcm)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        return {"available": True, "path": str(source), "original_path": str(source),
                "playable": False, "mime_type": "application/octet-stream",
                "reason": "voice_decode_failed"}
    return {"available": True, "path": str(destination), "original_path": str(source),
            "playable": True, "mime_type": "audio/wav", "reason": None}
