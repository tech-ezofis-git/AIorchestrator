"""API tests with a fake SQL store."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.dashboard_agent import DashboardAgent
from app.insights import fallback_insights
from app.main import app
from app.store import DashboardStore


@pytest.fixture(autouse=True)
def _fast_insights(monkeypatch):
    async def fake(*, repository_name, kpis, charts, data, message=""):
        return fallback_insights(
            repository_name=repository_name,
            kpis=kpis,
            charts=charts,
            data=data,
            message=message,
        )

    monkeypatch.setattr("app.dashboard_agent.generate_insights", fake)


async def _stub_propose(*, message, target, columns, sample_rows=None):
    return [
        {
            "id": "total_ap",
            "label": "TOTAL AP",
            "enabled": True,
            "agg": "sum",
            "columns": {"value": "InvoiceAmount"},
        },
        {
            "id": "overdue",
            "label": "OVERDUE",
            "enabled": True,
            "agg": "overdue_sum",
            "columns": {"value": "InvoiceAmount", "date": "DueDate"},
        },
        {"id": "open_invoices", "label": "OPEN INVOICES", "enabled": True, "agg": "count", "columns": {}},
    ], [
        {
            "id": "supplier_risk",
            "label": "Supplier Risk Radar",
            "title": "Supplier Risk Radar",
            "type": "radar",
            "enabled": True,
            "agg": "sum",
            "grain": "none",
            "columns": {"group": "Supplier", "value": "InvoiceAmount"},
        },
    ]


class FakeStore(DashboardStore):
    def resolve_target(self, *, tenant_id, repository_id=None, workflow_id=None):
        tenant_id = (tenant_id or "").strip()
        repository_id = (repository_id or "").strip() or None
        workflow_id = (workflow_id or "").strip() or None
        if not tenant_id:
            raise ValueError("payload.tenant_id is required for intent=dashboard.")
        if not repository_id and not workflow_id:
            raise ValueError("payload.repository_id or payload.workflow_id is required for intent=dashboard.")
        if repository_id == "00000000-0000-0000-0000-000000000099":
            raise ValueError(f"Repository '{repository_id}' was not found.")
        if workflow_id and not repository_id:
            repository_id = "38b1b6dd-854b-489f-aa44-ac6d4dd691e8"
        return {
            "tenant_id": tenant_id,
            "repository_id": repository_id or "38b1b6dd-854b-489f-aa44-ac6d4dd691e8",
            "repository_name": "AP Invoices",
            "workflow_id": workflow_id,
            "workflow_name": "AP",
            "form_id": None,
            "schema": "repository",
            "table": "Items_38b1b6dd",
            "qualified_table": "repository.Items_38b1b6dd",
        }

    def list_columns(self, *, tenant_id, schema, table):
        return ["Id", "Supplier", "InvoiceAmount", "DueDate", "MatchedStatus", "InvoiceDate", "Currency", "Status", "IsDeleted"]

    def fetch_rows(self, *, tenant_id, schema, table, columns, limit=None):
        rows = [
            {"Id": "item-1", "InvoiceAmount": "6200", "DueDate": date(2020, 1, 1), "Supplier": "Acme", "IsDeleted": 0},
            {"Id": "item-2", "InvoiceAmount": "5300", "DueDate": date(2020, 1, 1), "Supplier": "Acme", "IsDeleted": 0},
        ]
        return rows[:limit] if limit else rows

    def fetch_extract_artifacts(self, *, tenant_id, item_keys):
        return {}

    def list_targets(self, *, tenant_id):
        return {"tenant_id": tenant_id, "repositories": [], "workflows": []}


def _client() -> TestClient:
    store = FakeStore()
    app.state.store = store
    app.state.agent = DashboardAgent(store, proposer=_stub_propose)
    return TestClient(app)


TENANT = "0B3E1B77-4A6C-46F2-83EE-2F0A5B84956B"
REPO = "38b1b6dd-854b-489f-aa44-ac6d4dd691e8"


def test_dashboard_requires_tenant_and_target():
    client = _client()
    response = client.post(
        "/chat",
        json={"session_id": "s1", "intent": "dashboard", "message": "I need an AP dashboard"},
    )
    assert response.status_code == 400
    assert "tenant_id" in response.json()["detail"]


def test_dashboard_call1_schema_from_repository():
    client = _client()
    response = client.post(
        "/chat",
        json={
            "session_id": "s1",
            "intent": "dashboard",
            "message": "I need an AP dashboard",
            "payload": {"tenant_id": TENANT, "repository_id": REPO},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    result = body["dashboard_result"]
    assert body["reply"].startswith("Suggested dashboard")
    assert result["phase"] == "schema"
    assert result["data"] is None
    assert result["table"] == "repository.Items_38b1b6dd"
    assert "total_ap" in [row["id"] for row in result["kpis"]]
    assert result["kpis"][0].get("agg")
    assert result["kpis"][0].get("color")
    assert result["kpis"][0].get("position") == "top"
    assert result["kpis"][0].get("description")
    assert result["charts"][0].get("description")
    assert "sections" not in result
    assert result.get("data") is None
    assert "insights" not in result


def test_dashboard_call2_hydrates_enabled_widgets():
    client = _client()
    first = client.post(
        "/chat",
        json={
            "session_id": "s2",
            "intent": "dashboard",
            "message": "I need an AP dashboard",
            "payload": {"tenant_id": TENANT, "repository_id": REPO},
        },
    )
    schema = first.json()["dashboard_result"]
    for row in schema["kpis"]:
        row["enabled"] = row["id"] in {"total_ap", "overdue"}
    for row in schema["charts"]:
        row["enabled"] = row["id"] == "supplier_risk"
    second = client.post(
        "/chat",
        json={
            "session_id": "s2",
            "intent": "dashboard",
            "message": "apply",
            "payload": {"tenant_id": TENANT, "repository_id": REPO, "dashboard_json": schema},
        },
    )
    assert second.status_code == 200, second.text
    result = second.json()["dashboard_result"]
    assert result["phase"] == "data"
    assert result["data"]["kpis"]["total_ap"]["value"] == 11500
    assert result["data"]["kpis"]["overdue"]["value"] == 11500
    assert "open_invoices" not in result["data"]["kpis"]
    html = second.json().get("html") or ""
    assert "ez-dash" in html
    assert "ez-section" not in html
    assert "AP Command Center" in html
    assert "ez-acc" in html
    assert "AI-generated insights" in html


def test_unknown_repository():
    client = _client()
    response = client.post(
        "/chat",
        json={
            "session_id": "s3",
            "intent": "dashboard",
            "message": "I need an AP dashboard",
            "payload": {
                "tenant_id": TENANT,
                "repository_id": "00000000-0000-0000-0000-000000000099",
            },
        },
    )
    assert response.status_code == 400
    assert "not found" in response.json()["detail"].lower()


def test_rejects_other_intents():
    client = _client()
    response = client.post(
        "/chat",
        json={"session_id": "s4", "intent": "prompt", "message": "hi"},
    )
    assert response.status_code == 422


def test_dashboard_schema_and_data_routes():
    client = _client()
    first = client.post(
        "/dashboard/schema",
        json={
            "session_id": "split",
            "message": "I need an AP dashboard",
            "tenant_id": TENANT,
            "repository_id": REPO,
        },
    )
    assert first.status_code == 200, first.text
    schema = first.json()["dashboard_result"]
    assert schema["phase"] == "schema"
    assert schema["data"] is None
    assert schema["message"] == "I need an AP dashboard"
    assert schema["kpis"][0].get("description")
    assert schema["charts"][0].get("description")
    assert "sections" not in schema
    assert "insights" not in schema
    for row in schema["kpis"]:
        row["enabled"] = row["id"] in {"total_ap", "overdue"}
    for row in schema["charts"]:
        row["enabled"] = row["id"] == "supplier_risk"
    second = client.post(
        "/dashboard/data",
        json={
            "session_id": "split",
            "tenant_id": TENANT,
            "repository_id": REPO,
            "dashboard_json": schema,
        },
    )
    assert second.status_code == 200, second.text
    assert "text/html" in second.headers["content-type"]
    html = second.text
    assert html.lstrip().startswith("<style>")
    assert "ez-dash" in html
    assert "ez-kicker" not in html
    assert "ez-title" not in html
    assert "ez-subtitle" not in html
    assert "EZOFIS" not in html
    assert "repository.Items" not in html
    assert not html.lstrip().startswith("{")
    assert "ez-section" not in html
    assert "AP Command Center" in html
    assert "ez-acc" in html
    assert "ez-acc-btn" in html
    assert "AI-generated insights" in html
    assert "Real-time" in html


def test_dashboard_schema_from_workflow_id():
    client = _client()
    response = client.post(
        "/dashboard/schema",
        json={
            "session_id": "wf1",
            "message": "I need a dashboard",
            "tenant_id": TENANT,
            "workflow_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["dashboard_result"]
    assert result["repository_id"] == REPO
    assert result["workflow_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_kpi_only_message_omits_charts_and_insights():
    client = _client()
    first = client.post(
        "/dashboard/schema",
        json={
            "session_id": "kpi-only",
            "message": "I need an AP dashboard only kpis",
            "tenant_id": TENANT,
            "repository_id": REPO,
        },
    )
    assert first.status_code == 200, first.text
    schema = first.json()["dashboard_result"]
    assert schema["kpis"]
    assert schema["charts"] == []
    second = client.post(
        "/dashboard/data",
        json={
            "session_id": "kpi-only",
            "message": "apply",
            "tenant_id": TENANT,
            "repository_id": REPO,
            "dashboard_json": schema,
        },
    )
    assert second.status_code == 200, second.text
    html = second.text
    assert "ez-kpi" in html
    assert 'class="ez-chart' not in html
    assert "AI-generated insights" not in html


class HrFakeStore(FakeStore):
    def resolve_target(self, *, tenant_id, repository_id=None, workflow_id=None):
        target = super().resolve_target(
            tenant_id=tenant_id, repository_id=repository_id, workflow_id=workflow_id
        )
        target["repository_name"] = "HR Files"
        target["workflow_name"] = "HR"
        return target

    def list_columns(self, *, tenant_id, schema, table):
        return ["Id", "Department", "EmployeeName", "CreatedAtUtc", "FileType", "FileSize", "Status", "IsDeleted"]

    def fetch_rows(self, *, tenant_id, schema, table, columns, limit=None):
        rows = [
            {
                "Id": "1",
                "Department": "Ops",
                "EmployeeName": "Ada",
                "CreatedAtUtc": date(2026, 1, 1),
                "FileType": "pdf",
                "FileSize": 1200,
                "Status": "Open",
                "IsDeleted": 0,
            },
            {
                "Id": "2",
                "Department": "Finance",
                "EmployeeName": "Bob",
                "CreatedAtUtc": date(2026, 2, 1),
                "FileType": "docx",
                "FileSize": 800,
                "Status": "Closed",
                "IsDeleted": 0,
            },
        ]
        return rows[:limit] if limit else rows


async def _generic_propose(*, message, target, columns, sample_rows=None):
    from app.propose import propose_generic

    return propose_generic(columns, message=message)


def test_non_ap_repo_gets_column_driven_widgets():
    store = HrFakeStore()
    app.state.store = store
    app.state.agent = DashboardAgent(store, proposer=_generic_propose)
    client = TestClient(app)
    response = client.post(
        "/chat",
        json={
            "session_id": "hr1",
            "intent": "dashboard",
            "message": "Show me an HR files dashboard by department",
            "payload": {"tenant_id": TENANT, "repository_id": REPO},
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["dashboard_result"]
    kpi_ids = [row["id"] for row in result["kpis"]]
    chart_ids = [row["id"] for row in result["charts"]]
    assert "record_count" in kpi_ids
    assert len(result["kpis"]) >= 4
    assert len(result["charts"]) >= 3
    assert "total_ap" not in kpi_ids
    assert "supplier_risk" not in chart_ids
    assert all(row.get("agg") for row in result["kpis"])
    assert all(row.get("columns", {}).get("group") for row in result["charts"])
    assert all(row.get("description") for row in result["kpis"])
    assert all(row.get("description") for row in result["charts"])


def _assert_schema_contract(body: dict) -> dict:
    assert "session_id" in body
    assert "reply" in body
    assert "correlation_id" in body
    assert "latency_ms" in body
    assert body.get("html") is None
    result = body["dashboard_result"]
    assert result["phase"] == "schema"
    assert result["data"] is None
    assert result.get("message")
    assert isinstance(result["kpis"], list) and result["kpis"]
    assert isinstance(result["charts"], list) and result["charts"]
    assert "sections" not in result
    assert "insights" not in result
    for kpi in result["kpis"]:
        assert kpi.get("id")
        assert kpi.get("label")
        assert kpi.get("description")
        assert kpi.get("agg")
        assert isinstance(kpi.get("columns"), dict)
        assert "enabled" in kpi
        assert kpi.get("order")
        assert kpi.get("position")
        assert kpi.get("color")
    for chart in result["charts"]:
        assert chart.get("id")
        assert chart.get("title") or chart.get("label")
        assert chart.get("description")
        assert chart.get("type")
        assert chart.get("agg")
        assert chart.get("grain") is not None
        assert isinstance(chart.get("columns"), dict)
        assert "enabled" in chart
        assert chart.get("order")
        assert chart.get("position")
        assert chart.get("span") in {1, 2}
        assert chart.get("color")
    return result


def test_schema_envelope_matches_documented_contract():
    client = _client()
    response = client.post(
        "/dashboard/schema",
        json={
            "session_id": "contract",
            "message": "I need an AP dashboard",
            "tenant_id": TENANT,
            "repository_id": REPO,
        },
    )
    assert response.status_code == 200, response.text
    _assert_schema_contract(response.json())


def test_schema_accepts_chat_payload_and_camel_case():
    client = _client()
    response = client.post(
        "/dashboard/schema",
        json={
            "sessionId": "nested",
            "message": "I need an AP dashboard",
            "payload": {
                "tenantId": TENANT,
                "repositoryId": REPO,
            },
        },
    )
    assert response.status_code == 200, response.text
    _assert_schema_contract(response.json())


def test_data_accepts_wrapped_chat_response_as_dashboard_json():
    client = _client()
    first = client.post(
        "/dashboard/schema",
        json={"session_id": "wrap", "tenant_id": TENANT, "repository_id": REPO, "message": "I need an AP dashboard"},
    )
    envelope = first.json()
    schema = envelope["dashboard_result"]
    second = client.post(
        "/dashboard/data",
        json={
            "session_id": "wrap",
            "tenant_id": TENANT,
            "repository_id": REPO,
            "dashboard_json": envelope,
        },
    )
    assert second.status_code == 200, second.text
    assert "text/html" in second.headers["content-type"]
    assert "ez-dash" in second.text
    third = client.post(
        "/dashboard/data",
        json={
            "session_id": "wrap2",
            "payload": {
                "tenant_id": TENANT,
                "repository_id": REPO,
                "dashboard_json": schema,
            },
        },
    )
    assert third.status_code == 200, third.text
    assert "ez-dash" in third.text


def test_data_validation_error_detail_is_string():
    client = _client()
    response = client.post(
        "/dashboard/data",
        json={"session_id": "bad", "tenant_id": TENANT, "repository_id": REPO},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, str)
    assert "dashboard_json" in detail
