from __future__ import annotations
from typing import Optional

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
