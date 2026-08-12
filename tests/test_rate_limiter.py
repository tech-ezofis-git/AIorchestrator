"""Unit tests for RateLimiter — uses fakeredis and an injectable fake
clock so window-boundary behavior is deterministic, never sleep-based —
plus an HTTP-level test proving a rate-limited request never reaches
intent classification or any agent (call-count assertions, not just the
response code).
"""
import fakeredis.aioredis
import pytest

from app.control.rate_limiter import RateLimitExceededError, RateLimiter, RateLimiterStoreUnavailableError


class _FakeClock:
    """Deterministic, manually-advanced clock — tests never sleep."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def test_allows_requests_under_the_threshold():
    redis = fakeredis.aioredis.FakeRedis()
    limiter = RateLimiter(redis, max_requests=3, window_seconds=60, clock=_FakeClock())

    for _ in range(3):
        await limiter.check("session-a")  # must not raise


async def test_rejects_requests_over_the_threshold():
    redis = fakeredis.aioredis.FakeRedis()
    limiter = RateLimiter(redis, max_requests=3, window_seconds=60, clock=_FakeClock())

    for _ in range(3):
        await limiter.check("session-a")

    with pytest.raises(RateLimitExceededError) as exc_info:
        await limiter.check("session-a")
    assert exc_info.value.retry_after_seconds > 0


async def test_different_sessions_have_independent_limits():
    redis = fakeredis.aioredis.FakeRedis()
    limiter = RateLimiter(redis, max_requests=1, window_seconds=60, clock=_FakeClock())

    await limiter.check("session-a")  # must not raise
    await limiter.check("session-b")  # different session, independent counter


async def test_limit_resets_after_the_window_advances():
    redis = fakeredis.aioredis.FakeRedis()
    clock = _FakeClock()
    limiter = RateLimiter(redis, max_requests=1, window_seconds=60, clock=clock)

    await limiter.check("session-a")
    with pytest.raises(RateLimitExceededError):
        await limiter.check("session-a")

    clock.advance(61)  # deterministic — no real sleeping
    await limiter.check("session-a")  # must not raise, new window


async def test_raises_store_unavailable_on_redis_outage():
    class _BrokenRedis:
        async def incr(self, key):
            raise ConnectionError("simulated redis outage")

    limiter = RateLimiter(_BrokenRedis(), max_requests=3, window_seconds=60)

    with pytest.raises(RateLimiterStoreUnavailableError):
        await limiter.check("session-a")


def test_chat_returns_429_when_rate_limited_and_downstream_never_invoked(client, monkeypatch):
    """Exceeding the limit must return 429 with a Retry-After hint, and
    must never reach intent classification or the LLM — proven by call
    counts, not just the status code."""
    import app.main as main_module

    # Tighten the app's live rate limiter to a threshold this test can
    # trip in two requests, reusing the same (fake) Redis client.
    monkeypatch.setattr(
        main_module.app.state,
        "rate_limiter",
        RateLimiter(main_module.app.state.rate_limiter._redis, max_requests=1, window_seconds=60),
    )

    classify_call_count = 0

    async def tracking_classify(self, message):
        nonlocal classify_call_count
        classify_call_count += 1
        from app.core.intent_router import Intent

        return Intent.CHAT

    monkeypatch.setattr("app.core.intent_router.IntentRouter.classify", tracking_classify)

    llm_call_count = 0

    async def tracking_chat_completion(self, messages):
        nonlocal llm_call_count
        llm_call_count += 1
        return {"content": "hi", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", tracking_chat_completion)

    first = client.post("/chat", json={"session_id": "s-ratelimit", "message": "hello"})
    assert first.status_code == 200
    assert classify_call_count == 1
    assert llm_call_count == 1

    second = client.post("/chat", json={"session_id": "s-ratelimit", "message": "hello again"})
    assert second.status_code == 429
    assert "Retry-After" in second.headers
    assert "Traceback" not in second.text
    # Rate limit must block BEFORE intent classification and the agent.
    assert classify_call_count == 1
    assert llm_call_count == 1


def test_confirm_returns_429_when_rate_limited_and_dispatcher_never_invoked(client, monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(
        main_module.app.state,
        "rate_limiter",
        RateLimiter(main_module.app.state.rate_limiter._redis, max_requests=1, window_seconds=60),
    )

    # confirm looks up a pending action via get() (non-destructive) before
    # ever consuming it — see the pending-action session-binding patch.
    lookup_calls = []

    async def tracking_get(self, action_id):
        lookup_calls.append(action_id)
        return None

    monkeypatch.setattr("app.core.pending_actions.PendingActionStore.get", tracking_get)

    first = client.post("/actions/some-action/confirm", params={"session_id": "s-confirm-ratelimit"})
    assert first.status_code == 404  # allowed through, just no such pending action
    assert lookup_calls == ["some-action"]

    second = client.post("/actions/some-action/confirm", params={"session_id": "s-confirm-ratelimit"})
    assert second.status_code == 429
    # Rate limit must block BEFORE the pending action is looked up.
    assert lookup_calls == ["some-action"]
