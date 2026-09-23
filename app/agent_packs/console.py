"""Console helpers for agent packs (Catalog DB preferred, SQLite fallback)."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, Request

from app.agent_packs.overlay import clear_agent_pack_cache
from app.agent_packs.store import AgentPackStore
from app.agent_skills.loader import resolve_pack_dir_from_settings
from app.catalog.store import CatalogStoreUnavailableError
from app.config import get_settings
from app.tenant_skills.store import store_from_settings
from app.tenant_skills.upload import parse_tenant_upload, upload_kind


async def _pack_store(request: Request) -> Optional[AgentPackStore]:
    catalog = getattr(request.app.state, "catalog_store", None)
    if catalog is None:
        return None
    settings = get_settings()
    if not getattr(settings, "agent_packs_from_db", True):
        return None
    # Prefer Catalog only when platform packs were seeded. Unit tests without
    # Catalog seed keep using SQLite so existing console APIs stay stable.
    try:
        store = AgentPackStore(catalog)
        n = 0
        for agent in (
            "summary",
            "classification",
            "document_intelligent",
            "ocr",
            "insight",
            "prompt",
            "pdf",
            "ap",
            "dashboard-prompts",
            "dashboard-schema",
            "dashboard-data",
        ):
            n += await store.platform_pack_count(agent)
            if n > 0:
                return store
        return None
    except CatalogStoreUnavailableError:
        return None
    except Exception:
        return None


async def console_defaults(request: Request, agent: str = "summary") -> dict:
    settings = get_settings()
    pack = await _pack_store(request)
    if pack is not None:
        return {"defaults": await pack.list_platform_defaults(agent), "source": "catalog"}
    store = store_from_settings(settings)
    pack_dir = resolve_pack_dir_from_settings(agent if agent != "pdf" else "summary", settings)
    if agent == "summary":
        return {"defaults": store.list_defaults(pack_dir=pack_dir), "source": "disk"}
    # Non-summary without catalog: expose disk via loader shape
    from app.agent_skills.loader import get_skill

    skill = get_skill(agent, settings=settings)
    return {
        "defaults": {
            "skill": {
                "slug": "default1",
                "source_file": "SKILL.md",
                "readonly": True,
                "body": skill.skill_body,
            },
            "rules": [
                {
                    "slug": r.path.stem,
                    "source_file": f"rules/{r.path.name}",
                    "readonly": True,
                    "body": r.body,
                    "always_apply": r.always_apply,
                }
                for r in skill.rules
            ],
        },
        "source": "disk",
    }


async def console_list(request: Request, *, agent: str, tenant_id: Optional[str]) -> dict:
    settings = get_settings()
    tid = (tenant_id or "").strip()
    pack = await _pack_store(request)
    base = await console_defaults(request, agent)
    payload: dict[str, Any] = {
        "tenant_id": tid or None,
        "agent": agent,
        "defaults": base["defaults"],
        "source": base.get("source"),
        "custom_skills": [],
        "custom_rules": [],
        "logs": [],
    }
    if not tid:
        return payload
    if pack is not None:
        payload["custom_skills"] = await pack.list_custom_skills(tenant_id=tid, agent_slug=agent)
        payload["custom_rules"] = await pack.list_custom_rules(tenant_id=tid, agent_slug=agent)
        payload["logs"] = await pack.list_logs(tenant_id=tid, agent_slug=agent, limit=20)
        return payload
    if agent != "summary":
        return payload
    store = store_from_settings(settings)
    store.migrate_legacy_md_rules_to_skills(tenant_id=tid, agent="summary")
    payload["custom_skills"] = store.list_custom_skills(tenant_id=tid, agent="summary")
    payload["custom_rules"] = store.list_custom_rules(tenant_id=tid, agent="summary")
    payload["logs"] = store.list_logs(tenant_id=tid, agent="summary", limit=20)
    payload["source"] = "sqlite"
    return payload


async def console_add_rule(
    request: Request, *, agent: str, tenant_id: str, body: str, changed_by: str
) -> dict:
    pack = await _pack_store(request)
    if pack is not None:
        rule = await pack.add_custom_rule(
            tenant_id=tenant_id, agent_slug=agent, body=body, changed_by=changed_by
        )
        clear_agent_pack_cache()
        return {"rule": rule, "source": "catalog"}
    if agent != "summary":
        raise HTTPException(status_code=400, detail="Catalog packs required for this agent")
    store = store_from_settings(get_settings())
    rule = store.add_custom_rule(tenant_id=tenant_id, body=body, changed_by=changed_by)
    clear_agent_pack_cache()
    return {"rule": rule, "source": "sqlite"}


async def console_upload(
    request: Request,
    *,
    agent: str,
    tenant_id: str,
    filename: str,
    raw: str,
    changed_by: str,
) -> dict:
    kind = upload_kind(filename)
    source_file, body = parse_tenant_upload(filename=filename, raw=raw)
    pack = await _pack_store(request)
    tid = tenant_id.strip()
    if pack is not None:
        if kind == "skill":
            skill = await pack.add_custom_skill(
                tenant_id=tid,
                agent_slug=agent,
                body=body,
                source_file=source_file,
                changed_by=changed_by,
            )
            clear_agent_pack_cache()
            return {"kind": "skill", "skill": skill, "source_file": source_file, "source": "catalog"}
        rule = await pack.add_custom_rule(
            tenant_id=tid,
            agent_slug=agent,
            body=body,
            source_file=source_file,
            changed_by=changed_by,
        )
        clear_agent_pack_cache()
        return {"kind": "rule", "rule": rule, "source_file": source_file, "source": "catalog"}
    if agent != "summary":
        raise HTTPException(status_code=400, detail="Catalog packs required for this agent")
    store = store_from_settings(get_settings())
    store.migrate_legacy_md_rules_to_skills(tenant_id=tid, agent="summary")
    if kind == "skill":
        skill = store.add_custom_skill(
            tenant_id=tid, body=body, source_file=source_file, changed_by=changed_by
        )
        clear_agent_pack_cache()
        return {"kind": "skill", "skill": skill, "source_file": source_file, "source": "sqlite"}
    rule = store.add_custom_rule(
        tenant_id=tid, body=body, source_file=source_file, changed_by=changed_by
    )
    clear_agent_pack_cache()
    return {"kind": "rule", "rule": rule, "source_file": source_file, "source": "sqlite"}


async def console_update_skill(
    request: Request,
    *,
    agent: str,
    item_id: str,
    tenant_id: str,
    body: Optional[str],
    is_active: Optional[bool],
    changed_by: str,
    source_file: Optional[str] = None,
) -> dict:
    pack = await _pack_store(request)
    if pack is not None:
        skill = await pack.update_custom_skill(
            item_id=item_id,
            tenant_id=tenant_id,
            body=body,
            is_active=is_active,
            changed_by=changed_by,
        )
        clear_agent_pack_cache()
        return {"kind": "skill", "skill": skill, "source": "catalog"}
    if agent != "summary":
        raise HTTPException(status_code=400, detail="Catalog packs required for this agent")
    store = store_from_settings(get_settings())
    kwargs: dict[str, Any] = {
        "item_id": int(item_id),
        "tenant_id": tenant_id,
        "changed_by": changed_by,
    }
    if body is not None:
        kwargs["body"] = body
    if is_active is not None:
        kwargs["is_active"] = is_active
    if source_file is not None:
        kwargs["source_file"] = source_file
    skill = store.update_custom_skill(**kwargs)
    clear_agent_pack_cache()
    return {"kind": "skill", "skill": skill, "source": "sqlite"}


async def console_update_rule(
    request: Request,
    *,
    agent: str,
    item_id: str,
    tenant_id: str,
    body: Optional[str],
    is_active: Optional[bool],
    changed_by: str,
    source_file: Optional[str] = None,
) -> dict:
    pack = await _pack_store(request)
    if pack is not None:
        rule = await pack.update_custom_rule(
            item_id=item_id,
            tenant_id=tenant_id,
            body=body,
            is_active=is_active,
            changed_by=changed_by,
        )
        clear_agent_pack_cache()
        return {"kind": "rule", "rule": rule, "source": "catalog"}
    if agent != "summary":
        raise HTTPException(status_code=400, detail="Catalog packs required for this agent")
    store = store_from_settings(get_settings())
    kwargs: dict[str, Any] = {
        "item_id": int(item_id),
        "tenant_id": tenant_id,
        "changed_by": changed_by,
    }
    if body is not None:
        kwargs["body"] = body
    if is_active is not None:
        kwargs["is_active"] = is_active
    if source_file is not None:
        kwargs["source_file"] = source_file
    rule = store.update_custom_rule(**kwargs)
    clear_agent_pack_cache()
    return {"kind": "rule", "rule": rule, "source": "sqlite"}


async def console_delete_skill(
    request: Request, *, agent: str, item_id: str, tenant_id: str, changed_by: str
) -> dict:
    pack = await _pack_store(request)
    if pack is not None:
        await pack.delete_custom_skill(item_id=item_id, tenant_id=tenant_id, changed_by=changed_by)
        clear_agent_pack_cache()
        return {"ok": True, "kind": "skill", "deleted": {"id": item_id}, "source": "catalog"}
    if agent != "summary":
        raise HTTPException(status_code=400, detail="Catalog packs required for this agent")
    store = store_from_settings(get_settings())
    deleted = store.delete_custom_skill(
        item_id=int(item_id), tenant_id=tenant_id, changed_by=changed_by
    )
    clear_agent_pack_cache()
    return {"ok": True, "kind": "skill", "deleted": deleted, "source": "sqlite"}


async def console_delete_rule(
    request: Request, *, agent: str, item_id: str, tenant_id: str, changed_by: str
) -> dict:
    pack = await _pack_store(request)
    if pack is not None:
        await pack.delete_custom_rule(item_id=item_id, tenant_id=tenant_id, changed_by=changed_by)
        clear_agent_pack_cache()
        return {"ok": True, "kind": "rule", "deleted": {"id": item_id}, "source": "catalog"}
    if agent != "summary":
        raise HTTPException(status_code=400, detail="Catalog packs required for this agent")
    store = store_from_settings(get_settings())
    deleted = store.delete_custom_rule(
        item_id=int(item_id), tenant_id=tenant_id, changed_by=changed_by
    )
    clear_agent_pack_cache()
    return {"ok": True, "kind": "rule", "deleted": deleted, "source": "sqlite"}
