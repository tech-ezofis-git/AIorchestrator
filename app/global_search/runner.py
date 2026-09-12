"""Shared Global Search tool fan-out used by GlobalSearchAgent and ChatbotAgent."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.dispatcher import Dispatcher, ToolExecutionError, ToolNotFoundError
from app.global_search.merge import build_result, merge_document_hits
from app.global_search.sql_search import normalize_query
from app.global_search.types import GlobalSearchResult, SearchHit

logger = logging.getLogger("orchestrator.global_search.runner")


def _as_hits(raw: Any) -> list[SearchHit]:
    if not raw:
        return []
    if isinstance(raw, list):
        out: list[SearchHit] = []
        for item in raw:
            if isinstance(item, SearchHit):
                out.append(item)
            elif isinstance(item, dict):
                out.append(SearchHit.model_validate(item))
        return out
    return []


async def run_global_search(
    dispatcher: Dispatcher,
    *,
    query: str,
    tenant_id: str,
    specific_id: str = "",
    limit: int = 20,
    rag_limit: int = 10,
    include_comments_tickets: bool = False,
) -> GlobalSearchResult:
    """Run GS tools in parallel and return a deduped flat result.

    When ``include_comments_tickets`` is True (Chatbot Phase 2+), also runs
    ``search_comments`` and ``search_tickets``.
    """
    query = normalize_query(query)
    tenant_id = (tenant_id or "").strip()
    specific_id = (specific_id or "").strip()
    if not tenant_id:
        raise ValueError("tenantId is required.")
    if not query:
        raise ValueError("query is required.")

    base_args = {
        "query": query,
        "tenant_id": tenant_id,
        "specific_id": specific_id,
        "limit": limit,
    }
    doc_args = {**base_args}
    rag_args = {**doc_args, "limit": rag_limit}
    form_args = {"query": query, "tenant_id": tenant_id, "limit": limit}
    extra_args = {"query": query, "tenant_id": tenant_id, "limit": limit}

    async def _call(name: str, payload: dict[str, Any]) -> list[SearchHit]:
        try:
            raw = await dispatcher.dispatch(name, payload)
        except (ToolExecutionError, ToolNotFoundError):
            logger.warning("global_search_tool_failed", extra={"tool": name})
            return []
        return _as_hits(raw)

    tasks = [
        _call("search_repo_metadata", doc_args),
        _call("search_repo_rag", rag_args),
        _call("search_repositories", base_args),
        _call("search_workflows", {"query": query, "tenant_id": tenant_id, "limit": limit}),
        _call("search_forms", form_args),
    ]
    if include_comments_tickets:
        tasks.extend(
            [
                _call("search_comments", extra_args),
                _call("search_tickets", extra_args),
            ]
        )

    results = await asyncio.gather(*tasks)
    meta_docs, rag_docs, repos, workflows, forms = results[:5]
    comments: list[SearchHit] = []
    tickets: list[SearchHit] = []
    if include_comments_tickets:
        comments, tickets = results[5], results[6]

    documents = merge_document_hits(meta_docs, rag_docs)
    return build_result(
        query,
        [*documents, *repos, *workflows, *forms, *comments, *tickets],
    )
