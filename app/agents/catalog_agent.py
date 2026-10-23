"""LLM agent defined in catalog_agents (kind=custom) — prompt + triggers, no Python per agent."""
from __future__ import annotations

from typing import Any

from app.catalog.tenant_llm import chat_completion_with_fallbacks, job_fallback_chain
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
        document_job: Any = None,
        llm_fallback_chain: Any = None,
        **_: object,
    ) -> dict[str, Any]:
        prompt = (catalog_agent.get("system_prompt") or "").strip() or _FALLBACK_PROMPT
        messages: list[dict[str, str]] = [{"role": "system", "content": prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": message})
        fallbacks = list(llm_fallback_chain or []) or job_fallback_chain(document_job)
        result = await chat_completion_with_fallbacks(self._llm, messages, fallbacks=fallbacks)
        return {"reply": result["content"], "usage": result["usage"]}
