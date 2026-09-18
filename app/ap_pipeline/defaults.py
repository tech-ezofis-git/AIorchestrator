"""Canonical platform AP pipeline config (Catalog seed + code fallback)."""
from __future__ import annotations

from typing import Any

from app.ap_skills.types import DEFAULT_LINE_MATCH_FLOOR, DEFAULT_SKILL_ORDER

# Singleton key for platform_ap_pipeline.pipeline_key.
DEFAULT_PIPELINE_KEY = "default"

# Product-default review strings (Core move-next / AIAGENTResponse). Editable in Catalog.
DEFAULT_REVIEW_LABELS: dict[str, str] = {
    "MATCHED": "Matched",
    "PARTIALLY_MATCHED": "Partially Matched",
    "NOT_MATCHED": "Not Matched",
    "NON_INVOICE": "Non-Invoice",
    "DUPLICATE": "Not Matched",
}


def default_platform_config() -> dict[str, Any]:
    """Product default when payload.skills is omitted.

    Thresholds and ``policy`` are Catalog-owned product knobs (Phase 4).
    Settings remain last-resort fallbacks when Catalog values are missing.
    """
    return {
        "skills_order": list(DEFAULT_SKILL_ORDER),
        "skills_enabled": None,
        "default_connector_id": None,
        "default_resource": None,
        "thresholds": {
            # Concrete defaults so Catalog is the source of truth (not null→Settings).
            "approved": 80,
            "partial": 50,
            "amount_tolerance": 0.02,
        },
        "flags": {
            "use_planner": False,
            # Opt-in code inject when Workflow asks SAP/HANA (Core usually stamps skills).
            "force_hana_po_lookup": False,
        },
        "policy": {
            "workflow_step_name": "AP AGENT 1",
            "review_labels": dict(DEFAULT_REVIEW_LABELS),
            "line_match_floor": DEFAULT_LINE_MATCH_FLOOR,
        },
    }
