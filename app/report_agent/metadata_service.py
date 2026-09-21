"""Database metadata discovery service — read-only information_schema inspection."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("orchestrator.report_agent.metadata")

import re

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
class WorkflowMeta:
    workflow_id: str
    name: str
    form_id: Optional[str] = None
    repository_id: Optional[str] = None


@dataclass
class DatabaseSchema:
    tables: list[tuple[str, str]] = field(default_factory=list)  # (schema, table)
    columns_by_table: dict[tuple[str, str], list[ColumnMeta]] = field(default_factory=dict)
    all_columns: list[ColumnMeta] = field(default_factory=list)
    workflows: list[WorkflowMeta] = field(default_factory=list)
    table_to_workflow: dict[str, str] = field(default_factory=dict)  # token / id prefix -> workflow name

    def get_columns_for_table(self, schema: str, table: str) -> list[ColumnMeta]:
        return self.columns_by_table.get((schema.lower(), table.lower()), [])

    def get_workflow_for_table(self, table: str) -> Optional[str]:
        t_clean = re.sub(r"[^a-zA-Z0-9]", "", table.lower())
        for key, wf_name in self.table_to_workflow.items():
            k_clean = re.sub(r"[^a-zA-Z0-9]", "", key.lower())
            if k_clean and (k_clean in t_clean or t_clean in k_clean):
                return wf_name
        return None


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

    await _discover_workflow_metadata(db, schema)
    await _discover_form_metadata(db, schema)
    await _discover_repository_metadata(db, schema)
    return schema


async def _discover_workflow_metadata(db: Any, schema: DatabaseSchema) -> None:
    """Discover workflow definitions and form/repository associations from wworkflow if available."""
    wf_tables = [
        (s, t) for (s, t) in schema.tables
        if t.lower() in ("wworkflow", "wworkflows", "workflows", "workflowdefinitions")
    ]
    if not wf_tables:
        return

    s, t = wf_tables[0]
    table_ident = f'"{s}"."{t}"' if s else f'"{t}"'
    try:
        cols = schema.get_columns_for_table(s, t)
        col_names_lower = {c.column.lower(): c.column for c in cols}
        id_col = (
            col_names_lower.get("id")
            or col_names_lower.get("wworkflowid")
            or col_names_lower.get("workflowid")
            or col_names_lower.get("iworkflowid")
        )
        name_col = (
            col_names_lower.get("name")
            or col_names_lower.get("workflowname")
            or col_names_lower.get("workflow_name")
            or col_names_lower.get("title")
        )
        form_col = (
            col_names_lower.get("formid")
            or col_names_lower.get("form_id")
            or col_names_lower.get("wformid")
            or col_names_lower.get("wform_id")
            or col_names_lower.get("iformid")
        )
        repo_col = (
            col_names_lower.get("repositoryid")
            or col_names_lower.get("repository_id")
            or col_names_lower.get("wrepositoryid")
            or col_names_lower.get("wrepository_id")
            or col_names_lower.get("irepositoryid")
        )
        del_col = col_names_lower.get("isdeleted") or col_names_lower.get("is_deleted")

        if not id_col or not name_col:
            return

        sel_cols = [f'"{id_col}" AS wf_id', f'"{name_col}" AS wf_name']
        if form_col:
            sel_cols.append(f'"{form_col}" AS form_id')
        if repo_col:
            sel_cols.append(f'"{repo_col}" AS repo_id')

        where_clause = f'WHERE ("{del_col}" IS NOT TRUE OR CAST("{del_col}" AS text) = \'0\')' if del_col else ""
        query = f"SELECT {', '.join(sel_cols)} FROM {table_ident} {where_clause};"
        rows = await db.fetch(query)

        for row in rows or []:
            wf_id = str(_row_get(row, "wf_id") or "").strip()
            wf_name = str(_row_get(row, "wf_name") or "").strip()
            form_id = str(_row_get(row, "form_id") or "").strip() if form_col else None
            repo_id = str(_row_get(row, "repo_id") or "").strip() if repo_col else None

            if not wf_id or not wf_name:
                continue

            schema.workflows.append(
                WorkflowMeta(workflow_id=wf_id, name=wf_name, form_id=form_id, repository_id=repo_id)
            )

            wf_id_clean = wf_id.replace("-", "").lower()
            schema.table_to_workflow[wf_id_clean] = wf_name
            if len(wf_id_clean) >= 8:
                schema.table_to_workflow[wf_id_clean[:8]] = wf_name

            if form_id:
                form_id_clean = form_id.replace("-", "").lower()
                schema.table_to_workflow[form_id_clean] = wf_name
                if len(form_id_clean) >= 8:
                    schema.table_to_workflow[form_id_clean[:8]] = wf_name

            if repo_id:
                repo_id_clean = repo_id.replace("-", "").lower()
                schema.table_to_workflow[repo_id_clean] = wf_name
                if len(repo_id_clean) >= 8:
                    schema.table_to_workflow[repo_id_clean[:8]] = wf_name

    except Exception as exc:
        logger.debug("report_agent_workflow_meta_discovery_skipped", extra={"error": str(exc)[:200]})


async def _discover_form_metadata(db: Any, schema: DatabaseSchema) -> None:
    """Discover Form definitions from wform table if available."""
    form_tables = [
        (s, t) for (s, t) in schema.tables
        if t.lower() in ("wform", "wforms", "forms", "formdefinitions", "ezfb_forms")
    ]
    if not form_tables:
        return

    s, t = form_tables[0]
    table_ident = f'"{s}"."{t}"' if s else f'"{t}"'
    try:
        cols = schema.get_columns_for_table(s, t)
        col_names_lower = {c.column.lower(): c.column for c in cols}
        id_col = (
            col_names_lower.get("id")
            or col_names_lower.get("wformid")
            or col_names_lower.get("formid")
            or col_names_lower.get("iformid")
        )
        name_col = (
            col_names_lower.get("name")
            or col_names_lower.get("formname")
            or col_names_lower.get("form_name")
            or col_names_lower.get("title")
        )
        del_col = col_names_lower.get("isdeleted") or col_names_lower.get("is_deleted")

        if not id_col or not name_col:
            return

        sel_cols = [f'"{id_col}" AS form_id', f'"{name_col}" AS form_name']
        where_clause = f'WHERE ("{del_col}" IS NOT TRUE OR CAST("{del_col}" AS text) = \'0\')' if del_col else ""
        query = f"SELECT {', '.join(sel_cols)} FROM {table_ident} {where_clause};"
        rows = await db.fetch(query)

        for row in rows or []:
            form_id = str(_row_get(row, "form_id") or "").strip()
            form_name = str(_row_get(row, "form_name") or "").strip()
            if not form_id or not form_name:
                continue

            form_id_clean = form_id.replace("-", "").lower()
            schema.table_to_workflow[form_id_clean] = form_name
            if len(form_id_clean) >= 8:
                schema.table_to_workflow[form_id_clean[:8]] = form_name

    except Exception as exc:
        logger.debug("report_agent_form_meta_discovery_skipped", extra={"error": str(exc)[:200]})


async def _discover_repository_metadata(db: Any, schema: DatabaseSchema) -> None:
    """Discover Repository definitions from wrepository table if available."""
    repo_tables = [
        (s, t) for (s, t) in schema.tables
        if t.lower() in ("wrepository", "wrepositories", "repositories", "repositorydefinitions")
    ]
    if not repo_tables:
        return

    s, t = repo_tables[0]
    table_ident = f'"{s}"."{t}"' if s else f'"{t}"'
    try:
        cols = schema.get_columns_for_table(s, t)
        col_names_lower = {c.column.lower(): c.column for c in cols}
        id_col = (
            col_names_lower.get("id")
            or col_names_lower.get("wrepositoryid")
            or col_names_lower.get("repositoryid")
            or col_names_lower.get("irepositoryid")
        )
        name_col = (
            col_names_lower.get("name")
            or col_names_lower.get("repositoryname")
            or col_names_lower.get("repository_name")
            or col_names_lower.get("title")
        )
        del_col = col_names_lower.get("isdeleted") or col_names_lower.get("is_deleted")

        if not id_col or not name_col:
            return

        sel_cols = [f'"{id_col}" AS repo_id', f'"{name_col}" AS repo_name']
        where_clause = f'WHERE ("{del_col}" IS NOT TRUE OR CAST("{del_col}" AS text) = \'0\')' if del_col else ""
        query = f"SELECT {', '.join(sel_cols)} FROM {table_ident} {where_clause};"
        rows = await db.fetch(query)

        for row in rows or []:
            repo_id = str(_row_get(row, "repo_id") or "").strip()
            repo_name = str(_row_get(row, "repo_name") or "").strip()
            if not repo_id or not repo_name:
                continue

            repo_id_clean = repo_id.replace("-", "").lower()
            schema.table_to_workflow[repo_id_clean] = repo_name
            if len(repo_id_clean) >= 8:
                schema.table_to_workflow[repo_id_clean[:8]] = repo_name

    except Exception as exc:
        logger.debug("report_agent_repo_meta_discovery_skipped", extra={"error": str(exc)[:200]})


