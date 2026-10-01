"""Document Intelligent rules: LLM instructions from SKILL.md/.mdc."""
from __future__ import annotations

import json
from typing import Any, Optional

from app.agent_skills.loader import get_skill

MIN_CONFIDENCE = 55.0
# A candidate with no matched folder fields is capped here so its score agrees with its rationale.
NO_EVIDENCE_MAX_SCORE = 30.0
EMPTY_TEXT = "This document has no readable text, so it can't be matched to a folder."
NO_MATCH = "This document doesn't match any of your folders."


def system_prompt(*, settings=None, tenant_id: Optional[str] = None) -> str:
    return get_skill("document_intelligent", settings=settings).system_prompt


async def async_system_prompt(*, settings=None, tenant_id: Optional[str] = None) -> str:
    from app.agent_packs.overlay import get_agent_skill

    skill = await get_agent_skill("document_intelligent", tenant_id=tenant_id, settings=settings)
    return skill.system_prompt


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
            "repository_id": row.get("repository_id"),
            "repository_name": row.get("repository_name"),
            "fields": list(row.get("fields") or [])[:24],
        }
        for row in catalog
    ]
    return (
        "Pick the best repository from the catalog for this OCR text. "
        "Use only ids and names from the catalog. "
        "Always list up to 3 closest repositories in candidates, even when none is a clear fit. "
        "For each candidate add matched_fields: the names, copied exactly from that repository's "
        "fields list, whose information is present in the OCR text in any language. "
        "Score each candidate by how much of its fields the text covers; a candidate with no "
        f"matched_fields must score below {NO_EVIDENCE_MAX_SCORE:g}.\n\n"
        f"Source: {source}{page}\n\n"
        f"Repository catalog:\n{json.dumps(slim, ensure_ascii=False)}\n\n"
        f"OCR text:\n{content}"
    )
