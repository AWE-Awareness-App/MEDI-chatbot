import os
from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text

from anthropic import Anthropic
from dotenv import load_dotenv
load_dotenv()

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
models = client.models.list()

for m in models.data:
    print(m.id)



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
    close_conversation,
)

# ---- Claude config (ENV ONLY) ----
USE_LLM = os.getenv("USE_LLM", "true").lower() == "true"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
LLM_MAX_HISTORY = int(os.getenv("LLM_MAX_HISTORY", "12"))


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


def is_reset_cmd(t: str) -> bool:
    return t in {"reset", "restart", "new"}


def is_menu_cmd(t: str) -> bool:
    return t in {"menu", "help"}


def is_menu_selection(t: str) -> bool:
    return t in {"1", "breathing", "2", "sleep", "3", "stress", "stress/anxiety", "anxiety"}


# --------- HISTORY FETCH ---------

def _get_recent_history(db: Session, conversation_id: str, limit: int) -> list[dict]:
    rows = db.execute(
        sql_text("""
            SELECT role, content
            FROM messages
            WHERE conversation_id = :cid
            ORDER BY created_at DESC
            LIMIT :lim
        """),
        {"cid": str(conversation_id), "lim": int(limit)},
    ).fetchall()

    rows = list(reversed(rows))

    history = []
    for role, content in rows:
        if role not in ("user", "assistant"):
            continue
        history.append({
            "role": role,
            "content": content if isinstance(content, str) else str(content)
        })

    return history


# --------- CLAUDE CALL ---------

def _claude_reply(history: list[dict]) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    print("key",ANTHROPIC_API_KEY)

    system_prompt = (
        "You are MEDI, a calm meditation and mental-wellness assistant.\n"
        "- Supportive, non-medical guidance only.\n"
        "- Prefer short, actionable exercises.\n"
        "- If self-harm intent or crisis: encourage contacting local emergency services.\n"
        "Tone: calm, brief, kind."
    )

    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=450,
        temperature=0.4,
        system=system_prompt,
        messages=history,
    )

    print("response",response)

    output = []
    for block in response.content:
        if block.type == "text":
            output.append(block.text)

    return "".join(output).strip()


# --------- RULE BASED ---------

def generate_reply_rule_based(text: str) -> str:
    t = text.strip().lower()

    if is_menu_cmd(t):
        return menu_text()

    if t in {"1", "breathing"}:
        return breathing_script()
    if t in {"2", "sleep"}:
        return sleep_script()
    if t in {"3", "stress", "stress/anxiety", "anxiety"}:
        return stress_script()
    if t in {"hi", "hello", "hey"}:
        return "Hi 🙂 I’m MEDI. Want breathing, sleep, or stress support?"
    return "I’m here. Tell me what you’re feeling right now, in one line."


# --------- MAIN HANDLER ---------

def handle_incoming_message(db: Session, source: str, external_id: str, text: str) -> dict:
    user = get_or_create_user(db, source=source, external_id=external_id)
    convo = get_or_create_active_conversation(db, user_id=user.id)

    t = (text or "").strip().lower()

    save_message(db, convo.id, "user", text)

    # Crisis first
    if check_crisis(text):
        reply = crisis_response()
        save_message(db, convo.id, "assistant", reply)
        return {"conversation_id": convo.id, "reply": reply}

    # Reset
    if is_reset_cmd(t):
        close_conversation(db, convo.id)
        new_convo = get_or_create_active_conversation(db, user_id=user.id)

        reply = "✅ Restarted.\n\n" + menu_text()
        save_message(db, new_convo.id, "assistant", reply)
        return {"conversation_id": new_convo.id, "reply": reply}

    # Menu fast path
    if is_menu_cmd(t) or is_menu_selection(t):
        reply = generate_reply_rule_based(text)
        if check_medical(text):
            reply = medical_disclaimer(reply)

        save_message(db, convo.id, "assistant", reply)
        return {"conversation_id": convo.id, "reply": reply}

    # Free text → Claude
    reply = None
    print("nnn",ANTHROPIC_API_KEY)
    if USE_LLM and ANTHROPIC_API_KEY:
        try:
            history = _get_recent_history(db, convo.id, LLM_MAX_HISTORY)
            reply = _claude_reply(history)
            print("reply",reply)
        except Exception:
            reply = None

    if not reply:
        reply = generate_reply_rule_based(text)

    if check_medical(text):
        reply = medical_disclaimer(reply)

    save_message(db, convo.id, "assistant", reply)

    return {"conversation_id": convo.id, "reply": reply}
