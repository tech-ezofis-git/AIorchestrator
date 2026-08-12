"""Phase 5a — proves memory is genuinely cross-session: a fact stored under
one session_id is reflected in a LATER Chat call made under a DIFFERENT
session_id, as long as it's the same user. Relies on MockPermissionProvider
(unconfigured) always resolving to the same user_id regardless of
session_id (app/control/permissions.py) — the whole reason no ChatRequest
schema change was needed to prove "same user" here.

Verified by inspecting what's actually passed to the LLM adapter (message
content), not just the final reply text — same rigor as this repo's other
call-count/call-content assertions (e.g. tests/test_memory_read_degradation.py).
"""


def test_new_session_same_user_sees_previously_stored_memory(client, monkeypatch):
    llm_calls = []

    async def tracking_chat_completion(self, messages):
        llm_calls.append(messages)
        if len(llm_calls) == 1:
            # First call: the memory-write path's fact-extraction call.
            return {
                "content": "Prefers email over phone calls.",
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }
        # Second call: ordinary chat, in a new session.
        return {
            "content": "Sure, I'll email you then.",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", tracking_chat_completion)

    write_response = client.post(
        "/chat",
        json={"session_id": "session-a", "message": "remember that I prefer email over phone calls"},
    )
    assert write_response.status_code == 200
    assert "Prefers email over phone calls." in write_response.json()["reply"]
    assert len(llm_calls) == 1

    chat_response = client.post(
        "/chat", json={"session_id": "session-b", "message": "what's a good way to reach me?"}
    )
    assert chat_response.status_code == 200
    # Still just one MORE call — memory enrichment never adds a second LLM
    # call on top of Chat's normal one (rule 8).
    assert len(llm_calls) == 2

    second_call_messages = llm_calls[1]
    contents = [m["content"] for m in second_call_messages]
    assert any("Prefers email over phone calls." in c for c in contents)
