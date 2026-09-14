-- Wipe EZOFIS Accounts Payable tickets / AP residue (tenant app DB).
-- Run against the EZOFIS tenant ConnectionString database (not catalog).
-- Preview first, then execute in one transaction.

BEGIN;

-- Preview: every table this script will wipe
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema = 'workflow'
  AND table_type = 'BASE TABLE'
  AND (
        table_name IN (
          'WorkflowInstanceLookup',
          'WorkflowApprovals',
          'ApAgentJobProgress',
          'jiraCreateIssue'
        )
     OR table_name LIKE 'workflow_instances_%'
     OR table_name LIKE 'workflow_step_instances_%'
     OR table_name LIKE 'workflow_instance_slas_%'
     OR table_name LIKE 'workflow_instance_user_state_%'
     OR table_name LIKE 'transaction_%'
     OR table_name LIKE 'process_form_%'
     OR table_name LIKE 'process_addon_%'
     OR table_name LIKE 'inbox_%'
     OR table_name LIKE 'sent_%'
     OR table_name LIKE 'completed_%'
     OR table_name LIKE 'workflow_comments_%'
     OR table_name LIKE 'workflow_attachments_%'
     OR table_name LIKE 'workflow_forms_%'
     OR table_name LIKE 'workflow_tasks_%'
     OR table_name LIKE 'workflow_signatures_%'
     OR table_name LIKE 'workflow_documents_%'
     OR table_name LIKE 'workflow_emails_%'
     OR table_name LIKE 'workflow_ai_validations_%'
     OR table_name LIKE 'agent_data_validation_%'
     OR table_name LIKE 'workflow_pdf_annotations_%'
     -- extras often left behind after ticket wipe
     OR table_name LIKE 'workflow_notifications_%'
     OR table_name LIKE 'workflow_activity_%'
     OR table_name LIKE 'workflow_audit_%'
     OR table_name LIKE 'workflow_history_%'
     OR table_name LIKE 'workflow_request_%'
     OR table_name LIKE 'ap_agent_%'
     OR table_name LIKE 'ApAgent%'
  )
ORDER BY table_name;

-- Wipe ticket / instance / transaction / mailbox rows
DO $$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT table_schema, table_name
    FROM information_schema.tables
    WHERE table_schema = 'workflow'
      AND table_type = 'BASE TABLE'
      AND (
            table_name IN (
              'WorkflowInstanceLookup',
              'WorkflowApprovals',
              'ApAgentJobProgress',
              'jiraCreateIssue'
            )
         OR table_name LIKE 'workflow_instances_%'
         OR table_name LIKE 'workflow_step_instances_%'
         OR table_name LIKE 'workflow_instance_slas_%'
         OR table_name LIKE 'workflow_instance_user_state_%'
         OR table_name LIKE 'transaction_%'
         OR table_name LIKE 'process_form_%'
         OR table_name LIKE 'process_addon_%'
         OR table_name LIKE 'inbox_%'
         OR table_name LIKE 'sent_%'
         OR table_name LIKE 'completed_%'
         OR table_name LIKE 'workflow_comments_%'
         OR table_name LIKE 'workflow_attachments_%'
         OR table_name LIKE 'workflow_forms_%'
         OR table_name LIKE 'workflow_tasks_%'
         OR table_name LIKE 'workflow_signatures_%'
         OR table_name LIKE 'workflow_documents_%'
         OR table_name LIKE 'workflow_emails_%'
         OR table_name LIKE 'workflow_ai_validations_%'
         OR table_name LIKE 'agent_data_validation_%'
         OR table_name LIKE 'workflow_pdf_annotations_%'
         OR table_name LIKE 'workflow_notifications_%'
         OR table_name LIKE 'workflow_activity_%'
         OR table_name LIKE 'workflow_audit_%'
         OR table_name LIKE 'workflow_history_%'
         OR table_name LIKE 'workflow_request_%'
         OR table_name LIKE 'ap_agent_%'
         OR table_name LIKE 'ApAgent%'
      )
  LOOP
    EXECUTE format(
      'TRUNCATE TABLE %I.%I RESTART IDENTITY CASCADE',
      r.table_schema,
      r.table_name
    );
    RAISE NOTICE 'truncated %.%', r.table_schema, r.table_name;
  END LOOP;
END $$;

-- Keep document templates; remove files attached to tickets
DELETE FROM workflow."WorkflowDocuments"
WHERE "WorkflowInstanceId" IS NOT NULL;

-- Optional: unlink repository files from deleted tickets (keeps the files)
DO $$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'repository'
      AND table_type = 'BASE TABLE'
      AND table_name LIKE 'items_%'
      AND table_name NOT LIKE '%\_stage' ESCAPE '\'
      AND table_name NOT LIKE '%\_history' ESCAPE '\'
  LOOP
    IF EXISTS (
      SELECT 1
      FROM information_schema.columns
      WHERE table_schema = 'repository'
        AND table_name = r.table_name
        AND lower(column_name) = 'workflow_instance_id'
    ) THEN
      EXECUTE format(
        'UPDATE repository.%I SET workflow_instance_id = NULL WHERE workflow_instance_id IS NOT NULL',
        r.table_name
      );
      RAISE NOTICE 'unlinked workflow_instance_id on repository.%', r.table_name;
    END IF;
  END LOOP;
END $$;

-- AP repository + form tables used by Accounts Payable
TRUNCATE TABLE repository.items_a6169a5c RESTART IDENTITY CASCADE;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'dbo' AND table_name = 'ezfb_6e45749f_items'
  ) THEN
    TRUNCATE TABLE dbo.ezfb_6e45749f_items RESTART IDENTITY CASCADE;
  ELSIF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'ezfb_6e45749f_items'
  ) THEN
    TRUNCATE TABLE public.ezfb_6e45749f_items RESTART IDENTITY CASCADE;
  END IF;
END $$;

-- Orchestrator AP run residue (if present on this same DB)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='ap_credit_ledger') THEN
    TRUNCATE TABLE public.ap_credit_ledger RESTART IDENTITY CASCADE;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='ap_skill_artifacts') THEN
    TRUNCATE TABLE public.ap_skill_artifacts RESTART IDENTITY CASCADE;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='ap_runs') THEN
    TRUNCATE TABLE public.ap_runs RESTART IDENTITY CASCADE;
  END IF;
END $$;

COMMIT;

-- ALSO run on database ezofis_Tenant_b843b988 (AP artifacts host) if different:
--   TRUNCATE TABLE public.ap_credit_ledger, public.ap_skill_artifacts, public.ap_runs RESTART IDENTITY CASCADE;
--
-- Redis (agents container):
--   redis-cli -u "$REDIS_URL" FLUSHDB
--   # or: docker compose exec redis redis-cli FLUSHDB
