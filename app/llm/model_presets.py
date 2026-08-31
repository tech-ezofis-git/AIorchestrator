"""Model presets for the Test Console.

Selecting a preset in the UI (GET /console/llm-presets + POST
/console/llm-config with default_preset_id) applies model, api_base,
api_key, and api_version on the shared LLMAdapter so chat uses that
deployment immediately — no restart.

API keys are stored in catalog_models when the catalog DB is available;
otherwise they come from .env via Settings (`QWEN_MAC_API_KEY`,
`AZURE_SOUTH_INDIA_API_KEY`, `AZURE_EAST_US_API_KEY`). The presets list
endpoint never returns api_key values.
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
        "id": "gpt-5-nano",
        "label": "gpt-5-nano",
        "model": "azure/gpt-5-nano",
        "model_version": None,
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

DEFAULT_PRESET_ID = "gpt-5-nano"

# Populated from catalog_models at startup (and after Catalog UI edits).
# None => use the hardcoded MODEL_PRESETS list.
_runtime_presets: Optional[list[dict[str, Any]]] = None


def set_runtime_presets(presets: Optional[list[dict[str, Any]]]) -> None:
    """Replace the in-memory preset list with catalog_models rows (or clear).

    Catalog rows store the key in `api_key`. Built-in slugs also keep
    `api_key_attr` so an empty catalog key still resolves from .env.
    """
    global _runtime_presets
    if presets is None:
        _runtime_presets = None
        return
    by_id = {row["id"]: row for row in MODEL_PRESETS}
    enriched: list[dict[str, Any]] = []
    for preset in presets:
        row = dict(preset)
        hardcoded = by_id.get(str(row.get("id") or ""))
        if hardcoded and hardcoded.get("api_key_attr") and not row.get("api_key_attr"):
            row["api_key_attr"] = hardcoded["api_key_attr"]
        enriched.append(row)
    _runtime_presets = enriched


def _preset_source() -> list[dict[str, Any]]:
    if _runtime_presets:
        return _runtime_presets
    return MODEL_PRESETS


def get_preset(preset_id: str) -> Optional[dict[str, Any]]:
    for preset in _preset_source():
        if preset["id"] == preset_id:
            return preset
    return None


def _resolve_api_key(preset: dict[str, Any]) -> Optional[str]:
    if preset.get("api_key"):
        return preset["api_key"]
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
            "has_api_key": bool(_resolve_api_key(p)),
        }
        for p in _preset_source()
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


def preset_has_api_key(preset_id: str) -> bool:
    preset = get_preset(preset_id)
    if preset is None:
        return False
    return bool(_resolve_api_key(preset))


def resolve_default_preset_id(preferred: str) -> str:
    """Pick a default preset that has an API key in .env, else keep preferred."""
    if preset_has_api_key(preferred):
        return preferred
    if preset_has_api_key(DEFAULT_PRESET_ID):
        return DEFAULT_PRESET_ID
    for preset in MODEL_PRESETS:
        pid = preset["id"]
        if preset_has_api_key(pid):
            return pid
    return preferred
