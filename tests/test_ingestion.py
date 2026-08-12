"""Unit tests for the chunking function and the ingestion pipeline
end-to-end (chunk -> embed -> persist), using a FakeDBPool + a fake
embedding adapter so no real API key/DB is needed.
"""
from app.knowledge.ingestion import IngestionPipeline, chunk_text
from app.knowledge.vector_store import VectorStore
from tests.fakes import FakeDBPool


def test_chunk_text_splits_with_overlap():
    text = " ".join(f"word{i}" for i in range(25))

    chunks = chunk_text(text, chunk_size_tokens=10, overlap_tokens=2)

    assert len(chunks) >= 3
    # Consecutive chunks overlap by exactly the configured overlap.
    first_words = chunks[0].split()
    second_words = chunks[1].split()
    assert first_words[-2:] == second_words[:2]


def test_chunk_text_empty_input_returns_no_chunks():
    assert chunk_text("   ", chunk_size_tokens=10, overlap_tokens=2) == []


def test_chunk_text_rejects_invalid_overlap():
    import pytest

    with pytest.raises(ValueError):
        chunk_text("word " * 20, chunk_size_tokens=10, overlap_tokens=10)


class _FakeEmbeddingAdapter:
    def __init__(self):
        self.calls: list[list[str]] = []

    async def embed(self, texts):
        self.calls.append(texts)
        return [[float(len(t)), 0.0] for t in texts]


async def test_ingest_text_chunks_embeds_and_persists():
    pool = FakeDBPool()
    vector_store = VectorStore(pool)
    embedding_adapter = _FakeEmbeddingAdapter()
    pipeline = IngestionPipeline(vector_store, embedding_adapter, chunk_size_tokens=10, overlap_tokens=2)

    text = " ".join(f"word{i}" for i in range(25))
    result = await pipeline.ingest_text(source="test-fixture", title="Sample", text=text)

    assert result.chunk_count > 0
    assert len(pool.chunks) == result.chunk_count
    assert all(c["embedding"] is not None for c in pool.chunks.values())
    assert len(embedding_adapter.calls) == 1  # chunks embedded in one batch call


async def test_ingest_text_with_no_content_persists_zero_chunks():
    pool = FakeDBPool()
    vector_store = VectorStore(pool)
    embedding_adapter = _FakeEmbeddingAdapter()
    pipeline = IngestionPipeline(vector_store, embedding_adapter, chunk_size_tokens=10, overlap_tokens=2)

    result = await pipeline.ingest_text(source="test-fixture", title="Empty", text="   ")

    assert result.chunk_count == 0
    assert len(pool.chunks) == 0
    assert embedding_adapter.calls == []
