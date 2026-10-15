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


async def chat_json(
    *,
    system: str,
    user: str,
    timeout: float = 45.0,
    temperature: float = 0.2,
    model: Optional[str] = None,
) -> dict[str, Any]:
    _ = timeout
    _ = temperature
    if _adapter is None:
        raise LLMError("Dashboard LLM adapter is not configured.")
    try:
        result = await _adapter.chat_completion(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=model,
        )
    except LLMAdapterError as exc:
        raise LLMError(str(exc)) from exc
    return _parse_json_object(str(result.get("content") or ""))


async def chat_text(
    *,
    system: str,
    user: str,
    model: Optional[str] = None,
    max_completion_tokens: Optional[int] = None,
) -> str:
    """Return raw model text (markdown). Does not require JSON."""
    if _adapter is None:
        raise LLMError("Dashboard LLM adapter is not configured.")
    try:
        result = await _adapter.chat_completion(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            model=model,
            max_completion_tokens=max_completion_tokens,
        )
    except LLMAdapterError as exc:
        raise LLMError(str(exc)) from exc
    text = str(result.get("content") or "").strip()
    if not text:
        raise LLMError("Empty model response.")
    fence = re.search(r"```(?:markdown|md)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fence and not text.lstrip().startswith("{"):
        text = fence.group(1).strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            inner = str(payload.get("prompt") or payload.get("message") or "").strip()
            if inner:
                text = inner
    if not text:
        raise LLMError("Empty model response.")
    return text


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
