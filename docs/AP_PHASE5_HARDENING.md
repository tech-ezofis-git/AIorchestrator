# Phase 5 — Hardening (exit checklist + stuck tickets)

**Status:** Phases 0–4 delivered; Phase 5 = tests + operational guidance.  
**Exit:** Automated matrix green; operators know how to clear stuck AP AGENT tickets and smoke PoMaster paths.

## Automated coverage

Run:

```bash
py -3 -m pytest tests/test_ap_phase5_hardening.py tests/test_ap_instructions.py tests/test_ap_pipeline_resolve.py tests/test_hana_po_master_gate.py tests/test_workflow_move_next_not_advanced.py tests/test_ap_pipeline_policy.py -q
```

| Area | What is locked |
|------|----------------|
| Pack-primary prompts | Catalog/disk AP pack wins over code fallback (Phase 1) |
| Pipeline-from-DB | Tenant Catalog order drives `skills_run`; flag `false` rolls back to code (Phase 2) |
| PoMaster matrix | InternalForm vs SAP/HANA/QB/Sage routing helpers (Phase 3) |
| Policy | Thresholds / review labels / step name from Catalog (Phase 4) |
| Move-next | `not advanced` → `ok=False` (progress FAILED, not fake COMPLETED) |

## Deploy order (live)

1. Merge/deploy **Core** (PoMaster enricher + move-next advance for Matched / Partially Matched / …).
2. Deploy **Agents** (`AP_PIPELINE_FROM_DB=true`, Catalog seed on boot).
3. Restart multi-container app; wait until `/health` is **200**.
4. Cancel or wipe **stuck** tickets still on AP AGENT 1 (below).
5. Smoke one **new** ticket per PoMaster path (InternalForm, SAP, QB).

## Stuck tickets — prefer cancel (safe)

If Hangfire/`ApAgentJobProgress` shows COMPLETED but the ticket is still on **AP AGENT 1**:

1. Confirm Core PR with `IsApAgentDecisionReview` / advance-on-review is live.
2. Confirm Agents build includes move-next “not advanced” → failure.
3. **Do not reuse** old stuck request numbers for validation.
4. In Workflow UI: **Cancel** (or Reject) the stuck instances (e.g. REQ-87, REQ-88).
5. Start a **new** invoice so Core stamps a fresh payload with `master_source` / `master_form_id` or connector skills.

## Full wipe (destructive — EZOFIS lab only)

Only when you intentionally reset the tenant AP demo data:

```bash
# Preview
py -3 scripts/wipe_ezofis_ap_tickets.py --preview

# Execute (tenant DB + Redis) — irreversible for those rows
py -3 scripts/wipe_ezofis_ap_tickets.py --execute
```

SQL alternative (tenant app DB, not Catalog): `deploy/WIPE_EZOFIS_AP_TICKETS.sql` — preview `SELECT`s first, then `BEGIN`…`COMMIT`.

## Smoke checklist (after deploy)

| # | Action | Pass |
|---|--------|------|
| 1 | `GET /health` (or `cloud.ezofis.com/api/health`) → 200 | ☐ |
| 2 | Console → AP → Pipeline shows platform thresholds + policy | ☐ |
| 3 | Console → AP → Instructions editable; extract reflects pack after save | ☐ |
| 4 | Workflow PoMaster = InternalForm + form id → new ticket leaves AP AGENT | ☐ |
| 5 | PoMaster = SAP + connector → `po_lookup_sap` in run / matched path | ☐ |
| 6 | Cancel remaining stuck AP AGENT tickets; no reuse | ☐ |

## Related docs

- [AP_INSTRUCTIONS_CONSOLE.md](./AP_INSTRUCTIONS_CONSOLE.md) — Phase 1
- [AP_PIPELINE_FROM_DB.md](./AP_PIPELINE_FROM_DB.md) — Phase 2
- [AP_POMASTER_WORKFLOW_ONLY.md](./AP_POMASTER_WORKFLOW_ONLY.md) — Phase 3
- [AP_PHASE4_SHRINK_CODE.md](./AP_PHASE4_SHRINK_CODE.md) — Phase 4
- [deploy/AZURE_AGENTS_APP_SETTINGS.md](../deploy/AZURE_AGENTS_APP_SETTINGS.md)
