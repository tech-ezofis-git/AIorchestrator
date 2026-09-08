"""Bounded ILIKE search over a resolved tenant table."""
from __future__ import annotations

import logging
import re
from typing import Any

from app.data_import.ident import quote_ident
from app.global_search.schema import (
    fetch_columns,
    find_items_tables,
    find_items_tables_for_repository,
    find_table,
    pick_deleted_column,
    pick_id_column,
    pick_name_column,
    pick_status_column,
    pick_text_columns,
    qualified,
    row_get,
)
from app.global_search.types import SearchHit

logger = logging.getLogger("orchestrator.global_search")

_WS = re.compile(r"\s+")

REPO_TABLES = ("wrepository", "wrepositories", "repositories", "repository")
WORKFLOW_TABLES = (
    "wworkflow",
    "wworkflows",
    "workflows",
    "workflow",
    "workflowdefinitions",
)
WORKFLOW_INSTANCE_TABLES = (
    "wworkflowinstance",
    "workflowinstances",
    "workflowinstance",
    "wprocess",
    "processinstance",
)
DEFAULT_LIMIT = 20


def normalize_query(raw: str) -> str:
    return _WS.sub(" ", (raw or "").strip())


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


async def _search_table(
    db: Any,
    *,
    schema: str,
    table: str,
    query: str,
    entity_type: str,
    limit: int,
    extra_text: tuple[str, ...] = (),
    specific_id: str = "",
    specific_id_keys: tuple[str, ...] = (),
) -> list[SearchHit]:
    try:
        by_lower, types_by_lower = await fetch_columns(db, schema, table)
    except Exception:
        return []
    id_col = pick_id_column(by_lower) or next(iter(by_lower.values()), None)
    name_col = pick_name_column(by_lower)
    text_cols = pick_text_columns(by_lower, extra=extra_text, types_by_lower=types_by_lower)
    if not id_col or not text_cols:
        logger.warning(
            "global_search_skip_table",
            extra={"table": table, "has_id": bool(id_col), "text_cols": len(text_cols)},
        )
        return []
    deleted = pick_deleted_column(by_lower)
    status_col = pick_status_column(by_lower)
    desc_col = by_lower.get("description")

    like_param = f"%{query}%"
    clauses = [f"CAST({quote_ident(col)} AS text) ILIKE $1" for col in text_cols]
    where = "(" + " OR ".join(clauses) + ")"
    args: list[Any] = [like_param]
    if deleted:
        qdel = quote_ident(deleted)
        where += (
            f" AND lower(COALESCE(CAST({qdel} AS text), '0'))"
            f" NOT IN ('1', 'true', 't', 'yes', 'y')"
        )
    if specific_id and specific_id_keys:
        repo_col = next((by_lower[k] for k in specific_id_keys if k in by_lower), None)
        if repo_col:
            args.append(specific_id)
            where += (
                f" AND lower(replace(CAST({quote_ident(repo_col)} AS text), '-', ''))"
                f" = lower(replace(CAST(${len(args)} AS text), '-', ''))"
            )

    select_cols = [f"{quote_ident(id_col)} AS entity_id"]
    if name_col:
        select_cols.append(f"{quote_ident(name_col)} AS entity_name")
    else:
        select_cols.append(f"CAST({quote_ident(id_col)} AS text) AS entity_name")
    if desc_col:
        select_cols.append(f"{quote_ident(desc_col)} AS description")
    if status_col:
        select_cols.append(f"{quote_ident(status_col)} AS status")
    for i, col in enumerate(text_cols):
        select_cols.append(f"{quote_ident(col)} AS {quote_ident(f'm{i}')}")

    sql = (
        f"SELECT {', '.join(select_cols)} FROM {qualified(schema, table)} "
        f"WHERE {where} LIMIT {int(limit)}"
    )
    try:
        rows = await db.fetch(sql, *args)
    except Exception as exc:
        logger.warning(
            "global_search_sql_failed",
            extra={"table": table, "error_type": type(exc).__name__},
        )
        return []

    hits: list[SearchHit] = []
    q_lower = query.lower()
    for row in rows or []:
        entity_id = _str(row_get(row, "entity_id"))
        entity_name = _str(row_get(row, "entity_name")) or entity_id
        if not entity_id:
            continue
        matched_field = name_col or "Name"
        matched_value = entity_name
        for i, col in enumerate(text_cols):
            value = _str(row_get(row, f"m{i}"))
            if value and q_lower in value.lower():
                matched_field = col
                matched_value = value
                break
        meta: dict[str, Any] = {}
        status = _str(row_get(row, "status"))
        if status:
            meta["status"] = status
        hit_kwargs: dict[str, Any] = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "entity_name": entity_name,
            "matched_field": matched_field,
            "matched_value": matched_value,
            "description": _str(row_get(row, "description")) or None,
            "metadata": meta,
        }
        if entity_type == "document":
            hit_kwargs["matchSource"] = "field"
            hit_kwargs["ifileName"] = entity_name
            hit_kwargs["name"] = entity_name
            hit_kwargs["id"] = {
                "workspaceId": "",
                "repositoryId": specific_id or "",
                "repositoryName": "",
                "itemId": entity_id,
                "workflowId": 0,
                "processId": 0,
            }
        hits.append(SearchHit(**hit_kwargs))
        if len(hits) >= limit:
            break
    return hits


async def search_repositories(
    db: Any, query: str, *, limit: int = DEFAULT_LIMIT, specific_id: str = ""
) -> list[SearchHit]:
    _ = specific_id
    located = await find_table(db, REPO_TABLES)
    if located is None:
        logger.warning("global_search_no_repository_table")
        return []
    schema, table = located
    return await _search_table(
        db,
        schema=schema,
        table=table,
        query=query,
        entity_type="repository",
        limit=limit,
        extra_text=("code",),
    )


async def search_workflows(db: Any, query: str, *, limit: int = DEFAULT_LIMIT) -> list[SearchHit]:
    hits: list[SearchHit] = []
    seen: set[str] = set()
    found_table = False
    for names, extras in (
        (WORKFLOW_TABLES, ("code", "status")),
        (WORKFLOW_INSTANCE_TABLES, ("code", "status", "title", "requestno", "request_no")),
    ):
        located = await find_table(db, names, prefer_schemas=("workflow", "dbo", "public"))
        if located is None:
            continue
        found_table = True
        schema, table = located
        remaining = limit - len(hits)
        if remaining <= 0:
            break
        part = await _search_table(
            db,
            schema=schema,
            table=table,
            query=query,
            entity_type="workflow",
            limit=remaining,
            extra_text=extras,
        )
        for hit in part:
            key = hit.entity_id.replace("-", "").lower()
            if key in seen:
                continue
            seen.add(key)
            hits.append(hit)
    if not found_table:
        logger.warning("global_search_no_workflow_table")
    return hits[:limit]


async def search_document_metadata(
    db: Any,
    query: str,
    *,
    specific_id: str = "",
    workspace_id: str = "",
    limit: int = DEFAULT_LIMIT,
) -> list[SearchHit]:
    from app.ap_skills.tenant_db import repository_items_table

    tables: list[tuple[str, str]] = []
    guessed = repository_items_table(specific_id) if specific_id else None
    if guessed:
        located = await find_table(
            db,
            (guessed.lower(), guessed),
            prefer_schemas=("repository", "dbo", "public"),
        )
        if located:
            tables.append(located)
        for row in await find_items_tables_for_repository(db, specific_id):
            tables.append(row)
    item_row = await find_table(
        db,
        ("repositoryitem", "repositoryitems", "repository_item"),
        prefer_schemas=("repository", "dbo", "public"),
    )
    if item_row:
        tables.append(item_row)
    index_row = await find_table(
        db,
        ("search_index", "searchindex"),
        prefer_schemas=("dbo", "repository", "public"),
    )
    if index_row:
        tables.append(index_row)
    if not specific_id:
        tables.extend(await find_items_tables(db, limit=8))
    elif not tables:
        logger.warning(
            "global_search_no_items_table",
            extra={"specific_id": specific_id, "guessed": guessed},
        )

    seen_tables: set[tuple[str, str]] = set()
    hits: list[SearchHit] = []
    for schema, table in tables:
        key = (schema.lower(), table.lower())
        if key in seen_tables:
            continue
        seen_tables.add(key)
        remaining = limit - len(hits)
        if remaining <= 0:
            break
        per_repo_items = table.lower().startswith("items_")
        part = await _search_table(
            db,
            schema=schema,
            table=table,
            query=query,
            entity_type="document",
            limit=remaining,
            extra_text=("ifilename", "filename", "description"),
            specific_id=specific_id,
            specific_id_keys=(
                ()
                if per_repo_items
                else ("wrepositoryid", "repositoryid", "repository_id")
            ),
        )
        for hit in part:
            if workspace_id and hit.id is not None:
                hit.id["workspaceId"] = workspace_id
            if specific_id and hit.id is not None and not hit.id.get("repositoryId"):
                hit.id["repositoryId"] = specific_id
        hits.extend(part)
    return hits[:limit]
