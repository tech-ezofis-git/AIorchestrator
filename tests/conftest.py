"""Shared test fixtures.

- Redis is swapped for fakeredis so tests don't need a live Redis instance.
- Postgres/pgvector is swapped for an in-memory FakeDBPool (tests/fakes.py)
  so tests don't need a live Postgres instance either.
- The LLM adapter's chat_completion (and, in Search tests, the embedding
  adapter's embed) are monkeypatched so tests never make a real network
  call to an LLM/embedding provider or need an API key.
"""
import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from tests.fakes import FakeDBPool


class _IsolatedFakeRedis:
    """Stands in for `redis.asyncio.Redis` so `Redis.from_url(...)` (the
    only way app/main.py ever constructs a Redis client) returns a fresh,
    independent in-memory store every time — NOT `fakeredis.aioredis
    .FakeRedis.from_url(...)` itself, which shares ONE process-wide store
    per connection URL (every test uses the same default REDIS_URL, so
    without this wrapper, two tests could silently read each other's
    data). This never mattered before Phase 5b: every prior Redis-backed
    component keys its data by something already unique per test (a
    session_id, a random pending-action id, ...), so a shared store never
    surfaced. Response caching (app/control/response_cache.py) is the
    first component that looks data up by CONTENT — the same query text
    under the same model, from two different tests, is now a genuine
    collision risk without real isolation here.
    """

    @staticmethod
    def from_url(url, **kwargs):
        return fakeredis.aioredis.FakeRedis(**kwargs)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main_module, "Redis", _IsolatedFakeRedis)

    # Azure / Qwen preset keys for Test Console — never real secrets in tests.
    monkeypatch.setenv("AZURE_SOUTH_INDIA_API_KEY", "test-south-india-key")
    monkeypatch.setenv("AZURE_EAST_US_API_KEY", "test-east-us-key")
    monkeypatch.setenv("QWEN_MAC_API_KEY", "test-qwen-mac-key")
    # Use local mock OCR (no remote extract_text) unless a test overrides.
    monkeypatch.setenv("OCR_EXTRACT_URL", "")
    monkeypatch.delenv("AZURE_STORAGE_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("CATALOG_DATABASE_URL", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    from app.llm.model_presets import set_runtime_presets

    set_runtime_presets(None)

    fake_db_pool = FakeDBPool()

    async def fake_create_pool(*args, **kwargs):
        return fake_db_pool

    monkeypatch.setattr(main_module.asyncpg, "create_pool", fake_create_pool)

    with TestClient(main_module.app) as test_client:
        test_client.fake_db_pool = fake_db_pool
        yield test_client

    get_settings.cache_clear()
    set_runtime_presets(None)
