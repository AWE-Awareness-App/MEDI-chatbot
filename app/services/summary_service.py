from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import Conversation, Message

SUMMARY_EVERY_N_USER_MESSAGES = 6
SUMMARY_MAX_CHARS = 1200
RECENT_MESSAGE_LIMIT = 20


def _get_new_user_message_count(db: Session, conversation_id: str, cutoff: datetime | None) -> int:
    q = db.query(func.count(Message.id)).filter(
        Message.conversation_id == conversation_id,
        Message.role == "user",
    )
    if cutoff:
        q = q.filter(Message.created_at > cutoff)
    return int(q.scalar() or 0)


def _get_messages_since(db: Session, conversation_id: str, cutoff: datetime | None) -> list[Message]:
    q = db.query(Message).filter(Message.conversation_id == conversation_id)
    if cutoff:
        q = q.filter(Message.created_at > cutoff)
    return q.order_by(Message.created_at.asc()).limit(RECENT_MESSAGE_LIMIT).all()


def build_summary(existing_summary: str | None, new_messages: list[Message]) -> str:
    """
    Placeholder non-LLM summary builder.
    Appends a compact list of recent user messages.
    """
    existing = (existing_summary or "").strip()
    user_lines = [m.content.strip() for m in new_messages if m.role == "user" and m.content]
    if not user_lines:
        return existing

    addition = "; ".join(user_lines[-6:])
    if existing:
        combined = f"{existing}\nRecent: {addition}"
    else:
        combined = f"Recent: {addition}"

    if len(combined) > SUMMARY_MAX_CHARS:
        combined = combined[-SUMMARY_MAX_CHARS:]
    return combined


def maybe_update_summary(db: Session, conversation_id: str) -> bool:
    convo = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not convo:
        return False

    cutoff = convo.summary_updated_at
    new_user_count = _get_new_user_message_count(db, conversation_id, cutoff)
    if new_user_count < SUMMARY_EVERY_N_USER_MESSAGES:
        return False

    new_messages = _get_messages_since(db, conversation_id, cutoff)
    convo.summary = build_summary(convo.summary, new_messages)
    convo.summary_updated_at = datetime.utcnow()
    db.commit()
    db.refresh(convo)
    return True


def get_summary_and_recent_messages(db: Session, conversation_id: str, last_n: int = 12) -> tuple[str, list[Message]]:
    convo = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    summary = (convo.summary if convo and convo.summary else "")

    recent = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(last_n)
        .all()
    )
    return summary, recent
