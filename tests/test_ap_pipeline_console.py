"""Phase 4 — console AP pipeline GET/PUT."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.ap_pipeline import seed_platform_ap_pipeline, set_catalog_store
from app.ap_skills.types import DEFAULT_SKILL_ORDER
from app.catalog.store import CatalogStore
from app.config import get_settings
from tests.conftest import _IsolatedFakeRedis
from tests.fakes import FakeDBPool


@pytest.fixture
def pipeline_client(monkeypatch):
    monkeypatch.setattr(main_module, "Redis", _IsolatedFakeRedis)
    monkeypatch.setenv("AZURE_SOUTH_INDIA_API_KEY", "test-south-india-key")
    monkeypatch.setenv("AZURE_EAST_US_API_KEY", "test-east-us-key")
    monkeypatch.setenv("QWEN_MAC_API_KEY", "test-qwen-mac-key")
    monkeypatch.setenv("OCR_EXTRACT_URL", "")
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("CATALOG_DATABASE_URL", raising=False)
    monkeypatch.setenv("EZOFIS_LOGIN_EMAIL", "")
    monkeypatch.setenv("EZOFIS_LOGIN_PASSWORD", "")
    monkeypatch.setenv("EZOFIS_ENV", "trial")
    monkeypatch.setenv("AP_PIPELINE_FROM_DB", "true")
    get_settings.cache_clear()
    from app.llm.model_presets import set_runtime_presets

    set_runtime_presets(None)
    fake_db = FakeDBPool()

    async def fake_create_pool(*args, **kwargs):
        return fake_db

    monkeypatch.setattr(main_module.asyncpg, "create_pool", fake_create_pool)

    async def fake_ap_progress(self, **kwargs):
        return {"ok": True, "mock": True, **kwargs}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.report_ap_progress",
        fake_ap_progress,
    )

    with TestClient(main_module.app) as test_client:
        test_client.fake_db_pool = fake_db
        yield test_client

    get_settings.cache_clear()
    set_runtime_presets(None)
    set_catalog_store(None)


def test_console_get_ap_pipeline_platform(pipeline_client):
    res = pipeline_client.get("/console/ap-pipeline")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ap_pipeline_from_db"] is True
    assert body["platform"]["config"]["skills_order"] == list(DEFAULT_SKILL_ORDER)
    assert body["tenant"] is None
    assert "extract_invoice" in body["known_skills"]["all_skills"]


def test_console_put_tenant_pipeline_and_reload(pipeline_client):
    order = [s for s in DEFAULT_SKILL_ORDER if s != "po_match"]
    payload = {
        "tenant_id": "console-tenant-1",
        "config": {
            "skills_order": list(DEFAULT_SKILL_ORDER),
            "skills_enabled": order,
            "default_connector_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "default_resource": "SAP",
            "thresholds": {"approved": 90, "partial": None, "amount_tolerance": 0.05},
            "flags": {"use_planner": False, "force_hana_po_lookup": True},
        },
        "changed_by": "console-test",
    }
    put = pipeline_client.put("/console/ap-pipeline/tenant", json=payload)
    assert put.status_code == 200, put.text
    tenant = put.json()["tenant"]
    assert tenant["tenant_id"] == "console-tenant-1"
    assert "po_match" not in (tenant["config"]["skills_enabled"] or [])
    assert tenant["config"]["default_resource"] == "SAP"
    assert tenant["config"]["thresholds"]["approved"] == 90

    get = pipeline_client.get("/console/ap-pipeline", params={"tenant_id": "console-tenant-1"})
    assert get.status_code == 200
    body = get.json()
    assert body["tenant"]["config"]["skills_enabled"] == order
    assert len(body["logs"]) >= 1
    assert body["logs"][0]["action"] in ("CREATE", "UPDATE")


def test_console_put_rejects_unknown_skill(pipeline_client):
    res = pipeline_client.put(
        "/console/ap-pipeline/tenant",
        json={
            "tenant_id": "bad-tenant",
            "config": {"skills_order": ["not_a_skill"], "flags": {}},
        },
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_store_upsert_writes_audit_log():
    db = FakeDBPool()
    catalog = CatalogStore(db)
    await seed_platform_ap_pipeline(catalog)
    set_catalog_store(catalog)
    from app.ap_pipeline.store import upsert_tenant_pipeline

    row = await upsert_tenant_pipeline(
        catalog,
        tenant_id="audit-t",
        config={
            "skills_order": list(DEFAULT_SKILL_ORDER),
            "skills_enabled": None,
            "thresholds": {},
            "flags": {"use_planner": False, "force_hana_po_lookup": True},
        },
        changed_by="unit",
    )
    assert row["tenant_id"] == "audit-t"
    assert len(db.tenant_ap_pipeline_logs) == 1
    assert db.tenant_ap_pipeline_logs[0]["action"] == "CREATE"
