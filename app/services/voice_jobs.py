from sqlalchemy.orm import Session
from app.db.models import VoiceJob

def create_voice_job(
    db: Session,
    source: str,
    user_id: str,
    twilio_media_url: str | None,
    audio_blob_path: str | None,
    twilio_message_sid:str | None
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
    return {
        "id": job.id,
        "source": job.source,
        "user_id": job.user_id,
        "status": job.status,
        "transcript": job.transcript,
        "reply_text": job.reply_text,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }
