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
_EZFB_TABLE_RE = re.compile(r"^ezfb_(.+)_items$", re.I)
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
FORM_DEF_TABLES = ("wform", "wforms", "forms", "form")
REPO_ITEM_TABLES = ("repositoryitem", "repositoryitems", "repository_item")
DEFAULT_LIMIT = 20
# Per-workflow link tables: item_id → process/instance.
_PROCESS_ADDON_PREFIXES = ("process_addon_", "processaddon_")
_ATTACHMENT_PREFIXES = ("workflow_attachments_", "workflowattachments_")
_LINK_TABLE_LIMIT = 200


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


def _looks_like_guid(value: str) -> bool:
    compact = (value or "").replace("-", "").lower()
    return len(compact) >= 32 and all(c in "0123456789abcdef" for c in compact[:32])


def _form_token_from_table(table: str) -> str:
    match = _EZFB_TABLE_RE.match((table or "").strip())
    return match.group(1) if match else ""


async def _lookup_form_meta(
    db: Any, *, table_name: str, cache: dict[str, dict[str, str]]
) -> dict[str, str]:
    """Resolve form definition id + display name from ezfb_{token}_items table name."""
    token = _form_token_from_table(table_name)
    if not token:
        return {"formId": "", "formName": ""}
    key = token.lower()
    if key in cache:
        return cache[key]
    located = await find_table(db, FORM_DEF_TABLES, prefer_schemas=("dbo", "public", "form"))
    empty = {"formId": "", "formName": ""}
    if located is None:
        cache[key] = empty
        return empty
    schema, table = located
    try:
        by_lower, _ = await fetch_columns(db, schema, table)
    except Exception:
        cache[key] = empty
        return empty
    id_col = pick_id_column(by_lower) or by_lower.get("id") or by_lower.get("wformid")
    name_col = pick_name_column(by_lower) or by_lower.get("name") or by_lower.get("title")
    if not id_col or not name_col:
        cache[key] = empty
        return empty
    compact_token = token.replace("-", "").lower()
    sql = (
        f"SELECT CAST({quote_ident(id_col)} AS text) AS form_id, "
        f"CAST({quote_ident(name_col)} AS text) AS form_name "
        f"FROM {qualified(schema, table)} "
        f"WHERE lower(replace(CAST({quote_ident(id_col)} AS text), '-', '')) "
        f"LIKE $1 || '%' "
        f"OR lower(CAST({quote_ident(id_col)} AS text)) = lower($2) "
        f"LIMIT 1"
    )
    try:
        row = await db.fetchrow(sql, compact_token, token)
    except Exception:
        try:
            rows = await db.fetch(sql, compact_token, token)
            row = rows[0] if rows else None
        except Exception:
            row = None
    meta = {
        "formId": _str(row_get(row, "form_id")) if row is not None else "",
        "formName": _str(row_get(row, "form_name")) if row is not None else "",
    }
    cache[key] = meta
    return meta


async def _find_tables_by_prefixes(
    db: Any,
    prefixes: tuple[str, ...],
    *,
    limit: int = _LINK_TABLE_LIMIT,
) -> list[tuple[str, str]]:
    """List tenant tables whose names start with any of the given prefixes."""
    if not prefixes:
        return []
    # Prefer starts_with (PG14+); fall back to LIKE for older engines.
    starts = " OR ".join(
        f"starts_with(lower(table_name), '{p}')" for p in prefixes
    )
    likes = " OR ".join(f"lower(table_name) LIKE '{p}%'" for p in prefixes)
    for where_expr in (starts, likes):
        try:
            rows = await db.fetch(
                f"""
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE ({where_expr})
                  AND table_type IN ('BASE TABLE', 'TABLE')
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY CASE
                    WHEN lower(table_schema) = 'workflow' THEN 0
                    WHEN table_schema = 'dbo' THEN 1
                    ELSE 2
                END, table_name
                LIMIT $1
                """,
                limit,
            )
        except Exception:
            rows = None
        if rows is None:
            continue
        out: list[tuple[str, str]] = []
        for row in rows or []:
            schema = _str(row_get(row, "table_schema"))
            name = _str(row_get(row, "table_name"))
            if schema and name:
                out.append((schema, name))
        return out
    return []


def _table_suffix(table_name: str, prefixes: tuple[str, ...]) -> str:
    lower = (table_name or "").lower()
    for prefix in prefixes:
        if lower.startswith(prefix):
            return table_name[len(prefix) :]
    return ""


def _link_kind(table_name: str) -> str:
    lower = (table_name or "").lower()
    if any(lower.startswith(p) for p in _PROCESS_ADDON_PREFIXES):
        return "process_addon"
    if any(lower.startswith(p) for p in _ATTACHMENT_PREFIXES):
        return "attachment"
    return ""


async def _lookup_instance_meta(
    db: Any,
    *,
    suffix: str,
    process_id: str,
    cache: dict[str, dict[str, str]],
) -> dict[str, str]:
    """Read workflow_id / workflow_name / reference_number from workflow_instances_{suffix}."""
    empty = {"workflowId": "", "workflowName": "", "requestNo": ""}
    compact = process_id.replace("-", "").lower()
    if not suffix or not compact:
        return empty
    cache_key = f"{suffix}:{compact}"
    if cache_key in cache:
        return cache[cache_key]
    candidates = (f"workflow_instances_{suffix}", f"workflowinstances_{suffix}")
    located = await find_table(
        db, candidates, prefer_schemas=("workflow", "dbo", "public")
    )
    if located is None:
        cache[cache_key] = empty
        return empty
    schema, table = located
    try:
        by_lower, _ = await fetch_columns(db, schema, table)
    except Exception:
        cache[cache_key] = empty
        return empty
    id_col = pick_id_column(by_lower) or by_lower.get("id")
    if not id_col:
        cache[cache_key] = empty
        return empty
    wf_col = _pick_col(by_lower, "workflowid", "workflow_id", "wworkflowid")
    name_col = _pick_col(by_lower, "workflowname", "workflow_name", "name")
    req_col = _pick_col(
        by_lower,
        "referencenumber",
        "reference_number",
        "requestno",
        "request_no",
        "requestnumber",
    )
    parts = [f"CAST({quote_ident(id_col)} AS text) AS instance_id"]
    if wf_col:
        parts.append(f"CAST({quote_ident(wf_col)} AS text) AS workflow_id")
    if name_col:
        parts.append(f"CAST({quote_ident(name_col)} AS text) AS workflow_name")
    if req_col:
        parts.append(f"CAST({quote_ident(req_col)} AS text) AS request_no")
    sql = (
        f"SELECT {', '.join(parts)} FROM {qualified(schema, table)} "
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
    meta = {
        "workflowId": _str(row_get(row, "workflow_id")) if row is not None else "",
        "workflowName": _str(row_get(row, "workflow_name")) if row is not None else "",
        "requestNo": _str(row_get(row, "request_no")) if row is not None else "",
    }
    cache[cache_key] = meta
    return meta


async def _fetchrow_safe(db: Any, sql: str, *args: Any) -> Any:
    try:
        return await db.fetchrow(sql, *args)
    except Exception:
        try:
            rows = await db.fetch(sql, *args)
            return rows[0] if rows else None
        except Exception:
            return None


async def _query_link_table(
    db: Any,
    *,
    schema: str,
    table: str,
    item_id: str = "",
    file_name: str = "",
    repository_id: str = "",
) -> tuple[str, str]:
    """Return (process_id, suffix) from one process_addon / workflow_attachments table."""
    kind = _link_kind(table)
    if not kind:
        return "", ""
    try:
        by_lower, _ = await fetch_columns(db, schema, table)
    except Exception:
        return "", ""
    item_col = _pick_col(by_lower, "itemid", "item_id", "repositoryitemid")
    if kind == "process_addon":
        process_col = _pick_col(
            by_lower,
            "processid",
            "process_id",
            "instanceid",
            "instance_id",
            "winstanceid",
            "workflow_instance_id",
            "workflowinstanceid",
        )
        prefixes = _PROCESS_ADDON_PREFIXES
    else:
        process_col = _pick_col(
            by_lower,
            "workflow_instance_id",
            "workflowinstanceid",
            "processid",
            "process_id",
            "instanceid",
            "instance_id",
        )
        prefixes = _ATTACHMENT_PREFIXES
    file_col = _pick_col(by_lower, "file_name", "filename", "ifilename")
    repo_col = _pick_col(by_lower, "repository_id", "repositoryid", "wrepositoryid")
    if not process_col:
        return "", ""

    deleted = pick_deleted_column(by_lower)
    deleted_sql = ""
    if deleted:
        qdel = quote_ident(deleted)
        deleted_sql = (
            f" AND lower(COALESCE(CAST({qdel} AS text), '0'))"
            f" NOT IN ('1', 'true', 't', 'yes', 'y')"
        )
    order_col = _pick_col(
        by_lower, "id", "createdat", "created_at", "created_at_utc", "modified_at_utc"
    )
    order_sql = f"ORDER BY {quote_ident(order_col)} DESC" if order_col else ""
    select_sql = f"CAST({quote_ident(process_col)} AS text) AS process_id"

    attempts: list[tuple[str, list[Any]]] = []
    compact_item = item_id.replace("-", "").lower() if item_id else ""
    if item_col and compact_item:
        # Prefer native uuid equality when the value looks like a GUID.
        if _looks_like_guid(item_id):
            attempts.append(
                (
                    f"SELECT {select_sql} FROM {qualified(schema, table)} "
                    f"WHERE {quote_ident(item_col)} = $1::uuid{deleted_sql} "
                    f"{order_sql} LIMIT 1",
                    [item_id],
                )
            )
        attempts.append(
            (
                f"SELECT {select_sql} FROM {qualified(schema, table)} "
                f"WHERE lower(replace(CAST({quote_ident(item_col)} AS text), '-', '')) = $1"
                f"{deleted_sql} {order_sql} LIMIT 1",
                [compact_item],
            )
        )
    # Many items_* rows share a file name; process_addon stores the attached copy.
    fname = _str(file_name)
    if file_col and fname:
        args: list[Any] = [fname]
        where = f"lower(CAST({quote_ident(file_col)} AS text)) = lower($1)"
        if repo_col and repository_id and _looks_like_guid(repository_id):
            args.append(repository_id)
            where += f" AND {quote_ident(repo_col)} = $2::uuid"
        elif repo_col and repository_id:
            args.append(repository_id.replace("-", "").lower())
            where += (
                f" AND lower(replace(CAST({quote_ident(repo_col)} AS text), '-', ''))"
                f" = ${len(args)}"
            )
        attempts.append(
            (
                f"SELECT {select_sql} FROM {qualified(schema, table)} "
                f"WHERE {where}{deleted_sql} {order_sql} LIMIT 1",
                args,
            )
        )

    for sql, args in attempts:
        row = await _fetchrow_safe(db, sql, *args)
        process_id = _str(row_get(row, "process_id")) if row is not None else ""
        if process_id:
            return process_id, _table_suffix(table, prefixes)
    return "", ""


async def _hydrate_from_link_tables(
    db: Any,
    *,
    item_id: str,
    workflow_name_cache: dict[str, str],
    file_name: str = "",
    repository_id: str = "",
    link_tables_cache: Optional[list[tuple[str, str]]] = None,
    instance_meta_cache: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, str]:
    """Resolve ticket via process_addon_* / workflow_attachments_* (item_id or file_name)."""
    out = {"workflowId": "", "workflowName": "", "instanceId": "", "requestNo": ""}
    if not item_id and not file_name:
        return out
    if link_tables_cache is not None:
        tables = link_tables_cache
    else:
        tables = await _find_tables_by_prefixes(
            db,
            _PROCESS_ADDON_PREFIXES + _ATTACHMENT_PREFIXES,
            limit=_LINK_TABLE_LIMIT,
        )
    if not tables:
        logger.info("global_search_no_link_tables")
        return out
    inst_cache = instance_meta_cache if instance_meta_cache is not None else {}

    # Prefer process_addon tables first (explicit item→process link), then attachments.
    ordered = sorted(
        tables,
        key=lambda t: (0 if _link_kind(t[1]) == "process_addon" else 1, t[1].lower()),
    )
    for schema, table in ordered:
        process_id, suffix = await _query_link_table(
            db,
            schema=schema,
            table=table,
            item_id=item_id,
            file_name=file_name,
            repository_id=repository_id,
        )
        if not process_id:
            continue
        out["instanceId"] = process_id
        meta = await _lookup_instance_meta(
            db, suffix=suffix, process_id=process_id, cache=inst_cache
        )
        out["workflowId"] = meta.get("workflowId") or ""
        out["workflowName"] = meta.get("workflowName") or ""
        out["requestNo"] = meta.get("requestNo") or ""
        if not out["workflowId"] and suffix:
            out["workflowId"] = await _lookup_workflow_id_by_suffix(db, suffix)
        if out["workflowId"] and not out["workflowName"]:
            out["workflowName"] = await _lookup_name(
                db,
                table_candidates=WORKFLOW_TABLES,
                entity_id=out["workflowId"],
                prefer_schemas=("workflow", "dbo", "public"),
                cache=workflow_name_cache,
            )
        logger.info(
            "global_search_ticket_hydrated",
            extra={
                "table": table,
                "item_id": item_id[:36] if item_id else "",
                "has_workflow": bool(out["workflowId"]),
            },
        )
        return out
    return out


async def _lookup_workflow_id_by_suffix(db: Any, suffix: str) -> str:
    token = (suffix or "").replace("-", "").lower()
    if len(token) < 8:
        return ""
    located = await find_table(
        db, WORKFLOW_TABLES, prefer_schemas=("workflow", "dbo", "public")
    )
    if located is None:
        return ""
    schema, table = located
    try:
        by_lower, _ = await fetch_columns(db, schema, table)
    except Exception:
        return ""
    id_col = pick_id_column(by_lower)
    if not id_col:
        return ""
    sql = (
        f"SELECT CAST({quote_ident(id_col)} AS text) AS workflow_id "
        f"FROM {qualified(schema, table)} "
        f"WHERE lower(replace(CAST({quote_ident(id_col)} AS text), '-', '')) "
        f"LIKE $1 || '%' LIMIT 1"
    )
    row = await _fetchrow_safe(db, sql, token[:8])
    return _str(row_get(row, "workflow_id")) if row is not None else ""


async def _hydrate_ticket_for_item(
    db: Any,
    *,
    item_id: str,
    workflow_name_cache: dict[str, str],
    file_name: str = "",
    repository_id: str = "",
    link_tables_cache: Optional[list[tuple[str, str]]] = None,
    instance_meta_cache: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, str]:
    """Fill workflow/instance/requestNo for a repository item from ticket tables."""
    out = {"workflowId": "", "workflowName": "", "instanceId": "", "requestNo": ""}
    compact = item_id.replace("-", "").lower()
    if not compact and not file_name:
        return out

    # Preferred: process_addon / workflow_attachments link item (or file) → process.
    addon = await _hydrate_from_link_tables(
        db,
        item_id=item_id,
        workflow_name_cache=workflow_name_cache,
        file_name=file_name,
        repository_id=repository_id,
        link_tables_cache=link_tables_cache,
        instance_meta_cache=instance_meta_cache,
    )
    if addon.get("instanceId") or addon.get("workflowId"):
        return addon

    async def _scan(table_names: tuple[str, ...], prefer: tuple[str, ...]) -> Optional[Any]:
        located = await find_table(db, table_names, prefer_schemas=prefer)
        if located is None:
            return None
        schema, table = located
        try:
            by_lower, _ = await fetch_columns(db, schema, table)
        except Exception:
            return None
        id_keys = (
            "itemid",
            "item_id",
            "repositoryitemid",
            "ifileid",
            "id",
        )
        item_col = next((by_lower[k] for k in id_keys if k in by_lower), None)
        if not item_col or not compact:
            return None
        select_parts = [
            f"CAST({quote_ident(item_col)} AS text) AS item_id",
        ]
        wf_col = _pick_col(by_lower, "wworkflowid", "workflowid", "workflow_id")
        inst_col = _pick_col(
            by_lower, "instanceid", "instance_id", "winstanceid", "workflowinstanceid"
        )
        req_col = _pick_col(
            by_lower, "requestno", "request_no", "requestnumber", "request_number"
        )
        if wf_col:
            select_parts.append(f"CAST({quote_ident(wf_col)} AS text) AS workflow_id")
        if inst_col:
            select_parts.append(f"CAST({quote_ident(inst_col)} AS text) AS instance_id")
        if req_col:
            select_parts.append(f"CAST({quote_ident(req_col)} AS text) AS request_no")
        if len(select_parts) == 1:
            return None
        sql = (
            f"SELECT {', '.join(select_parts)} FROM {qualified(schema, table)} "
            f"WHERE lower(replace(CAST({quote_ident(item_col)} AS text), '-', '')) = $1 "
            f"LIMIT 1"
        )
        return await _fetchrow_safe(db, sql, compact)

    row = await _scan(REPO_ITEM_TABLES, ("dbo", "repository", "public"))
    if row is None:
        row = await _scan(WORKFLOW_INSTANCE_TABLES, ("workflow", "dbo", "public"))
    if row is None:
        return out
    out["workflowId"] = _str(row_get(row, "workflow_id"))
    out["instanceId"] = _str(row_get(row, "instance_id"))
    out["requestNo"] = _str(row_get(row, "request_no"))
    if out["workflowId"]:
        out["workflowName"] = await _lookup_name(
            db,
            table_candidates=WORKFLOW_TABLES,
            entity_id=out["workflowId"],
            prefer_schemas=("workflow", "dbo", "public"),
            cache=workflow_name_cache,
        )
    return out


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
    form_meta_cache: Optional[dict[str, dict[str, str]]] = None,
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
    wf_col = _pick_col(by_lower, "wworkflowid", "workflowid", "workflow_id", "iworkflowid")
    inst_col = _pick_col(
        by_lower, "instanceid", "instance_id", "winstanceid", "workflowinstanceid"
    )
    req_col = _pick_col(
        by_lower, "requestno", "request_no", "requestnumber", "request_number"
    )
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
    form_cache = form_meta_cache if form_meta_cache is not None else {}
    link_tables: Optional[list[tuple[str, str]]] = None
    instance_meta_cache: dict[str, dict[str, str]] = {}
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
            wf_name = ""
            if not (wf_id and inst and req):
                if link_tables is None:
                    link_tables = await _find_tables_by_prefixes(
                        db,
                        _PROCESS_ADDON_PREFIXES + _ATTACHMENT_PREFIXES,
                        limit=_LINK_TABLE_LIMIT,
                    )
                ticket = await _hydrate_ticket_for_item(
                    db,
                    item_id=entity_id,
                    workflow_name_cache=wf_cache,
                    file_name=entity_name,
                    repository_id=repo_id,
                    link_tables_cache=link_tables,
                    instance_meta_cache=instance_meta_cache,
                )
                wf_id = wf_id or ticket["workflowId"]
                inst = inst or ticket["instanceId"]
                req = req or ticket["requestNo"]
                wf_name = ticket.get("workflowName") or ""
            repo_name = ""
            if repo_id:
                repo_name = await _lookup_name(
                    db,
                    table_candidates=REPO_TABLES,
                    entity_id=repo_id,
                    prefer_schemas=("dbo", "repository", "public"),
                    cache=repo_cache,
                )
            if wf_id and not wf_name:
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
                "workflowId": int(wf_id) if str(wf_id).isdigit() else (wf_id or 0),
                "workflowName": wf_name,
                "instanceId": inst,
                "requestNo": req,
            }
        elif entity_type == "form":
            wf_id = _str(row_get(row, "workflow_id"))
            inst = _str(row_get(row, "instance_id"))
            req = _str(row_get(row, "request_no"))
            form_meta = await _lookup_form_meta(db, table_name=table, cache=form_cache)
            form_id = _str(row_get(row, "form_id")) or form_meta.get("formId") or ""
            form_name = form_meta.get("formName") or ""
            # Never present the row GUID as the form/master display name.
            if not form_name or _looks_like_guid(form_name) or form_name == entity_id:
                form_name = ""
            display_name = form_name or (entity_name if not _looks_like_guid(entity_name) else "")
            is_workflow = bool(wf_id or inst or req)
            form_kind = "workflow" if is_workflow else "master"
            hit_kwargs["formKind"] = form_kind
            hit_kwargs["entity_name"] = display_name or entity_id
            hit_kwargs["name"] = display_name or entity_id
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
                    "formId": form_id,
                    "formName": display_name or form_name,
                    "formEntryId": entity_id,
                    "workflowId": int(wf_id) if str(wf_id).isdigit() else (wf_id or 0),
                    "workflowName": wf_name,
                    "instanceId": inst,
                    "requestNo": req,
                }
            else:
                hit_kwargs["id"] = {
                    "formId": form_id,
                    "formName": display_name or form_name,
                    "formEntryId": entity_id,
                    "masterFormId": form_id,
                    "masterFormName": display_name or form_name,
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
    form_cache: dict[str, dict[str, str]] = {}
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
            form_meta_cache=form_cache,
        )
        hits.extend(part)
    return hits[:limit]
