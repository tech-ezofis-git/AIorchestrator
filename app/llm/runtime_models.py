"""Default + fallback model selection for the Test Console.

Keys stay in .env (applied via presets). Console Save writes the chosen
preset ids to Redis so they survive process/hosting restarts; they only
change again when someone manually Saves a new selection.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from app.llm.model_presets import DEFAULT_PRESET_ID, get_preset

logger = logging.getLogger("orchestrator.runtime_models")

# No TTL — selection stays until an operator overwrites it via the console.
_REDIS_KEY = "orchestrator:llm:runtime_models"


class RedisLike(Protocol):
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: str, ex: Optional[int] = None) -> Any: ...


@dataclass
class RuntimeModelSelection:
    default_preset_id: str = DEFAULT_PRESET_ID
    fallback_preset_id: Optional[str] = None

    def describe(self) -> dict:
        return {
            "default_preset_id": self.default_preset_id,
            "fallback_preset_id": self.fallback_preset_id,
        }

    def set_default(self, preset_id: str) -> None:
        if get_preset(preset_id) is None:
            raise ValueError(f"Unknown preset_id: {preset_id}")
        self.default_preset_id = preset_id

    def set_fallback(self, preset_id: Optional[str]) -> None:
        if preset_id:
            if get_preset(preset_id) is None:
                raise ValueError(f"Unknown fallback_preset_id: {preset_id}")
            self.fallback_preset_id = preset_id
        else:
            self.fallback_preset_id = None

    async def load_from_redis(self, redis: RedisLike) -> bool:
        """Apply a previously Saved selection. Returns True if Redis had one."""
        try:
            raw = await redis.get(_REDIS_KEY)
        except Exception:
            logger.warning(
                "runtime_models_load_failed",
                extra={"error_type": "redis"},
            )
            return False
        if not raw:
            return False
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            logger.warning("runtime_models_corrupt", extra={"key": _REDIS_KEY})
            return False
        if not isinstance(data, dict):
            return False

        default_id = (data.get("default_preset_id") or "").strip()
        fallback_id = data.get("fallback_preset_id")
        if fallback_id is not None:
            fallback_id = str(fallback_id).strip() or None

        changed = False
        if default_id and get_preset(default_id):
            self.default_preset_id = default_id
            changed = True
        if fallback_id is None:
            # Explicit null in Redis means "cleared" — keep None.
            if "fallback_preset_id" in data:
                self.fallback_preset_id = None
                changed = True
        elif get_preset(fallback_id):
            self.fallback_preset_id = fallback_id
            changed = True

        if changed:
            logger.info(
                "runtime_models_loaded",
                extra={
                    "default_preset_id": self.default_preset_id,
                    "fallback_preset_id": self.fallback_preset_id,
                },
            )
        return changed

    async def save_to_redis(self, redis: RedisLike) -> None:
        """Persist current selection (no expiry)."""
        payload = json.dumps(self.describe())
        try:
            await redis.set(_REDIS_KEY, payload)
        except Exception:
            logger.warning(
                "runtime_models_save_failed",
                extra={"error_type": "redis"},
            )
            raise
        logger.info(
            "runtime_models_saved",
            extra={
                "default_preset_id": self.default_preset_id,
                "fallback_preset_id": self.fallback_preset_id,
            },
        )
