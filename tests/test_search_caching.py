"""Phase 5b — Search caching: the query embedding (long TTL) and the full
result (short TTL), both keyed on the literal query text + the relevant
model identifier. Two layers:
  - HTTP-level, via the live app (`client` fixture): proves a repeated
    identical query is a genuine cache hit — zero redundant embedding or
    LLM calls, not just a matching response.
  - Unit-level, constructing SearchAgent directly with fakes (mirrors
    tests/test_hybrid_search.py's style): proves model-swap isolation, TTL
    expiry via an injectable clock, and Redis-outage soft-fail — none of
    which need a live app or real settings/env vars to exercise.
"""
import asyncio
from pathlib import Path

import fakeredis.aioredis

import app.main as main_module
from app.agents.search_agent import SearchAgent
from app.control.response_cache import ResponseCache
from app.knowledge.ingestion import IngestionPipeline

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_VOCAB = ["pto", "vacation", "policy", "expense", "reimbursement", "receipt"]


def _fake_embed_vector(text: str) -> list[float]:
    lowered = text.lower()
    return [float(lowered.count(term)) for term in _VOCAB]


class _FakeClock:
    """Deterministic, manually-advanced clock — tests never sleep."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeHybridSearch:
    def __init__(self):
        self.search_calls = 0

    async def search(self, query, *, top_n, query_embedding=None):
        self.search_calls += 1
        return []


class _FakeResponseComposer:
    def __init__(self):
        self.synthesize_calls = 0

    async def synthesize_search_answer(self, *, question, results):
        self.synthesize_calls += 1
        return {"content": f"answer for {question}", "usage": None}


class _FakeEmbeddingAdapter:
    def __init__(self):
        self.embed_calls = 0

    async def embed(self, texts):
        self.embed_calls += 1
        return [[1.0, 2.0] for _ in texts]


def _make_agent(hybrid_search, composer, embeddings, cache, *, embedding_model="model-a", llm_model="llm-a"):
    return SearchAgent(
        hybrid_search,
        composer,
        embeddings,
        cache,
        top_n=5,
        embedding_model=embedding_model,
        llm_model=llm_model,
        embedding_cache_ttl_seconds=86400,
        result_cache_ttl_seconds=300,
    )


def _ingest_fixtures(monkeypatch):
    async def fake_embed(self, texts):
        return [_fake_embed_vector(t) for t in texts]

    monkeypatch.setattr("app.llm.embedding_adapter.EmbeddingAdapter.embed", fake_embed)

    pipeline = IngestionPipeline(
        main_module.app.state.vector_store,
        main_module.app.state.embedding_adapter,
        chunk_size_tokens=50,
        overlap_tokens=5,
    )
    pto_text = (_FIXTURES_DIR / "pto_policy.txt").read_text()
    asyncio.run(pipeline.ingest_text(source="test-fixture", title="PTO Policy", text=pto_text))


# --- HTTP-level: proves the wiring end to end -------------------------------


def test_repeated_identical_search_query_is_a_cache_hit_no_redundant_calls(client, monkeypatch):
    _ingest_fixtures(monkeypatch)

    embed_calls = []

    async def tracking_embed(self, texts):
        embed_calls.append(list(texts))
        return [_fake_embed_vector(t) for t in texts]

    monkeypatch.setattr("app.llm.embedding_adapter.EmbeddingAdapter.embed", tracking_embed)

    llm_calls = []

    async def tracking_chat_completion(self, messages):
        llm_calls.append(messages)
        return {
            "content": "PTO accrues at 1.5 days/month. [1]",
            "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", tracking_chat_completion)

    first = client.post("/chat", json={"session_id": "s-cache-a", "message": "search for the PTO policy"})
    assert first.status_code == 200
    assert len(embed_calls) == 1
    assert len(llm_calls) == 1

    second = client.post("/chat", json={"session_id": "s-cache-b", "message": "search for the PTO policy"})
    assert second.status_code == 200
    # Zero additional embedding or LLM calls — the full-result cache hit
    # skips hybrid_search AND synthesis entirely.
    assert len(embed_calls) == 1
    assert len(llm_calls) == 1
    assert second.json()["reply"] == first.json()["reply"]
    assert second.json()["chunk_ids"] == first.json()["chunk_ids"]


def test_different_query_text_is_a_cache_miss(client, monkeypatch):
    _ingest_fixtures(monkeypatch)

    embed_calls = []

    async def tracking_embed(self, texts):
        embed_calls.append(list(texts))
        return [_fake_embed_vector(t) for t in texts]

    monkeypatch.setattr("app.llm.embedding_adapter.EmbeddingAdapter.embed", tracking_embed)

    llm_calls = []

    async def tracking_chat_completion(self, messages):
        llm_calls.append(messages)
        return {"content": "answer", "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", tracking_chat_completion)

    client.post("/chat", json={"session_id": "s-cache-c", "message": "search for the PTO policy"})
    client.post("/chat", json={"session_id": "s-cache-d", "message": "search for the expense policy"})

    assert len(embed_calls) == 2
    assert len(llm_calls) == 2


# --- Unit-level: model isolation, TTL expiry, Redis-outage soft-fail --------


async def test_changing_embedding_model_is_a_cache_miss_not_a_stale_hit():
    cache = ResponseCache(fakeredis.aioredis.FakeRedis())
    hybrid_search = _FakeHybridSearch()
    composer = _FakeResponseComposer()
    embeddings = _FakeEmbeddingAdapter()

    agent_a = _make_agent(hybrid_search, composer, embeddings, cache, embedding_model="model-a", llm_model="llm-a")
    # Different LLM_MODEL too, so agent_b's full-result cache lookup is
    # independently a miss — isolates this test to the embedding cache. A
    # shared llm_model would let agent_b's full-result cache check hit
    # agent_a's cached response before ever reaching the embedding step.
    agent_b = _make_agent(hybrid_search, composer, embeddings, cache, embedding_model="model-b", llm_model="llm-b")

    await agent_a.handle(session_id="s1", message="same query text", history=[])
    assert embeddings.embed_calls == 1

    await agent_b.handle(session_id="s2", message="same query text", history=[])
    # A different embedding model must force a fresh embed call — never
    # serve model-a's cached vector under model-b's key.
    assert embeddings.embed_calls == 2


async def test_changing_llm_model_is_a_cache_miss_for_the_full_result_only():
    cache = ResponseCache(fakeredis.aioredis.FakeRedis())
    hybrid_search = _FakeHybridSearch()
    composer = _FakeResponseComposer()
    embeddings = _FakeEmbeddingAdapter()

    # Same embedding model, different LLM model.
    agent_a = _make_agent(hybrid_search, composer, embeddings, cache, embedding_model="model-a", llm_model="llm-a")
    agent_b = _make_agent(hybrid_search, composer, embeddings, cache, embedding_model="model-a", llm_model="llm-b")

    await agent_a.handle(session_id="s1", message="q", history=[])
    assert composer.synthesize_calls == 1

    await agent_b.handle(session_id="s2", message="q", history=[])
    # Different LLM_MODEL -> the full-result cache must miss -> synthesis
    # runs again...
    assert composer.synthesize_calls == 2
    # ...but the embedding model is unchanged and shared, so the embedding
    # cache still hits — only one embed call total across both agents.
    assert embeddings.embed_calls == 1


async def test_full_result_cache_expires_after_its_ttl():
    clock = _FakeClock()
    cache = ResponseCache(fakeredis.aioredis.FakeRedis(), clock=clock)
    hybrid_search = _FakeHybridSearch()
    composer = _FakeResponseComposer()
    embeddings = _FakeEmbeddingAdapter()
    agent = _make_agent(hybrid_search, composer, embeddings, cache)

    await agent.handle(session_id="s1", message="q", history=[])
    assert composer.synthesize_calls == 1

    clock.advance(299)  # still within the 300s result TTL
    await agent.handle(session_id="s2", message="q", history=[])
    assert composer.synthesize_calls == 1  # still cached

    clock.advance(2)  # now past the TTL
    await agent.handle(session_id="s3", message="q", history=[])
    assert composer.synthesize_calls == 2  # expired -> recomputed, no real sleeping


async def test_search_succeeds_when_cache_lookup_and_write_fail():
    class _BrokenRedis:
        async def get(self, key):
            raise ConnectionError("simulated redis outage")

        async def set(self, key, value, ex=None):
            raise ConnectionError("simulated redis outage")

    cache = ResponseCache(_BrokenRedis())
    hybrid_search = _FakeHybridSearch()
    composer = _FakeResponseComposer()
    embeddings = _FakeEmbeddingAdapter()
    agent = _make_agent(hybrid_search, composer, embeddings, cache)

    # Must not raise — caching is never a correctness dependency (rule 7).
    result = await agent.handle(session_id="s1", message="q", history=[])

    assert result["reply"] == "answer for q"
    assert embeddings.embed_calls == 1
    assert composer.synthesize_calls == 1
