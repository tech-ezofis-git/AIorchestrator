"""Phase 4 — Catalog-driven AP product policy (labels, step name, thresholds)."""
from __future__ import annotations

from app.ap_pipeline.defaults import default_platform_config
from app.ap_pipeline.policy import (
    apply_policy_to_thresholds,
    line_match_floor,
    review_label,
    workflow_step_name,
)
from app.ap_pipeline.resolve import ResolvedPipeline, attach_pipeline_policy
from app.ap_skills.agent_validation_response import build_aiagent_response


def test_review_label_uses_catalog_override():
    labels = dict(default_platform_config()["policy"]["review_labels"])
    labels["MATCHED"] = "Fully Matched"
    assert (
        review_label("MATCHED", thresholds={"review_labels": labels}) == "Fully Matched"
    )
    assert review_label("NOT_MATCHED") == "Not Matched"


def test_workflow_step_name_from_policy():
    assert workflow_step_name(thresholds={"workflow_step_name": "AP AGENT 2"}) == "AP AGENT 2"
    assert workflow_step_name() == "AP AGENT 1"


def test_line_match_floor_from_policy():
    assert line_match_floor(thresholds={"line_match_floor": 0.7}) == 0.7
    assert line_match_floor() == 0.5


def test_attach_pipeline_policy_flattens_onto_thresholds():
    pipeline = ResolvedPipeline(
        skills_order=list(default_platform_config()["skills_order"]),
        thresholds={"approved": 90},
        policy={
            "workflow_step_name": "CUSTOM STEP",
            "review_labels": {"MATCHED": "Matched"},
            "line_match_floor": 0.6,
        },
        source="tenant",
    )
    out = attach_pipeline_policy({}, pipeline)
    assert out["approved"] == 90
    assert out["workflow_step_name"] == "CUSTOM STEP"
    assert out["line_match_floor"] == 0.6
    assert out["partial"] == 50  # filled from platform defaults


def test_apply_policy_fills_numeric_defaults():
    out = apply_policy_to_thresholds({})
    assert out["approved"] == 80
    assert out["partial"] == 50
    assert out["amount_tolerance"] == 0.02


def test_build_aiagent_response_honors_threshold_labels():
    out = build_aiagent_response(
        decision="MATCHED",
        reason="ok",
        artifacts={"finalize_decision": {"decision": "MATCHED", "reason": "ok", "score": 100}},
        invoice={"invoice_number": "1", "vendor": "A", "total": 1},
        thresholds={"review_labels": {"MATCHED": "Matched", "NOT_MATCHED": "Not Matched"}},
    )
    assert out["decision"] == "Matched"
