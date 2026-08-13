"""Model presets for the Test Console.

Selecting a preset in the UI (GET /console/llm-presets + POST
/console/llm-config with default_preset_id) applies model, api_base,
api_key, and api_version on the shared LLMAdapter so chat uses that
deployment immediately — no restart.

API keys are NOT stored here — they come from .env via Settings
(`QWEN_MAC_API_KEY`, `AZURE_SOUTH_INDIA_API_KEY`, `AZURE_EAST_US_API_KEY`).
Only id/label/region/endpoints are returned by the presets list endpoint.
"""
from __future__ import annotations

from typing import Any, Optional

from app.config import get_settings

# api_base is the OpenAI-compatible root (…/v1) or Azure resource root —
# not the full /chat/completions URL. api_key_attr maps to a Settings
# field loaded from .env. Empty api_version clears Azure api-version.
MODEL_PRESETS: list[dict[str, Any]] = [
    {
        "id": "ezofis-gpu-box",
        "label": "ezofis-gpu-box",
        "model": "openai/qwen3.5-9b",
        "model_version": None,
        "region": "Canada Central (ACI)",
        "api_base": "http://ezqwenmac-aci.canadacentral.azurecontainer.io:8080/v1",
        "api_key_attr": "qwen_mac_api_key",
        "api_version": "",
    },
    {
        "id": "gpt-4.1-nano",
        "label": "gpt-4.1-nano",
        "model": "azure/gpt-4.1-nano",
        "model_version": "2025-04-14",
        "region": "South India",
        "api_base": "https://ezazopenai.openai.azure.com",
        "api_key_attr": "azure_south_india_api_key",
        "api_version": "2025-01-01-preview",
    },
    {
        "id": "gpt-4.1-mini",
        "label": "gpt-4.1-mini",
        "model": "azure/gpt-4.1-mini",
        "model_version": "2025-04-14",
        "region": "South India",
        "api_base": "https://ezazopenai.openai.azure.com",
        "api_key_attr": "azure_south_india_api_key",
        "api_version": "2025-01-01-preview",
    },
    {
        "id": "gpt-4o-mini",
        "label": "gpt-4o-mini",
        "model": "azure/gpt-4o-mini",
        "model_version": "2024-07-18",
        "region": "East US",
        "api_base": "https://api-4omin-ez.openai.azure.com",
        "api_key_attr": "azure_east_us_api_key",
        "api_version": "2025-01-01-preview",
    },
]

DEFAULT_PRESET_ID = "ezofis-gpu-box"


def get_preset(preset_id: str) -> Optional[dict[str, Any]]:
    for preset in MODEL_PRESETS:
        if preset["id"] == preset_id:
            return preset
    return None


def _resolve_api_key(preset: dict[str, Any]) -> Optional[str]:
    settings = get_settings()
    attr = preset.get("api_key_attr")
    if not attr:
        return None
    return getattr(settings, attr, None) or None


def list_presets_public() -> list[dict[str, Any]]:
    """Safe for the wire — never includes api_key."""
    return [
        {
            "id": p["id"],
            "label": p["label"],
            "model": p["model"],
            "model_version": p.get("model_version"),
            "region": p.get("region"),
            "api_base": p["api_base"],
            "api_version": p.get("api_version") or None,
        }
        for p in MODEL_PRESETS
    ]


def apply_preset(adapter: Any, preset_id: str) -> bool:
    """Configure `adapter` from a preset. Returns False if id unknown."""
    preset = get_preset(preset_id)
    if preset is None:
        return False
    adapter.configure(
        model=preset["model"],
        api_base=preset["api_base"],
        api_key=_resolve_api_key(preset) or "",
        # Empty string clears a previous Azure api_version.
        api_version=preset.get("api_version") if preset.get("api_version") is not None else "",
        preset_id=preset["id"],
    )
    return True
