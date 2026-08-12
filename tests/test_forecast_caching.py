"""Phase 5b — Forecast caching: the narration only (short TTL), keyed on
the actual forecast content + LLM_MODEL. `run_forecast` itself is NEVER
cached — `forecast_result`'s raw numbers must be freshly computed on every
call, cache hit or miss for the narration.

Two layers, same split as tests/test_search_caching.py: HTTP-level via the
live app for an end-to-end proof, unit-level constructing ForecastAgent
directly with fakes for model-swap isolation, TTL expiry (injectable
clock), and Redis-outage soft-fail.
"""
import fakeredis.aioredis

from app.agents.forecast_agent import ForecastAgent
from app.control.response_cache import ResponseCache


class _FakeClock:
    """Deterministic, manually-advanced clock — tests never sleep."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeDispatcher:
    def __init__(self, forecast: dict):
        self.dispatch_calls = 0
        self._forecast = forecast

    async def dispatch(self, tool_name, arguments):
        self.dispatch_calls += 1
        return self._forecast


class _FakeResponseComposer:
    def __init__(self):
        self.synthesize_calls = 0

    async def synthesize_forecast(self, *, forecast):
        self.synthesize_calls += 1
        return {"content": f"narration for {forecast['metric']}", "usage": None}


_SAMPLE_FORECAST = {
    "metric": "revenue",
    "horizon": "next quarter",
    "predicted_values": [1000.0, 1030.0, 1060.9, 1092.7],
    "confidence_interval": [{"period": 1, "low": 900.0, "high": 1100.0}],
    "mock": True,
}


def _make_agent(dispatcher, composer, cache, *, llm_model="llm-a", ttl_seconds=300):
    return ForecastAgent(dispatcher, composer, cache, llm_model=llm_model, narration_cache_ttl_seconds=ttl_seconds)


# --- HTTP-level: proves the wiring end to end -------------------------------


def test_repeated_identical_forecast_is_a_narration_cache_hit_but_numbers_stay_fresh(client, monkeypatch):
    llm_calls = []

    async def tracking_chat_completion(self, messages):
        llm_calls.append(messages)
        return {
            "content": "Revenue is projected to grow steadily.",
            "usage": {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", tracking_chat_completion)

    run_forecast_calls = []

    async def tracking_run_forecast(self, metric, horizon):
        run_forecast_calls.append((metric, horizon))
        return dict(_SAMPLE_FORECAST)

    monkeypatch.setattr("app.integrations.forecast_model.ForecastModelClient.run_forecast", tracking_run_forecast)

    first = client.post("/chat", json={"session_id": "s-forecast-cache-a", "message": "forecast revenue"})
    assert first.status_code == 200
    assert len(llm_calls) == 1
    assert len(run_forecast_calls) == 1

    second = client.post("/chat", json={"session_id": "s-forecast-cache-b", "message": "forecast revenue"})
    assert second.status_code == 200
    # Narration cache hit — no additional LLM call...
    assert len(llm_calls) == 1
    assert second.json()["reply"] == first.json()["reply"]
    # ...but run_forecast is NEVER cached — it still ran again, fresh.
    assert len(run_forecast_calls) == 2
    assert second.json()["forecast_result"]["predicted_values"] == first.json()["forecast_result"]["predicted_values"]


# --- Unit-level: model isolation, TTL expiry, Redis-outage soft-fail --------


async def test_repeated_call_with_identical_forecast_content_is_a_cache_hit():
    cache = ResponseCache(fakeredis.aioredis.FakeRedis())
    dispatcher = _FakeDispatcher(dict(_SAMPLE_FORECAST))
    composer = _FakeResponseComposer()
    agent = _make_agent(dispatcher, composer, cache)

    await agent.handle(session_id="s1", message="forecast revenue", history=[])
    await agent.handle(session_id="s2", message="forecast revenue", history=[])

    assert composer.synthesize_calls == 1  # second call is a narration cache hit
    assert dispatcher.dispatch_calls == 2  # run_forecast is never cached — always fresh


async def test_changing_llm_model_is_a_cache_miss_not_a_stale_hit():
    cache = ResponseCache(fakeredis.aioredis.FakeRedis())
    dispatcher = _FakeDispatcher(dict(_SAMPLE_FORECAST))
    composer = _FakeResponseComposer()

    agent_a = _make_agent(dispatcher, composer, cache, llm_model="llm-a")
    agent_b = _make_agent(dispatcher, composer, cache, llm_model="llm-b")

    await agent_a.handle(session_id="s1", message="forecast revenue", history=[])
    assert composer.synthesize_calls == 1

    await agent_b.handle(session_id="s2", message="forecast revenue", history=[])
    # Different LLM_MODEL -> the narration cache must miss, not serve
    # agent_a's narration under agent_b's model.
    assert composer.synthesize_calls == 2


async def test_narration_cache_expires_after_its_ttl():
    clock = _FakeClock()
    cache = ResponseCache(fakeredis.aioredis.FakeRedis(), clock=clock)
    dispatcher = _FakeDispatcher(dict(_SAMPLE_FORECAST))
    composer = _FakeResponseComposer()
    agent = _make_agent(dispatcher, composer, cache, ttl_seconds=300)

    await agent.handle(session_id="s1", message="forecast revenue", history=[])
    assert composer.synthesize_calls == 1

    clock.advance(299)  # still within the 300s narration TTL
    await agent.handle(session_id="s2", message="forecast revenue", history=[])
    assert composer.synthesize_calls == 1  # still cached

    clock.advance(2)  # now past the TTL
    await agent.handle(session_id="s3", message="forecast revenue", history=[])
    assert composer.synthesize_calls == 2  # expired -> recomputed, no real sleeping


async def test_forecast_succeeds_when_cache_lookup_and_write_fail():
    class _BrokenRedis:
        async def get(self, key):
            raise ConnectionError("simulated redis outage")

        async def set(self, key, value, ex=None):
            raise ConnectionError("simulated redis outage")

    cache = ResponseCache(_BrokenRedis())
    dispatcher = _FakeDispatcher(dict(_SAMPLE_FORECAST))
    composer = _FakeResponseComposer()
    agent = _make_agent(dispatcher, composer, cache)

    # Must not raise — caching is never a correctness dependency (rule 7).
    result = await agent.handle(session_id="s1", message="forecast revenue", history=[])

    assert result["reply"] == "narration for revenue"
    assert result["forecast_result"] == _SAMPLE_FORECAST
    assert composer.synthesize_calls == 1
