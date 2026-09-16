"""Database metadata discovery service — read-only information_schema inspection."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("orchestrator.report_agent.metadata")

_SKIP_SCHEMAS = ("pg_catalog", "information_schema")


@dataclass
class ColumnMeta:
    schema: str
    table: str
    column: str
    data_type: str
    udt_name: str = ""
    is_nullable: bool = True


@dataclass
class DatabaseSchema:
    tables: list[tuple[str, str]] = field(default_factory=list)  # (schema, table)
    columns_by_table: dict[tuple[str, str], list[ColumnMeta]] = field(default_factory=dict)
    all_columns: list[ColumnMeta] = field(default_factory=list)

    def get_columns_for_table(self, schema: str, table: str) -> list[ColumnMeta]:
        return self.columns_by_table.get((schema.lower(), table.lower()), [])


def _row_get(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except Exception:
        return None


async def get_database_schema(db: Any) -> DatabaseSchema:
    """Read all non-system tables and columns from information_schema."""
    query = """
        SELECT
            c.table_schema,
            c.table_name,
            c.column_name,
            c.data_type,
            c.udt_name,
            c.is_nullable
        FROM information_schema.columns c
        JOIN information_schema.tables t
          ON c.table_schema = t.table_schema
         AND c.table_name = t.table_name
        WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
          AND t.table_type IN ('BASE TABLE', 'TABLE', 'VIEW')
        ORDER BY
            CASE
                WHEN lower(c.table_schema) = 'workflow' THEN 0
                WHEN lower(c.table_schema) = 'repository' THEN 1
                WHEN lower(c.table_schema) = 'dbo' THEN 2
                WHEN lower(c.table_schema) = 'public' THEN 3
                ELSE 4
            END,
            c.table_schema,
            c.table_name,
            c.ordinal_position;
    """
    try:
        rows = await db.fetch(query)
    except Exception as exc:
        logger.warning(
            "report_agent_schema_fetch_fallback",
            extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
        )
        # Fallback simpler query if join fails on certain postgres variants / views
        simple_query = """
            SELECT
                table_schema,
                table_name,
                column_name,
                data_type,
                udt_name,
                is_nullable
            FROM information_schema.columns
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name, ordinal_position;
        """
        rows = await db.fetch(simple_query)

    schema = DatabaseSchema()
    seen_tables: set[tuple[str, str]] = set()

    for row in rows or []:
        table_schema = str(_row_get(row, "table_schema") or "").strip()
        table_name = str(_row_get(row, "table_name") or "").strip()
        column_name = str(_row_get(row, "column_name") or "").strip()
        data_type = str(_row_get(row, "data_type") or "").strip()
        udt_name = str(_row_get(row, "udt_name") or "").strip()
        is_nullable_str = str(_row_get(row, "is_nullable") or "").strip().upper()

        if not table_schema or not table_name or not column_name:
            continue

        table_key = (table_schema.lower(), table_name.lower())
        if table_key not in seen_tables:
            seen_tables.add(table_key)
            schema.tables.append((table_schema, table_name))

        display_type = udt_name if data_type.lower() == "user-defined" and udt_name else data_type
        col = ColumnMeta(
            schema=table_schema,
            table=table_name,
            column=column_name,
            data_type=display_type,
            udt_name=udt_name,
            is_nullable=is_nullable_str == "YES",
        )
        schema.columns_by_table.setdefault(table_key, []).append(col)
        schema.all_columns.append(col)

    logger.info(
        "report_agent_schema_discovered",
        extra={"tables_count": len(schema.tables), "columns_count": len(schema.all_columns)},
    )
    return schema
