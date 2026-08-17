"""OCR rules: LLM instructions from SKILL.md/.mdc; field parse stays in code.

Replaceable packs live under `skills/ocr/` (or OCR_SKILL_DIR / AGENT_SKILLS_ROOT).
"""
from __future__ import annotations

from app.agent_skills.loader import get_skill

NEVER_INVENT_WHEN_PARAMETERS = True
DATE_FORMAT = "YYYY-MM-DD"
DEFAULT_MAX_RECOMMENDED_FIELDS = 15


def system_prompt(*, max_recommended_fields: int = 15, settings=None) -> str:
    """LLM system prompt = OCR SKILL.md + rules/*.mdc (N substituted)."""
    prompt = get_skill("ocr", settings=settings).system_prompt
    return prompt.replace("at most N ", f"at most {max_recommended_fields} ")


def __getattr__(name: str):
    if name == "SYSTEM_PROMPT":
        return system_prompt()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_user_prompt(
    *,
    instruction: str,
    page_label: str,
    param_lines: str,
    table_lines: str,
    ocr_text: str,
) -> str:
    return (
        f"Instruction:\n{instruction}\n\n"
        f"Page focus: {page_label}\n\n"
        f"Parameters:\n{param_lines}\n\n"
        f"Table parameters:\n{table_lines}\n\n"
        f"OCR text:\n{ocr_text}"
    )
