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
from typing import Any, Optional, Sequence

from app.llm.model_presets import apply_preset, get_preset, resolve_preset_overrides

logger = logging.getLogger("orchestrator.catalog")


def fallback_override_chain(*parts: Any) -> list[dict[str, Any]]:
    """Flatten dicts/lists of chat_completion override dicts.

    Skips empties and duplicate model names so primary -> fallback -> 2nd
    fallback never retries the same deployment twice.
    """
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    for part in parts:
        items: Sequence[Any]
        if isinstance(part, dict):
            items = [part] if part else []
        elif isinstance(part, (list, tuple)):
            items = part
        else:
            items = []
        for raw in items:
            if not isinstance(raw, dict) or not raw:
                continue
            model = str(raw.get("model") or "").strip().lower()
            if model and model in seen:
                continue
            if model:
                seen.add(model)
            chain.append(raw)
    return chain


def job_fallback_chain(job: Any = None) -> list[dict[str, Any]]:
    """Fallback override dicts stored on a document_job / chat kwargs blob."""
    data = job if isinstance(job, dict) else {}
    return fallback_override_chain(
        data.get("llm_fallback_chain"),
        data.get("llm_fallback_overrides"),
        data.get("llm_fallback2_overrides"),
    )


async def chat_completion_with_fallbacks(
    llm: Any,
    messages: list[dict[str, str]],
    *,
    primary: Optional[dict[str, Any]] = None,
    fallbacks: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Call chat_completion on primary, then each fallback, in order."""
    attempts = [primary if isinstance(primary, dict) else {}]
    attempts.extend(fallback_override_chain(fallbacks))
    last_exc: Optional[BaseException] = None
    for index, overrides in enumerate(attempts):
        try:
            return await llm.chat_completion(messages, **(overrides or {}))
        except Exception as exc:
            last_exc = exc
            if index >= len(attempts) - 1:
                break
            logger.warning(
                "llm_call_failed_trying_fallback",
                extra={
                    "model": (overrides or {}).get("model") or "default",
                    "error_type": type(exc).__name__,
                    "next_model": (attempts[index + 1] or {}).get("model") or "default",
                },
            )
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("LLM call failed with no exception.")


async def apply_tenant_agent_llm(
    catalog: Any,
    tenant_id: Optional[str],
    agent_slug: Optional[str],
) -> dict[str, Any]:
    """Resolve the catalog tenant+agent model selection.

    Resolution order (in catalog store): per-agent row, then tenant-wide
    default. Returns:
      {"default_slug": str | None, "fallback_slug": str | None,
       "fallback2_slug": str | None,
       "overrides": <default-preset chat_completion() override dict or None>,
       "fallback_overrides": <first fallback override dict or None>,
       "fallback2_overrides": <second fallback override dict or None>,
       "fallback_chain": [<fallback dicts in order>]}
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
        "fallback2_slug": None,
        "overrides": None,
        "fallback_overrides": None,
        "fallback2_overrides": None,
        "fallback_chain": [],
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
    fallback2_slug = (slugs.get("fallback2_slug") or "").strip() or None

    overrides = resolve_preset_overrides(default_slug) if default_slug else None
    if default_slug and overrides is None:
        logger.warning(
            "catalog_tenant_agent_default_skipped",
            extra={"agent_slug": agent_slug, "default_slug": default_slug},
        )
    fallback_overrides = resolve_preset_overrides(fallback_slug) if fallback_slug else None
    fallback2_overrides = resolve_preset_overrides(fallback2_slug) if fallback2_slug else None
    chain = fallback_override_chain(fallback_overrides, fallback2_overrides)
    return {
        "default_slug": default_slug,
        "fallback_slug": fallback_slug,
        "fallback2_slug": fallback2_slug,
        "overrides": overrides,
        "fallback_overrides": chain[0] if chain else None,
        "fallback2_overrides": chain[1] if len(chain) > 1 else None,
        "fallback_chain": chain,
    }


def restore_runtime_llm(llm: Any, runtime_models: Any) -> None:
    """Re-apply the process-global Console default preset. Only meaningful
    after code that legitimately mutates the shared adapter's own default
    (e.g. an explicit Console preset change) — NOT part of the per-request
    tenant-override path above, which never mutates the adapter at all."""
    preset = getattr(runtime_models, "default_preset_id", None) if runtime_models else None
    if preset and llm is not None and get_preset(preset):
        apply_preset(llm, preset)
