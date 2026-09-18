"""Phase 2 — seed platform_ap_pipeline from DEFAULT_SKILL_ORDER."""
from __future__ import annotations

import pytest

from app.ap_pipeline import DEFAULT_PIPELINE_KEY, default_platform_config, seed_platform_ap_pipeline
from app.ap_skills.types import DEFAULT_SKILL_ORDER
from app.catalog.store import CatalogStore
from app.config import Settings
from tests.fakes import FakeDBPool


def test_default_platform_config_matches_code_defaults() -> None:
    cfg = default_platform_config()
    assert cfg["skills_order"] == list(DEFAULT_SKILL_ORDER)
    assert cfg["skills_enabled"] is None
    assert cfg["default_connector_id"] is None
    assert cfg["default_resource"] is None
    assert cfg["thresholds"] == {
        "approved": 80,
        "partial": 50,
        "amount_tolerance": 0.02,
    }
    assert cfg["flags"] == {"use_planner": False, "force_hana_po_lookup": False}
    assert cfg["policy"]["workflow_step_name"] == "AP AGENT 1"
    assert cfg["policy"]["review_labels"]["MATCHED"] == "Matched"
    assert cfg["policy"]["line_match_floor"] == 0.5


@pytest.mark.asyncio
async def test_seed_platform_ap_pipeline_idempotent() -> None:
    db = FakeDBPool()
    catalog = CatalogStore(db)

    first = await seed_platform_ap_pipeline(catalog)
    assert first["pipeline_key"] == DEFAULT_PIPELINE_KEY
    assert first["skills_order"] == list(DEFAULT_SKILL_ORDER)
    row = db.platform_ap_pipeline[DEFAULT_PIPELINE_KEY]
    assert row["version"] == 1
    assert row["config_json"]["skills_order"] == list(DEFAULT_SKILL_ORDER)

    second = await seed_platform_ap_pipeline(catalog)
    assert second["skills_order"] == list(DEFAULT_SKILL_ORDER)
    row2 = db.platform_ap_pipeline[DEFAULT_PIPELINE_KEY]
    assert row2["version"] == 2
    assert row2["config_json"]["flags"]["force_hana_po_lookup"] is False
    assert len(db.platform_ap_pipeline) == 1


def test_ap_pipeline_from_db_defaults_true() -> None:
    """Phase 2: runner reads Catalog by default; set false for rollback."""
    assert Settings().ap_pipeline_from_db is True
