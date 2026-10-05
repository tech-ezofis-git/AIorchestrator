"""Document Intelligent rules: LLM instructions from SKILL.md/.mdc."""
from __future__ import annotations

import json
from typing import Any, Optional

from app.agent_skills.loader import get_skill

# The model is told to keep candidates with no matched fields below this.
NO_EVIDENCE_MAX_SCORE = 30.0
EMPTY_TEXT = "This document has no readable text, so it can't be matched to a folder."
OCR_FAILED = "Text could not be extracted from this document, so it can't be matched to a folder."
NO_MATCH = "This document doesn't match any of your folders."


def system_prompt(*, settings=None, tenant_id: Optional[str] = None) -> str:
    return get_skill("document_intelligent", settings=settings).system_prompt


async def async_system_prompt(*, settings=None, tenant_id: Optional[str] = None) -> str:
    from app.agent_packs.overlay import get_agent_skill

    skill = await get_agent_skill("document_intelligent", tenant_id=tenant_id, settings=settings)
    return skill.system_prompt


def catalog_ref(index: int) -> str:
    """Short handle the model copies instead of a GUID: 0 -> 'R1'."""
    return f"R{index + 1}"


def build_user_prompt(
    *,
    source: str,
    page_label: str,
    content: str,
    catalog: list[dict[str, Any]],
) -> str:
    page = f" ({page_label})" if page_label else ""
    slim = [
        {
            "ref": catalog_ref(i),
            "repository_name": row.get("repository_name"),
            "fields": list(row.get("fields") or [])[:24],
        }
        for i, row in enumerate(catalog)
    ]
    return (
        "Pick the repository from the catalog that this document belongs in. "
        "Identify each repository by its ref (R1, R2, ...) copied exactly from the catalog. "
        "Always list at least 2 and up to 3 closest repositories in candidates (when the catalog has that many), "
        "best first, even when none is a clear fit. "
        "For each candidate add matched_fields: the names, copied exactly from that repository's "
        "fields list, whose information is present in the OCR text in any language or abbreviation "
        "(e.g. 'Inv No' for 'Invoice Number'). "
        "Score each candidate 0-100 as your confidence that the document belongs in that repository, "
        "NOT as the share of its fields found: a few clear, specific matches are enough for 80+. "
        "Give different scores to different candidates; the best fit must clearly lead. "
        f"A candidate with no matched_fields must score below {NO_EVIDENCE_MAX_SCORE:g}.\n\n"
        f"Source: {source}{page}\n\n"
        f"Repository catalog:\n{json.dumps(slim, ensure_ascii=False)}\n\n"
        f"OCR text:\n{content}"
    )
