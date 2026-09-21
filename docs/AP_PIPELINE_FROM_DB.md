# AP Pipeline from Catalog (Phase 2)

**Goal:** Skill order / enable / thresholds / flags come from Catalog, not hard-coded `DEFAULT_SKILL_ORDER` alone.

## Flag

| Setting | Default | Rollback |
|---------|---------|----------|
| `AP_PIPELINE_FROM_DB` | **`true`** | Set `false` on Agents → restart |

Local compose already sets `AP_PIPELINE_FROM_DB=true`. Azure Agents should set the same App Setting (see `deploy/AZURE_AGENTS_APP_SETTINGS.md`).

## What Catalog controls

| Knob | Platform table | Tenant override |
|------|----------------|-----------------|
| `skills_order` | `platform_ap_pipeline` | `tenant_ap_pipeline` |
| `skills_enabled` | optional filter | optional filter |
| Thresholds | `approved` / `partial` / `amount_tolerance` | merge over platform |
| `flags.use_planner` | default false | tenant |
| `flags.force_hana_po_lookup` | **default false** (no silent inject) | set true only if you want code inject when Workflow asks SAP/HANA |

Explicit `payload.skills` still wins over Catalog order.

## No silent HANA force

With the flag on, `po_lookup_sap` is **not** auto-injected unless:

1. Tenant/platform sets `flags.force_hana_po_lookup: true` **and** Workflow/payload asks for SAP/HANA, or
2. Core/Hangfire start payload already lists `po_lookup_sap` in `skills`.

InternalForm / Ezofis PO master stays on `/masters/po` (no connector inject).

## Console

1. Open Agents Console → **AP** → **Pipeline**.
2. Confirm platform JSON matches intended default order.
3. Select tenant → edit enable/order/flags → **Save** (writes `tenant_ap_pipeline` + audit log).
4. Run a new AP job **without** an explicit `skills` list → `skills_run` should match the Console order.

API:

- `GET /console/ap-pipeline/platform`
- `GET /console/ap-pipeline/tenant?tenant_id=…`
- `PUT /console/ap-pipeline/tenant`

## Seed

On Agents boot, `seed_platform_ap_pipeline` upserts `pipeline_key=default` from `app/ap_pipeline/defaults.py` (includes `force_hana_po_lookup: false`). Restart after deploy so live Catalog picks up the new platform flags.

## Exit check

Change tenant `skills_enabled` (e.g. drop `po_match`) in Console → next omit-skills AP run skips that skill.
