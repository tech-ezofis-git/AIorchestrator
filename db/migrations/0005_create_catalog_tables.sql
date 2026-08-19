-- Catalog tables for agents, LLM model endpoints/keys, and per-tenant
-- default/fallback model selection.
--
-- Target: ezofis_catalog_new (CATALOG_DATABASE_URL), NOT per-tenant
-- ezofis_Tenant_* databases. The app also runs this DDL on startup
-- (CREATE IF NOT EXISTS) so Azure and local stacks pick it up without
-- recreating volumes.
--
-- Local docker still mounts this file into initdb for a fresh orchestrator
-- volume (fallback if CATALOG_DATABASE_URL is unset). Existing Azure DBs
-- are updated by the app on connect, or by hand:
--   psql "$CATALOG_DATABASE_URL" -f db/migrations/0005_create_catalog_tables.sql

CREATE TABLE IF NOT EXISTS catalog_agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('builtin', 'custom')),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    system_prompt TEXT,
    trigger_phrases TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS catalog_agents_kind_idx ON catalog_agents (kind, name);

CREATE TABLE IF NOT EXISTS catalog_models (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    model TEXT NOT NULL,
    api_base TEXT NOT NULL DEFAULT '',
    api_key TEXT NOT NULL DEFAULT '',
    api_version TEXT,
    region TEXT,
    model_version TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS catalog_models_sort_idx ON catalog_models (sort_order, label);

CREATE TABLE IF NOT EXISTS catalog_tenant_models (
    tenant_id TEXT PRIMARY KEY,
    default_model_id UUID NOT NULL REFERENCES catalog_models(id) ON DELETE RESTRICT,
    fallback_model_id UUID REFERENCES catalog_models(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
