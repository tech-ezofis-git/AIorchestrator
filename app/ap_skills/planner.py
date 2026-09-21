"""Skill resolution + optional LLM reorder. LLM cannot enable extra skills."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.ap_skills.hana_po import wants_sap_or_hana_po_master
from app.ap_skills.types import ALL_SKILLS, DEFAULT_SKILL_ORDER, ApSkillError

logger = logging.getLogger("orchestrator.ap_planner")

# Code fallback only when Catalog/disk AP pack is missing. Primary copy lives in
# skills/ap/rules/planner.mdc (seeded to platform_agent_rules).
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
    default_order: Optional[list[str]] = None,
) -> list[str]:
    """Resolve which skills to run.

    - ``requested is None`` → ``default_order`` or ``DEFAULT_SKILL_ORDER``.
      When ``enabled`` is a list, keep only those ids (preserving order).
    - ``requested`` is a list → run exactly those ids (must be known);
      ``enabled`` / ``default_order`` are ignored.
    """
    if requested is None:
        order = list(default_order) if default_order is not None else list(DEFAULT_SKILL_ORDER)
        unknown = [s for s in order if s not in ALL_SKILLS]
        if unknown:
            raise ApSkillError(f"Unknown skill(s): {', '.join(unknown)}")
        if enabled is not None:
            allowed = {str(s) for s in enabled if str(s) in ALL_SKILLS}
            order = [s for s in order if s in allowed]
        if not order:
            raise ApSkillError("No skills to run.")
        return order

    unknown = [s for s in requested if s not in ALL_SKILLS]
    if unknown:
        raise ApSkillError(f"Unknown skill(s): {', '.join(unknown)}")
    if not requested:
        raise ApSkillError("No skills requested.")
    return list(requested)


def ensure_ezofis_hana_po_lookup(
    skills: list[str],
    *,
    tenant_id: str,
    force: bool = True,
    document_job: Optional[dict[str, Any]] = None,
    thresholds: Optional[dict[str, Any]] = None,
) -> list[str]:
    """Inject ``po_lookup_sap`` before ``po_match`` when Workflow asks for SAP/HANA.

    Phase 3: any tenant — gated only on payload/Workflow + Catalog ``force``.
    Core should already stamp skills; this is a Catalog-opt-in safety net.
    ``tenant_id`` is unused (kept for call-site compatibility).
    """
    _ = tenant_id
    if not force:
        return skills
    if "po_match" not in skills or "po_lookup_sap" in skills:
        return skills
    if not wants_sap_or_hana_po_master(document_job, thresholds=thresholds):
        return skills
    out = list(skills)
    out.insert(out.index("po_match"), "po_lookup_sap")
    logger.info(
        "ap_workflow_sap_hana_po_lookup_injected",
        extra={"skills": out},
    )
    return out


async def maybe_reorder(
    skills: list[str],
    *,
    llm: Any,
    use_planner: bool,
    llm_overrides: Optional[dict[str, Any]] = None,
    tenant_id: Optional[str] = None,
    settings: Any = None,
) -> list[str]:
    if not use_planner or llm is None or len(skills) < 2:
        return skills
    allowed = set(skills)
    try:
        from app.ap_skills.instructions import resolve_system_prompt

        system = await resolve_system_prompt(
            code_fallback=_PLANNER_PROMPT,
            tenant_id=tenant_id,
            settings=settings,
        )
    except Exception:
        logger.warning("ap_planner_instructions_failed", extra={"error_type": "instructions"})
        system = _PLANNER_PROMPT
    try:
        result = await llm.chat_completion(
            [
                {"role": "system", "content": system},
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
