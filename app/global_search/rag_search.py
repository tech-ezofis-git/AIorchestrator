"""Map HybridSearch chunks onto flat Global Search document hits."""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.data_import.ident import quote_ident
from app.global_search.schema import (
    fetch_columns,
    find_items_tables_for_repository,
    find_table,
    pick_deleted_column,
    qualified,
    row_get,
)
from app.global_search.types import SearchHit
from app.knowledge.hybrid_search import HybridSearch
from app.knowledge.vector_store import VectorStore

logger = logging.getLogger("orchestrator.global_search")

_DELETED_TRUE = frozenset({"1", "true", "t", "yes", "y"})
_ITEM_ID_KEYS = ("id", "itemid", "item_id", "repositoryitemid", "ifileid")
_STATUS_LIVE = "live"
_STATUS_DELETED = "deleted"
_STATUS_MISSING = "missing"
_STATUS_UNKNOWN = "unknown"


def _items_table_name(repository_id: str) -> str:
    compact = (repository_id or "").replace("-", "").lower()
    return f"items_{compact[:8]}" if len(compact) >= 8 else ""


async def _row_deleted_flag(
    db: Any,
    schema: str,
    table: str,
    compact: str,
    *,
    id_keys: tuple[str, ...] = _ITEM_ID_KEYS,
) -> Optional[bool]:
    """True deleted or missing, False live, None when this table could not be checked."""
    try:
        by_lower, _types = await fetch_columns(db, schema, table)
    except Exception:
        return None
    item_col = next((by_lower[key] for key in id_keys if key in by_lower), None)
    if not item_col:
        return None
    deleted_col = pick_deleted_column(by_lower)
    deleted_sql = (
        f"lower(COALESCE(CAST({quote_ident(deleted_col)} AS text), '0'))"
        if deleted_col
        else "'0'"
    )
    sql = (
        f"SELECT {deleted_sql} AS deleted "
        f"FROM {qualified(schema, table)} "
        f"WHERE lower(replace(CAST({quote_ident(item_col)} AS text), '-', '')) = $1 "
        f"LIMIT 1"
    )
    try:
        row = await db.fetchrow(sql, compact)
    except Exception:
        logger.warning(
            "rag_deleted_lookup_failed",
            extra={"table": f"{schema}.{table}"},
        )
        return None
    if row is None:
        return True
    flag = str(row_get(row, "deleted") or "0").strip().lower()
    return flag in _DELETED_TRUE


async def _items_id_present(db: Any, schema: str, table: str, compact: str) -> Optional[bool]:
    """True when `id` matches, False when the table has no such row, None on error."""
    sql = (
        f"SELECT 1 AS present FROM {qualified(schema, table)} "
        f"WHERE lower(replace(CAST({quote_ident('id')} AS text), '-', '')) = $1 "
        f"LIMIT 1"
    )
    try:
        row = await db.fetchrow(sql, compact)
    except Exception as exc:
        logger.warning(
            "rag_items_id_lookup_failed",
            extra={"table": f"{schema}.{table}", "error": str(exc)[:200]},
        )
        return None
    return row is not None


async def _item_status(db: Any, *, item_id: str, repository_id: str = "") -> str:
    """live | deleted | missing | unknown for one repository item id."""
    compact = (item_id or "").replace("-", "").lower()
    if not compact or db is None:
        return _STATUS_UNKNOWN
    repo_tables: list[tuple[str, str]] = []
    if repository_id:
        table_name = _items_table_name(repository_id)
        if table_name:
            present = await _items_id_present(db, "repository", table_name, compact)
            if present is False:
                logger.info(
                    "rag_hit_missing_items_row",
                    extra={"table": f"repository.{table_name}", "item_id": item_id},
                )
                return _STATUS_MISSING
            if present is True:
                flag = await _row_deleted_flag(db, "repository", table_name, compact)
                if flag is True:
                    return _STATUS_DELETED
                if flag is False:
                    return _STATUS_LIVE
                return _STATUS_LIVE
        for located in await find_items_tables_for_repository(db, repository_id):
            if located not in repo_tables:
                repo_tables.append(located)
    checked = False
    for schema, table in repo_tables:
        flag = await _row_deleted_flag(db, schema, table, compact)
        if flag is None:
            continue
        checked = True
        if flag is False:
            return _STATUS_LIVE
    if checked:
        return _STATUS_MISSING
    located = await find_table(
        db,
        ("repositoryitem", "repositoryitems", "repository_item"),
        prefer_schemas=("dbo", "repository", "public"),
    )
    if located is None:
        return _STATUS_MISSING if repository_id else _STATUS_UNKNOWN
    flag = await _row_deleted_flag(db, located[0], located[1], compact)
    if flag is True:
        return _STATUS_DELETED
    if flag is False:
        return _STATUS_LIVE
    return _STATUS_MISSING if repository_id else _STATUS_UNKNOWN


async def item_marked_deleted(db: Any, *, item_id: str, repository_id: str = "") -> bool:
    """True when the live repository row exists and is flagged deleted."""
    return await _item_status(db, item_id=item_id, repository_id=repository_id) == _STATUS_DELETED


async def item_is_live(db: Any, *, item_id: str, repository_id: str = "") -> bool:
    """True only when a non-deleted repository row exists for this item."""
    return await _item_status(db, item_id=item_id, repository_id=repository_id) == _STATUS_LIVE


async def drop_deleted_document_hits(db: Any, hits: list[SearchHit]) -> list[SearchHit]:
    """Keep RAG hits that still have a live (not deleted) repository row.

    Stale vector chunks outlive EZOFIS deletes. Missing rows, unknown ids,
    and unverified lookups are dropped so Chatbot / Global Search do not
    show deleted files.
    """
    if not hits:
        return hits
    if db is None:
        logger.warning("rag_deleted_filter_skipped_no_db", extra={"hit_count": len(hits)})
        return []
    kept: list[SearchHit] = []
    cache: dict[tuple[str, str], bool] = {}
    for hit in hits:
        ids = hit.id if isinstance(hit.id, dict) else {}
        item_id = str(ids.get("itemId") or hit.entity_id or "")
        repository_id = str(ids.get("repositoryId") or "")
        key = (item_id.replace("-", "").lower(), repository_id.replace("-", "").lower())
        try:
            live = cache.get(key)
            if live is None:
                live = await item_is_live(db, item_id=item_id, repository_id=repository_id)
                cache[key] = live
        except Exception:
            logger.warning("rag_deleted_filter_failed", extra={"entity_id": hit.entity_id})
            continue
        if not live:
            logger.info(
                "rag_hit_dropped_deleted",
                extra={"item_id": item_id, "repository_id": repository_id},
            )
            continue
        kept.append(hit)
    return kept


def _pick(meta: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = meta.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _snippet(text: str, limit: int = 180) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 3] + "..."


async def search_documents_rag(
    hybrid_search: HybridSearch,
    vector_store: VectorStore,
    query: str,
    *,
    top_n: int,
    specific_id: str = "",
    workspace_id: str = "",
    query_embedding: Optional[list[float]] = None,
) -> list[SearchHit]:
    _ = workspace_id
    results = await hybrid_search.search(query, top_n=top_n, query_embedding=query_embedding)
    doc_ids = list({str(r.chunk.document_id) for r in results})
    docs = await vector_store.get_documents(doc_ids)
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for scored in results:
        doc = docs.get(str(scored.chunk.document_id))
        extra: dict[str, Any] = {}
        title = None
        if doc is not None:
            extra = dict(doc.metadata.extra or {})
            title = doc.metadata.title
        item_id = _pick(extra, "itemId", "item_id", "Id", "id")
        repo_id = _pick(extra, "repositoryId", "repository_id")
        if specific_id and repo_id:
            a = specific_id.replace("-", "").lower()
            b = repo_id.replace("-", "").lower()
            if a and b and a != b:
                continue
        entity_id = item_id or str(scored.chunk.document_id)
        key = entity_id.replace("-", "").lower()
        if key in seen:
            continue
        seen.add(key)
        ifile = _pick(extra, "ifileName", "fileName", "filename") or (title or "") or entity_id
        repo_name = _pick(extra, "repositoryName", "repository_name")
        wf_id = _pick(extra, "workflowId", "workflow_id")
        wf_name = _pick(extra, "workflowName", "workflow_name")
        inst = _pick(extra, "instanceId", "instance_id")
        req = _pick(extra, "requestNo", "request_no")
        description = _pick(extra, "description") or ""
        modified = _pick(extra, "modifiedDateandtime", "modifiedAt", "modified_at")
        created = _pick(extra, "dateandtime", "createdAt", "created_at")
        snippet = _snippet(scored.chunk.text or "")
        hits.append(
            SearchHit(
                type="document",
                entity_type="document",
                entity_id=entity_id,
                entity_name=ifile,
                matched_field="content",
                matched_value=snippet,
                description=description,
                modifiedDateandtime=modified,
                dateandtime=created,
                matchSource="content",
                ifileName=ifile,
                name=repo_name or ifile,
                requestNo=req or None,
                metadata={"chunk_id": scored.chunk.id},
                id={
                    "itemId": item_id or entity_id,
                    "repositoryId": repo_id or specific_id or "",
                    "repositoryName": repo_name,
                    "workflowId": wf_id or 0,
                    "workflowName": wf_name,
                    "instanceId": inst,
                    "requestNo": req,
                },
            )
        )
    return hits
