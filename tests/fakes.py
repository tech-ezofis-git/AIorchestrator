"""Shared test fakes for Phase 2 (Search/RAG), Phase 4b (audit
persistence), and Phase 5a (Chat memory) — a tiny in-memory stand-in for
the asyncpg pool so tests never need a live Postgres(+pgvector) instance.

Understands only the fixed set of query shapes app/knowledge/vector_store.py,
app/control/audit_store.py, and app/control/memory_store.py issue (matched
by distinctive substrings) — not a general SQL engine.
"""
import math
import re
import uuid
from typing import Any


def _parse_vector_literal(literal: str) -> list[float]:
    return [float(x) for x in literal.strip("[]").split(",") if x]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1e-9
    norm_b = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (norm_a * norm_b)


class FakeDBPool:
    def __init__(self):
        self.documents: dict[str, dict[str, Any]] = {}
        self.chunks: dict[str, dict[str, Any]] = {}
        self.audit_log: list[dict[str, Any]] = []
        self.memories: list[dict[str, Any]] = []

    async def fetchrow(self, query: str, *args: Any):
        if "INSERT INTO documents" in query:
            source, title, metadata_json = args
            doc_id = uuid.uuid4()
            row = {"id": doc_id, "source": source, "title": title, "metadata": metadata_json}
            self.documents[str(doc_id)] = row
            return row
        raise AssertionError(f"FakeDBPool.fetchrow: unrecognized query: {query!r}")

    async def executemany(self, query: str, args_list: list[tuple]):
        if "INSERT INTO chunks" in query:
            for chunk_id, document_id, chunk_index, text, embedding_literal in args_list:
                self.chunks[str(chunk_id)] = {
                    "id": chunk_id,
                    "document_id": document_id,
                    "chunk_index": chunk_index,
                    "text": text,
                    "embedding": _parse_vector_literal(embedding_literal) if embedding_literal else None,
                }
            return
        raise AssertionError(f"FakeDBPool.executemany: unrecognized query: {query!r}")

    async def fetch(self, query: str, *args: Any):
        if "embedding <=>" in query:
            query_embedding = _parse_vector_literal(args[0])
            top_n = args[1]
            scored = [
                (c, _cosine_similarity(query_embedding, c["embedding"]))
                for c in self.chunks.values()
                if c["embedding"] is not None
            ]
            scored.sort(key=lambda pair: pair[1], reverse=True)
            return [
                {"id": c["id"], "document_id": c["document_id"], "chunk_index": c["chunk_index"], "text": c["text"], "score": score}
                for c, score in scored[:top_n]
            ]
        if "FROM memories" in query:
            user_id, limit = args
            matches = [m for m in self.memories if m["user_id"] == user_id]
            matches.reverse()  # self.memories is append-order (oldest first); newest first per the real query's ORDER BY created_at DESC
            return [{"fact": m["fact"]} for m in matches[:limit]]
        if "text_search @@" in query:
            search_query, top_n = args
            terms = [t.lower() for t in re.findall(r"\w+", search_query)]
            scored = []
            for c in self.chunks.values():
                text_lower = c["text"].lower()
                hits = sum(text_lower.count(t) for t in terms)
                if hits > 0:
                    scored.append((c, float(hits)))
            scored.sort(key=lambda pair: pair[1], reverse=True)
            return [
                {"id": c["id"], "document_id": c["document_id"], "chunk_index": c["chunk_index"], "text": c["text"], "score": score}
                for c, score in scored[:top_n]
            ]
        raise AssertionError(f"FakeDBPool.fetch: unrecognized query: {query!r}")

    async def execute(self, query: str, *args: Any):
        if "INSERT INTO memories" in query:
            user_id, fact = args
            self.memories.append({"user_id": user_id, "fact": fact})
            return
        if "INSERT INTO audit_log" in query:
            (
                correlation_id,
                session_id,
                intent,
                event_type,
                status,
                latency_ms,
                redacted_request_snippet,
                redacted_response_snippet,
            ) = args
            self.audit_log.append(
                {
                    "correlation_id": correlation_id,
                    "session_id": session_id,
                    "intent": intent,
                    "event_type": event_type,
                    "status": status,
                    "latency_ms": latency_ms,
                    "redacted_request_snippet": redacted_request_snippet,
                    "redacted_response_snippet": redacted_response_snippet,
                }
            )
            return
        raise AssertionError(f"FakeDBPool.execute: unrecognized query: {query!r}")

    async def close(self):
        pass
