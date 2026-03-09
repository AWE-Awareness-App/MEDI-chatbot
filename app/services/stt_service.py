import subprocess

import httpx
import numpy as np
from faster_whisper import WhisperModel

from app.core.config import settings

# Required env/settings:
# settings.WHISPER_MODEL_SIZE (default "small")
# settings.WHISPER_DEVICE (default "cpu")
# settings.WHISPER_COMPUTE_TYPE (default "int8")
_model = WhisperModel(
    getattr(settings, "WHISPER_MODEL_SIZE", "small"),
    device=getattr(settings, "WHISPER_DEVICE", "cpu"),
    compute_type=getattr(settings, "WHISPER_COMPUTE_TYPE", "int8"),
)

MAX_AUDIO_MB = int(getattr(settings, "MAX_AUDIO_MB", 8))
MAX_AUDIO_SECONDS = int(getattr(settings, "MAX_AUDIO_SECONDS", 180))


def _ffmpeg_to_pcm_f32(audio_bytes: bytes) -> bytes:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-t",
        str(MAX_AUDIO_SECONDS),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "f32le",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, input=audio_bytes, capture_output=True)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="ignore")
        raise RuntimeError(f"ffmpeg failed: {stderr[:2000]}")
    return proc.stdout


def _transcribe_pcm_f32(pcm_bytes: bytes) -> str:
    if not pcm_bytes:
        return ""

    audio = np.frombuffer(pcm_bytes, dtype=np.float32)
    segments, _info = _model.transcribe(
        audio,
        vad_filter=True,
        word_timestamps=False,
    )
    return " ".join(s.text.strip() for s in segments).strip()


def transcribe_audio_bytes(audio_bytes: bytes) -> str:
    if len(audio_bytes) > MAX_AUDIO_MB * 1024 * 1024:
        raise ValueError("Audio too large")

    pcm = _ffmpeg_to_pcm_f32(audio_bytes)
    return _transcribe_pcm_f32(pcm)


def download_twilio_media(media_url: str) -> bytes:
    print("downloading")
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        r = client.get(media_url, auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN))
        r.raise_for_status()
        data = r.content
    if len(data) > MAX_AUDIO_MB * 1024 * 1024:
        raise ValueError("Audio too large")
    return data
