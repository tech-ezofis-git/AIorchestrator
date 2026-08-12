-- Phase 5a: durable, cross-session Chat memory — scoped by user_id, not
-- session_id (see app/control/memory_store.py).
--
-- Runs automatically on first Postgres container start, same mechanism as
-- the prior migrations (mounted into /docker-entrypoint-initdb.d/ by
-- docker-compose.yml, numbered to run after them). Note: Postgres only
-- runs /docker-entrypoint-initdb.d/ scripts against a *fresh* data
-- volume — see the README for what to do if you're adding this to an
-- existing stack.
--
-- No update/delete path is exposed by the application in this phase
-- (memory deletion/editing is explicitly out of scope) — this table is
-- append-only from the app's point of view, same INSERT-only discipline
-- as audit_log.

CREATE TABLE IF NOT EXISTS memories (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    fact TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Supports "most recent N facts for this user" — the only read pattern
-- MemoryStore.fetch_recent issues.
CREATE INDEX IF NOT EXISTS memories_user_id_created_at_idx ON memories (user_id, created_at DESC);
