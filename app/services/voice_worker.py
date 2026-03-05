from sqlalchemy.orm import Session
from app.db.session import SessionLocal

from app.services.voice_jobs import mark_processing, mark_done, mark_failed
from app.services.stt_service import transcribe_audio_bytes, download_twilio_media
from app.services.azure_blob import download_audio_bytes, delete_audio
from app.services.twilio_sender import send_whatsapp_text

from app.services.chat_service import handle_incoming_message
from app.db.models import VoiceJob

# NEW: TTS + WhatsApp audio sender
from app.services.tts_piper import PiperTTS
from app.services.twilio_sender import send_whatsapp_audio  # you will add this in twilio_sender.py
from app.services.tts_text import format_for_tts
from app.services.twilio_sender import send_whatsapp_typing_indicator



def _try_store_web_audio_fields(job: VoiceJob, audio_url: str, mime: str) -> None:
    """
    Store audio info in VoiceJob if your model has fields for it.
    This is safe: it won't crash if fields don't exist yet.
    """
    if hasattr(job, "reply_audio_url"):
        setattr(job, "reply_audio_url", audio_url)
    if hasattr(job, "reply_audio_mime"):
        setattr(job, "reply_audio_mime", mime)


def process_voice_job(payload: dict) -> None:
    """
    RQ worker entrypoint.
    payload: { "job_id": "<id>" }
    """
    job_id = payload["job_id"]
    db: Session = SessionLocal()

    try:
        job = db.query(VoiceJob).filter(VoiceJob.id == job_id).one_or_none()
        if not job:
            return

        if job.source == "whatsapp" and job.twilio_message_sid:
            try:
                send_whatsapp_typing_indicator(job.twilio_message_sid)
            except Exception:
                pass

        mark_processing(db, job_id)

        try:
            # 1) load audio bytes depending on source
            if job.source == "whatsapp":
                audio_bytes = download_twilio_media(job.twilio_media_url)
            elif job.source == "web":
                audio_bytes = download_audio_bytes(job.audio_blob_path)
            else:
                raise RuntimeError(f"Unknown voice job source: {job.source}")

            # 2) transcribe
            transcript = transcribe_audio_bytes(audio_bytes)
            print("heeeeere 4")


            # optional cleanup (recommended in local mode)
            delete_audio(job.audio_blob_path)

            if not transcript:
                reply = "I couldn’t hear anything clearly. Try again a bit closer to the mic."
            else:
                # 3) run your existing MEDI pipeline using transcript as text
                result = handle_incoming_message(
                    db=db,
                    source=job.source,
                    external_id=job.user_id,
                    text=transcript,
                )
                reply = result["reply"]

            # 4) persist text results (same as you do now)
            mark_done(db, job_id, transcript=transcript, reply_text=reply)

            # 5) NEW: Generate audio reply + deliver
            #    - WhatsApp: OGG/Opus and send via Twilio media_url
            #    - Web: MP3 and store URL so polling endpoint can return it
            try:
                tts = PiperTTS()

                if job.source == "whatsapp":
                    print("heeeeere 5")
                    # Generate OGG/Opus
                    tts_text = format_for_tts(reply)
                    out = tts.synthesize(text=tts_text, target="whatsapp")  # out.public_url -> /media/tts/xxx.ogg

                    # Send voice note/audio (Twilio fetches this URL)
                    # send_whatsapp_audio(to_number=job.user_id, media_url=out.public_url)
                    send_whatsapp_audio(to_e164=job.user_id, ogg_url=out.public_url)

                    # Optional: also send text (if you want both)
                    # send_whatsapp_text(to_number=job.user_id, body=reply)

                elif job.source == "web":
                    # Generate MP3
                    tts_text = format_for_tts(reply)
                    out = tts.synthesize(text=tts_text, target="web")  # out.public_url -> /media/tts/xxx.mp3

                    # Store audio url in DB if you have fields (recommended)
                    _try_store_web_audio_fields(job, audio_url=out.public_url, mime="audio/mpeg")
                    db.commit()

            except Exception as e:
                # If audio fails, fallback:
                print("TTS/audio delivery failed:", e)

                # WhatsApp fallback: send text so user still gets something
                if job.source == "whatsapp":
                    send_whatsapp_text(to_number=job.user_id, body=reply)

        except Exception as e:
            print("ERRor", e)
            mark_failed(db, job_id, error=str(e))
            if job.source == "whatsapp":
                send_whatsapp_text(
                    to_number=job.user_id,
                    body="Sorry — I couldn’t process that voice note. Please try again."
                )

    finally:
        db.close()
