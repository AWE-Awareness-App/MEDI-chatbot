import uuid
import subprocess
from pathlib import Path

import httpx
from faster_whisper import WhisperModel
from app.core.config import settings

TMP_DIR = Path(getattr(settings, "TMP_DIR", "/tmp/medi_audio"))
TMP_DIR.mkdir(parents=True, exist_ok=True)

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

def _ffmpeg_to_wav(in_path: Path, out_path: Path) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(in_path),
        "-t", str(MAX_AUDIO_SECONDS),
        "-ac", "1",
        "-ar", "16000",
        "-f", "wav",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[:2000]}")

def _transcribe_wav(wav_path: Path) -> str:
    segments, _info = _model.transcribe(
        str(wav_path),
        vad_filter=True,
        word_timestamps=False,
    )
    return " ".join(s.text.strip() for s in segments).strip()

def transcribe_audio_bytes(audio_bytes: bytes) -> str:
    if len(audio_bytes) > MAX_AUDIO_MB * 1024 * 1024:
        raise ValueError("Audio too large")

    uid = uuid.uuid4().hex
    raw_path = TMP_DIR / f"{uid}.bin"
    wav_path = TMP_DIR / f"{uid}.wav"

    try:
        raw_path.write_bytes(audio_bytes)
        _ffmpeg_to_wav(raw_path, wav_path)
        return _transcribe_wav(wav_path)
    finally:
        for p in (raw_path, wav_path):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass

def download_twilio_media(media_url: str) -> bytes:
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        r = client.get(media_url, auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN))
        r.raise_for_status()
        data = r.content
    if len(data) > MAX_AUDIO_MB * 1024 * 1024:
        raise ValueError("Audio too large")
    return data
