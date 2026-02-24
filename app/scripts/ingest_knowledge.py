from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

# Allow running this file directly: `python app/scripts/ingest_knowledge.py`
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from openai import OpenAI
from pypdf import PdfReader
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


def safe_print(msg: str) -> None:
    """Print with non-ASCII characters replaced, to avoid Windows console errors."""
    print(msg.encode("ascii", errors="replace").decode("ascii"))


def extract_text(p: Path) -> str:
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(str(p))
        pages = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            pages.append(page_text)
        raw = "\n".join(pages)
        # Collapse excessive whitespace left by PDF extraction
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        raw = re.sub(r" {2,}", " ", raw)
        return raw.strip()
    return p.read_text(encoding="utf-8", errors="ignore")


def infer_topic(filename: str) -> str | None:
    # topic = file stem, normalised (e.g. "grossman_2004_mbsr")
    stem = Path(filename).stem.strip().lower()
    # replace spaces/dashes with underscores for consistency
    stem = re.sub(r"[\s\-]+", "_", stem)
    return stem or None


def main() -> None:
    folder = PROJECT_ROOT / "knowledge"
    files = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in {".md", ".txt", ".pdf"}]
    if not files:
        raise SystemExit("No .md/.txt/.pdf files found in ./knowledge")

    inserted, skipped, failed = 0, 0, 0

    with engine.begin() as conn:
        for p in files:
            try:
                raw = extract_text(p)
            except Exception as exc:
                safe_print(f"  [SKIP] {p.name} -- could not extract text: {exc}")
                failed += 1
                continue

            if not raw.strip():
                safe_print(f"  [SKIP] {p.name} -- no text extracted")
                failed += 1
                continue

            chunks = chunk_text(raw)
            safe_print(f"  {p.name}: {len(chunks)} chunks")
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

    print(f"Inserted: {inserted} | Skipped (duplicate): {skipped} | Failed: {failed}")


if __name__ == "__main__":
    main()
