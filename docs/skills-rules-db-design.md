# Skills & Rules database design

Shareable design for how EZOFIS Orchestrator stores **agent skills** and **rules**.

**Status**
- **Live today:** platform defaults on disk; tenant extras in SQLite (`tenant_skills`, `tenant_rules`, `tenant_skill_rule_logs`).
- **Target:** keep tenant tables as they are; add **separate platform tables** for default skills/rules so they can appear in Excel / console without mixing with tenant uploads.

Local SQLite file (dev): `app/tenant_skills/data/tenant_skills.sqlite`

---

## Agents in the console (Document actions)

The console **Document actions** buttons are four **agents**. Each agent has its own job. Skills/rules are scoped by the `agent` column so one tenant’s Summary pack never leaks into OCR, Insight, or AP.

| Console button | Agent name | `agent` value | What it does | Default skill/rules today |
|----------------|------------|---------------|--------------|---------------------------|
| **OCR agent** | OCR agent | `ocr` | Extract structured fields from a file / blob | `skills/ocr/SKILL.md` + `skills/ocr/rules/` |
| **Summary agent** | Summary agent | `summary` | Turn OCR text / JSON / file into a locked summary | `skills/summary/SKILL.md` + `skills/summary/rules/` |
| **Insight agent** | Insight agent | `insight` | Generate insights from JSON / document | `skills/insight/SKILL.md` + `skills/insight/rules/` |
| **AP agent** | AP agent | `ap` | Invoice / PO matching pipeline (extract, match, workflow) | Python skills under `app/ap_skills/` (not the same `.md` / `.mdc` pack) |
| **Prompt agent** | Prompt agent | `prompt` | Run the user prompt; raw text in `prompt_result.text` | `skills/prompt/SKILL.md` + `skills/prompt/rules/` |

**Which agent uses the SQLite skill/rule overlay today**

| Agent | Platform pack on disk | Tenant `tenant_skills` / `tenant_rules` overlay |
|-------|----------------------|--------------------------------------------------|
| **Summary agent** | Yes | **Yes** (wired in console + prompt merge) |
| **OCR agent** | Yes | Not wired yet (`agent` value reserved) |
| **Insight agent** | Yes | Not wired yet (`agent` value reserved) |
| **Prompt agent** | Yes | Not wired yet (`agent` value reserved) |
| **AP agent** | Different model: named pipeline steps in `app/ap_skills/` | Not in these tables yet |

Live CHECK on `tenant_skills.agent` / `tenant_rules.agent` is `ocr` | `summary` | `insight`. Target platform tables should use the same three for markdown packs. **AP agent** stays a separate skill runner unless product later adds `ap` to this schema.

Every tenant row still has `tenant_id` **and** `agent`, so:

- tenant `123` + **Summary agent** extras ≠ tenant `123` + **OCR agent** extras
- Platform defaults are per agent (one default skill pack per agent)

---

## 1. Two layers (do not mix)

| Layer | Who owns it | Shared across tenants? | Editable by tenant? | Store |
|--------|-------------|------------------------|----------------------|--------|
| **Platform defaults** | Product / platform | Yes | No (read-only in tenant UI) | Separate tables (target) + files on disk today |
| **Tenant extras** | Tenant | No | Yes (upload, edit, enable/disable, delete) | Existing `tenant_*` tables |

A tenant upload is **never** written as the product default. Defaults never use `tenant_id = 123`.

---

## 2. Runtime merge (Summary)

When a Summary job runs with a `tenant_id`:

1. Load **platform** skill (`SKILL.md` body).
2. Append every **active** tenant custom skill from `tenant_skills`.
3. Load **platform** rules (`*.mdc`).
4. Append every **active** tenant custom rule from `tenant_rules`.

```text
LLM system prompt
  = platform SKILL.md
  + active tenant skills (is_active = 1)
  + platform rules
  + active tenant rules (is_active = 1)
```

Disabled tenant rows stay in the DB but are **not** injected. Deleted rows are gone from `tenant_skills` / `tenant_rules`; the audit table keeps a `DELETE` log.

Insight agent and OCR agent use the same **file pack** pattern (`skills/insight`, `skills/ocr`). Tenant SQLite overlay is wired for the **Summary agent** first. The **AP agent** uses `app/ap_skills/` (invoice extract, PO match, workflow, …), not these markdown tables.

---

## 3. What “skill” vs “rule” means

| Kind | File type | Role |
|------|-----------|------|
| **Skill** | `.md` | Main instruction pack (who the assistant is, overall task) |
| **Rule** | `.mdc` | Extra constraint (output contract, highlights, privacy, …) |

Console upload: `.md` → skill table, `.mdc` → rule table.

---

## 4. Target table set

Use **separate tables** for platform vs tenant.

```text
┌─────────────────────┐     ┌─────────────────────┐
│  platform_skills    │     │  tenant_skills      │
│  (no tenant_id)     │     │  (per tenant_id)    │
└─────────────────────┘     └─────────────────────┘
┌─────────────────────┐     ┌─────────────────────┐
│  platform_rules     │     │  tenant_rules       │
│  (no tenant_id)     │     │  (per tenant_id)    │
└─────────────────────┘     └─────────────────────┘
                            ┌─────────────────────┐
                            │ tenant_skill_rule_  │
                            │ logs  (audit only)  │
                            └─────────────────────┘
```

**Why not one table for both?**  
`tenant_*` is keyed by `tenant_id`. Platform rows have no tenant. Mixing them needs a fake tenant (`_platform`) and makes Excel/UI easy to misread.

---

## 5. Platform tables (target — separate)

Not live in SQLite yet. Defaults still load from:

- `skills/summary/SKILL.md`
- `skills/summary/rules/output-contract.mdc`
- `skills/summary/rules/highlights.mdc`
- `skills/summary/rules/narrative.mdc`
- Same layout under `skills/insight/` and `skills/ocr/`

### 5.1 `platform_skills`

One default skill per agent.

| Column | Type | Notes |
|--------|------|--------|
| `id` | INTEGER PK | |
| `agent` | TEXT | `ocr` \| `summary` \| `insight` |
| `slug` | TEXT | e.g. `default1` |
| `source_file` | TEXT | e.g. `SKILL.md` |
| `is_active` | INTEGER 0/1 | Usually 1 |
| `body` | TEXT | Full markdown after optional frontmatter |
| `updated_by` | TEXT | |
| `created_at` / `updated_at` | TEXT | ISO datetime |
| **UNIQUE** | `(agent, slug)` | |

No `tenant_id`. No `is_custom`.

### 5.2 `platform_rules`

Several default rules per agent.

| Column | Type | Notes |
|--------|------|--------|
| `id` | INTEGER PK | |
| `agent` | TEXT | `ocr` \| `summary` \| `insight` |
| `slug` | TEXT | e.g. `default1`, `default2` |
| `source_file` | TEXT | e.g. `output-contract.mdc` |
| `is_active` | INTEGER 0/1 | |
| `always_apply` | INTEGER 0/1 | Default 1 |
| `body` | TEXT | |
| `updated_by` | TEXT | |
| `created_at` / `updated_at` | TEXT | |
| **UNIQUE** | `(agent, slug)` | |

### 5.3 Seed mapping (Summary)

| Table | slug | source_file |
|-------|------|-------------|
| `platform_skills` | `default1` | `SKILL.md` |
| `platform_rules` | `default1` | `output-contract.mdc` |
| `platform_rules` | `default2` | `highlights.mdc` |
| `platform_rules` | `default3` | `narrative.mdc` |

---

## 6. Tenant tables (live)

### 6.1 `tenant_skills`

Custom `.md` uploads for one tenant + agent.

| Column | Type | Notes |
|--------|------|--------|
| `id` | INTEGER PK | |
| `tenant_id` | TEXT | Required (e.g. `123`) |
| `agent` | TEXT | Currently `summary` in the console sample |
| `slug` | TEXT | Auto `custom1`, `custom2`, … |
| `source_file` | TEXT | Original filename (`test-skill.md`) |
| `is_custom` | INTEGER | Always **1** for tenant uploads |
| `is_active` | INTEGER | 1 = in prompt, 0 = disabled (row kept) |
| `body` | TEXT | Instruction body |
| `updated_by` | TEXT | e.g. `console` |
| `created_at` / `updated_at` | TEXT | |
| **UNIQUE** | `(tenant_id, agent, slug)` | |

### 6.2 `tenant_rules`

Custom `.mdc` uploads. Same shape as skills plus:

| Column | Type | Notes |
|--------|------|--------|
| `always_apply` | INTEGER | Default 1 |

`is_custom` is always **1** for tenant rows.

### 6.3 `tenant_skill_rule_logs` (audit)

Append-only. Deletes **do not** remove log rows.

| Column | Type | Notes |
|--------|------|--------|
| `id` | INTEGER PK | |
| `tenant_id` | TEXT | |
| `agent` | TEXT | |
| `item_type` | TEXT | `skill` or `rule` |
| `item_id` | INTEGER | Id in `tenant_skills` or `tenant_rules` at the time of the action |
| `action` | TEXT | `CREATE` \| `UPDATE` \| `ENABLE` \| `DISABLE` \| `DELETE` |
| `old_value` | TEXT | Previous body (nullable) |
| `new_value` | TEXT | New body (null on DELETE) |
| `changed_by` | TEXT | |
| `changed_at` | TEXT | |

**Disable vs delete**
- **DISABLE:** `is_active = 0`, row stays, log `DISABLE`.
- **DELETE:** row removed, log `DELETE` with `old_value` = last body.

---

## 7. Console vs database

| UI | Data source |
|----|-------------|
| **Platform** tab | Disk pack today; later `platform_skills` + `platform_rules` |
| **Tenant** tab | `tenant_skills` + `tenant_rules` for that `tenant_id` |
| **Change log** | `tenant_skill_rule_logs` |

Tenant APIs (Summary console):

- Upload `.md` → `tenant_skills` + log `CREATE`
- Upload `.mdc` → `tenant_rules` + log `CREATE`
- PATCH enable/disable/edit → `ENABLE` / `DISABLE` / `UPDATE`
- DELETE → remove row + log `DELETE`

---

## 8. Excel export

Script: `db/local/export_tenant_skills_xlsx.py`  
Output: `db/local/tenant_skills_export.xlsx`

| Sheet | Source | Expected content |
|-------|--------|------------------|
| README | — | Explains sheets |
| Skills | `tenant_skills` | Tenant customs only |
| Rules | `tenant_rules` | Tenant customs only |
| Audit Log | `tenant_skill_rule_logs` | History including DELETE |

Platform `SKILL.md` / default `*.mdc` **do not** appear until `platform_*` tables exist and the exporter adds a **Platform Skills** / **Platform Rules** sheet.

---

## 9. Proposed SQL (platform tables)

```sql
CREATE TABLE IF NOT EXISTS platform_skills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent        TEXT NOT NULL CHECK (agent IN ('ocr', 'summary', 'insight')),
    slug         TEXT NOT NULL,
    source_file  TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    body         TEXT NOT NULL,
    updated_by   TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (agent, slug)
);

CREATE TABLE IF NOT EXISTS platform_rules (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent        TEXT NOT NULL CHECK (agent IN ('ocr', 'summary', 'insight')),
    slug         TEXT NOT NULL,
    source_file  TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    always_apply INTEGER NOT NULL DEFAULT 1 CHECK (always_apply IN (0, 1)),
    body         TEXT NOT NULL,
    updated_by   TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (agent, slug)
);
```

`agent` values `ocr`, `summary`, `insight` match **OCR agent**, **Summary agent**, **Insight agent**. **AP agent** is not in this CHECK today; it uses `app/ap_skills/` instead of these markdown tables.

---

## 10. Do / don’t

**Do**
- Store product defaults in `platform_*` (or files until those tables are wired).
- Store tenant overlays in `tenant_*`.
- Keep audit in `tenant_skill_rule_logs`.

**Don’t**
- Insert platform `SKILL.md` into `tenant_skills` with a real `tenant_id`.
- Treat disable and delete as the same (disable keeps the row; delete removes it).
- Expect Excel Skills/Rules sheets to list disk defaults — they only dump tenant tables today.

---

## 11. File locations (current defaults)

| Agent | Console button | Skill / pack |
|-------|----------------|--------------|
| **OCR agent** | OCR agent | `skills/ocr/SKILL.md`, `skills/ocr/rules/` |
| **Summary agent** | Summary agent | `skills/summary/SKILL.md`, `skills/summary/rules/` |
| **Insight agent** | Insight agent | `skills/insight/SKILL.md`, `skills/insight/rules/` |
| **Prompt agent** | Prompt agent | `skills/prompt/SKILL.md`, `skills/prompt/rules/` |
| **AP agent** | AP agent | `app/ap_skills/` (Python pipeline skills, not `.md` / `.mdc` in this DB) |

Schema live in code: `app/tenant_skills/schema.sql`  
Overlay merge (Summary agent): `app/tenant_skills/overlay.py`
