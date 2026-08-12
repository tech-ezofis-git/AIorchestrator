"""Closes a gap from Phase 3a: explicitly proves ToolExecutionError never
leaks raw exception text, a stack trace, or an internal file path to the
API caller — for every registered tool. Extended in Phase 3c to cover
fetch_invoice_status, and in Phase 3d to cover send_email, alongside the
Phase 3a (fetch_document, fetch_report_data) and Phase 3b (run_ocr,
run_forecast) tools.

send_email gets its own dedicated test function below rather than a 6th
entry in `_CASES`: it's requires_confirmation=True, so (unlike the other
five) it can only ever fail via POST /actions/{action_id}/confirm, not a
single /chat call — a fundamentally different call shape the shared
parametrized test isn't built for. Same property under test either way.

Phase 5a adds store_memory, which also gets its own dedicated test rather
than a `_CASES` entry, for a different reason: its LLM call (fact
extraction) happens BEFORE the tool call, unlike all five `_CASES` tools
(tool call first, any synthesis after) — so a fake LLM must be installed
first, or the failure would be masked by an unrelated, expected LLM
failure instead of exercising store_memory's own leak-safety.
fetch_memories needs no such test: per its contract (see
app/control/memory_store.py), it never raises in the first place.
"""
import pytest

# (tool_name, dotted path to the class method to break, request payload
# that routes to the intent using that tool)
_CASES = [
    (
        "fetch_document",
        "app.integrations.ezofis_client.EzofisClient.fetch_document",
        {"session_id": "s-leak-fetch-document", "message": "summarize document DOC-1"},
    ),
    (
        "fetch_report_data",
        "app.integrations.ezofis_client.EzofisClient.fetch_report_data",
        {"session_id": "s-leak-fetch-report-data", "message": "insights on report RPT-1"},
    ),
    (
        "run_ocr",
        "app.integrations.ocr_engine.OcrEngineClient.run_ocr",
        {"session_id": "s-leak-run-ocr", "message": "run ocr on scan SCN-1"},
    ),
    (
        "run_forecast",
        "app.integrations.forecast_model.ForecastModelClient.run_forecast",
        {"session_id": "s-leak-run-forecast", "message": "forecast revenue"},
    ),
    (
        "fetch_invoice_status",
        "app.integrations.ezofis_client.EzofisClient.fetch_invoice_status",
        {"session_id": "s-leak-fetch-invoice-status", "message": "what's the status of invoice INV-123"},
    ),
]


@pytest.mark.parametrize("tool_name,patch_target,payload", _CASES, ids=[c[0] for c in _CASES])
def test_tool_execution_error_never_leaks_internals(client, monkeypatch, tool_name, patch_target, payload):
    secret_marker = f"simulated-{tool_name}-failure, api_key=sk-should-never-leak, path=/etc/secrets/{tool_name}"

    async def broken_handler(*args, **kwargs):
        raise RuntimeError(secret_marker)

    monkeypatch.setattr(patch_target, broken_handler)

    response = client.post("/chat", json=payload)

    assert response.status_code == 502
    body_text = response.text
    assert secret_marker not in body_text
    assert "RuntimeError" not in body_text
    assert "Traceback" not in body_text
    assert "line " not in body_text
    assert ".py" not in body_text
    assert "detail" in response.json()


def test_send_email_tool_execution_error_never_leaks_internals_via_confirm(client, monkeypatch):
    """send_email is requires_confirmation=True, so — unlike the five
    tools above — it can only fail via the confirm endpoint, not a single
    /chat call. Create a real draft via /chat, break the tool, then
    confirm and prove nothing leaks: same property, different call shape.
    """

    async def fake_chat_completion(self, messages):
        return {
            "content": "Subject: Test\nBody:\nHello.",
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    draft_response = client.post(
        "/chat",
        json={"session_id": "s-leak-send-email", "message": "send an email to leak-test@example.com about x"},
    )
    action_id = draft_response.json()["mail_draft"]["action_id"]

    secret_marker = "simulated-send_email-failure, api_key=sk-should-never-leak, path=/etc/secrets/send_email"

    async def broken_send_email(self, *, recipient, subject, body):
        raise RuntimeError(secret_marker)

    monkeypatch.setattr("app.integrations.email_client.EmailClient.send_email", broken_send_email)

    response = client.post(f"/actions/{action_id}/confirm", params={"session_id": "s-leak-send-email"})

    assert response.status_code == 502
    body_text = response.text
    assert secret_marker not in body_text
    assert "RuntimeError" not in body_text
    assert "Traceback" not in body_text
    assert "line " not in body_text
    assert ".py" not in body_text
    assert "detail" in response.json()


def test_store_memory_tool_execution_error_never_leaks_internals(client, monkeypatch):
    """store_memory needs its own dedicated test, not a `_CASES` entry: its
    LLM call (fact extraction) happens BEFORE the tool call, unlike every
    other case above (tool first, optional synthesis after) — so a fake
    LLM must be installed first, or the failure would be masked by an
    (expected, unrelated) LLM failure instead of exercising store_memory's
    own leak-safety.
    """

    async def fake_chat_completion(self, messages):
        return {"content": "Likes tea.", "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    secret_marker = "simulated-store_memory-failure, api_key=sk-should-never-leak, path=/etc/secrets/store_memory"

    async def broken_store(self, *, user_id, fact):
        raise RuntimeError(secret_marker)

    monkeypatch.setattr("app.control.memory_store.MemoryStore.store", broken_store)

    response = client.post("/chat", json={"session_id": "s-leak-store-memory", "message": "remember that I like tea"})

    assert response.status_code == 502
    body_text = response.text
    assert secret_marker not in body_text
    assert "RuntimeError" not in body_text
    assert "Traceback" not in body_text
    assert "line " not in body_text
    assert ".py" not in body_text
    assert "detail" in response.json()
