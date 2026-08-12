"""Phase 5a — the memory READ path degrades gracefully. A memory-store
outage during fetch must not break Chat's normal response: it's just
logged as a warning and treated as "no memories" (rule 7), and it must
never add a second LLM call (rule 8) — see app/control/memory_store.py and
app/agents/chat_agent.py.
"""


def test_chat_succeeds_when_memory_read_fails(client, monkeypatch):
    llm_calls = []

    async def tracking_chat_completion(self, messages):
        llm_calls.append(messages)
        return {"content": "Sure thing!", "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", tracking_chat_completion)

    async def broken_fetch(*args, **kwargs):
        raise ConnectionError("simulated postgres outage during memory read")

    monkeypatch.setattr(client.fake_db_pool, "fetch", broken_fetch)

    response = client.post("/chat", json={"session_id": "s-mem-read-fail", "message": "hello there"})

    assert response.status_code == 200
    assert response.json()["reply"] == "Sure thing!"
    # Exactly one LLM call — memory enrichment (or its failure) never adds
    # a second one (rule 8).
    assert len(llm_calls) == 1
    contents = [m["content"] for m in llm_calls[0]]
    assert not any("Known facts" in c for c in contents)


def test_memory_read_failure_logs_a_warning(client, monkeypatch):
    import app.control.memory_store as memory_store_module

    async def broken_fetch(*args, **kwargs):
        raise ConnectionError("simulated postgres outage")

    monkeypatch.setattr(client.fake_db_pool, "fetch", broken_fetch)

    warning_calls = []

    def tracking_warning(msg, *args, **kwargs):
        warning_calls.append(msg)

    monkeypatch.setattr(memory_store_module.logger, "warning", tracking_warning)

    async def fake_chat_completion(self, messages):
        return {"content": "ok", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    response = client.post("/chat", json={"session_id": "s-mem-read-fail-2", "message": "hi"})

    assert response.status_code == 200
    assert "memory_store_read_failed" in warning_calls
