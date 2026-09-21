"""Load agent packs: Catalog DB (platform + tenant) with disk/SQLite fallback."""
from __future__ import annotations

import logging
from dataclasses import replace
from functools import lru_cache
from typing import Any, Optional

from app.agent_skills.loader import clear_skill_cache, get_skill, resolve_pack_dir_from_settings
from app.agent_skills.types import LoadedRule, LoadedSkill
from app.catalog.store import CatalogStoreUnavailableError

logger = logging.getLogger("orchestrator.agent_packs.overlay")

_catalog_ref: Any = None


def set_catalog_store(catalog: Any) -> None:
    """Wire CatalogStore from app lifespan (or None to disable DB packs)."""
    global _catalog_ref
    _catalog_ref = catalog
    clear_agent_pack_cache()


def clear_agent_pack_cache() -> None:
    clear_skill_cache()
    _merge_cached.cache_clear()
    try:
        from app.tenant_skills.store import reset_store

        reset_store()
    except Exception:
        pass


def _packs_from_db_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "agent_packs_from_db", True))


async def _load_from_catalog(
    agent: str,
    *,
    tenant_id: Optional[str],
    settings: Any,
) -> Optional[LoadedSkill]:
    catalog = _catalog_ref
    if catalog is None or not _packs_from_db_enabled(settings):
        return None
    try:
        from app.agent_packs.store import AgentPackStore

        store = AgentPackStore(catalog)
        if await store.platform_pack_count(agent) <= 0:
            return None
        pack_dir = resolve_pack_dir_from_settings(agent, settings)
        skill = await store.load_platform_skill(agent, pack_dir=pack_dir)
        if skill is None:
            return None
        tid = (tenant_id or "").strip()
        if not tid:
            return skill
        extras = await store.list_active_tenant_skills(tenant_id=tid, agent_slug=agent)
        if extras:
            blocks = [skill.skill_body.strip()]
            for row in extras:
                blocks.append(str(row.get("body") or "").strip())
            skill = replace(skill, skill_body="\n\n".join(b for b in blocks if b))
        rules = await store.list_active_tenant_rules(tenant_id=tid, agent_slug=agent)
        if rules:
            extra_rules = tuple(
                LoadedRule(
                    path=skill.pack_dir / "rules" / f"{row['slug']}.mdc",
                    description=f"Tenant rule: {row['slug']}",
                    body=str(row.get("body") or ""),
                    always_apply=bool(row.get("always_apply", True)),
                )
                for row in rules
            )
            skill = replace(skill, rules=skill.rules + extra_rules)
        return skill
    except CatalogStoreUnavailableError:
        logger.warning("agent_packs_catalog_unavailable", extra={"agent": agent})
        return None
    except Exception as exc:
        logger.warning(
            "agent_packs_catalog_load_failed",
            extra={"agent": agent, "error_type": type(exc).__name__, "error": str(exc)[:200]},
        )
        return None


def _load_disk_with_sqlite_overlay(agent: str, *, tenant_id: Optional[str], settings: Any) -> LoadedSkill:
    skill = get_skill(agent, settings=settings)
    tid = (tenant_id or "").strip()
    if not tid or agent != "summary":
        return skill
    try:
        from app.tenant_skills.overlay import get_summary_skill

        return get_summary_skill(tenant_id=tid, settings=settings)
    except Exception:
        return skill


@lru_cache(maxsize=64)
def _merge_cached(agent: str, tenant_key: str, settings_key: str) -> LoadedSkill:
    # Sync cache key only — async path uses get_agent_skill below.
    raise RuntimeError("use get_agent_skill")


async def get_agent_skill(
    agent: str,
    *,
    tenant_id: Optional[str] = None,
    settings: Any = None,
) -> LoadedSkill:
    """Platform pack (+ tenant extras) for any markdown agent."""
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    agent = (agent or "").strip().lower()
    db_skill = await _load_from_catalog(agent, tenant_id=tenant_id, settings=settings)
    if db_skill is not None:
        return db_skill
    return _load_disk_with_sqlite_overlay(agent, tenant_id=tenant_id, settings=settings)


def get_summary_skill(*, tenant_id: Optional[str] = None, settings=None) -> LoadedSkill:
    """Sync Summary path used by existing code — prefers Catalog when available via cache warm.

    Prefer ``await get_agent_skill('summary', ...)`` in async call sites.
    """
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    # Sync callers: disk + SQLite overlay (legacy). Async agents should use get_agent_skill.
    # When Catalog is wired, summary_skills.rules will call async path where possible.
    return _load_disk_with_sqlite_overlay("summary", tenant_id=tenant_id, settings=settings)
