"""Unit tests for Catalog agent pack seed + overlay (no live DB required)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_skills.loader import _parse_frontmatter, default_skills_root
from app.agent_packs.overlay import get_agent_skill, set_catalog_store


@pytest.mark.asyncio
async def test_get_agent_skill_falls_back_to_disk_without_catalog():
    set_catalog_store(None)
    skill = await get_agent_skill("summary", tenant_id=None)
    assert "summar" in skill.skill_body.lower() or "JSON" in skill.skill_body
    assert skill.rules


def test_disk_packs_parse_for_all_markdown_agents():
    root = default_skills_root()
    for agent in (
        "summary",
        "classification",
        "document_intelligent",
        "ocr",
        "insight",
        "prompt",
        "dashboard-prompts",
        "dashboard-schema",
        "dashboard-data",
        "ftl_qualifier",
        "ftl_quote_estimator",
    ):
        skill_path = root / agent / "SKILL.md"
        assert skill_path.is_file(), agent
        _meta, body = _parse_frontmatter(skill_path.read_text(encoding="utf-8"))
        assert body.strip()
        rules = list((root / agent / "rules").glob("*.mdc"))
        assert rules, agent


@pytest.mark.asyncio
async def test_ftl_qualifier_pack_loads_from_disk():
    set_catalog_store(None)
    skill = await get_agent_skill("ftl_qualifier", tenant_id=None)
    assert "OEM or Wittur" in skill.skill_body
    template_rules = [r for r in skill.rules if r.description.lower().startswith("template:")]
    assert len(template_rules) == 2
    assert any("qualified" in r.description.lower() for r in template_rules)
    assert any("disqualify" in r.description.lower() for r in template_rules)


@pytest.mark.asyncio
async def test_ftl_quote_estimator_pack_loads_from_disk():
    set_catalog_store(None)
    skill = await get_agent_skill("ftl_quote_estimator", tenant_id=None)
    assert "quote-building analyst" in skill.skill_body
    assert "OL35-RC" in skill.skill_body
    template_rules = [r for r in skill.rules if r.description.lower().startswith("template:")]
    assert len(template_rules) == 1
    assert "remarks-provisional-example" in template_rules[0].description
    assert any("roller" in r.description.lower() for r in skill.rules)


def test_skill_dict_splits_templates_from_references():
    from app.agent_skills.types import LoadedRule, LoadedSkill
    from app.ftl.qualifier.skill_store import skill_dict_from_loaded

    loaded = LoadedSkill(
        agent="ftl_qualifier",
        skill_id="ftl_qualifier",
        name="ftl_qualifier",
        description="",
        skill_body="Qualify door packages.",
        rules=(
            LoadedRule(path=Path("oem.mdc"), description="OEM guide", body="Check brands."),
            LoadedRule(
                path=Path("qualified.mdc"),
                description="template: internal-qualified-notification",
                body="Subject: QUALIFY",
            ),
        ),
    )
    skill = skill_dict_from_loaded(loaded, {"name": "ftl-rfq-qualifier", "evals": []})
    assert skill["instructions"] == "Qualify door packages."
    assert skill["references"] == [{"title": "OEM guide", "content": "Check brands."}]
    assert skill["templates"] == [
        {"name": "internal-qualified-notification", "content": "Subject: QUALIFY"}
    ]


@pytest.mark.asyncio
async def test_dashboard_packs_load_from_disk():
    set_catalog_store(None)
    prompts = await get_agent_skill("dashboard-prompts", tenant_id=None)
    schema = await get_agent_skill("dashboard-schema", tenant_id=None)
    data = await get_agent_skill("dashboard-data", tenant_id=None)
    assert "prompt" in prompts.system_prompt.lower()
    assert "kpis" in schema.system_prompt.lower()
    assert "insights" in data.system_prompt.lower()
