"""information_schema helpers for tenant EZOFIS tables (AP-store style)."""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.data_import.ident import quote_ident

logger = logging.getLogger("orchestrator.global_search")

_SKIP_SCHEMAS = ("pg_catalog", "information_schema")


def row_get(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except Exception:
        return None


def columns_by_lower(col_rows: list[Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in col_rows or []:
        name = str(row_get(row, "column_name") or "").strip()
        if name:
            out[name.lower()] = name
    return out


_SKIP_SEARCH_COLS = frozenset(
    {
        "id",
        "itemid",
        "item_id",
        "ifileid",
        "ifile_id",
        "repositoryitemid",
        "witemid",
        "wrepositoryid",
        "wworkflowid",
        "workflowid",
        "guid",
        "createdat",
        "created_at",
        "createdby",
        "created_by",
        "modifiedat",
        "modified_at",
        "modifiedby",
        "modified_by",
        "isdeleted",
        "is_deleted",
        "embedding",
        "password",
        "token",
    }
)
_TEXT_TYPES = frozenset(
    {
        "character varying",
        "varchar",
        "text",
        "character",
        "citext",
        "name",
        "json",
        "jsonb",
    }
)


def pick_id_column(by_lower: dict[str, str]) -> Optional[str]:
    for key in (
        "itemid",
        "item_id",
        "ifileid",
        "ifile_id",
        "repositoryitemid",
        "witemid",
        "id",
        "wrepositoryid",
        "wworkflowid",
        "workflowid",
    ):
        if key in by_lower:
            return by_lower[key]
    return None


def pick_name_column(by_lower: dict[str, str]) -> Optional[str]:
    for key in ("name", "title", "ifilename", "filename", "file_name", "displayname"):
        if key in by_lower:
            return by_lower[key]
    return None


def pick_text_columns(
    by_lower: dict[str, str],
    *,
    extra: tuple[str, ...] = (),
    types_by_lower: Optional[dict[str, str]] = None,
) -> list[str]:
    preferred = (
        "name",
        "title",
        "description",
        "code",
        "ifilename",
        "filename",
        "file_name",
        "displayname",
        "ponumber",
        "po_number",
        "pono",
        "documentno",
        "documentnumber",
        "referencenumber",
        "invoiceno",
        "invoicenumber",
        "itemname",
        "requestno",
        "request_no",
        *extra,
    )
    seen: set[str] = set()
    out: list[str] = []

    def _add(actual: Optional[str]) -> None:
        if actual and actual not in seen:
            seen.add(actual)
            out.append(actual)

    for key in preferred:
        _add(by_lower.get(key))
    if types_by_lower:
        for key, actual in by_lower.items():
            if key in _SKIP_SEARCH_COLS:
                continue
            dtype = (types_by_lower.get(key) or "").lower()
            if dtype in _TEXT_TYPES or dtype.startswith("character"):
                _add(actual)
    return out[:80]


def pick_deleted_column(by_lower: dict[str, str]) -> Optional[str]:
    return by_lower.get("isdeleted") or by_lower.get("is_deleted")


def pick_status_column(by_lower: dict[str, str]) -> Optional[str]:
    return by_lower.get("status") or by_lower.get("workflowstatus")


async def fetch_columns(db: Any, schema: str, table: str) -> tuple[dict[str, str], dict[str, str]]:
    rows = await db.fetch(
        """
        SELECT column_name, data_type, udt_name
        FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = $2
        """,
        schema,
        table,
    )
    by_lower: dict[str, str] = {}
    types_by_lower: dict[str, str] = {}
    for row in rows or []:
        name = str(row_get(row, "column_name") or "").strip()
        if not name:
            continue
        key = name.lower()
        by_lower[key] = name
        data_type = str(row_get(row, "data_type") or "").strip()
        udt = str(row_get(row, "udt_name") or "").strip()
        types_by_lower[key] = data_type if data_type.lower() != "user-defined" else udt
    return by_lower, types_by_lower


async def find_table(
    db: Any,
    name_candidates: tuple[str, ...],
    *,
    prefer_schemas: tuple[str, ...] = ("dbo", "workflow", "repository", "public"),
) -> Optional[tuple[str, str]]:
    lowered = [n.lower() for n in name_candidates]
    rows: list[Any] = []
    try:
        rows = await db.fetch(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE lower(table_name) = ANY($1::text[])
              AND table_type IN ('BASE TABLE', 'TABLE')
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            """,
            lowered,
        )
    except Exception as exc:
        logger.warning(
            "global_search_find_table_any_failed",
            extra={"error_type": type(exc).__name__},
        )
        rows = []
        for name in lowered:
            try:
                found = await db.fetch(
                    """
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE lower(table_name) = $1
                      AND table_schema NOT IN ('pg_catalog', 'information_schema')
                    """,
                    name,
                )
            except Exception:
                continue
            rows.extend(found or [])
    ranked: list[tuple[int, int, str, str]] = []
    for row in rows or []:
        schema = str(row_get(row, "table_schema") or "")
        name = str(row_get(row, "table_name") or "")
        if not schema or not name:
            continue
        name_rank = lowered.index(name.lower()) if name.lower() in lowered else 99
        try:
            schema_rank = prefer_schemas.index(schema.lower())
        except ValueError:
            schema_rank = 20
        ranked.append((name_rank, schema_rank, schema, name))
    if not ranked:
        return None
    ranked.sort()
    return ranked[0][2], ranked[0][3]


async def find_items_tables(db: Any, *, limit: int = 12) -> list[tuple[str, str]]:
    return await _find_items_tables_prefix(db, "items_", limit=limit)


async def find_items_tables_for_repository(
    db: Any, repository_id: str, *, limit: int = 8
) -> list[tuple[str, str]]:
    compact = (repository_id or "").replace("-", "").lower()
    prefix = compact[:8]
    if len(prefix) < 8:
        return []
    return await _find_items_tables_prefix(db, f"items_{prefix}", limit=limit)


async def _find_items_tables_prefix(db: Any, prefix: str, *, limit: int) -> list[tuple[str, str]]:
    try:
        rows = await db.fetch(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE starts_with(lower(table_name), $1)
              AND table_type IN ('BASE TABLE', 'TABLE')
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY CASE
                WHEN lower(table_schema) = 'repository' THEN 0
                WHEN table_schema = 'dbo' THEN 1
                ELSE 2
            END, table_name
            LIMIT $2
            """,
            prefix.lower(),
            limit,
        )
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    for row in rows or []:
        schema = str(row_get(row, "table_schema") or "")
        name = str(row_get(row, "table_name") or "")
        if schema and name:
            out.append((schema, name))
    return out


def qualified(schema: str, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}"
