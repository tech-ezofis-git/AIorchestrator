"""Unit tests for AP skill plan gating and artifact re-runs."""
import pytest

from app.ap_skills.planner import resolve_skills
from app.ap_skills.types import PHASE1_SKILL_ORDER, ApSkillError


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
        resolve_skills(requested=["gl_match"], enabled=list(PHASE1_SKILL_ORDER))
