"""Phase 5 — AP Instructions soft packs."""
from __future__ import annotations

import pytest

from app.agent_skills.loader import get_skill
from app.ap_skills.instructions import ap_instructions_system_prompt, merge_system_prompt
from app.ap_skills.planner import maybe_reorder


def test_ap_disk_pack_loads():
    skill = get_skill("ap")
    assert skill.agent == "ap"
    assert "soft guidance" in skill.skill_body.lower() or "Soft" in skill.skill_body
    assert skill.system_prompt
    assert any("planner" in (r.description or "").lower() or r.path.stem == "planner" for r in skill.rules)


@pytest.mark.asyncio
async def test_ap_instructions_system_prompt_from_disk():
    text = await ap_instructions_system_prompt(tenant_id=None, settings=None)
    assert "AP Instructions" in text or "soft" in text.lower()
    assert "po_match" in text.lower() or "Python" in text


def test_merge_system_prompt():
    assert merge_system_prompt("base", "") == "base"
    assert "Additional AP Instructions" in merge_system_prompt("base", "addon")


@pytest.mark.asyncio
async def test_planner_includes_ap_instructions_in_system(monkeypatch):
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
    assert "order AP" in system or "JSON only" in system
    assert "Additional AP Instructions" in system or "soft" in system.lower()


def test_console_ap_pack_defaults(client):
    res = client.get("/console/agent-packs/ap/defaults")
    assert res.status_code == 200, res.text
    body = res.json()
    defaults = body.get("defaults") or {}
    skill = defaults.get("skill") or {}
    assert skill.get("body") or defaults.get("rules") is not None
    assert body.get("source") in ("disk", "catalog", None) or "defaults" in body
