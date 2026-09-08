"""Global Search Dispatcher tools — agent always calls these; LLM does not choose them."""
from typing import Any, Optional

from app.global_search.rag_search import search_documents_rag
from app.global_search.sql_search import (
    search_document_metadata,
    search_repositories,
    search_workflows,
)
from app.knowledge.hybrid_search import HybridSearch
from app.knowledge.vector_store import VectorStore
from app.models.tool_schema import ToolSchema

SEARCH_REPOSITORIES_SCHEMA = ToolSchema(
    name="search_repositories",
    description="Quick metadata search for EZOFIS repository libraries by name/code.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "tenant_id": {"type": "string"},
            "specific_id": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query", "tenant_id"],
    },
)

SEARCH_WORKFLOWS_SCHEMA = ToolSchema(
    name="search_workflows",
    description="Quick metadata search for EZOFIS workflow definitions by name/status.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "tenant_id": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query", "tenant_id"],
    },
)

SEARCH_REPO_METADATA_SCHEMA = ToolSchema(
    name="search_repo_metadata",
    description="Quick document metadata search (Items_* / RepositoryItem). matchSource=field.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "tenant_id": {"type": "string"},
            "specific_id": {"type": "string"},
            "workspace_id": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query", "tenant_id"],
    },
)

SEARCH_REPO_RAG_SCHEMA = ToolSchema(
    name="search_repo_rag",
    description="Perfect document search via hybrid RAG. matchSource=content.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "tenant_id": {"type": "string"},
            "specific_id": {"type": "string"},
            "workspace_id": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    },
)


def _search_db(tenant_pools: Any, catalog_store: Any):
    async def acquire(tenant_id: str):
        fn = getattr(tenant_pools, "acquire_for_global_search", None)
        if callable(fn):
            return await fn(tenant_id, catalog_store)
        return await tenant_pools.acquire(tenant_id)

    return acquire


def make_search_repositories_handler(tenant_pools: Any, catalog_store: Any = None):
    acquire = _search_db(tenant_pools, catalog_store)

    async def handler(*, query: str, tenant_id: str, specific_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
        db = await acquire(tenant_id)
        hits = await search_repositories(db, query, limit=limit, specific_id=specific_id)
        return [h.model_dump() for h in hits]

    return handler


def make_search_workflows_handler(tenant_pools: Any, catalog_store: Any = None):
    acquire = _search_db(tenant_pools, catalog_store)

    async def handler(*, query: str, tenant_id: str, limit: int = 20) -> list[dict[str, Any]]:
        db = await acquire(tenant_id)
        hits = await search_workflows(db, query, limit=limit)
        return [h.model_dump() for h in hits]

    return handler


def make_search_repo_metadata_handler(tenant_pools: Any, catalog_store: Any = None):
    acquire = _search_db(tenant_pools, catalog_store)

    async def handler(
        *,
        query: str,
        tenant_id: str,
        specific_id: str = "",
        workspace_id: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        db = await acquire(tenant_id)
        hits = await search_document_metadata(
            db, query, specific_id=specific_id, workspace_id=workspace_id, limit=limit
        )
        return [h.model_dump() for h in hits]

    return handler


def make_search_repo_rag_handler(hybrid_search: HybridSearch, vector_store: VectorStore):
    async def handler(
        *,
        query: str,
        tenant_id: str = "",
        specific_id: str = "",
        workspace_id: str = "",
        limit: int = 10,
        query_embedding: Optional[list[float]] = None,
    ) -> list[dict[str, Any]]:
        hits = await search_documents_rag(
            hybrid_search,
            vector_store,
            query,
            top_n=limit,
            specific_id=specific_id,
            workspace_id=workspace_id,
            query_embedding=query_embedding,
        )
        return [h.model_dump() for h in hits]

    return handler
