import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from xml.sax.saxutils import escape as xml_escape

import httpx

from app.services.azure_blob import build_blob_read_url, upload_audio_bytes

Target = Literal["web", "whatsapp"]


@dataclass
class TTSOut:
    public_url: str
    mime_type: str
    storage_path: str
    file_path: str | None = None


def _output_format_for_target(target: Target) -> str:
    if target == "whatsapp":
        return "ogg-24khz-16bit-mono-opus"
    return "audio-24khz-48kbitrate-mono-mp3"


def _mime_for_target(target: Target) -> str:
    if target == "whatsapp":
        return "audio/ogg"
    return "audio/mpeg"


def _ext_for_target(target: Target) -> str:
    if target == "whatsapp":
        return "ogg"
    return "mp3"


def _resolve_piper_model_path() -> str:
    configured = (os.getenv("PIPER_MODEL_PATH") or "").strip()

    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))

    candidates.append(Path("/app/piper_models/en_US-hfc_female-medium.onnx"))
    candidates.append(
        Path(__file__).resolve().parents[2] / "piper_models" / "en_US-hfc_female-medium.onnx"
    )

    for path in candidates:
        if path.is_file():
            return str(path)

    if configured:
        raise RuntimeError(f"PIPER_MODEL_PATH does not exist: {configured}")

    raise RuntimeError(
        "Azure Speech is not configured and no Piper model was found. "
        "Set AZURE_SPEECH_KEY/AZURE_SPEECH_REGION or PIPER_MODEL_PATH."
    )


class PiperTTS:
    """
    Kept class name for compatibility with existing imports.
    Backend selection:
    - Azure Neural TTS if AZURE_SPEECH_KEY and AZURE_SPEECH_REGION are set
    - Piper fallback otherwise
    """

    def __init__(self):
        self.speech_key = (os.getenv("AZURE_SPEECH_KEY") or "").strip()
        self.speech_region = (os.getenv("AZURE_SPEECH_REGION") or "").strip()
        self.use_azure = bool(self.speech_region and re.fullmatch(r"[A-Fa-f0-9]{32}", self.speech_key or ""))

        self.voice = (os.getenv("AZURE_TTS_VOICE") or "en-US-JennyNeural").strip()
        self.style = (os.getenv("AZURE_TTS_STYLE") or "").strip()
        self.rate = (os.getenv("AZURE_TTS_RATE") or "0%").strip()
        self.pitch = (os.getenv("AZURE_TTS_PITCH") or "0%").strip()

        if not self.use_azure:
            self.piper_model_path = _resolve_piper_model_path()

        ttl_raw = (os.getenv("AZURE_BLOB_URL_TTL_SECONDS") or "86400").strip()
        self.url_ttl_seconds = int(ttl_raw or "86400")

    def _key(self, text: str, target: Target, voice: str) -> str:
        return hashlib.sha256(f"{voice}|{target}|{text}".encode("utf-8")).hexdigest()[:32]

    def _build_ssml(self, text: str) -> str:
        escaped_text = xml_escape(text)
        escaped_voice = xml_escape(self.voice)
        escaped_rate = xml_escape(self.rate)
        escaped_pitch = xml_escape(self.pitch)

        if self.style:
            escaped_style = xml_escape(self.style)
            inner = f'<mstts:express-as style="{escaped_style}">{escaped_text}</mstts:express-as>'
        else:
            inner = escaped_text

        return (
            '<speak version="1.0" xml:lang="en-US" '
            'xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="https://www.w3.org/2001/mstts">'
            f'<voice name="{escaped_voice}">'
            f'<prosody rate="{escaped_rate}" pitch="{escaped_pitch}">{inner}</prosody>'
            '</voice>'
            '</speak>'
        )

    def _synthesize_azure(self, text: str, target: Target) -> bytes:
        endpoint = f"https://{self.speech_region}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Ocp-Apim-Subscription-Key": self.speech_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": _output_format_for_target(target),
            "User-Agent": "medi-tts",
        }
        ssml = self._build_ssml(text)

        with httpx.Client(timeout=90) as client:
            response = client.post(endpoint, headers=headers, content=ssml.encode("utf-8"))

        if response.status_code != 200:
            detail = (response.text or "").strip()
            if response.status_code == 401:
                detail = f"{detail} Check AZURE_SPEECH_KEY and AZURE_SPEECH_REGION."
            raise RuntimeError(f"Azure TTS failed ({response.status_code}): {detail}")

        if not response.content:
            raise RuntimeError("Azure TTS returned empty audio")

        return response.content

    def _synthesize_piper(self, text: str, target: Target) -> bytes:
        with tempfile.TemporaryDirectory(prefix="medi_tts_") as tmp:
            wav_path = Path(tmp) / "out.wav"
            if target == "web":
                out_path = Path(tmp) / "out.mp3"
                ffmpeg_cmd = [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(wav_path),
                    "-codec:a",
                    "libmp3lame",
                    "-q:a",
                    "4",
                    str(out_path),
                ]
            else:
                out_path = Path(tmp) / "out.ogg"
                ffmpeg_cmd = [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(wav_path),
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "24k",
                    "-vbr",
                    "on",
                    "-application",
                    "voip",
                    str(out_path),
                ]

            piper_cmd = [
                "python",
                "-m",
                "piper",
                "--model",
                self.piper_model_path,
                "--output_file",
                str(wav_path),
            ]
            p = subprocess.run(
                piper_cmd,
                input=text.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if p.returncode != 0 or not wav_path.exists():
                err = p.stderr.decode("utf-8", errors="ignore")
                raise RuntimeError(f"Piper failed: {err}")

            p2 = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if p2.returncode != 0 or not out_path.exists():
                err = p2.stderr.decode("utf-8", errors="ignore")
                raise RuntimeError(f"ffmpeg failed: {err}")

            return out_path.read_bytes()

    def synthesize(self, text: str, target: Target, voice: str = "default") -> TTSOut:
        text = (text or "").strip()
        if not text:
            raise ValueError("Empty text")

        mime = _mime_for_target(target)
        ext = _ext_for_target(target)
        key = self._key(text, target, voice)

        if self.use_azure:
            audio_bytes = self._synthesize_azure(text=text, target=target)
        else:
            audio_bytes = self._synthesize_piper(text=text, target=target)

        storage_path = upload_audio_bytes(
            data=audio_bytes,
            content_type=mime,
            filename=f"{key}.{ext}",
            prefix="tts",
        )

        public_url = build_blob_read_url(storage_path, expiry_seconds=self.url_ttl_seconds)

        return TTSOut(
            public_url=public_url,
            mime_type=mime,
            storage_path=storage_path,
            file_path=None,
        )

