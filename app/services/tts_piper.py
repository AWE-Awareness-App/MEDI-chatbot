import hashlib
import io
import logging
import os
import re
import subprocess
import threading
import wave
from dataclasses import dataclass
from typing import Literal
from xml.sax.saxutils import escape as xml_escape

import httpx

from app.core.observability import instrument_module_functions
from app.services.azure_blob import build_blob_read_url, upload_audio_bytes
from app.services.language_service import normalize_language

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


def _is_valid_azure_speech_key(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Fa-f0-9]{32}", (value or "").strip()))


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _flatten_samples(obj) -> list[float]:
    if isinstance(obj, (list, tuple)):
        out: list[float] = []
        for item in obj:
            out.extend(_flatten_samples(item))
        return out
    try:
        return [float(obj)]
    except Exception:
        return []


_AZURE_TTS_VOICE_BY_LANGUAGE = {
    "en": "en-US-JennyNeural",
    "fr": "fr-FR-DeniseNeural",
    "ja": "ja-JP-NanamiNeural",
    "ar": "ar-SA-ZariyahNeural",
}
logger = logging.getLogger(__name__)


class PiperTTS:
    """
    Kept class name for compatibility with existing imports.
    Backend selection:
    - Azure Neural TTS if AZURE_SPEECH_KEY and AZURE_SPEECH_REGION are set correctly
    - Chatterbox fallback otherwise
    """

    _chatterbox_model = None
    _chatterbox_device: str | None = None
    _chatterbox_lock = threading.Lock()

    def __init__(self):
        self.speech_key = (os.getenv("AZURE_SPEECH_KEY") or "").strip()
        self.speech_region = (os.getenv("AZURE_SPEECH_REGION") or "").strip()
        self.use_azure = bool(self.speech_region and _is_valid_azure_speech_key(self.speech_key))

        self.voice = (os.getenv("AZURE_TTS_VOICE") or "en-US-JennyNeural").strip()
        self.style = (os.getenv("AZURE_TTS_STYLE") or "").strip()
        self.rate = (os.getenv("AZURE_TTS_RATE") or "0%").strip()
        self.pitch = (os.getenv("AZURE_TTS_PITCH") or "0%").strip()

        self.chatterbox_device = (os.getenv("CHATTERBOX_DEVICE") or "").strip().lower()
        self.chatterbox_exaggeration = _env_float("CHATTERBOX_EXAGGERATION", 0.3)
        self.chatterbox_cfg_weight = _env_float("CHATTERBOX_CFG_WEIGHT", 0.3)
        self.chatterbox_temperature = _env_float("CHATTERBOX_TEMPERATURE", 0.3)
        self.chatterbox_trim_threshold = _env_float("CHATTERBOX_TRIM_THRESHOLD", 0.005)
        self.chatterbox_trim_pad_ms = _env_int("CHATTERBOX_TRIM_PAD_MS", 180)

        ttl_raw = (os.getenv("AZURE_BLOB_URL_TTL_SECONDS") or "86400").strip()
        self.url_ttl_seconds = int(ttl_raw or "86400")
        logger.info(
            "tts backend initialized backend=%s chatterbox_device=%s trim_threshold=%.4f trim_pad_ms=%s",
            "azure" if self.use_azure else "chatterbox",
            self.chatterbox_device or "auto",
            self.chatterbox_trim_threshold,
            self.chatterbox_trim_pad_ms,
        )

    def _key(self, text: str, target: Target, voice: str, language: str | None) -> str:
        lang = normalize_language(language) or "en"
        return hashlib.sha256(f"{voice}|{lang}|{target}|{text}".encode("utf-8")).hexdigest()[:32]

    def _azure_voice_for_language(self, language: str | None) -> str:
        lang = normalize_language(language) or "en"

        env_override = (os.getenv(f"AZURE_TTS_VOICE_{lang.upper()}") or "").strip()
        if env_override:
            return env_override

        default_en_voice = _AZURE_TTS_VOICE_BY_LANGUAGE["en"]
        if self.voice and self.voice != default_en_voice:
            return self.voice

        return _AZURE_TTS_VOICE_BY_LANGUAGE.get(lang, self.voice or default_en_voice)

    def _build_ssml(self, text: str, voice_name: str) -> str:
        escaped_text = xml_escape(text)
        escaped_voice = xml_escape(voice_name)
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
            "</voice>"
            "</speak>"
        )

    def _synthesize_azure(self, text: str, target: Target, language: str | None = None) -> bytes:
        endpoint = f"https://{self.speech_region}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Ocp-Apim-Subscription-Key": self.speech_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": _output_format_for_target(target),
            "User-Agent": "medi-tts",
        }
        voice_name = self._azure_voice_for_language(language)
        ssml = self._build_ssml(text, voice_name=voice_name)

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

    def _resolve_chatterbox_device(self) -> str:
        if self.chatterbox_device:
            return self.chatterbox_device

        try:
            import torch

            if torch.cuda.is_available():
                return "cuda"
        except Exception:
            pass

        return "cpu"

    def _get_chatterbox_model(self):
        try:
            from chatterbox.tts import ChatterboxTTS
        except Exception as exc:
            raise RuntimeError(
                "Missing chatterbox-tts dependency. Install with `pip install chatterbox-tts`."
            ) from exc

        device = self._resolve_chatterbox_device()

        with self._chatterbox_lock:
            if self._chatterbox_model is None or self._chatterbox_device != device:
                self._chatterbox_model = ChatterboxTTS.from_pretrained(device=device)
                self._chatterbox_device = device

        return self._chatterbox_model

    def _trim_silence_numpy(self, arr, sample_rate: int):
        import numpy as np

        if arr.size == 0:
            return arr

        threshold = max(float(self.chatterbox_trim_threshold), 1e-6)
        active = np.flatnonzero(np.abs(arr) > threshold)
        if active.size == 0:
            logger.warning("chatterbox silence trim found no active samples, keeping original audio")
            return arr

        pad_samples = max(0, int(sample_rate * (self.chatterbox_trim_pad_ms / 1000.0)))
        start = max(0, int(active[0]) - pad_samples)
        end = min(arr.shape[0], int(active[-1]) + pad_samples + 1)
        trimmed = arr[start:end]

        if trimmed.shape[0] != arr.shape[0]:
            before_s = arr.shape[0] / float(sample_rate)
            after_s = trimmed.shape[0] / float(sample_rate)
            logger.info(
                "chatterbox silence trimmed duration_before=%.2fs duration_after=%.2fs removed=%.2fs",
                before_s,
                after_s,
                max(0.0, before_s - after_s),
            )
        return trimmed

    def _wav_tensor_to_bytes(self, wav_tensor, sample_rate: int) -> bytes:
        # Fast path with numpy if available.
        try:
            import numpy as np

            try:
                import torch

                if isinstance(wav_tensor, torch.Tensor):
                    arr = wav_tensor.detach().cpu().numpy()
                else:
                    arr = np.asarray(wav_tensor)
            except Exception:
                arr = np.asarray(wav_tensor)

            arr = np.asarray(arr, dtype=np.float32).squeeze()
            if arr.ndim == 0:
                arr = np.asarray([float(arr)], dtype=np.float32)
            elif arr.ndim > 1:
                arr = arr.reshape(-1)

            arr = np.clip(arr, -1.0, 1.0)
            arr = self._trim_silence_numpy(arr, sample_rate=sample_rate)
            pcm_bytes = (arr * 32767.0).astype(np.int16).tobytes()
        except Exception:
            # Fallback path without numpy.
            try:
                import torch

                if isinstance(wav_tensor, torch.Tensor):
                    samples = wav_tensor.detach().cpu().flatten().tolist()
                else:
                    samples = _flatten_samples(wav_tensor)
            except Exception:
                samples = _flatten_samples(wav_tensor)

            pcm = bytearray()
            for s in samples:
                if s > 1.0:
                    s = 1.0
                elif s < -1.0:
                    s = -1.0
                iv = int(s * 32767.0)
                if iv > 32767:
                    iv = 32767
                elif iv < -32768:
                    iv = -32768
                pcm.extend(int(iv).to_bytes(2, byteorder="little", signed=True))
            pcm_bytes = bytes(pcm)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(sample_rate))
            wf.writeframes(pcm_bytes)
        return buf.getvalue()

    def _transcode_wav_bytes(self, wav_bytes: bytes, target: Target) -> bytes:
        if target == "web":
            cmd = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "4",
                "-f",
                "mp3",
                "pipe:1",
            ]
        else:
            cmd = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                "pipe:0",
                "-c:a",
                "libopus",
                "-b:a",
                "24k",
                "-vbr",
                "on",
                "-application",
                "voip",
                "-f",
                "ogg",
                "pipe:1",
            ]

        proc = subprocess.run(cmd, input=wav_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode != 0 or not proc.stdout:
            err = proc.stderr.decode("utf-8", errors="ignore")
            raise RuntimeError(f"ffmpeg failed: {err}")

        return proc.stdout

    def _synthesize_chatterbox(self, text: str, target: Target) -> bytes:
        model = self._get_chatterbox_model()
        logger.info(
            "chatterbox synthesis start chars=%s target=%s exaggeration=%s cfg_weight=%s temperature=%s",
            len(text),
            target,
            self.chatterbox_exaggeration,
            self.chatterbox_cfg_weight,
            self.chatterbox_temperature,
        )
        wav = model.generate(
            text,
            exaggeration=self.chatterbox_exaggeration,
            cfg_weight=self.chatterbox_cfg_weight,
            temperature=self.chatterbox_temperature,
        )
        sr = int(getattr(model, "sr", 24000))
        wav_bytes = self._wav_tensor_to_bytes(wav, sample_rate=sr)
        return self._transcode_wav_bytes(wav_bytes, target=target)

    def synthesize(
        self,
        text: str,
        target: Target,
        voice: str = "default",
        language: str | None = None,
    ) -> TTSOut:
        text = (text or "").strip()
        if not text:
            raise ValueError("Empty text")

        mime = _mime_for_target(target)
        ext = _ext_for_target(target)
        key = self._key(text, target, voice, language=language)

        if self.use_azure:
            logger.info("tts synthesis using azure target=%s language=%s", target, normalize_language(language))
            audio_bytes = self._synthesize_azure(text=text, target=target, language=language)
        else:
            logger.info("tts synthesis using chatterbox target=%s", target)
            audio_bytes = self._synthesize_chatterbox(text=text, target=target)

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


instrument_module_functions(globals(), include_private=True)
