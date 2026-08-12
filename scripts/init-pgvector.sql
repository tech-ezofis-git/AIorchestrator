-- Runs automatically on first Postgres container start (mounted into
-- /docker-entrypoint-initdb.d/). Enables pgvector now so it's ready for
-- Phase 2 (RAG + Search) — no schema/tables are created yet, Phase 1
-- doesn't use the database.
CREATE EXTENSION IF NOT EXISTS vector;
