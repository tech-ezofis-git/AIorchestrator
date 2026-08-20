-- Per-tenant, per-agent LLM model selection (Catalog Agents grid).
-- Falls back to catalog_tenant_models when no row exists for an agent.

CREATE TABLE IF NOT EXISTS catalog_tenant_agent_models (
    tenant_id TEXT NOT NULL,
    agent_slug TEXT NOT NULL,
    model_id UUID REFERENCES catalog_models(id) ON DELETE SET NULL,
    fallback_model_id UUID REFERENCES catalog_models(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, agent_slug)
);

CREATE INDEX IF NOT EXISTS catalog_tenant_agent_models_tenant_idx
    ON catalog_tenant_agent_models (tenant_id);
