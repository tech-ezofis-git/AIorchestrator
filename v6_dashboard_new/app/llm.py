"""Azure OpenAI chat helper for dashboard widget proposals."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("v6_dashboard.llm")


class LLMError(RuntimeError):
    """Raised when Azure OpenAI cannot return a usable response."""


def _chat_url(deployment: str, api_version: str) -> str:
    base = (settings.azure_openai_endpoint or "").rstrip("/")
    return f"{base}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"


async def chat_json(*, system: str, user: str, timeout: float = 45.0, temperature: float = 0.2) -> dict[str, Any]:
    if not settings.azure_openai_endpoint or not settings.azure_openai_api_key:
        raise LLMError("Azure OpenAI is not configured.")
    deployments = [
        (settings.azure_openai_deployment, settings.azure_openai_api_version),
    ]
    if settings.azure_openai_fallback_deployment:
        deployments.append(
            (
                settings.azure_openai_fallback_deployment,
                settings.azure_openai_fallback_api_version or settings.azure_openai_api_version,
            )
        )
    last_error: Exception | None = None
    for deployment, api_version in deployments:
        if not deployment:
            continue
        try:
            text = await _complete(deployment, api_version, system, user, timeout, temperature)
            return _parse_json_object(text)
        except Exception as exc:
            last_error = exc
            logger.warning("llm_call_failed deployment=%s error=%s", deployment, exc)
    raise LLMError(str(last_error) if last_error else "Azure OpenAI call failed.")


async def _complete(
    deployment: str,
    api_version: str,
    system: str,
    user: str,
    timeout: float,
    temperature: float = 0.2,
) -> str:
    payload = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": 1800,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=timeout, verify=settings.azure_openai_ssl_verify) as client:
        response = await client.post(
            _chat_url(deployment, api_version),
            headers={"api-key": settings.azure_openai_api_key, "Content-Type": "application/json"},
            json=payload,
        )
        if response.status_code >= 400:
            # Older API versions may reject response_format.
            payload.pop("response_format", None)
            response = await client.post(
                _chat_url(deployment, api_version),
                headers={"api-key": settings.azure_openai_api_key, "Content-Type": "application/json"},
                json=payload,
            )
        response.raise_for_status()
        body = response.json()
    choices = body.get("choices") or []
    if not choices:
        raise LLMError("Azure OpenAI returned no choices.")
    return str((choices[0].get("message") or {}).get("content") or "")


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
