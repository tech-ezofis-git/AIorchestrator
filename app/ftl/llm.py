"""FTL LLM client — same preset, key, and endpoint as the other agents.

Other agents call LLMAdapter with catalog/tenant overrides
(`app.llm.model_presets`). FTL's tool loop still speaks the OpenAI SDK, so
this module turns that same override dict into an OpenAI or AzureOpenAI
client instead of reading QWEN_* / OPENAI_API_KEY on its own.
"""
from __future__ import annotations

from typing import Any, Optional

from openai import AzureOpenAI, OpenAI

from app.llm.model_presets import (
    DEFAULT_PRESET_ID,
    resolve_default_preset_id,
    resolve_preset_overrides,
)

_MISSING_KEY = (
    "No LLM API key is configured for the agent model preset. "
    "Set the same key the other agents use (catalog model preset or AZURE_SOUTH_INDIA_API_KEY)."
)


def resolve_llm_config(overrides: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Prefer a frozen chat override (tenant preset or adapter snapshot).

    A model-only override keeps the default preset's key and endpoint, matching
    LLMAdapter.chat_completion when only `model` is passed.
    """
    ov = {k: v for k, v in (overrides or {}).items() if v not in (None, "")}
    if ov.get("api_key") and ov.get("model"):
        return {
            "model": str(ov["model"]),
            "api_base": str(ov.get("api_base") or ""),
            "api_key": str(ov["api_key"]),
            "api_version": str(ov.get("api_version") or ""),
        }

    preset_id = resolve_default_preset_id(DEFAULT_PRESET_ID)
    preset = resolve_preset_overrides(preset_id)
    if not preset or not preset.get("api_key"):
        raise RuntimeError(_MISSING_KEY)
    if ov.get("model"):
        preset = {**preset, "model": str(ov["model"])}
    return preset


def open_client(config: dict[str, Any]) -> tuple[Any, str]:
    """Return (sdk client, deployment name) for one FTL tool-loop."""
    model = str(config.get("model") or "").strip()
    api_base = str(config.get("api_base") or "").strip()
    api_key = str(config.get("api_key") or "").strip()
    api_version = str(config.get("api_version") or "").strip() or "2025-01-01-preview"
    if not model or not api_key:
        raise RuntimeError(_MISSING_KEY)

    azure = model.startswith("azure/") or "openai.azure.com" in api_base
    if azure:
        deploy = model[len("azure/"):] if model.startswith("azure/") else model
        if deploy.startswith("openai/"):
            deploy = deploy[len("openai/"):]
        endpoint = api_base or "https://ezazopenai.openai.azure.com"
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )
        return client, deploy

    deploy = model[len("openai/"):] if model.startswith("openai/") else model
    if api_base:
        return OpenAI(base_url=api_base, api_key=api_key), deploy
    return OpenAI(api_key=api_key), deploy


def prefers_json_mode(model: str) -> bool:
    return "qwen" in (model or "").lower()
