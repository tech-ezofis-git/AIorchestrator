"""information_schema helpers for tenant EZOFIS tables (AP-store style)."""
from __future__ import annotations

from typing import Any, Optional

from app.data_import.ident import quote_ident

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


def pick_id_column(by_lower: dict[str, str]) -> Optional[str]:
    for key in ("id", "wrepositoryid", "wworkflowid", "itemid", "item_id"):
        if key in by_lower:
            return by_lower[key]
    return None


def pick_name_column(by_lower: dict[str, str]) -> Optional[str]:
    for key in ("name", "title", "ifilename", "filename", "file_name", "displayname"):
        if key in by_lower:
            return by_lower[key]
    return None


def pick_text_columns(by_lower: dict[str, str], *, extra: tuple[str, ...] = ()) -> list[str]:
    preferred = (
        "name",
        "title",
        "description",
        "code",
        "ifilename",
        "filename",
        "file_name",
        "displayname",
        *extra,
    )
    seen: set[str] = set()
    out: list[str] = []
    for key in preferred:
        actual = by_lower.get(key)
        if actual and actual not in seen:
            seen.add(actual)
            out.append(actual)
    return out


def pick_deleted_column(by_lower: dict[str, str]) -> Optional[str]:
    return by_lower.get("isdeleted") or by_lower.get("is_deleted")


def pick_status_column(by_lower: dict[str, str]) -> Optional[str]:
    return by_lower.get("status") or by_lower.get("workflowstatus")


async def fetch_columns(db: Any, schema: str, table: str) -> dict[str, str]:
    rows = await db.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = $1 AND table_name = $2
        """,
        schema,
        table,
    )
    return columns_by_lower(rows)


async def find_table(
    db: Any,
    name_candidates: tuple[str, ...],
    *,
    prefer_schemas: tuple[str, ...] = ("dbo", "workflow", "repository", "public"),
) -> Optional[tuple[str, str]]:
    lowered = [n.lower() for n in name_candidates]
    try:
        rows = await db.fetch(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE lower(table_name) = ANY($1::text[])
              AND table_type = 'BASE TABLE'
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            """,
            lowered,
        )
    except Exception:
        return None
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
    try:
        rows = await db.fetch(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE lower(table_name) LIKE 'items\\_%'
              AND table_type = 'BASE TABLE'
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY CASE WHEN table_schema = 'dbo' THEN 0 ELSE 1 END, table_name
            LIMIT $1
            """,
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
