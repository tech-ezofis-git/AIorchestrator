"""Unit tests for ResponseCache — uses fakeredis and an injectable fake
clock so TTL-expiry behavior is deterministic, never sleep-based. Same
pattern as tests/test_rate_limiter.py.
"""
import fakeredis.aioredis

from app.control.response_cache import ResponseCache


class _FakeClock:
    """Deterministic, manually-advanced clock — tests never sleep."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def test_miss_when_never_cached():
    cache = ResponseCache(fakeredis.aioredis.FakeRedis())

    result = await cache.get(prefix="embedding", model="model-a", input_text="hello")

    assert result is None


async def test_hit_after_set_returns_the_exact_value():
    cache = ResponseCache(fakeredis.aioredis.FakeRedis())

    await cache.set(prefix="embedding", model="model-a", input_text="hello", value=[1.0, 2.0, 3.0], ttl_seconds=60)
    result = await cache.get(prefix="embedding", model="model-a", input_text="hello")

    assert result == [1.0, 2.0, 3.0]


async def test_hit_works_for_dict_values_too():
    """search_result / forecast_narration cache entries are whole dicts,
    not just vectors — prove those round-trip through JSON correctly."""
    cache = ResponseCache(fakeredis.aioredis.FakeRedis())
    value = {"reply": "hi", "usage": {"total_tokens": 5}, "chunk_ids": ["a", "b"]}

    await cache.set(prefix="search_result", model="gpt-4.1-mini", input_text="what is pto", value=value, ttl_seconds=60)
    result = await cache.get(prefix="search_result", model="gpt-4.1-mini", input_text="what is pto")

    assert result == value


async def test_different_input_text_is_an_independent_key():
    cache = ResponseCache(fakeredis.aioredis.FakeRedis())

    await cache.set(prefix="embedding", model="model-a", input_text="hello", value="value-for-hello", ttl_seconds=60)

    assert await cache.get(prefix="embedding", model="model-a", input_text="goodbye") is None


async def test_different_prefix_is_an_independent_key():
    """Same model + same input text, different entry-kind prefix — must
    not collide (embedding cache vs. search_result cache vs.
    forecast_narration cache all share the model+input-text shape)."""
    cache = ResponseCache(fakeredis.aioredis.FakeRedis())

    await cache.set(prefix="embedding", model="model-a", input_text="x", value="embedding-value", ttl_seconds=60)

    assert await cache.get(prefix="search_result", model="model-a", input_text="x") is None


async def test_different_model_is_a_cache_miss_not_a_stale_hit():
    """Changing the relevant model must never serve output computed under
    a different model — rule 5."""
    cache = ResponseCache(fakeredis.aioredis.FakeRedis())

    await cache.set(prefix="embedding", model="text-embedding-3-small", input_text="hello", value=[1.0], ttl_seconds=60)

    assert await cache.get(prefix="embedding", model="text-embedding-3-large", input_text="hello") is None
    # The original model is unaffected — this proves it's a genuinely
    # independent key, not a shared entry that got overwritten/cleared.
    assert await cache.get(prefix="embedding", model="text-embedding-3-small", input_text="hello") == [1.0]


async def test_entry_expires_after_its_ttl_via_injectable_clock():
    clock = _FakeClock()
    cache = ResponseCache(fakeredis.aioredis.FakeRedis(), clock=clock)

    await cache.set(prefix="search_result", model="gpt-4.1-mini", input_text="q", value="cached-answer", ttl_seconds=300)

    # Still within TTL.
    clock.advance(299)
    assert await cache.get(prefix="search_result", model="gpt-4.1-mini", input_text="q") == "cached-answer"

    # Past TTL — deterministic miss, no real sleeping.
    clock.advance(2)
    assert await cache.get(prefix="search_result", model="gpt-4.1-mini", input_text="q") is None


async def test_lookup_degrades_to_a_miss_on_redis_outage_not_an_exception():
    class _BrokenRedis:
        async def get(self, key):
            raise ConnectionError("simulated redis outage")

    cache = ResponseCache(_BrokenRedis())

    # Must not raise — caching is a performance optimization, never a
    # correctness dependency (rule 7).
    result = await cache.get(prefix="embedding", model="model-a", input_text="hello")

    assert result is None


async def test_lookup_failure_logs_a_warning(monkeypatch):
    import app.control.response_cache as response_cache_module

    class _BrokenRedis:
        async def get(self, key):
            raise ConnectionError("simulated redis outage")

    warning_calls = []
    monkeypatch.setattr(response_cache_module.logger, "warning", lambda msg, *a, **kw: warning_calls.append(msg))

    cache = ResponseCache(_BrokenRedis())
    await cache.get(prefix="embedding", model="model-a", input_text="hello")

    assert "cache_lookup_failed" in warning_calls


async def test_write_failure_is_swallowed_not_raised():
    class _BrokenRedis:
        async def set(self, key, value, ex=None):
            raise ConnectionError("simulated redis outage")

    cache = ResponseCache(_BrokenRedis())

    # Must not raise — a lost cache write only costs a future recompute.
    await cache.set(prefix="embedding", model="model-a", input_text="hello", value=[1.0], ttl_seconds=60)


async def test_write_failure_logs_a_warning(monkeypatch):
    import app.control.response_cache as response_cache_module

    class _BrokenRedis:
        async def set(self, key, value, ex=None):
            raise ConnectionError("simulated redis outage")

    warning_calls = []
    monkeypatch.setattr(response_cache_module.logger, "warning", lambda msg, *a, **kw: warning_calls.append(msg))

    cache = ResponseCache(_BrokenRedis())
    await cache.set(prefix="embedding", model="model-a", input_text="hello", value=[1.0], ttl_seconds=60)

    assert "cache_write_failed" in warning_calls
