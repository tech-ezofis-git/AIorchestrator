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
# not the full /chat/completions URL. api_key_attr/api_base_attr map to
# Settings fields loaded from .env — code-review finding #16: api_base
# used to be a hardcoded literal here with no env override at all (unlike
# api_key, which already went through Settings); now both follow the same
# pattern, defaulting to today's values (app/config.py) so behavior is
# unchanged out of the box. Empty api_version clears Azure api-version.
MODEL_PRESETS: list[dict[str, Any]] = [
    {
        "id": "ezofis-gpu-box",
        "label": "ezofis-gpu-box",
        "model": "openai/qwen3.5-9b",
        "model_version": None,
        "region": "Canada Central (ACI)",
        "api_base_attr": "qwen_mac_api_base",
        "api_key_attr": "qwen_mac_api_key",
        "api_version": "",
    },
    {
        "id": "gpt-4.1-nano",
        "label": "gpt-4.1-nano",
        "model": "azure/gpt-4.1-nano",
        "model_version": "2025-04-14",
        "region": "South India",
        "api_base_attr": "azure_south_india_api_base",
        "api_key_attr": "azure_south_india_api_key",
        "api_version": "2025-01-01-preview",
    },
    {
        "id": "gpt-5-nano",
        "label": "gpt-5-nano",
        "model": "azure/gpt-5-nano",
        "model_version": None,
        "region": "South India",
        "api_base_attr": "azure_south_india_api_base",
        "api_key_attr": "azure_south_india_api_key",
        "api_version": "2025-01-01-preview",
    },
    {
        "id": "gpt-4.1-mini",
        "label": "gpt-4.1-mini",
        "model": "azure/gpt-4.1-mini",
        "model_version": "2025-04-14",
        "region": "South India",
        "api_base_attr": "azure_south_india_api_base",
        "api_key_attr": "azure_south_india_api_key",
        "api_version": "2025-01-01-preview",
    },
    {
        "id": "gpt-4o-mini",
        "label": "gpt-4o-mini",
        "model": "azure/gpt-4o-mini",
        "model_version": "2024-07-18",
        "region": "East US",
        "api_base_attr": "azure_east_us_api_base",
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

    Catalog rows store the key in `api_key` and their own `api_base`
    literal. Built-in slugs also keep `api_key_attr`/`api_base_attr` so an
    empty catalog key/base still resolves from .env.
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
        if hardcoded and hardcoded.get("api_base_attr") and not row.get("api_base") and not row.get("api_base_attr"):
            row["api_base_attr"] = hardcoded["api_base_attr"]
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


def _resolve_preset_field(preset: dict[str, Any], *, literal_key: str, attr_key: str) -> Optional[str]:
    """Shared resolution rule (ultrareview reuse/simplification fix — this
    used to be two near-identical hand-copies, one for api_key and one for
    api_base, differing only in field names and the empty-value fallback):
    an explicit literal on `preset` (e.g. a catalog row's own column) wins;
    otherwise resolved from Settings/.env via `<field>_attr`. Returns None
    if neither is set."""
    if preset.get(literal_key):
        return preset[literal_key]
    attr = preset.get(attr_key)
    if not attr:
        return None
    return getattr(get_settings(), attr, None) or None


def resolve_api_key(preset: dict[str, Any]) -> Optional[str]:
    return _resolve_preset_field(preset, literal_key="api_key", attr_key="api_key_attr")


def resolve_api_base(preset: dict[str, Any]) -> str:
    """Public (not `_`-prefixed): also called from app/catalog/store.py's
    `seed_defaults`, which needs the identical literal-or-Settings
    resolution when first writing built-in presets into catalog_models."""
    return _resolve_preset_field(preset, literal_key="api_base", attr_key="api_base_attr") or ""


def list_presets_public() -> list[dict[str, Any]]:
    """Safe for the wire — never includes api_key."""
    return [
        {
            "id": p["id"],
            "label": p["label"],
            "model": p["model"],
            "model_version": p.get("model_version"),
            "region": p.get("region"),
            "api_base": resolve_api_base(p),
            "api_version": p.get("api_version") or None,
            "has_api_key": bool(resolve_api_key(p)),
        }
        for p in _preset_source()
    ]


def apply_preset(adapter: Any, preset_id: str) -> bool:
    """Configure `adapter` from a preset. Returns False if id unknown.

    Mutates the adapter's own process-wide default — only appropriate for
    the Console's explicit "Save default" action (app/main.py's POST
    /console/llm-config) and startup. Per-request/per-tenant selection
    (app/catalog/tenant_llm.py, AP/OCR document jobs) must use
    `resolve_preset_overrides()` below instead, which never mutates shared
    state — see LLMAdapter.chat_completion's docstring for why."""
    preset = get_preset(preset_id)
    if preset is None:
        return False
    adapter.configure(
        model=preset["model"],
        api_base=resolve_api_base(preset),
        api_key=resolve_api_key(preset) or "",
        # Empty string clears a previous Azure api_version.
        api_version=preset.get("api_version") if preset.get("api_version") is not None else "",
        preset_id=preset["id"],
    )
    return True


def resolve_preset_overrides(preset_id: str) -> Optional[dict[str, Any]]:
    """Same lookup as `apply_preset`, but returned as data instead of
    applied by mutation — pass the result as **kwargs into
    `LLMAdapter.chat_completion(messages, **overrides)`, scoping the
    preset to that one call. Returns None if `preset_id` is unknown or
    has no usable API key."""
    preset = get_preset(preset_id)
    if preset is None or not preset_has_api_key(preset_id):
        return None
    return {
        "model": preset["model"],
        "api_base": resolve_api_base(preset),
        "api_key": resolve_api_key(preset) or "",
        "api_version": preset.get("api_version") if preset.get("api_version") is not None else "",
    }


def preset_has_api_key(preset_id: str) -> bool:
    preset = get_preset(preset_id)
    if preset is None:
        return False
    return bool(resolve_api_key(preset))


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
