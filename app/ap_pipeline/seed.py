"""Seed platform_ap_pipeline from code defaults (Catalog DB)."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.ap_pipeline.defaults import DEFAULT_PIPELINE_KEY, default_platform_config
from app.catalog.store import CatalogStore

logger = logging.getLogger("orchestrator.ap_pipeline")


async def seed_platform_ap_pipeline(
    catalog: CatalogStore,
    *,
    updated_by: str = "seed_platform_ap_pipeline",
) -> dict[str, Any]:
    """Upsert ``pipeline_key='default'`` from ``DEFAULT_SKILL_ORDER``.

    Idempotent: re-runs refresh ``config_json`` from code (platform row is
    product-owned / console read-only). Does not touch ``tenant_ap_pipeline``.
    Safe to call when ``AP_PIPELINE_FROM_DB`` is false — runner ignores rows
    until Phase 3.
    """
    config = default_platform_config()
    config_json = json.dumps(config, default=str)
    row_id = uuid.uuid4()
    await catalog._run(
        "execute",
        "INSERT INTO platform_ap_pipeline "
        "(id, pipeline_key, config_json, version, is_active, updated_by) "
        "VALUES ($1, $2, $3::jsonb, 1, TRUE, $4) "
        "ON CONFLICT (pipeline_key) DO UPDATE SET "
        "config_json = EXCLUDED.config_json, "
        "version = platform_ap_pipeline.version + 1, "
        "is_active = TRUE, "
        "updated_by = EXCLUDED.updated_by, "
        "updated_at = now()",
        row_id,
        DEFAULT_PIPELINE_KEY,
        config_json,
        updated_by,
    )
    logger.info(
        "ap_pipeline_platform_seeded",
        extra={
            "pipeline_key": DEFAULT_PIPELINE_KEY,
            "skills_order": config.get("skills_order"),
        },
    )
    return {
        "pipeline_key": DEFAULT_PIPELINE_KEY,
        "skills_count": len(config.get("skills_order") or []),
        "skills_order": list(config.get("skills_order") or []),
    }
