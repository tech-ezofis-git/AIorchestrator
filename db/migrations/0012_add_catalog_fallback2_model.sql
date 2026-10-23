-- Second catalog fallback model (primary -> fallback -> 2nd fallback).

ALTER TABLE catalog_tenant_models
    ADD COLUMN IF NOT EXISTS fallback2_model_id UUID REFERENCES catalog_models(id) ON DELETE SET NULL;

ALTER TABLE catalog_tenant_agent_models
    ADD COLUMN IF NOT EXISTS fallback2_model_id UUID REFERENCES catalog_models(id) ON DELETE SET NULL;
