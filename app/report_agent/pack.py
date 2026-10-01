"""Load Report Agent phase packs from Catalog / disk."""
from __future__ import annotations

import logging
from typing import Optional

from app.agent_packs.overlay import get_agent_skill

logger = logging.getLogger("orchestrator.report_agent.pack")

PACK_BY_PHASE = {
    "prompt": "report-prompt",
    "run": "report-run",
}


async def report_system_prompt(
    phase: str,
    *,
    tenant_id: Optional[str] = None,
    settings=None,
    fallback: str = "",
) -> str:
    agent = PACK_BY_PHASE.get((phase or "").strip().lower())
    if not agent:
        return fallback
    try:
        skill = await get_agent_skill(agent, tenant_id=tenant_id, settings=settings)
        text = (skill.system_prompt or "").strip()
        if text:
            return text
    except Exception as exc:
        logger.warning(
            "report_pack_load_failed phase=%s agent=%s: %s",
            phase,
            agent,
            exc,
        )
    return fallback
