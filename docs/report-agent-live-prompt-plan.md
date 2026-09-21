# Live Prompt and Report Plan Workspace

## Goal

Show the editable Report Agent prompt and its generated Report Plan in one workspace. When a user stops typing, refresh the plan from the edited prompt and replace only the plan preview. The prompt is always visible; users should not have to open a separate "override" section or inspect chat history.

## Required behaviour (not a static preview)

The prompt is the user-editable source of truth for the plan. This is different from simply displaying a generated prompt beside a template-based plan.

After the initial generation:

1. The user edits the prompt.
2. The application sends that exact edited prompt to the plan endpoint.
3. The server extracts supported report instructions from it.
4. The server rebuilds the structured `reportPlan`, safe SQL, and data preview from those instructions and the live validated schema.
5. The Report plan pane shows the new title, description, filters, grouping, sorting, calculations, SQL, and data preview where applicable.

For example, changing **“Outstanding AP requests bucketed by age”** to **“Show only unpaid invoices due in the next 30 days, grouped by vendor, highest amount first”** must change the plan's filters, grouping, ordering, and SQL. Merely changing the plan description or showing an “up to date” label does **not** meet this requirement.

The visual shown in the reference image is therefore only an initial layout state. It is not the desired finished behaviour unless its plan refresh is driven by the edited prompt and visibly reflects the resulting plan changes.

```text
Select template
      |
      v
Generate schema-backed prompt
      |
      +--> editable Prompt pane
      |
      +--> generate Report Plan
                  |
                  v
             Plan preview pane

Edit prompt
      |
      v
Debounce 600 ms -> cancel older request -> generate refreshed plan -> replace plan pane
```

## User experience

1. In Report Agent, show a two-column workspace below the template, tenant, and row-limit fields.
2. The left column is an always-visible `textarea` labelled **Prompt**. It starts with the schema-backed prompt returned by `POST /api/report-agent/generate-prompt`.
3. The right column is **Report plan**. Before generation it says, “Edit the prompt, then the plan will appear here.”
4. Selecting a template generates the prompt and first plan. The user can also use a **Refresh plan** button.
5. After every prompt edit, wait 600 ms. If the user keeps typing, restart the timer. When it expires, request a fresh plan and show a small “Updating plan…” state.
6. If the user switches templates or continues typing while a request is running, abort the old request. Only render a response if it belongs to the newest edit.
7. On narrow screens, stack Prompt above Report plan.

## Front-end layout

### Layout rule: the two boxes must never merge

The screenshot shows the Prompt and Report plan areas visually running together. Make each a separate card with its own border, background, heading bar, padding, and minimum height. Do not place the plan card inside the prompt card, and do not rely on a faint background colour as the only separator.

Required visual hierarchy:

```text
Report Agent options

┌──────────────────────── Prompt ───────────────────────┐  ┌────────────────── Report plan ───────────────────┐
│ Schema-backed prompt · editing refreshes the plan      │  │ Plan status · Refresh plan                        │
│                                                        │  │──────────────────────────────────────────────────│
│ [large editable prompt textarea]                       │  │ [plan summary, filters, SQL, data preview]        │
│                                                        │  │                                                  │
└────────────────────────────────────────────────────────┘  └──────────────────────────────────────────────────┘
```

On screens narrower than 1100px, stack the complete cards vertically. Never squeeze both cards into narrow columns where their contents overlap or are clipped.

Add this inside the existing Report Agent panel in `app/static/console.html`:

```html
<section class="report-workspace" aria-label="Live report workspace">
  <div class="report-workspace-pane">
    <div class="report-workspace-heading">
      <label for="reportPromptOverride">Prompt</label>
      <span id="reportPromptStatus" role="status">Generate a template to start</span>
    </div>
    <textarea id="reportPromptOverride"
      placeholder="Select a template to generate a schema-backed prompt"></textarea>
  </div>

  <div class="report-workspace-pane">
    <div class="report-workspace-heading">
      <span>Report plan</span>
      <button type="button" class="btn-ghost" id="reportPlanRefreshBtn">Refresh plan</button>
    </div>
    <div id="reportPlanPreview" aria-live="polite">
      Edit the prompt to generate the report plan.
    </div>
  </div>
</section>
```

Use a grid for desktop and a single column at 1100px or less. These styles intentionally use strong card separation and no fixed parent height:

```css
.report-workspace {
  display: grid;
  grid-template-columns: minmax(420px, 1fr) minmax(420px, 1fr);
  gap: 20px;
  align-items: stretch;
  width: 100%;
  margin-top: 16px;
}
.report-workspace-pane {
  min-width: 0;
  min-height: 500px;
  overflow: hidden;
  border: 1px solid var(--line-strong);
  border-radius: var(--radius);
  background: var(--surface);
  box-shadow: var(--shadow-sm);
}
.report-workspace-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  min-height: 64px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--line-strong);
  background: var(--gradient-soft);
  font-weight: 700;
}
.report-workspace-pane > textarea,
#reportPlanPreview {
  display: block;
  width: calc(100% - 32px);
  margin: 16px;
}
#reportPromptOverride {
  min-height: 400px;
  resize: vertical;
  font-family: var(--mono);
  line-height: 1.5;
}
#reportPlanPreview {
  min-height: 400px;
  max-height: 620px;
  overflow: auto;
}
@media (max-width: 1100px) {
  .report-workspace { grid-template-columns: 1fr; }
  .report-workspace-pane { min-height: 420px; }
}
```

Also check the parent controls. The report panel and its grid must allow the workspace to use full width and natural height:

```css
#reportPanel .ocr-grid { display: block; max-height: none; overflow: visible; }
#reportPanel .ocr-actions { margin-top: 16px; }
```

Do not use `position: absolute`, a fixed height, `overflow: hidden`, or a nested two-column grid on the workspace parent. Those are the common causes of the two panels appearing merged or cut off.

## Request lifecycle

Keep one `AbortController`, one debounce timer, and a monotonic revision number. This prevents a slow response for an old prompt from overwriting the plan produced for a newer prompt.

```js
let reportPlanTimer;
let reportPlanController;
let reportPlanRevision = 0;

function queueReportPlanRefresh() {
  clearTimeout(reportPlanTimer);
  reportPlanTimer = setTimeout(refreshReportPlan, 600);
}

async function refreshReportPlan() {
  const revision = ++reportPlanRevision;
  reportPlanController?.abort();
  reportPlanController = new AbortController();

  const prompt = document.getElementById('reportPromptOverride').value.trim();
  if (!prompt) return renderPlanEmptyState();

  setReportPlanStatus('Updating plan…');
  const response = await fetch('/api/report-agent/generate-report-plan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    signal: reportPlanController.signal,
    body: JSON.stringify({
      templateId: document.getElementById('reportTemplateSelect').value,
      tenantId: document.getElementById('reportTenant').value.trim() || undefined,
      limit: Number(document.getElementById('reportLimit').value) || 20,
      prompt,
    }),
  });

  const result = await readResponseJson(response);
  if (revision !== reportPlanRevision) return;
  if (!response.ok) return renderPlanError(result);
  renderLiveReportPlan(result);
  setReportPlanStatus('Plan is up to date');
}

document.getElementById('reportPromptOverride')
  .addEventListener('input', queueReportPlanRefresh);
document.getElementById('reportPlanRefreshBtn')
  .addEventListener('click', refreshReportPlan);
```

Treat `AbortError` as normal; do not display it as a user error.

## Rendering the current API response

The current backend returns snake_case fields internally and aliases when serialized. The UI renderer should accept both forms while the interface is being standardized:

```js
function renderLiveReportPlan(result) {
  const plan = result.reportPlan || result.report_plan || {};
  const query = result.dataQuery || result.data_query || {};
  const data = result.data || {};
  const validation = result.validation || {};

  document.getElementById('reportPlanPreview').innerHTML = [
    `<h3>${escapeHtml(plan.title || 'Report plan')}</h3>`,
    `<p>${escapeHtml(plan.description || '')}</p>`,
    `<p><strong>Source:</strong> ${escapeHtml(plan.source?.table || '—')}</p>`,
    `<p><strong>Columns:</strong> ${escapeHtml((plan.columns || []).map(c => c.label).join(', ') || '—')}</p>`,
    `<p><strong>Validation:</strong> ${validation.valid ? 'Valid' : 'Needs review'}</p>`,
    `<pre class="report-sql-code">${escapeHtml(query.sql || '')}</pre>`,
  ].join('');
}
```

The existing transcript renderer must use this actual contract too:

| Existing incorrect key | API key to read |
| --- | --- |
| `result.plan` | `result.reportPlan` / `result.report_plan` |
| `result.sql.sqlQuery` | `result.dataQuery.sql` / `result.data_query.sql` |
| `plan.reportTitle` | `plan.title` |
| `validation.passed` | `validation.valid` |
| `validation.issues` | `validation.errors` and `validation.warnings` |

## Safe backend rule

The browser may send an edited prompt, but it must never be trusted as a source of executable table names, fields, joins, or raw SQL. Continue to:

1. resolve the selected template server-side;
2. rediscover and validate the live schema;
3. create SQL only from validated server-side fields; and
4. validate that generated SQL is read-only before execution.

The prompt may safely change the presentation intent (for example the report title, description, requested ordering, or a recognized date range). Any new filtering or grouping instruction needs a server-side parser that maps it to validated fields and typed values; unknown instructions should become a plan warning, not SQL.

## Backend change: compile the edited prompt into a safe plan request

Add a small prompt-intent compiler before `create_report_plan()` in `app/report_agent/service.py`. It must return structured business intent, never SQL and never client-supplied database identifiers.

```python
PromptIntent(
    title: str | None,
    description: str | None,
    requested_filters: list[BusinessFilter],
    group_by_concepts: list[str],
    sort_concepts: list[BusinessSort],
    requested_calculations: list[str],
    warnings: list[str],
)
```

The compiler can recognize a deliberately small supported vocabulary first:

| User wording | Safe structured intent |
| --- | --- |
| `unpaid`, `open`, `pending` | status concept, resolved only to a validated status field and sampled values |
| `due in the next 30 days` | a typed date-range filter on a validated due-date field |
| `last 7 days` / `last 30 days` | a typed date-range filter on a validated activity/date field |
| `group by vendor` | group by the validated field that best maps to the vendor concept |
| `highest amount first` | descending sort on a validated monetary field |
| `count by status` | count calculation and group by a validated status field |

The plan builder receives this `PromptIntent` in addition to the selected template. It maps each business concept to one of the fields rediscovered from the live database. If mapping is ambiguous or unavailable, add a warning such as **“Could not apply ‘group by vendor’: no vendor field exists in the live schema.”** Do not guess.

```text
Edited prompt
  -> prompt-intent compiler
  -> concept-to-validated-field resolver
  -> ReportPlan
  -> read-only SQL validator
  -> query execution
  -> refreshed plan pane
```

## Recommended delivery order

1. Add the two-pane workspace and render the existing plan response in the right pane.
2. Make template selection generate the prompt and immediately call the plan endpoint once.
3. Add debounce, abort, revision protection, and the manual refresh fallback.
4. Normalize the transcript renderer to the current Phase 2 response contract.
5. Add safe prompt-intent parsing on the server for the specific editing controls you want to support (date range, status, sort, and grouping).
6. Add tests for debounce behavior, stale-response protection, empty prompt, API failure, and the responsive stacked layout.

## Acceptance criteria

- Prompt and plan are visible together without opening a secondary control.
- Editing the prompt refreshes the adjacent plan after a short pause.
- An old network response can never overwrite the latest plan.
- Every refresh retains the selected tenant and row limit.
- No prompt-provided schema name, column name, or SQL can reach the query generator unchecked.
- A manual refresh remains available for accessibility and recovery.
