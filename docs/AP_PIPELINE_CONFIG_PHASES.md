# AP Agent — Pipeline config (+ optional Instructions) phased plan

**Status:** **Phases 0–5 complete**. Phase 5 = optional AP Instructions (soft packs). Phase 6 optional hardening.  
**Phase 0 artifact:** [AP_PIPELINE_PHASE0_INVENTORY.md](./AP_PIPELINE_PHASE0_INVENTORY.md)  
**Goal:** Make **AP Pipeline Configuration** real (Catalog → `ApSkillRunner`). Keep **AP Instructions** (LLM guidance) as an optional later track, clearly separated in UI.

```text
AP AGENT
├── AP Instructions          ← optional later (markdown packs, soft)
│   └── LLM guidance / narrative / response format
└── AP Pipeline Configuration ← primary track (this plan)
    ├── Skill enable/disable
    ├── Execution order
    ├── Thresholds
    ├── Connector defaults
    └── Feature flags
            ↓
      ApSkillRunner
            ↓
  extract_invoice → po_lookup_sap → po_match → … → SAP/HANA
```

---

## Principles (locked)

1. **Two panels, never mix**  
   - **Pipeline** = executable config the runner reads.  
   - **Instructions** = optional LLM text only (same idea as Summary packs).  
2. **Code remains the skill implementation** — Catalog does not store Python; it stores which skills run, order, and knobs.  
3. **Safe defaults** — empty/missing tenant config → today’s `DEFAULT_SKILL_ORDER` + current hard-coded EZOFIS HANA behavior.  
4. **Audit** — every tenant config change logged.  
5. **No rewrite of past AP runs** — config applies to **new** runs only.  
6. **Request payload still wins when explicit** — e.g. `skills: [...]` in the chat/AP job overrides tenant default order (document clearly); omit `skills` → use tenant/platform pipeline config.

---

## What exists today (baseline)

| Piece | Today |
|-------|--------|
| Skills | Python in `app/ap_skills/*` |
| Default order | `DEFAULT_SKILL_ORDER` in code |
| Request override | `payload.skills` list |
| EZOFIS HANA | `ensure_ezofis_hana_po_lookup` injects `po_lookup_sap` |
| Connector | Request / EZOFIS default GUID in code |
| Thresholds / flags | Mostly hard-coded in skills (`po_match`, finalize, …) |
| Console | AP form (manual skills textarea) — **no** Catalog pipeline UI |

---

## Phase 0 — Design lock + inventory ✅ *(approved; doc only)*

| Step | Action | Done |
|------|--------|------|
| 0.1 | Freeze skill registry (`ALL_SKILLS` / `REGISTRY`) + default vs opt-in | [inventory §0.1](./AP_PIPELINE_PHASE0_INVENTORY.md#01-skill-registry-frozen-for-v1) |
| 0.2 | v1 knobs: order, enable filter, connector/resource defaults, `approved` / `partial` / `amount_tolerance`, `use_planner`, `force_hana_po_lookup` | [inventory §0.2](./AP_PIPELINE_PHASE0_INVENTORY.md#02-knobs-to-expose-in-catalog-v1-pipeline-config) |
| 0.3 | Override rule: explicit `payload.skills` > tenant pipeline > platform > code; thresholds payload > tenant > platform > `ap_tenant_plans` > Settings | [inventory §0.3](./AP_PIPELINE_PHASE0_INVENTORY.md#03-override-rules-locked) |
| 0.4 | Rollback: `AP_PIPELINE_FROM_DB=false`; missing rows fall back | [inventory §0.5](./AP_PIPELINE_PHASE0_INVENTORY.md#05-rollback--restore) |

**Exit:** [AP_PIPELINE_PHASE0_INVENTORY.md](./AP_PIPELINE_PHASE0_INVENTORY.md) — **no code change**.

**Notable baseline:** `ap_tenant_plans.enabled_skills` exists in tenant DB but is **not** applied by `resolve_skills` today; Phase 3 must wire Catalog (or deprecate that column explicitly).

---

## Phase 1 — Schema (Catalog only) ✅ *(approved)*

Target: **Catalog DB** (same pattern as `tenant_agent_*` / `catalog_tenant_models`).

| Table | Purpose |
|-------|---------|
| `platform_ap_pipeline` | Product default pipeline (`pipeline_key='default'`, `config_json`) — no `tenant_id` |
| `tenant_ap_pipeline` | Per-`tenant_id` override (`config_json`) |
| `tenant_ap_pipeline_logs` | Audit CREATE/UPDATE/DISABLE/ENABLE/DELETE/RESET |

**Delivered:**
- `db/migrations/0009_create_ap_pipeline_tables.sql`
- Included in `CatalogStore.ensure_schema()` (`app/catalog/store.py`)
- Feature flag `AP_PIPELINE_FROM_DB` / `ap_pipeline_from_db` **default false** (`app/config.py`); compose sets `"false"` explicitly
- **No runner change** — behavior identical to pre-Phase-1

Suggested config JSON shape (v1):

```json
{
  "skills_enabled": ["extract_invoice", "po_lookup_sap", "po_match", "finalize_decision", "workflow_move_next"],
  "skills_order": ["extract_invoice", "po_lookup_sap", "po_match", "finalize_decision", "workflow_move_next"],
  "default_connector_id": null,
  "default_resource": null,
  "thresholds": { "approved": null, "partial": null, "amount_tolerance": null },
  "flags": { "use_planner": false, "force_hana_po_lookup": true }
}
```

**Exit:** Migration + `ensure_schema`; feature flag `AP_PIPELINE_FROM_DB=false` by default; no runner change yet.

---

## Phase 2 — Seed platform default from code ✅ *(approved)*

| Step | Action | Done |
|------|--------|------|
| 2.1 | Upsert `platform_ap_pipeline` from `DEFAULT_SKILL_ORDER` + EZOFIS-safe flags | `app/ap_pipeline/defaults.py` + `seed.py` |
| 2.2 | Idempotent on startup when Catalog connects | `app/main.py` boot (not gated on `AP_PIPELINE_FROM_DB`) |

**Exit:** Platform row `pipeline_key='default'` matches today’s default pipeline; flag still off → runner behavior unchanged.

---

## Phase 3 — Runner cutover (parity) ✅ *(approved)*

| Step | Action | Done |
|------|--------|------|
| 3.1 | Resolve: request → tenant → platform → code | `app/ap_pipeline/resolve.py` |
| 3.2 | Wire `resolve_skills` / HANA inject / connector defaults / thresholds | `ApSkillRunner` + `planner.resolve_skills` |
| 3.3 | Flag on for local compose; Settings default remains `false` (rollback) | `docker-compose.yml` `AP_PIPELINE_FROM_DB=true` |
| 3.4 | Tests: empty tenant = default order; tenant `skills_enabled` drops `po_match` | `tests/test_ap_pipeline_resolve.py` |

**Exit:** With flag on + empty tenant config, AP skill list matches pre-change. Past tickets untouched.

---

## Phase 4 — Console UI (Pipeline tab only) ✅ *(approved)*

| UI | Behavior |
|----|----------|
| AP mode → right panel **AP Pipeline** | Skill enable + ↑↓ order, connector/resource, thresholds, flags |
| Platform | Read-only JSON (seeded) |
| Tenant | Editable when tenant selected; Save → `PUT /console/ap-pipeline/tenant` + audit log |
| Existing AP form | Unchanged (manual skills textarea still works) |

**Delivered:** `app/ap_pipeline/console.py`, `store.py` upsert/logs, routes `GET/PUT /console/ap-pipeline*`, console aside `#apPipelineInspector`.

**Exit:** Edit tenant order in console → next AP run (without explicit `skills`, flag on) uses new order.

---

## Phase 5 — Optional: AP Instructions (soft packs) ✅ *(approved)*

| Step | Action | Done |
|------|--------|------|
| 5.1 | Reuse `platform_agent_*` / `tenant_agent_*` with `agent_slug='ap'` | `skills/ap/` + seed via `_PACK_AGENTS` |
| 5.2 | Console **Instructions** tab (same UX as Summary packs) | AP Configuration → Pipeline \| Instructions |
| 5.3 | Wire LLM sites only (planner + extract structuring) | `instructions.py` → `planner` / `extract_invoice` |

**Exit:** Soft packs guide LLM wording/reorder only; they do **not** replace `po_match` or Pipeline config.

---

## Phase 6 — Optional hardening

- Per-skill thresholds (not only global)
- Environment scopes (live vs sample)
- “Reset to platform default” button
- Read-only view of last run’s resolved skill list (debug)

---

## What we will **not** put in Catalog

- Python source for `extract_invoice`, `po_match`, …
- Live SAP/HANA response caches as “knowledge”
- Rewriting historical `ap_runs` / tickets

---

## Phase order (you run one-by-one)

```text
Phase 0  Inventory + override rules (doc only)
Phase 1  Tables
Phase 2  Seed platform default
Phase 3  Runner + flag + parity tests
Phase 4  Console Pipeline UI
Phase 5  Instructions tab (optional)
Phase 6  Hardening (optional)
```

---

## Confirm checklist

- [x] Approve **Phase 0 only** — inventory locked ([AP_PIPELINE_PHASE0_INVENTORY.md](./AP_PIPELINE_PHASE0_INVENTORY.md))  
- [x] Approve **Phase 1** (Catalog schema)  
- [x] Approve **Phase 2** (seed platform default from code)  
- [x] Approve **Phase 3** (runner cutover + parity tests)  
- [x] Approve **Phase 4** (console Pipeline UI)  
- [x] Approve **Phase 5** (optional Instructions tab)  
- [ ] Approve **Phase 6** (optional hardening)  

After you confirm a phase, implementation starts for **that phase only**.
