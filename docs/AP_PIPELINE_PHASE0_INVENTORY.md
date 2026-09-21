# AP Pipeline — Phase 0 inventory & design lock

**Status:** Phase 0 **complete** (doc only; no code).  
**Approved through:** Phase 5 (AP Instructions soft packs). Phase 6 optional.  
**Companion:** [AP_PIPELINE_CONFIG_PHASES.md](./AP_PIPELINE_CONFIG_PHASES.md)

---

## 0.1 Skill registry (frozen for v1)

Source of truth today: `app/ap_skills/types.py` (`ALL_SKILL_ORDER`, `DEFAULT_SKILL_ORDER`), `app/ap_skills/runner.py` (`REGISTRY`).

| Skill ID | In `REGISTRY` | Default pipeline (`payload.skills` omitted) | Opt-in only | Role (one line) |
|----------|---------------|-----------------------------------------------|-------------|-----------------|
| `extract_invoice` | yes | yes (always first in default) | no | OCR/extract invoice JSON from document or payload |
| `po_lookup_sap` | yes | no* | yes | SAP/HANA PO lookup via connector |
| `po_lookup_quickbooks` | yes | no | yes | QuickBooks PO lookup |
| `po_lookup_sage` | yes | no | yes | Sage PO lookup |
| `po_match` | yes | yes | no | Match invoice to PO (uses connector artifacts or form master) |
| `gl_match` | yes | no | yes | GL coding / match scoring |
| `grn_match` | yes | no | yes | GRN line match vs invoice |
| `duplicate_detect` | yes | yes | no | Duplicate invoice detection |
| `vendor_validate` | yes | yes | no | Vendor master validation |
| `matter_validate` | yes | no | yes | Matter / engagement validation |
| `backorder_detect` | yes | yes | no | Backorder / line mismatch heuristics |
| `finalize_decision` | yes | yes | no | Aggregate scores → decision |
| `workflow_progress` | yes | no | yes | Workflow progress reporting (non-terminal) |
| `workflow_move_next` | yes | yes (last in default) | no | Advance workflow + HANA match side effects |

\* **EZOFIS exception (code today):** For tenant `b843b988-00ec-44e3-aca2-b8470133ef63`, if `po_match` is in the resolved list and `po_lookup_sap` is not, `ensure_ezofis_hana_po_lookup` inserts `po_lookup_sap` immediately before `po_match`. Effective default for EZOFIS ≈ `… → po_lookup_sap → po_match → …`.

**Canonical orders (code):**

- **Default pipeline:** `extract_invoice` → `po_match` → `duplicate_detect` → `vendor_validate` → `backorder_detect` → `finalize_decision` → `workflow_move_next`
- **Full deterministic order** (when building merged lists): see `ALL_SKILL_ORDER` in `types.py` (connector lookups before `po_match`, `workflow_progress` before `workflow_move_next`).

**Unknown IDs:** Rejected with `ApSkillError` (`resolve_skills`).

---

## 0.2 Knobs to expose in Catalog v1 (Pipeline config)

These are the **first** fields the Catalog schema should carry. Names below match **runtime keys already read** where possible.

### Skills

| Field | Type | Semantics |
|-------|------|-----------|
| `skills_order` | `string[]` | Ordered list of skill IDs to run when request omits `skills`. Must ⊆ `ALL_SKILLS`. |
| `skills_enabled` | `string[]` (optional v1) | If present, filter `skills_order` to this set (same order). If omitted, `skills_order` alone defines the run. |

**Phase 3 note:** Today `resolve_skills(..., enabled=...)` **ignores** `enabled`; tenant `ap_tenant_plans.enabled_skills` is loaded but not applied. Wiring `skills_enabled` is an explicit Phase 3 deliverable.

### Connector / resource defaults

Applied when job payload omits the field (injection in runner/context, not inside each skill file rewrite).

| Field | Maps to job / `ctx.thresholds` | Notes |
|-------|--------------------------------|-------|
| `default_connector_id` | `payload.connector_id` | EZOFIS: code default `f7636e21-1a0c-457c-a2b4-e28430705477` when tenant is EZOFIS and payload empty (`hana_po.resolve_hana_connector_id`). |
| `default_resource` | `payload.resource` / `thresholds.po_resource` | e.g. `SAP`, `HANA`, `QUICKBOOKS` — drives connector lookup skills. |

Per-connector threshold fallbacks (already in skills): `sap_connector_id`, `quickbooks_connector_id`, `sage_connector_id` — **defer to Phase 6** unless a tenant needs them in v1; v1 can use single `default_connector_id` + `default_resource`.

### Thresholds (global v1 — max 3 + existing extras documented)

Read from `ctx.thresholds` with **Settings fallback** (`app/config.py`):

| Threshold key | Settings default | Used by |
|---------------|------------------|---------|
| `approved` | `ap_approved_threshold` = **80** | `po_match`, `gl_match`, `grn_match`, decision helpers |
| `partial` | `ap_partial_threshold` = **50** | same |
| `amount_tolerance` | `ap_amount_tolerance` = **0.02** | `po_match` (absolute delta vs PO total) |

**Not in v1 UI (document only):** `matter_master_id` (`matter_validate`), line-match floor constants in code.

Use **absolute amount tolerance**, not `%`, in v1 JSON — align with existing `amount_tolerance` key (not `amount_tolerance_pct`).

### Feature flags (pipeline-level v1)

| Flag | Today | Proposed Catalog default |
|------|-------|---------------------------|
| `use_planner` | `Settings.ap_llm_planner` (env `AP_LLM_PLANNER`, default **false**); only when `payload.skills` is omitted | false |
| `force_hana_po_lookup` | Implicit **true** for EZOFIS via `ensure_ezofis_hana_po_lookup` | true for EZOFIS platform row; false elsewhere |

**Not moved to Catalog in v1:** `ap_dedupe_window_seconds`, `force_rerun` (stay request/env).

### LLM planner constraints (unchanged)

When `use_planner` is true and `payload.skills` is omitted: LLM may **reorder only** skills already in the resolved list; it cannot add skills not in that list (`maybe_reorder`).

---

## 0.3 Override rules (locked)

Evaluation order for a **new** AP run (`ApSkillRunner.run`):

```text
1. Dedupe short-circuit (payload.skills omitted, not force_rerun, recent completed run)
      → return stored run; no config read for skill list

2. Skill list resolution:
      a. payload.skills is a non-null list
            → EXACTLY that list (validated ⊆ ALL_SKILLS)
            → NO tenant/platform pipeline order
            → ensure_ezofis_hana_po_lookup STILL applies (injection before po_match)
            → maybe_reorder OFF (planner only when requested is None)

      b. payload.skills is null/omitted
            → tenant_ap_pipeline (Catalog, when AP_PIPELINE_FROM_DB=true) 
            OR until Phase 3: code DEFAULT_SKILL_ORDER
            → apply skills_enabled filter if configured
            → ensure_ezofis_hana_po_lookup
            → maybe_reorder if flag use_planner (Catalog or Settings)

3. Thresholds / connector defaults:
      payload fields (connector_id, resource, …) 
            > tenant pipeline JSON thresholds/defaults
            > platform pipeline JSON
            > existing ap_tenant_plans.thresholds (tenant DB) — **today only this path is live**
            > Settings (ap_*_threshold)

4. Env / settings-only (not overridden by Catalog v1):
      ap_dedupe_window_seconds, ap_llm_planner when Catalog flag absent
```

**Explicit non-goals:** Catalog config does **not** rewrite past `ap_runs`, artifacts, or tickets. Config changes affect **subsequent** runs only.

**Request wins for skills:** Console/manual `skills` textarea and workflow payloads that send `skills: [...]` always define the run list; pipeline config is for the “omit skills” path only.

---

## 0.4 Baseline gaps (important for Phase 1–3)

| Piece | Location | Behavior today |
|-------|----------|----------------|
| Default skill order | Code `DEFAULT_SKILL_ORDER` | Used when `skills` omitted |
| Tenant plan | `ap_tenant_plans` in **per-tenant** DB (`enabled_skills`, `thresholds`) | Runner loads plan; **only `thresholds` affect skills**; `enabled_skills` unused |
| Platform default | None in DB | N/A |
| Catalog pipeline tables | `platform_ap_pipeline`, `tenant_ap_pipeline`, `tenant_ap_pipeline_logs` | Schema Phase 1; platform row seeded Phase 2; runner reads gated by `AP_PIPELINE_FROM_DB` (Phase 3) |
| Console pipeline UI | AP mode → **AP Pipeline** inspector | Platform read-only + tenant edit/save (Phase 4) |

Phase 1–3 should **either** migrate `ap_tenant_plans` into Catalog **or** define a short dual-read period (thresholds from tenant DB until Catalog row exists). Default recommendation: **Catalog as console source of truth** (same as agent packs), one-time seed from code + optional import from existing `ap_tenant_plans`.

---

## 0.5 Rollback / restore

| Mechanism | Effect |
|-----------|--------|
| `AP_PIPELINE_FROM_DB=false` (default until Phase 3 cutover) | Ignore Catalog rows; skill list = code default (+ EZOFIS injection); thresholds still from `ap_tenant_plans` + payload + Settings as today |
| Empty or missing `tenant_ap_pipeline` row | Fall through to `platform_ap_pipeline`, then code |
| Bad JSON / unknown skill in stored config | Fail closed on read (log + fall back to platform/code — exact behavior to implement in Phase 3) |
| Disable planner | `use_planner: false` in platform row or `AP_LLM_PLANNER=false` |

No migration rollback required for Phase 0 (no schema).

---

## 0.6 Suggested v1 JSON shape (locked names)

```json
{
  "skills_order": [
    "extract_invoice",
    "po_match",
    "duplicate_detect",
    "vendor_validate",
    "backorder_detect",
    "finalize_decision",
    "workflow_move_next"
  ],
  "skills_enabled": null,
  "default_connector_id": null,
  "default_resource": null,
  "thresholds": {
    "approved": null,
    "partial": null,
    "amount_tolerance": null
  },
  "flags": {
    "use_planner": false,
    "force_hana_po_lookup": true
  }
}
```

Platform seed for EZOFIS (Phase 2): same order with `force_hana_po_lookup: true` and optional default connector GUID for documentation parity with `hana_po.HANA_PO_CONNECTOR_ID`.

---

## Phase 0 exit checklist

- [x] Skill registry table (default vs opt-in)
- [x] v1 knobs list (skills, connector defaults, 3 thresholds, 2 flags)
- [x] Override rules documented
- [x] Rollback via feature flag + fallbacks
- [x] Existing `ap_tenant_plans` gap called out

**Next step (optional):** Phase 6 — hardening (reset to platform, last-run debug, per-skill thresholds).
