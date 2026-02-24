from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.embeddings_service import embed_query


def _to_pgvector(vec: list[float]) -> str:
    # pgvector literal format: '[0.1,0.2,...]'
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def retrieve_chunks(
    db: Session,
    query: str,
    k: int = 5,
    topic: str | None = None,
) -> list[dict]:
    """
    Day 10 RAG retrieval
    - Uses pgvector distance operator:
        L2 distance:   embedding <-> qvec
      (smaller = better)
    - Adds evidence tie-break ranking from metadata JSONB:
        metadata->>'evidence_priority'
    - Returns: content, topic, source, score, evidence_level, evidence_priority

    IMPORTANT:
    - We cast :qvec to vector explicitly: :qvec::vector
      so Postgres knows the type and the operator works.
    """

    qvec = embed_query(query)
    qvec_str = _to_pgvector(qvec)

    topic_clause = ""
    params = {"qvec": qvec_str, "k": int(k)}

    if topic:
        topic_clause = "WHERE topic = :topic"
        params["topic"] = topic

    rows = db.execute(
    text(
        f"""
        SELECT
            content,
            topic,
            source,
            (embedding <-> CAST(:qvec AS vector)) AS score,
            metadata
        FROM knowledge_chunks
        {topic_clause}
        ORDER BY
            (embedding <-> CAST(:qvec AS vector)) ASC,
            COALESCE((metadata->>'evidence_priority')::int, 0) DESC
        LIMIT :k
        """
    ),
    params,
).all()


    out: list[dict] = []
    for content, row_topic, source, score, metadata in rows:
        md = dict(metadata or {})
        out.append(
            {
                "content": content,
                "topic": row_topic or md.get("topic") or "general",
                "source": source or md.get("filename") or "unknown",
                "score": float(score),  # distance (smaller = better)
                "evidence_level": md.get("evidence_level", "unknown"),
                "evidence_priority": int(md.get("evidence_priority", 0) or 0),
            }
        )

    return out
