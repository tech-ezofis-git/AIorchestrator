"""Prompt rules: LLM instructions from SKILL.md/.mdc; no JSON lock in code.

Replaceable packs live under `skills/prompt/` (or PROMPT_SKILL_DIR /
AGENT_SKILLS_ROOT). The orchestrator stores the model output as raw text.
"""
from __future__ import annotations

from app.agent_skills.loader import get_skill


def system_prompt(*, settings=None) -> str:
    """LLM system prompt = Prompt SKILL.md + rules/*.mdc."""
    return get_skill("prompt", settings=settings).system_prompt


def __getattr__(name: str):
    if name == "SYSTEM_PROMPT":
        return system_prompt()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
