"""Classification rules: LLM instructions from SKILL.md/.mdc; lock stays in Python."""
from __future__ import annotations

from typing import Optional

from app.agent_skills.loader import get_skill

CLASSIFICATION_JSON_KEYS: tuple[str, ...] = (
    "confidence_score",
    "document_type",
    "rationale",
    "suggested_labels",
    "ocr_text",
)

EMPTY_CLASSIFICATION_TEXT = (
    "I couldn't extract any text from that document, so I can't classify it."
)

USER_PROMPT_PREFIX = "Classify this document."


def system_prompt(*, settings=None, tenant_id: Optional[str] = None) -> str:
    """LLM system prompt = skills/classification SKILL.md + rules/*.mdc (disk)."""
    return get_skill("classification", settings=settings).system_prompt


async def async_system_prompt(*, settings=None, tenant_id: Optional[str] = None) -> str:
    """Always the skills/classification folder on disk — the client-approved
    prompt must not be overridden by Catalog DB / tenant pack copies."""
    return system_prompt(settings=settings, tenant_id=tenant_id)


def __getattr__(name: str):
    if name == "SYSTEM_PROMPT":
        return system_prompt()
    raise AttributeError(f"module {__name__!r} has no attribute {name}")


def build_user_prompt(*, source: str, page_label: str, content: str) -> str:
    page = f" ({page_label})" if page_label else ""
    return (
        f"{USER_PROMPT_PREFIX}\n\n"
        f"Source: {source}{page}\n\n"
        f"OCR text:\n{content}"
    )
