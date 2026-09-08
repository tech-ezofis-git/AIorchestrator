"""Map HybridSearch chunks onto SEARCH_API.md document cards."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.global_search.types import SearchHit
from app.knowledge.hybrid_search import HybridSearch
from app.knowledge.vector_store import VectorStore

logger = logging.getLogger("orchestrator.global_search")


def _meta_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _pick(meta: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = meta.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


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
    results = await hybrid_search.search(query, top_n=top_n, query_embedding=query_embedding)
    doc_ids = list({str(r.chunk.document_id) for r in results})
    docs = await vector_store.get_documents(doc_ids)
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for scored in results:
        doc = docs.get(str(scored.chunk.document_id))
        extra = {}
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
        ws = workspace_id or _pick(extra, "workspaceId", "workspace_id")
        snippet = (scored.chunk.text or "").strip()
        if len(snippet) > 180:
            snippet = snippet[:177] + "..."
        hits.append(
            SearchHit(
                entity_type="document",
                entity_id=entity_id,
                entity_name=ifile,
                matched_field="content",
                matched_value=snippet,
                description=snippet or None,
                matchSource="content",
                ifileName=ifile,
                name=repo_name or ifile,
                metadata={"chunk_id": scored.chunk.id},
                id={
                    "workspaceId": ws or "",
                    "repositoryId": repo_id or specific_id or "",
                    "repositoryName": repo_name,
                    "itemId": item_id or entity_id,
                    "workflowId": 0,
                    "processId": 0,
                },
            )
        )
    return hits
