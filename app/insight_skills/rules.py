"""Insight rules: LLM instructions from SKILL.md/.mdc; lock in Python."""
from __future__ import annotations

import json
from typing import Any, Optional

from app.agent_skills.loader import get_skill

INSIGHT_JSON_KEYS: tuple[str, ...] = ("insights",)

EMPTY_INSIGHTS: list[str] = []


def system_prompt(*, settings=None) -> str:
    """LLM system prompt = Insight SKILL.md + rules/*.mdc."""
    return get_skill("insight", settings=settings).system_prompt


def __getattr__(name: str):
    if name == "SYSTEM_PROMPT":
        return system_prompt()
    raise AttributeError(f"module {__name__!r} has no attribute {name}")


def build_user_prompt(
    *,
    source: str,
    content: str,
    content_kind: str = "text",
    instruction: Optional[str] = None,
) -> str:
    kind = (content_kind or "text").strip().lower()
    label = "JSON data" if kind == "json" else "Source text"
    hint = (instruction or "").strip()
    hint_block = f"\n\nExtra instruction:\n{hint}" if hint else ""
    return (
        f"Source: {source}\n"
        f"Content kind: {kind}\n\n"
        f"{label}:\n{content}"
        f"{hint_block}"
    )


def format_structured_payload(data: Any) -> str:
    """Pretty-print arbitrary JSON for the LLM user message."""
    try:
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        return str(data)
