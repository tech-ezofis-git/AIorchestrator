"""Load tenant repository names and field/column labels. Fail closed."""
from __future__ import annotations

import logging
from typing import Any

from app.dashboard.ids import normalize_guid

logger = logging.getLogger("orchestrator.document_intelligent.store")

_MAX_REPOS = 80
_MAX_FIELDS = 24


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


class DocumentIntelligentStore:
    def __init__(self, tenant_pools: Any = None, catalog_store: Any = None) -> None:
        self._tenant_pools = tenant_pools
        self._catalog_store = catalog_store

    async def _connect(self, tenant_id: str) -> Any:
        if self._tenant_pools is None:
            raise ValueError("Document Intelligent tenant pools are not configured.")
        acquire = getattr(self._tenant_pools, "acquire_for_global_search", None)
        if acquire is not None:
            return await acquire(tenant_id, self._catalog_store)
        return await self._tenant_pools.acquire(tenant_id)

    async def list_catalog(self, tenant_id: str) -> list[dict[str, Any]]:
        tenant_id = (tenant_id or "").strip()
        if not tenant_id:
            raise ValueError("payload.tenant_id is required for intent=document_intelligent.")
        try:
            pool = await self._connect(tenant_id)
            repos = await pool.fetch(
                """
                SELECT "Id"::text AS id, "Name" AS name
                FROM repository."Repositories"
                WHERE COALESCE("IsDeleted", false) = false
                ORDER BY "Name"
                LIMIT $1
                """,
                _MAX_REPOS,
            )
        except ValueError:
            raise
        except Exception as exc:
            logger.warning(
                "document_intelligent_repo_list_failed",
                extra={"error_type": type(exc).__name__},
            )
            raise ValueError("Could not load repositories for this tenant.") from exc

        catalog: list[dict[str, Any]] = []
        for row in repos or []:
            rid = _as_str(_row_get(row, "id"))
            name = _as_str(_row_get(row, "name"))
            if not rid:
                continue
            catalog.append(
                {
                    "repository_id": normalize_guid(rid) or rid,
                    "repository_name": name or rid,
                    "fields": [],
                }
            )
        if not catalog:
            raise ValueError("No repositories found for this tenant.")

        fields_by_repo = await self._load_fields(pool, [row["repository_id"] for row in catalog])
        for item in catalog:
            item["fields"] = fields_by_repo.get(item["repository_id"].lower(), [])
        return catalog

    async def _load_fields(self, pool: Any, repository_ids: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        queries = (
            """
            SELECT "RepositoryId"::text AS repository_id, "Name" AS name, "ColumnName" AS column_name
            FROM repository."Fields"
            """,
            """
            SELECT "wRepositoryId"::text AS repository_id, "name" AS name, "columnName" AS column_name
            FROM dbo.wrepositoryfield
            WHERE COALESCE("isDeleted", 0) = 0
            """,
        )
        rows = None
        for sql in queries:
            try:
                rows = await pool.fetch(sql)
                break
            except Exception:
                continue
        if not rows:
            return out
        allowed = {(normalize_guid(rid) or rid).lower() for rid in repository_ids}
        for row in rows:
            rid = (normalize_guid(_as_str(_row_get(row, "repository_id"))) or _as_str(_row_get(row, "repository_id"))).lower()
            if rid not in allowed:
                continue
            labels = []
            for key in ("name", "column_name"):
                label = _as_str(_row_get(row, key))
                if label and label not in labels:
                    labels.append(label)
            if not labels:
                continue
            bucket = out.setdefault(rid, [])
            for label in labels:
                if label not in bucket and len(bucket) < _MAX_FIELDS:
                    bucket.append(label)
        return out
