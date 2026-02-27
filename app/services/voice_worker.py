from sqlalchemy.orm import Session
from app.db.session import SessionLocal

from app.services.voice_jobs import mark_processing, mark_done, mark_failed
from app.services.stt_service import transcribe_audio_bytes, download_twilio_media
from app.services.azure_blob import download_audio_bytes
from app.services.twilio_sender import send_whatsapp_text
from app.services.chat_service import handle_incoming_message
from app.db.models import VoiceJob
from app.services.azure_blob import download_audio_bytes, delete_audio

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

            # 4) persist
            mark_done(db, job_id, transcript=transcript, reply_text=reply)

            # 5) WhatsApp sends message back (web reads via polling)
            if job.source == "whatsapp":
                send_whatsapp_text(to_number=job.user_id, body=reply)

        except Exception as e:
            print("ERRor",e)
            mark_failed(db, job_id, error=str(e))
            if job.source == "whatsapp":
                send_whatsapp_text(to_number=job.user_id, body="Sorry — I couldn’t process that voice note. Please try again.")

    finally:
        db.close()
