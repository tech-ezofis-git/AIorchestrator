"""Postgres persistence for catalog_agents / catalog_models / catalog_tenant_models."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

from app.catalog.defaults import BUILTIN_AGENTS, RESERVED_SLUGS

logger = logging.getLogger("orchestrator.catalog")

_MIGRATIONS = (
    Path(__file__).resolve().parents[2] / "db" / "migrations" / "0005_create_catalog_tables.sql",
    Path(__file__).resolve().parents[2] / "db" / "migrations" / "0006_create_catalog_tenant_agent_models.sql",
    Path(__file__).resolve().parents[2] / "db" / "migrations" / "0007_add_agent_default_model.sql",
)
# Preset ids include dots (gpt-4.1-nano). Letters, digits, dots, hyphens, underscores.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class CatalogStoreUnavailableError(Exception):
    """Raised when a catalog read/write against Postgres fails."""


class CatalogConflictError(Exception):
    """Raised when a slug or tenant_id already exists."""


class DBExecutor(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...
    async def fetch(self, query: str, *args: Any) -> list[Any]: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...


def _row_get(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    return row[key]


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, tuple):
        return [str(v) for v in value]
    return [str(value)]


def normalize_slug(raw: str) -> str:
    slug = (raw or "").strip().lower()
    if not slug or not _SLUG_RE.match(slug):
        raise ValueError("slug must be lowercase letters, digits, dots, hyphens, or underscores (1–64 chars).")
    return slug


def _public_agent(row: Any) -> dict[str, Any]:
    return {
        "id": str(_row_get(row, "id")),
        "slug": _row_get(row, "slug"),
        "name": _row_get(row, "name"),
        "description": _row_get(row, "description") or "",
        "kind": _row_get(row, "kind"),
        "enabled": bool(_row_get(row, "enabled")),
        "system_prompt": _row_get(row, "system_prompt"),
        "trigger_phrases": _as_str_list(_row_get(row, "trigger_phrases")),
        "created_at": _fmt_dt(_row_get(row, "created_at")),
        "updated_at": _fmt_dt(_row_get(row, "updated_at")),
    }


def _fmt_dt(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _key_last4(api_key: Optional[str]) -> Optional[str]:
    if not api_key:
        return None
    return api_key[-4:] if len(api_key) >= 4 else api_key


def _public_model(row: Any) -> dict[str, Any]:
    api_key = _row_get(row, "api_key") or ""
    return {
        "id": str(_row_get(row, "id")),
        "slug": _row_get(row, "slug"),
        "label": _row_get(row, "label"),
        "model": _row_get(row, "model"),
        "api_base": _row_get(row, "api_base") or "",
        "api_version": _row_get(row, "api_version") or None,
        "region": _row_get(row, "region"),
        "model_version": _row_get(row, "model_version"),
        "enabled": bool(_row_get(row, "enabled")),
        "sort_order": int(_row_get(row, "sort_order") or 0),
        "has_api_key": bool(api_key),
        "api_key_last4": _key_last4(api_key),
        "created_at": _fmt_dt(_row_get(row, "created_at")),
        "updated_at": _fmt_dt(_row_get(row, "updated_at")),
    }


def _internal_preset(row: Any) -> dict[str, Any]:
    """Preset dict for model_presets cache — includes api_key, never sent on the wire."""
    return {
        "id": _row_get(row, "slug"),
        "label": _row_get(row, "label"),
        "model": _row_get(row, "model"),
        "model_version": _row_get(row, "model_version"),
        "region": _row_get(row, "region"),
        "api_base": _row_get(row, "api_base") or "",
        "api_key": _row_get(row, "api_key") or "",
        "api_version": _row_get(row, "api_version") or "",
    }


def _split_sql(sql: str) -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    for raw_line in sql.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--"):
            continue
        buf.append(raw_line)
        if line.endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";")
            if stmt:
                statements.append(stmt)
            buf = []
    tail = "\n".join(buf).strip().rstrip(";")
    if tail:
        statements.append(tail)
    return statements


class CatalogStore:
    def __init__(self, db: DBExecutor):
        self._db = db

    async def _run(self, method: str, query: str, *args: Any) -> Any:
        try:
            fn = getattr(self._db, method)
            return await fn(query, *args)
        except CatalogStoreUnavailableError:
            raise
        except CatalogConflictError:
            raise
        except ValueError:
            raise
        except Exception as exc:
            logger.warning(
                "catalog_store_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            raise CatalogStoreUnavailableError("Catalog store is currently unavailable.") from exc

    async def ensure_schema(self) -> None:
        for migration in _MIGRATIONS:
            sql = migration.read_text(encoding="utf-8")
            for stmt in _split_sql(sql):
                await self._run("execute", stmt)

    async def seed_defaults(self, presets: list[dict[str, Any]], settings: Any) -> None:
        now = datetime.now(timezone.utc)
        for agent in BUILTIN_AGENTS:
            agent_id = uuid.uuid4()
            await self._run(
                "execute",
                "INSERT INTO catalog_agents (id, slug, name, description, kind, enabled, system_prompt, trigger_phrases) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
                "ON CONFLICT (slug) DO NOTHING",
                agent_id,
                agent["slug"],
                agent["name"],
                agent["description"],
                "builtin",
                True,
                None,
                [],
            )
        for index, preset in enumerate(presets):
            attr = preset.get("api_key_attr")
            api_key = ""
            if attr:
                api_key = getattr(settings, attr, None) or ""
            await self._run(
                "execute",
                "INSERT INTO catalog_models (id, slug, label, model, api_base, api_key, api_version, region, model_version, enabled, sort_order) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
                "ON CONFLICT (slug) DO NOTHING",
                uuid.uuid4(),
                preset["id"],
                preset["label"],
                preset["model"],
                preset.get("api_base") or "",
                api_key,
                preset.get("api_version") or None,
                preset.get("region"),
                preset.get("model_version"),
                True,
                index,
            )
        _ = now  # timestamps come from DB defaults

    async def list_agents(self) -> list[dict[str, Any]]:
        rows = await self._run(
            "fetch",
            "SELECT id, slug, name, description, kind, enabled, system_prompt, trigger_phrases, created_at, updated_at "
            "FROM catalog_agents ORDER BY kind ASC, name ASC",
        )
        return [_public_agent(row) for row in rows]

    async def get_agent_by_slug(self, slug: str) -> Optional[dict[str, Any]]:
        slug = (slug or "").strip()
        if not slug:
            return None
        row = await self._run(
            "fetchrow",
            "SELECT id, slug, name, description, kind, enabled, system_prompt, trigger_phrases, created_at, updated_at "
            "FROM catalog_agents WHERE slug = $1",
            slug,
        )
        return _public_agent(row) if row else None

    async def get_enabled_custom(self, slug: str) -> Optional[dict[str, Any]]:
        row = await self._run(
            "fetchrow",
            "SELECT id, slug, name, description, kind, enabled, system_prompt, trigger_phrases, created_at, updated_at "
            "FROM catalog_agents WHERE slug = $1 AND kind = 'custom' AND enabled = TRUE",
            slug,
        )
        return _public_agent(row) if row else None

    async def list_enabled_custom(self) -> list[dict[str, Any]]:
        rows = await self._run(
            "fetch",
            "SELECT id, slug, name, description, kind, enabled, system_prompt, trigger_phrases, created_at, updated_at "
            "FROM catalog_agents WHERE kind = 'custom' AND enabled = TRUE ORDER BY name ASC",
        )
        return [_public_agent(row) for row in rows]

    async def create_custom_agent(
        self,
        *,
        slug: str,
        name: str,
        description: str = "",
        system_prompt: str,
        trigger_phrases: Optional[list[str]] = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        slug = normalize_slug(slug)
        if slug in RESERVED_SLUGS:
            raise ValueError(f"slug '{slug}' is reserved for a built-in agent.")
        name = (name or "").strip()
        if not name:
            raise ValueError("name is required.")
        prompt = (system_prompt or "").strip()
        if not prompt:
            raise ValueError("system_prompt is required for a custom agent.")
        phrases = [p.strip() for p in (trigger_phrases or []) if p and str(p).strip()]
        existing = await self._run(
            "fetchrow",
            "SELECT id FROM catalog_agents WHERE slug = $1",
            slug,
        )
        if existing:
            raise CatalogConflictError(f"An agent with slug '{slug}' already exists.")
        agent_id = uuid.uuid4()
        row = await self._run(
            "fetchrow",
            "INSERT INTO catalog_agents (id, slug, name, description, kind, enabled, system_prompt, trigger_phrases) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
            "RETURNING id, slug, name, description, kind, enabled, system_prompt, trigger_phrases, created_at, updated_at",
            agent_id,
            slug,
            name,
            description or "",
            "custom",
            enabled,
            prompt,
            phrases,
        )
        return _public_agent(row)

    async def update_agent(
        self,
        agent_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        enabled: Optional[bool] = None,
        system_prompt: Optional[str] = None,
        trigger_phrases: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        row = await self._run(
            "fetchrow",
            "SELECT id, slug, name, description, kind, enabled, system_prompt, trigger_phrases, created_at, updated_at "
            "FROM catalog_agents WHERE id = $1",
            uuid.UUID(str(agent_id)),
        )
        if row is None:
            raise KeyError("agent not found")
        kind = _row_get(row, "kind")
        new_name = _row_get(row, "name") if name is None else name.strip()
        if not new_name:
            raise ValueError("name is required.")
        new_description = _row_get(row, "description") if description is None else description
        new_enabled = bool(_row_get(row, "enabled") if enabled is None else enabled)
        new_prompt = _row_get(row, "system_prompt") if system_prompt is None else system_prompt
        new_phrases = _as_str_list(_row_get(row, "trigger_phrases") if trigger_phrases is None else trigger_phrases)
        if kind == "custom":
            if not (new_prompt or "").strip():
                raise ValueError("system_prompt is required for a custom agent.")
        else:
            # Built-ins: only enabled (and description) are mutable.
            new_prompt = _row_get(row, "system_prompt")
            new_phrases = _as_str_list(_row_get(row, "trigger_phrases"))
            new_name = _row_get(row, "name")
        updated = await self._run(
            "fetchrow",
            "UPDATE catalog_agents SET name = $1, description = $2, enabled = $3, system_prompt = $4, "
            "trigger_phrases = $5, updated_at = now() WHERE id = $6 "
            "RETURNING id, slug, name, description, kind, enabled, system_prompt, trigger_phrases, created_at, updated_at",
            new_name,
            new_description or "",
            new_enabled,
            new_prompt,
            [p.strip() for p in new_phrases if p and str(p).strip()],
            uuid.UUID(str(agent_id)),
        )
        return _public_agent(updated)

    async def delete_custom_agent(self, agent_id: str) -> None:
        row = await self._run(
            "fetchrow",
            "DELETE FROM catalog_agents WHERE id = $1 AND kind = 'custom' RETURNING id",
            uuid.UUID(str(agent_id)),
        )
        if row is None:
            raise KeyError("custom agent not found")

    async def list_models(self) -> list[dict[str, Any]]:
        rows = await self._run(
            "fetch",
            "SELECT id, slug, label, model, api_base, api_key, api_version, region, model_version, enabled, sort_order, created_at, updated_at "
            "FROM catalog_models ORDER BY sort_order ASC, label ASC",
        )
        return [_public_model(row) for row in rows]

    async def list_model_presets_internal(self) -> list[dict[str, Any]]:
        rows = await self._run(
            "fetch",
            "SELECT id, slug, label, model, api_base, api_key, api_version, region, model_version, enabled, sort_order, created_at, updated_at "
            "FROM catalog_models WHERE enabled = TRUE ORDER BY sort_order ASC, label ASC",
        )
        return [_internal_preset(row) for row in rows]

    async def create_model(
        self,
        *,
        slug: str,
        label: str,
        model: str,
        api_base: str = "",
        api_key: str = "",
        api_version: Optional[str] = None,
        region: Optional[str] = None,
        model_version: Optional[str] = None,
        enabled: bool = True,
        sort_order: int = 100,
    ) -> dict[str, Any]:
        slug = normalize_slug(slug)
        label = (label or "").strip()
        model = (model or "").strip()
        if not label:
            raise ValueError("label is required.")
        if not model:
            raise ValueError("model is required.")
        existing = await self._run(
            "fetchrow",
            "SELECT id FROM catalog_models WHERE slug = $1",
            slug,
        )
        if existing:
            raise CatalogConflictError(f"A model with slug '{slug}' already exists.")
        row = await self._run(
            "fetchrow",
            "INSERT INTO catalog_models (id, slug, label, model, api_base, api_key, api_version, region, model_version, enabled, sort_order) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11) "
            "RETURNING id, slug, label, model, api_base, api_key, api_version, region, model_version, enabled, sort_order, created_at, updated_at",
            uuid.uuid4(),
            slug,
            label,
            model,
            api_base or "",
            api_key or "",
            api_version or None,
            region or None,
            model_version or None,
            enabled,
            sort_order,
        )
        return _public_model(row)

    async def update_model(
        self,
        model_id: str,
        *,
        label: Optional[str] = None,
        model: Optional[str] = None,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        api_version: Optional[str] = None,
        region: Optional[str] = None,
        model_version: Optional[str] = None,
        enabled: Optional[bool] = None,
        sort_order: Optional[int] = None,
        clear_api_version: bool = False,
    ) -> dict[str, Any]:
        row = await self._run(
            "fetchrow",
            "SELECT id, slug, label, model, api_base, api_key, api_version, region, model_version, enabled, sort_order, created_at, updated_at "
            "FROM catalog_models WHERE id = $1",
            uuid.UUID(str(model_id)),
        )
        if row is None:
            raise KeyError("model not found")
        new_label = _row_get(row, "label") if label is None else label.strip()
        new_model = _row_get(row, "model") if model is None else model.strip()
        if not new_label:
            raise ValueError("label is required.")
        if not new_model:
            raise ValueError("model is required.")
        new_api_base = _row_get(row, "api_base") if api_base is None else api_base
        # Empty api_key on PATCH means keep the existing key.
        new_api_key = _row_get(row, "api_key") if not api_key else api_key
        if api_key == "":
            new_api_key = _row_get(row, "api_key")
        new_api_version = _row_get(row, "api_version") if api_version is None else api_version
        if clear_api_version:
            new_api_version = None
        new_region = _row_get(row, "region") if region is None else region
        new_model_version = _row_get(row, "model_version") if model_version is None else model_version
        new_enabled = bool(_row_get(row, "enabled") if enabled is None else enabled)
        new_sort = int(_row_get(row, "sort_order") or 0) if sort_order is None else int(sort_order)
        updated = await self._run(
            "fetchrow",
            "UPDATE catalog_models SET label = $1, model = $2, api_base = $3, api_key = $4, api_version = $5, "
            "region = $6, model_version = $7, enabled = $8, sort_order = $9, updated_at = now() WHERE id = $10 "
            "RETURNING id, slug, label, model, api_base, api_key, api_version, region, model_version, enabled, sort_order, created_at, updated_at",
            new_label,
            new_model,
            new_api_base or "",
            new_api_key or "",
            new_api_version or None,
            new_region or None,
            new_model_version or None,
            new_enabled,
            new_sort,
            uuid.UUID(str(model_id)),
        )
        return _public_model(updated)

    async def delete_model(self, model_id: str) -> None:
        in_use = await self._run(
            "fetchrow",
            "SELECT tenant_id FROM catalog_tenant_models "
            "WHERE default_model_id = $1 OR fallback_model_id = $1",
            uuid.UUID(str(model_id)),
        )
        if in_use:
            raise ValueError("Cannot delete a model that is assigned to a tenant.")
        row = await self._run(
            "fetchrow",
            "DELETE FROM catalog_models WHERE id = $1 RETURNING id",
            uuid.UUID(str(model_id)),
        )
        if row is None:
            raise KeyError("model not found")

    async def list_tenant_models(self) -> list[dict[str, Any]]:
        rows = await self._run(
            "fetch",
            "SELECT t.tenant_id, t.default_model_id, t.fallback_model_id, t.updated_at, "
            "d.slug AS default_slug, d.label AS default_label, "
            "f.slug AS fallback_slug, f.label AS fallback_label "
            "FROM catalog_tenant_models t "
            "JOIN catalog_models d ON d.id = t.default_model_id "
            "LEFT JOIN catalog_models f ON f.id = t.fallback_model_id "
            "ORDER BY t.tenant_id ASC",
        )
        return [_public_tenant(row) for row in rows]

    async def get_tenant_models(self, tenant_id: str) -> Optional[dict[str, Any]]:
        tenant_id = (tenant_id or "").strip()
        if not tenant_id:
            return None
        row = await self._run(
            "fetchrow",
            "SELECT t.tenant_id, t.default_model_id, t.fallback_model_id, t.updated_at, "
            "d.slug AS default_slug, d.label AS default_label, "
            "f.slug AS fallback_slug, f.label AS fallback_label "
            "FROM catalog_tenant_models t "
            "JOIN catalog_models d ON d.id = t.default_model_id "
            "LEFT JOIN catalog_models f ON f.id = t.fallback_model_id "
            "WHERE t.tenant_id = $1",
            tenant_id,
        )
        return _public_tenant(row) if row else None

    async def upsert_tenant_models(
        self,
        *,
        tenant_id: str,
        default_model_id: str,
        fallback_model_id: Optional[str] = None,
    ) -> dict[str, Any]:
        tenant_id = (tenant_id or "").strip()
        if not tenant_id:
            raise ValueError("tenant_id is required.")
        default_uuid = uuid.UUID(str(default_model_id))
        fallback_uuid = uuid.UUID(str(fallback_model_id)) if fallback_model_id else None
        default_row = await self._run(
            "fetchrow",
            "SELECT id FROM catalog_models WHERE id = $1",
            default_uuid,
        )
        if default_row is None:
            raise ValueError("default_model_id is not a known model.")
        if fallback_uuid is not None:
            fallback_row = await self._run(
                "fetchrow",
                "SELECT id FROM catalog_models WHERE id = $1",
                fallback_uuid,
            )
            if fallback_row is None:
                raise ValueError("fallback_model_id is not a known model.")
        await self._run(
            "execute",
            "INSERT INTO catalog_tenant_models (tenant_id, default_model_id, fallback_model_id) "
            "VALUES ($1, $2, $3) "
            "ON CONFLICT (tenant_id) DO UPDATE SET default_model_id = EXCLUDED.default_model_id, "
            "fallback_model_id = EXCLUDED.fallback_model_id, updated_at = now()",
            tenant_id,
            default_uuid,
            fallback_uuid,
        )
        row = await self._run(
            "fetchrow",
            "SELECT t.tenant_id, t.default_model_id, t.fallback_model_id, t.updated_at, "
            "d.slug AS default_slug, d.label AS default_label, "
            "f.slug AS fallback_slug, f.label AS fallback_label "
            "FROM catalog_tenant_models t "
            "JOIN catalog_models d ON d.id = t.default_model_id "
            "LEFT JOIN catalog_models f ON f.id = t.fallback_model_id "
            "WHERE t.tenant_id = $1",
            tenant_id,
        )
        return _public_tenant(row)

    async def list_tenant_agent_models(self, tenant_id: str) -> list[dict[str, Any]]:
        tenant_id = (tenant_id or "").strip()
        if not tenant_id:
            return []
        rows = await self._run(
            "fetch",
            "SELECT t.tenant_id, t.agent_slug, t.model_id, t.default_model_id, t.fallback_model_id, t.updated_at, "
            "m.slug AS model_slug, m.label AS model_label, "
            "d.slug AS default_slug, d.label AS default_label, "
            "f.slug AS fallback_slug, f.label AS fallback_label "
            "FROM catalog_tenant_agent_models t "
            "LEFT JOIN catalog_models m ON m.id = t.model_id "
            "LEFT JOIN catalog_models d ON d.id = t.default_model_id "
            "LEFT JOIN catalog_models f ON f.id = t.fallback_model_id "
            "WHERE t.tenant_id = $1 "
            "ORDER BY t.agent_slug ASC",
            tenant_id,
        )
        return [_public_tenant_agent(row) for row in rows]

    async def get_tenant_agent_model(self, tenant_id: str, agent_slug: str) -> Optional[dict[str, Any]]:
        tenant_id = (tenant_id or "").strip()
        agent_slug = (agent_slug or "").strip()
        if not tenant_id or not agent_slug:
            return None
        row = await self._run(
            "fetchrow",
            "SELECT t.tenant_id, t.agent_slug, t.model_id, t.default_model_id, t.fallback_model_id, t.updated_at, "
            "m.slug AS model_slug, m.label AS model_label, "
            "d.slug AS default_slug, d.label AS default_label, "
            "f.slug AS fallback_slug, f.label AS fallback_label "
            "FROM catalog_tenant_agent_models t "
            "LEFT JOIN catalog_models m ON m.id = t.model_id "
            "LEFT JOIN catalog_models d ON d.id = t.default_model_id "
            "LEFT JOIN catalog_models f ON f.id = t.fallback_model_id "
            "WHERE t.tenant_id = $1 AND t.agent_slug = $2",
            tenant_id,
            agent_slug,
        )
        return _public_tenant_agent(row) if row else None

    async def upsert_tenant_agent_model(
        self,
        *,
        tenant_id: str,
        agent_slug: str,
        model_id: Optional[str],
        default_model_id: Optional[str] = None,
        fallback_model_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        tenant_id = (tenant_id or "").strip()
        agent_slug = normalize_slug(agent_slug)
        if not tenant_id:
            raise ValueError("tenant_id is required.")
        agent_row = await self.get_agent_by_slug(agent_slug)
        if agent_row is None:
            raise ValueError(f"Unknown agent slug: {agent_slug}")
        if not model_id and not default_model_id and not fallback_model_id:
            await self._run(
                "execute",
                "DELETE FROM catalog_tenant_agent_models WHERE tenant_id = $1 AND agent_slug = $2",
                tenant_id,
                agent_slug,
            )
            return None
        model_uuid = uuid.UUID(str(model_id)) if model_id else None
        default_uuid = uuid.UUID(str(default_model_id)) if default_model_id else None
        fallback_uuid = uuid.UUID(str(fallback_model_id)) if fallback_model_id else None
        for label, mid in (
            ("model_id", model_uuid),
            ("default_model_id", default_uuid),
            ("fallback_model_id", fallback_uuid),
        ):
            if mid is None:
                continue
            row = await self._run(
                "fetchrow",
                "SELECT id FROM catalog_models WHERE id = $1",
                mid,
            )
            if row is None:
                raise ValueError(f"{label} is not a known model.")
        await self._run(
            "execute",
            "INSERT INTO catalog_tenant_agent_models "
            "(tenant_id, agent_slug, model_id, default_model_id, fallback_model_id) "
            "VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (tenant_id, agent_slug) DO UPDATE SET "
            "model_id = EXCLUDED.model_id, "
            "default_model_id = EXCLUDED.default_model_id, "
            "fallback_model_id = EXCLUDED.fallback_model_id, "
            "updated_at = now()",
            tenant_id,
            agent_slug,
            model_uuid,
            default_uuid,
            fallback_uuid,
        )
        return await self.get_tenant_agent_model(tenant_id, agent_slug)

    async def resolve_tenant_agent_llm_slugs(
        self,
        tenant_id: str,
        agent_slug: str,
    ) -> dict[str, Optional[str]]:
        """Per-agent mapping first, then tenant-wide default/fallback."""
        per_agent = await self.get_tenant_agent_model(tenant_id, agent_slug)
        tenant = await self.get_tenant_models(tenant_id)
        if per_agent:
            primary = (
                per_agent.get("model_slug")
                or per_agent.get("default_slug")
                or (tenant.get("default_slug") if tenant else None)
            )
            fallback = (
                per_agent.get("fallback_slug")
                or (tenant.get("fallback_slug") if tenant else None)
            )
            return {"default_slug": primary, "fallback_slug": fallback}
        if tenant:
            return {
                "default_slug": tenant.get("default_slug"),
                "fallback_slug": tenant.get("fallback_slug"),
            }
        return {"default_slug": None, "fallback_slug": None}


def _public_tenant_agent(row: Any) -> dict[str, Any]:
    model_id = _row_get(row, "model_id")
    default_id = _row_get(row, "default_model_id")
    fallback_id = _row_get(row, "fallback_model_id")
    return {
        "tenant_id": _row_get(row, "tenant_id"),
        "agent_slug": _row_get(row, "agent_slug"),
        "model_id": str(model_id) if model_id else None,
        "default_model_id": str(default_id) if default_id else None,
        "fallback_model_id": str(fallback_id) if fallback_id else None,
        "model_slug": _row_get(row, "model_slug"),
        "model_label": _row_get(row, "model_label"),
        "default_slug": _row_get(row, "default_slug"),
        "default_label": _row_get(row, "default_label"),
        "fallback_slug": _row_get(row, "fallback_slug"),
        "fallback_label": _row_get(row, "fallback_label"),
        "updated_at": _fmt_dt(_row_get(row, "updated_at")),
    }


def _public_tenant(row: Any) -> dict[str, Any]:
    fallback_id = _row_get(row, "fallback_model_id")
    return {
        "tenant_id": _row_get(row, "tenant_id"),
        "default_model_id": str(_row_get(row, "default_model_id")),
        "fallback_model_id": str(fallback_id) if fallback_id else None,
        "default_slug": _row_get(row, "default_slug"),
        "default_label": _row_get(row, "default_label"),
        "fallback_slug": _row_get(row, "fallback_slug"),
        "fallback_label": _row_get(row, "fallback_label"),
        "updated_at": _fmt_dt(_row_get(row, "updated_at")),
    }
