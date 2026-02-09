from sqlalchemy.orm import Session

from services.safety_service import (
    check_crisis,
    check_medical,
    crisis_response,
    medical_disclaimer,
)

from services.chat_repo import (
    get_or_create_user,
    get_or_create_active_conversation,
    save_message,
    close_conversation
)


def breathing_script() -> str:
    return (
        "🫁 *Breathing (1 minute)*\n"
        "Inhale for 4 seconds…\n"
        "Hold for 2 seconds…\n"
        "Exhale for 6 seconds…\n\n"
        "Repeat this 5 times.\n"
        "Type *done* when you’re finished."
    )


def sleep_script() -> str:
    return (
        "😴 *Sleep wind-down*\n"
        "Lie down comfortably.\n"
        "Unclench your jaw and relax your shoulders.\n\n"
        "Inhale slowly… exhale longer.\n"
        "Type *next* if you want a longer routine."
    )


def stress_script() -> str:
    return (
        "🌱 *Grounding (5-4-3-2-1)*\n"
        "5 things you can see\n"
        "4 things you can feel\n"
        "3 things you can hear\n"
        "2 things you can smell\n"
        "1 thing you can taste\n\n"
        "Reply with anything you noticed."
    )

def menu_text() -> str:
    return (
        "I’m MEDI 🌱 Choose one:\n"
        "1) Breathing\n"
        "2) Sleep\n"
        "3) Stress / Anxiety\n\n"
        "Reply 1, 2, or 3 (or type menu anytime)."
    )


def generate_reply(text: str,db :any,user: str,convo:str) -> str:
    # MVP reply logic (we’ll swap with LLM later)
    t = text.strip().lower()
    if t in {"reset", "restart", "new"}:
        # close current convo
        close_conversation(db, convo.id)

        # start fresh convo
        convo = get_or_create_active_conversation(db, user_id=user.id)

        reply = "✅ Restarted.\n\n"
        # save_message(db, convo.id, "assistant", reply)
        return reply


    if t in {"1", "breathing"}:
        return breathing_script()
    if t in {"2", "sleep"}:
        return sleep_script()
    if t in {"3", "stress", "stress/anxiety", "anxiety"}:
        return stress_script()
    if t in {"hi", "hello", "hey"}:
        return "Hi 🙂 I’m MEDI. Want breathing, sleep, or stress support?"
    return "I’m here. Tell me what you’re feeling right now, in one line."

def handle_incoming_message(db: Session, source: str, external_id: str, text: str) -> dict:
    # 1) user + convo
    user = get_or_create_user(db, source=source, external_id=external_id)
    convo = get_or_create_active_conversation(db, user_id=user.id)

    # 2) save user message
    save_message(db, convo.id, "user", text)

    # 3) generate reply
    # SAFETY FIRST
    if check_crisis(text):
        reply = crisis_response()
    else:
        reply = generate_reply(text,db,user,convo)

        # add disclaimer if medical-like
        if check_medical(text):
            reply = medical_disclaimer(reply)
    # 4) save assistant reply
    save_message(db, convo.id, "assistant", reply)

    # 5) return response payload
    return {
        "conversation_id": convo.id,
        "reply": reply,
    }
