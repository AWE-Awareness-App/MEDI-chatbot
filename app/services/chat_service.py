from __future__ import annotations

import logging
import re
import time

from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text

from anthropic import Anthropic

from app.core.config import settings
from app.services.safety_service import (
    check_crisis,
    check_medical,
    crisis_response,
    medical_disclaimer,
)
from app.services.chat_repo import (
    get_or_create_user,
    get_or_create_active_conversation,
    save_message,
    close_conversation,
)
from app.services.summary_service import maybe_update_summary
from app.services.rag_service import retrieve_chunks

logger = logging.getLogger(__name__)

# ---- Claude config (ENV ONLY) ----
USE_LLM = settings.USE_LLM
ANTHROPIC_API_KEY = settings.ANTHROPIC_API_KEY
ANTHROPIC_MODEL = settings.ANTHROPIC_MODEL or "claude-sonnet-4-20250514"
LLM_MAX_HISTORY = int(settings.LLM_MAX_HISTORY or "12")

# Optional debug switch (if you add DEBUG_RAG to settings/env)
DEBUG_RAG = str(getattr(settings, "DEBUG_RAG", "false")).lower() == "true"


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


# --------- HISTORY + SUMMARY FETCH ---------

def _get_recent_history(db: Session, conversation_id: str, limit: int) -> list[dict]:
    rows = db.execute(
        sql_text(
            """
            SELECT role, content
            FROM messages
            WHERE conversation_id = :cid
            ORDER BY created_at DESC
            LIMIT :lim
            """
        ),
        {"cid": str(conversation_id), "lim": int(limit)},
    ).fetchall()

    rows = list(reversed(rows))

    history: list[dict] = []
    for role, content in rows:
        if role not in ("user", "assistant"):
            continue
        history.append({"role": role, "content": content if isinstance(content, str) else str(content)})

    return history


def _get_conversation_summary(db: Session, conversation_id: str) -> str:
    """
    Fetch the latest running summary for the conversation.

    NOTE: This assumes your conversations table has a `summary` column.
    If your schema uses a different column/table, update the SQL here.
    """
    row = db.execute(
        sql_text(
            """
            SELECT summary
            FROM conversations
            WHERE id = :cid
            """
        ),
        {"cid": str(conversation_id)},
    ).fetchone()

    if not row:
        return ""
    summary = row[0]
    return summary if isinstance(summary, str) else (str(summary) if summary is not None else "")


# --------- RAG HELPERS (Citation Mode) ---------

def _format_retrieved(chunks: list[dict]) -> tuple[str, list[str]]:
    """
    Format retrieved chunks for injection into Claude with stable citation ids.

    Returns:
      (retrieved_text, valid_ids)

    Each chunk becomes:
      [K1] topic=... source=... score=...
      <content>
    """
    if not chunks:
        return "(none)", []

    parts: list[str] = []
    valid_ids: list[str] = []

    for i, c in enumerate(chunks, start=1):
        cid = f"K{i}"
        valid_ids.append(cid)

        topic = c.get("topic")
        source = c.get("source")
        score = c.get("score", c.get("similarity", 0.0))
        content = c.get("content", "")

        # Keep prompt size reasonable
        if isinstance(content, str) and len(content) > 1200:
            content = content[:1200].rstrip() + "…"

        try:
            score_f = float(score)
        except Exception:
            score_f = 0.0

        parts.append(
            f"[{cid}] topic={topic} source={source} score={score_f:.4f}\n"
            f"{content}"
        )

    return "\n\n".join(parts), valid_ids


def _detect_used_kb(answer: str) -> bool:
    return bool(re.search(r"\[K\d+\]", answer or ""))


def _extract_citation_ids(answer: str) -> list[str]:
    return re.findall(r"\[(K\d+)\]", answer or "")


# --------- CLAUDE CALL ---------

def _claude_reply(history: list[dict], *, retrieved_text: str = "", summary_text: str = "", valid_ids: list[str] | None = None) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    summary_block = ""
    if summary_text.strip():
        summary_block = "=== Conversation Summary ===\n" + summary_text.strip() + "\n\n"

    system_prompt = (
        "You are MEDI, a calm meditation and mental-wellness assistant.\n"
        "- Supportive, non-medical guidance only.\n"
        "- Prefer short, actionable suggestions.\n"
        "- Do not provide medical diagnosis or treatment.\n"
        "- If self-harm intent or crisis: encourage contacting local emergency services.\n"
        "Tone: calm, brief, kind.\n\n"
        "=== Citation Rules ===\n"
        "- If you use any information from 'Retrieved Knowledge', you MUST cite it using its bracket id (e.g., [K1], [K2]).\n"
        "- Place citations at the end of the sentence that uses the knowledge.\n"
        "- If you did NOT use Retrieved Knowledge, do NOT include any [K#] citations.\n"
        "- Do not invent citations. Only cite ids that appear in Retrieved Knowledge.\n\n"
        "=== Grounding Priority ===\n"
        "- If Retrieved Knowledge is relevant, prioritize it over your general knowledge.\n"
        "- If it's not relevant, answer normally without citations.\n\n"
        f"{summary_block}"
        "=== Retrieved Knowledge (use when relevant; do not invent sources) ===\n"
        f"{retrieved_text or '(none)'}"
    )

    t0 = time.time()
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=450,
        temperature=0.4,
        system=system_prompt,
        messages=history,
    )
    latency_s = time.time() - t0

    # Observability (latency + token usage if present)
    usage = getattr(response, "usage", None)
    if usage is not None:
        logger.info(
            "Claude latency=%.2fs input_tokens=%s output_tokens=%s",
            latency_s,
            getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None),
        )
    else:
        logger.info("Claude latency=%.2fs", latency_s)

    output: list[str] = []
    for block in response.content:
        if block.type == "text":
            output.append(block.text)

    answer = "".join(output).strip()

    # Validate citations: warn if Claude invented ids
    if valid_ids:
        found = set(_extract_citation_ids(answer))
        invalid = sorted(found - set(valid_ids))
        if invalid:
            logger.warning("Invalid citations found (not in retrieved knowledge): %s", invalid)

    return answer


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

    incoming = (text or "").strip()
    t = incoming.lower()

    save_message(db, convo.id, "user", incoming)

    # Crisis first
    if check_crisis(incoming):
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
        reply = generate_reply_rule_based(incoming)
        if check_medical(incoming):
            reply = medical_disclaimer(reply)

        save_message(db, convo.id, "assistant", reply)
        return {"conversation_id": convo.id, "reply": reply}

    # Free text → Claude (with RAG + forced citations)
    reply: str | None = None
    used_kb = False
    citations: list[str] = []
    rag_meta: dict = {}

    if USE_LLM and ANTHROPIC_API_KEY:
        try:
            # 1) recent chat history
            history = _get_recent_history(db, convo.id, LLM_MAX_HISTORY)

            # 2) conversation summary (long-term memory)
            summary_text = _get_conversation_summary(db, convo.id)

            # 3) retrieve top-k knowledge chunks
            chunks = retrieve_chunks(db, incoming, k=5)
            retrieved_text, valid_ids = _format_retrieved(chunks)

            if DEBUG_RAG:
                rag_meta = {
                    "retrieved_count": len(chunks),
                    "valid_ids": valid_ids,
                    "preview": retrieved_text[:800] + ("…" if len(retrieved_text) > 800 else ""),
                }

            # 4) call Claude with summary + retrieved grounding
            reply = _claude_reply(
                history,
                retrieved_text=retrieved_text,
                summary_text=summary_text,
                valid_ids=valid_ids,
            )

            used_kb = _detect_used_kb(reply)
            citations = _extract_citation_ids(reply)

            logger.info("RAG used_kb=%s citations=%s", used_kb, citations)
        except Exception:
            logger.exception("Claude/RAG call failed")
            reply = None

    if not reply:
        reply = generate_reply_rule_based(incoming)

    if check_medical(incoming):
        reply = medical_disclaimer(reply)

    save_message(db, convo.id, "assistant", reply)

    # update running summary every N user messages
    maybe_update_summary(db, convo.id)

    response = {"conversation_id": convo.id, "reply": reply}

    # Optional: surface grounding info when debugging
    if DEBUG_RAG:
        response["used_kb"] = used_kb
        response["citations"] = citations
        response["rag"] = rag_meta

    return response
