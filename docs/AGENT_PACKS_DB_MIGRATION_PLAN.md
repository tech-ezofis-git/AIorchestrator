# Agent packs → Catalog + Tenant DB (phased plan)

**Status:** Implementation in progress on `main` working tree (Phases 0–4).  
**Goal:** Required defaults in **Catalog DB** (uneditable in tenant UI), customizations in **tenant-scoped Catalog tables** (editable; same pattern as `catalog_tenant_models`), UI reads/writes tables, agents keep current behavior.

---

## Principles (locked)

1. **Two layers — never mix**
   - Catalog = product defaults (required, sealed for tenants).
   - Tenant DB = editable / addable extras per tenant + agent.
2. **Runtime merge (unchanged semantics)**  
   `prompt = catalog defaults(agent) + active tenant skills/rules(agent)`.
3. **Assets** = UI name for **Skill** (`.md`) + **Rule** (`.mdc`). No separate asset table required.
4. **Templates** = PDF pdfme JSON — **separate tables**, not skills.
5. **AP Python pipeline** stays in code; only markdown packs + PDF templates move to DB.
6. **New agent in DB** with `kind=prompt|chat` can run; OCR/Summary/Insight/AP still need existing handlers unless we add kinds later.
7. **Parity first:** after cutover, Summary/OCR/Insight/Prompt/PDF behave like today with empty tenant extras.

---

## Phase 0 — Backup current source *(confirm first)*

| Step | Action |
|------|--------|
| 0.1 | Git tag + branch, e.g. `backup/agent-packs-pre-db` from current `main` |
| 0.2 | Archive packs: zip `skills/` + `app/skills/` + `app/tenant_skills/` (+ SQLite if present) → `deploy/backups/agent-packs-YYYYMMDD.zip` |
| 0.3 | Export console SQLite tenant extras (if any) to JSON for re-import |
| 0.4 | Document restore: checkout tag / unpack zip / restore SQLite |

**Exit:** Backup artifact + tag exist; no runtime change.

---

## Phase 1 — Schema (Catalog + Tenant), multi-tenant ready

### 1A. Catalog DB (`ezofis_catalog_new`)

| Table | Purpose |
|-------|---------|
| `platform_agent_skills` | Required default skills per `agent_slug` |
| `platform_agent_rules` | Required default rules per `agent_slug` |
| `platform_pdf_templates` | Optional default PDF templates (may be empty initially) |

Suggested columns (skills/rules):

- `id` UUID PK  
- `agent_slug` TEXT NOT NULL  -- `ocr`,`summary`,`insight`,`prompt`,`pdf`, future agents  
- `slug` TEXT NOT NULL       -- e.g. `SKILL`, `output-contract`  
- `kind` TEXT                -- skill \| rule (or separate tables)  
- `source_file` TEXT  
- `body` TEXT NOT NULL  
- `sort_order` INT  
- `is_active` BOOL DEFAULT true  
- `version` INT  
- `updated_at` / `updated_by`  
- UNIQUE(`agent_slug`, `slug`)

Templates: `agent_slug`, `template_slug`, `body_json` JSONB, `is_active`, UNIQUE(`agent_slug`, `template_slug`).

**No `tenant_id`** on platform tables (global required defaults). Supports all future tenants automatically.

### 1B. Tenant DB pattern (`ezofis_Tenant_{guid}`)

Tables created **per tenant** (migration helper + onboarding hook for new tenants):

| Table | Purpose |
|-------|---------|
| `agent_skills` | Editable/addable skills |
| `agent_rules` | Editable/addable rules |
| `agent_skill_rule_logs` | Audit |
| `pdf_templates` | Editable PDF templates (optional Phase 1B / Phase 4) |

Columns: same shape as today SQLite + `agent_slug` (open set, not hard CHECK limited to 3 agents — use TEXT + app validation so new agents work).

**New tenant:** onboarding / first-use ensures these tables exist (same as other tenant schema bootstrap).

**Exit:** SQL migrations + Python ensure_schema; no behavior change yet (feature flag off).

---

## Phase 2 — Seed Catalog from disk packs

| Agent | Seed from |
|-------|-----------|
| `summary` | `skills/summary/SKILL.md` + `rules/*.mdc` |
| `ocr` | `skills/ocr/...` |
| `insight` | `skills/insight/...` |
| `prompt` | `skills/prompt/...` |
| `pdf` | `skills/pdf/SKILL.md` (+ rules if any) |

Idempotent upsert by `(agent_slug, slug)`.  
Keep disk files as **seed source / fallback** until Phase 3 green.

Migrate existing SQLite tenant Summary extras → Tenant DB for known tenants (e.g. EZOFIS).

**Exit:** Catalog rows match disk; spot-check body hashes.

---

## Phase 3 — Runtime cutover (parity)

| Change | Detail |
|--------|--------|
| Loader | Prefer Catalog platform packs; fallback to disk if empty/flag off |
| Overlay | Summary (then OCR/Insight/Prompt) merge Tenant DB actives |
| Feature flag | e.g. `AGENT_PACKS_FROM_DB=1` for safe rollout |
| Tests | Extend loader/overlay/API tests; golden prompts unchanged when tenant empty |
| AP | Unchanged (Python skills) |

**Exit:** With flag on + empty tenant extras, OCR/Summary/Insight/Prompt results match pre-migration (same contracts).

---

## Phase 4 — Console UI (table-backed)

| UI | Behavior |
|----|----------|
| **Platform / Defaults** | Read Catalog; show as Assets (Skill/Rule); **read-only** for tenant operators (admin seed only) |
| **Tenant / Custom** | CRUD Tenant DB: upload `.md`/`.mdc`, edit body, enable/disable, delete + audit |
| **Agents** | Selector by `agent_slug` (summary, ocr, insight, prompt, …) |
| **PDF templates** (optional same phase or 4b) | List/edit tenant templates; catalog defaults read-only |

APIs: generalize `/console/summary-skills/*` → `/console/agent-packs/{agent}` (keep Summary aliases for compat).

**Exit:** Edit tenant rule → next Summary run uses new text without redeploy.

---

## Phase 5 — Optional: PDF templates + dynamic prompt agents

- Seed/list Catalog + Tenant PDF templates; `load_template(name)` resolves DB.  
- New `catalog_agents` row with `kind=custom` already chats; optionally merge packs for that slug.  
- Document: new slug ≠ OCR/AP unless handler exists.

---

## What we will **not** put in these tables

- AP skill Python modules (`extract_invoice`, `po_match`, …)  
- Deterministic locks / parsers  
- Redis / wipe scripts  

---

## Implementation notes (2026-09-16)

| Phase | Done |
|-------|------|
| 0 Backup | Tag `backup/agent-packs-pre-db-20260916-171225`, zip `deploy/backups/agent-packs-20260916-171225.zip`, `RESTORE_AGENT_PACKS.md` |
| 1 Schema | `db/migrations/0008_create_agent_pack_tables.sql` (+ optional `0008b` for tenant DB mirror) |
| 2 Seed | Startup `seed_platform_packs_from_disk` when Catalog connects |
| 3 Runtime | `async_system_prompt` / `get_agent_skill` for summary/ocr/insight/prompt; disk fallback |
| 4 UI | Console Summary APIs prefer Catalog; `/console/agent-packs/{agent}` added |
| 5 PDF | Tables created; named template loader not wired yet |

**Tenant customs** are stored in Catalog as `tenant_agent_skills` / `tenant_agent_rules` (keyed by `tenant_id`) so all future tenants work without per-tenant skill DDL — same pattern as `catalog_tenant_models`.

**Flag:** `AGENT_PACKS_FROM_DB` / `agent_packs_from_db` (default `true`). Set `false` to force disk + SQLite.


```text
Phase 0  Backup
Phase 1  Tables (Catalog + Tenant bootstrap)
Phase 2  Seed Catalog (+ migrate SQLite extras)
Phase 3  Runtime (flag + parity tests)
Phase 4  Console UI ↔ DB
Phase 5  PDF templates / dynamic agents (optional)
```

---

## Confirm checklist

Reply with what you want to proceed:

- [ ] Approve **full plan** as written  
- [ ] Approve **Phase 0 only** first (backup)  
- [ ] Changes: e.g. skip PDF for now / Catalog defaults also per-tenant sealed rows / include AP thresholds later  

After you confirm a phase, implementation starts for **that phase only**.
