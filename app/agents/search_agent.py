"""The Search agent (Phase 2) — retrieves chunks via hybrid search, then
hands them to the Response Composer's synthesis path to produce a cited
answer. Mirrors ChatAgent's shape (same `handle(...)` signature, same
{"reply": ..., "usage": ...} return contract) so the Agent Router doesn't
need to special-case it — plus `chunk_ids` for traceability.

Phase 5b adds two independent caches (app/control/response_cache.py),
both keyed on the literal query text:
  - The query's EMBEDDING (long TTL — a given text+EMBEDDING_MODEL always
    produces the same vector, a pure function, nothing about it goes
    "stale").
  - The FULL result — reply + usage + chunk_ids together (short TTL — the
    whole pipeline's output for a given question is a point-in-time view
    over an index that can change as documents are ingested/updated; the
    short TTL is the only staleness mitigation this phase implements, see
    rule 8 — no cache-bust on ingestion).
A full-result cache HIT skips hybrid_search AND synthesis entirely: zero
embedding calls, zero vector/keyword search, zero LLM calls. A full-result
MISS still gets a chance at a faster path via the embedding cache alone
(skips only the embedding call, not the search/synthesis). On a cache
miss all the way through, behavior is byte-identical to before this phase
— caching is purely additive.
"""
from app.control.response_cache import ResponseCache
from app.core.response_composer import ResponseComposer
from app.knowledge.hybrid_search import HybridSearch
from app.llm.embedding_adapter import EmbeddingAdapter

_EMBEDDING_CACHE_PREFIX = "embedding"
_RESULT_CACHE_PREFIX = "search_result"


class SearchAgent:
    def __init__(
        self,
        hybrid_search: HybridSearch,
        response_composer: ResponseComposer,
        embedding_adapter: EmbeddingAdapter,
        response_cache: ResponseCache,
        *,
        top_n: int,
        embedding_model: str,
        llm_model: str,
        embedding_cache_ttl_seconds: int,
        result_cache_ttl_seconds: int,
    ):
        self._hybrid_search = hybrid_search
        self._response_composer = response_composer
        self._embeddings = embedding_adapter
        self._cache = response_cache
        self._top_n = top_n
        # Model identifiers are captured once, at construction (same as
        # LLMAdapter/EmbeddingAdapter themselves) — a live env var change
        # only takes effect on the next app restart, which is also when
        # these cache keys change, naturally missing any entry written
        # under the old model.
        self._embedding_model = embedding_model
        self._llm_model = llm_model
        self._embedding_cache_ttl_seconds = embedding_cache_ttl_seconds
        self._result_cache_ttl_seconds = result_cache_ttl_seconds

    async def handle(self, *, session_id: str, message: str, history: list[dict[str, str]], **_: object) -> dict:
        """Returns {"reply": str, "usage": dict | None, "chunk_ids": list[str]}.

        `history` isn't used yet — Phase 2 search is single-turn (retrieve
        for the current message only). Accepted for interface parity with
        ChatAgent.handle so the Agent Router can call either uniformly.
        """
        cached_result = await self._cache.get(
            prefix=_RESULT_CACHE_PREFIX, model=self._llm_model, input_text=message
        )
        if cached_result is not None:
            return cached_result

        query_embedding = await self._cache.get(
            prefix=_EMBEDDING_CACHE_PREFIX, model=self._embedding_model, input_text=message
        )
        if query_embedding is None:
            query_embedding = (await self._embeddings.embed([message]))[0]
            await self._cache.set(
                prefix=_EMBEDDING_CACHE_PREFIX,
                model=self._embedding_model,
                input_text=message,
                value=query_embedding,
                ttl_seconds=self._embedding_cache_ttl_seconds,
            )

        results = await self._hybrid_search.search(message, top_n=self._top_n, query_embedding=query_embedding)
        synthesis = await self._response_composer.synthesize_search_answer(question=message, results=results)
        result = {
            "reply": synthesis["content"],
            "usage": synthesis["usage"],
            "chunk_ids": [r.chunk.id for r in results],
        }

        await self._cache.set(
            prefix=_RESULT_CACHE_PREFIX,
            model=self._llm_model,
            input_text=message,
            value=result,
            ttl_seconds=self._result_cache_ttl_seconds,
        )
        return result
