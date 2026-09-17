-- Agent skill/rule packs (platform defaults + tenant customs).
-- Target: ezofis_catalog_new (CATALOG_DATABASE_URL).
--
-- Platform tables have no tenant_id (shared required defaults for all tenants).
-- Tenant-scoped tables live in Catalog DB (same pattern as catalog_tenant_models)
-- so every upcoming tenant works without per-DB skill DDL for console/runtime.
-- Optional mirror DDL for ezofis_Tenant_* is in 0008b_tenant_db_agent_packs.sql.
--
-- Applied on CatalogStore.ensure_schema() and:
--   psql "$CATALOG_DATABASE_URL" -f db/migrations/0008_create_agent_pack_tables.sql

CREATE TABLE IF NOT EXISTS platform_agent_skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_slug TEXT NOT NULL,
    slug TEXT NOT NULL,
    source_file TEXT,
    body TEXT NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    version INT NOT NULL DEFAULT 1,
    updated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_slug, slug)
);

CREATE INDEX IF NOT EXISTS platform_agent_skills_agent_idx
    ON platform_agent_skills (agent_slug, is_active, sort_order);

CREATE TABLE IF NOT EXISTS platform_agent_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_slug TEXT NOT NULL,
    slug TEXT NOT NULL,
    source_file TEXT,
    description TEXT,
    body TEXT NOT NULL,
    always_apply BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INT NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    version INT NOT NULL DEFAULT 1,
    updated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_slug, slug)
);

CREATE INDEX IF NOT EXISTS platform_agent_rules_agent_idx
    ON platform_agent_rules (agent_slug, is_active, sort_order);

CREATE TABLE IF NOT EXISTS platform_pdf_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_slug TEXT NOT NULL DEFAULT 'pdf',
    template_slug TEXT NOT NULL,
    body_json JSONB NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    version INT NOT NULL DEFAULT 1,
    updated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_slug, template_slug)
);

CREATE TABLE IF NOT EXISTS tenant_agent_skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    agent_slug TEXT NOT NULL,
    slug TEXT NOT NULL,
    source_file TEXT,
    body TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, agent_slug, slug)
);

CREATE INDEX IF NOT EXISTS tenant_agent_skills_lookup_idx
    ON tenant_agent_skills (tenant_id, agent_slug, is_active);

CREATE TABLE IF NOT EXISTS tenant_agent_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    agent_slug TEXT NOT NULL,
    slug TEXT NOT NULL,
    source_file TEXT,
    description TEXT,
    body TEXT NOT NULL,
    always_apply BOOLEAN NOT NULL DEFAULT TRUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, agent_slug, slug)
);

CREATE INDEX IF NOT EXISTS tenant_agent_rules_lookup_idx
    ON tenant_agent_rules (tenant_id, agent_slug, is_active);

CREATE TABLE IF NOT EXISTS tenant_agent_skill_rule_logs (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    agent_slug TEXT NOT NULL,
    item_type TEXT NOT NULL CHECK (item_type IN ('skill', 'rule')),
    item_id UUID,
    action TEXT NOT NULL CHECK (action IN ('CREATE', 'UPDATE', 'DISABLE', 'ENABLE', 'DELETE')),
    old_value TEXT,
    new_value TEXT,
    changed_by TEXT,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tenant_agent_skill_rule_logs_lookup_idx
    ON tenant_agent_skill_rule_logs (tenant_id, agent_slug, changed_at DESC);

CREATE TABLE IF NOT EXISTS tenant_pdf_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id TEXT NOT NULL,
    agent_slug TEXT NOT NULL DEFAULT 'pdf',
    template_slug TEXT NOT NULL,
    body_json JSONB NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, agent_slug, template_slug)
);

-- Idempotent upgrades for DBs that already created 0008 without description.
ALTER TABLE platform_agent_rules ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE tenant_agent_rules ADD COLUMN IF NOT EXISTS description TEXT;
