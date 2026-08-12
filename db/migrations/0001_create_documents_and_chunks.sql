-- Phase 2: documents + chunks tables for RAG / hybrid search.
--
-- Runs automatically on first Postgres container start, same mechanism as
-- scripts/init-pgvector.sql from Phase 0/1 (mounted into
-- /docker-entrypoint-initdb.d/ by docker-compose.yml, after the pgvector
-- extension is enabled — see that file's ordering comment). Note: Postgres
-- only runs /docker-entrypoint-initdb.d/ scripts against a *fresh* data
-- volume. If you're adding this to an existing local stack, either
-- `docker compose down -v` first (drops the volume) or apply this file by
-- hand with psql.
--
-- Index choice: ivfflat vs hnsw
--   ivfflat's recall depends on the `lists` parameter being tuned to
--   table size (docs recommend roughly rows/1000, or sqrt(rows) past 1M
--   rows) *and* on `probes` at query time. Get that mismatched — which is
--   exactly what happens on a small, unpredictable corpus like this
--   phase's test fixtures — and it doesn't error, it just silently misses
--   most rows (confirmed while validating this migration: lists=100
--   against 5 seeded rows returned 1 result instead of 5). That's a worse
--   failure mode than the explicit-error discipline the rest of this
--   phase follows (see VectorStoreUnavailableError) — a wrong answer
--   instead of a loud one.
--   hnsw has no training step and no lists/probes tuning to get wrong —
--   it's slower to build and uses more memory at large scale, but is the
--   safe default while corpus size is small and unpredictable. Revisit
--   for ivfflat (cheaper to build) once real EZOFIS-scale ingestion with
--   a known row-count range lands.
--
-- Embedding dimension: VECTOR(1536) matches OpenAI's text-embedding-3-small
-- (the default EMBEDDING_MODEL). pgvector columns are fixed-size, so
-- swapping EMBEDDING_MODEL to a model with a *different* output dimension
-- requires a new migration to change this column's size (and
-- re-ingesting) — that's a data-layer constraint of pgvector itself, not
-- provider branching in application code.

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source TEXT NOT NULL,
    title TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    text TEXT NOT NULL,
    embedding VECTOR(1536),
    text_search TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS chunks_text_search_idx
    ON chunks USING GIN (text_search);
