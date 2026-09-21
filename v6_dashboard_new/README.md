# v6_dashboard

Standalone FastAPI dashboard agent for **SQL Server** (EZOFIS tenant DBs).

The orchestrator dashboard agent stays on Postgres. This project is the SQL Server port: same two-call `/chat` contract, pyodbc instead of asyncpg.

## Run

```powershell
cd D:\raahina\v6_summary\orchestrator\v6_dashboard
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8011
```

Requires **ODBC Driver 18 for SQL Server**. Credentials live in `local.settings.json` (gitignored). Copy `local.settings.example.json` if you need a blank template.

| What | URL |
|---|---|
| Console | http://localhost:8011/console |
| Swagger | http://localhost:8011/docs |
| Health | http://localhost:8011/health |
| API | `POST /prompts` then `POST /dashboard/schema` then `POST /dashboard/data` |

On this machine the catalog default tenant is not present as a database. The console lists tenants whose DB exists (for example `3EE0E334-…` → `ezofis_Tenant_3ee0e334`, repository **Accounts Payable**).

## Calls

**`POST /prompts`** — same identity as schema (`tenant_id` + `repository_id` **or** `workflow_id`; optional `repository_name` / `workflow_name`). Reads the items table and returns a `prompt` string you can paste as `message` on schema (or edit first). JSON.

**`POST /dashboard/schema`** — JSON `ChatResponse`. Send `session_id`, `message`, `tenant_id`, and `repository_id` **or** `workflow_id` (aliases `repositoryId` / `workflowId` / nested `/chat` `payload` also work). Returns `dashboard_result` with `kpis[]`, `charts[]`, per-widget `description`, and `data: null`. `html` is always `null`. There is no `sections` array.

**`POST /dashboard/data`** — send that `dashboard_result` as `dashboard_json`. Response is **`text/html`** (not JSON): KPI values and charts only. Errors stay JSON `{ "detail": "..." }` (a string, not an array). Put `response.text()` into the page — do not call `response.json()`:

```javascript
const html = await fetch("/dashboard/data", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ session_id: "demo", tenant_id, repository_id, dashboard_json }),
}).then((r) => r.text());
document.getElementById("dashboard").innerHTML = html;
```

`POST /chat` is the combined form (call 2 = same body with `payload.dashboard_json`). Azure OpenAI settings in `local.settings.json` are used for schema.
