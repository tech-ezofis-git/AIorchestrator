"""Document/Chunk schemas for the Phase 2 Search (RAG) pipeline.

These describe data moving between ingestion, the vector store, and hybrid
search — not the Postgres schema directly (see db/migrations/ for that).
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class DocumentMetadata(BaseModel):
    source: str = Field(..., description="Where this document came from, e.g. 'test-fixture'.")
    title: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Document(BaseModel):
    id: str
    metadata: DocumentMetadata


class Chunk(BaseModel):
    id: str
    document_id: str
    chunk_index: int
    text: str
    embedding: Optional[list[float]] = None
    created_at: Optional[datetime] = None


class ScoredChunk(BaseModel):
    """One chunk plus the score(s) hybrid search ranked it by. `vector_score`
    and/or `keyword_score` may be absent if a chunk was only found by one of
    the two retrieval methods."""

    chunk: Chunk
    vector_score: Optional[float] = None
    keyword_score: Optional[float] = None
    combined_score: float = 0.0
