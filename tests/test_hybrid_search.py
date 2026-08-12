"""Unit tests for HybridSearch's merge/ranking logic, isolated from the
HTTP layer — a FakeDBPool + VectorStore stand in for Postgres/pgvector.
"""
import uuid

import pytest

from app.knowledge.hybrid_search import HybridSearch
from app.knowledge.vector_store import VectorStore, VectorStoreUnavailableError
from app.models.document import Chunk
from tests.fakes import FakeDBPool


class _FakeEmbeddingAdapter:
    def __init__(self, vector: list[float]):
        self._vector = vector

    async def embed(self, texts):
        return [self._vector for _ in texts]


async def _seed_chunk(vector_store: VectorStore, *, document_id: str, index: int, text: str, embedding: list[float]) -> str:
    chunk_id = str(uuid.uuid4())
    await vector_store.insert_chunks(
        [Chunk(id=chunk_id, document_id=document_id, chunk_index=index, text=text, embedding=embedding)]
    )
    return chunk_id


async def test_hybrid_search_ranks_dual_match_above_single_match():
    pool = FakeDBPool()
    vector_store = VectorStore(pool)
    document = await vector_store.create_document(source="test-fixture", title="Doc")

    # Chunk A matches both the query's embedding *and* its keywords.
    chunk_a = await _seed_chunk(
        vector_store, document_id=document.id, index=0, text="pto vacation policy details", embedding=[1.0, 0.0]
    )
    # Chunk B is an exact vector match but shares no keywords with the query.
    chunk_b = await _seed_chunk(
        vector_store, document_id=document.id, index=1, text="unrelated expense text", embedding=[1.0, 0.0]
    )
    # Chunk C matches only by keyword; its embedding points elsewhere.
    chunk_c = await _seed_chunk(
        vector_store, document_id=document.id, index=2, text="pto vacation policy again", embedding=[0.0, 1.0]
    )

    hybrid = HybridSearch(vector_store, _FakeEmbeddingAdapter([1.0, 0.0]))
    results = await hybrid.search("pto vacation policy", top_n=3)

    result_ids = [r.chunk.id for r in results]
    assert chunk_a in result_ids
    # Chunk A (matches both signals) should outrank a single-signal match.
    assert result_ids.index(chunk_a) < result_ids.index(chunk_b)
    assert result_ids.index(chunk_a) < result_ids.index(chunk_c)


async def test_hybrid_search_raises_when_vector_store_unavailable():
    class _BrokenDB:
        async def fetch(self, *args, **kwargs):
            raise ConnectionError("simulated pgvector outage")

        async def fetchrow(self, *args, **kwargs):
            raise ConnectionError("simulated pgvector outage")

        async def executemany(self, *args, **kwargs):
            raise ConnectionError("simulated pgvector outage")

    vector_store = VectorStore(_BrokenDB())
    hybrid = HybridSearch(vector_store, _FakeEmbeddingAdapter([1.0, 0.0]))

    with pytest.raises(VectorStoreUnavailableError):
        await hybrid.search("pto policy", top_n=3)
