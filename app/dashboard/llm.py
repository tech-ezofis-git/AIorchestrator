"""Dashboard LLM helper — uses the orchestrator LiteLLM adapter. Hardcoded prompts live in propose/prompts/insights."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from app.llm.adapter import LLMAdapter, LLMAdapterError

logger = logging.getLogger("orchestrator.dashboard.llm")


class LLMError(RuntimeError):
    """Raised when the model cannot return usable JSON."""


_adapter: Optional[LLMAdapter] = None


def configure_dashboard_llm(adapter: Optional[LLMAdapter]) -> None:
    global _adapter
    _adapter = adapter


async def chat_json(*, system: str, user: str, timeout: float = 45.0, temperature: float = 0.2) -> dict[str, Any]:
    _ = timeout
    _ = temperature
    if _adapter is None:
        raise LLMError("Dashboard LLM adapter is not configured.")
    try:
        result = await _adapter.chat_completion(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )
    except LLMAdapterError as exc:
        raise LLMError(str(exc)) from exc
    return _parse_json_object(str(result.get("content") or ""))


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise LLMError("Empty model response.")
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, flags=re.DOTALL)
    if fence:
        raw = fence.group(1)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise LLMError("Model response was not JSON.")
        payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise LLMError("Model JSON was not an object.")
    return payload
