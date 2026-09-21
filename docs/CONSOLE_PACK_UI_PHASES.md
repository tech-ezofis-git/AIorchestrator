# Console pack UI + sample tenant customs (phased)

**Status:** Phases **A → B → C** implemented (2026-09-17).  
**Goal:** Show Catalog defaults (read-only) + tenant editable skills/rules cleanly; seed sample customs from prior Summary work; no nested/visible scroll clutter.

---

## Current findings (why UI feels broken)

1. **Tenant customs table is empty** — local SQLite has 0 rows; Catalog `tenant_agent_*` is empty. Nothing to edit until we seed/upload.
2. **UI still assumes SQLite integers** — Catalog returns UUID `id` and boolean `is_active`. Console uses `Number(id)` and `Number(is_active) === 1`, so Edit/Save/active badges break for Catalog rows.
3. **Platform defaults only appear in Summary mode** — right “Summary pack” panel; Platform tab = read-only Catalog assets; Tenant tab needs console **tenant** selected.
4. **Layout** — pack inspector uses nested `overflow` regions; we will tighten so one panel scrolls cleanly without double scrollbars / clipped content.

Previous agent **run results** (tickets/OCR/summary outputs) are **not** rewritten by packs.

---

## Phase A — Seed sample tenant customs (EZOFIS)

**What:** Insert a few **editable** Summary customs into Catalog for the EZOFIS tenant (same ideas we used in console work: narrative tone, highlight preference, optional “local check” rule).

| Item | Kind | Purpose |
|------|------|---------|
| `custom1` skill | `.md` | Short tenant Summary emphasis (parties / amounts) |
| `custom1` rule | `.mdc` | Prefer bold+underline highlights for key IDs |
| optional `custom2` rule | `.mdc` | Soft tone / “do not invent fields” reminder |

**Tables:** `tenant_agent_skills`, `tenant_agent_rules` (+ log rows).  
**Exit:** `SELECT` for that `tenant_id` shows rows; Tenant tab lists them after Phase B.

---

## Phase B — Fix console CRUD for Catalog (must before trusting UI)

**What:** In `console.html` (Summary pack inspector):

- Treat `id` as **string** (UUID), never `Number(id)`.
- Treat `is_active` as true for `true` / `1` / `"1"` / `"true"`.
- Keep Edit / Save / Enable / Disable / Delete / Replace working against `/console/summary-skills/*`.
- Status line counts use the same `is_active` helper.

**Exit:** Upload + edit + disable a custom rule against local Catalog; reload shows correct active badge.

---

## Phase C — Pack UI layout (no visible scroll mess)

**What:** Careful CSS/structure only for the pack inspector:

- Single scroll region inside Platform viewer body and Tenant custom list (not page + panel + nested fighting).
- Platform: asset list + read-only body always visible without horizontal scroll.
- Tenant: banner + upload sticky; custom cards scroll in one column; change log collapsed by default.
- No overlapping scrollbars; avoid clipping Edit textarea; respect reduced-motion.

**Exit:** Summary mode → Platform shows defaults; Tenant shows customs; no double scrollbar / cut-off controls on desktop (~1280+) and reasonable laptop height.

---

## Phase D — Multi-agent pack inspector (done 2026-09-17)

- Console pack panel opens for **summary / ocr / insight / prompt / pdf** (not AP).
- CRUD via `/console/agent-packs/{agent}/…` (Summary aliases kept).
- Sample EZOFIS customs seeded for each pack agent (`scripts/seed_ezofis_summary_customs.py`).

---

## Confirm checklist

- [x] Approved **A → B → C** (2026-09-17)
- [x] A seeded EZOFIS Summary customs (`custom1` skill, `custom1`+`custom2` rules)
- [x] B UUID / `is_active` console CRUD + tenant sync
- [x] C pack inspector scroll/layout polish

**How to check locally:** open http://127.0.0.1:8010/console → Summary mode → set tenant `b843b988-00ec-44e3-aca2-b8470133ef63` → **Platform** (defaults) / **Tenant** (editable customs).
