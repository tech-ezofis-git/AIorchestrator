"""Seed / read / write agent packs in Catalog Postgres."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.agent_skills.loader import _parse_frontmatter, default_skills_root, resolve_pack_dir_from_settings
from app.agent_skills.types import LoadedRule, LoadedSkill
from app.catalog.store import CatalogStore, CatalogStoreUnavailableError

logger = logging.getLogger("orchestrator.agent_packs")

_MAX_BODY_CHARS = 64 * 1024
_CUSTOM_SLUG_RE = re.compile(r"^custom(\d+)$")
_PACK_AGENTS = ("summary", "ocr", "insight", "prompt", "pdf")


def _row_get(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    # asyncpg.Record supports mapping get(); platform SELECTs omit tenant_id.
    getter = getattr(row, "get", None)
    if callable(getter):
        return getter(key)
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _fmt_dt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _public_skill(row: Any, *, readonly: bool = False) -> dict[str, Any]:
    return {
        "id": str(_row_get(row, "id")),
        "tenant_id": _row_get(row, "tenant_id"),
        "agent": _row_get(row, "agent_slug") or _row_get(row, "agent"),
        "slug": _row_get(row, "slug"),
        "source_file": _row_get(row, "source_file"),
        "body": _row_get(row, "body") or "",
        "is_active": bool(_row_get(row, "is_active")),
        "readonly": readonly,
        "updated_by": _row_get(row, "updated_by"),
        "created_at": _fmt_dt(_row_get(row, "created_at")),
        "updated_at": _fmt_dt(_row_get(row, "updated_at")),
    }


def _public_rule(row: Any, *, readonly: bool = False) -> dict[str, Any]:
    out = _public_skill(row, readonly=readonly)
    out["always_apply"] = bool(_row_get(row, "always_apply") if _row_get(row, "always_apply") is not None else True)
    out["description"] = _row_get(row, "description") or out.get("slug")
    return out


class AgentPackStore:
    """Platform + tenant pack CRUD against CatalogStore's asyncpg pool."""

    def __init__(self, catalog: CatalogStore):
        self._catalog = catalog

    async def list_platform_defaults(self, agent_slug: str) -> dict[str, Any]:
        agent_slug = (agent_slug or "").strip().lower()
        skill_rows = await self._catalog._run(
            "fetch",
            "SELECT id, agent_slug, slug, source_file, body, is_active, updated_by, created_at, updated_at "
            "FROM platform_agent_skills WHERE agent_slug = $1 AND is_active = TRUE "
            "ORDER BY sort_order ASC, slug ASC",
            agent_slug,
        )
        rule_rows = await self._catalog._run(
            "fetch",
            "SELECT id, agent_slug, slug, source_file, description, body, always_apply, is_active, updated_by, created_at, updated_at "
            "FROM platform_agent_rules WHERE agent_slug = $1 AND is_active = TRUE "
            "ORDER BY sort_order ASC, slug ASC",
            agent_slug,
        )
        skill = None
        for row in skill_rows:
            if str(_row_get(row, "slug") or "").upper() in {"SKILL", "DEFAULT1"} or (
                _row_get(row, "source_file") or ""
            ).endswith("SKILL.md"):
                skill = _public_skill(row, readonly=True)
                skill["slug"] = "default1"
                break
        if skill is None and skill_rows:
            skill = _public_skill(skill_rows[0], readonly=True)
            skill["slug"] = "default1"
        return {
            "skill": skill,
            "rules": [_public_rule(r, readonly=True) for r in rule_rows],
        }

    async def platform_pack_count(self, agent_slug: str) -> int:
        row = await self._catalog._run(
            "fetchrow",
            "SELECT ("
            " (SELECT COUNT(*) FROM platform_agent_skills WHERE agent_slug = $1 AND is_active) "
            "+ (SELECT COUNT(*) FROM platform_agent_rules WHERE agent_slug = $1 AND is_active)"
            ") AS n",
            agent_slug,
        )
        return int(_row_get(row, "n") or 0)

    async def load_platform_skill(self, agent_slug: str, *, pack_dir: Path) -> Optional[LoadedSkill]:
        defaults = await self.list_platform_defaults(agent_slug)
        skill_row = defaults.get("skill")
        if not skill_row or not (skill_row.get("body") or "").strip():
            return None
        rules = tuple(
            LoadedRule(
                path=pack_dir / "rules" / f"{r['slug']}.mdc",
                description=str(r.get("description") or r.get("slug") or "rule"),
                body=str(r.get("body") or ""),
                always_apply=bool(r.get("always_apply", True)),
            )
            for r in (defaults.get("rules") or [])
            if (r.get("body") or "").strip()
        )
        return LoadedSkill(
            agent=agent_slug,
            skill_id=agent_slug,
            name=agent_slug,
            description=f"Catalog platform pack for {agent_slug}",
            skill_body=str(skill_row["body"]),
            rules=rules,
            pack_dir=pack_dir,
        )

    async def list_active_tenant_skills(self, *, tenant_id: str, agent_slug: str) -> list[dict[str, Any]]:
        rows = await self._catalog._run(
            "fetch",
            "SELECT id, tenant_id, agent_slug, slug, source_file, body, is_active, updated_by, created_at, updated_at "
            "FROM tenant_agent_skills WHERE tenant_id = $1 AND agent_slug = $2 AND is_active = TRUE "
            "ORDER BY slug ASC",
            tenant_id,
            agent_slug,
        )
        return [_public_skill(r) for r in rows]

    async def list_active_tenant_rules(self, *, tenant_id: str, agent_slug: str) -> list[dict[str, Any]]:
        rows = await self._catalog._run(
            "fetch",
            "SELECT id, tenant_id, agent_slug, slug, source_file, body, always_apply, is_active, updated_by, created_at, updated_at "
            "FROM tenant_agent_rules WHERE tenant_id = $1 AND agent_slug = $2 AND is_active = TRUE "
            "ORDER BY slug ASC",
            tenant_id,
            agent_slug,
        )
        return [_public_rule(r) for r in rows]

    async def list_custom_skills(self, *, tenant_id: str, agent_slug: str) -> list[dict[str, Any]]:
        rows = await self._catalog._run(
            "fetch",
            "SELECT id, tenant_id, agent_slug, slug, source_file, body, is_active, updated_by, created_at, updated_at "
            "FROM tenant_agent_skills WHERE tenant_id = $1 AND agent_slug = $2 ORDER BY slug ASC",
            tenant_id,
            agent_slug,
        )
        return [_public_skill(r) for r in rows]

    async def list_custom_rules(self, *, tenant_id: str, agent_slug: str) -> list[dict[str, Any]]:
        rows = await self._catalog._run(
            "fetch",
            "SELECT id, tenant_id, agent_slug, slug, source_file, body, always_apply, is_active, updated_by, created_at, updated_at "
            "FROM tenant_agent_rules WHERE tenant_id = $1 AND agent_slug = $2 ORDER BY slug ASC",
            tenant_id,
            agent_slug,
        )
        return [_public_rule(r) for r in rows]

    async def list_logs(self, *, tenant_id: str, agent_slug: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._catalog._run(
            "fetch",
            "SELECT id, tenant_id, agent_slug, item_type, item_id, action, old_value, new_value, changed_by, changed_at "
            "FROM tenant_agent_skill_rule_logs WHERE tenant_id = $1 AND agent_slug = $2 "
            "ORDER BY changed_at DESC LIMIT $3",
            tenant_id,
            agent_slug,
            max(1, min(100, int(limit))),
        )
        out = []
        for row in rows:
            out.append(
                {
                    "id": _row_get(row, "id"),
                    "tenant_id": _row_get(row, "tenant_id"),
                    "agent": _row_get(row, "agent_slug"),
                    "item_type": _row_get(row, "item_type"),
                    "item_id": str(_row_get(row, "item_id")) if _row_get(row, "item_id") else None,
                    "action": _row_get(row, "action"),
                    "old_value": _row_get(row, "old_value"),
                    "new_value": _row_get(row, "new_value"),
                    "changed_by": _row_get(row, "changed_by"),
                    "changed_at": _fmt_dt(_row_get(row, "changed_at")),
                }
            )
        return out

    async def _next_custom_slug(self, table: str, *, tenant_id: str, agent_slug: str) -> str:
        rows = await self._catalog._run(
            "fetch",
            f"SELECT slug FROM {table} WHERE tenant_id = $1 AND agent_slug = $2",
            tenant_id,
            agent_slug,
        )
        used = set()
        for row in rows:
            m = _CUSTOM_SLUG_RE.match(str(_row_get(row, "slug") or ""))
            if m:
                used.add(int(m.group(1)))
        n = 1
        while n in used:
            n += 1
        return f"custom{n}"

    async def _log(
        self,
        *,
        tenant_id: str,
        agent_slug: str,
        item_type: str,
        item_id: Optional[str],
        action: str,
        old_value: Optional[str],
        new_value: Optional[str],
        changed_by: str,
    ) -> None:
        await self._catalog._run(
            "execute",
            "INSERT INTO tenant_agent_skill_rule_logs "
            "(tenant_id, agent_slug, item_type, item_id, action, old_value, new_value, changed_by) "
            "VALUES ($1, $2, $3, $4::uuid, $5, $6, $7, $8)",
            tenant_id,
            agent_slug,
            item_type,
            item_id,
            action,
            old_value,
            new_value,
            changed_by,
        )

    async def add_custom_skill(
        self,
        *,
        tenant_id: str,
        agent_slug: str,
        body: str,
        source_file: Optional[str] = None,
        changed_by: str = "console",
    ) -> dict[str, Any]:
        tid = tenant_id.strip()
        agent = agent_slug.strip().lower()
        text = (body or "").strip()
        if not tid or not agent or not text:
            raise ValueError("tenant_id, agent, and body are required")
        if len(text) > _MAX_BODY_CHARS:
            raise ValueError(f"body exceeds {_MAX_BODY_CHARS} characters")
        slug = await self._next_custom_slug("tenant_agent_skills", tenant_id=tid, agent_slug=agent)
        item_id = str(uuid.uuid4())
        row = await self._catalog._run(
            "fetchrow",
            "INSERT INTO tenant_agent_skills (id, tenant_id, agent_slug, slug, source_file, body, is_active, updated_by) "
            "VALUES ($1::uuid, $2, $3, $4, $5, $6, TRUE, $7) "
            "RETURNING id, tenant_id, agent_slug, slug, source_file, body, is_active, updated_by, created_at, updated_at",
            item_id,
            tid,
            agent,
            slug,
            source_file or f"{slug}.md",
            text,
            changed_by,
        )
        await self._log(
            tenant_id=tid,
            agent_slug=agent,
            item_type="skill",
            item_id=item_id,
            action="CREATE",
            old_value=None,
            new_value=text,
            changed_by=changed_by,
        )
        return _public_skill(row)

    async def add_custom_rule(
        self,
        *,
        tenant_id: str,
        agent_slug: str,
        body: str,
        source_file: Optional[str] = None,
        changed_by: str = "console",
    ) -> dict[str, Any]:
        tid = tenant_id.strip()
        agent = agent_slug.strip().lower()
        text = (body or "").strip()
        if not tid or not agent or not text:
            raise ValueError("tenant_id, agent, and body are required")
        if len(text) > _MAX_BODY_CHARS:
            raise ValueError(f"body exceeds {_MAX_BODY_CHARS} characters")
        slug = await self._next_custom_slug("tenant_agent_rules", tenant_id=tid, agent_slug=agent)
        item_id = str(uuid.uuid4())
        row = await self._catalog._run(
            "fetchrow",
            "INSERT INTO tenant_agent_rules "
            "(id, tenant_id, agent_slug, slug, source_file, body, always_apply, is_active, updated_by) "
            "VALUES ($1::uuid, $2, $3, $4, $5, $6, TRUE, TRUE, $7) "
            "RETURNING id, tenant_id, agent_slug, slug, source_file, body, always_apply, is_active, updated_by, created_at, updated_at",
            item_id,
            tid,
            agent,
            slug,
            source_file or f"{slug}.mdc",
            text,
            changed_by,
        )
        await self._log(
            tenant_id=tid,
            agent_slug=agent,
            item_type="rule",
            item_id=item_id,
            action="CREATE",
            old_value=None,
            new_value=text,
            changed_by=changed_by,
        )
        return _public_rule(row)

    async def update_custom_skill(
        self,
        *,
        item_id: str,
        tenant_id: str,
        body: Optional[str] = None,
        is_active: Optional[bool] = None,
        changed_by: str = "console",
    ) -> dict[str, Any]:
        row = await self._catalog._run(
            "fetchrow",
            "SELECT id, tenant_id, agent_slug, slug, source_file, body, is_active, updated_by, created_at, updated_at "
            "FROM tenant_agent_skills WHERE id = $1::uuid AND tenant_id = $2",
            item_id,
            tenant_id.strip(),
        )
        if not row:
            raise ValueError("skill not found")
        old_body = _row_get(row, "body") or ""
        old_active = bool(_row_get(row, "is_active"))
        new_body = old_body if body is None else body.strip()
        new_active = old_active if is_active is None else bool(is_active)
        if body is not None and len(new_body) > _MAX_BODY_CHARS:
            raise ValueError(f"body exceeds {_MAX_BODY_CHARS} characters")
        updated = await self._catalog._run(
            "fetchrow",
            "UPDATE tenant_agent_skills SET body = $1, is_active = $2, updated_by = $3, updated_at = now() "
            "WHERE id = $4::uuid AND tenant_id = $5 "
            "RETURNING id, tenant_id, agent_slug, slug, source_file, body, is_active, updated_by, created_at, updated_at",
            new_body,
            new_active,
            changed_by,
            item_id,
            tenant_id.strip(),
        )
        action = "UPDATE"
        if is_active is not None and new_active != old_active:
            action = "ENABLE" if new_active else "DISABLE"
        await self._log(
            tenant_id=tenant_id.strip(),
            agent_slug=str(_row_get(row, "agent_slug")),
            item_type="skill",
            item_id=item_id,
            action=action,
            old_value=old_body,
            new_value=new_body,
            changed_by=changed_by,
        )
        return _public_skill(updated)

    async def update_custom_rule(
        self,
        *,
        item_id: str,
        tenant_id: str,
        body: Optional[str] = None,
        is_active: Optional[bool] = None,
        changed_by: str = "console",
    ) -> dict[str, Any]:
        row = await self._catalog._run(
            "fetchrow",
            "SELECT id, tenant_id, agent_slug, slug, source_file, body, always_apply, is_active, updated_by, created_at, updated_at "
            "FROM tenant_agent_rules WHERE id = $1::uuid AND tenant_id = $2",
            item_id,
            tenant_id.strip(),
        )
        if not row:
            raise ValueError("rule not found")
        old_body = _row_get(row, "body") or ""
        old_active = bool(_row_get(row, "is_active"))
        new_body = old_body if body is None else body.strip()
        new_active = old_active if is_active is None else bool(is_active)
        if body is not None and len(new_body) > _MAX_BODY_CHARS:
            raise ValueError(f"body exceeds {_MAX_BODY_CHARS} characters")
        updated = await self._catalog._run(
            "fetchrow",
            "UPDATE tenant_agent_rules SET body = $1, is_active = $2, updated_by = $3, updated_at = now() "
            "WHERE id = $4::uuid AND tenant_id = $5 "
            "RETURNING id, tenant_id, agent_slug, slug, source_file, body, always_apply, is_active, updated_by, created_at, updated_at",
            new_body,
            new_active,
            changed_by,
            item_id,
            tenant_id.strip(),
        )
        action = "UPDATE"
        if is_active is not None and new_active != old_active:
            action = "ENABLE" if new_active else "DISABLE"
        await self._log(
            tenant_id=tenant_id.strip(),
            agent_slug=str(_row_get(row, "agent_slug")),
            item_type="rule",
            item_id=item_id,
            action=action,
            old_value=old_body,
            new_value=new_body,
            changed_by=changed_by,
        )
        return _public_rule(updated)

    async def delete_custom_skill(self, *, item_id: str, tenant_id: str, changed_by: str = "console") -> None:
        row = await self._catalog._run(
            "fetchrow",
            "DELETE FROM tenant_agent_skills WHERE id = $1::uuid AND tenant_id = $2 "
            "RETURNING id, agent_slug, body",
            item_id,
            tenant_id.strip(),
        )
        if not row:
            raise ValueError("skill not found")
        await self._log(
            tenant_id=tenant_id.strip(),
            agent_slug=str(_row_get(row, "agent_slug")),
            item_type="skill",
            item_id=item_id,
            action="DELETE",
            old_value=_row_get(row, "body"),
            new_value=None,
            changed_by=changed_by,
        )

    async def delete_custom_rule(self, *, item_id: str, tenant_id: str, changed_by: str = "console") -> None:
        row = await self._catalog._run(
            "fetchrow",
            "DELETE FROM tenant_agent_rules WHERE id = $1::uuid AND tenant_id = $2 "
            "RETURNING id, agent_slug, body",
            item_id,
            tenant_id.strip(),
        )
        if not row:
            raise ValueError("rule not found")
        await self._log(
            tenant_id=tenant_id.strip(),
            agent_slug=str(_row_get(row, "agent_slug")),
            item_type="rule",
            item_id=item_id,
            action="DELETE",
            old_value=_row_get(row, "body"),
            new_value=None,
            changed_by=changed_by,
        )

    async def upsert_platform_skill(
        self,
        *,
        agent_slug: str,
        slug: str,
        body: str,
        source_file: Optional[str] = None,
        sort_order: int = 0,
        updated_by: str = "seed",
    ) -> None:
        await self._catalog._run(
            "execute",
            "INSERT INTO platform_agent_skills (id, agent_slug, slug, source_file, body, sort_order, is_active, updated_by) "
            "VALUES ($1::uuid, $2, $3, $4, $5, $6, TRUE, $7) "
            "ON CONFLICT (agent_slug, slug) DO UPDATE SET "
            "body = EXCLUDED.body, source_file = EXCLUDED.source_file, sort_order = EXCLUDED.sort_order, "
            "is_active = TRUE, updated_by = EXCLUDED.updated_by, updated_at = now(), version = platform_agent_skills.version + 1",
            str(uuid.uuid4()),
            agent_slug,
            slug,
            source_file,
            body,
            sort_order,
            updated_by,
        )

    async def upsert_platform_rule(
        self,
        *,
        agent_slug: str,
        slug: str,
        body: str,
        source_file: Optional[str] = None,
        description: Optional[str] = None,
        always_apply: bool = True,
        sort_order: int = 0,
        updated_by: str = "seed",
    ) -> None:
        await self._catalog._run(
            "execute",
            "INSERT INTO platform_agent_rules "
            "(id, agent_slug, slug, source_file, description, body, always_apply, sort_order, is_active, updated_by) "
            "VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, TRUE, $9) "
            "ON CONFLICT (agent_slug, slug) DO UPDATE SET "
            "body = EXCLUDED.body, source_file = EXCLUDED.source_file, description = EXCLUDED.description, "
            "always_apply = EXCLUDED.always_apply, "
            "sort_order = EXCLUDED.sort_order, is_active = TRUE, updated_by = EXCLUDED.updated_by, "
            "updated_at = now(), version = platform_agent_rules.version + 1",
            str(uuid.uuid4()),
            agent_slug,
            slug,
            source_file,
            description,
            body,
            always_apply,
            sort_order,
            updated_by,
        )


async def seed_platform_packs_from_disk(catalog: CatalogStore, *, settings: Any = None) -> dict[str, int]:
    """Upsert disk SKILL.md + rules/*.mdc into platform_* tables."""
    store = AgentPackStore(catalog)
    counts: dict[str, int] = {}
    for agent in _PACK_AGENTS:
        try:
            pack_dir = resolve_pack_dir_from_settings(agent, settings)
        except Exception:
            pack_dir = default_skills_root() / agent
        if not pack_dir.is_dir():
            counts[agent] = 0
            continue
        n = 0
        skill_path = pack_dir / "SKILL.md"
        if skill_path.is_file():
            _meta, body = _parse_frontmatter(skill_path.read_text(encoding="utf-8"))
            await store.upsert_platform_skill(
                agent_slug=agent,
                slug="SKILL",
                body=body,
                source_file="SKILL.md",
                sort_order=0,
            )
            n += 1
        rules_dir = pack_dir / "rules"
        if rules_dir.is_dir():
            for idx, path in enumerate(sorted(rules_dir.glob("*.mdc")), start=1):
                meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
                always = str(meta.get("alwaysApply") or "true").strip().lower() in {"1", "true", "yes", "on"}
                await store.upsert_platform_rule(
                    agent_slug=agent,
                    slug=path.stem,
                    body=body,
                    source_file=f"rules/{path.name}",
                    description=(meta.get("description") or "").strip() or path.stem,
                    always_apply=always,
                    sort_order=idx,
                )
                n += 1
        counts[agent] = n
        logger.info("agent_packs_seeded", extra={"agent": agent, "count": n})
    return counts
