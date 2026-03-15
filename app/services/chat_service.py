from __future__ import annotations

import logging
import re
import time
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text

from anthropic import Anthropic

from app.core.config import settings
from app.core.observability import instrument_module_functions
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
from app.services.severity_service import score_severity
from app.services.language_service import language_name, resolve_language


logger = logging.getLogger(__name__)

# ---- Claude config (ENV ONLY) ----
USE_LLM = settings.USE_LLM
ANTHROPIC_API_KEY = settings.ANTHROPIC_API_KEY
ANTHROPIC_MODEL = settings.ANTHROPIC_MODEL or "claude-sonnet-4-20250514"
LLM_MAX_HISTORY = int(settings.LLM_MAX_HISTORY or "12")

# Optional debug switch (if you add DEBUG_RAG to settings/env)
DEBUG_RAG = str(getattr(settings, "DEBUG_RAG", "false")).lower() == "true"

# Day 10 knobs (env optional)
RAG_TOP_K = int(getattr(settings, "RAG_TOP_K", 5) or 5)
RAG_SKIP_SHORT_CHARS = int(getattr(settings, "RAG_SKIP_SHORT_CHARS", 15) or 15)
CONF_ENFORCE_CITATIONS = float(getattr(settings, "CONF_ENFORCE_CITATIONS", 0.55) or 0.55)


# -------------------------
# Day 10: Topic detection
# -------------------------
TOPIC_KEYWORDS = {
    "sleep": ["sleep", "insomnia", "awake", "night", "bed", "restless"],
    "breathing": ["breath", "breathing", "inhale", "exhale", "hyperventilate", "rsa", "hrv"],
    "polyvagal": ["polyvagal", "vagus", "vagal", "neuroception"],
    "anxiety": ["anxiety", "anxious", "panic", "worry", "overthinking", "nervous"],
    "stress": ["stress", "overwhelmed", "pressure", "burnout"],
    "depression": ["depressed", "depression", "hopeless", "low mood"],
    "cancer": ["cancer", "chemo", "tumor", "oncology", "lung cancer"],
    "infertility": ["infertility", "ivf", "fertility"],
    "youth": ["teen", "teenager", "school", "child", "adolescent"],
}

def detect_topic(text: str) -> Optional[str]:
    t = (text or "").lower()
    for topic, words in TOPIC_KEYWORDS.items():
        if any(w in t for w in words):
            return topic
    return None


# -------------------------
# Confidence scoring
# -------------------------
def rag_confidence_from_scores(scores: list[float]) -> float:
    """
    Your rag_service returns score = (embedding <-> qvec) distance. Smaller = better.
    Map average distance to 0..1 confidence (heuristic; tune after you log real values).
    """
    if not scores:
        return 0.0
    avg = sum(scores) / len(scores)

    # Heuristic mapping (works decently for many pgvector setups; tune by observing logs)
    if avg <= 0.25:
        return 0.90
    if avg <= 0.35:
        return 0.75
    if avg <= 0.50:
        return 0.55
    if avg <= 0.70:
        return 0.35
    return 0.15


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


_RULE_BASED_I18N = {
    "en": {
        "menu": "I am MEDI. Choose one:\n1) Breathing\n2) Sleep\n3) Stress / Anxiety\n\nReply 1, 2, or 3 (or type menu anytime).",
        "breathing": "Breathing (1 minute)\nInhale for 4 seconds.\nHold for 2 seconds.\nExhale for 6 seconds.\n\nRepeat this 5 times.\nType 'done' when you are finished.",
        "sleep": "Sleep wind-down\nLie down comfortably.\nRelax your jaw and shoulders.\n\nInhale slowly, exhale longer.\nType 'next' for a longer routine.",
        "stress": "Grounding (5-4-3-2-1)\n5 things you can see\n4 things you can feel\n3 things you can hear\n2 things you can smell\n1 thing you can taste\n\nReply with anything you noticed.",
        "hello": "Hi, I am MEDI. Want breathing, sleep, or stress support?",
        "fallback": "I am here. Tell me what you are feeling right now, in one line.",
        "reset": "Restarted.",
    },
    "fr": {
        "menu": "Je suis MEDI. Choisissez:\n1) Respiration\n2) Sommeil\n3) Stress / Anxiete\n\nRepondez 1, 2, ou 3 (ou tapez menu).",
        "breathing": "Respiration (1 minute)\nInspirez pendant 4 secondes.\nRetenez pendant 2 secondes.\nExpirez pendant 6 secondes.\n\nRepetez 5 fois.\nEcrivez 'done' quand vous avez fini.",
        "sleep": "Routine sommeil\nAllongez-vous confortablement.\nDetendez la machoire et les epaules.\n\nInspirez lentement, expirez plus longtemps.\nEcrivez 'next' pour une routine plus longue.",
        "stress": "Ancrage (5-4-3-2-1)\n5 choses que vous voyez\n4 choses que vous ressentez\n3 choses que vous entendez\n2 choses que vous sentez\n1 chose que vous goutez\n\nRepondez avec ce que vous avez remarque.",
        "hello": "Bonjour, je suis MEDI. Respiration, sommeil ou stress ?",
        "fallback": "Je suis la. Dites-moi ce que vous ressentez, en une ligne.",
        "reset": "Conversation redemarree.",
    },
    "ja": {
        "menu": "MEDI\\u3067\\u3059\\u3002\\u9078\\u3093\\u3067\\u304f\\u3060\\u3055\\u3044:\\n1) \\u547c\\u5438\\n2) \\u7761\\u7720\\n3) \\u30b9\\u30c8\\u30ec\\u30b9 / \\u4e0d\\u5b89\\n\\n1\\u30012\\u30013\\u3067\\u8fd4\\u4fe1\\u3057\\u3066\\u304f\\u3060\\u3055\\u3044\\u3002",
        "breathing": "\\u547c\\u5438 (1\\u5206)\\n4\\u79d2\\u5438\\u3044\\u307e\\u3059\\u3002\\n2\\u79d2\\u6b62\\u3081\\u307e\\u3059\\u3002\\n6\\u79d2\\u5410\\u304d\\u307e\\u3059\\u3002\\n\\n\\u3053\\u308c\\u30925\\u56de\\u7e70\\u308a\\u8fd4\\u3057\\u307e\\u3059\\u3002\\n\\u7d42\\u308f\\u3063\\u305f\\u3089 'done' \\u3068\\u5165\\u529b\\u3057\\u3066\\u304f\\u3060\\u3055\\u3044\\u3002",
        "sleep": "\\u7720\\u7720\\u30eb\\u30fc\\u30c6\\u30a3\\u30f3\\n\\u697d\\u306a\\u59ff\\u52e2\\u3067\\u6a2a\\u306b\\u306a\\u3063\\u3066\\u304f\\u3060\\u3055\\u3044\\u3002\\n\\u3042\\u3054\\u3068\\u80a9\\u306e\\u529b\\u3092\\u629c\\u304d\\u307e\\u3057\\u3087\\u3046\\u3002\\n\\n\\u3086\\u3063\\u304f\\u308a\\u5438\\u3063\\u3066\\u3001\\u9577\\u304f\\u5410\\u304d\\u307e\\u3059\\u3002\\n\\u3082\\u3063\\u3068\\u9577\\u3044\\u30eb\\u30fc\\u30c6\\u30a3\\u30f3\\u306f 'next'\\u3002",
        "stress": "\\u30b0\\u30e9\\u30a6\\u30f3\\u30c7\\u30a3\\u30f3\\u30b0 (5-4-3-2-1)\\n\\u898b\\u3048\\u308b\\u3082\\u306e 5 \\u3064\\n\\u611f\\u3058\\u308b\\u3082\\u306e 4 \\u3064\\n\\u805e\\u3053\\u3048\\u308b\\u97f3 3 \\u3064\\n\\u5302\\u3044 2 \\u3064\\n\\u5473 1 \\u3064\\n\\n\\u6c17\\u3065\\u3044\\u305f\\u3053\\u3068\\u3092\\u8fd4\\u4fe1\\u3057\\u3066\\u304f\\u3060\\u3055\\u3044\\u3002",
        "hello": "\\u3053\\u3093\\u306b\\u3061\\u306f\\u3001MEDI\\u3067\\u3059\\u3002\\u547c\\u5438\\u3001\\u7761\\u7720\\u3001\\u30b9\\u30c8\\u30ec\\u30b9\\u306e\\u3069\\u308c\\u306b\\u3057\\u307e\\u3059\\u304b?",
        "fallback": "\\u3053\\u3053\\u306b\\u3044\\u307e\\u3059\\u3002\\u4eca\\u306e\\u6c17\\u6301\\u3061\\u3092\\u4e00\\u6587\\u3067\\u6559\\u3048\\u3066\\u304f\\u3060\\u3055\\u3044\\u3002",
        "reset": "\\u30ea\\u30bb\\u30c3\\u30c8\\u3057\\u307e\\u3057\\u305f\\u3002",
    },
    "ar": {
        "menu": "\\u0623\\u0646\\u0627 MEDI. \\u0627\\u062e\\u062a\\u0631:\\n1) \\u0627\\u0644\\u062a\\u0646\\u0641\\u0633\\n2) \\u0627\\u0644\\u0646\\u0648\\u0645\\n3) \\u0627\\u0644\\u0636\\u063a\\u0637 / \\u0627\\u0644\\u0642\\u0644\\u0642\\n\\n\\u0627\\u0631\\u062f\\u062f \\u0628 1 \\u0623\\u0648 2 \\u0623\\u0648 3.",
        "breathing": "\\u062a\\u0646\\u0641\\u0633 (\\u062f\\u0642\\u064a\\u0642\\u0629 \\u0648\\u0627\\u062d\\u062f\\u0629)\\n\\u0634\\u0647\\u064a\\u0642 \\u0644\\u0645\\u062f\\u0629 4 \\u062b\\u0648\\u0627\\u0646.\\n\\u0627\\u062d\\u0628\\u0633 \\u0646\\u0641\\u0633\\u0643 \\u0644\\u0645\\u062f\\u0629 2 \\u062b\\u0627\\u0646\\u064a\\u062a\\u064a\\u0646.\\n\\u0632\\u0641\\u064a\\u0631 \\u0644\\u0645\\u062f\\u0629 6 \\u062b\\u0648\\u0627\\u0646.\\n\\n\\u0643\\u0631\\u0631 \\u0630\\u0644\\u0643 5 \\u0645\\u0631\\u0627\\u062a.\\n\\u0627\\u0643\\u062a\\u0628 'done' \\u0639\\u0646\\u062f\\u0645\\u0627 \\u062a\\u0646\\u062a\\u0647\\u064a.",
        "sleep": "\\u0631\\u0648\\u062a\\u064a\\u0646 \\u0627\\u0644\\u0646\\u0648\\u0645\\n\\u0627\\u0633\\u062a\\u0644\\u0642\\u064e \\u0628\\u0634\\u0643\\u0644 \\u0645\\u0631\\u064a\\u062d.\\n\\u0623\\u0631\\u062e\\u0650 \\u0627\\u0644\\u0641\\u0643 \\u0648\\u0627\\u0644\\u0643\\u062a\\u0641\\u064a\\u0646.\\n\\n\\u0634\\u0647\\u064a\\u0642 \\u0628\\u0628\\u0637\\u0621\\u060c \\u0648\\u0632\\u0641\\u064a\\u0631 \\u0623\\u0637\\u0648\\u0644.\\n\\u0627\\u0643\\u062a\\u0628 'next' \\u0644\\u0631\\u0648\\u062a\\u064a\\u0646 \\u0623\\u0637\\u0648\\u0644.",
        "stress": "\\u062a\\u0623\\u0631\\u064a\\u0636 (5-4-3-2-1)\\n5 \\u0623\\u0634\\u064a\\u0627\\u0621 \\u062a\\u0631\\u0627\\u0647\\u0627\\n4 \\u0623\\u0634\\u064a\\u0627\\u0621 \\u062a\\u0634\\u0639\\u0631 \\u0628\\u0647\\u0627\\n3 \\u0623\\u0635\\u0648\\u0627\\u062a \\u062a\\u0633\\u0645\\u0639\\u0647\\u0627\\n2 \\u0631\\u0648\\u0627\\u0626\\u062d \\u062a\\u0634\\u0645\\u0647\\u0627\\n1 \\u0637\\u0639\\u0645 \\u062a\\u0634\\u0639\\u0631 \\u0628\\u0647\\n\\n\\u0627\\u0631\\u062f\\u062f \\u0628\\u0645\\u0627 \\u0644\\u0627\\u062d\\u0638\\u062a\\u0647.",
        "hello": "\\u0645\\u0631\\u062d\\u0628\\u0627\\u060c \\u0623\\u0646\\u0627 MEDI. \\u0647\\u0644 \\u062a\\u0631\\u064a\\u062f \\u062a\\u0646\\u0641\\u0633\\u0627 \\u0623\\u0648 \\u0646\\u0648\\u0645\\u0627 \\u0623\\u0648 \\u062f\\u0639\\u0645 \\u0644\\u0644\\u0636\\u063a\\u0637\\u061f",
        "fallback": "\\u0623\\u0646\\u0627 \\u0645\\u0639\\u0643. \\u0623\\u062e\\u0628\\u0631\\u0646\\u064a \\u0628\\u0645\\u0627 \\u062a\\u0634\\u0639\\u0631 \\u0628\\u0647 \\u0627\\u0644\\u0622\\u0646 \\u0641\\u064a \\u0633\\u0637\\u0631 \\u0648\\u0627\\u062d\\u062f.",
        "reset": "\\u062a\\u0645\\u062a \\u0625\\u0639\\u0627\\u062f\\u0629 \\u0627\\u0644\\u0645\\u062d\\u0627\\u062f\\u062b\\u0629.",
    },
}


def _rb_text(language: str, key: str) -> str:
    lang = language if language in _RULE_BASED_I18N else "en"
    value = _RULE_BASED_I18N[lang].get(key, _RULE_BASED_I18N["en"][key])
    if "\\u" in value or "\\n" in value:
        try:
            value = value.encode("utf-8").decode("unicode_escape")
        except Exception:
            pass
    return value


def is_reset_cmd(t: str) -> bool:
    return t in {
        "reset",
        "restart",
        "new",
        "recommencer",
        "restart conversation",
        "\u518d\u958b",
        "\u30ea\u30bb\u30c3\u30c8",
        "\u0625\u0639\u0627\u062f\u0629",
    }


def is_menu_cmd(t: str) -> bool:
    return t in {
        "menu",
        "help",
        "aide",
        "options",
        "\u30e1\u30cb\u30e5\u30fc",
        "\u52a9\u3051\u3066",
        "\u0627\u0644\u0642\u0627\u0626\u0645\u0629",
        "\u0645\u0633\u0627\u0639\u062f\u0629",
    }


def is_menu_selection(t: str) -> bool:
    return t in {
        "1",
        "breathing",
        "respiration",
        "\u547c\u5438",
        "\u062a\u0646\u0641\u0633",
        "2",
        "sleep",
        "sommeil",
        "\u7761\u7720",
        "\u0646\u0648\u0645",
        "3",
        "stress",
        "stress/anxiety",
        "anxiety",
        "anxiete",
        "\u4e0d\u5b89",
        "\u0642\u0644\u0642",
    }


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
    Assumes conversations.summary exists.
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

    Each chunk becomes:
      [K1] topic=... source=... score=... evidence=...
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
        score = c.get("score", 0.0)  # distance (smaller is better)

        # Day 10: evidence info (if rag_service returns it)
        evidence_level = c.get("evidence_level", "unknown")
        evidence_priority = c.get("evidence_priority", 0)

        content = c.get("content", "")

        # Keep prompt size reasonable
        if isinstance(content, str) and len(content) > 1200:
            content = content[:1200].rstrip() + "…"

        try:
            score_f = float(score)
        except Exception:
            score_f = 0.0

        parts.append(
            f"[{cid}] topic={topic} source={source} score={score_f:.4f} "
            f"evidence={evidence_level}({evidence_priority})\n"
            f"{content}"
        )

    return "\n\n".join(parts), valid_ids


def _detect_used_kb(answer: str) -> bool:
    return bool(re.search(r"\[K\d+\]", answer or ""))


def _extract_citation_ids(answer: str) -> list[str]:
    return re.findall(r"\[(K\d+)\]", answer or "")


# --------- CLAUDE CALL ---------

def _claude_reply(
    history: list[dict],
    *,
    retrieved_text: str = "",
    summary_text: str = "",
    valid_ids: list[str] | None = None,
    enforce_citations: bool = True,  # Day 10: conditional enforcement
    topic: str | None = None,        # Day 10: pass topic to help behavior (optional)
    response_language: str = "en",
) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set in environment")

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    summary_block = ""
    if summary_text.strip():
        summary_block = "=== Conversation Summary ===\n" + summary_text.strip() + "\n\n"

    # Day 10: response structure rules (consistent output)
    structure_rules = (
        "=== Response Style Rules ===\n"
        "- Keep it calm, brief, kind, and practical.\n"
        "- If anxiety/stress: provide ONE breathing exercise in 3–5 steps.\n"
        "- If sleep: provide ONE wind-down routine (2–4 steps) + one gentle reframing line.\n"
        "- If shutdown/trauma feelings: start with safety + grounding first, then optional breath.\n"
        "- Avoid medical claims. Avoid statistics (no effect sizes, no 95% CI).\n"
        "- If you used Retrieved Knowledge, cite [K#] at most 1–2 times.\n\n"
    )

    # conditional citation rules
    if enforce_citations:
        citation_rules = (
            "=== Citation Rules ===\n"
            "- If you use any information from 'Retrieved Knowledge', you MUST cite it using its bracket id (e.g., [K1], [K2]).\n"
            "- Place citations at the end of the sentence that uses the knowledge.\n"
            "- If you did NOT use Retrieved Knowledge, do NOT include any [K#] citations.\n"
            "- Do not invent citations. Only cite ids that appear in Retrieved Knowledge.\n\n"
        )
    else:
        citation_rules = (
            "=== Citation Rules ===\n"
            "- Use Retrieved Knowledge only if it is clearly relevant.\n"
            "- If you use it, you MAY cite [K#].\n"
            "- If not relevant, answer normally without citations.\n\n"
        )

    topic_hint = ""
    if topic:
        topic_hint = f"User topic hint: {topic}\n\n"

    lang_label = language_name(response_language)
    language_rules = (
        "=== Language Rules ===\n"
        f"- Always respond in {lang_label} (language code: {response_language}).\n"
        f"- The visible reply must be entirely in {lang_label}.\n"
        "- Do not switch to English unless the language code is en.\n"
        "- Keep citations format as [K#] if needed.\n\n"
    )

    system_prompt = (
        "You are MEDI, a calm meditation and mental-wellness assistant.\n"
        "- Supportive, non-medical guidance only.\n"
        "- Prefer short, actionable suggestions.\n"
        "- Do not provide medical diagnosis or treatment.\n"
        "- If self-harm intent or crisis: encourage contacting local emergency services.\n"
        "Tone: calm, brief, kind.\n\n"
        f"{topic_hint}"
        f"{language_rules}"
        f"{structure_rules}"
        f"{citation_rules}"
        "=== Grounding Priority ===\n"
        "- If Retrieved Knowledge is relevant, prioritize it over your general knowledge.\n"
        "- If it's not relevant, answer normally.\n\n"
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

def generate_reply_rule_based(text: str, language: str = "en") -> str:
    t = text.strip().lower()

    if is_menu_cmd(t):
        return _rb_text(language, "menu")

    if t in {"1", "breathing", "respiration", "\u547c\u5438", "\u062a\u0646\u0641\u0633"}:
        return _rb_text(language, "breathing")
    if t in {"2", "sleep", "sommeil", "\u7761\u7720", "\u0646\u0648\u0645"}:
        return _rb_text(language, "sleep")
    if t in {"3", "stress", "stress/anxiety", "anxiety", "anxiete", "\u4e0d\u5b89", "\u0642\u0644\u0642"}:
        return _rb_text(language, "stress")
    if t in {"hi", "hello", "hey", "bonjour", "salut", "\u3053\u3093\u306b\u3061\u306f", "\u0645\u0631\u062d\u0628\u0627"}:
        return _rb_text(language, "hello")
    return _rb_text(language, "fallback")


# --------- MAIN HANDLER ---------

def handle_incoming_message(
    db: Session,
    source: str,
    external_id: str,
    text: str,
    language_hint: str | None = None,
) -> dict:
    user = get_or_create_user(db, source=source, external_id=external_id)
    convo = get_or_create_active_conversation(db, user_id=user.id)

    incoming = (text or "").strip()
    t = incoming.lower()
    response_language = resolve_language(incoming, language_hint=language_hint, default="en")

    save_message(db, convo.id, "user", incoming)

    sev = score_severity(incoming)
    logger.info(
        "severity level=%s reasons=%s is_crisis=%s is_high=%s",
        sev.level,
        sev.reasons,
        sev.is_crisis,
        sev.is_high,
    )

# If crisis: short-circuit (no RAG/Claude)
    if sev.is_crisis:
        logger.warning("crisis flow triggered")
        reply = crisis_response()
        save_message(db, convo.id, "assistant", reply)
        return {"conversation_id": convo.id, "reply": reply, "language": response_language}

    # # Crisis first
    # if check_crisis(incoming):
    #     reply = crisis_response()
    #     save_message(db, convo.id, "assistant", reply)
    #     return {"conversation_id": convo.id, "reply": reply, "language": response_language}

    # Reset
    if is_reset_cmd(t):
        close_conversation(db, convo.id)
        new_convo = get_or_create_active_conversation(db, user_id=user.id)

        reply = _rb_text(response_language, "reset") + "\n\n" + _rb_text(response_language, "menu")
        save_message(db, new_convo.id, "assistant", reply)
        return {"conversation_id": new_convo.id, "reply": reply, "language": response_language}

    # Menu fast path
    if is_menu_cmd(t) or is_menu_selection(t):
        reply = generate_reply_rule_based(incoming, language=response_language)
        if check_medical(incoming):
            reply = medical_disclaimer(reply)

        save_message(db, convo.id, "assistant", reply)
        return {"conversation_id": convo.id, "reply": reply, "language": response_language}

    # Free text → Claude (with RAG + Day 10 precision layer)
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

            # 3) topic detect (Day 10)
            topic = detect_topic(incoming)

            # 4) retrieve top-k knowledge chunks (topic-aware)
            chunks: list[dict] = []
            if len(incoming) >= RAG_SKIP_SHORT_CHARS:
                chunks = retrieve_chunks(db, incoming, k=RAG_TOP_K, topic=topic)
            else:
                chunks = []

            retrieved_text, valid_ids = _format_retrieved(chunks)

            # 5) RAG confidence + conditional citation enforcement
            scores = []
            for c in chunks:
                try:
                    scores.append(float(c.get("score", 999.0)))
                except Exception:
                    pass

            rag_conf = rag_confidence_from_scores(scores)
            enforce_citations = (rag_conf >= CONF_ENFORCE_CITATIONS) and bool(chunks)

            # Debug meta
            if DEBUG_RAG:
                rag_meta = {
                    "topic": topic,
                    "rag_confidence": round(rag_conf, 3),
                    "enforce_citations": enforce_citations,
                    "retrieved_count": len(chunks),
                    "top_scores": [round(s, 4) for s in scores[:5]],
                    "valid_ids": valid_ids,
                    "chunks": [
                        {
                            "topic": c.get("topic"),
                            "source": c.get("source"),
                            "score": c.get("score"),
                            "evidence_level": c.get("evidence_level"),
                            "evidence_priority": c.get("evidence_priority"),
                        }
                        for c in chunks
                    ],
                    "preview": retrieved_text[:800] + ("…" if len(retrieved_text) > 800 else ""),
                }

            # 6) call Claude with summary + retrieved grounding
            reply = _claude_reply(
                history,
                retrieved_text=retrieved_text,
                summary_text=summary_text,
                valid_ids=valid_ids,
                enforce_citations=enforce_citations,
                topic=topic,
                response_language=response_language,
            )

            used_kb = _detect_used_kb(reply)
            citations = _extract_citation_ids(reply)

            logger.info(
                "topic=%s rag_conf=%.2f enforce=%s used_kb=%s citations=%s top_scores=%s",
                topic,
                rag_conf,
                enforce_citations,
                used_kb,
                citations,
                [round(s, 4) for s in scores[:3]],
            )
        except Exception:
            logger.exception("Claude/RAG call failed")
            reply = None

    if not reply:
        reply = generate_reply_rule_based(incoming, language=response_language)

    if check_medical(incoming):
        reply = medical_disclaimer(reply)

    save_message(db, convo.id, "assistant", reply)

    # update running summary every N user messages
    maybe_update_summary(db, convo.id)

    response = {"conversation_id": convo.id, "reply": reply, "language": response_language}

    # Optional: surface grounding info when debugging
    if DEBUG_RAG:
        response["used_kb"] = used_kb
        response["citations"] = citations
        response["rag"] = rag_meta

    return response


instrument_module_functions(globals(), include_private=settings.TRACE_INCLUDE_PRIVATE)
