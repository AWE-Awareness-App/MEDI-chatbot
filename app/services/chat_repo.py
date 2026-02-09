from sqlalchemy.orm import Session
from db.models import User, Conversation, Message
import uuid
from sqlalchemy.orm import Session

def get_or_create_user(db: Session, source: str, external_id: str) -> User:
    user = db.query(User).filter(User.external_id == external_id).first()
    if user:
        return user
    user = User(source=source, external_id=external_id)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def get_or_create_active_conversation(db: Session, user_id: str) -> Conversation:
    convo = (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id, Conversation.status == "active")
        .order_by(Conversation.created_at.desc())
        .first()
    )
    if convo:
        return convo

    convo = Conversation(
        id=str(uuid.uuid4()),
        user_id=user_id,
        status="active",
    )
    db.add(convo)
    db.commit()
    db.refresh(convo)
    return convo

def save_message(db: Session, conversation_id: str, role: str, content: str) -> Message:
    msg = Message(conversation_id=conversation_id, role=role, content=content)
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg

def close_conversation(db: Session, conversation_id: str) -> Conversation | None:
    convo = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not convo:
        return None
    convo.status = "closed"
    db.commit()
    db.refresh(convo)
    return convo
