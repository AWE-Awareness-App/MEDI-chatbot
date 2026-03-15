import re
import unicodedata

from app.core.observability import instrument_module_functions

SUPPORTED_LANGUAGES = {"en", "fr", "ja", "ar"}

_LANG_ALIASES = {
    "ar": "ar",
    "ar-sa": "ar",
    "arabic": "ar",
    "en": "en",
    "en-gb": "en",
    "en-us": "en",
    "english": "en",
    "fr": "fr",
    "fr-fr": "fr",
    "francais": "fr",
    "french": "fr",
    "ja": "ja",
    "ja-jp": "ja",
    "japanese": "ja",
    "jp": "ja",
}

_LANG_NAMES = {
    "ar": "Arabic",
    "en": "English",
    "fr": "French",
    "ja": "Japanese",
}

_FRENCH_SINGLE_TOKEN_MARKERS = {
    "anxiete",
    "bonjour",
    "bonsoir",
    "francais",
    "merci",
    "oui",
    "respiration",
    "salut",
    "sommeil",
}

_FRENCH_PHRASE_MARKERS = (
    "bonjour",
    "bonsoir",
    "ca va",
    "comment ca va",
    "comment",
    "c est",
    "est ce",
    "il y a",
    "j ai",
    "j aime",
    "j espere",
    "je me",
    "je suis",
    "je veux",
    "je voudrais",
    "merci beaucoup",
    "parce que",
    "pourquoi",
    "qu est ce",
    "s il te plait",
    "s il vous plait",
)

_FRENCH_TOKEN_MARKERS = {
    "aide",
    "angoisse",
    "anxiete",
    "besoin",
    "bonjour",
    "bonsoir",
    "ca",
    "fatigue",
    "francais",
    "mal",
    "merci",
    "peur",
    "pourquoi",
    "respiration",
    "salut",
    "sommeil",
    "stress",
    "triste",
}

_FRENCH_FUNCTION_WORDS = {
    "au",
    "aux",
    "avec",
    "dans",
    "de",
    "des",
    "du",
    "elle",
    "et",
    "il",
    "j",
    "je",
    "la",
    "le",
    "les",
    "mais",
    "mes",
    "mon",
    "nous",
    "ou",
    "pas",
    "pour",
    "que",
    "qui",
    "sans",
    "sur",
    "tu",
    "une",
    "vous",
}


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _simplify_latin_text(text: str) -> str:
    simplified = _strip_accents((text or "").lower())
    simplified = simplified.replace("_", " ")
    simplified = simplified.replace("-", " ")
    simplified = simplified.replace("\u2019", "'")
    simplified = simplified.replace("`", "'")
    simplified = re.sub(r"[^a-z' ]+", " ", simplified)
    simplified = simplified.replace("'", " ")
    simplified = re.sub(r"\s+", " ", simplified).strip()
    return simplified


def normalize_language(language: str | None) -> str | None:
    if language is None:
        return None
    key = _strip_accents(language.strip().lower()).replace("_", "-")
    if not key:
        return None
    return _LANG_ALIASES.get(key)


def language_name(language: str | None) -> str:
    code = normalize_language(language) or "en"
    return _LANG_NAMES.get(code, "English")


def detect_language_from_text(text: str | None) -> str | None:
    t = (text or "").strip()
    if not t:
        return None

    if re.search(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]", t):
        return "ar"

    if re.search(r"[\u3040-\u30FF\u4E00-\u9FFF]", t):
        return "ja"

    low = t.lower()
    if re.search(r"[\u00e0\u00e2\u00e6\u00e7\u00e9\u00e8\u00ea\u00eb\u00ee\u00ef\u00f4\u0153\u00f9\u00fb\u00fc\u00ff]", low):
        return "fr"

    simplified = _simplify_latin_text(t)
    if not simplified:
        return "en"

    if any(marker in simplified for marker in _FRENCH_PHRASE_MARKERS):
        return "fr"

    tokens = set(simplified.split())
    if len(tokens) == 1 and next(iter(tokens)) in _FRENCH_SINGLE_TOKEN_MARKERS:
        return "fr"

    token_hits = len(tokens & _FRENCH_TOKEN_MARKERS)
    function_hits = len(tokens & _FRENCH_FUNCTION_WORDS)
    if token_hits >= 2:
        return "fr"
    if function_hits >= 3:
        return "fr"
    if token_hits >= 1 and function_hits >= 1:
        return "fr"

    return "en"


def resolve_language(text: str | None, language_hint: str | None = None, default: str = "en") -> str:
    hinted = normalize_language(language_hint)
    if hinted in SUPPORTED_LANGUAGES:
        return hinted

    detected = detect_language_from_text(text)
    if detected in SUPPORTED_LANGUAGES:
        return detected

    return normalize_language(default) or "en"


instrument_module_functions(globals(), include_private=False)
