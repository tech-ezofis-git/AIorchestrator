-- Local-only SQLite sample for tenant Summary extras.
-- Defaults stay on disk (read-only). Tenants add custom rules here.
-- Not mounted by Docker; created on first console use.

CREATE TABLE IF NOT EXISTS tenant_skills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    TEXT NOT NULL,
    agent        TEXT NOT NULL CHECK (agent IN ('ocr', 'summary', 'insight')),
    slug         TEXT NOT NULL,
    source_file  TEXT,
    is_custom    INTEGER NOT NULL DEFAULT 0 CHECK (is_custom IN (0, 1)),
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    body         TEXT NOT NULL,
    updated_by   TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (tenant_id, agent, slug)
);

CREATE TABLE IF NOT EXISTS tenant_rules (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    TEXT NOT NULL,
    agent        TEXT NOT NULL CHECK (agent IN ('ocr', 'summary', 'insight')),
    slug         TEXT NOT NULL,
    source_file  TEXT,
    is_custom    INTEGER NOT NULL DEFAULT 1 CHECK (is_custom IN (0, 1)),
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    always_apply INTEGER NOT NULL DEFAULT 1 CHECK (always_apply IN (0, 1)),
    body         TEXT NOT NULL,
    updated_by   TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (tenant_id, agent, slug)
);

CREATE TABLE IF NOT EXISTS tenant_skill_rule_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   TEXT NOT NULL,
    agent       TEXT NOT NULL,
    item_type   TEXT NOT NULL CHECK (item_type IN ('skill', 'rule')),
    item_id     INTEGER NOT NULL,
    action      TEXT NOT NULL CHECK (action IN ('CREATE', 'UPDATE', 'DISABLE', 'ENABLE', 'DELETE')),
    old_value   TEXT,
    new_value   TEXT,
    changed_by  TEXT,
    changed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS tenant_rules_lookup_idx
    ON tenant_rules (tenant_id, agent, is_active, is_custom);

CREATE INDEX IF NOT EXISTS tenant_skill_rule_logs_lookup_idx
    ON tenant_skill_rule_logs (tenant_id, agent, changed_at DESC);
