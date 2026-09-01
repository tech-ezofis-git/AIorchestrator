"""Skill resolution + optional LLM reorder. LLM cannot enable extra skills."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.ap_skills.types import ALL_SKILLS, DEFAULT_SKILL_ORDER, ApSkillError

logger = logging.getLogger("orchestrator.ap_planner")

_PLANNER_PROMPT = (
    "You order AP invoice-processing skills. Reply with JSON only: "
    '{"skills": ["extract_invoice", "..."]}. Use only the allowed skill ids, '
    "keep extract_invoice first if present, finalize_decision before workflow_* "
    "if present, and workflow_move_next last if present."
)


def resolve_skills(
    *,
    requested: Optional[list[str]],
    enabled: Optional[list[str]] = None,
) -> list[str]:
    """Resolve which skills to run.

    - ``requested is None`` → always ``DEFAULT_SKILL_ORDER`` (includes
      finalize_decision + workflow_move_next). ``enabled`` is ignored.
    - ``requested`` is a list → run exactly those ids (must be known).
    """
    del enabled  # retained for call-site compat; gating is payload-driven now
    if requested is None:
        return list(DEFAULT_SKILL_ORDER)

    unknown = [s for s in requested if s not in ALL_SKILLS]
    if unknown:
        raise ApSkillError(f"Unknown skill(s): {', '.join(unknown)}")
    if not requested:
        raise ApSkillError("No skills requested.")
    return list(requested)


async def maybe_reorder(
    skills: list[str],
    *,
    llm: Any,
    use_planner: bool,
    llm_overrides: Optional[dict[str, Any]] = None,
) -> list[str]:
    if not use_planner or llm is None or len(skills) < 2:
        return skills
    allowed = set(skills)
    try:
        result = await llm.chat_completion(
            [
                {"role": "system", "content": _PLANNER_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps({"allowed_skills": skills}, default=str),
                },
            ],
            **(llm_overrides or {}),
        )
        content = (result or {}).get("content") or ""
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            return skills
        parsed = json.loads(content[start : end + 1])
        proposed = parsed.get("skills") if isinstance(parsed, dict) else None
        if not isinstance(proposed, list):
            return skills
        ordered = [s for s in proposed if s in allowed]
        missing = [s for s in skills if s not in ordered]
        return ordered + missing
    except Exception:
        logger.warning("ap_planner_failed", extra={"error_type": "planner"})
        return skills
