"""Global Search agent — four Dispatcher tools in parallel, grouped cards.

Does not replace the RAG Search agent (intent=search cited answers).
This agent returns grouped repository / workflow / document cards.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.core.dispatcher import Dispatcher, ToolExecutionError, ToolNotFoundError
from app.global_search.merge import build_result, merge_document_hits, status_reply
from app.global_search.sql_search import normalize_query
from app.global_search.types import SearchHit

logger = logging.getLogger("orchestrator.global_search_agent")

_SUCCESS_EMPTY = "No matches."


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


class GlobalSearchAgent:
    def __init__(self, dispatcher: Dispatcher, *, limit: int = 20, rag_limit: int = 10):
        self._dispatcher = dispatcher
        self._limit = limit
        self._rag_limit = rag_limit

    async def handle(
        self,
        *,
        session_id: str,
        message: str,
        history: list[dict[str, str]],
        document_job: Optional[dict[str, Any]] = None,
        **_: Any,
    ) -> dict:
        job = document_job or {}
        tenant_id = str(job.get("tenant_id") or "").strip()
        if not tenant_id:
            raise ValueError("tenantId is required for intent=global_search.")
        query = normalize_query(str(job.get("query") or message or ""))
        if not query:
            raise ValueError("query is required for intent=global_search.")
        specific_id = str(job.get("specific_id") or job.get("repository_id") or "").strip()
        workspace_id = str(job.get("workspace_id") or "").strip()
        action_from = str(job.get("action_from") or "").strip()
        locked_repo = action_from.lower() == "repository" and bool(specific_id)

        repo_args = {
            "query": query,
            "tenant_id": tenant_id,
            "specific_id": specific_id,
            "limit": self._limit,
        }
        doc_args = {
            **repo_args,
            "workspace_id": workspace_id,
        }
        rag_args = {**doc_args, "limit": self._rag_limit}

        async def _call(name: str, payload: dict[str, Any]) -> list[SearchHit]:
            try:
                raw = await self._dispatcher.dispatch(name, payload)
            except (ToolExecutionError, ToolNotFoundError):
                logger.warning("global_search_tool_failed", extra={"tool": name})
                return []
            return _as_hits(raw)

        async def _empty() -> list[SearchHit]:
            return []

        tasks: list[Any] = [
            _call("search_repo_metadata", doc_args),
            _call("search_repo_rag", rag_args),
        ]
        if not locked_repo:
            tasks.append(_call("search_repositories", repo_args))
            tasks.append(_call("search_workflows", {"query": query, "tenant_id": tenant_id, "limit": self._limit}))
        else:
            tasks.append(_empty())
            tasks.append(_empty())

        meta_docs, rag_docs, repos, workflows = await asyncio.gather(*tasks)
        documents = merge_document_hits(meta_docs, rag_docs)
        result = build_result(
            query,
            repositories=repos,
            workflows=workflows,
            documents=documents,
        )
        payload = result.model_dump()
        return {
            "reply": status_reply(result) or _SUCCESS_EMPTY,
            "usage": None,
            "global_search_result": payload,
        }
