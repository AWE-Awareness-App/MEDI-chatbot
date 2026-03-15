import logging

from sqlalchemy.orm import Session

from app.core.observability import instrument_module_functions
from app.db.models import VoiceJob
from app.services.azure_blob import build_blob_read_url


logger = logging.getLogger(__name__)

def create_voice_job(
    db: Session,
    source: str,
    user_id: str,
    twilio_media_url: str | None,
    audio_blob_path: str | None,
    twilio_message_sid: str | None = None,
) -> str:
    job = VoiceJob(
        source=source,
        user_id=user_id,
        twilio_media_url=twilio_media_url,
        audio_blob_path=audio_blob_path,
        twilio_message_sid=twilio_message_sid,
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job.id

def mark_processing(db: Session, job_id: str) -> None:
    job = db.query(VoiceJob).filter(VoiceJob.id == job_id).one_or_none()
    if not job:
        return
    job.status = "processing"
    db.commit()

def mark_done(db: Session, job_id: str, transcript: str, reply_text: str) -> None:
    job = db.query(VoiceJob).filter(VoiceJob.id == job_id).one_or_none()
    if not job:
        return
    job.status = "done"
    job.transcript = transcript
    job.reply_text = reply_text
    job.error = None
    db.commit()

def mark_failed(db: Session, job_id: str, error: str) -> None:
    job = db.query(VoiceJob).filter(VoiceJob.id == job_id).one_or_none()
    if not job:
        return
    job.status = "failed"
    job.error = (error or "")[:8000]
    db.commit()

def get_voice_job_public_dict(db: Session, job_id: str) -> dict | None:
    job = db.query(VoiceJob).filter(VoiceJob.id == job_id).one_or_none()
    if not job:
        return None
    reply_audio_url = getattr(job, "reply_audio_url", None)
    reply_audio_path = getattr(job, "reply_audio_path", None)
    reply_audio_mime = getattr(job, "reply_audio_mime", None)

    # Prefer a fresh SAS URL from stored blob path if available.
    if reply_audio_path:
        try:
            reply_audio_url = build_blob_read_url(reply_audio_path)
        except Exception as exc:
            logger.warning("voice job audio url generation failed job_id=%s error=%s", job_id, exc)

    return {
        "id": job.id,
        "source": job.source,
        "user_id": job.user_id,
        "status": job.status,
        "transcript": job.transcript,
        "reply_text": job.reply_text,
        "reply_audio_url": reply_audio_url,
        "reply_audio_mime": reply_audio_mime,
        "reply_audio_path": reply_audio_path,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


instrument_module_functions(globals(), include_private=False)
