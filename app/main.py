# app/main.py

from fastapi.middleware.cors import CORSMiddleware
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.observability import configure_logging, trace_call
from app.db.base import Base
from app.db.schema_patch import ensure_runtime_schema
from app.db.session import engine, get_db

# registers models
from app.db import models  # noqa: F401

from app.routes.schemas import ChatHistoryResponse, ChatRequest, ChatResponse
from app.routes.twilio_webhook import router as twilio_router
from app.services.azure_blob import upload_audio_bytes
from app.services.chat_service import handle_incoming_message
from app.services.history_repo import get_chat_history, get_latest_active_conversation_id
from app.services.voice_jobs import create_voice_job, get_voice_job_public_dict
from app.services.voice_worker import process_voice_job


configure_logging(settings.LOG_LEVEL)

app = FastAPI(title=settings.APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://awedigitalwellness.com", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(twilio_router)


@app.on_event("startup")
@trace_call
def on_startup():
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema(engine)


@app.get("/health")
@trace_call
def health():
    return {"ok": True, "app": settings.APP_NAME, "env": settings.ENV}


@app.post("/chat", response_model=ChatResponse)
@trace_call
def chat(payload: ChatRequest, db: Session = Depends(get_db)):
    result = handle_incoming_message(
        db=db,
        source="web",
        external_id=payload.user_id,
        text=payload.text,
        language_hint=payload.language or "en",
    )
    return result


@app.get("/conversations/{conversation_id}/messages", response_model=ChatHistoryResponse)
@trace_call
def read_chat_history(conversation_id: str, limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    rows = get_chat_history(db, conversation_id=conversation_id, limit=limit, offset=offset)
    return {
        "conversation_id": conversation_id,
        "messages": [{"role": m.role, "content": m.content, "created_at": m.created_at} for m in rows],
    }


@app.get("/users/{user_uuid}/latest-messages", response_model=ChatHistoryResponse)
@trace_call
def read_latest_chat(user_uuid: str, limit: int = 50, db: Session = Depends(get_db)):
    convo_id = get_latest_active_conversation_id(db, user_id=user_uuid)
    if not convo_id:
        raise HTTPException(status_code=404, detail="No active conversation found")
    rows = get_chat_history(db, conversation_id=convo_id, limit=limit, offset=0)
    return {
        "conversation_id": convo_id,
        "messages": [{"role": m.role, "content": m.content, "created_at": m.created_at} for m in rows],
    }


@app.post("/voice/process")
@trace_call
async def web_voice_upload(
    background_tasks: BackgroundTasks,
    user_id: str = Form(...),
    audio: UploadFile = File(...),
    language: str = Form("en"),
    db: Session = Depends(get_db),
):
    if not (audio.content_type or "").startswith("audio/"):
        raise HTTPException(status_code=400, detail="audio file required")

    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty audio")

    blob_path = upload_audio_bytes(
        data=data,
        content_type=audio.content_type or "application/octet-stream",
        filename=audio.filename or "voice",
        prefix="input",
    )

    job_id = create_voice_job(
        db=db,
        source="web",
        user_id=user_id,
        twilio_media_url=None,
        audio_blob_path=blob_path,
        twilio_message_sid=None,
    )

    background_tasks.add_task(
        process_voice_job,
        {"job_id": job_id, "preferred_language": language or "en"},
    )

    return {"job_id": job_id, "status": "queued"}


@app.get("/voice/jobs/{job_id}")
@trace_call
def voice_job_status(job_id: str, db: Session = Depends(get_db)):
    job = get_voice_job_public_dict(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="not found")
    return job

