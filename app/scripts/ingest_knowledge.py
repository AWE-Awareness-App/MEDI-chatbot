from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Optional

# Allow running this file directly: `python app/scripts/ingest_knowledge.py`
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI
from sqlalchemy import create_engine, text
from psycopg2.extras import Json

from app.core.config import settings


DB_URL = settings.DATABASE_URL
OPENAI_API_KEY = settings.OPENAI_API_KEY
EMBED_MODEL = settings.OPENAI_EMBED_MODEL

client = OpenAI(api_key=OPENAI_API_KEY)
engine = create_engine(DB_URL, pool_pre_ping=True)


# ----------------------------
# Day 10: Evidence ranking
# ----------------------------
EVIDENCE_LEVEL_PRIORITY = {
    "meta_analysis": 4,
    "rct": 3,
    "review": 2,
    "theory": 1,
    "unknown": 0,
}


def chunk_text(s: str, chunk_size: int = 1200, overlap: int = 150) -> list[str]:
    s = s.strip()
    out = []
    i = 0
    while i < len(s):
        out.append(s[i : i + chunk_size])
        i += max(1, chunk_size - overlap)
    return [c.strip() for c in out if c.strip()]


def embed(texts: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
    data = sorted(resp.data, key=lambda x: x.index)
    return [d.embedding for d in data]


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ----------------------------
# Day 10: Topic + evidence inference
# ----------------------------
def infer_topic_from_filename(filename: str) -> str:
    f = filename.lower()

    # domains
    if "sleep" in f:
        return "sleep"
    if "breath" in f or "breathing" in f or "rsa" in f or "hrv" in f:
        return "breathing"
    if "polyvagal" in f or "vagal" in f or "neuroception" in f:
        return "polyvagal"

    # populations/conditions
    if "cancer" in f or "lung" in f or "oncolog" in f:
        return "cancer"
    if "infertil" in f or "ivf" in f or "fertilit" in f:
        return "infertility"
    if "child" in f or "adolesc" in f or "youth" in f or "teen" in f or "school" in f:
        return "youth"

    # symptoms
    if "anxiety" in f or "panic" in f:
        return "anxiety"
    if "stress" in f or "burnout" in f:
        return "stress"
    if "depress" in f or "mood" in f:
        return "depression"

    return "general"


def infer_evidence_level(filename: str, text_preview: str) -> str:
    f = filename.lower()
    t = (text_preview or "").lower()

    if "meta" in f or "meta-analysis" in t or "meta analysis" in t:
        return "meta_analysis"
    if "systematic review" in t or "review" in f:
        return "review"
    if "randomized" in t or "randomised" in t or "rct" in f or "trial" in f:
        return "rct"
    if "theory" in f or "theory" in t:
        return "theory"

    return "unknown"


def main() -> None:
    # Keep same behavior as your file
    folder = PROJECT_ROOT / "knowledge"
    files = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in {".md", ".txt"}]
    if not files:
        raise SystemExit("No .md/.txt files found in ./knowledge")

    inserted, skipped = 0, 0

    with engine.begin() as conn:
        for p in files:
            raw = p.read_text(encoding="utf-8", errors="ignore").strip()
            if not raw:
                continue

            chunks = chunk_text(raw)

            topic = infer_topic_from_filename(p.name)
            evidence_level = infer_evidence_level(p.name, raw[:4000])
            evidence_priority = EVIDENCE_LEVEL_PRIORITY.get(evidence_level, 0)

            try:
                source = str(p.relative_to(PROJECT_ROOT))
            except Exception:
                source = str(p)

            BATCH = 64
            for i in range(0, len(chunks), BATCH):
                batch = chunks[i : i + BATCH]
                vectors = embed(batch)

                for content, vec in zip(batch, vectors):
                    h = sha(content)

                    exists = conn.execute(
                        text("SELECT 1 FROM knowledge_chunks WHERE chunk_hash=:h LIMIT 1"),
                        {"h": h},
                    ).fetchone()

                    if exists:
                        skipped += 1
                        continue

                    metadata = {
                        "filename": p.name,
                        "topic": topic,
                        "evidence_level": evidence_level,
                        "evidence_priority": evidence_priority,
                    }

                    conn.execute(
                        text(
                            """
                            INSERT INTO knowledge_chunks (content, topic, source, metadata, chunk_hash, embedding)
                            VALUES (:content, :topic, :source, :metadata, :chunk_hash, :embedding)
                            """
                        ),
                        {
                            "content": content,
                            "topic": topic,
                            "source": source,
                            "metadata": Json(metadata),
                            "chunk_hash": h,
                            "embedding": vec,
                        },
                    )
                    inserted += 1

    print(f"Inserted: {inserted} | Skipped: {skipped}")


if __name__ == "__main__":
    main()


# NEEDED for database config
#-- enable uuid generator (choose ONE) 
# CREATE EXTENSION IF NOT EXISTS pgcrypto;

# -- Set default UUID for id if id is uuid type
# ALTER TABLE knowledge_chunks
# ALTER COLUMN id SET DEFAULT gen_random_uuid();
