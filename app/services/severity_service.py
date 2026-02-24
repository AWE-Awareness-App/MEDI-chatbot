from __future__ import annotations
from dataclasses import dataclass
from typing import List, Tuple
import re

@dataclass
class SeverityResult:
    level: int                 # 0..4
    reasons: List[str]         # tags
    is_crisis: bool            # True if level == 4
    is_high: bool              # True if level >= 3

# --- Pattern sets (keep simple + explainable) ---
# IMPORTANT: do not over-trigger on words like "kill time" etc.
CRISIS_PATTERNS: List[Tuple[str, str]] = [
    (r"\b(i (want|wanna|plan) to (die|kill myself))\b", "self_harm_intent"),
    (r"\b(suicid(al|e)|end my life)\b", "self_harm_signal"),
    (r"\b(i have a plan)\b", "plan_mention"),
    (r"\b(i will (kill|hurt) myself)\b", "self_harm_intent"),
    (r"\b(can you help me (kill myself|suicide))\b", "request_self_harm_help"),
]

IMMINENT_DANGER_PATTERNS: List[Tuple[str, str]] = [
    (r"\b(i am going to do it (now|tonight|today))\b", "imminent_timeframe"),
    (r"\b(i have (a gun|a knife|pills|rope))\b", "means_mentioned"),
]

HIGH_DISTRESS_PATTERNS: List[Tuple[str, str]] = [
    (r"\b(can't cope|can't take it|can't go on)\b", "cannot_cope"),
    (r"\b(hopeless|worthless)\b", "hopelessness"),
    (r"\b(panic attack|panicking)\b", "panic"),
    (r"\b(i can't function|i can’t function)\b", "cannot_function"),
    (r"\b(hearing voices|voices telling me)\b", "possible_psychosis"),
]

MODERATE_DISTRESS_PATTERNS: List[Tuple[str, str]] = [
    (r"\b(anxious|anxiety|stressed|overwhelmed)\b", "anxiety_stress"),
    (r"\b(can't sleep|insomnia)\b", "sleep_issue"),
    (r"\b(sad|down|depressed)\b", "low_mood"),
]

MEDICAL_RISK_PATTERNS: List[Tuple[str, str]] = [
    (r"\b(chest pain|can't breathe|shortness of breath)\b", "possible_urgent_medical"),
    (r"\b(fainting|passed out)\b", "possible_urgent_medical"),
]

def _match_any(text: str, patterns: List[Tuple[str, str]]) -> List[str]:
    reasons: List[str] = []
    for pat, tag in patterns:
        if re.search(pat, text, flags=re.IGNORECASE):
            reasons.append(tag)
    return reasons

def score_severity(user_text: str) -> SeverityResult:
    print("ahhhhaaaaaaaaaaaaaaaa")
    t = (user_text or "").strip()
    if not t:
        return SeverityResult(level=0, reasons=[], is_crisis=False, is_high=False)

    # 4 = crisis
    crisis_reasons = _match_any(t, CRISIS_PATTERNS) + _match_any(t, IMMINENT_DANGER_PATTERNS)
    if crisis_reasons:
        return SeverityResult(level=4, reasons=sorted(set(crisis_reasons)), is_crisis=True, is_high=True)

    # 3 = high distress / possible urgent medical / psychosis signals (non-diagnostic)
    high = _match_any(t, HIGH_DISTRESS_PATTERNS)
    medical = _match_any(t, MEDICAL_RISK_PATTERNS)
    if high or medical:
        return SeverityResult(level=3, reasons=sorted(set(high + medical)), is_crisis=False, is_high=True)

    # 2 = moderate distress
    moderate = _match_any(t, MODERATE_DISTRESS_PATTERNS)
    if moderate:
        return SeverityResult(level=2, reasons=sorted(set(moderate)), is_crisis=False, is_high=False)

    return SeverityResult(level=1, reasons=["general"], is_crisis=False, is_high=False)
