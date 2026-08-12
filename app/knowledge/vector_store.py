"""pgvector-backed read/write for documents + chunks.

Talks to Postgres through a minimal `DBExecutor` protocol (fetch/fetchrow/
executemany) rather than depending on `asyncpg.Pool` directly — an
`asyncpg.Pool` already implements exactly this shape, so production code
passes one straight through, and tests inject a small in-memory fake
(see tests/fakes.py) instead of needing a live Postgres.

DB failures (connection errors, timeouts, ...) are caught here, logged, and
re-raised as VectorStoreUnavailableError — same "explicit failure over
silent empty-result" discipline Phase 0/1 established for Redis
(ContextManager.SessionStoreUnavailableError). A pgvector outage must never
look like "no matching documents".

asyncpg has no built-in pgvector codec, so embedding vectors are passed as
pgvector's text literal format ('[0.1,0.2,...]') and cast in SQL via
`::vector` — see `_embedding_literal`.
"""
import json
import logging
import uuid
from typing import Any, Optional, Protocol

from app.models.document import Chunk, Document, DocumentMetadata, ScoredChunk

logger = logging.getLogger("orchestrator.vector_store")


class VectorStoreUnavailableError(Exception):
    """Raised when Postgres/pgvector can't be read from or written to. Safe
    to surface to API callers as a generic 503 — never wraps the
    underlying DB exception's message."""


class DBExecutor(Protocol):
    async def fetch(self, query: str, *args: Any) -> list[Any]: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def executemany(self, query: str, args: list[tuple]) -> None: ...


def _embedding_literal(embedding: Optional[list[float]]) -> Optional[str]:
    if embedding is None:
        return None
    return "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"


class VectorStore:
    def __init__(self, db: DBExecutor):
        self._db = db

    async def create_document(
        self, *, source: str, title: Optional[str] = None, metadata: Optional[dict[str, Any]] = None
    ) -> Document:
        try:
            row = await self._db.fetchrow(
                """
                INSERT INTO documents (source, title, metadata)
                VALUES ($1, $2, $3::jsonb)
                RETURNING id, source, title, metadata
                """,
                source,
                title,
                json.dumps(metadata or {}),
            )
        except Exception as exc:
            logger.warning("vector_store_create_document_failed", extra={"source": source})
            raise VectorStoreUnavailableError("Document store is currently unavailable.") from exc

        raw_metadata = row["metadata"]
        extra = json.loads(raw_metadata) if isinstance(raw_metadata, str) else (raw_metadata or {})
        return Document(
            id=str(row["id"]),
            metadata=DocumentMetadata(source=row["source"], title=row["title"], extra=extra),
        )

    async def insert_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        try:
            await self._db.executemany(
                """
                INSERT INTO chunks (id, document_id, chunk_index, text, embedding)
                VALUES ($1, $2, $3, $4, $5::vector)
                ON CONFLICT (document_id, chunk_index) DO UPDATE
                    SET text = EXCLUDED.text, embedding = EXCLUDED.embedding
                """,
                [
                    (
                        uuid.UUID(c.id),
                        uuid.UUID(c.document_id),
                        c.chunk_index,
                        c.text,
                        _embedding_literal(c.embedding),
                    )
                    for c in chunks
                ],
            )
        except Exception as exc:
            logger.warning("vector_store_insert_chunks_failed", extra={"chunk_count": len(chunks)})
            raise VectorStoreUnavailableError("Document store is currently unavailable.") from exc

        # Chunk count only — never the text or the embedding vectors.
        logger.info("chunks_inserted", extra={"chunk_count": len(chunks)})

    async def vector_search(self, query_embedding: list[float], top_n: int) -> list[ScoredChunk]:
        try:
            rows = await self._db.fetch(
                """
                SELECT id, document_id, chunk_index, text,
                       1 - (embedding <=> $1::vector) AS score
                FROM chunks
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                _embedding_literal(query_embedding),
                top_n,
            )
        except Exception as exc:
            logger.warning("vector_store_vector_search_failed")
            raise VectorStoreUnavailableError("Document store is currently unavailable.") from exc

        return [
            ScoredChunk(
                chunk=Chunk(
                    id=str(r["id"]), document_id=str(r["document_id"]), chunk_index=r["chunk_index"], text=r["text"]
                ),
                vector_score=float(r["score"]),
                combined_score=float(r["score"]),
            )
            for r in rows
        ]

    async def keyword_search(self, query: str, top_n: int) -> list[ScoredChunk]:
        try:
            rows = await self._db.fetch(
                """
                SELECT id, document_id, chunk_index, text,
                       ts_rank(text_search, plainto_tsquery('english', $1)) AS score
                FROM chunks
                WHERE text_search @@ plainto_tsquery('english', $1)
                ORDER BY score DESC
                LIMIT $2
                """,
                query,
                top_n,
            )
        except Exception as exc:
            logger.warning("vector_store_keyword_search_failed")
            raise VectorStoreUnavailableError("Document store is currently unavailable.") from exc

        return [
            ScoredChunk(
                chunk=Chunk(
                    id=str(r["id"]), document_id=str(r["document_id"]), chunk_index=r["chunk_index"], text=r["text"]
                ),
                keyword_score=float(r["score"]),
                combined_score=float(r["score"]),
            )
            for r in rows
        ]
