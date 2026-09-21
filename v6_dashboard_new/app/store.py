"""Resolve tenant items table (via repository or workflow) and read rows from SQL Server."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from app.ids import guid_prefix, normalize_guid
from app.mock_data import (
    get_sample_columns,
    get_sample_repositories,
    get_sample_rows,
    get_sample_target,
    get_sample_workflows,
)
from app.sql import AzureSqlConnectionError, fetch_all, fetch_one, get_tenant_connection

logger = logging.getLogger("v6_dashboard.store")

_UNSAFE_IDENT = re.compile(r"[\x00-\x1f;]|--|/\*|\*/")


class DashboardStoreUnavailableError(Exception):
    """Raised when dashboard SQL against the tenant DB fails."""


def quote_ident(name: str) -> str:
    """Bracket-quote a SQL Server identifier (ASCII or Unicode column names)."""
    raw = (name or "").strip()
    if not raw or len(raw) > 128:
        raise ValueError(f"Invalid SQL identifier {name!r}.")
    if _UNSAFE_IDENT.search(raw):
        raise ValueError(f"Invalid SQL identifier {name!r}.")
    return "[" + raw.replace("]", "]]") + "]"


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
        table = f"Items_{head}"
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


class DashboardStore:
    def resolve_target(
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
            connection = get_tenant_connection(tenant_id)
        except AzureSqlConnectionError as exc:
            logger.info("SQL Server unavailable, using sample target for tenant=%s: %s", tenant_id, exc)
            return get_sample_target(tenant_id=tenant_id, repository_id=repository_id, workflow_id=workflow_id)

        form_id = None
        workflow_name = None
        try:
            if not repository_id and workflow_id:
                wf = fetch_one(
                    connection,
                    """
                    SELECT Id, Name, RepositoryId, FormId
                    FROM workflow.Workflows
                    WHERE CAST(Id AS nvarchar(36)) = ?
                      AND ISNULL(IsDeleted, 0) = 0
                    """,
                    [normalize_guid(workflow_id) or workflow_id],
                )
                if wf is None:
                    return get_sample_target(tenant_id=tenant_id, repository_id=repository_id, workflow_id=workflow_id)
                repository_id = _as_str(_row_get(wf, "RepositoryId", "repository_id")) or None
                form_id = _as_str(_row_get(wf, "FormId", "form_id")) or None
                workflow_name = _as_str(_row_get(wf, "Name", "name")) or None
                if not repository_id:
                    return get_sample_target(tenant_id=tenant_id, repository_id=repository_id, workflow_id=workflow_id)

            repo = fetch_one(
                connection,
                """
                SELECT Id, Name, ItemsTableName
                FROM repository.Repositories
                WHERE CAST(Id AS nvarchar(36)) = ?
                  AND ISNULL(IsDeleted, 0) = 0
                """,
                [normalize_guid(repository_id) or repository_id],
            )
            if repo is None:
                return get_sample_target(tenant_id=tenant_id, repository_id=repository_id, workflow_id=workflow_id)
            schema, table = split_table(
                _as_str(_row_get(repo, "ItemsTableName", "items_table_name")),
                _as_str(_row_get(repo, "Id", "id")) or repository_id or "",
            )
            return {
                "tenant_id": tenant_id,
                "repository_id": _as_str(_row_get(repo, "Id", "id")) or repository_id,
                "repository_name": _as_str(_row_get(repo, "Name", "name")) or None,
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
        finally:
            try:
                connection.close()
            except Exception:
                pass

    def list_columns(self, *, tenant_id: str, schema: str, table: str) -> list[str]:
        try:
            connection = get_tenant_connection(tenant_id)
        except AzureSqlConnectionError:
            return get_sample_columns(table)
        try:
            rows = fetch_all(
                connection,
                """
                SELECT COLUMN_NAME
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
                ORDER BY ORDINAL_POSITION
                """,
                [schema, table],
            )
            names: list[str] = []
            for row in rows:
                name = _row_get(row, "COLUMN_NAME", "column_name")
                if name:
                    names.append(str(name))
            if names:
                return names
        except Exception as exc:
            logger.warning("dashboard_columns_failed: %s", exc)
        finally:
            try:
                connection.close()
            except Exception:
                pass
        return get_sample_columns(table)

    def fetch_rows(
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
        try:
            connection = get_tenant_connection(tenant_id)
        except AzureSqlConnectionError:
            return get_sample_rows(table, limit=limit)
        try:
            quoted_cols = ", ".join(quote_ident(name) for name in columns)
            qualified = f"{quote_ident(schema)}.{quote_ident(table)}"
            deleted = next((name for name in columns if name.lower() in {"is_deleted", "isdeleted"}), None)
            where = f"WHERE ISNULL({quote_ident(deleted)}, 0) = 0" if deleted else ""
            top = f"TOP ({max(1, int(limit))}) " if limit is not None else ""
            sql = f"SELECT {top}{quoted_cols} FROM {qualified} {where}"
            rows = fetch_all(connection, sql)
            if rows:
                return [dict(row) for row in rows]
        except Exception as exc:
            logger.warning("dashboard_items_failed: %s", exc)
        finally:
            try:
                connection.close()
            except Exception:
                pass
        return get_sample_rows(table, limit=limit)

    def fetch_extract_artifacts(
        self,
        *,
        tenant_id: str,
        item_keys: list[str],
    ) -> dict[str, dict[str, Any]]:
        keys = [key.strip() for key in item_keys if key and str(key).strip()]
        if not keys:
            return {}
        placeholders = ", ".join("?" for _ in keys)
        sql = f"""
            SELECT item_key, skill_id, result_json
            FROM (
                SELECT
                    CAST(item_key AS nvarchar(100)) AS item_key,
                    skill_id,
                    result_json,
                    ROW_NUMBER() OVER (
                        PARTITION BY item_key, skill_id
                        ORDER BY created_at DESC
                    ) AS rn
                FROM ap_skill_artifacts
                WHERE CAST(tenant_id AS nvarchar(36)) = ?
                  AND skill_id IN (?, ?)
                  AND CAST(item_key AS nvarchar(100)) IN ({placeholders})
            ) ranked
            WHERE rn = 1
        """
        params: list[Any] = [
            normalize_guid(tenant_id) or tenant_id,
            "extract_invoice",
            "po_match",
            *keys,
        ]
        try:
            connection = get_tenant_connection(tenant_id)
        except AzureSqlConnectionError:
            return {}
        try:
            rows = fetch_all(connection, sql, params)
        except Exception as exc:
            logger.warning("dashboard_extracts_failed: %s", exc)
            return {}
        finally:
            try:
                connection.close()
            except Exception:
                pass
        by_item: dict[str, dict[str, Any]] = {}
        for row in rows:
            item_key = _as_str(_row_get(row, "item_key"))
            skill_id = _as_str(_row_get(row, "skill_id"))
            payload = _row_get(row, "result_json")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = None
            if not item_key or not skill_id:
                continue
            by_item.setdefault(item_key, {})[skill_id] = payload
        return by_item

    def list_targets(self, *, tenant_id: str) -> dict[str, Any]:
        tenant_id = (tenant_id or "").strip()
        if not tenant_id:
            raise ValueError("tenant_id is required.")
        try:
            connection = get_tenant_connection(tenant_id)
            try:
                repos = fetch_all(
                    connection,
                    """
                    SELECT Id, Name, ItemsTableName
                    FROM repository.Repositories
                    WHERE ISNULL(IsDeleted, 0) = 0
                    ORDER BY Name
                    """,
                )
                workflows = fetch_all(
                    connection,
                    """
                    SELECT Id, Name, RepositoryId
                    FROM workflow.Workflows
                    WHERE ISNULL(IsDeleted, 0) = 0
                    ORDER BY Name
                    """,
                )
                repo_list = [
                    {
                        "id": _as_str(_row_get(row, "Id", "id")),
                        "name": _as_str(_row_get(row, "Name", "name")),
                        "items_table": _as_str(_row_get(row, "ItemsTableName", "items_table_name")),
                    }
                    for row in repos
                ]
                wf_list = [
                    {
                        "id": _as_str(_row_get(row, "Id", "id")),
                        "name": _as_str(_row_get(row, "Name", "name")),
                        "repository_id": _as_str(_row_get(row, "RepositoryId", "repository_id")),
                    }
                    for row in workflows
                ]
                if repo_list or wf_list:
                    return {
                        "tenant_id": tenant_id,
                        "repositories": repo_list,
                        "workflows": wf_list,
                    }
            finally:
                try:
                    connection.close()
                except Exception:
                    pass
        except Exception as exc:
            logger.info("SQL Server unavailable for list_targets, returning sample targets: %s", exc)

        return {
            "tenant_id": tenant_id,
            "repositories": get_sample_repositories(tenant_id),
            "workflows": get_sample_workflows(tenant_id),
        }
