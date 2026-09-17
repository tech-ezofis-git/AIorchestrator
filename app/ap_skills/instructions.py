"""Soft AP Instructions from Catalog/disk packs (agent_slug=ap)."""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("orchestrator.ap.instructions")


async def ap_instructions_system_prompt(
    *,
    tenant_id: Optional[str] = None,
    settings: Any = None,
) -> str:
    """Platform + tenant AP instruction pack as an LLM system-prompt addon.

    Empty string when the pack is missing — callers keep their hard-coded prompts.
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


def merge_system_prompt(base: str, addon: str) -> str:
    base = (base or "").strip()
    addon = (addon or "").strip()
    if not addon:
        return base
    if not base:
        return addon
    return f"{base}\n\n# Additional AP Instructions\n\n{addon}"
