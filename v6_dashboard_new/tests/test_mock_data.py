"""Test offline sample data mode."""
import pytest
from fastapi.testclient import TestClient

from app.dashboard_agent import DashboardAgent
from app.main import app
from app.store import DashboardStore


@pytest.fixture
def client():
    store = DashboardStore()
    app.state.store = store
    app.state.agent = DashboardAgent(store)
    return TestClient(app)


def test_health_sample_mode(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in {"ok", "degraded"}
    assert "sql" in data


def test_tenants_sample_mode(client):
    response = client.get("/tenants")
    assert response.status_code == 200
    data = response.json()
    assert "tenants" in data
    assert len(data["tenants"]) > 0
    assert data["tenants"][0]["available"] is True


def test_targets_sample_mode(client):
    response = client.get("/targets?tenant_id=3EE0E334-CCB9-4DFF-968A-9BAAE71A5231")
    assert response.status_code == 200
    data = response.json()
    assert "repositories" in data
    assert len(data["repositories"]) > 0
    assert data["repositories"][0]["name"] == "Accounts Payable"


def test_suggest_prompt_sample_mode(client):
    response = client.post(
        "/prompts",
        json={
            "session_id": "demo",
            "tenant_id": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231",
            "repository_id": "DF175C77-598C-4E92-BA55-318D222C3B52",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "prompt" in data
    assert len(data["prompt"]) > 0


def test_dashboard_flow_sample_mode(client):
    # Step 1: Propose schema
    schema_res = client.post(
        "/dashboard/schema",
        json={
            "session_id": "demo",
            "message": "I need an AP dashboard",
            "tenant_id": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231",
            "repository_id": "DF175C77-598C-4E92-BA55-318D222C3B52",
        },
    )
    assert schema_res.status_code == 200
    schema_body = schema_res.json()
    assert "dashboard_result" in schema_body
    dashboard_json = schema_body["dashboard_result"]
    assert len(dashboard_json.get("kpis", [])) > 0
    assert len(dashboard_json.get("charts", [])) > 0

    # Step 2: Hydrate data (returns text/html)
    data_res = client.post(
        "/dashboard/data",
        json={
            "session_id": "demo",
            "message": "apply",
            "tenant_id": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231",
            "repository_id": "DF175C77-598C-4E92-BA55-318D222C3B52",
            "dashboard_json": dashboard_json,
        },
    )
    assert data_res.status_code == 200
    assert "text/html" in data_res.headers.get("content-type", "")
    assert len(data_res.text) > 50
