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
    for agent in ("summary", "ocr", "insight", "prompt"):
        skill_path = root / agent / "SKILL.md"
        assert skill_path.is_file(), agent
        _meta, body = _parse_frontmatter(skill_path.read_text(encoding="utf-8"))
        assert body.strip()
        rules = list((root / agent / "rules").glob("*.mdc"))
        assert rules, agent
