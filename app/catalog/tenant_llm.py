"""Apply catalog tenant/agent LLM presets on the shared adapter."""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.llm.model_presets import apply_preset, get_preset, preset_has_api_key

logger = logging.getLogger("orchestrator.catalog")


async def apply_tenant_agent_llm(
    catalog: Any,
    llm: Any,
    tenant_id: Optional[str],
    agent_slug: Optional[str],
) -> Optional[str]:
    """Switch the shared adapter to the catalog model for tenant+agent.

    Resolution order (in catalog store): per-agent row, then tenant-wide default.
    Returns fallback preset slug when configured, else None.
    """
    tenant_id = (tenant_id or "").strip()
    agent_slug = (agent_slug or "").strip()
    if not tenant_id or not agent_slug or catalog is None or llm is None:
        return None
    try:
        slugs = await catalog.resolve_tenant_agent_llm_slugs(tenant_id, agent_slug)
    except Exception:
        logger.warning(
            "catalog_tenant_agent_llm_lookup_failed",
            extra={"tenant_id": tenant_id, "agent_slug": agent_slug},
        )
        return None
    default_slug = (slugs.get("default_slug") or "").strip()
    fallback_slug = (slugs.get("fallback_slug") or "").strip() or None
    if default_slug and get_preset(default_slug) and preset_has_api_key(default_slug):
        apply_preset(llm, default_slug)
    elif default_slug:
        logger.warning(
            "catalog_tenant_agent_default_skipped",
            extra={"agent_slug": agent_slug, "default_slug": default_slug},
        )
    if fallback_slug and get_preset(fallback_slug) and preset_has_api_key(fallback_slug):
        return fallback_slug
    return None


def restore_runtime_llm(llm: Any, runtime_models: Any) -> None:
    """Restore the process-global console default preset after a request."""
    preset = getattr(runtime_models, "default_preset_id", None) if runtime_models else None
    if preset and llm is not None and get_preset(preset):
        apply_preset(llm, preset)
