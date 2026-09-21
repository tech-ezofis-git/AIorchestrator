"""Resolve tenant items table (repository or workflow) and read rows from Postgres.

Always caps item reads at ROW_LIMIT (50) so large tenant tables stay bounded.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from app.dashboard.ids import guid_prefix, normalize_guid
from app.dashboard.mock_data import (
    get_sample_columns,
    get_sample_repositories,
    get_sample_rows,
    get_sample_target,
    get_sample_workflows,
)
from app.data_import.ident import quote_ident

logger = logging.getLogger("orchestrator.dashboard.store")

ROW_LIMIT = 50
_UNSAFE_IDENT = re.compile(r"[\x00-\x1f;]|--|/\*|\*/")


class DashboardStoreUnavailableError(Exception):
    """Raised when dashboard SQL against the tenant DB fails."""


def split_table(raw: str, repository_id: str) -> tuple[str, str]:
    text = (raw or "").strip()
    if "." in text:
        schema, table = text.split(".", 1)
        schema, table = schema.strip(), table.strip()
    else:
        schema, table = "repository", text
    if not table:
        head = guid_prefix(repository_id).lower()
        if not head:
            raise ValueError("Could not resolve items table name.")
        table = f"items_{head}"
    return schema or "repository", table


def _row_get(row: Any, *keys: str) -> Any:
    if row is None:
        return None
    if not isinstance(row, dict):
        try:
            mapping = dict(row)
        except Exception:
            return None
    else:
        mapping = row
    lower = {str(key).lower(): value for key, value in mapping.items()}
    for key in keys:
        if key in mapping:
            return mapping[key]
        if key.lower() in lower:
            return lower[key.lower()]
    return None


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_ident(name: str) -> str:
    raw = (name or "").strip()
    if not raw or len(raw) > 128:
        raise ValueError(f"Invalid SQL identifier {name!r}.")
    if _UNSAFE_IDENT.search(raw):
        raise ValueError(f"Invalid SQL identifier {name!r}.")
    return quote_ident(raw)


def _clamp_limit(limit: Optional[int]) -> int:
    if limit is None:
        return ROW_LIMIT
    return max(1, min(int(limit), ROW_LIMIT))


class DashboardStore:
    def __init__(self, tenant_pools: Any = None, catalog_store: Any = None) -> None:
        self._tenant_pools = tenant_pools
        self._catalog_store = catalog_store

    async def _connect(self, tenant_id: str) -> Any:
        if self._tenant_pools is None:
            raise DashboardStoreUnavailableError("Dashboard tenant pools are not configured.")
        acquire = getattr(self._tenant_pools, "acquire_for_global_search", None)
        if acquire is not None:
            return await acquire(tenant_id, self._catalog_store)
        return await self._tenant_pools.acquire(tenant_id)

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

        try:
            pool = await self._connect(tenant_id)
        except Exception as exc:
            logger.info("Postgres unavailable, using sample target for tenant=%s: %s", tenant_id, exc)
            return get_sample_target(tenant_id=tenant_id, repository_id=repository_id, workflow_id=workflow_id)

        form_id = None
        workflow_name = None
        try:
            if not repository_id and workflow_id:
                wf = await pool.fetchrow(
                    """
                    SELECT "Id"::text AS id, "Name" AS name, "RepositoryId"::text AS repository_id, "FormId"::text AS form_id
                    FROM workflow."Workflows"
                    WHERE "Id"::text = $1
                      AND COALESCE("IsDeleted", false) = false
                    """,
                    normalize_guid(workflow_id) or workflow_id,
                )
                if wf is None:
                    return get_sample_target(tenant_id=tenant_id, repository_id=repository_id, workflow_id=workflow_id)
                mapping = dict(wf)
                repository_id = _as_str(_row_get(mapping, "repository_id", "RepositoryId")) or None
                form_id = _as_str(_row_get(mapping, "form_id", "FormId")) or None
                workflow_name = _as_str(_row_get(mapping, "name", "Name")) or None
                if not repository_id:
                    return get_sample_target(tenant_id=tenant_id, repository_id=repository_id, workflow_id=workflow_id)

            repo = await pool.fetchrow(
                """
                SELECT "Id"::text AS id, "Name" AS name, "ItemsTableName" AS items_table_name
                FROM repository."Repositories"
                WHERE "Id"::text = $1
                  AND COALESCE("IsDeleted", false) = false
                """,
                normalize_guid(repository_id) or repository_id,
            )
            if repo is None:
                return get_sample_target(tenant_id=tenant_id, repository_id=repository_id, workflow_id=workflow_id)
            mapping = dict(repo)
            schema, table = split_table(
                _as_str(_row_get(mapping, "items_table_name", "ItemsTableName")),
                _as_str(_row_get(mapping, "id", "Id")) or repository_id or "",
            )
            return {
                "tenant_id": tenant_id,
                "repository_id": _as_str(_row_get(mapping, "id", "Id")) or repository_id,
                "repository_name": _as_str(_row_get(mapping, "name", "Name")) or None,
                "workflow_id": workflow_id,
                "workflow_name": workflow_name,
                "form_id": form_id,
                "schema": schema,
                "table": table,
                "qualified_table": f"{schema}.{table}",
            }
        except Exception as exc:
            logger.info("dashboard_resolve_failed, using sample target: %s", exc)
            return get_sample_target(tenant_id=tenant_id, repository_id=repository_id, workflow_id=workflow_id)

    async def list_columns(self, *, tenant_id: str, schema: str, table: str) -> list[str]:
        try:
            pool = await self._connect(tenant_id)
        except Exception:
            return get_sample_columns(table)
        try:
            rows = await pool.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE lower(table_schema) = lower($1) AND lower(table_name) = lower($2)
                ORDER BY ordinal_position
                """,
                schema,
                table,
            )
            names = [str(_row_get(dict(row), "column_name")) for row in rows if _row_get(dict(row), "column_name")]
            if names:
                return names
        except Exception as exc:
            logger.warning("dashboard_columns_failed: %s", exc)
        return get_sample_columns(table)

    async def fetch_rows(
        self,
        *,
        tenant_id: str,
        schema: str,
        table: str,
        columns: list[str],
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        if not columns:
            return []
        cap = _clamp_limit(limit)
        try:
            pool = await self._connect(tenant_id)
        except Exception:
            return get_sample_rows(table, limit=cap)[:cap]
        try:
            quoted_cols = ", ".join(_safe_ident(name) for name in columns)
            qualified = f"{_safe_ident(schema)}.{_safe_ident(table)}"
            deleted = next((name for name in columns if name.lower() in {"is_deleted", "isdeleted"}), None)
            where = f"WHERE COALESCE(({_safe_ident(deleted)})::int, 0) = 0" if deleted else ""
            sql = f"SELECT {quoted_cols} FROM {qualified} {where} LIMIT {cap}"
            rows = await pool.fetch(sql)
            if rows:
                return [dict(row) for row in rows][:cap]
        except Exception as exc:
            logger.warning("dashboard_items_failed: %s", exc)
        sample = get_sample_rows(table, limit=cap)
        return sample[:cap]

    async def fetch_extract_artifacts(
        self,
        *,
        tenant_id: str,
        item_keys: list[str],
    ) -> dict[str, dict[str, Any]]:
        keys = [key.strip() for key in item_keys if key and str(key).strip()][:ROW_LIMIT]
        if not keys:
            return {}
        try:
            pool = await self._connect(tenant_id)
        except Exception:
            return {}
        sql = """
            SELECT item_key::text AS item_key, skill_id, result_json
            FROM (
                SELECT
                    item_key::text AS item_key,
                    skill_id,
                    result_json,
                    ROW_NUMBER() OVER (
                        PARTITION BY item_key, skill_id
                        ORDER BY created_at DESC
                    ) AS rn
                FROM ap_skill_artifacts
                WHERE tenant_id::text = $1
                  AND skill_id = ANY($2::text[])
                  AND item_key::text = ANY($3::text[])
            ) ranked
            WHERE rn = 1
        """
        try:
            rows = await pool.fetch(
                sql,
                normalize_guid(tenant_id) or tenant_id,
                ["extract_invoice", "po_match"],
                keys,
            )
        except Exception as exc:
            logger.warning("dashboard_extracts_failed: %s", exc)
            return {}
        by_item: dict[str, dict[str, Any]] = {}
        for row in rows:
            mapping = dict(row)
            item_key = _as_str(_row_get(mapping, "item_key"))
            skill_id = _as_str(_row_get(mapping, "skill_id"))
            payload = _row_get(mapping, "result_json")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = None
            if not item_key or not skill_id:
                continue
            by_item.setdefault(item_key, {})[skill_id] = payload
        return by_item

    async def list_targets(self, *, tenant_id: str) -> dict[str, Any]:
        tenant_id = (tenant_id or "").strip()
        if not tenant_id:
            raise ValueError("tenant_id is required.")
        try:
            pool = await self._connect(tenant_id)
            repos = await pool.fetch(
                """
                SELECT "Id"::text AS id, "Name" AS name, "ItemsTableName" AS items_table_name
                FROM repository."Repositories"
                WHERE COALESCE("IsDeleted", false) = false
                ORDER BY "Name"
                """
            )
            workflows = await pool.fetch(
                """
                SELECT "Id"::text AS id, "Name" AS name, "RepositoryId"::text AS repository_id
                FROM workflow."Workflows"
                WHERE COALESCE("IsDeleted", false) = false
                ORDER BY "Name"
                """
            )
            repo_list = [
                {
                    "id": _as_str(_row_get(dict(row), "id")),
                    "name": _as_str(_row_get(dict(row), "name")),
                    "items_table": _as_str(_row_get(dict(row), "items_table_name")),
                }
                for row in repos
            ]
            wf_list = [
                {
                    "id": _as_str(_row_get(dict(row), "id")),
                    "name": _as_str(_row_get(dict(row), "name")),
                    "repository_id": _as_str(_row_get(dict(row), "repository_id")),
                }
                for row in workflows
            ]
            if repo_list or wf_list:
                return {
                    "tenant_id": tenant_id,
                    "repositories": repo_list,
                    "workflows": wf_list,
                }
        except Exception as exc:
            logger.info("Postgres unavailable for list_targets, returning sample targets: %s", exc)

        return {
            "tenant_id": tenant_id,
            "repositories": get_sample_repositories(tenant_id),
            "workflows": get_sample_workflows(tenant_id),
        }
