from __future__ import annotations
from typing import List, Dict

from sqlalchemy import select
from core.config import settings
from db.models import Message  # <-- change to your actual import

MAX_HISTORY = int(settings.LLM_MAX_HISTORY or "12")

def _role_ok(role: str) -> bool:
    return role in {"user", "assistant"}

async def build_recent_history(db, conversation_id) -> List[Dict[str, str]]:
    """
    Returns chronological last N messages:
      [{"role":"user","content":"..."}, {"role":"assistant","content":"..."}]
    """
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(MAX_HISTORY)
    )
    rows = (await db.execute(stmt)).scalars().all()
    rows = list(reversed(rows))

    history: List[Dict[str, str]] = []
    for m in rows:
        if not _role_ok(m.role):
            continue
        content = m.content if isinstance(m.content, str) else str(m.content)
        history.append({"role": m.role, "content": content})
    return history
