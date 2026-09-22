# Dashboard Agent — End to End

How Dashboard works on **`POST /chat` only** (`intent=dashboard`).  
There is no `/dashboard/schema` or `/dashboard/data` on the orchestrator — three **phases** share one URL.

**Demo URLs (local)**

| What | URL |
|---|---|
| Test console | http://localhost:8010/console |
| Swagger | http://localhost:8010/docs (examples: `dashboard_prompts`, `dashboard_schema`, `dashboard_data`) |
| Health | http://localhost:8010/health |
| API | `POST` http://localhost:8010/chat |

---

## 1. What it does

The caller sends `intent=dashboard` plus a tenant and **either** a repository or a workflow. The agent reads EZOFIS **Postgres** tenant items (max **50** rows), then:

| Phase | `payload.phase` | What happens |
|---|---|---|
| **Prompts** | `prompts` | LLM suggests **one** dashboard request from table columns |
| **Schema** | `schema` (default if phase omitted and no `dashboard_json`) | LLM proposes `kpis` + `charts` JSON (`data` is null) |
| **Data** | `data` (or any body with `dashboard_json`) | Hydrate widgets from live rows; **`Content-Type: text/html`** |

Typical order: prompts → paste `prompt` as `message` on schema → send schema `kpis`/`charts` back as `dashboard_json` on data.

---

## 2. Input → output

```
Client
  POST /chat
        │
        ▼
  Hallway (same as every /chat call)
        │
        ▼
  Intent = dashboard
        │
        ▼
  DashboardAgent.handle
        │
        ├─ resolve target (repository_id, or workflow_id → RepositoryId)
        ├─ tenant DB  ezofis_Tenant_{first 8 of tenant_id}
        ├─ items table  repository.items_{first 8 of repository_id}
        │
        ├─ phase prompts  → generate_prompt → JSON dashboard_result
        ├─ phase schema   → propose widgets → JSON dashboard_result
        └─ phase data     → hydrate (LIMIT 50) + insights → HTML
```

---

## 3. Request schema (`ChatRequest`)

Always `POST /chat`. Required on every call: `session_id`, `intent`, `payload.tenant_id`, and **`repository_id` or `workflow_id`**.

Optional labels: `payload.repository_name`, `payload.workflow_name` (prompt hinting only).

### 3.1 Prompts

```json
{
  "session_id": "demo",
  "intent": "dashboard",
  "payload": {
    "phase": "prompts",
    "tenant_id": "YOUR-TENANT-UUID",
    "repository_id": "YOUR-REPO-UUID"
  }
}
```

`message` is optional. Response is JSON.

### 3.2 Schema

```json
{
  "session_id": "demo",
  "intent": "dashboard",
  "message": "I need an AP dashboard",
  "payload": {
    "phase": "schema",
    "tenant_id": "YOUR-TENANT-UUID",
    "repository_id": "YOUR-REPO-UUID"
  }
}
```

Workflow instead of repository:

```json
{
  "session_id": "demo",
  "intent": "dashboard",
  "message": "I need an AP dashboard",
  "payload": {
    "phase": "schema",
    "tenant_id": "YOUR-TENANT-UUID",
    "workflow_id": "YOUR-WORKFLOW-UUID"
  }
}
```

`message` is the user request (use the prompts-phase `prompt` here). Response is JSON; copy `kpis` and `charts` into the data call.

### 3.3 Data

```json
{
  "session_id": "demo",
  "intent": "dashboard",
  "message": "apply",
  "payload": {
    "phase": "data",
    "tenant_id": "YOUR-TENANT-UUID",
    "repository_id": "YOUR-REPO-UUID",
    "dashboard_json": {
      "phase": "schema",
      "kpis": [],
      "charts": []
    }
  }
}
```

Replace empty `kpis`/`charts` with the arrays from the schema response. You can send the whole `dashboard_result` object; the API unwraps nested envelopes.

If `dashboard_json` is present, phase is treated as **data** even without `"phase": "data"`.

Data **response** is raw HTML (`text/html; charset=utf-8`), not the Chat JSON envelope.

---

## 4. Responses

**Prompts** — flat JSON (no `dashboard_result` / `reply` / `html`):

```json
{
  "session_id": "demo",
  "prompt": "...",
  "correlation_id": "...",
  "latency_ms": 0,
  "tenant_id": "...",
  "repository_id": "...",
  "repository_name": "...",
  "workflow_id": null,
  "workflow_name": null,
  "table": "repository.items_..."
}
```

**Schema** — compact Chat JSON with `dashboard_result` (`phase: "schema"`, `kpis`, `charts`, `"data": null`).

**Data** — HTML document (styles + `.ez-dash` markup). Rows are capped at 50.

---

## 5. Data source

| Setting | Role |
|---|---|
| `CATALOG_DATABASE_URL` | Catalog: `catalog."Tenants"`, `catalog."Repositories"`, workflow → repository |
| Tenant Postgres | Same Azure host as Catalog; database `ezofis_Tenant_{first 8 chars of tenant UUID}` |
| Items table | `repository.items_{first 8 chars of repository UUID}` |

`DATABASE_URL` is the orchestrator app DB, **not** the tenant items DB.

If the tenant DB cannot be opened or the repository/workflow is missing, `/chat` returns **400**. It does not substitute the demo Accounts Payable repository.

---

## 6. Skill packs (Catalog)

System prompts load from Catalog when `AGENT_PACKS_FROM_DB=true` (default), with hardcoded fallbacks in:

| Phase | `agent_slug` | Code fallback |
|---|---|---|
| prompts | `dashboard-prompts` | `app/dashboard/prompts.py` |
| schema | `dashboard-schema` | `app/dashboard/propose.py` |
| data (insights) | `dashboard-data` | `app/dashboard/insights.py` |

Disk copies: `skills/dashboard-prompts|schema|data/` (and `app/skills/…`). Tables: `platform_agent_skills` / `platform_agent_rules`.

---

## 7. Console

http://localhost:8010/console → **Dashboard agent**.

1. Tenant gate (or Continue without tenant and paste UUID).
2. **Prompts** → **Schema** → **Data**.
3. Tenant picker is `GET /console/catalog/tenants` (`catalog."Tenants"` + Ezofis `GET /auth/tenants`). Empty list if both sources are empty.

For Ezofis names locally, set `EZOFIS_LOGIN_EMAIL` and `EZOFIS_LOGIN_PASSWORD` (Catalog URL alone does not fill the Ezofis half).

---

## 8. Code map

| Piece | Where |
|---|---|
| Agent | `app/dashboard/agent.py` (`app/agents/dashboard_agent.py` wrapper) |
| Store / LIMIT 50 | `app/dashboard/store.py` |
| Render HTML | `app/dashboard/render.py` |
| `/chat` HTML for data | `app/main.py` |
| Intent | `Intent.DASHBOARD` |
| Tests | `tests/test_dashboard_endpoint.py` |
