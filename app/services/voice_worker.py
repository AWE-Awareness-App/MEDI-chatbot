import logging

from sqlalchemy.orm import Session

from app.core.observability import trace_call
from app.db.models import VoiceJob
from app.db.session import SessionLocal
from app.services.azure_blob import delete_audio, download_audio_bytes
from app.services.chat_service import handle_incoming_message
from app.services.language_service import resolve_language
from app.services.stt_service import download_twilio_media, transcribe_audio_bytes_with_language
from app.services.tts_piper import PiperTTS
from app.services.tts_text import format_for_tts
from app.services.twilio_sender import (
    send_whatsapp_audio,
    send_whatsapp_text,
    send_whatsapp_typing_indicator,
)
from app.services.voice_jobs import mark_done, mark_failed, mark_processing


logger = logging.getLogger(__name__)


def _voice_transcription_failure_text(language: str | None) -> str:
    if resolve_language(None, language_hint=language, default="en") == "fr":
        return "Je n'ai pas bien entendu. Reessayez un peu plus pres du micro."
    return "I couldn't hear anything clearly. Try again a bit closer to the mic."


@trace_call
def _try_store_web_audio_fields(job: VoiceJob, audio_url: str, mime: str, storage_path: str | None = None) -> None:
    """
    Store audio info in VoiceJob if your model has fields for it.
    This is safe: it won't crash if fields don't exist yet.
    """
    if hasattr(job, "reply_audio_url"):
        setattr(job, "reply_audio_url", audio_url)
    if hasattr(job, "reply_audio_mime"):
        setattr(job, "reply_audio_mime", mime)
    if storage_path and hasattr(job, "reply_audio_path"):
        setattr(job, "reply_audio_path", storage_path)


@trace_call
def process_voice_job(payload: dict) -> None:
    """
    Background worker entrypoint.
    payload: { "job_id": "<id>", "preferred_language": "en|fr|ja|ar" (optional) }
    """
    job_id = payload["job_id"]
    preferred_language = payload.get("preferred_language")
    db: Session = SessionLocal()
    logger.info("voice job start job_id=%s preferred_language=%s", job_id, preferred_language)

    try:
        job = db.query(VoiceJob).filter(VoiceJob.id == job_id).one_or_none()
        if not job:
            logger.warning("voice job not found job_id=%s", job_id)
            return

        if job.source == "whatsapp" and job.twilio_message_sid:
            try:
                send_whatsapp_typing_indicator(job.twilio_message_sid)
            except Exception:
                logger.exception("failed to send typing indicator sid=%s", job.twilio_message_sid)

        mark_processing(db, job_id)

        try:
            if job.source == "whatsapp":
                audio_bytes = download_twilio_media(job.twilio_media_url)
            elif job.source == "web":
                audio_bytes = download_audio_bytes(job.audio_blob_path)
            else:
                raise RuntimeError(f"Unknown voice job source: {job.source}")

            transcript, detected_language = transcribe_audio_bytes_with_language(audio_bytes)
            transcript_language = resolve_language(
                transcript,
                language_hint=preferred_language,
                default=detected_language or "en",
            )
            reply_language = transcript_language
            logger.info(
                "voice job transcribed job_id=%s transcript_chars=%s detected_language=%s transcript_language=%s",
                job_id,
                len(transcript or ""),
                detected_language,
                transcript_language,
            )

            # Optional cleanup for uploaded source audio blob.
            delete_audio(job.audio_blob_path)

            if not transcript:
                reply = _voice_transcription_failure_text(reply_language)
            else:
                result = handle_incoming_message(
                    db=db,
                    source=job.source,
                    external_id=job.user_id,
                    text=transcript,
                    language_hint=preferred_language,
                )
                reply = result["reply"]
                reply_language = result.get("language", reply_language)
            tts_ready = False

            try:
                tts = PiperTTS()

                if job.source == "whatsapp":
                    tts_text = format_for_tts(reply)
                    out = tts.synthesize(
                        text=tts_text,
                        target="whatsapp",
                        language=reply_language,
                    )
                    send_whatsapp_audio(to_e164=job.user_id, ogg_url=out.public_url)
                    logger.info(
                        "voice job whatsapp audio sent job_id=%s storage_path=%s",
                        job_id,
                        out.storage_path,
                    )
                    tts_ready = True

                elif job.source == "web":
                    tts_text = format_for_tts(reply)
                    out = tts.synthesize(
                        text=tts_text,
                        target="web",
                        language=reply_language,
                    )
                    _try_store_web_audio_fields(
                        job,
                        audio_url=out.public_url,
                        mime=out.mime_type,
                        storage_path=out.storage_path,
                    )
                    logger.info(
                        "voice job web audio ready job_id=%s storage_path=%s",
                        job_id,
                        out.storage_path,
                    )
                    tts_ready = True

            except Exception as exc:
                logger.exception("TTS/audio delivery failed job_id=%s", job_id)
                if job.source == "web":
                    mark_failed(db, job_id, error=f"TTS failed: {exc}")
                    return
                if job.source == "whatsapp":
                    send_whatsapp_text(to_number=job.user_id, body=reply)
                    tts_ready = True

            if job.source == "web" and not tts_ready:
                mark_failed(db, job_id, error="TTS did not complete")
                return

            mark_done(db, job_id, transcript=transcript, reply_text=reply)

        except Exception as exc:
            logger.exception("voice job processing failed job_id=%s error=%s", job_id, exc)
            mark_failed(db, job_id, error=str(exc))
            if job.source == "whatsapp":
                send_whatsapp_text(
                    to_number=job.user_id,
                    body="Sorry - I couldn't process that voice note. Please try again.",
                )

    finally:
        db.close()
        logger.info("voice job end job_id=%s", job_id)
