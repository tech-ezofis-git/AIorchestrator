"""Unit tests for AP skill resolution (default pipeline + explicit list)."""
import pytest

from app.ap_skills.planner import resolve_skills
from app.ap_skills.types import DEFAULT_SKILL_ORDER, ApSkillError


def test_null_skills_uses_default_order_with_finalize_and_move_next():
    skills = resolve_skills(requested=None)
    assert skills == list(DEFAULT_SKILL_ORDER)
    assert skills[-2] == "finalize_decision"
    assert skills[-1] == "workflow_move_next"


def test_enabled_arg_ignored_for_null_skills():
    # Tenant plan no longer gates the default pipeline.
    skills = resolve_skills(requested=None, enabled=["extract_invoice"])
    assert skills == list(DEFAULT_SKILL_ORDER)


def test_explicit_list_runs_exactly_those_ids():
    skills = resolve_skills(requested=["vendor_validate", "gl_match"])
    assert skills == ["vendor_validate", "gl_match"]


def test_explicit_phase2_skills_allowed_without_tenant_plan():
    skills = resolve_skills(requested=["gl_match", "finalize_decision", "workflow_move_next"])
    assert skills == ["gl_match", "finalize_decision", "workflow_move_next"]


def test_unknown_skill_is_rejected():
    with pytest.raises(ApSkillError, match="Unknown"):
        resolve_skills(requested=["not_a_real_skill"])


def test_empty_list_is_rejected():
    with pytest.raises(ApSkillError, match="No skills"):
        resolve_skills(requested=[])
