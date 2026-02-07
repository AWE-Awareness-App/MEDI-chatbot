from typing import Optional

CRISIS_KEYWORDS = [
    "kill myself",
    "end my life",
    "suicide",
    "want to die",
    "self harm",
    "hurt myself",
]

MEDICAL_KEYWORDS = [
    "diagnose",
    "medication",
    "dosage",
    "prescription",
    "treatment",
]

def check_crisis(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in CRISIS_KEYWORDS)

def check_medical(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in MEDICAL_KEYWORDS)

def crisis_response() -> str:
    return (
        "I’m really sorry you’re feeling this way. "
        "You’re not alone.\n\n"
        "If you’re in immediate danger, please contact local emergency services.\n"
        "If you’re in Canada, you can call or text **988** for the Suicide Crisis Helpline.\n\n"
        "If you want, you can tell me what’s been weighing on you — I’m here to listen."
    )

def medical_disclaimer(reply: str) -> str:
    disclaimer = (
        "\n\n⚠️ *Note:* I’m not a medical professional. "
        "This is for general support only and not a medical diagnosis."
    )
    return reply + disclaimer
