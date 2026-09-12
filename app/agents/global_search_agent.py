"""Global Search agent — parallel Dispatcher tools, flat hits[]."""
from __future__ import annotations

from typing import Any, Optional

from app.core.dispatcher import Dispatcher
from app.global_search.merge import status_reply
from app.global_search.runner import run_global_search

_SUCCESS_EMPTY = "No matches."


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
        query = str(job.get("query") or message or "").strip()
        if not query:
            raise ValueError("query is required for intent=global_search.")
        specific_id = str(job.get("specific_id") or job.get("repository_id") or "").strip()

        result = await run_global_search(
            self._dispatcher,
            query=query,
            tenant_id=tenant_id,
            specific_id=specific_id,
            limit=self._limit,
            rag_limit=self._rag_limit,
        )
        return {
            "reply": status_reply(result) or _SUCCESS_EMPTY,
            "usage": None,
            "global_search_result": result.model_dump(),
        }
