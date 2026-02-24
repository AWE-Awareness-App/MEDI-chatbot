from sqlalchemy.orm import Session
from app.db.models import Message, Conversation

def get_chat_history(db: Session, conversation_id: str, limit: int = 50, offset: int = 0):
    """
    Returns messages for a conversation ordered oldest -> newest.
    """
    q = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .offset(offset)
        .limit(limit)
    )
    return q.all()

def get_latest_active_conversation_id(db: Session, user_id: str):
    convo = (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id, Conversation.status == "active")
        .order_by(Conversation.created_at.desc())
        .first()
    )
    return convo.id if convo else None
