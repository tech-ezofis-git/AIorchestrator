-- Code-review findings #2 (duplicate AP runs) and #3 (a run reporting
-- "completed" regardless of extraction/decision quality).
--
-- `data_quality` carries a JSON summary computed by ApSkillRunner (extract
-- completeness, whether OCR/masters were mocked, ...); `status` can now
-- also be "completed_low_confidence" (still TEXT, no enum change needed).
--
-- The partial unique index prevents two concurrently-"running" ap_runs
-- rows for the same (tenant_id, item_key) — a genuinely concurrent
-- duplicate submission fails the INSERT outright (ApStore.create_run
-- catches the conflict and looks up the in-flight run instead of creating
-- a second one). It does NOT prevent a *sequential* retry after a run has
-- finished — that's handled by ApSkillRunner's dedupe-window check against
-- the most recent completed run, not by this index.
ALTER TABLE ap_runs ADD COLUMN IF NOT EXISTS data_quality JSONB;

CREATE UNIQUE INDEX IF NOT EXISTS ap_runs_tenant_item_active_idx
    ON ap_runs (tenant_id, item_key) WHERE status = 'running';
