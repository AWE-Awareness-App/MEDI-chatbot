# app/main.py

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from core.config import settings

# DB: Base + engine + session dependency
from db.base import Base
from db.session import engine, get_db

# IMPORTANT: this import registers your models with SQLAlchemy
# (so Base.metadata.create_all can actually create tables)
from db import models  # noqa: F401

from services.chat_service import handle_incoming_message
from services.history_repo import get_chat_history, get_latest_active_conversation_id

from routes.schemas import ChatRequest, ChatResponse, ChatHistoryResponse
from routes.schemas import ChatHistoryResponse, MessageOut
from routes.twilio_webhook import router as twilio_router



# Repo helpers
from services.chat_repo import (
    get_or_create_user,
    get_or_create_active_conversation,
    save_message,
)

app = FastAPI(title=settings.APP_NAME)
app.include_router(twilio_router)


@app.on_event("startup")
def on_startup():
    # Create tables if they don't exist (OK for MVP; later use Alembic migrations)
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health():
    return {"ok": True, "app": settings.APP_NAME, "env": settings.ENV}


# One-time helper: create tables manually (useful while debugging)
@app.post("/debug/create-tables")
def create_tables():
    Base.metadata.create_all(bind=engine)
    return {"ok": True, "message": "tables created (if they did not already exist)"}


@app.post("/debug/seed")
def debug_seed(db: Session = Depends(get_db)):
    user = get_or_create_user(db, source="whatsapp", external_id="whatsapp:+10000000000")
    convo = get_or_create_active_conversation(db, user_id=user.id)

    save_message(db, convo.id, "user", "hello medi")
    save_message(db, convo.id, "assistant", "hello! how can I help?")

    return {"user_id": user.id, "conversation_id": convo.id}

@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, db: Session = Depends(get_db)):
    result = handle_incoming_message(
        db=db,
        source="web",               # for now, web/local testing
        external_id=payload.user_id,
        text=payload.text,
    )
    return result


@app.get("/conversations/{conversation_id}/messages", response_model=ChatHistoryResponse)
def read_chat_history(conversation_id: str, limit: int = 50, offset: int = 0, db: Session = Depends(get_db)):
    rows = get_chat_history(db, conversation_id=conversation_id, limit=limit, offset=offset)

    return {
        "conversation_id": conversation_id,
        "messages": [
            {"role": m.role, "content": m.content, "created_at": m.created_at}
            for m in rows
        ],
    }


@app.get("/users/{user_uuid}/latest-messages", response_model=ChatHistoryResponse)
def read_latest_chat(user_uuid: str, limit: int = 50, db: Session = Depends(get_db)):
    convo_id = get_latest_active_conversation_id(db, user_id=user_uuid)
    if not convo_id:
        raise HTTPException(status_code=404, detail="No active conversation found")

    rows = get_chat_history(db, conversation_id=convo_id, limit=limit, offset=0)
    return {
        "conversation_id": convo_id,
        "messages": [{"role": m.role, "content": m.content, "created_at": m.created_at} for m in rows],
    }
