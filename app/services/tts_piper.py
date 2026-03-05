import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

Target = Literal["web", "whatsapp"]

@dataclass
class TTSOut:
    public_url: str
    file_path: Path
    mime_type: str


def _normalize_public_base_url(raw_value: str) -> str:
    value = (raw_value or "").strip().rstrip("/")
    if not value:
        raise RuntimeError("Missing PUBLIC_BASE_URL (Twilio needs public media URLs)")

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(
            f"Invalid PUBLIC_BASE_URL '{value}'. Expected absolute http(s) URL."
        )
    return value


def _resolve_model_path() -> str:
    configured = (os.environ.get("PIPER_MODEL_PATH") or "").strip()
    if configured and Path(configured).is_file():
        return configured

    # Docker default (volume-mounted in docker-compose.yml)
    docker_default = Path("/app/.local_audio/piper_models/en_US-hfc_female-medium.onnx")
    if docker_default.is_file():
        return str(docker_default)

    # Local repo default
    repo_default = Path(__file__).resolve().parents[2] / "piper_models" / "en_US-hfc_female-medium.onnx"
    if repo_default.is_file():
        return str(repo_default)

    if configured:
        raise RuntimeError(f"PIPER_MODEL_PATH does not exist: {configured}")

    raise RuntimeError("Missing PIPER_MODEL_PATH and no default Piper model found")


class PiperTTS:
    def __init__(self):
        self.model_path = _resolve_model_path()

        self.public_base_url = _normalize_public_base_url(
            os.environ.get("PUBLIC_BASE_URL", "")
        )

        default_audio_dir = (os.environ.get("LOCAL_AUDIO_STORAGE_DIR") or "/app/.local_audio").strip()
        tts_storage_dir = (os.environ.get("TTS_STORAGE_DIR") or f"{default_audio_dir}/tts").strip()
        self.tts_dir = Path(tts_storage_dir)
        self.tts_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, text: str, target: Target, voice: str) -> str:
        return hashlib.sha256(f"{voice}|{target}|{text}".encode("utf-8")).hexdigest()[:32]

    def synthesize(self, text: str, target: Target, voice: str = "default") -> TTSOut:
        text = (text or "").strip()
        if not text:
            raise ValueError("Empty text")

        key = self._key(text, target, voice)

        wav_path = self.tts_dir / f"{key}.wav"
        if target == "web":
            out_path = self.tts_dir / f"{key}.mp3"
            mime = "audio/mpeg"
        else:
            out_path = self.tts_dir / f"{key}.ogg"
            mime = "audio/ogg"

        # Cache
        if out_path.exists():
            return TTSOut(
                public_url=f"{self.public_base_url}/media/tts/{out_path.name}",
                file_path=out_path,
                mime_type=mime,
            )

        # 1) Piper -> WAV
        piper_cmd = ["python", "-m", "piper", "--model", self.model_path, "--output_file", str(wav_path)]
        p = subprocess.run(
            piper_cmd,
            input=text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if p.returncode != 0 or not wav_path.exists():
            raise RuntimeError(f"Piper failed: {p.stderr.decode('utf-8', errors='ignore')}")

        # 2) WAV -> MP3 or OGG/OPUS
        if target == "web":
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-i", str(wav_path),
                "-codec:a", "libmp3lame",
                "-q:a", "4",
                str(out_path),
            ]
        else:
            # WhatsApp voice-note style: OGG container + Opus codec
            ffmpeg_cmd = [
                "ffmpeg", "-y",
                "-i", str(wav_path),
                "-c:a", "libopus",
                "-b:a", "24k",
                "-vbr", "on",
                "-application", "voip",
                str(out_path),
            ]

        p2 = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if p2.returncode != 0 or not out_path.exists():
            raise RuntimeError(f"ffmpeg failed: {p2.stderr.decode('utf-8', errors='ignore')}")

        # cleanup wav to save space
        try:
            wav_path.unlink(missing_ok=True)
        except Exception:
            pass

        return TTSOut(
            public_url=f"{self.public_base_url}/media/tts/{out_path.name}",
            file_path=out_path,
            mime_type=mime,
        )
