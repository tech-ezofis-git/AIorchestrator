"""Resolve AP product policy from Catalog pipeline (thresholds + policy block).

Deterministic skill code stays in Python; labels / step name / floors come from
Catalog so product changes do not require an Agents deploy.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from app.ap_pipeline.defaults import DEFAULT_REVIEW_LABELS, default_platform_config
from app.ap_skills.types import DEFAULT_LINE_MATCH_FLOOR


def _policy_from(mapping: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(mapping, Mapping):
        return {}
    nested = mapping.get("policy")
    if isinstance(nested, dict):
        return dict(nested)
    # Flattened onto thresholds by the runner (Phase 4).
    out: dict[str, Any] = {}
    for key in ("workflow_step_name", "review_labels", "line_match_floor"):
        if key in mapping and mapping[key] is not None:
            out[key] = mapping[key]
    return out


def merge_policy_layers(
    *,
    pipeline_policy: Optional[Mapping[str, Any]] = None,
    thresholds: Optional[Mapping[str, Any]] = None,
    settings: Any = None,
) -> dict[str, Any]:
    """Code defaults ← Catalog policy ← thresholds flatten ← Settings step name."""
    base = dict(default_platform_config().get("policy") or {})
    if isinstance(pipeline_policy, Mapping):
        for key, value in pipeline_policy.items():
            if value is None:
                continue
            if key == "review_labels" and isinstance(value, dict):
                labels = dict(base.get("review_labels") or DEFAULT_REVIEW_LABELS)
                labels.update({str(k).upper(): str(v) for k, v in value.items() if v is not None})
                base["review_labels"] = labels
            else:
                base[key] = value
    flat = _policy_from(thresholds)
    for key, value in flat.items():
        if value is None:
            continue
        if key == "review_labels" and isinstance(value, dict):
            labels = dict(base.get("review_labels") or DEFAULT_REVIEW_LABELS)
            labels.update({str(k).upper(): str(v) for k, v in value.items() if v is not None})
            base["review_labels"] = labels
        else:
            base[key] = value
    if settings is not None:
        env_step = getattr(settings, "ap_agent_workflow_step_name", None)
        # Settings only fill when Catalog left the default placeholder unused —
        # prefer Catalog when it differs from empty.
        if not str(base.get("workflow_step_name") or "").strip() and env_step:
            base["workflow_step_name"] = str(env_step).strip()
    return base


def review_label(
    decision: str,
    *,
    thresholds: Optional[Mapping[str, Any]] = None,
    pipeline_policy: Optional[Mapping[str, Any]] = None,
    settings: Any = None,
    doc_type: str = "",
) -> str:
    """Map MATCHED / … to Workflow UI review string from Catalog policy."""
    policy = merge_policy_layers(
        pipeline_policy=pipeline_policy,
        thresholds=thresholds,
        settings=settings,
    )
    labels = policy.get("review_labels") if isinstance(policy.get("review_labels"), dict) else {}
    labels = {str(k).upper(): str(v) for k, v in (labels or DEFAULT_REVIEW_LABELS).items()}
    if str(doc_type or "").lower() == "other":
        return labels.get("NON_INVOICE", "Non-Invoice")
    decision_u = str(decision or "").strip().upper()
    if decision_u in labels:
        return labels[decision_u]
    raw = str(decision or "").strip()
    allowed = set(labels.values())
    if raw in allowed:
        return raw
    return labels.get("NOT_MATCHED", "Not Matched")


def workflow_step_name(
    *,
    thresholds: Optional[Mapping[str, Any]] = None,
    pipeline_policy: Optional[Mapping[str, Any]] = None,
    settings: Any = None,
) -> str:
    policy = merge_policy_layers(
        pipeline_policy=pipeline_policy,
        thresholds=thresholds,
        settings=settings,
    )
    name = str(policy.get("workflow_step_name") or "").strip()
    if name:
        return name
    if settings is not None:
        env = str(getattr(settings, "ap_agent_workflow_step_name", None) or "").strip()
        if env:
            return env
    return "AP AGENT 1"


def line_match_floor(
    *,
    thresholds: Optional[Mapping[str, Any]] = None,
    pipeline_policy: Optional[Mapping[str, Any]] = None,
) -> float:
    policy = merge_policy_layers(pipeline_policy=pipeline_policy, thresholds=thresholds)
    raw = policy.get("line_match_floor", DEFAULT_LINE_MATCH_FLOOR)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_LINE_MATCH_FLOOR


def apply_policy_to_thresholds(
    thresholds: dict[str, Any],
    *,
    pipeline_policy: Optional[Mapping[str, Any]] = None,
    settings: Any = None,
) -> dict[str, Any]:
    """Copy resolved policy onto thresholds so skills share one dict."""
    out = dict(thresholds or {})
    policy = merge_policy_layers(
        pipeline_policy=pipeline_policy,
        thresholds=out,
        settings=settings,
    )
    out["workflow_step_name"] = policy.get("workflow_step_name")
    out["review_labels"] = dict(policy.get("review_labels") or DEFAULT_REVIEW_LABELS)
    out["line_match_floor"] = policy.get("line_match_floor", DEFAULT_LINE_MATCH_FLOOR)
    # Fill numeric thresholds from platform defaults when still missing.
    defaults = default_platform_config()["thresholds"]
    for key in ("approved", "partial", "amount_tolerance"):
        if out.get(key) is None and defaults.get(key) is not None:
            out[key] = defaults[key]
    if settings is not None:
        if out.get("approved") is None:
            out["approved"] = getattr(settings, "ap_approved_threshold", 80)
        if out.get("partial") is None:
            out["partial"] = getattr(settings, "ap_partial_threshold", 50)
        if out.get("amount_tolerance") is None:
            out["amount_tolerance"] = getattr(settings, "ap_amount_tolerance", 0.02)
    return out
