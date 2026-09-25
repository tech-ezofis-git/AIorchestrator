"""Public API shape of a qualifier decision.

The model fills in snake_case keys (see agent.py's submit_qualification_decision schema) and runs
are stored that way; callers see Title Case keys ("Project Name", "Matched Items", "Ai Insight",
including the keys inside each item) and Title Case code values ("Needs Review", "Door Operator").
to_internal accepts either shape so an edited public result can be sent straight back to the
Quote Estimator.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.ftl.key_format import snake_keys, title_keys

DECISION_KEYS = (
    "qualify",
    "project_type",
    "project_name",
    "deadline",
    "matched_items",
    "excluded_items",
    "flags",
    "reasoning",
    "confidence",
    "ai_insight",
)

_LIST_KEYS = ("matched_items", "excluded_items", "flags")
_TEXT_KEYS = ("project_type", "project_name", "reasoning", "ai_insight")


def _confidence_percent(value: Any) -> Optional[int]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 1:
        number *= 100
    return int(round(max(0.0, min(100.0, number))))


def _confidence_fraction(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1:
        number /= 100
    return max(0.0, min(1.0, number))


def to_public(decision: Dict[str, Any]) -> Dict[str, Any]:
    decision = to_internal(decision)
    out: Dict[str, Any] = {}
    for key in DECISION_KEYS:
        value = decision.get(key)
        if key == "qualify":
            value = value or "needs_review"
        elif key == "confidence":
            value = _confidence_percent(value)
        elif key in _LIST_KEYS:
            value = value or []
        elif key in _TEXT_KEYS:
            value = value or ""
        out[key] = value
    return title_keys(out)


def to_internal(result: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    out = snake_keys(result)
    if "confidence" in out:
        out["confidence"] = _confidence_fraction(out["confidence"])
    return out
