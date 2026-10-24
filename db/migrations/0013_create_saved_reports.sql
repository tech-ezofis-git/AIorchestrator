-- Saved Report Library (Report page cards load from this table).
-- Target: orchestrator DATABASE_URL. App also runs this on startup
-- (CREATE IF NOT EXISTS) so existing volumes pick it up.

CREATE TABLE IF NOT EXISTS saved_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    domain TEXT NOT NULL DEFAULT '',
    report_type TEXT NOT NULL DEFAULT 'all_workflows',
    workflow_name TEXT,
    repository_name TEXT,
    owner TEXT NOT NULL DEFAULT '',
    visibility TEXT NOT NULL DEFAULT 'Private',
    tags TEXT[] NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'Draft',
    version INT NOT NULL DEFAULT 1,
    favorite BOOLEAN NOT NULL DEFAULT FALSE,
    views INT NOT NULL DEFAULT 0,
    runs INT NOT NULL DEFAULT 0,
    viz TEXT NOT NULL DEFAULT 'table',
    report_prompt TEXT NOT NULL DEFAULT '',
    report_spec JSONB NOT NULL DEFAULT '{}'::jsonb,
    schedule JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    modified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_generated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS saved_reports_tenant_modified_idx
    ON saved_reports (tenant_id, modified_at DESC);

CREATE INDEX IF NOT EXISTS saved_reports_tenant_status_idx
    ON saved_reports (tenant_id, status);
