"""Bounded ILIKE search over resolved tenant tables — flat hit contract."""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

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
_SNIPPET_MAX = 180
_CONTENT_FIELD_HINTS = frozenset(
    {"ocr_text", "ocrtext", "ocr", "content", "fulltext", "full_text", "searchtext"}
)

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


def _snippet(value: str, limit: int = _SNIPPET_MAX) -> str:
    text = _str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _fmt_date(value: Any) -> str:
    text = _str(value)
    if not text:
        return ""
    # Prefer YYYY-MM-DD when value looks like a timestamp.
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text


def _pick_col(by_lower: dict[str, str], *keys: str) -> Optional[str]:
    for key in keys:
        if key in by_lower:
            return by_lower[key]
    return None


def _is_content_field(name: str) -> bool:
    key = (name or "").lower().replace(" ", "")
    return key in _CONTENT_FIELD_HINTS or "ocr" in key


async def _lookup_name(
    db: Any,
    *,
    table_candidates: tuple[str, ...],
    entity_id: str,
    prefer_schemas: tuple[str, ...] = ("dbo", "workflow", "repository", "public"),
    cache: dict[str, str],
) -> str:
    compact = entity_id.replace("-", "").lower()
    if not compact:
        return ""
    if compact in cache:
        return cache[compact]
    located = await find_table(db, table_candidates, prefer_schemas=prefer_schemas)
    if located is None:
        cache[compact] = ""
        return ""
    schema, table = located
    try:
        by_lower, _ = await fetch_columns(db, schema, table)
    except Exception:
        cache[compact] = ""
        return ""
    id_col = pick_id_column(by_lower)
    name_col = pick_name_column(by_lower)
    if not id_col or not name_col:
        cache[compact] = ""
        return ""
    sql = (
        f"SELECT CAST({quote_ident(name_col)} AS text) AS n "
        f"FROM {qualified(schema, table)} "
        f"WHERE lower(replace(CAST({quote_ident(id_col)} AS text), '-', '')) = $1 "
        f"LIMIT 1"
    )
    try:
        row = await db.fetchrow(sql, compact)
    except Exception:
        try:
            rows = await db.fetch(sql, compact)
            row = rows[0] if rows else None
        except Exception:
            row = None
    name = _str(row_get(row, "n")) if row is not None else ""
    cache[compact] = name
    return name


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
    repo_name_cache: Optional[dict[str, str]] = None,
    workflow_name_cache: Optional[dict[str, str]] = None,
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
    desc_col = _pick_col(by_lower, "description", "doctype", "documenttype", "type")
    mod_col = _pick_col(
        by_lower, "modifieddateandtime", "modifiedat", "modified_at", "updatedat", "updated_at"
    )
    date_col = _pick_col(by_lower, "dateandtime", "createdat", "created_at", "createddate")
    repo_col = _pick_col(by_lower, "wrepositoryid", "repositoryid", "repository_id")
    wf_col = _pick_col(by_lower, "wworkflowid", "workflowid", "workflow_id")
    inst_col = _pick_col(by_lower, "instanceid", "instance_id", "winstanceid")
    req_col = _pick_col(by_lower, "requestno", "request_no", "requestnumber")
    form_col = _pick_col(by_lower, "formid", "form_id", "wformid")

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
        filter_col = next((by_lower[k] for k in specific_id_keys if k in by_lower), None)
        if filter_col:
            args.append(specific_id)
            where += (
                f" AND lower(replace(CAST({quote_ident(filter_col)} AS text), '-', ''))"
                f" = lower(replace(CAST(${len(args)} AS text), '-', ''))"
            )

    select_cols = [f"{quote_ident(id_col)} AS entity_id"]
    if name_col:
        select_cols.append(f"{quote_ident(name_col)} AS entity_name")
    else:
        select_cols.append(f"CAST({quote_ident(id_col)} AS text) AS entity_name")
    if desc_col:
        select_cols.append(f"{quote_ident(desc_col)} AS description")
    if mod_col:
        select_cols.append(f"{quote_ident(mod_col)} AS modified_dt")
    if date_col:
        select_cols.append(f"{quote_ident(date_col)} AS created_dt")
    if status_col:
        select_cols.append(f"{quote_ident(status_col)} AS status")
    if repo_col:
        select_cols.append(f"{quote_ident(repo_col)} AS repository_id")
    if wf_col:
        select_cols.append(f"{quote_ident(wf_col)} AS workflow_id")
    if inst_col:
        select_cols.append(f"{quote_ident(inst_col)} AS instance_id")
    if req_col:
        select_cols.append(f"{quote_ident(req_col)} AS request_no")
    if form_col:
        select_cols.append(f"{quote_ident(form_col)} AS form_id")
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

    repo_cache = repo_name_cache if repo_name_cache is not None else {}
    wf_cache = workflow_name_cache if workflow_name_cache is not None else {}
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
        matched_value = _snippet(matched_value)
        description = _str(row_get(row, "description"))
        modified = _fmt_date(row_get(row, "modified_dt"))
        created = _fmt_date(row_get(row, "created_dt"))
        status = _str(row_get(row, "status"))
        meta: dict[str, Any] = {}
        if status:
            meta["status"] = status

        hit_kwargs: dict[str, Any] = {
            "type": entity_type,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "entity_name": entity_name,
            "matched_field": matched_field,
            "matched_value": matched_value,
            "description": description,
            "modifiedDateandtime": modified,
            "dateandtime": created,
            "metadata": meta,
        }

        if entity_type == "repository":
            hit_kwargs["name"] = entity_name
            hit_kwargs["id"] = {
                "repositoryId": entity_id,
                "repositoryName": entity_name,
            }
        elif entity_type == "workflow":
            hit_kwargs["name"] = entity_name
            req = _str(row_get(row, "request_no"))
            inst = _str(row_get(row, "instance_id"))
            wf_id = _str(row_get(row, "workflow_id")) or entity_id
            hit_kwargs["requestNo"] = req or None
            hit_kwargs["id"] = {
                "workflowId": wf_id,
                "workflowName": entity_name,
                "instanceId": inst,
                "requestNo": req,
            }
        elif entity_type == "document":
            repo_id = _str(row_get(row, "repository_id")) or specific_id
            wf_id = _str(row_get(row, "workflow_id"))
            inst = _str(row_get(row, "instance_id"))
            req = _str(row_get(row, "request_no"))
            repo_name = ""
            wf_name = ""
            if repo_id:
                repo_name = await _lookup_name(
                    db,
                    table_candidates=REPO_TABLES,
                    entity_id=repo_id,
                    prefer_schemas=("dbo", "repository", "public"),
                    cache=repo_cache,
                )
            if wf_id:
                wf_name = await _lookup_name(
                    db,
                    table_candidates=WORKFLOW_TABLES,
                    entity_id=wf_id,
                    prefer_schemas=("workflow", "dbo", "public"),
                    cache=wf_cache,
                )
            content_match = _is_content_field(matched_field) or table.lower() in {
                "search_index",
                "searchindex",
            }
            hit_kwargs["matchSource"] = "content" if content_match else "field"
            hit_kwargs["ifileName"] = entity_name
            hit_kwargs["name"] = repo_name or entity_name
            hit_kwargs["requestNo"] = req or None
            hit_kwargs["id"] = {
                "itemId": entity_id,
                "repositoryId": repo_id,
                "repositoryName": repo_name,
                "workflowId": wf_id or 0,
                "workflowName": wf_name,
                "instanceId": inst,
                "requestNo": req,
            }
        elif entity_type == "form":
            wf_id = _str(row_get(row, "workflow_id"))
            inst = _str(row_get(row, "instance_id"))
            req = _str(row_get(row, "request_no"))
            form_id = _str(row_get(row, "form_id"))
            is_workflow = bool(wf_id or inst or req)
            form_kind = "workflow" if is_workflow else "master"
            hit_kwargs["formKind"] = form_kind
            hit_kwargs["name"] = entity_name
            hit_kwargs["requestNo"] = req or None
            if is_workflow:
                wf_name = ""
                if wf_id:
                    wf_name = await _lookup_name(
                        db,
                        table_candidates=WORKFLOW_TABLES,
                        entity_id=wf_id,
                        prefer_schemas=("workflow", "dbo", "public"),
                        cache=wf_cache,
                    )
                hit_kwargs["id"] = {
                    "formId": form_id or "",
                    "formName": entity_name,
                    "formEntryId": entity_id,
                    "workflowId": wf_id or 0,
                    "workflowName": wf_name,
                    "instanceId": inst,
                    "requestNo": req,
                }
            else:
                master_id = form_id or entity_id
                hit_kwargs["id"] = {
                    "formId": form_id or "",
                    "formName": entity_name,
                    "formEntryId": entity_id,
                    "masterFormId": master_id,
                    "masterFormName": entity_name,
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
    """Name search on workflow definitions only (not every instance row)."""
    located = await find_table(db, WORKFLOW_TABLES, prefer_schemas=("workflow", "dbo", "public"))
    if located is None:
        logger.warning("global_search_no_workflow_table")
        return []
    schema, table = located
    return await _search_table(
        db,
        schema=schema,
        table=table,
        query=query,
        entity_type="workflow",
        limit=limit,
        extra_text=("code", "status"),
    )


async def search_document_metadata(
    db: Any,
    query: str,
    *,
    specific_id: str = "",
    workspace_id: str = "",
    limit: int = DEFAULT_LIMIT,
) -> list[SearchHit]:
    _ = workspace_id
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
    repo_cache: dict[str, str] = {}
    wf_cache: dict[str, str] = {}
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
            extra_text=("ifilename", "filename", "description", "ocr_text", "ocrtext"),
            specific_id=specific_id,
            specific_id_keys=(
                ()
                if per_repo_items
                else ("wrepositoryid", "repositoryid", "repository_id")
            ),
            repo_name_cache=repo_cache,
            workflow_name_cache=wf_cache,
        )
        for hit in part:
            if specific_id and hit.id is not None and not hit.id.get("repositoryId"):
                hit.id["repositoryId"] = specific_id
        hits.extend(part)
    return hits[:limit]


async def find_ezfb_tables(db: Any, *, limit: int = 8) -> list[tuple[str, str]]:
    try:
        rows = await db.fetch(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE starts_with(lower(table_name), 'ezfb_')
              AND lower(table_name) LIKE '%_items'
              AND table_type IN ('BASE TABLE', 'TABLE')
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
        schema = _str(row_get(row, "table_schema"))
        name = _str(row_get(row, "table_name"))
        if schema and name and name.lower().endswith("_items"):
            out.append((schema, name))
    return out


async def search_forms(
    db: Any,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[SearchHit]:
    """Search ezfb_*_items form tables; classify workflow vs master."""
    tables = await find_ezfb_tables(db, limit=8)
    if not tables:
        return []
    hits: list[SearchHit] = []
    wf_cache: dict[str, str] = {}
    for schema, table in tables:
        remaining = limit - len(hits)
        if remaining <= 0:
            break
        part = await _search_table(
            db,
            schema=schema,
            table=table,
            query=query,
            entity_type="form",
            limit=remaining,
            extra_text=("requestno", "request_no", "description", "name"),
            workflow_name_cache=wf_cache,
        )
        hits.extend(part)
    return hits[:limit]
