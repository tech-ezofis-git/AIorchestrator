# EZOFIS Report Agent — full details and scope reset

This is the current Report Agent as implemented, plus why the **scope model has to be redesigned**. Use this file as the brief for the next rebuild.

Console: `http://localhost:8011/console` (Docker `8011 → 8000`).  
Live tenant used for checks: `b843b988-00ec-44e3-aca2-b8470133ef63`.

**Hard rule today (and it should stay):** the model never writes or runs SQL. Python chooses columns, locks names, builds SQL, or counts EZOFIS catalogs in memory.

---

## 1. Product intent (what we wanted)

A real **Report Builder + Report Library**, not hardcoded catalog cards.

1. Pick a **scope** (all workflows, one workflow, all repositories, one repository).
2. Type a **description** (or click a chip: Aging, SLA, Total Inbox, …).
3. **Generate Prompt** analyzes the **live schema of that scope** and returns an editable short spec (title, columns, filters).
4. User can add/remove columns, add a formula, save to the library.
5. **Run Report** returns a table whose columns **match the spec**.

What users actually need:

| Scope | What they want to report on | Example reports |
|---|---|---|
| All Workflows | Shared instance fields across every workflow | Aging, SLA, Total Inbox, tickets processed |
| One workflow | **That workflow’s form fields only** | RFQ: Company_Name, Order_Number, Date, Total |
| All Repositories | Shared document fields across every folder | Total documents, document aging, retention |
| One repository | **That folder’s designer fields only** | HR Records: EmployeeName, FileName, PayslipMonth |

RFQ must never return Invoice_No / PO_Number. Those belong to Accounts Payable, not RFQ.

---

## 2. Why the full scope has to change again

The agent currently uses **three different “schemas” that do not agree**.

| World | Used by | What it contains |
|---|---|---|
| **EZOFIS REST** | Phase 1 generate, pickers, All-* run | Live workflows, formJson, repos, instances, items |
| **Tenant EZOFIS SQL** | Intended Phase 2 lock + SQL | `dbo.ezfb_*_items`, `repository.items_*` — **often unreachable from Docker** |
| **Local orchestrator Postgres** | Phase 2 lock fallback | Catalog tables like `workflow.Workflows` (`Name`, `IsDeleted`) — **not report rows** |

Phase 1 looks at EZOFIS. Phase 2 (specified workflow/repo) locks against local Postgres. Lock then:

1. Drops `ezfb_787372d8_items` (unknown).
2. Drops `Company_Name` (unknown).
3. Keeps `COUNT(*)` if present, **or** redirects to `workflow.Workflows`.
4. UI shows **one column** (`Name` = `Accounts Payable`).

That is not a prompt/skill bug. **Scope and execute source are split.**

A second scope bug: **Accounts Payable recipes are typed as `specific_workflow`**. If someone picks RFQ and types “Aging”, AP keywords can still collide, or generic fallback picks one identifier field. Specified-workflow reports are not form-grounded enough, and they cannot execute even when Phase 1 columns are correct.

A third scope bug: **All Workflows / All Repositories were implemented as virtual SQL tables** (`workflow_instances_all`, `items_all`) that do not exist in any database. Run only works because we added a special Path A that skips SQL. Specified scopes never got the same execute path.

**Proposed durable rule:** one scope → one schema → same schema for Generate and Run. EZOFIS is the system of record when tenant SQL is down.

---

## 3. Current UI (console)

Panel: `#reportPanel` in `app/static/console.html`.

User flow:

1. Report Type select (`#reportTypeSelect`) — four types from `GET /api/report-agent/types`.
2. Workflow or repository select (shown only for specified types) from `GET /api/report-agent/scope-options`.
3. Description + example chips (`#reportExampleChips`).
4. Tenant ID (`#reportTenant`).
5. **Generate Prompt** → fills title, objective, column chips, filter, sort, warnings.
6. User can +Add column from `availableColumns`, apply formula, change filter/sort.
7. **Save** → `saved_reports`.
8. **Run Report** → table in `#reportPlanPreview`.

Buttons call:

- Generate → `POST /api/report-agent/generate-prompt`
- Run → `POST /api/report-agent/run` with `reportSpec` (camelCase: `sourceTable`, `groupBy`, `availableColumns`)

---

## 4. Current report types (scope registry)

`app/report_agent/report_types.py`

| Key | UI name | Required | Phase 1 schema today | Phase 2 execute today |
|---|---|---|---|---|
| `all_workflows` | All Workflows | description | Virtual instance fields | Path A: EZOFIS instances, **works** |
| `specific_workflow` | Specified Workflow | `workflowName` | EZOFIS formJson | Path B: local SQL lock, **broken** |
| `all_repositories` | All Repositories | description | Virtual document fields | Path A: EZOFIS items, **works** |
| `specific_repository` | Specified Repository | `repositoryName` | EZOFIS designer fields | Path B: local SQL lock, **broken** |

`schema_focus` still lists SQL prefixes (`ezfb_`, `inbox_`, `items_`). That is leftover from the “dump tenant information_schema” design. It is not how Phase 1 actually loads columns anymore.

---

## 5. Architecture (as built)

```text
Console / REST
      │
      ├─ GET /types, /catalog, /scope-options, /reports
      │
      ├─ POST /generate-prompt     PHASE 1
      │     1. validate report type
      │     2. schema:
      │          EZOFIS API  ──► form / designer / virtual all-*
      │          overlay or fallback ──► tenant/local information_schema
      │     3. LLM (pack report-prompt)  OR  Python recipe fallback
      │     4. align_spec_to_intent (recipes always win for Aging/Inbox/SLA/…)
      │     5. enrich_spec_with_schema
      │     → reportSpec
      │
      └─ POST /run                 PHASE 2
            if sourceTable in {workflow_instances_all, items_all}
               PATH A  catalog_execute.py  (EZOFIS GET instances/items)
            else
               PATH B  LLM reportDefinition
                       merge spec
                       lock against LOCAL information_schema
                       SQL → execute
```

Entry points:

| Piece | File |
|---|---|
| HTTP | `app/main.py` `/api/report-agent/*` |
| Engine | `app/report_agent/service.py` `ReportAgentService` |
| Chat wrapper | `app/agents/report_agent.py` (calls the service only) |
| Console | `app/static/console.html` |

---

## 6. APIs

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/report-agent/types` | Four report types |
| GET | `/api/report-agent/catalog` | Recipe metadata (not library cards) |
| GET | `/api/report-agent/scope-options?scope=workflows\|repositories&tenantId=` | Picker names from EZOFIS |
| POST | `/api/report-agent/generate-prompt` | Phase 1 |
| POST | `/api/report-agent/run` | Phase 2 |
| GET/POST/PATCH/DELETE | `/api/report-agent/reports` | Saved library |
| POST | `/api/report-agent/reports/{id}/run` | Run a saved spec |

### Phase 1 request

```json
{
  "reportType": "all_workflows | specific_workflow | all_repositories | specific_repository",
  "description": "Aging",
  "workflowName": "RFQ Process",
  "repositoryName": "HR Records",
  "tenantId": "…"
}
```

### Phase 1 `reportSpec` (what the UI edits)

```json
{
  "title": "Aging",
  "objective": "…",
  "reportType": "all_workflows",
  "columns": [{ "key": "Aging_Bucket", "label": "Aging Bucket", "formula": "CASE …" }],
  "filters": [{ "field": "completedAtUtc", "op": "is_null", "value": null }],
  "groupBy": ["Aging_Bucket", "workflowName"],
  "sort": { "field": "Request_Count", "direction": "desc" },
  "availableColumns": ["workflowName", "status", "createdAtUtc", "…"],
  "sourceSchema": "workflow",
  "sourceTable": "workflow_instances_all",
  "warnings": []
}
```

### Phase 2 request

```json
{
  "reportType": "all_workflows",
  "reportSpec": { },
  "reportPrompt": "optional override",
  "tenantId": "…",
  "page": 1,
  "pageSize": 25,
  "includeDebug": true
}
```

---

## 7. Schema sources in detail

### 7.1 EZOFIS REST (Phase 1 + Path A)

`app/integrations/ezofis_client.py`

| Method | API | Used for |
|---|---|---|
| `list_workflows` | `POST /workflow/all` | Picker + all-workflows + specified match |
| `get_form` | `GET /form/{id}` | Specified workflow columns |
| `list_workflow_instances` | `GET /workflows/{id}/instances` | Path A run |
| `list_repositories` | `GET /repositories` | Picker + all-repos |
| `get_repository` | `GET /repositories/{id}` | Specified repo designer fields |
| `list_repository_items` | `GET /repositories/{id}/items` | Path A run |

Form JSON → columns: `app/report_agent/form_schema.py` `extract_form_fields`  
(skips TABLE children and calc AST junk; uses control labels).

Repo designer → columns: `extract_repository_fields` (`sqlColumnName`, prepend FileName).

### 7.2 Virtual tables (All-* only)

`metadata_service.py`

**`workflow.workflow_instances_all`**

- workflowName, status, startedBy, createdAtUtc, startedAtUtc, completedAtUtc, id, workflowId

**`repository.items_all`**

- Repository_Name, FileName, FileVersion, CreatedBy, CreatedAt, Status, id

These tables **do not exist in SQL**. They are a contract for the spec. Path A executes them in Python.

Instance DTO keys from EZOFIS: `workflowName`, `status`, `startedBy`, `createdAtUtc`, `startedAtUtc`, `completedAtUtc`, `id`, `workflowId`.

Do **not** take sample repository item keys as all-repo columns (AP invoice fields can appear on RFQ folder sample items).

### 7.3 Tenant / local SQL (Phase 2 Path B)

`get_database_schema()` reads `information_schema.columns`.

Then `resolve_report_source()` tries `workflow.Workflows` + form id → `dbo.ezfb_{formId8}_items`.

From Docker this usually fails (`gaierror` on Azure catalog, missing `ezofis_Tenant_…`). Fallback is **local orchestrator DB**.

Lock then treats EZOFIS form tables as unknown and falls back to `workflow.Workflows`.

---

## 8. Phase 1 generate (step by step)

`ReportAgentService.generate_prompt`

1. `validate_report_type_input`
2. `resolve_report_source(db)` if tenant DB exists
3. `_schema_from_ezofis(...)` — **this is what actually fills RFQ/HR/all-* columns**
4. Overlay physical columns if the same table exists in SQL
5. `scope_schema` → approved slice
6. System prompt = pack `report-prompt` (or `_FALLBACK_PROMPT_SYSTEM`)
7. User payload: reportType, description, catalogNames, preferredSource, live column list, `catalog_prompt_hint`
8. LLM `reportSpec` JSON — or text wrapped as objective — or total failure
9. If no columns: `fallback_report_spec` (recipes)
10. `align_spec_to_intent` — rebuilds Aging/Inbox/SLA/… even if LLM returned 2 columns
11. `enrich_spec_with_schema`
12. Return `reportPrompt` (prose built from spec) + `reportSpec`

If the model is down (typical on this tenant):

`Prompt model failed (… provider is currently unavailable.); used schema-backed short prompt.`

Recipes still produce Aging / Inbox / SLA correctly for All Workflows.

For specified RFQ with description `RFQ list`, no recipe matches → generic picker may emit **one** column (`Company_Name`) even though `availableColumns` has the full form. That is a Phase 1 gap, separate from lock.

---

## 9. Phase 2 run (step by step)

`ReportAgentService.run_report`

1. Parse `reportSpec` (camelCase aliases).
2. `align_spec_to_intent` again using spec title.
3. If `all_workflows` / `all_repositories` and `sourceTable` empty, default virtual table.
4. **Path A** if `sourceTable` is `workflow_instances_all` or `items_all`:
   - `catalog_execute.execute_catalog_spec`
   - fetch every workflow’s instances (or repo items)
   - aggregate (aging 0-5/6-15/16-30/30+, inbox open, SLA 5-day, document counts)
   - return spec columns + rows  
   Warning: `Counted live EZOFIS workflow instances across the tenant catalog.`
5. **Path B** otherwise:
   - `get_database_schema(local)`
   - LLM `reportDefinition` (pack `report-run`)
   - `merge_spec_into_definition`
   - `lock_report_definition` ← **this is the 1-column machine**
   - `generate_definition_sql` + `validate_read_only_sql` + `execute_report_query`

Lock behavior (`definition_lock.py`):

- Drop unknown source tables.
- Drop unknown column sources / CASE that mention unknown cols.
- **Keep `COUNT(*)` / `COUNT(1)` even if the table was dropped.**
- If nothing remains, redirect to a catalog fact such as `workflow.Workflows`.

---

## 10. Model, skills, recipes

### Model

Catalog agent slug: **`report`**.  
`apply_tenant_agent_llm(catalog, tenant_id, "report")`.

| When | Pack | Output |
|---|---|---|
| Phase 1 | `report-prompt` | `reportSpec` JSON, no SQL |
| Phase 2 Path B only | `report-run` | `reportDefinition` JSON, no SQL |

Not used for: pickers, form extraction, SQL, Path A counts, recipe rebuild.

### Skills (instructions only)

```text
skills/report-prompt/SKILL.md
  rules/schema-grounding.mdc
  rules/output-contract.mdc
  rules/prompt-structure.mdc

skills/report-run/SKILL.md
  rules/schema-grounding.mdc
  rules/definition-contract.mdc
  rules/safety.mdc
  rules/formatting.mdc
```

Load order (`agent_packs/overlay.py`): Catalog DB pack → tenant extras → disk.

`LoadedSkill.system_prompt` = SKILL.md + alwaysApply rules.

**Editing SKILL.md cannot fix specified-workflow 1-column.** Lock ignores the model when the table is missing.

Both packs say: never invent fields; never use `Workflows` / `Repositories` / `wForm` as the row source. Path B then **does exactly that** after lock failure.

### Recipes (Python — real behavior)

`app/report_agent/report_catalog.py`

| Intent key | Type | Kind | Notes |
|---|---|---|---|
| ap_aging, pending_approval, vendor_outstanding, approved_total | specific_workflow | AP-only | `workflow_name="Accounts Payable"` |
| awaiting_action | all_workflows | inbox_total | Total Inbox |
| workflow_aging | all_workflows | aging | Aging buckets |
| tickets_processed | all_workflows | tickets_processed | completedAtUtc not_null |
| workflow_sla | all_workflows | sla | 5-day CASE |
| pending_workflow | all_workflows | pending_workflow | |
| repo_total, repo_aging, document_version, no_recent_access, retention_status | all_repositories | … | Shared document fields |

`align_spec_to_intent` now **always rebuilds** metric kinds (`aging`, `sla`, `inbox_total`, `tickets_processed`, `repo_total`, …) so LLM 2-column lists are replaced.

Specified non-AP workflows have **no form-generic recipe**. Fallback uses description tokens or the first identifier.

---

## 11. Library

Table `saved_reports` (`db/migrations/0013_create_saved_reports.sql`), also `CREATE IF NOT EXISTS` on startup.

Stores: name, description, domain, report_type, workflow_name, repository_name, report_prompt, report_spec JSON, status, favorite, runs.

Running a saved report calls the same `run_report` (so saved RFQ specs still hit Path B).

---

## 12. Live results (after local restart)

Health: EZOFIS live login configured.  
Tests: report-agent suite green.

| Scope | Description | Phase 1 columns | Phase 2 columns | Path | Result |
|---|---|---|---|---|---|
| All Workflows | Aging | Aging_Bucket, workflowName, Request_Count | **same** | A | 13 rows |
| All Workflows | Total Inbox | workflowName, status, Inbox_Count | **same** | A | 7 rows |
| All Workflows | SLA | Sla_Bucket, workflowName, Request_Count | **same** | A | 14 rows |
| All Workflows | Tickets processed | workflowName, Processed_Count | **same** | A | 4 rows |
| All Repositories | Total documents | Repository_Name, Status, Document_Count | **same** | A | 29 rows |
| Specified RFQ | RFQ list / Aging | Company_Name on `ezfb_787372d8_items` | **Name** | B | 1 row Accounts Payable; lock dropped form table |

Warnings on RFQ run:

- Dropped unknown source table `ezfb_787372d8_items`
- Dropped unknown column source `Company_Name`
- Redirected report source to `workflow.Workflows`
- SQL: `SELECT "t"."Name" FROM "workflow"."Workflows"`

---

## 13. What is wrong with the current scope model

1. **Four types, two execute engines.** All-* skip SQL. Specified still assume tenant SQL that Docker cannot see.
2. **Phase 1 schema ≠ Phase 2 schema.** Generate is EZOFIS-grounded; Run is local-Postgres-grounded.
3. **Virtual tables leak into the spec** (`workflow_instances_all`) as if they were SQL. Only Path A understands them.
4. **Specified workflow is “AP-shaped”.** Example description is still “Total Approved with Score”. Recipes for specified_workflow are AP-only. RFQ/HR/Quote get a generic 1-field list.
5. **Catalog tables are used as row sources after lock failure.** Skills forbid `Workflows` as the fact table; lock then selects it.
6. **COUNT(*) is sacred in lock.** One metric column survives after every real column is dropped — the “always 1 column” symptom.
7. **Tenant SQL from Docker is not a reliable source.** Catalog DNS / missing tenant DB. Lists already moved to EZOFIS REST; Run for specified scopes did not.
8. **Status on inbox is a raw code** (`"1"`), not a label. Scope of “status” is undefined (EZOFIS enum vs form field).

---

## 14. Proposed new scope (rebuild target)

Keep the four user-facing types. Change **what each type means** and **how both phases use it**.

### Scope contract

| Type | Columns (Generate + Run) | Rows (Run) |
|---|---|---|
| All Workflows | Shared instance fields only (never form Invoice/PO) | Union of `GET /workflows/{id}/instances` |
| Specified workflow | **That formJson only** | Instances of that workflow, projected onto form fields (EZOFIS instance/form payload, or tenant `ezfb_*` **if** it exists) |
| All Repositories | Shared document fields only | Union of repository items |
| Specified repository | **That designer fields only** | Items of that repository |

### Execute rule (same for every type)

```text
schema = EZOFIS schema for this scope
spec   = generate from that schema (recipes + optional LLM)
run    = query the same EZOFIS/tenant source
lock   = only against THAT schema, never against local Workflows
```

- If tenant SQL has the matching `ezfb_*` / `items_*` table, SQL Path B may run **on that table**.
- If not, Python/EZOFIS Path A must run **for specified scopes too**.
- Never redirect to `workflow.Workflows`.
- If the source is missing, return spec columns with empty rows and a clear warning — not one `Name` column.

### Recipe rule

- All Workflows / All Repositories: keep shared chips (Aging, SLA, Inbox, processed / documents, aging, retention).
- Specified workflow: **no AP recipes unless the selected workflow is Accounts Payable**. Default spec = first 6–8 live form fields, not one identifier.
- Specified repository: default spec = FileName + designer fields.

### Model rule

- Optional. Recipes + form fields must work with the model down (current reality).
- Skills stay as JSON contracts. They must not be treated as the execute engine.

### Lock rule

- Lock against the Phase 1 slice (EZOFIS virtual/form schema), not local `information_schema`.
- Do not keep COUNT if the source table was dropped.
- Do not substitute catalog definition tables.

---

## 15. Files that would change in a scope reset

| File | Why |
|---|---|
| `app/report_agent/report_types.py` | Redefine schema_focus / examples; specified example must not be AP-only |
| `app/report_agent/service.py` | Same schema helper for generate **and** run; Path A for specified scopes |
| `app/report_agent/catalog_execute.py` | Execute specified workflow/repo from EZOFIS, not only all-* |
| `app/report_agent/definition_lock.py` | Accept virtual/form schema; stop Workflows redirect; stop COUNT-after-drop |
| `app/report_agent/report_catalog.py` | Gate AP recipes on workflow name; better default for generic specified forms |
| `app/report_agent/metadata_service.py` | Stop treating local SQL dump as the specified-workflow source |
| `app/report_agent/schema_scope.py` | Slice = EZOFIS schema, not leftover ezfb prefixes |
| `app/integrations/ezofis_client.py` | Instance + item list completeness / paging |
| `skills/report-prompt/*` and `skills/report-run/*` | Align instructions with EZOFIS-first scope |
| `app/static/console.html` | Chips per type; show `sourceTable` + path warning in the result |
| `tests/test_report_agent_rebuild.py` | Specified RFQ generate **and** run must keep form columns |

Do **not** change skill markdown alone. Scope lives in Python execute + lock.

---

## 16. How to verify after a scope reset

For tenant `b843b988-00ec-44e3-aca2-b8470133ef63`:

1. All Workflows Aging / Inbox / SLA — Generate columns **equal** Run columns (already true).
2. Specified **RFQ Process** — Generate includes Company_Name, Order_Number, Date, Total (not Invoice_No). Run returns **those same keys**, not `Name`.
3. Specified **HR Records** (repository) — EmployeeName / FileName / PayslipMonth on both phases.
4. Accounts Payable specified — AP recipes still allowed (Invoice/PO exist on **that** form).
5. Warnings must not contain `Redirected report source … Workflows`.
6. `includeDebug` SQL, if present, must query `ezfb_*` or `items_*`, never `workflow.Workflows`.

---

## 17. Key files (current tree)

| Path | Role |
|---|---|
| `app/report_agent/service.py` | Phase 1 + 2 orchestration |
| `app/report_agent/report_types.py` | Four types |
| `app/report_agent/report_catalog.py` | Recipes + align |
| `app/report_agent/report_spec.py` | Parse / enrich / fallback spec |
| `app/report_agent/form_schema.py` | formJson / designer → columns |
| `app/report_agent/metadata_service.py` | SQL schema + virtual all-* |
| `app/report_agent/schema_scope.py` | Approved slice |
| `app/report_agent/definition_lock.py` | Phase 2 lock |
| `app/report_agent/definition_sql.py` | Read-only SQL |
| `app/report_agent/catalog_execute.py` | EZOFIS aggregation (all-* only today) |
| `app/report_agent/data_service.py` | Run SQL |
| `app/report_agent/sql_validator.py` | SELECT-only |
| `app/report_agent/pack.py` | Load report-prompt / report-run |
| `app/report_agent/library_store.py` | Saved reports |
| `app/integrations/ezofis_client.py` | EZOFIS REST |
| `app/models/report_agent.py` | Request/response models |
| `skills/report-prompt/` | Phase 1 LLM instructions |
| `skills/report-run/` | Phase 2 LLM instructions |
| `db/migrations/0013_create_saved_reports.sql` | Library table |
| `app/static/console.html` | Builder UI |

Related older docs (partial / UI-only, not the execute truth): `docs/report-agent-ui-only.md`, `docs/report-agent-live-prompt-plan.md`, `report-builder-ai-agent.md`.

---

## 18. Bottom line

- **All Workflows / All Repositories** are usable: Generate and Run share EZOFIS instance/item fields.
- **Specified workflow / repository are not:** Generate uses the form; Run locks against local `Workflows` and shows one column.
- A scope reset means: **one EZOFIS-first schema per type, used by both phases**, AP recipes only on AP, and lock that cannot invent `Workflows` as the report.
