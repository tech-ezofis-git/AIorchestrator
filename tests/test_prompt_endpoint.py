"""Prompt agent: explicit intent=prompt → one user message, raw reply.

No EZOFIS system prompt, no session history, no memories, and no JSON
parse/validation even when the model returns JSON-looking text.
"""
import json


_FAKE_JSON = (
    '{"folderName": "Accounts Payable", "description": "Supplier invoices", '
    '"reply": "Ready.", "fields": [{"fieldName": "Vendor", "dataType": "SHORT_TEXT", '
    '"includeInFolderStructure": true, "isMandatory": true, "iconKey": "building"}]}'
)

_MALFORMED = '{not valid json, "folderName":'


def _install_fake_llm(monkeypatch, content=None):
    payload = content if content is not None else _FAKE_JSON
    calls: list[list[dict]] = []

    async def fake_chat_completion(self, messages):
        calls.append(messages)
        return {
            "content": payload,
            "usage": {"prompt_tokens": 8, "completion_tokens": 12, "total_tokens": 20},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)
    return calls


def test_prompt_intent_returns_raw_model_text(client, monkeypatch):
    calls = _install_fake_llm(monkeypatch)
    prompt = "Respond with ONLY a JSON object (no markdown)."

    response = client.post(
        "/chat",
        json={"session_id": "s-prompt", "intent": "prompt", "message": prompt},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Prompt executed successfully."
    assert body["prompt_result"] == {"text": _FAKE_JSON}
    assert body["token_usage"]["total_tokens"] == 20
    assert body["summary_result"] is None
    assert body["ocr_result"] is None
    assert body["insight_result"] is None
    assert body["ap_result"] is None
    assert body["chunk_ids"] is None
    # One user message plus the Prompt skill pack — not Chat's EZOFIS
    # system prompt / history / memories.
    assert len(calls[0]) == 2
    assert calls[0][0]["role"] == "system"
    assert "not the chat assistant" in calls[0][0]["content"].lower()
    assert "output contract" in calls[0][0]["content"].lower()
    assert "passthrough" in calls[0][0]["content"].lower()
    assert "EZOFIS, an enterprise document" not in calls[0][0]["content"]
    assert calls[0][1] == {"role": "user", "content": prompt}
    # We did not parse prompt_result; the string is still valid JSON by coincidence.
    parsed = json.loads(body["prompt_result"]["text"])
    assert parsed["folderName"] == "Accounts Payable"


def test_prompt_does_not_validate_or_parse_json(client, monkeypatch):
    _install_fake_llm(monkeypatch, content=_MALFORMED)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-prompt-bad-json",
            "intent": "prompt",
            "message": "Return JSON for a folder config.",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Prompt executed successfully."
    assert body["prompt_result"] == {"text": _MALFORMED}
    # Orchestrator must not 500 / rewrite just because this is not JSON.
    try:
        json.loads(body["prompt_result"]["text"])
        raise AssertionError("fixture was supposed to be invalid JSON")
    except json.JSONDecodeError:
        pass


def test_prompt_does_not_inject_session_history(client, monkeypatch):
    calls = _install_fake_llm(monkeypatch, content="ok")

    first = client.post("/chat", json={"session_id": "s-prompt-hist", "message": "hello from chat"})
    assert first.status_code == 200

    response = client.post(
        "/chat",
        json={
            "session_id": "s-prompt-hist",
            "intent": "prompt",
            "message": "now run this prompt",
        },
    )

    assert response.status_code == 200
    prompt_call = calls[1]
    assert prompt_call[0]["role"] == "system"
    assert prompt_call[1] == {"role": "user", "content": "now run this prompt"}
    contents = [m["content"] for m in prompt_call]
    assert "hello from chat" not in contents
    assert "EZOFIS, an enterprise document" not in contents
    assert not any(
        m.get("role") == "system" and "Known facts about this user" in (m.get("content") or "")
        for m in prompt_call
    )


def test_word_prompt_without_intent_stays_chat(client, monkeypatch):
    calls = _install_fake_llm(monkeypatch, content="chat-reply")

    response = client.post(
        "/chat",
        json={"session_id": "s-not-prompt", "message": "please prompt me with an idea"},
    )

    assert response.status_code == 200
    roles = [m["role"] for m in calls[0]]
    assert "system" in roles
    assert any("EZOFIS" in (m.get("content") or "") for m in calls[0])


def test_prompt_payload_prompt_alias_when_message_empty(client, monkeypatch):
    calls = _install_fake_llm(monkeypatch, content="aliased")

    response = client.post(
        "/chat",
        json={
            "session_id": "s-prompt-alias",
            "intent": "prompt",
            "payload": {"prompt": "alias body"},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["reply"] == "Prompt executed successfully."
    assert response.json()["prompt_result"] == {"text": "aliased"}
    assert calls[0][0]["role"] == "system"
    assert calls[0][1] == {"role": "user", "content": "alias body"}


def test_prompt_empty_returns_422(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post(
        "/chat",
        json={"session_id": "s-prompt-empty", "intent": "prompt", "message": ""},
    )

    assert response.status_code == 422
