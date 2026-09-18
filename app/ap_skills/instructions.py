"""AP Instructions from Catalog/disk packs (agent_slug=ap) — primary LLM system prompt.

Same pattern as OCR/Summary: Catalog (platform + tenant) drives the system prompt.
Python keeps only a short code fallback when the pack is missing.
Deterministic skills (po_match, lookups, workflow) stay in code.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("orchestrator.ap.instructions")


async def ap_instructions_system_prompt(
    *,
    tenant_id: Optional[str] = None,
    settings: Any = None,
) -> str:
    """Platform + tenant AP instruction pack as the primary LLM system prompt.

    Empty string when the pack is missing — callers use ``code_fallback``.
    Never used to replace Python skills (po_match, etc.).
    """
    try:
        from app.agent_packs.overlay import get_agent_skill

        skill = await get_agent_skill("ap", tenant_id=tenant_id, settings=settings)
        return (skill.system_prompt or "").strip()
    except FileNotFoundError:
        return ""
    except Exception as exc:
        logger.warning(
            "ap_instructions_load_failed",
            extra={"error_type": type(exc).__name__},
        )
        return ""


async def resolve_system_prompt(
    *,
    code_fallback: str,
    tenant_id: Optional[str] = None,
    settings: Any = None,
) -> str:
    """OCR-style: Catalog/disk pack wins; otherwise the short code fallback."""
    pack = await ap_instructions_system_prompt(tenant_id=tenant_id, settings=settings)
    if pack:
        return pack
    return (code_fallback or "").strip()


def merge_system_prompt(base: str, addon: str) -> str:
    """Legacy soft-append helper (pre Phase-1). Prefer ``resolve_system_prompt``."""
    base = (base or "").strip()
    addon = (addon or "").strip()
    if not addon:
        return base
    if not base:
        return addon
    return f"{base}\n\n# Additional AP Instructions\n\n{addon}"
