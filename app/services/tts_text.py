import re

_ORD = {
    1: "First",
    2: "Second",
    3: "Third",
    4: "Fourth",
    5: "Fifth",
    6: "Sixth",
    7: "Seventh",
    8: "Eighth",
    9: "Ninth",
    10: "Tenth",
}

def format_for_tts(text: str) -> str:
    """
    Convert markdown / numbered lists into spoken-friendly text.
    """
    if not text:
        return ""

    t = text.strip()

    # Remove common markdown emphasis/backticks that sound weird
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"\*([^*]+)\*", r"\1", t)

    # Convert numbered list like "1) item" or "1 ) item" into "First: item"
    def repl_num_paren(m: re.Match) -> str:
        n = int(m.group(1))
        word = _ORD.get(n, f"Step {n}")
        return f"{word}: "

    t = re.sub(r"(?m)^\s*(\d+)\s*\)\s+", repl_num_paren, t)

    # Convert numbered list like "1. item" into "First: item"
    def repl_num_dot(m: re.Match) -> str:
        n = int(m.group(1))
        word = _ORD.get(n, f"Step {n}")
        return f"{word}: "

    t = re.sub(r"(?m)^\s*(\d+)\.\s+", repl_num_dot, t)

    # Convert bullets "- item" or "* item" into "• item" (sounds like a pause)
    t = re.sub(r"(?m)^\s*[-*]\s+", "• ", t)

    # Replace weird standalone parentheses that get read
    t = t.replace(" )", ")").replace("( ", "(")

    # Slightly improve pauses for TTS
    t = re.sub(r"\n{3,}", "\n\n", t)      # collapse huge gaps
    t = re.sub(r"[ \t]{2,}", " ", t)      # collapse extra spaces

    return t