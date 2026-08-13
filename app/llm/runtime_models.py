"""In-memory default + fallback model selection for the Test Console.

Keys stay in .env (applied via presets). The console only chooses which
preset is primary and which is used if OCR structuring fails.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.llm.model_presets import DEFAULT_PRESET_ID, get_preset


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
