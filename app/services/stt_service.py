import logging
import subprocess

import httpx
import numpy as np
from faster_whisper import WhisperModel

from app.core.observability import trace_call
from app.core.config import settings
from app.services.language_service import normalize_language

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
logger = logging.getLogger(__name__)


@trace_call
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
    logger.info("stt: ffmpeg decode start bytes=%s max_seconds=%s", len(audio_bytes), MAX_AUDIO_SECONDS)
    proc = subprocess.run(cmd, input=audio_bytes, capture_output=True)
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="ignore")
        raise RuntimeError(f"ffmpeg failed: {stderr[:2000]}")
    logger.info("stt: ffmpeg decode completed pcm_bytes=%s", len(proc.stdout))
    return proc.stdout


@trace_call
def _transcribe_pcm_f32_with_language(pcm_bytes: bytes) -> tuple[str, str | None]:
    if not pcm_bytes:
        return "", None

    audio = np.frombuffer(pcm_bytes, dtype=np.float32)
    segments, info = _model.transcribe(
        audio,
        vad_filter=True,
        word_timestamps=False,
    )
    text = " ".join(s.text.strip() for s in segments).strip()
    lang = normalize_language(getattr(info, "language", None))
    return text, lang


@trace_call
def transcribe_audio_bytes(audio_bytes: bytes) -> str:
    text, _lang = transcribe_audio_bytes_with_language(audio_bytes)
    return text


@trace_call
def transcribe_audio_bytes_with_language(audio_bytes: bytes) -> tuple[str, str | None]:
    if len(audio_bytes) > MAX_AUDIO_MB * 1024 * 1024:
        raise ValueError("Audio too large")

    pcm = _ffmpeg_to_pcm_f32(audio_bytes)
    return _transcribe_pcm_f32_with_language(pcm)


@trace_call
def download_twilio_media(media_url: str) -> bytes:
    logger.info("stt: downloading twilio media")
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        r = client.get(media_url, auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN))
        r.raise_for_status()
        data = r.content
    if len(data) > MAX_AUDIO_MB * 1024 * 1024:
        raise ValueError("Audio too large")
    logger.info("stt: downloaded twilio media bytes=%s", len(data))
    return data
