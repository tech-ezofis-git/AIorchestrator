"""Merge read-only default Summary pack with tenant custom skills/rules from SQLite."""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from app.agent_skills.loader import clear_skill_cache, get_skill
from app.agent_skills.types import LoadedRule, LoadedSkill
from app.tenant_skills.store import store_from_settings


def _append_custom_skills(skill: LoadedSkill, tenant_id: str, settings=None) -> LoadedSkill:
    tenant_id = (tenant_id or "").strip()
    if not tenant_id:
        return skill
    store = store_from_settings(settings)
    extras = store.list_active_custom_skills(tenant_id=tenant_id, agent="summary")
    if not extras:
        return skill
    blocks = [skill.skill_body.strip()]
    for row in extras:
        blocks.append(row["body"].strip())
    return replace(skill, skill_body="\n\n".join(b for b in blocks if b))


def _append_custom_rules(skill: LoadedSkill, tenant_id: str, settings=None) -> LoadedSkill:
    tenant_id = (tenant_id or "").strip()
    if not tenant_id:
        return skill
    store = store_from_settings(settings)
    extras = store.list_active_custom_rules(tenant_id=tenant_id, agent="summary")
    if not extras:
        return skill
    extra_rules = tuple(
        LoadedRule(
            path=skill.pack_dir / "rules" / f"{row['slug']}.mdc",
            description=f"Tenant rule: {row['slug']}",
            body=row["body"],
            always_apply=bool(row.get("always_apply", 1)),
        )
        for row in extras
    )
    return replace(skill, rules=skill.rules + extra_rules)


def get_summary_skill(*, tenant_id: Optional[str] = None, settings=None) -> LoadedSkill:
    """Default Summary pack from disk + active tenant custom skills and rules."""
    skill = get_skill("summary", settings=settings)
    skill = _append_custom_skills(skill, tenant_id or "", settings=settings)
    return _append_custom_rules(skill, tenant_id or "", settings=settings)


def clear_summary_skill_cache() -> None:
    clear_skill_cache()
    from app.tenant_skills.store import reset_store

    reset_store()
