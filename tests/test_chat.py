def _install_fake_llm(monkeypatch):
    """Replace ChatAgent's LLM call with a deterministic, call-recording fake."""
    calls: list[list[dict]] = []

    async def fake_chat_completion(self, messages):
        calls.append(messages)
        return {"content": f"reply-{len(calls)}", "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)
    return calls


def test_chat_returns_200_with_expected_shape(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post("/chat", json={"session_id": "s1", "message": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "s1"
    assert body["reply"] == "reply-1"
    assert "correlation_id" in body
    assert body["latency_ms"] >= 0
    assert body["token_usage"]["total_tokens"] == 10


def test_chat_session_continuity_across_two_calls(client, monkeypatch):
    calls = _install_fake_llm(monkeypatch)

    first = client.post("/chat", json={"session_id": "s2", "message": "my name is Ada"})
    assert first.status_code == 200
    assert first.json()["reply"] == "reply-1"

    second = client.post("/chat", json={"session_id": "s2", "message": "what did I just say?"})
    assert second.status_code == 200
    assert second.json()["reply"] == "reply-2"

    # The second LLM call must have received the first turn's history —
    # proving the Context Manager persisted and reloaded it from Redis.
    second_call_messages = calls[1]
    contents = [m["content"] for m in second_call_messages]
    assert "my name is Ada" in contents
    assert "reply-1" in contents
    assert "what did I just say?" in contents


def test_chat_different_sessions_do_not_share_history(client, monkeypatch):
    calls = _install_fake_llm(monkeypatch)

    client.post("/chat", json={"session_id": "session-a", "message": "hello from A"})
    client.post("/chat", json={"session_id": "session-b", "message": "hello from B"})

    # session-b's call must not contain session-a's message.
    session_b_call = calls[1]
    contents = [m["content"] for m in session_b_call]
    assert "hello from A" not in contents
