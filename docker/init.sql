-- Run once when the PostgreSQL container is first created.
-- Enables pgvector and creates the knowledge_chunks table.
-- The app's SQLAlchemy models (users, conversations, messages) are
-- created automatically on FastAPI startup via Base.metadata.create_all().

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id          SERIAL PRIMARY KEY,
    content     TEXT        NOT NULL,
    topic       TEXT,
    source      TEXT,
    metadata    JSONB,
    chunk_hash  TEXT        UNIQUE NOT NULL,
    embedding   vector(1536)        -- text-embedding-3-small default dimension
);
