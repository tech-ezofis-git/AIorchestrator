# Phase 4 — Shrink code-based AP Agent (Catalog product policy)

**Goal:** Python keeps deterministic runners (match math, HTTP, ezfb SQL). Product policy (thresholds, review labels, workflow step name, line-match floor) lives in Catalog.

## Remaining hardcodes (inventory)

| Item | Was | Now |
|------|-----|-----|
| LLM extract/planner prompts | Python strings | Catalog AP Instructions (Phase 1) |
| Skill order / enable / HANA inject flag | `DEFAULT_SKILL_ORDER` + code | Catalog pipeline (Phase 2) |
| PO master routing | EZOFIS tenant / HANA GUID | Workflow payload (Phase 3) |
| Score thresholds | Settings env defaults | Catalog `thresholds` (seeded 80 / 50 / 0.02) |
| Review UI strings | `_REVIEW_LABELS` in Python | Catalog `policy.review_labels` |
| Workflow step name | Settings `AP_AGENT_WORKFLOW_STEP_NAME` | Catalog `policy.workflow_step_name` |
| Line match floor | `DEFAULT_LINE_MATCH_FLOOR` | Catalog `policy.line_match_floor` |
| Match math / HTTP / ezfb SQL | Python | **stays in code** (deterministic) |

`HANA_PO_CONNECTOR_ID` remains only as an optional fingerprint for “is this the known HANA connector?”, never as a silent default.

## Catalog `policy` shape

```json
{
  "thresholds": { "approved": 80, "partial": 50, "amount_tolerance": 0.02 },
  "flags": { "use_planner": false, "force_hana_po_lookup": false },
  "policy": {
    "workflow_step_name": "AP AGENT 1",
    "review_labels": {
      "MATCHED": "Matched",
      "PARTIALLY_MATCHED": "Partially Matched",
      "NOT_MATCHED": "Not Matched",
      "NON_INVOICE": "Non-Invoice",
      "DUPLICATE": "Not Matched"
    },
    "line_match_floor": 0.5
  }
}
```

Edit via Console → AP → **Pipeline** (tenant or platform JSON). Seed refreshes platform on Agents boot.

## Exit

Change `policy.workflow_step_name` or `thresholds.approved` in Catalog → next AP run uses the new values without an Agents code deploy.

## Optional later (Phase 4.4 / Phase 5)

Rule engine in Catalog for “when to inject lookup skill” (today: Core stamps skills, or Catalog `force_hana_po_lookup`).
