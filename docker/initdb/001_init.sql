CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS knowledge_chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  content text NOT NULL,
  topic text,
  source text,
  metadata jsonb DEFAULT '{}'::jsonb,
  chunk_hash text UNIQUE NOT NULL,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  embedding vector(1536) NOT NULL
);

CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding_ivfflat
ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

ANALYZE knowledge_chunks;
