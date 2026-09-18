"""Phase 1 — AP Instructions as primary Catalog/disk system prompt (OCR pattern)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent_skills.loader import get_skill
from app.agent_skills.types import LoadedRule, LoadedSkill
from app.ap_skills.extract_invoice import _EXTRACT_PROMPT, _structure_with_llm
from app.ap_skills.instructions import (
    ap_instructions_system_prompt,
    merge_system_prompt,
    resolve_system_prompt,
)
from app.ap_skills.planner import maybe_reorder
from app.ap_skills.types import ApContext


def test_ap_disk_pack_loads():
    skill = get_skill("ap")
    assert skill.agent == "ap"
    assert "AP Instructions" in skill.skill_body
    assert skill.system_prompt
    stems = {r.path.stem for r in skill.rules}
    assert "extract" in stems
    assert "planner" in stems
    assert "invoice_number" in skill.system_prompt
    assert "allowed list" in skill.system_prompt.lower() or "allowed_skills" in skill.system_prompt.lower() or "skill ids" in skill.system_prompt.lower()


@pytest.mark.asyncio
async def test_ap_instructions_system_prompt_from_disk():
    text = await ap_instructions_system_prompt(tenant_id=None, settings=None)
    assert "AP Instructions" in text
    assert "Extract AP invoice fields" in text
    assert "po_match" in text.lower() or "Python" in text


@pytest.mark.asyncio
async def test_resolve_system_prompt_pack_wins_over_code(monkeypatch):
    async def fake_pack(**kwargs):
        return "PACK_PRIMARY_PROMPT"

    monkeypatch.setattr(
        "app.ap_skills.instructions.ap_instructions_system_prompt",
        fake_pack,
    )
    out = await resolve_system_prompt(code_fallback="CODE_FALLBACK")
    assert out == "PACK_PRIMARY_PROMPT"


@pytest.mark.asyncio
async def test_resolve_system_prompt_falls_back_when_pack_empty(monkeypatch):
    async def fake_pack(**kwargs):
        return ""

    monkeypatch.setattr(
        "app.ap_skills.instructions.ap_instructions_system_prompt",
        fake_pack,
    )
    out = await resolve_system_prompt(code_fallback="CODE_FALLBACK")
    assert out == "CODE_FALLBACK"


def test_merge_system_prompt_legacy():
    assert merge_system_prompt("base", "") == "base"
    assert "Additional AP Instructions" in merge_system_prompt("base", "addon")


@pytest.mark.asyncio
async def test_planner_uses_pack_as_primary_system(monkeypatch):
    captured = {}

    class FakeLlm:
        async def chat_completion(self, messages, **kwargs):
            captured["messages"] = messages
            return {"content": '{"skills": ["extract_invoice", "finalize_decision"]}'}

    out = await maybe_reorder(
        ["extract_invoice", "finalize_decision"],
        llm=FakeLlm(),
        use_planner=True,
        tenant_id="t1",
        settings=None,
    )
    assert out == ["extract_invoice", "finalize_decision"]
    system = captured["messages"][0]["content"]
    # Pack is primary — no soft "Additional AP Instructions" append.
    assert "Additional AP Instructions" not in system
    assert "order AP" in system.lower() or "JSON only" in system or "skill ids" in system.lower()


@pytest.mark.asyncio
async def test_extract_uses_catalog_pack_as_primary(monkeypatch):
    captured = {}

    class FakeLlm:
        async def chat_completion(self, messages, **kwargs):
            captured["messages"] = messages
            return {
                "content": '{"doc_type":"invoice","invoice_number":"INV-1","vendor":"ACME",'
                '"po_number":"","total":10,"currency":"USD","line_items":[]}',
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    async def fake_resolve(*, code_fallback, tenant_id=None, settings=None):
        assert code_fallback == _EXTRACT_PROMPT
        return "CUSTOM_CATALOG_EXTRACT_PROMPT"

    monkeypatch.setattr(
        "app.ap_skills.instructions.resolve_system_prompt",
        fake_resolve,
    )

    ctx = ApContext(
        tenant_id="t1",
        item_key="item-1",
        run_id="run-1",
        session_id="sess-1",
        invoice_json=None,
        artifacts={},
        settings=SimpleNamespace(),
        ezofis=None,
        llm=FakeLlm(),
        document_job={},
    )
    invoice, usage = await _structure_with_llm(ctx, "Invoice # INV-1 ACME Total 10")
    assert invoice is not None
    assert usage is not None
    assert captured["messages"][0]["content"] == "CUSTOM_CATALOG_EXTRACT_PROMPT"


def test_console_ap_pack_defaults(client):
    res = client.get("/console/agent-packs/ap/defaults")
    assert res.status_code == 200, res.text
    body = res.json()
    defaults = body.get("defaults") or {}
    skill = defaults.get("skill") or {}
    assert skill.get("body") or defaults.get("rules") is not None
    assert body.get("source") in ("disk", "catalog", None) or "defaults" in body


@pytest.mark.asyncio
async def test_tenant_rule_changes_primary_prompt(monkeypatch):
    """Exit criteria: Catalog/tenant instructions change extract prompt without code."""
    base = get_skill("ap")
    custom = LoadedSkill(
        agent="ap",
        skill_id=base.skill_id,
        name=base.name,
        description=base.description,
        skill_body=base.skill_body,
        rules=base.rules
        + (
            LoadedRule(
                path=Path("tenant-custom.mdc"),
                description="Tenant custom extract rule",
                body="ALWAYS set currency to TENANT-CURRENCY-TEST.",
                always_apply=True,
            ),
        ),
        pack_dir=base.pack_dir,
    )

    async def fake_get_agent_skill(agent, *, tenant_id=None, settings=None):
        assert agent == "ap"
        return custom

    monkeypatch.setattr(
        "app.agent_packs.overlay.get_agent_skill",
        fake_get_agent_skill,
    )
    text = await ap_instructions_system_prompt(tenant_id="tenant-x", settings=None)
    assert "TENANT-CURRENCY-TEST" in text
