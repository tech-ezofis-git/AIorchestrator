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
_ITEM_ID_KEYS = ("itemid", "item_id", "repositoryitemid", "ifileid", "id")


async def item_marked_deleted(db: Any, *, item_id: str, repository_id: str = "") -> bool:
    """True only when the live repository row exists and is flagged deleted.

    A missing row is left in the results. The items table name varies by
    repository, and hiding every unresolved id would drop live files.
    """
    compact = (item_id or "").replace("-", "").lower()
    if not compact or db is None:
        return False
    tables: list[tuple[str, str]] = []
    if repository_id:
        tables.extend(await find_items_tables_for_repository(db, repository_id))
    located = await find_table(
        db,
        ("repositoryitem", "repositoryitems", "repository_item"),
        prefer_schemas=("dbo", "repository", "public"),
    )
    if located and located not in tables:
        tables.append(located)
    for schema, table in tables:
        try:
            by_lower, _types = await fetch_columns(db, schema, table)
        except Exception:
            continue
        item_col = next((by_lower[key] for key in _ITEM_ID_KEYS if key in by_lower), None)
        deleted_col = pick_deleted_column(by_lower)
        if not item_col or not deleted_col:
            continue
        sql = (
            f"SELECT lower(COALESCE(CAST({quote_ident(deleted_col)} AS text), '0')) AS deleted "
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
            continue
        if row is None:
            continue
        flag = str(row_get(row, "deleted") or "0").strip().lower()
        return flag in _DELETED_TRUE
    return False


async def drop_deleted_document_hits(db: Any, hits: list[SearchHit]) -> list[SearchHit]:
    if db is None or not hits:
        return hits
    kept: list[SearchHit] = []
    for hit in hits:
        ids = hit.id if isinstance(hit.id, dict) else {}
        try:
            deleted = await item_marked_deleted(
                db,
                item_id=str(ids.get("itemId") or hit.entity_id or ""),
                repository_id=str(ids.get("repositoryId") or ""),
            )
        except Exception:
            logger.warning("rag_deleted_filter_failed", extra={"entity_id": hit.entity_id})
            kept.append(hit)
            continue
        if deleted:
            logger.info(
                "rag_hit_dropped_deleted",
                extra={"item_id": ids.get("itemId") or hit.entity_id},
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
