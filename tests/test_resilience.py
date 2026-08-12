"""Proves the app's failure-handling contract actually holds:
  - Redis failures (ContextManager) must surface as an explicit 503 —
    never a silent, unsaved 200.
  - LLM provider failures (LLMAdapter / POST /chat) must degrade to a
    generic 502, never leak a stack trace or provider internals.
"""
import pytest

from app.core.context_manager import ContextManager, SessionStoreUnavailableError


class _BrokenRedis:
    """Stands in for a Redis client that can't reach the server at all."""

    async def get(self, key):
        raise ConnectionError("simulated redis outage")

    async def set(self, key, value, ex=None):
        raise ConnectionError("simulated redis outage")


async def test_get_history_raises_session_store_unavailable_on_redis_outage():
    context_manager = ContextManager(_BrokenRedis(), ttl_seconds=60)

    with pytest.raises(SessionStoreUnavailableError):
        await context_manager.get_history("s1")


async def test_append_turn_raises_session_store_unavailable_on_redis_outage():
    context_manager = ContextManager(_BrokenRedis(), ttl_seconds=60)

    # History must never be silently dropped while still "succeeding" —
    # a Redis outage must raise, not swallow.
    with pytest.raises(SessionStoreUnavailableError):
        await context_manager.append_turn("s1", "user", "hello")


def test_chat_returns_503_when_redis_is_down(client, monkeypatch):
    """End-to-end: break the live app's Redis client mid-flight and confirm
    POST /chat returns 503 — not 200 with silently-dropped history."""
    import app.main as main_module

    async def broken_get(*args, **kwargs):
        raise ConnectionError("simulated redis outage")

    monkeypatch.setattr(main_module.app.state.context_manager._redis, "get", broken_get)

    response = client.post("/chat", json={"session_id": "s-redis-down", "message": "hi"})

    assert response.status_code == 503
    body = response.json()
    assert "detail" in body
    assert "Traceback" not in response.text
    assert "simulated redis outage" not in response.text


def test_chat_returns_502_when_llm_provider_fails(client, monkeypatch):
    async def broken_acompletion(*args, **kwargs):
        raise RuntimeError("simulated provider timeout, key=sk-should-never-appear")

    monkeypatch.setattr("litellm.acompletion", broken_acompletion)

    response = client.post("/chat", json={"session_id": "s-broken", "message": "hi"})

    assert response.status_code == 502
    body = response.json()
    assert "sk-should-never-appear" not in response.text
    assert "Traceback" not in response.text
    assert "detail" in body


def test_chat_unknown_intent_returns_501_not_a_crash(client, monkeypatch):
    """Proves the Agent Router's NotImplementedError path is handled by
    /chat as a clean 501, not left to bubble up as a raw 500.

    By Phase 3d every real Intent value is registered in the live app —
    unlike Phases 2/3a/3b/3c, there's no longer a real, still-unregistered
    Intent left to force. So instead of forcing one (which would now
    break the moment the next phase registers it, same as this test's own
    history), swap in a bare AgentRouter (only a dummy chat handler
    registered) for the duration of this test — any non-chat intent is
    "unregistered" from *its* point of view, proving the same underlying
    mechanism without depending on which real intents happen to be wired
    up. This version doesn't need to move forward again.
    """
    import app.main as main_module
    from app.core.agent_router import AgentRouter
    from app.core.intent_router import Intent

    class _UnusedChatAgent:
        async def handle(self, **kwargs):
            raise AssertionError("chat handler should not be invoked in this test")

    monkeypatch.setattr(main_module.app.state, "agent_router", AgentRouter(_UnusedChatAgent()))

    async def classify_as_search(self, message):
        return Intent.SEARCH

    monkeypatch.setattr("app.core.intent_router.IntentRouter.classify", classify_as_search)

    response = client.post("/chat", json={"session_id": "s-intent", "message": "anything"})

    assert response.status_code == 501
    assert "Traceback" not in response.text
