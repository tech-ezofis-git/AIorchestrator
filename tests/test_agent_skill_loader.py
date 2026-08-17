"""Runtime SKILL.md + rules/*.mdc loader for Summary / OCR / Insight."""
from pathlib import Path

import pytest

from app.agent_skills.loader import (
    clear_skill_cache,
    default_skills_root,
    get_skill,
    load_skill_pack,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_skill_cache()
    yield
    clear_skill_cache()


def test_default_skills_root_contains_summary_ocr_and_insight_packs():
    root = default_skills_root()
    assert (root / "summary" / "SKILL.md").is_file()
    assert (root / "ocr" / "SKILL.md").is_file()
    assert (root / "insight" / "SKILL.md").is_file()
    assert list((root / "summary" / "rules").glob("*.mdc"))
    assert list((root / "ocr" / "rules").glob("*.mdc"))
    assert list((root / "insight" / "rules").glob("*.mdc"))


def test_load_summary_skill_includes_rules_in_system_prompt():
    skill = load_skill_pack("summary")
    assert skill.skill_id == "summarize_document"
    prompt = skill.system_prompt.lower()
    assert "document summarization" in prompt or "summarize" in prompt
    assert "key_facts_extracted" in prompt
    assert "<mark>" in prompt
    assert "label: value" in prompt or "label:value" in prompt.replace(" ", "")
    assert len(skill.rules) >= 3


def test_load_insight_skill_includes_output_contract():
    skill = load_skill_pack("insight")
    assert skill.skill_id == "generate_insights"
    prompt = skill.system_prompt.lower()
    assert "insights" in prompt
    assert "output contract" in prompt or '"insights"' in prompt
    assert len(skill.rules) >= 2


def test_load_ocr_skill_supports_n_substitution():
    from app.ocr_skills.rules import system_prompt

    prompt = system_prompt(max_recommended_fields=7).lower()
    assert "ocrresult" in prompt
    assert "at most 7 " in prompt
    assert "yyyy-mm-dd" in prompt


def test_custom_pack_dir_overrides_default(tmp_path: Path):
    pack = tmp_path / "summary"
    rules = pack / "rules"
    rules.mkdir(parents=True)
    (pack / "SKILL.md").write_text(
        "---\nname: custom_summary\ndescription: custom\n---\n\n# Custom\n\nCUSTOM_SKILL_TOKEN\n",
        encoding="utf-8",
    )
    (rules / "one.mdc").write_text(
        "---\ndescription: custom rule\nalwaysApply: true\n---\n\nCUSTOM_RULE_TOKEN\n",
        encoding="utf-8",
    )
    skill = load_skill_pack("summary", pack_dir=pack)
    assert skill.skill_id == "custom_summary"
    assert "CUSTOM_SKILL_TOKEN" in skill.system_prompt
    assert "CUSTOM_RULE_TOKEN" in skill.system_prompt


def test_get_skill_respects_summary_skill_dir_setting(tmp_path: Path, monkeypatch):
    pack = tmp_path / "summary"
    pack.mkdir()
    (pack / "SKILL.md").write_text(
        "---\nname: from_settings\n---\n\nSETTINGS_PACK\n",
        encoding="utf-8",
    )
    (pack / "rules").mkdir()

    class _Settings:
        agent_skills_root = None
        summary_skill_dir = str(pack)
        ocr_skill_dir = None
        insight_skill_dir = None

    skill = get_skill("summary", settings=_Settings())
    assert "SETTINGS_PACK" in skill.system_prompt
