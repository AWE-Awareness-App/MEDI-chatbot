from __future__ import annotations

import hashlib
import sys
from pathlib import Path

# Allow running this file directly: `python app/scripts/ingest_knowledge.py`
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI
from sqlalchemy import create_engine, text
from app.core.config import settings
from psycopg2.extras import Json



DB_URL = settings.DATABASE_URL
OPENAI_API_KEY =  settings.OPENAI_API_KEY
EMBED_MODEL =  settings.OPENAI_EMBED_MODEL

client = OpenAI(api_key=OPENAI_API_KEY)
engine = create_engine(DB_URL, pool_pre_ping=True)


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


def infer_topic(filename: str) -> str | None:
    # topic = file name (e.g., "mbsr_youth_overview")
    stem = Path(filename).stem.strip().lower()
    return stem or None


def main() -> None:
    folder = PROJECT_ROOT / "knowledge"
    files = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in {".md", ".txt"}]
    if not files:
        raise SystemExit("No .md/.txt files found in ./knowledge")

    inserted, skipped = 0, 0

    with engine.begin() as conn:
        for p in files:
            raw = p.read_text(encoding="utf-8", errors="ignore")
            chunks = chunk_text(raw)
            topic = infer_topic(p.name)
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
        "metadata": Json({"filename": p.name}),
        "chunk_hash": h,
        "embedding": vec,
    },
)

                    inserted += 1

    print(f"Inserted: {inserted} | Skipped: {skipped}")


if __name__ == "__main__":
    main()
