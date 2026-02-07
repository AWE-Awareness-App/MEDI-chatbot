from sqlalchemy.orm import Session

from services.chat_repo import (
    get_or_create_user,
    get_or_create_active_conversation,
    save_message,
)

def generate_reply(text: str) -> str:
    # MVP reply logic (we’ll swap with LLM later)
    t = text.strip().lower()

    if t in {"hi", "hello", "hey"}:
        return "Hi 🙂 I’m MEDI. Want breathing, sleep, or stress support?"
    if "breath" in t or "breathing" in t:
        return "Let’s do a quick breathing round: inhale 4… hold 2… exhale 6. Repeat 5 times."
    if "sleep" in t:
        return "Okay. Lie down comfortably. Inhale slowly… exhale longer… relax your jaw and shoulders."
    if "stress" in t or "anxiety" in t:
        return "Try 5-4-3-2-1 grounding: 5 things you see, 4 you feel, 3 you hear, 2 you smell, 1 you taste."
    if t == "help":
        return "Reply with: 1) Breathing  2) Sleep  3) Stress"

    return "I’m here. Tell me what you’re feeling right now, in one line."

def handle_incoming_message(db: Session, source: str, external_id: str, text: str) -> dict:
    # 1) user + convo
    user = get_or_create_user(db, source=source, external_id=external_id)
    convo = get_or_create_active_conversation(db, user_id=user.id)

    # 2) save user message
    save_message(db, convo.id, "user", text)

    # 3) generate reply
    reply = generate_reply(text)

    # 4) save assistant reply
    save_message(db, convo.id, "assistant", reply)

    # 5) return response payload
    return {
        "conversation_id": convo.id,
        "reply": reply,
    }
