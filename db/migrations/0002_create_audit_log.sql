-- Phase 4b: durable audit_log table — an additional sink alongside the
-- existing stdout structured logging (see app/control/audit.py's
-- AuditMiddleware + configure_app_logging()), not a replacement.
--
-- Runs automatically on first Postgres container start, same mechanism as
-- scripts/init-pgvector.sql and 0001_create_documents_and_chunks.sql
-- (mounted into /docker-entrypoint-initdb.d/ by docker-compose.yml,
-- numbered to run after both). Note: Postgres only runs
-- /docker-entrypoint-initdb.d/ scripts against a *fresh* data volume — see
-- the README for what to do if you're adding this to an existing stack.
--
-- INSERT-only by application convention (see app/control/audit_store.py,
-- which deliberately exposes no update/delete path) — not enforced at the
-- database level in this phase; querying/reporting/retention are all
-- explicitly out of scope here too (see spec).
--
-- `intent` is nullable: guardrail rejections (content filter, rate limit)
-- can happen before intent classification ever runs. `latency_ms` is
-- nullable for the same reason on rejection paths where no request
-- actually completed. The two snippet columns are nullable and, per the
-- Phase 4b spec, are only ever populated for the six low-stakes intents
-- (chat/search/summary/insight/ocr/forecast) — AP and Mail rows, and any
-- row where intent isn't yet known, always have NULL snippets.

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    correlation_id TEXT NOT NULL,
    session_id TEXT,
    intent TEXT,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms DOUBLE PRECISION,
    redacted_request_snippet TEXT,
    redacted_response_snippet TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_log_correlation_id_idx ON audit_log (correlation_id);
CREATE INDEX IF NOT EXISTS audit_log_session_id_idx ON audit_log (session_id);
CREATE INDEX IF NOT EXISTS audit_log_created_at_idx ON audit_log (created_at);
