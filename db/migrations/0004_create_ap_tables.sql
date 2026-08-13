-- AP document jobs (Phase 1): runs, per-skill artifacts, tenant plans, credit audit.
--
-- Mounted into /docker-entrypoint-initdb.d/ by docker-compose.yml. Postgres only
-- runs those scripts against a *fresh* data volume — apply by hand with psql
-- (or recreate the volume) when adding this to an existing local stack.
--
-- Cloud billing remains the source of truth; ap_credit_ledger is a local audit.

CREATE TABLE IF NOT EXISTS ap_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    item_key TEXT NOT NULL,
    requested_skills JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'running',
    decision TEXT,
    credits_charged INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ap_runs_tenant_item_idx
    ON ap_runs (tenant_id, item_key, created_at DESC);

CREATE TABLE IF NOT EXISTS ap_skill_artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID REFERENCES ap_runs(id) ON DELETE CASCADE,
    tenant_id TEXT NOT NULL,
    item_key TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    result_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ap_skill_artifacts_item_skill_idx
    ON ap_skill_artifacts (tenant_id, item_key, skill_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ap_tenant_plans (
    tenant_id TEXT PRIMARY KEY,
    enabled_skills JSONB NOT NULL,
    thresholds JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ap_credit_ledger (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID REFERENCES ap_runs(id) ON DELETE SET NULL,
    tenant_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    credits INT NOT NULL DEFAULT 1,
    identify TEXT,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ap_credit_ledger_run_idx
    ON ap_credit_ledger (run_id, created_at DESC);
