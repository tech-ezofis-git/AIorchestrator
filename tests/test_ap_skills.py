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


def test_ezofis_default_pipeline_injects_hana_po_lookup():
    from app.ap_skills.hana_po import EZOFIS_TENANT_ID
    from app.ap_skills.planner import ensure_ezofis_hana_po_lookup

    skills = ensure_ezofis_hana_po_lookup(
        list(DEFAULT_SKILL_ORDER), tenant_id=EZOFIS_TENANT_ID
    )
    assert "po_lookup_sap" in skills
    assert skills.index("po_lookup_sap") < skills.index("po_match")


def test_ezofis_inject_skipped_when_lookup_already_present():
    from app.ap_skills.hana_po import EZOFIS_TENANT_ID
    from app.ap_skills.planner import ensure_ezofis_hana_po_lookup

    base = ["extract_invoice", "po_lookup_sap", "po_match", "finalize_decision"]
    assert ensure_ezofis_hana_po_lookup(base, tenant_id=EZOFIS_TENANT_ID) == base


def test_non_ezofis_tenant_does_not_inject_hana_lookup():
    from app.ap_skills.planner import ensure_ezofis_hana_po_lookup

    base = list(DEFAULT_SKILL_ORDER)
    assert ensure_ezofis_hana_po_lookup(base, tenant_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") == base
