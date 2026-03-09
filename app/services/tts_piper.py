import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import httpx

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
    # Backward compatibility no-op. Azure Neural TTS does not require a local model path.
    return ""


def _required_env(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing {name} for Azure Neural TTS")
    return value


def _looks_like_storage_account_key(value: str) -> bool:
    # Azure Storage account keys are long base64 strings (often ending with '=').
    return len(value) >= 80 and any(ch in value for ch in ["+", "/", "="])


def _voice_lang(voice_name: str) -> str:
    # ex: en-US-JennyNeural -> en-US
    parts = voice_name.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return "en-US"


def _build_ssml(text: str, voice: str, style: str | None, rate: str, pitch: str) -> str:
    safe_text = escape(text)
    lang = _voice_lang(voice)
    if style:
        return (
            "<speak version='1.0' xml:lang='{lang}'>"
            "<voice name='{voice}'>"
            "<mstts:express-as style='{style}' xmlns:mstts='https://www.w3.org/2001/mstts'>"
            "<prosody rate='{rate}' pitch='{pitch}'>{text}</prosody>"
            "</mstts:express-as>"
            "</voice>"
            "</speak>"
        ).format(lang=lang, voice=voice, style=escape(style), rate=escape(rate), pitch=escape(pitch), text=safe_text)
    return (
        "<speak version='1.0' xml:lang='{lang}'>"
        "<voice name='{voice}'>"
        "<prosody rate='{rate}' pitch='{pitch}'>{text}</prosody>"
        "</voice>"
        "</speak>"
    ).format(lang=lang, voice=voice, rate=escape(rate), pitch=escape(pitch), text=safe_text)


class PiperTTS:
    def __init__(self):
        self.model_path = _resolve_model_path()

        self.public_base_url = _normalize_public_base_url(
            os.environ.get("PUBLIC_BASE_URL", "")
        )

        self.azure_key = _required_env("AZURE_SPEECH_KEY")
        self.azure_region = _required_env("AZURE_SPEECH_REGION")
        self.azure_voice = (os.environ.get("AZURE_TTS_VOICE") or "en-US-JennyNeural").strip()
        self.azure_style = (os.environ.get("AZURE_TTS_STYLE") or "").strip() or None
        self.azure_rate = (os.environ.get("AZURE_TTS_RATE") or "0%").strip()
        self.azure_pitch = (os.environ.get("AZURE_TTS_PITCH") or "0%").strip()

        default_audio_dir = (os.environ.get("LOCAL_AUDIO_STORAGE_DIR") or "/app/.local_audio").strip()
        tts_storage_dir = (os.environ.get("TTS_STORAGE_DIR") or f"{default_audio_dir}/tts").strip()
        self.tts_dir = Path(tts_storage_dir)
        self.tts_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, text: str, target: Target, voice: str) -> str:
        style = self.azure_style or ""
        key_input = f"{voice}|{target}|{self.azure_voice}|{style}|{self.azure_rate}|{self.azure_pitch}|{text}"
        return hashlib.sha256(key_input.encode("utf-8")).hexdigest()[:32]

    def synthesize(self, text: str, target: Target, voice: str = "default") -> TTSOut:
        text = (text or "").strip()
        if not text:
            raise ValueError("Empty text")

        key = self._key(text, target, voice)

        if target == "web":
            out_path = self.tts_dir / f"{key}.mp3"
            mime = "audio/mpeg"
            output_format = "audio-24khz-48kbitrate-mono-mp3"
        else:
            out_path = self.tts_dir / f"{key}.ogg"
            mime = "audio/ogg"
            output_format = "ogg-24khz-16bit-mono-opus"

        # Cache
        if out_path.exists():
            return TTSOut(
                public_url=f"{self.public_base_url}/media/tts/{out_path.name}",
                file_path=out_path,
                mime_type=mime,
            )

        ssml = _build_ssml(
            text=text,
            voice=self.azure_voice,
            style=self.azure_style,
            rate=self.azure_rate,
            pitch=self.azure_pitch,
        )
        url = f"https://{self.azure_region}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Ocp-Apim-Subscription-Key": self.azure_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": output_format,
            "User-Agent": "medi-tts",
        }
        resp = httpx.post(url, headers=headers, content=ssml.encode("utf-8"), timeout=60.0)
        if resp.status_code != 200:
            hint = ""
            if resp.status_code == 401 and _looks_like_storage_account_key(self.azure_key):
                hint = (
                    " Hint: AZURE_SPEECH_KEY appears to be a Storage Account key. "
                    "Use a key from an Azure Speech resource (or Azure AI Services Speech-capable resource)."
                )
            raise RuntimeError(
                f"Azure TTS failed ({resp.status_code}): {resp.text[:400]}{hint}"
            )
        out_path.write_bytes(resp.content)
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError("Azure TTS returned empty audio")

        return TTSOut(
            public_url=f"{self.public_base_url}/media/tts/{out_path.name}",
            file_path=out_path,
            mime_type=mime,
        )
