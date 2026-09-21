"""Dashboard agent: catalog slug, /chat intent, 50-row cap. No skill pack."""
from __future__ import annotations

import asyncio

from app.core.intent_router import Intent, IntentRouter
from app.dashboard.insights import fallback_insights
from app.dashboard.store import ROW_LIMIT, _clamp_limit


TENANT = "b843b988-00ec-44e3-aca2-b8470133ef63"
REPO = "a6169a5c-1468-4fb5-90a9-220082a89f2a"
WORKFLOW = "452859c2-9bbe-4e17-acd2-f11524a0650e"


def test_catalog_includes_dashboard(client):
    slugs = [row["slug"] for row in client.get("/console/catalog/agents").json()["agents"]]
    assert "dashboard" in slugs


def test_intent_router_dashboard_before_search():
    router = IntentRouter()
    assert asyncio.run(router.classify("I need a dashboard")) == Intent.DASHBOARD
    assert asyncio.run(router.classify("build a dashboard for AP")) == Intent.DASHBOARD
    assert asyncio.run(router.classify("search for the PTO policy")) == Intent.SEARCH


def test_clamp_limit_caps_at_fifty():
    assert _clamp_limit(None) == ROW_LIMIT
    assert _clamp_limit(5000) == 50
    assert _clamp_limit(10) == 10
    assert _clamp_limit(0) == 1


class _FakeStore:
    def __init__(self):
        self.last_limit = None
        self.rows = [{"Id": i, "InvoiceAmount": 10, "Supplier": "Acme", "DueDate": "2026-01-01"} for i in range(80)]

    async def resolve_target(self, *, tenant_id, repository_id=None, workflow_id=None):
        if not tenant_id:
            raise ValueError("payload.tenant_id is required for intent=dashboard.")
        if not repository_id and not workflow_id:
            raise ValueError("payload.repository_id or payload.workflow_id is required for intent=dashboard.")
        if workflow_id and not repository_id:
            repository_id = REPO
        return {
            "tenant_id": tenant_id,
            "repository_id": repository_id or REPO,
            "repository_name": "Accounts Payable",
            "workflow_id": workflow_id,
            "workflow_name": "Accounts Payable",
            "form_id": None,
            "schema": "repository",
            "table": "items_a6169a5c",
            "qualified_table": "repository.items_a6169a5c",
        }

    async def list_columns(self, *, tenant_id, schema, table):
        return ["Id", "InvoiceAmount", "Supplier", "DueDate", "MatchedStatus"]

    async def fetch_rows(self, *, tenant_id, schema, table, columns, limit=None):
        from app.dashboard.store import _clamp_limit as clamp

        cap = clamp(limit)
        self.last_limit = cap
        return self.rows[:cap]

    async def fetch_extract_artifacts(self, *, tenant_id, item_keys):
        return {}


async def _stub_propose(*, message, target, columns, sample_rows=None):
    return [
        {
            "id": "total_ap",
            "label": "TOTAL AP",
            "enabled": True,
            "agg": "sum",
            "columns": {"value": "InvoiceAmount"},
        }
    ], []


def _install_fake(client, monkeypatch):
    store = _FakeStore()
    client.app.state.dashboard_store = store
    client.app.state.dashboard_agent._store = store
    client.app.state.dashboard_agent._proposer = _stub_propose

    async def fake_insights(*, repository_name, kpis, charts, data, message=""):
        return fallback_insights(
            repository_name=repository_name,
            kpis=kpis,
            charts=charts,
            data=data,
            message=message,
        )

    monkeypatch.setattr("app.dashboard.agent.generate_insights", fake_insights)
    return store


def test_dashboard_prompts_via_chat(client, monkeypatch):
    _install_fake(client, monkeypatch)

    async def fake_prompt(**kwargs):
        return {
            "prompt": "Build an Accounts Payable dashboard with total payable.",
            "tenant_id": kwargs.get("tenant_id"),
            "repository_id": kwargs.get("repository_id") or REPO,
            "repository_name": "Accounts Payable",
            "workflow_id": kwargs.get("workflow_id"),
            "workflow_name": None,
            "table": "repository.items_a6169a5c",
        }

    monkeypatch.setattr("app.dashboard.prompts.generate_prompt", fake_prompt)
    response = client.post(
        "/chat",
        json={
            "session_id": "s-dash-prompts",
            "intent": "dashboard",
            "payload": {"phase": "prompts", "tenant_id": TENANT, "repository_id": REPO},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "dashboard_result" not in body
    assert "reply" not in body
    assert "html" not in body
    assert "payable" in str(body.get("prompt") or "").lower()
    assert body["session_id"] == "s-dash-prompts"
    assert body["tenant_id"] == TENANT
    assert body["repository_id"] == REPO
    assert body.get("workflow_id") is None
    assert body.get("workflow_name") is None
    assert "table" in body


def test_dashboard_schema_from_repository(client, monkeypatch):
    _install_fake(client, monkeypatch)
    response = client.post(
        "/chat",
        json={
            "session_id": "s-dash",
            "intent": "dashboard",
            "message": "I need an AP dashboard",
            "payload": {
                "phase": "schema",
                "tenant_id": TENANT,
                "repository_id": REPO,
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "session_id",
        "reply",
        "correlation_id",
        "latency_ms",
        "dashboard_result",
        "html",
    }
    result = body["dashboard_result"]
    assert result["phase"] == "schema"
    assert result["repository_id"]
    assert result["workflow_id"] is None
    assert result.get("workflow_name") is None
    assert result["kpis"]
    assert result["data"] is None
    assert body["html"] is None


def test_dashboard_schema_from_workflow_navigates_to_repo(client, monkeypatch):
    _install_fake(client, monkeypatch)
    response = client.post(
        "/chat",
        json={
            "session_id": "s-dash-wf",
            "intent": "dashboard",
            "message": "I need a dashboard",
            "payload": {
                "phase": "schema",
                "tenant_id": TENANT,
                "workflow_id": WORKFLOW,
            },
        },
    )
    assert response.status_code == 200
    result = response.json()["dashboard_result"]
    assert result["repository_id"] == REPO
    assert result["workflow_id"] == WORKFLOW


def test_dashboard_data_caps_rows_at_50(client, monkeypatch):
    store = _install_fake(client, monkeypatch)
    schema = client.post(
        "/chat",
        json={
            "session_id": "s-dash-data",
            "intent": "dashboard",
            "message": "I need an AP dashboard",
            "payload": {
                "phase": "schema",
                "tenant_id": TENANT,
                "repository_id": REPO,
            },
        },
    ).json()["dashboard_result"]
    response = client.post(
        "/chat",
        json={
            "session_id": "s-dash-data",
            "intent": "dashboard",
            "message": "apply",
            "payload": {
                "phase": "data",
                "tenant_id": TENANT,
                "repository_id": REPO,
                "dashboard_json": {
                    "phase": "schema",
                    "kpis": schema["kpis"],
                    "charts": schema["charts"],
                },
            },
        },
    )
    assert response.status_code == 200
    assert store.last_limit == 50
    assert "text/html" in response.headers.get("content-type", "")
    body = response.text
    assert body.lstrip().startswith("<style>")
    assert "ez-dash" in body


def test_dashboard_data_payload_kpis_charts_only(client, monkeypatch):
    store = _install_fake(client, monkeypatch)
    schema = client.post(
        "/chat",
        json={
            "session_id": "s-dash-flat",
            "intent": "dashboard",
            "message": "I need an AP dashboard",
            "payload": {"phase": "schema", "tenant_id": TENANT, "repository_id": REPO},
        },
    ).json()["dashboard_result"]
    response = client.post(
        "/chat",
        json={
            "session_id": "s-dash-flat",
            "intent": "dashboard",
            "message": "apply",
            "payload": {
                "phase": "data",
                "tenant_id": TENANT,
                "repository_id": REPO,
                "dashboard_json": {
                    "phase": "schema",
                    "kpis": schema["kpis"],
                    "charts": schema["charts"],
                },
            },
        },
    )
    assert response.status_code == 200, response.text
    assert store.last_limit == 50
    assert "text/html" in response.headers.get("content-type", "")
    assert "ez-dash" in response.text


def test_dashboard_requires_tenant_and_target(client, monkeypatch):
    _install_fake(client, monkeypatch)
    response = client.post(
        "/chat",
        json={"session_id": "s-dash-bad", "intent": "dashboard", "message": "dashboard"},
    )
    assert response.status_code == 400


def test_dashboard_prompts_connect_fail_does_not_swap_sample_repo(client):
    from app.dashboard.store import DashboardStore

    class _FailPools:
        async def acquire_for_global_search(self, tenant_id, catalog_store=None):
            raise RuntimeError("database does not exist")

    store = DashboardStore(tenant_pools=_FailPools(), catalog_store=None)
    client.app.state.dashboard_store = store
    client.app.state.dashboard_agent._store = store
    response = client.post(
        "/chat",
        json={
            "session_id": "s-dash-nosample",
            "intent": "dashboard",
            "message": "suggest a dashboard",
            "payload": {
                "phase": "prompts",
                "tenant_id": TENANT,
                "repository_id": "e7596082-d04a-4233-a842-c4c6c3138b79",
            },
        },
    )
    assert response.status_code == 400
    detail = str(response.json().get("detail") or "")
    assert "tenant database" in detail.lower()
    assert "DF175C77" not in response.text


def test_dashboard_schema_unknown_repo_does_not_swap_sample(client):
    from app.dashboard.store import DashboardStore

    class _EmptyPool:
        async def fetchrow(self, *args, **kwargs):
            return None

    class _OkPools:
        async def acquire_for_global_search(self, tenant_id, catalog_store=None):
            return _EmptyPool()

    store = DashboardStore(tenant_pools=_OkPools(), catalog_store=None)
    client.app.state.dashboard_store = store
    client.app.state.dashboard_agent._store = store
    response = client.post(
        "/chat",
        json={
            "session_id": "s-dash-norepo",
            "intent": "dashboard",
            "message": "I need an AP dashboard",
            "payload": {
                "phase": "schema",
                "tenant_id": TENANT,
                "repository_id": "e7596082-d04a-4233-a842-c4c6c3138b79",
            },
        },
    )
    assert response.status_code == 400
    detail = str(response.json().get("detail") or "")
    assert "repository_id" in detail.lower()
    assert "DF175C77" not in response.text

