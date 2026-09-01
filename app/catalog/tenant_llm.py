"""Resolve catalog tenant/agent LLM presets into per-call overrides.

IMPORTANT: `apply_tenant_agent_llm()` used to mutate the one shared
LLMAdapter instance in place (`apply_preset(llm, ...)`) and rely on the
caller restoring it afterward (`restore_runtime_llm`). That is unsafe under
real concurrency: the app has exactly one LLMAdapter, shared by every
in-flight request, so between "mutate to tenant A's preset" and "actually
make the LLM call" (many awaited I/O steps later — OCR, DB queries, EZOFIS
calls), a concurrent request for tenant B (or another intent, or a Console
Save) could reconfigure the same adapter first, and tenant A's call would
silently go out under tenant B's model/API key. `apply_tenant_agent_llm`
now instead resolves the tenant+agent selection into plain override dicts
(see `app.llm.model_presets.resolve_preset_overrides`) that the caller
passes straight into `LLMAdapter.chat_completion(messages, **overrides)`
for that one call only — nothing here mutates shared state anymore.
`restore_runtime_llm` remains for the one legitimate global mutation left
(the Console's own "Save default preset" action), unrelated to per-request
tenant selection.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.llm.model_presets import apply_preset, get_preset, resolve_preset_overrides

logger = logging.getLogger("orchestrator.catalog")


async def apply_tenant_agent_llm(
    catalog: Any,
    tenant_id: Optional[str],
    agent_slug: Optional[str],
) -> dict[str, Any]:
    """Resolve the catalog tenant+agent model selection.

    Resolution order (in catalog store): per-agent row, then tenant-wide
    default. Returns:
      {"default_slug": str | None, "fallback_slug": str | None,
       "overrides": <default-preset chat_completion() override dict or None>,
       "fallback_overrides": <fallback-preset override dict or None>}
    `overrides`/`fallback_overrides` never touch an adapter instance —
    document-job callers (AP, OCR) pass the relevant one straight into
    `chat_completion(..., **overrides)` for one call. `default_slug` is
    still returned for callers that intentionally mutate the shared
    adapter's process-wide default (every non-document-job intent today —
    Chat/Search/Summary/Insight/Prompt/Mail — via `apply_preset()`,
    unchanged pre-existing behavior this function doesn't alter)."""
    empty: dict[str, Any] = {
        "default_slug": None,
        "fallback_slug": None,
        "overrides": None,
        "fallback_overrides": None,
    }
    tenant_id = (tenant_id or "").strip()
    agent_slug = (agent_slug or "").strip()
    if not tenant_id or not agent_slug or catalog is None:
        return empty
    try:
        slugs = await catalog.resolve_tenant_agent_llm_slugs(tenant_id, agent_slug)
    except Exception:
        logger.warning(
            "catalog_tenant_agent_llm_lookup_failed",
            extra={"tenant_id": tenant_id, "agent_slug": agent_slug},
        )
        return empty
    default_slug = (slugs.get("default_slug") or "").strip() or None
    fallback_slug = (slugs.get("fallback_slug") or "").strip() or None

    overrides = resolve_preset_overrides(default_slug) if default_slug else None
    if default_slug and overrides is None:
        logger.warning(
            "catalog_tenant_agent_default_skipped",
            extra={"agent_slug": agent_slug, "default_slug": default_slug},
        )
    fallback_overrides = resolve_preset_overrides(fallback_slug) if fallback_slug else None
    return {
        "default_slug": default_slug,
        "fallback_slug": fallback_slug,
        "overrides": overrides,
        "fallback_overrides": fallback_overrides,
    }


def restore_runtime_llm(llm: Any, runtime_models: Any) -> None:
    """Re-apply the process-global Console default preset. Only meaningful
    after code that legitimately mutates the shared adapter's own default
    (e.g. an explicit Console preset change) — NOT part of the per-request
    tenant-override path above, which never mutates the adapter at all."""
    preset = getattr(runtime_models, "default_preset_id", None) if runtime_models else None
    if preset and llm is not None and get_preset(preset):
        apply_preset(llm, preset)
