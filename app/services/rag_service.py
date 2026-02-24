from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.embeddings_service import embed_query  # you’ll create below


def retrieve_chunks(db: Session, query: str, k: int = 5, topic: str | None = None) -> list[dict]:
    qvec = embed_query(query)
    qvec_str = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"

    if topic:
        rows = db.execute(
            text("""
                SELECT content, topic, source, (embedding <-> :qvec) AS score
                FROM knowledge_chunks
                WHERE topic = :topic
                ORDER BY embedding <-> :qvec
                LIMIT :k
            """),
            {"qvec": qvec_str, "topic": topic, "k": k},
        ).all()
    else:
        rows = db.execute(
            text("""
                SELECT content, topic, source, (embedding <-> :qvec) AS score
                FROM knowledge_chunks
                ORDER BY embedding <-> :qvec
                LIMIT :k
            """),
            {"qvec": qvec_str, "k": k},
        ).all()

    return [{"content": r[0], "topic": r[1], "source": r[2], "score": float(r[3])} for r in rows]
