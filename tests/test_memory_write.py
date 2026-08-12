"""Phase 5a — the memory WRITE path: an explicit "remember that ..." (etc.)
trigger extracts a clean fact via one LLM call, persists it via
store_memory, and confirms it in the normal /chat response. Write failures
must be an explicit error, never a false "I'll remember that" (rule 6) —
see app/control/memory_store.py and app/agents/chat_agent.py.
"""


def _install_fake_llm(monkeypatch, content="Prefers email over phone calls."):
    async def fake_chat_completion(self, messages):
        return {"content": content, "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)


def test_remember_that_stores_a_fact_and_confirms_it(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post(
        "/chat", json={"session_id": "s-mem-write", "message": "remember that I prefer email over phone calls"}
    )

    assert response.status_code == 200
    body = response.json()
    assert "Prefers email over phone calls." in body["reply"]
    assert len(client.fake_db_pool.memories) == 1
    assert client.fake_db_pool.memories[0]["fact"] == "Prefers email over phone calls."
    # MockPermissionProvider(), unconfigured, always resolves to this
    # user_id regardless of session_id — see app/control/permissions.py.
    assert client.fake_db_pool.memories[0]["user_id"] == "anonymous"


def test_memory_write_various_trigger_phrases(client, monkeypatch):
    _install_fake_llm(monkeypatch, content="Likes concise answers.")

    for phrase in [
        "please remember I like concise answers",
        "for future reference, I like concise answers",
        "don't forget that I like concise answers",
    ]:
        response = client.post("/chat", json={"session_id": "s-mem-triggers", "message": phrase})
        assert response.status_code == 200

    assert len(client.fake_db_pool.memories) == 3


def test_memory_store_db_outage_returns_clean_error_not_false_success(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    async def broken_execute(*args, **kwargs):
        raise ConnectionError("simulated postgres outage")

    monkeypatch.setattr(client.fake_db_pool, "execute", broken_execute)

    response = client.post(
        "/chat", json={"session_id": "s-mem-fail", "message": "remember that I prefer email"}
    )

    assert response.status_code == 502
    assert "Traceback" not in response.text
    assert client.fake_db_pool.memories == []
