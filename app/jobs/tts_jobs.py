from app.services.tts_piper import PiperTTS
from app.services.twilio_sender import send_whatsapp_audio

def whatsapp_tts_reply_job(to_e164: str, text: str) -> dict:
    tts = PiperTTS()
    out = tts.synthesize(text=text, target="whatsapp")
    sid = send_whatsapp_audio(to_e164=to_e164, ogg_url=out.public_url)
    return {"twilio_sid": sid, "audio_url": out.public_url}

def web_tts_job(text: str) -> dict:
    tts = PiperTTS()
    out = tts.synthesize(text=text, target="web")
    return {"audio_url": out.public_url, "mime_type": out.mime_type}