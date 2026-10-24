"""Database metadata discovery service — read-only information_schema inspection."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.data_import.ident import quote_ident

logger = logging.getLogger("orchestrator.report_agent.metadata")

import re

_SKIP_SCHEMAS = ("pg_catalog", "information_schema")


def _ezfb_table_name(form_id: Optional[str]) -> Optional[str]:
    raw = str(form_id or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return f"ezfb_{int(raw)}_items"
    compact = raw.replace("-", "").lower()
    if len(compact) >= 8:
        return f"ezfb_{compact[:8]}_items"
    return None


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
class RepositoryMeta:
    repository_id: str
    name: str
    items_table: Optional[str] = None


@dataclass
class DatabaseSchema:
    tables: list[tuple[str, str]] = field(default_factory=list)  # (schema, table)
    columns_by_table: dict[tuple[str, str], list[ColumnMeta]] = field(default_factory=dict)
    all_columns: list[ColumnMeta] = field(default_factory=list)
    workflows: list[WorkflowMeta] = field(default_factory=list)
    repositories: list[RepositoryMeta] = field(default_factory=list)
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

    def resolve_table(self, schema_name: Optional[str], table: str) -> Optional[tuple[str, str]]:
        """Return the live (schema, table) pair with original identifier case."""
        table_clean = (table or "").strip().strip('"')
        schema_clean = (schema_name or "").strip().strip('"')
        if not table_clean:
            return None
        if "." in table_clean and not schema_clean:
            schema_clean, table_clean = table_clean.split(".", 1)
        if schema_clean:
            cols = self.get_columns_for_table(schema_clean, table_clean)
            if cols:
                return cols[0].schema, cols[0].table
        for live_schema, live_table in self.tables:
            if live_table.lower() != table_clean.lower():
                continue
            if not schema_clean or live_schema.lower() == schema_clean.lower():
                return live_schema, live_table
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

    await _apply_ezofis_catalogs(db, schema)
    if not schema.workflows:
        await _discover_workflow_metadata(db, schema)
    if not schema.repositories:
        await _discover_repository_metadata(db, schema)
    await _discover_form_metadata(db, schema)
    return schema


_WORKFLOW_TABLE_NAMES = (
    "workflows",
    "wworkflow",
    "wworkflows",
    "workflowdefinitions",
)
_REPO_TABLE_NAMES = (
    "repositories",
    "wrepository",
    "wrepositories",
    "repositorydefinitions",
)
_WORKFLOW_QUERIES = (
    """
    SELECT "Id"::text AS id, "Name" AS name,
           "FormId"::text AS form_id, "RepositoryId"::text AS repository_id
    FROM workflow."Workflows"
    ORDER BY "Name"
    """,
    """
    SELECT id::text AS id, name AS name,
           formid::text AS form_id, repositoryid::text AS repository_id
    FROM workflow.workflows
    ORDER BY name
    """,
    """
    SELECT "Id"::text AS id, "Name" AS name,
           "FormId"::text AS form_id, "RepositoryId"::text AS repository_id
    FROM dbo."wworkflow"
    ORDER BY "Name"
    """,
    """
    SELECT id::text AS id, name AS name,
           formid::text AS form_id, repositoryid::text AS repository_id
    FROM dbo.wworkflow
    ORDER BY name
    """,
)
_REPO_QUERIES = (
    """
    SELECT "Id"::text AS id, "Name" AS name, "ItemsTableName" AS items_table
    FROM repository."Repositories"
    ORDER BY "Name"
    """,
    """
    SELECT id::text AS id, name AS name, itemstablename AS items_table
    FROM repository.repositories
    ORDER BY name
    """,
    """
    SELECT "Id"::text AS id, "Name" AS name, "ItemsTableName" AS items_table
    FROM dbo."wrepository"
    ORDER BY "Name"
    """,
    """
    SELECT id::text AS id, name AS name, itemstablename AS items_table
    FROM dbo.wrepository
    ORDER BY name
    """,
)


def _not_deleted_sql(column: Optional[str]) -> str:
    if not column:
        return ""
    ident = quote_ident(column)
    return (
        f" AND COALESCE(lower(CAST({ident} AS text)), 'false') "
        f"NOT IN ('1', 'true', 't', 'yes', 'y')"
    )


async def _find_catalog_tables(
    db: Any,
    names: tuple[str, ...],
    *,
    prefer_schemas: tuple[str, ...] = ("workflow", "repository", "dbo", "public"),
) -> list[tuple[str, str]]:
    if db is None or not hasattr(db, "fetch"):
        return []
    lowered = [n.lower() for n in names]
    try:
        rows = await db.fetch(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE lower(table_name) = ANY($1::text[])
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            """,
            lowered,
        )
    except Exception as exc:
        logger.warning(
            "report_agent_find_catalog_tables_failed",
            extra={"error_type": type(exc).__name__, "error": str(exc)[:200]},
        )
        return []
    ranked: list[tuple[int, int, str, str]] = []
    for row in rows or []:
        schema_name = str(_row_get(row, "table_schema") or "").strip()
        table_name = str(_row_get(row, "table_name") or "").strip()
        if not schema_name or not table_name:
            continue
        name_rank = lowered.index(table_name.lower()) if table_name.lower() in lowered else 99
        try:
            schema_rank = prefer_schemas.index(schema_name.lower())
        except ValueError:
            schema_rank = 20
        ranked.append((name_rank, schema_rank, schema_name, table_name))
    ranked.sort()
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for _n, _s, schema_name, table_name in ranked:
        key = (schema_name.lower(), table_name.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append((schema_name, table_name))
    return out


async def _table_columns(db: Any, schema_name: str, table_name: str) -> dict[str, str]:
    try:
        rows = await db.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE lower(table_schema) = lower($1)
              AND lower(table_name) = lower($2)
            """,
            schema_name,
            table_name,
        )
    except Exception:
        return {}
    out: dict[str, str] = {}
    for row in rows or []:
        name = str(_row_get(row, "column_name") or "").strip()
        if name:
            out[name.lower()] = name
    return out


async def _read_named_rows(
    db: Any,
    schema_name: str,
    table_name: str,
    *,
    extra_name_cols: tuple[str, ...] = (),
    extra_id_cols: tuple[str, ...] = (),
    extra_cols: dict[str, tuple[str, ...]] | None = None,
) -> list[dict[str, Optional[str]]]:
    by_lower = await _table_columns(db, schema_name, table_name)
    if not by_lower:
        return []
    id_col = None
    for key in ("id", *extra_id_cols):
        if key in by_lower:
            id_col = by_lower[key]
            break
    name_col = None
    for key in ("name", "title", "displayname", "display_name", *extra_name_cols):
        if key in by_lower:
            name_col = by_lower[key]
            break
    if not id_col or not name_col:
        return []
    del_col = by_lower.get("isdeleted") or by_lower.get("is_deleted")
    select = [
        f"CAST({quote_ident(id_col)} AS text) AS id",
        f"CAST({quote_ident(name_col)} AS text) AS name",
    ]
    aliases: dict[str, str] = {}
    for alias, candidates in (extra_cols or {}).items():
        found = None
        for key in candidates:
            if key in by_lower:
                found = by_lower[key]
                break
        if found:
            aliases[alias] = found
            select.append(f"CAST({quote_ident(found)} AS text) AS {alias}")
    sql = (
        f"SELECT {', '.join(select)} "
        f"FROM {quote_ident(schema_name)}.{quote_ident(table_name)} "
        f"WHERE CAST({quote_ident(name_col)} AS text) IS NOT NULL "
        f"AND btrim(CAST({quote_ident(name_col)} AS text)) <> '' "
        f"{_not_deleted_sql(del_col)} "
        f"ORDER BY 2 "
        f"LIMIT 500"
    )
    try:
        rows = await db.fetch(sql)
    except Exception as exc:
        logger.warning(
            "report_agent_catalog_table_read_failed",
            extra={
                "table": f"{schema_name}.{table_name}",
                "error_type": type(exc).__name__,
                "error": str(exc)[:200],
            },
        )
        return []
    out: list[dict[str, Optional[str]]] = []
    for row in rows or []:
        name = str(_row_get(row, "name") or "").strip()
        if not name:
            continue
        item: dict[str, Optional[str]] = {
            "id": str(_row_get(row, "id") or "").strip() or name,
            "name": name,
        }
        for alias in aliases:
            value = str(_row_get(row, alias) or "").strip()
            item[alias] = value or None
        out.append(item)
    return out


async def _fetch_all(db: Any, queries: tuple[str, ...]) -> list[Any]:
    if db is None or not hasattr(db, "fetch"):
        return []
    rows: list[Any] = []
    for query in queries:
        try:
            found = await db.fetch(query)
        except Exception:
            continue
        if found:
            rows.extend(list(found))
    return rows


def _parse_items_table(raw: Optional[str], repository_id: str) -> Optional[tuple[str, str]]:
    text = (raw or "").strip()
    schema_name = "repository"
    table = text
    if "." in text:
        schema_name, table = [part.strip() for part in text.split(".", 1)]
    if not table:
        compact = (repository_id or "").replace("-", "").lower()
        if len(compact) >= 8:
            table = f"items_{compact[:8]}"
        else:
            return None
    return schema_name or "repository", table


def _index_workflow(schema: DatabaseSchema, wf: WorkflowMeta) -> None:
    schema.workflows.append(wf)
    wf_id_clean = (wf.workflow_id or "").replace("-", "").lower()
    if wf_id_clean:
        schema.table_to_workflow[wf_id_clean] = wf.name
        if len(wf_id_clean) >= 8:
            schema.table_to_workflow[wf_id_clean[:8]] = wf.name
    if wf.form_id:
        form_id_clean = wf.form_id.replace("-", "").lower()
        schema.table_to_workflow[form_id_clean] = wf.name
        if len(form_id_clean) >= 8:
            schema.table_to_workflow[form_id_clean[:8]] = wf.name
        ezfb = _ezfb_table_name(wf.form_id)
        if ezfb:
            schema.table_to_workflow[ezfb] = wf.name
            schema.table_to_workflow[ezfb.replace("_", "")] = wf.name
    if wf.repository_id:
        repo_id_clean = wf.repository_id.replace("-", "").lower()
        schema.table_to_workflow[repo_id_clean] = wf.name
        if len(repo_id_clean) >= 8:
            schema.table_to_workflow[repo_id_clean[:8]] = wf.name


def _index_repository(schema: DatabaseSchema, repo: RepositoryMeta) -> None:
    schema.repositories.append(repo)
    repo_id_clean = (repo.repository_id or "").replace("-", "").lower()
    if repo_id_clean:
        schema.table_to_workflow[repo_id_clean] = repo.name
        if len(repo_id_clean) >= 8:
            schema.table_to_workflow[repo_id_clean[:8]] = repo.name
    parsed = _parse_items_table(repo.items_table, repo.repository_id)
    if parsed:
        _schema, table = parsed
        schema.table_to_workflow[table] = repo.name
        schema.table_to_workflow[table.lower()] = repo.name


def _dedupe_named(rows: list[dict[str, Optional[str]]]) -> list[dict[str, Optional[str]]]:
    by_id: dict[str, dict[str, Optional[str]]] = {}
    by_name: dict[str, dict[str, Optional[str]]] = {}
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        rid = str(row.get("id") or "").replace("-", "").lower()
        nkey = name.lower()
        if rid and rid in by_id:
            continue
        existing = by_name.get(nkey)
        if existing is not None:
            if existing.get("form_id") or existing.get("items_table") or not (
                row.get("form_id") or row.get("items_table")
            ):
                continue
        if rid:
            by_id[rid] = row
        by_name[nkey] = row
    return sorted(by_name.values(), key=lambda item: str(item.get("name") or "").lower())


async def load_ezofis_catalogs(db: Any) -> tuple[list[WorkflowMeta], list[RepositoryMeta]]:
    """Read live EZOFIS workflow/repository names from every matching catalog table."""
    raw_wf: list[dict[str, Optional[str]]] = []
    for schema_name, table_name in await _find_catalog_tables(
        db, _WORKFLOW_TABLE_NAMES, prefer_schemas=("workflow", "dbo", "public")
    ):
        raw_wf.extend(
            await _read_named_rows(
                db,
                schema_name,
                table_name,
                extra_name_cols=("workflowname", "workflow_name"),
                extra_id_cols=("wworkflowid", "workflowid", "iworkflowid"),
                extra_cols={
                    "form_id": ("formid", "form_id", "wformid", "wform_id", "iformid"),
                    "repository_id": (
                        "repositoryid",
                        "repository_id",
                        "wrepositoryid",
                        "wrepository_id",
                        "irepositoryid",
                    ),
                },
            )
        )
    if not raw_wf:
        for row in await _fetch_all(db, _WORKFLOW_QUERIES):
            raw_wf.append(
                {
                    "id": str(_row_get(row, "id") or "").strip() or None,
                    "name": str(_row_get(row, "name") or "").strip() or None,
                    "form_id": str(_row_get(row, "form_id") or "").strip() or None,
                    "repository_id": str(_row_get(row, "repository_id") or "").strip() or None,
                }
            )

    raw_repo: list[dict[str, Optional[str]]] = []
    for schema_name, table_name in await _find_catalog_tables(
        db, _REPO_TABLE_NAMES, prefer_schemas=("repository", "dbo", "public")
    ):
        raw_repo.extend(
            await _read_named_rows(
                db,
                schema_name,
                table_name,
                extra_name_cols=("repositoryname", "repository_name"),
                extra_id_cols=("wrepositoryid", "repositoryid", "irepositoryid"),
                extra_cols={
                    "items_table": ("itemstablename", "items_table_name", "tablename", "table_name"),
                },
            )
        )
    if not raw_repo:
        for row in await _fetch_all(db, _REPO_QUERIES):
            raw_repo.append(
                {
                    "id": str(_row_get(row, "id") or "").strip() or None,
                    "name": str(_row_get(row, "name") or "").strip() or None,
                    "items_table": str(_row_get(row, "items_table") or "").strip() or None,
                }
            )

    workflows = [
        WorkflowMeta(
            workflow_id=str(row.get("id") or row.get("name") or ""),
            name=str(row.get("name") or ""),
            form_id=row.get("form_id"),
            repository_id=row.get("repository_id"),
        )
        for row in _dedupe_named(raw_wf)
        if row.get("name")
    ]
    repositories = [
        RepositoryMeta(
            repository_id=str(row.get("id") or row.get("name") or ""),
            name=str(row.get("name") or ""),
            items_table=row.get("items_table"),
        )
        for row in _dedupe_named(raw_repo)
        if row.get("name")
    ]
    logger.info(
        "report_agent_ezofis_catalogs",
        extra={"workflows": len(workflows), "repositories": len(repositories)},
    )
    return workflows, repositories


async def _apply_ezofis_catalogs(db: Any, schema: DatabaseSchema) -> None:
    workflows, repositories = await load_ezofis_catalogs(db)
    for wf in workflows:
        _index_workflow(schema, wf)
    for repo in repositories:
        _index_repository(schema, repo)


async def find_live_table(db: Any, table_name: str, schema_hint: Optional[str] = None) -> Optional[tuple[str, str]]:
    if db is None or not hasattr(db, "fetch") or not (table_name or "").strip():
        return None
    try:
        rows = await db.fetch(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE lower(table_name) = lower($1)
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
              AND ($2::text IS NULL OR lower(table_schema) = lower($2))
            ORDER BY CASE lower(table_schema)
                WHEN 'dbo' THEN 0
                WHEN 'workflow' THEN 1
                WHEN 'repository' THEN 2
                WHEN 'public' THEN 3
                ELSE 4
            END
            LIMIT 1
            """,
            table_name.strip(),
            (schema_hint or "").strip() or None,
        )
    except Exception as exc:
        logger.warning(
            "report_agent_find_table_failed",
            extra={"table": table_name, "error_type": type(exc).__name__},
        )
        return None
    if not rows:
        return None
    row = rows[0]
    return str(_row_get(row, "table_schema") or "").strip(), str(_row_get(row, "table_name") or "").strip()


async def get_table_columns(db: Any, schema_name: str, table_name: str) -> list[ColumnMeta]:
    if db is None or not hasattr(db, "fetch"):
        return []
    try:
        rows = await db.fetch(
            """
            SELECT table_schema, table_name, column_name, data_type, udt_name, is_nullable
            FROM information_schema.columns
            WHERE lower(table_schema) = lower($1)
              AND lower(table_name) = lower($2)
            ORDER BY ordinal_position
            """,
            schema_name,
            table_name,
        )
    except Exception as exc:
        logger.warning(
            "report_agent_table_columns_failed",
            extra={"table": table_name, "error_type": type(exc).__name__},
        )
        return []
    cols: list[ColumnMeta] = []
    for row in rows or []:
        column_name = str(_row_get(row, "column_name") or "").strip()
        if not column_name:
            continue
        data_type = str(_row_get(row, "data_type") or "").strip()
        udt_name = str(_row_get(row, "udt_name") or "").strip()
        display_type = udt_name if data_type.lower() == "user-defined" and udt_name else data_type
        cols.append(
            ColumnMeta(
                schema=str(_row_get(row, "table_schema") or schema_name),
                table=str(_row_get(row, "table_name") or table_name),
                column=column_name,
                data_type=display_type,
                udt_name=udt_name,
                is_nullable=str(_row_get(row, "is_nullable") or "").upper() == "YES",
            )
        )
    return cols


async def list_fact_tables(db: Any, *, limit: int = 12) -> list[tuple[str, str]]:
    if db is None or not hasattr(db, "fetch"):
        return []
    try:
        rows = await db.fetch(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
              AND (
                    lower(table_name) LIKE 'ezfb_%_items'
                 OR lower(table_name) LIKE 'items_%'
              )
            ORDER BY
                CASE WHEN lower(table_name) LIKE 'ezfb_%_items' THEN 0 ELSE 1 END,
                CASE lower(table_schema)
                    WHEN 'dbo' THEN 0
                    WHEN 'repository' THEN 1
                    WHEN 'public' THEN 2
                    ELSE 3
                END,
                table_name
            LIMIT $1
            """,
            limit,
        )
    except Exception as exc:
        logger.warning(
            "report_agent_list_fact_tables_failed",
            extra={"error_type": type(exc).__name__},
        )
        return []
    out: list[tuple[str, str]] = []
    for row in rows or []:
        schema_name = str(_row_get(row, "table_schema") or "").strip()
        table_name = str(_row_get(row, "table_name") or "").strip()
        if schema_name and table_name:
            out.append((schema_name, table_name))
    return out


def schema_from_columns(
    columns: list[ColumnMeta],
    *,
    workflows: Optional[list[WorkflowMeta]] = None,
    repositories: Optional[list[RepositoryMeta]] = None,
) -> DatabaseSchema:
    schema = DatabaseSchema()
    seen: set[tuple[str, str]] = set()
    for col in columns:
        key = (col.schema.lower(), col.table.lower())
        if key not in seen:
            seen.add(key)
            schema.tables.append((col.schema, col.table))
        schema.columns_by_table.setdefault(key, []).append(col)
        schema.all_columns.append(col)
    for wf in workflows or []:
        _index_workflow(schema, wf)
    for repo in repositories or []:
        _index_repository(schema, repo)
    return schema


def schema_from_form_fields(
    *,
    form_id: str,
    fields: list[dict[str, str]],
    workflow: Optional[WorkflowMeta] = None,
) -> DatabaseSchema:
    table = _ezfb_table_name(form_id) or "ezfb_items"
    columns = [
        ColumnMeta("dbo", table, str(item.get("key") or "").strip(), str(item.get("type") or "text"))
        for item in fields
        if str(item.get("key") or "").strip()
    ]
    return schema_from_columns(columns, workflows=[workflow] if workflow else None)


def schema_from_repository_fields(
    *,
    repository_id: str,
    name: str,
    items_table: Optional[str],
    fields: list[dict[str, str]],
) -> DatabaseSchema:
    parsed = _parse_items_table(items_table, repository_id)
    schema_name, table = parsed if parsed else ("repository", "items")
    columns = [
        ColumnMeta(schema_name, table, str(item.get("key") or "").strip(), str(item.get("type") or "text"))
        for item in fields
        if str(item.get("key") or "").strip()
    ]
    repo = RepositoryMeta(repository_id=repository_id, name=name, items_table=f"{schema_name}.{table}")
    return schema_from_columns(columns, repositories=[repo])


_ALL_WORKFLOW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("workflowName", "text"),
    ("status", "text"),
    ("startedBy", "text"),
    ("createdAtUtc", "date"),
    ("startedAtUtc", "date"),
    ("completedAtUtc", "date"),
    ("id", "uuid"),
    ("workflowId", "uuid"),
)
_ALL_REPOSITORY_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Repository_Name", "text"),
    ("FileName", "text"),
    ("FileVersion", "text"),
    ("CreatedBy", "text"),
    ("CreatedAt", "date"),
    ("Status", "text"),
    ("id", "uuid"),
)


def schema_for_all_workflows(workflows: list[WorkflowMeta]) -> DatabaseSchema:
    columns = [
        ColumnMeta("workflow", "workflow_instances_all", name, data_type)
        for name, data_type in _ALL_WORKFLOW_COLUMNS
    ]
    return schema_from_columns(columns, workflows=workflows)


def schema_for_all_repositories(repositories: list[RepositoryMeta]) -> DatabaseSchema:
    columns = [
        ColumnMeta("repository", "items_all", name, data_type)
        for name, data_type in _ALL_REPOSITORY_COLUMNS
    ]
    return schema_from_columns(columns, repositories=repositories)


async def resolve_report_source(
    db: Any,
    *,
    report_type: str,
    workflow_name: Optional[str] = None,
    repository_name: Optional[str] = None,
) -> tuple[DatabaseSchema, list[str]]:
    """Resolve the live EZOFIS fact table + columns for this report (tenant DB)."""
    warnings: list[str] = []
    workflows, repositories = await load_ezofis_catalogs(db)
    targets: list[tuple[str, str]] = []

    def _match_wf() -> Optional[WorkflowMeta]:
        want = (workflow_name or "").strip().lower()
        if not want:
            return None
        for wf in workflows:
            if wf.name.lower() == want or wf.workflow_id.lower() == want:
                return wf
        for wf in workflows:
            if want in wf.name.lower() or wf.name.lower() in want:
                return wf
        return None

    def _match_repo(name_or_id: Optional[str] = None, repo_id: Optional[str] = None) -> Optional[RepositoryMeta]:
        want = (name_or_id or "").strip().lower()
        rid = (repo_id or "").replace("-", "").lower()
        for repo in repositories:
            if rid and (repo.repository_id or "").replace("-", "").lower() == rid:
                return repo
            if want and (repo.name.lower() == want or (repo.repository_id or "").lower() == want):
                return repo
        if want:
            for repo in repositories:
                if want in repo.name.lower() or repo.name.lower() in want:
                    return repo
        return None

    if report_type == "specific_workflow":
        wf = _match_wf()
        if wf is None:
            warnings.append(
                f"Workflow '{workflow_name}' was not found in workflow.Workflows on this tenant database."
            )
        else:
            if wf.form_id:
                ezfb = _ezfb_table_name(wf.form_id)
                if ezfb:
                    live = await find_live_table(db, ezfb, "dbo") or await find_live_table(db, ezfb)
                    if live:
                        targets.append(live)
            repo = _match_repo(repo_id=wf.repository_id)
            if repo:
                parsed = _parse_items_table(repo.items_table, repo.repository_id)
                if parsed:
                    live = await find_live_table(db, parsed[1], parsed[0]) or await find_live_table(db, parsed[1])
                    if live and live not in targets:
                        targets.append(live)
            if not targets:
                warnings.append(
                    f"Workflow '{wf.name}' was found but its form/items table is missing in this tenant database."
                )

    if report_type == "specific_repository":
        repo = _match_repo(name_or_id=repository_name)
        if repo is None:
            warnings.append(
                f"Repository '{repository_name}' was not found in repository.Repositories on this tenant database."
            )
        else:
            parsed = _parse_items_table(repo.items_table, repo.repository_id)
            if parsed:
                live = await find_live_table(db, parsed[1], parsed[0]) or await find_live_table(db, parsed[1])
                if live:
                    targets.append(live)
            if not targets:
                warnings.append(
                    f"Repository '{repo.name}' was found but ItemsTableName is missing in this tenant database."
                )

    if report_type in ("all_workflows", "all_repositories"):
        kind = "ezfb_%_items" if "workflow" in report_type else "items_%"
        facts = await list_fact_tables(db, limit=8)
        if report_type == "all_repositories":
            facts = [item for item in facts if item[1].lower().startswith("items_")] or facts
        elif report_type == "all_workflows":
            facts = [item for item in facts if "ezfb_" in item[1].lower()] or facts
        for item in facts:
            if item not in targets:
                targets.append(item)
        if not targets and kind:
            warnings.append("No ezfb_*_items or items_* fact tables were found on this tenant database.")
    elif not targets:
        warnings.append(
            "No fact table was resolved for this specific workflow/repository; "
            "not substituting another workflow's columns."
        )

    columns: list[ColumnMeta] = []
    for schema_name, table_name in targets:
        cols = await get_table_columns(db, schema_name, table_name)
        if not cols:
            warnings.append(f"No columns discovered for {schema_name}.{table_name}.")
            continue
        columns.extend(cols)

    schema = schema_from_columns(columns, workflows=workflows, repositories=repositories)
    return schema, warnings


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
                ezfb = _ezfb_table_name(form_id)
                if ezfb:
                    schema.table_to_workflow[ezfb] = wf_name
                    schema.table_to_workflow[ezfb.replace("_", "")] = wf_name

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
        items_col = (
            col_names_lower.get("itemstablename")
            or col_names_lower.get("items_table_name")
            or col_names_lower.get("tablename")
        )

        if not id_col or not name_col:
            return

        sel_cols = [f'"{id_col}" AS repo_id', f'"{name_col}" AS repo_name']
        if items_col:
            sel_cols.append(f'"{items_col}" AS items_table')
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
            items_table = str(_row_get(row, "items_table") or "").strip() if items_col else ""
            if items_table:
                token = items_table.split(".")[-1]
                schema.table_to_workflow[token] = repo_name
                schema.table_to_workflow[token.lower()] = repo_name
            schema.repositories.append(
                RepositoryMeta(
                    repository_id=repo_id,
                    name=repo_name,
                    items_table=items_table or None,
                )
            )

    except Exception as exc:
        logger.debug("report_agent_repo_meta_discovery_skipped", extra={"error": str(exc)[:200]})


