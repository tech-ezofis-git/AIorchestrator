"""Unit tests for AP skill plan gating (Phase 1 + Phase 2)."""
import pytest

from app.ap_skills.planner import resolve_skills
from app.ap_skills.types import ALL_SKILL_ORDER, PHASE1_SKILL_ORDER, ApSkillError


def test_default_plan_uses_phase1_order():
    skills = resolve_skills(requested=None, enabled=list(PHASE1_SKILL_ORDER))
    assert skills == list(PHASE1_SKILL_ORDER)


def test_disabled_backorder_never_selected_on_default_plan():
    enabled = [s for s in PHASE1_SKILL_ORDER if s != "backorder_detect"]
    skills = resolve_skills(requested=None, enabled=enabled)
    assert "backorder_detect" not in skills
    assert "extract_invoice" in skills


def test_explicit_disabled_skill_is_rejected():
    enabled = [s for s in PHASE1_SKILL_ORDER if s != "backorder_detect"]
    with pytest.raises(ApSkillError, match="not enabled"):
        resolve_skills(requested=["backorder_detect"], enabled=enabled)


def test_unknown_skill_is_rejected():
    with pytest.raises(ApSkillError, match="Unknown"):
        resolve_skills(requested=["not_a_real_skill"], enabled=list(PHASE1_SKILL_ORDER))


def test_phase2_gl_known_but_gated_by_plan():
    with pytest.raises(ApSkillError, match="not enabled"):
        resolve_skills(requested=["gl_match"], enabled=list(PHASE1_SKILL_ORDER))


def test_phase2_skills_in_all_order_when_enabled():
    enabled = list(PHASE1_SKILL_ORDER) + ["gl_match", "grn_match", "matter_validate"]
    skills = resolve_skills(requested=None, enabled=enabled)
    assert skills.index("gl_match") < skills.index("duplicate_detect")
    assert "grn_match" in skills
    assert "matter_validate" in skills
    assert skills[-1] == "finalize_decision"
    assert "workflow_move_next" not in skills
    assert set(skills).issubset(set(ALL_SKILL_ORDER))
