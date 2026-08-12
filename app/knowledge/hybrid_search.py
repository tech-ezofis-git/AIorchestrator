"""Combines pgvector cosine-similarity search + Postgres full-text search
into one ranked list of chunks.

Weighting: vector cosine-similarity and ts_rank scores live on different
scales, so each result list is min-max normalized to [0, 1] independently
before combining:

    combined = VECTOR_WEIGHT * norm(vector_score) + KEYWORD_WEIGHT * norm(keyword_score)

A chunk found by only one method still gets a combined score (its missing
half counts as 0), so a chunk both methods rate highly outranks a chunk
only one method found — a reasonable default, not finely tuned; reranking
and query expansion are explicitly out of scope for this phase.

Phase 5b: `search()` accepts an optional pre-computed `query_embedding` —
purely additive (defaults to None, in which case behavior is byte-identical
to before this phase). This lets SearchAgent serve the embedding from its
own cache (app/control/response_cache.py) without HybridSearch needing to
know anything about caching itself.
"""
import asyncio
import logging
from typing import Optional

from app.knowledge.vector_store import ScoredChunk, VectorStore

logger = logging.getLogger("orchestrator.hybrid_search")

VECTOR_WEIGHT = 0.6
KEYWORD_WEIGHT = 0.4


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    if hi == lo:
        return dict.fromkeys(scores, 1.0)
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


class HybridSearch:
    def __init__(self, vector_store: VectorStore, embedding_adapter):
        self._vector_store = vector_store
        self._embeddings = embedding_adapter

    async def search(
        self, query: str, *, top_n: int, query_embedding: Optional[list[float]] = None
    ) -> list[ScoredChunk]:
        if query_embedding is None:
            query_embedding = (await self._embeddings.embed([query]))[0]

        # Fan out vector + keyword search concurrently. Both raise
        # VectorStoreUnavailableError on a pgvector/Postgres outage, which
        # asyncio.gather propagates as-is (no silent empty-result fallback).
        vector_results, keyword_results = await asyncio.gather(
            self._vector_store.vector_search(query_embedding, top_n=top_n * 2),
            self._vector_store.keyword_search(query, top_n=top_n * 2),
        )

        by_id: dict[str, ScoredChunk] = {r.chunk.id: r for r in vector_results}
        for r in keyword_results:
            existing = by_id.get(r.chunk.id)
            if existing is not None:
                by_id[r.chunk.id] = existing.model_copy(update={"keyword_score": r.keyword_score})
            else:
                by_id[r.chunk.id] = r

        vector_scores = {cid: sc.vector_score for cid, sc in by_id.items() if sc.vector_score is not None}
        keyword_scores = {cid: sc.keyword_score for cid, sc in by_id.items() if sc.keyword_score is not None}
        norm_vector = _normalize(vector_scores)
        norm_keyword = _normalize(keyword_scores)

        combined: list[ScoredChunk] = []
        for cid, sc in by_id.items():
            score = VECTOR_WEIGHT * norm_vector.get(cid, 0.0) + KEYWORD_WEIGHT * norm_keyword.get(cid, 0.0)
            combined.append(sc.model_copy(update={"combined_score": score}))

        combined.sort(key=lambda sc: sc.combined_score, reverse=True)
        top = combined[:top_n]

        logger.info("hybrid_search_completed", extra={"query_len": len(query), "result_count": len(top)})
        return top
