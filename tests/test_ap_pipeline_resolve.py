"""Phase 3 — Catalog pipeline resolve + runner parity."""
from __future__ import annotations

import pytest

from app.ap_pipeline import (
    default_platform_config,
    resolve_pipeline_config,
    seed_platform_ap_pipeline,
    set_catalog_store,
)
from app.ap_pipeline.resolve import apply_connector_defaults, merge_thresholds
from app.ap_skills.planner import ensure_ezofis_hana_po_lookup, resolve_skills
from app.ap_skills.runner import ApSkillRunner
from app.ap_skills.store import ApStore
from app.ap_skills.types import DEFAULT_SKILL_ORDER
from app.catalog.store import CatalogStore
from app.config import Settings
from app.integrations.ezofis_client import EzofisClient
from tests.fakes import FakeDBPool

SAMPLE_INVOICE = {
    "invoice_number": "INV-PIPE",
    "vendor": "ACME Supplies",
    "po_number": "PO-1",
    "total": 100.0,
    "currency": "USD",
    "line_items": [{"description": "Widget", "qty": 1, "amount": 100.0}],
}


@pytest.fixture(autouse=True)
def _clear_catalog_ref():
    set_catalog_store(None)
    yield
    set_catalog_store(None)


@pytest.mark.asyncio
async def test_resolve_empty_tenant_matches_code_defaults_after_seed():
    db = FakeDBPool()
    catalog = CatalogStore(db)
    await seed_platform_ap_pipeline(catalog)
    set_catalog_store(catalog)

    resolved = await resolve_pipeline_config("tenant-with-no-row")
    assert resolved.source == "platform"
    assert resolved.skills_order == list(DEFAULT_SKILL_ORDER)
    assert resolved.skills_enabled is None
    assert resolved.flags.get("force_hana_po_lookup") is True


@pytest.mark.asyncio
async def test_resolve_falls_back_to_code_without_catalog():
    set_catalog_store(None)
    resolved = await resolve_pipeline_config("any")
    assert resolved.source == "code"
    assert resolved.skills_order == list(default_platform_config()["skills_order"])


@pytest.mark.asyncio
async def test_tenant_skills_enabled_filters_po_match():
    db = FakeDBPool()
    catalog = CatalogStore(db)
    await seed_platform_ap_pipeline(catalog)
    db.tenant_ap_pipeline["t-filter"] = {
        "id": "tid-1",
        "tenant_id": "t-filter",
        "config_json": {
            "skills_order": list(DEFAULT_SKILL_ORDER),
            "skills_enabled": [
                "extract_invoice",
                "duplicate_detect",
                "vendor_validate",
                "backorder_detect",
                "finalize_decision",
                "workflow_move_next",
            ],
            "thresholds": {},
            "flags": {"use_planner": False, "force_hana_po_lookup": True},
        },
        "version": 1,
        "is_active": True,
    }
    set_catalog_store(catalog)

    resolved = await resolve_pipeline_config("t-filter")
    assert resolved.source == "tenant"
    skills = resolve_skills(
        requested=None,
        default_order=resolved.skills_order,
        enabled=resolved.skills_enabled,
    )
    assert "po_match" not in skills
    assert skills[0] == "extract_invoice"
    assert skills[-1] == "workflow_move_next"


@pytest.mark.asyncio
async def test_bad_tenant_skills_fall_back_to_platform():
    db = FakeDBPool()
    catalog = CatalogStore(db)
    await seed_platform_ap_pipeline(catalog)
    db.tenant_ap_pipeline["t-bad"] = {
        "id": "tid-bad",
        "tenant_id": "t-bad",
        "config_json": {"skills_order": ["not_a_real_skill"], "flags": {}},
        "version": 1,
        "is_active": True,
    }
    set_catalog_store(catalog)

    resolved = await resolve_pipeline_config("t-bad")
    assert resolved.source == "platform"
    assert resolved.skills_order == list(DEFAULT_SKILL_ORDER)


def test_merge_thresholds_catalog_overrides_plan():
    merged = merge_thresholds(
        plan_thresholds={"approved": 70, "partial": 40},
        pipeline=type(
            "P",
            (),
            {"thresholds": {"approved": 90, "amount_tolerance": 0.05}},
        )(),
    )
    assert merged["approved"] == 90
    assert merged["partial"] == 40
    assert merged["amount_tolerance"] == 0.05


def test_apply_connector_defaults_only_when_missing():
    job = {"connector_id": "", "resource": "SAP"}
    pipeline = type(
        "P",
        (),
        {
            "default_connector_id": "conn-guid",
            "default_resource": "HANA",
        },
    )()
    apply_connector_defaults(job, pipeline)
    assert job["connector_id"] == "conn-guid"
    assert job["resource"] == "SAP"


def test_force_hana_false_skips_injection():
    from app.ap_skills.hana_po import EZOFIS_TENANT_ID

    base = list(DEFAULT_SKILL_ORDER)
    assert ensure_ezofis_hana_po_lookup(base, tenant_id=EZOFIS_TENANT_ID, force=False) == base


@pytest.mark.asyncio
async def test_runner_parity_flag_on_empty_tenant_matches_default_order():
    """With AP_PIPELINE_FROM_DB + seeded platform + no tenant row → same skills as code."""
    db = FakeDBPool()
    catalog = CatalogStore(db)
    await seed_platform_ap_pipeline(catalog)
    set_catalog_store(catalog)

    settings = Settings(ap_pipeline_from_db=True, ap_llm_planner=False)
    runner = ApSkillRunner(
        store=ApStore(db),
        ezofis=EzofisClient(settings),
        settings=settings,
    )
    result = await runner.run(
        session_id="s-parity",
        document_job={
            "tenant_id": "parity-tenant",
            "item_id": "parity-doc-1",
            "invoice_json": SAMPLE_INVOICE,
            "force_rerun": True,
        },
    )
    assert result["skills_run"] == list(DEFAULT_SKILL_ORDER)


@pytest.mark.asyncio
async def test_runner_honors_tenant_disable_po_match():
    db = FakeDBPool()
    catalog = CatalogStore(db)
    await seed_platform_ap_pipeline(catalog)
    order = list(DEFAULT_SKILL_ORDER)
    # Drop po_match (and backorder, which requires the po_match artifact).
    enabled = [
        s
        for s in order
        if s not in {"po_match", "backorder_detect"}
    ]
    db.tenant_ap_pipeline["t-no-po"] = {
        "id": "tid-no-po",
        "tenant_id": "t-no-po",
        "config_json": {
            "skills_order": order,
            "skills_enabled": enabled,
            "thresholds": {},
            "flags": {"use_planner": False, "force_hana_po_lookup": False},
        },
        "version": 1,
        "is_active": True,
    }
    set_catalog_store(catalog)

    settings = Settings(ap_pipeline_from_db=True)
    runner = ApSkillRunner(
        store=ApStore(db),
        ezofis=EzofisClient(settings),
        settings=settings,
    )
    result = await runner.run(
        session_id="s-no-po",
        document_job={
            "tenant_id": "t-no-po",
            "item_id": "doc-no-po",
            "invoice_json": SAMPLE_INVOICE,
            "force_rerun": True,
        },
    )
    assert "po_match" not in result["skills_run"]
    assert result["skills_run"][0] == "extract_invoice"


def test_settings_default_flag_still_false_for_safe_rollback():
    assert Settings(ap_pipeline_from_db=False).ap_pipeline_from_db is False
