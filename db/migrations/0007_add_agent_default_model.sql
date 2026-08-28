-- Per-agent default model (actual model remains model_id; fallback is fallback_model_id).

ALTER TABLE catalog_tenant_agent_models
    ADD COLUMN IF NOT EXISTS default_model_id UUID REFERENCES catalog_models(id) ON DELETE SET NULL;
