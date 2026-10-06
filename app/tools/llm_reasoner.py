"""LLM Reasoner tool — prepared messages (from Prompt Builder) → LLM call → raw reply + parsed JSON."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from app.llm.adapter import LLMAdapter
from app.summary_skills.lock import loads_json_object

logger = logging.getLogger("orchestrator.tools.llm_reasoner")

TOOL_ID = "llm_reasoner"
NO_MESSAGES_ERROR = "Prepared input must include a non-empty messages list."
_ROLES = {"system", "user", "assistant"}


def _result(
    *,
    status: str,
    started: float,
    intent: Optional[str] = None,
    content: str = "",
    parsed: Optional[dict[str, Any]] = None,
    usage: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "tool": TOOL_ID,
        "status": status,
        "intent": intent,
        "content": content,
        "json": parsed,
        "usage": usage,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "error": error,
    }


def _clean_messages(raw: Any) -> Optional[list[dict[str, str]]]:
    if not isinstance(raw, list) or not raw:
        return None
    messages = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        role = str(item.get("role") or "").strip().lower()
        content = item.get("content")
        if role not in _ROLES or not isinstance(content, str) or not content.strip():
            return None
        messages.append({"role": role, "content": content})
    return messages


async def run_llm_reasoning(
    llm: LLMAdapter,
    prepared: dict[str, Any],
    *,
    llm_overrides: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Send `prepared["messages"]` (the Prompt Builder result) to the LLM and return its reply.

    `json` holds the reply parsed as a JSON object when it is one, else None.
    `llm_overrides` are per-call model settings (model / api_base / api_key / api_version).
    Never raises: invalid input and LLM failures come back as status FAILED with `error` set.
    """
    started = time.perf_counter()
    prepared = prepared if isinstance(prepared, dict) else {}
    intent = prepared.get("intent")
    if prepared.get("status") == "FAILED":
        return _result(
            status="FAILED", started=started, intent=intent,
            error=prepared.get("error") or "Prepared input is marked FAILED.",
        )
    messages = _clean_messages(prepared.get("messages"))
    if messages is None:
        return _result(status="FAILED", started=started, intent=intent, error=NO_MESSAGES_ERROR)

    try:
        response = await llm.chat_completion(messages, **(llm_overrides or {}))
    except Exception as exc:
        logger.warning("llm_reasoner_failed", extra={"error_type": type(exc).__name__})
        reason = str(exc).strip() or type(exc).__name__
        return _result(status="FAILED", started=started, intent=intent, error=f"LLM call failed: {reason}")

    content = response.get("content") or ""
    usage = response.get("usage")
    if not content.strip():
        return _result(
            status="FAILED", started=started, intent=intent, usage=usage, error="The LLM returned an empty reply."
        )
    parsed = loads_json_object(content)
    return _result(
        status="SUCCEEDED", started=started, intent=intent, content=content,
        parsed=parsed if isinstance(parsed, dict) else None, usage=usage,
    )
