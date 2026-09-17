"""Canonical platform AP pipeline config (mirrors code defaults)."""
from __future__ import annotations

from typing import Any

from app.ap_skills.types import DEFAULT_SKILL_ORDER

# Singleton key for platform_ap_pipeline.pipeline_key.
DEFAULT_PIPELINE_KEY = "default"


def default_platform_config() -> dict[str, Any]:
    """Product default when payload.skills is omitted.

    Matches ``DEFAULT_SKILL_ORDER`` plus EZOFIS-safe flags documented in
    ``docs/AP_PIPELINE_PHASE0_INVENTORY.md``. Thresholds stay null so
    Settings (``ap_approved_threshold``, etc.) remain the live fallback
    until a tenant/platform row sets them.
    """
    return {
        "skills_order": list(DEFAULT_SKILL_ORDER),
        "skills_enabled": None,
        "default_connector_id": None,
        "default_resource": None,
        "thresholds": {
            "approved": None,
            "partial": None,
            "amount_tolerance": None,
        },
        "flags": {
            "use_planner": False,
            # Documents current EZOFIS injection; runner still gates on tenant
            # via ensure_ezofis_hana_po_lookup until Phase 3 reads this flag.
            "force_hana_po_lookup": True,
        },
    }
