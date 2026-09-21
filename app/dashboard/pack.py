"""Load Dashboard LLM system prompts from Catalog packs, with hardcoded fallback."""
from __future__ import annotations

import logging

from app.agent_packs.overlay import get_agent_skill

logger = logging.getLogger("orchestrator.dashboard.pack")

PACK_BY_PHASE = {
    "prompts": "dashboard-prompts",
    "schema": "dashboard-schema",
    "data": "dashboard-data",
}


async def dashboard_system_prompt(phase: str, fallback: str) -> str:
    agent = PACK_BY_PHASE.get((phase or "").strip().lower())
    if not agent:
        return fallback
    try:
        skill = await get_agent_skill(agent)
        text = (skill.system_prompt or "").strip()
        if text:
            return text
    except Exception as exc:
        logger.warning("dashboard_pack_load_failed phase=%s agent=%s: %s", phase, agent, exc)
    return fallback
