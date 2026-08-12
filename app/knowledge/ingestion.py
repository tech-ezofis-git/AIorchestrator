"""Chunking + embedding pipeline for Search (Phase 2).

Ingests raw text handed to it directly — real EZOFIS document fetch stays a
TODO on app/integrations/ezofis_client.py; this module only ever sees text
that's already been read from somewhere (a local test fixture today).
Chunks into overlapping token windows, embeds each chunk via the injected
EmbeddingAdapter (LiteLLM, EMBEDDING_MODEL — no provider branching here),
and persists everything through VectorStore.
"""
import logging
import uuid
from dataclasses import dataclass
from typing import Optional, Protocol

from app.knowledge.vector_store import VectorStore
from app.models.document import Chunk, Document

logger = logging.getLogger("orchestrator.ingestion")


class EmbeddingAdapterLike(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class IngestResult:
    document: Document
    chunk_count: int


def chunk_text(text: str, *, chunk_size_tokens: int, overlap_tokens: int) -> list[str]:
    """Splits `text` into overlapping windows. Uses whitespace-delimited
    words as a simple, dependency-free stand-in for a real tokenizer —
    good enough for chunk-boundary purposes in Phase 2."""
    if chunk_size_tokens <= 0:
        raise ValueError("chunk_size_tokens must be positive")
    if overlap_tokens < 0 or overlap_tokens >= chunk_size_tokens:
        raise ValueError("overlap_tokens must be >= 0 and < chunk_size_tokens")

    words = text.split()
    if not words:
        return []

    step = chunk_size_tokens - overlap_tokens
    chunks: list[str] = []
    for start in range(0, len(words), step):
        window = words[start : start + chunk_size_tokens]
        if not window:
            break
        chunks.append(" ".join(window))
        if start + chunk_size_tokens >= len(words):
            break
    return chunks


class IngestionPipeline:
    def __init__(
        self,
        vector_store: VectorStore,
        embedding_adapter: EmbeddingAdapterLike,
        *,
        chunk_size_tokens: int,
        overlap_tokens: int,
    ):
        self._vector_store = vector_store
        self._embeddings = embedding_adapter
        self._chunk_size_tokens = chunk_size_tokens
        self._overlap_tokens = overlap_tokens

    async def ingest_text(self, *, source: str, title: Optional[str], text: str) -> IngestResult:
        """Ingest one document's raw text: chunk, embed, persist."""
        document = await self._vector_store.create_document(source=source, title=title)

        chunk_texts = chunk_text(
            text, chunk_size_tokens=self._chunk_size_tokens, overlap_tokens=self._overlap_tokens
        )
        if not chunk_texts:
            logger.warning("ingest_no_chunks", extra={"document_id": document.id, "source": source})
            return IngestResult(document=document, chunk_count=0)

        embeddings = await self._embeddings.embed(chunk_texts)

        chunks = [
            Chunk(
                id=str(uuid.uuid4()),
                document_id=document.id,
                chunk_index=i,
                text=chunk_texts[i],
                embedding=embeddings[i],
            )
            for i in range(len(chunk_texts))
        ]
        await self._vector_store.insert_chunks(chunks)

        # Chunk count only — never chunk text or embedding vectors.
        logger.info(
            "document_ingested",
            extra={"document_id": document.id, "source": source, "chunk_count": len(chunks)},
        )
        return IngestResult(document=document, chunk_count=len(chunks))
