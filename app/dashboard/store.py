"""Resolve tenant items table (via repository or workflow) and read rows."""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger("orchestrator.dashboard")

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_GUID_HEAD = re.compile(r"^[0-9a-fA-F]{8}")


class DashboardStoreUnavailableError(Exception):
    """Raised when dashboard SQL against the tenant DB fails."""


def quote_ident(name: str) -> str:
    raw = (name or "").strip()
    if not _IDENT.fullmatch(raw):
        raise ValueError(f"Invalid SQL identifier {name!r}.")
    return f'"{raw}"'


def split_table(raw: str, repository_id: str) -> tuple[str, str]:
    text = (raw or "").strip()
    if "." in text:
        schema, table = text.split(".", 1)
        schema, table = schema.strip(), table.strip()
    else:
        schema, table = "repository", text
    if not table:
        head = (repository_id or "").replace("-", "")[:8].lower()
        if not _GUID_HEAD.match(head):
            raise ValueError("Could not resolve items table name.")
        table = f"items_{head}"
    return schema or "repository", table


def _row_get(row: Any, *keys: str) -> Any:
    if row is None:
        return None
    mapping: dict[str, Any]
    if isinstance(row, dict):
        mapping = row
    else:
        try:
            mapping = dict(row)
        except Exception:
            mapping = {}
            for key in keys:
                try:
                    return row[key]
                except Exception:
                    continue
            return None
    lower = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        if key in mapping:
            return mapping[key]
        if key.lower() in lower:
            return lower[key.lower()]
    return None


class DashboardStore:
    def __init__(self, *, tenant_pools: Any = None, fallback_pool: Any = None):
        self._tenant_pools = tenant_pools
        self._fallback = fallback_pool

    async def _db(self, tenant_id: str) -> Any:
        try:
            if self._tenant_pools is not None:
                return await self._tenant_pools.acquire(tenant_id)
            return self._fallback
        except DashboardStoreUnavailableError:
            raise
        except Exception as exc:
            logger.warning(
                "dashboard_tenant_db_acquire_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            raise DashboardStoreUnavailableError("Dashboard store is currently unavailable.") from exc

    async def resolve_target(
        self,
        *,
        tenant_id: str,
        repository_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
    ) -> dict[str, Any]:
        tenant_id = (tenant_id or "").strip()
        repository_id = (repository_id or "").strip() or None
        workflow_id = (workflow_id or "").strip() or None
        if not tenant_id:
            raise ValueError("payload.tenant_id is required for intent=dashboard.")
        if not repository_id and not workflow_id:
            raise ValueError("payload.repository_id or payload.workflow_id is required for intent=dashboard.")

        db = await self._db(tenant_id)
        form_id = None
        workflow_name = None
        try:
            if not repository_id and workflow_id:
                wf = await db.fetchrow(
                    """
                    -- dashboard:workflow
                    SELECT "Id" AS id, "Name" AS name,
                           "RepositoryId" AS repository_id, "FormId" AS form_id
                    FROM workflow."Workflows"
                    WHERE "Id"::text = $1 AND COALESCE("IsDeleted", false) = false
                    LIMIT 1
                    """,
                    workflow_id,
                )
                if wf is None:
                    raise ValueError(f"Workflow '{workflow_id}' was not found.")
                repository_id = str(_row_get(wf, "repository_id") or "").strip() or None
                form_id = str(_row_get(wf, "form_id") or "").strip() or None
                workflow_name = str(_row_get(wf, "name") or "").strip() or None
                if not repository_id:
                    raise ValueError(f"Workflow '{workflow_id}' has no RepositoryId.")

            repo = await db.fetchrow(
                """
                -- dashboard:repo
                SELECT "Id" AS id, "Name" AS name, "ItemsTableName" AS items_table_name
                FROM repository."Repositories"
                WHERE "Id"::text = $1 AND COALESCE("IsDeleted", false) = false
                LIMIT 1
                """,
                repository_id,
            )
            if repo is None:
                raise ValueError(f"Repository '{repository_id}' was not found.")
            schema, table = split_table(
                str(_row_get(repo, "items_table_name") or ""),
                str(_row_get(repo, "id") or repository_id),
            )
            return {
                "tenant_id": tenant_id,
                "repository_id": str(_row_get(repo, "id") or repository_id),
                "repository_name": str(_row_get(repo, "name") or "").strip() or None,
                "workflow_id": workflow_id,
                "workflow_name": workflow_name,
                "form_id": form_id,
                "schema": schema,
                "table": table,
                "qualified_table": f"{schema}.{table}",
            }
        except (ValueError, DashboardStoreUnavailableError):
            raise
        except Exception as exc:
            logger.warning(
                "dashboard_resolve_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            raise DashboardStoreUnavailableError("Dashboard store is currently unavailable.") from exc

    async def list_columns(self, *, tenant_id: str, schema: str, table: str) -> list[str]:
        db = await self._db(tenant_id)
        try:
            rows = await db.fetch(
                """
                -- dashboard:columns
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = $2
                ORDER BY ordinal_position
                """,
                schema,
                table,
            )
        except Exception as exc:
            logger.warning(
                "dashboard_columns_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            raise DashboardStoreUnavailableError("Dashboard store is currently unavailable.") from exc
        names: list[str] = []
        for row in rows:
            name = _row_get(row, "column_name")
            if name:
                names.append(str(name))
        return names

    async def fetch_rows(self, *, tenant_id: str, schema: str, table: str, columns: list[str]) -> list[dict[str, Any]]:
        if not columns:
            return []
        db = await self._db(tenant_id)
        quoted_cols = ", ".join(quote_ident(name) for name in columns)
        qualified = f"{quote_ident(schema)}.{quote_ident(table)}"
        deleted = next((name for name in columns if name.lower() == "is_deleted"), None)
        where = f"WHERE COALESCE({quote_ident(deleted)}, false) = false" if deleted else ""
        sql = f"-- dashboard:items\nSELECT {quoted_cols} FROM {qualified} {where}"
        try:
            rows = await db.fetch(sql)
        except Exception as exc:
            logger.warning(
                "dashboard_items_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            raise DashboardStoreUnavailableError("Dashboard store is currently unavailable.") from exc
        out: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, dict):
                out.append(row)
            else:
                try:
                    out.append(dict(row))
                except Exception:
                    out.append({col: _row_get(row, col) for col in columns})
        return out

    async def fetch_extract_artifacts(
        self,
        *,
        tenant_id: str,
        item_keys: list[str],
    ) -> dict[str, dict[str, Any]]:
        keys = [key.strip() for key in item_keys if key and str(key).strip()]
        if not keys:
            return {}
        db = await self._db(tenant_id)
        try:
            rows = await db.fetch(
                """
                -- dashboard:extracts
                SELECT DISTINCT ON (item_key, skill_id)
                       item_key::text AS item_key,
                       skill_id,
                       result_json
                FROM ap_skill_artifacts
                WHERE tenant_id::text = $1
                  AND skill_id = ANY($2::text[])
                  AND item_key::text = ANY($3::text[])
                ORDER BY item_key, skill_id, created_at DESC
                """,
                tenant_id,
                ["extract_invoice", "po_match"],
                keys,
            )
        except Exception as exc:
            logger.warning(
                "dashboard_extracts_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
            )
            return {}
        by_item: dict[str, dict[str, Any]] = {}
        for row in rows:
            item_key = str(_row_get(row, "item_key") or "").strip()
            skill_id = str(_row_get(row, "skill_id") or "").strip()
            payload = _row_get(row, "result_json")
            if not item_key or not skill_id:
                continue
            by_item.setdefault(item_key, {})[skill_id] = payload
        return by_item
