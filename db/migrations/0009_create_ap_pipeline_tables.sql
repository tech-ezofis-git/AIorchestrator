-- AP Pipeline Configuration (platform default + per-tenant override).
-- Target: Catalog DB (CATALOG_DATABASE_URL) — same pattern as tenant_agent_*.
--
-- Stores executable pipeline knobs (skill order, thresholds, connector defaults,
-- flags). Does NOT store Python skill implementations.
--
-- Applied on CatalogStore.ensure_schema() and:
--   psql "$CATALOG_DATABASE_URL" -f db/migrations/0009_create_ap_pipeline_tables.sql
--
-- Runtime reads gated by AP_PIPELINE_FROM_DB (default false until Phase 3).
-- See docs/AP_PIPELINE_PHASE0_INVENTORY.md for config_json v1 shape.

CREATE TABLE IF NOT EXISTS platform_ap_pipeline (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline_key TEXT NOT NULL DEFAULT 'default',
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    version INT NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pipeline_key)
);

CREATE INDEX IF NOT EXISTS platform_ap_pipeline_active_idx
    ON platform_ap_pipeline (is_active) WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS tenant_ap_pipeline (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    config_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    version INT NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id)
);

CREATE INDEX IF NOT EXISTS tenant_ap_pipeline_lookup_idx
    ON tenant_ap_pipeline (tenant_id, is_active);

CREATE TABLE IF NOT EXISTS tenant_ap_pipeline_logs (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    pipeline_id UUID,
    action TEXT NOT NULL CHECK (action IN ('CREATE', 'UPDATE', 'DISABLE', 'ENABLE', 'DELETE', 'RESET')),
    old_value TEXT,
    new_value TEXT,
    changed_by TEXT,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tenant_ap_pipeline_logs_lookup_idx
    ON tenant_ap_pipeline_logs (tenant_id, changed_at DESC);
