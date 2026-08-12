"""In-memory, infra-free stand-ins for the pieces of the real pipeline the
eval harness doesn't need real Postgres for: vector storage (Search) and
durable memory (Chat). Redis-backed state (response caching, pending
actions) is instead given a real `fakeredis.aioredis.FakeRedis()` instance
per case by app/evals/runner.py — a real Redis-compatible client, just not
a live server.

None of this fakes what's actually under evaluation. The LLM adapter, the
embedding adapter, and every mocked-by-design integration client
(EzofisClient, ForecastModelClient, EmailClient — already pure in-process
mocks in production, see app/integrations/) stay completely real and
unmodified. This is the same "infra vs. what's-under-test" split
tests/fakes.py draws for `pytest`, applied here for a different reason:
`pytest` fakes the LLM because determinism is the point; the eval harness
fakes storage infra because output QUALITY — not persistence correctness,
which pytest already covers — is the point (rule 9: runnable without
Docker/a live server).

Both classes below are duck-type-compatible with the real classes they
stand in for (app.knowledge.vector_store.VectorStore and
app.control.memory_store.MemoryStore) — same method names, same
signatures, same return shapes — so IngestionPipeline, HybridSearch,
SearchAgent, the store_memory/fetch_memories tools, and ChatAgent all work
against them completely unmodified.
"""
import math
import re
import uuid
from typing import Any, Optional

from app.models.document import Chunk, Document, DocumentMetadata, ScoredChunk


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1e-9
    norm_b = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (norm_a * norm_b)


class InMemoryVectorStore:
    """Stands in for app.knowledge.vector_store.VectorStore — real cosine
    similarity / keyword-overlap ranking over a plain in-process list, no
    Postgres/pgvector involved. A fresh instance is created per eval case
    by the runner, so one case's ingested fixture documents never leak
    into another case's retrieval.
    """

    def __init__(self):
        self._chunks: list[Chunk] = []

    async def create_document(
        self, *, source: str, title: Optional[str] = None, metadata: Optional[dict[str, Any]] = None
    ) -> Document:
        return Document(
            id=str(uuid.uuid4()), metadata=DocumentMetadata(source=source, title=title, extra=metadata or {})
        )

    async def insert_chunks(self, chunks: list[Chunk]) -> None:
        self._chunks.extend(chunks)

    async def vector_search(self, query_embedding: list[float], top_n: int) -> list[ScoredChunk]:
        scored = [
            (chunk, _cosine_similarity(query_embedding, chunk.embedding))
            for chunk in self._chunks
            if chunk.embedding is not None
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [
            ScoredChunk(chunk=chunk, vector_score=score, combined_score=score) for chunk, score in scored[:top_n]
        ]

    async def keyword_search(self, query: str, top_n: int) -> list[ScoredChunk]:
        terms = [t.lower() for t in re.findall(r"\w+", query)]
        scored = []
        for chunk in self._chunks:
            text_lower = chunk.text.lower()
            hits = sum(text_lower.count(term) for term in terms)
            if hits > 0:
                scored.append((chunk, float(hits)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [
            ScoredChunk(chunk=chunk, keyword_score=score, combined_score=score) for chunk, score in scored[:top_n]
        ]


class InMemoryMemoryStore:
    """Stands in for app.control.memory_store.MemoryStore — store()/
    fetch_recent() over a plain in-process list, no Postgres involved. A
    fresh instance is created per eval case by the runner, so one Chat
    memory-honoring case's stored facts never leak into another case."""

    def __init__(self):
        self._facts: list[dict[str, str]] = []

    async def store(self, *, user_id: str, fact: str) -> None:
        self._facts.append({"user_id": user_id, "fact": fact})

    async def fetch_recent(self, *, user_id: str, limit: int) -> list[str]:
        matches = [f["fact"] for f in self._facts if f["user_id"] == user_id]
        matches.reverse()  # append-order -> most-recent-first
        return matches[:limit]
