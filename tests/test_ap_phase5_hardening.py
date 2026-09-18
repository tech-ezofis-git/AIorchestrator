"""Phase 5 — hardening matrix across Phases 1–4.

Covers: Catalog pack-primary prompts, pipeline-from-DB, PoMaster routing,
move-next advance / failure, and Catalog policy labels/step name.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ap_pipeline import (
    default_platform_config,
    resolve_pipeline_config,
    seed_platform_ap_pipeline,
    set_catalog_store,
)
from app.ap_pipeline.policy import review_label, workflow_step_name
from app.ap_skills import workflow_move_next as move_mod
from app.ap_skills.extract_invoice import _EXTRACT_PROMPT, _structure_with_llm
from app.ap_skills.hana_po import wants_form_po_master, wants_sap_or_hana_po_master
from app.ap_skills.instructions import resolve_system_prompt
from app.ap_skills.planner import ensure_ezofis_hana_po_lookup, resolve_skills
from app.ap_skills.runner import ApSkillRunner
from app.ap_skills.store import ApStore
from app.ap_skills.types import ApContext, DEFAULT_SKILL_ORDER
from app.catalog.store import CatalogStore
from app.config import Settings
from app.integrations.ezofis_client import EzofisClient
from tests.fakes import FakeDBPool

SAMPLE_INVOICE = {
    "invoice_number": "INV-P5",
    "vendor": "ACME Supplies",
    "po_number": "PO-P5",
    "total": 100.0,
    "currency": "USD",
    "line_items": [{"description": "Widget", "qty": 1, "amount": 100.0}],
}


@pytest.fixture(autouse=True)
def _clear_catalog_ref():
    set_catalog_store(None)
    yield
    set_catalog_store(None)


# --- Phase 1: pack-primary prompts ---


@pytest.mark.asyncio
async def test_phase5_pack_primary_wins_over_code_fallback(monkeypatch):
    async def fake_pack(**kwargs):
        return "PHASE5_CATALOG_EXTRACT_PROMPT"

    monkeypatch.setattr(
        "app.ap_skills.instructions.ap_instructions_system_prompt",
        fake_pack,
    )
    assert await resolve_system_prompt(code_fallback=_EXTRACT_PROMPT) == "PHASE5_CATALOG_EXTRACT_PROMPT"


@pytest.mark.asyncio
async def test_phase5_extract_llm_uses_resolved_pack(monkeypatch):
    captured = {}

    class FakeLlm:
        async def chat_completion(self, messages, **kwargs):
            captured["system"] = messages[0]["content"]
            return {
                "content": '{"doc_type":"invoice","invoice_number":"INV-P5","vendor":"ACME",'
                '"po_number":"PO-P5","total":100,"currency":"USD","line_items":[]}',
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    async def fake_resolve(*, code_fallback, tenant_id=None, settings=None):
        return "PHASE5_PRIMARY_SYSTEM"

    monkeypatch.setattr(
        "app.ap_skills.instructions.resolve_system_prompt",
        fake_resolve,
    )
    ctx = ApContext(
        tenant_id="t-p5",
        item_key="i1",
        run_id="r1",
        session_id="s1",
        invoice_json=None,
        artifacts={},
        settings=SimpleNamespace(),
        ezofis=None,
        llm=FakeLlm(),
    )
    invoice, usage = await _structure_with_llm(ctx, "Invoice INV-P5 ACME Total 100")
    assert invoice is not None and usage is not None
    assert captured["system"] == "PHASE5_PRIMARY_SYSTEM"


# --- Phase 2: pipeline from DB ---


@pytest.mark.asyncio
async def test_phase5_pipeline_from_db_drives_skill_order():
    db = FakeDBPool()
    catalog = CatalogStore(db)
    await seed_platform_ap_pipeline(catalog)
    custom = ["extract_invoice", "finalize_decision", "workflow_move_next"]
    db.tenant_ap_pipeline["t-p5-pipe"] = {
        "id": "tid-p5",
        "tenant_id": "t-p5-pipe",
        "config_json": {
            "skills_order": custom,
            "skills_enabled": None,
            "thresholds": {"approved": 85},
            "flags": {"use_planner": False, "force_hana_po_lookup": False},
            "policy": {"workflow_step_name": "AP AGENT 2"},
        },
        "version": 1,
        "is_active": True,
    }
    set_catalog_store(catalog)

    resolved = await resolve_pipeline_config("t-p5-pipe")
    assert resolved.source == "tenant"
    assert resolved.skills_order == custom
    assert resolved.thresholds.get("approved") == 85
    assert resolved.policy.get("workflow_step_name") == "AP AGENT 2"

    settings = Settings(ap_pipeline_from_db=True, ap_llm_planner=False)
    runner = ApSkillRunner(
        store=ApStore(db),
        ezofis=EzofisClient(settings),
        settings=settings,
    )
    result = await runner.run(
        session_id="s-p5-pipe",
        document_job={
            "tenant_id": "t-p5-pipe",
            "item_id": "doc-p5-pipe",
            "invoice_json": SAMPLE_INVOICE,
            "force_rerun": True,
        },
    )
    assert result["skills_run"] == custom


@pytest.mark.asyncio
async def test_phase5_pipeline_flag_off_rollback_uses_code_defaults():
    """AP_PIPELINE_FROM_DB=false → DEFAULT_SKILL_ORDER even if Catalog has a tenant row."""
    db = FakeDBPool()
    catalog = CatalogStore(db)
    await seed_platform_ap_pipeline(catalog)
    db.tenant_ap_pipeline["t-p5-off"] = {
        "id": "tid-off",
        "tenant_id": "t-p5-off",
        "config_json": {
            "skills_order": ["extract_invoice", "finalize_decision"],
            "skills_enabled": None,
            "thresholds": {},
            "flags": {"use_planner": False, "force_hana_po_lookup": False},
        },
        "version": 1,
        "is_active": True,
    }
    set_catalog_store(catalog)

    settings = Settings(ap_pipeline_from_db=False, ap_llm_planner=False)
    runner = ApSkillRunner(
        store=ApStore(db),
        ezofis=EzofisClient(settings),
        settings=settings,
    )
    result = await runner.run(
        session_id="s-p5-off",
        document_job={
            "tenant_id": "t-p5-off",
            "item_id": "doc-p5-off",
            "invoice_json": SAMPLE_INVOICE,
            "force_rerun": True,
        },
    )
    assert result["skills_run"] == list(DEFAULT_SKILL_ORDER)


# --- Phase 3: PoMaster matrix ---


@pytest.mark.parametrize(
    "job,expect_form,expect_sap",
    [
        ({}, True, False),
        ({"master_source": "InternalForm", "master_form_id": "form-1"}, True, False),
        ({"master_source": "SAP", "resource": "SAP", "connector_id": "c1"}, False, True),
        ({"master_source": "HANA", "resource": "HANA", "connector_id": "c1"}, False, True),
        ({"master_source": "QuickBooks", "resource": "QUICKBOOKS"}, False, False),
        ({"resource": "SAGE"}, False, False),
    ],
)
def test_phase5_pomaster_matrix(job, expect_form, expect_sap):
    assert wants_form_po_master(job) is expect_form
    assert wants_sap_or_hana_po_master(job) is expect_sap


def test_phase5_force_flag_injects_lookup_for_sap_any_tenant():
    base = list(DEFAULT_SKILL_ORDER)
    injected = ensure_ezofis_hana_po_lookup(
        base,
        tenant_id="any-tenant",
        force=True,
        document_job={"master_source": "SAP", "resource": "SAP"},
    )
    assert "po_lookup_sap" in injected
    assert injected.index("po_lookup_sap") < injected.index("po_match")

    skipped = ensure_ezofis_hana_po_lookup(
        base,
        tenant_id="any-tenant",
        force=False,
        document_job={"master_source": "SAP", "resource": "SAP"},
    )
    assert skipped == base


# --- Phase 4 + move-next ---


@pytest.mark.asyncio
async def test_phase5_move_next_matched_ok_uses_catalog_review_label(monkeypatch):
    async def fake_activity(ctx, job, workflow_id):
        assert workflow_step_name(thresholds=ctx.thresholds) == "AP AGENT CUSTOM"
        return "ACT-1"

    captured = {}

    class FakeEzofis:
        async def workflow_move_next(self, **kwargs):
            captured["payload"] = kwargs.get("payload") or {}
            return {"ok": True, "message": "advanced"}

    monkeypatch.setattr(move_mod, "_resolve_activity_id", fake_activity)

    ctx = ApContext(
        tenant_id="t1",
        item_key="item-1",
        run_id="run-1",
        session_id="s1",
        invoice_json={"doc_type": "invoice", "po_number": "PO-1", "invoice_number": "INV-1"},
        artifacts={
            "finalize_decision": {
                "decision": "MATCHED",
                "reason": "PO matched.",
                "used_mock_data": False,
            }
        },
        settings=SimpleNamespace(ap_agent_workflow_step_name="AP AGENT 1"),
        ezofis=FakeEzofis(),
        thresholds={
            "workflow_step_name": "AP AGENT CUSTOM",
            "review_labels": {
                "MATCHED": "Matched",
                "PARTIALLY_MATCHED": "Partially Matched",
                "NOT_MATCHED": "Not Matched",
                "NON_INVOICE": "Non-Invoice",
            },
        },
        document_job={
            "instance_id": "inst-1",
            "workflow_id": "wf-1",
            "activity_id": "ACT-1",
        },
    )
    result = await move_mod.run(ctx)
    assert result.data["ok"] is True
    assert result.data["review"] == "Matched"
    assert captured["payload"].get("review") == "Matched"


@pytest.mark.asyncio
async def test_phase5_move_next_not_advanced_fails_progress_path(monkeypatch):
    async def fake_activity(ctx, job, workflow_id):
        return "ACT-1"

    monkeypatch.setattr(move_mod, "_resolve_activity_id", fake_activity)

    ctx = ApContext(
        tenant_id="t1",
        item_key="item-1",
        run_id="run-1",
        session_id="s1",
        invoice_json={"doc_type": "invoice"},
        artifacts={
            "finalize_decision": {
                "decision": "PARTIALLY_MATCHED",
                "reason": "partial",
                "used_mock_data": False,
            }
        },
        settings=SimpleNamespace(),
        ezofis=SimpleNamespace(
            workflow_move_next=AsyncMock(
                return_value={
                    "ok": True,
                    "message": "AP agent review recorded; workflow not advanced (review is not Approve).",
                }
            )
        ),
        thresholds={"review_labels": default_platform_config()["policy"]["review_labels"]},
        document_job={"instance_id": "inst-1", "workflow_id": "wf-1", "activity_id": "ACT-1"},
    )
    result = await move_mod.run(ctx)
    assert result.data["ok"] is False
    assert review_label("PARTIALLY_MATCHED", thresholds=ctx.thresholds) == "Partially Matched"


def test_phase5_platform_seed_includes_policy_and_thresholds():
    cfg = default_platform_config()
    assert cfg["thresholds"]["approved"] == 80
    assert cfg["policy"]["workflow_step_name"] == "AP AGENT 1"
    assert "MATCHED" in cfg["policy"]["review_labels"]
    skills = resolve_skills(requested=None, default_order=cfg["skills_order"])
    assert skills[0] == "extract_invoice"
    assert skills[-1] == "workflow_move_next"
