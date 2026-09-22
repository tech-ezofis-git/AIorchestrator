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

USER_PROMPT_PREFIX = (
    "Infer the document type from the source data. Do not assume it is an invoice."
)


def system_prompt(*, settings=None, tenant_id: Optional[str] = None) -> str:
    """LLM system prompt = Classification SKILL.md + rules/*.mdc (sync/disk)."""
    return get_skill("classification", settings=settings).system_prompt


async def async_system_prompt(*, settings=None, tenant_id: Optional[str] = None) -> str:
    """Prefer Catalog DB packs when seeded; fallback to disk/SQLite."""
    from app.agent_packs.overlay import get_agent_skill

    skill = await get_agent_skill("classification", tenant_id=tenant_id, settings=settings)
    return skill.system_prompt


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
