"""Shared Global Search tool fan-out used by GlobalSearchAgent and ChatbotAgent."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Sequence

from app.core.dispatcher import Dispatcher, ToolExecutionError, ToolNotFoundError
from app.global_search.merge import build_result, merge_document_hits
from app.global_search.sql_search import normalize_query
from app.global_search.types import GlobalSearchResult, SearchHit

logger = logging.getLogger("orchestrator.global_search.runner")

# Off for now. Set True to show these Global Search sources again.
INCLUDE_REPOSITORY_LIST = False
INCLUDE_WORKFLOW_LIST = False
INCLUDE_MASTER_FORMS = False

_CORE_TOOLS = (
    "search_repo_metadata",
    "search_repo_rag",
    "search_forms",
)
_COMMENT_TICKET_TOOLS = ("search_comments", "search_tickets")


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
    tools: Optional[Sequence[str]] = None,
) -> GlobalSearchResult:
    """Run GS tools in parallel and return a deduped flat result.

    When ``include_comments_tickets`` is True (Chatbot Phase 2+), also runs
    ``search_comments`` and ``search_tickets``. Pass ``tools`` to run an
    explicit subset (Chatbot keyword scope); that list replaces the default.
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
    workflow_args = {"query": query, "tenant_id": tenant_id, "limit": limit}
    args_for = {
        "search_repo_metadata": doc_args,
        "search_repo_rag": rag_args,
        "search_repositories": base_args,
        "search_workflows": workflow_args,
        "search_forms": form_args,
        "search_comments": extra_args,
        "search_tickets": extra_args,
    }

    async def _call(name: str, payload: dict[str, Any]) -> list[SearchHit]:
        try:
            raw = await dispatcher.dispatch(name, payload)
        except (ToolExecutionError, ToolNotFoundError):
            logger.warning("global_search_tool_failed", extra={"tool": name})
            return []
        return _as_hits(raw)

    if tools is None:
        selected = list(_CORE_TOOLS)
        if INCLUDE_REPOSITORY_LIST:
            selected.append("search_repositories")
        if INCLUDE_WORKFLOW_LIST:
            selected.append("search_workflows")
        if include_comments_tickets:
            selected.extend(_COMMENT_TICKET_TOOLS)
    else:
        selected = [name for name in tools if name in args_for]

    results = await asyncio.gather(*[_call(name, args_for[name]) for name in selected])
    by_name = dict(zip(selected, results))
    documents = merge_document_hits(
        by_name.get("search_repo_metadata", []),
        by_name.get("search_repo_rag", []),
    )
    forms = by_name.get("search_forms", [])
    if tools is None and not INCLUDE_MASTER_FORMS:
        forms = [hit for hit in forms if (hit.formKind or "").lower() != "master"]
    return build_result(
        query,
        [
            *documents,
            *by_name.get("search_repositories", []),
            *by_name.get("search_workflows", []),
            *forms,
            *by_name.get("search_comments", []),
            *by_name.get("search_tickets", []),
        ],
    )
