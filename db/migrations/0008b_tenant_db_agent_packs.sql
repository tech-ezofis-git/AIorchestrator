-- Optional mirror of tenant pack tables for ezofis_Tenant_* databases.
-- Catalog-hosted tenant_agent_* tables are the runtime source of truth for
-- console + prompt merge. Apply this only if a tenant DB needs a local copy.
--
--   psql "$TENANT_DATABASE_URL" -f db/migrations/0008b_tenant_db_agent_packs.sql

CREATE TABLE IF NOT EXISTS agent_skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_slug TEXT NOT NULL,
    slug TEXT NOT NULL,
    source_file TEXT,
    body TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_slug, slug)
);

CREATE TABLE IF NOT EXISTS agent_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_slug TEXT NOT NULL,
    slug TEXT NOT NULL,
    source_file TEXT,
    body TEXT NOT NULL,
    always_apply BOOLEAN NOT NULL DEFAULT TRUE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_slug, slug)
);

CREATE TABLE IF NOT EXISTS agent_skill_rule_logs (
    id BIGSERIAL PRIMARY KEY,
    agent_slug TEXT NOT NULL,
    item_type TEXT NOT NULL CHECK (item_type IN ('skill', 'rule')),
    item_id UUID,
    action TEXT NOT NULL CHECK (action IN ('CREATE', 'UPDATE', 'DISABLE', 'ENABLE', 'DELETE')),
    old_value TEXT,
    new_value TEXT,
    changed_by TEXT,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pdf_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_slug TEXT NOT NULL DEFAULT 'pdf',
    template_slug TEXT NOT NULL,
    body_json JSONB NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_slug, template_slug)
);
