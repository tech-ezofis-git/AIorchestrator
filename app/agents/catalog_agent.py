"""LLM agent defined in catalog_agents (kind=custom) — prompt + triggers, no Python per agent."""
from __future__ import annotations

from typing import Any

from app.llm.adapter import LLMAdapter

_FALLBACK_PROMPT = "You are a helpful assistant for EZOFIS."


class CatalogAgent:
    def __init__(self, llm_adapter: LLMAdapter):
        self._llm = llm_adapter

    async def handle(
        self,
        *,
        session_id: str,
        message: str,
        history: list[dict[str, str]],
        catalog_agent: dict[str, Any],
        **_: object,
    ) -> dict[str, Any]:
        prompt = (catalog_agent.get("system_prompt") or "").strip() or _FALLBACK_PROMPT
        messages: list[dict[str, str]] = [{"role": "system", "content": prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": message})
        result = await self._llm.chat_completion(messages)
        return {"reply": result["content"], "usage": result["usage"]}
