# AP Instructions — edit in Console (Phase 1)

**Goal:** AP extract / planner LLM guidance comes from **Catalog packs** (same idea as OCR), not from hard-coded Python prompts.

## Where to edit

1. Open Agents **Console**.
2. Select agent **AP** (or AP Configuration → **Instructions** tab).
3. Edit:
   - **Platform defaults** — product-wide `SKILL.md` + rules (`extract`, `planner`, `soft-guidance`).
   - **Tenant overrides** — extra skills/rules for one tenant (active rules append to the system prompt).
4. Save. Next AP run that calls the LLM (extract structuring or planner reorder) picks up the new text **without an Agents code deploy**.

API equivalents (same as other agents):

| Method | Path |
|--------|------|
| GET | `/console/agent-packs/ap/defaults` |
| GET / PUT | `/console/agent-packs/ap/...` (tenant skill/rule routes — same as Summary/OCR) |

## What the pack controls

| Piece | Role |
|-------|------|
| `skills/ap/SKILL.md` | Scope / non-goals (seeded to `platform_agent_skills`) |
| `rules/extract.mdc` | Invoice JSON contract + field guidance for `extract_invoice` |
| `rules/planner.mdc` | Skill reorder JSON contract for optional planner |
| `rules/soft-guidance.mdc` | Anti-hallucination / evidence rules |
| Tenant rules | Appended when `tenant_id` is set on the job |

## Runtime behavior

- `resolve_system_prompt` loads Catalog (platform + tenant) via `get_agent_skill("ap")`.
- If the pack is present → **that text is the full system prompt** (OCR pattern).
- If Catalog/disk pack is missing → short code fallback in `extract_invoice` / `planner` only.
- Python skills (`po_match`, connector lookup, workflow move) are **not** replaced by Instructions.

## Seed

On Agents boot, `seed_platform_packs_from_disk` upserts disk `skills/ap/**` into Catalog (agent_slug=`ap`), including the new `extract` rule. Restart Agents (or re-seed) after changing disk packs so platform rows refresh.

## Smoke (extract-only)

1. Edit platform or tenant AP Instructions (e.g. add a distinctive sentence in `extract` rule).
2. Run an AP job with OCR text (or Console extract path) that hits `_structure_with_llm`.
3. Confirm the LLM system prompt / behavior reflects the edit (no Agents image rebuild required for Catalog edits).

## Rollback

- Disk pack still works when Catalog is empty or `AGENT_PACKS_FROM_DB=false`.
- Code fallback remains if both Catalog and disk packs fail to load.
