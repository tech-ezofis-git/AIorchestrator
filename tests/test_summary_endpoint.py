"""End-to-end: `summary` intent -> Dispatcher -> fetch_document (mocked
EZOFIS, never called directly by the agent) -> Response Composer synthesis
-> a summary citing the source document id. Plus the tool-failure path.
"""
def _install_fake_llm(monkeypatch):
    async def fake_chat_completion(self, messages):
        return {
            "content": "This document covers the PTO policy in brief.",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)


def test_summary_intent_returns_summary_with_document_id(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post("/chat", json={"session_id": "s-summary", "message": "summarize document DOC-123"})

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == "DOC-123"
    assert body["reply"] == "This document covers the PTO policy in brief."
    assert body["token_usage"]["total_tokens"] == 15
    # Search/Chat-only fields stay absent for Summary.
    assert body["chunk_ids"] is None
    assert body["cited_data_points"] is None


def test_summary_tool_failure_returns_502(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    async def broken_fetch_document(self, document_id):
        raise RuntimeError("simulated EZOFIS outage, token=should-never-leak")

    monkeypatch.setattr("app.integrations.ezofis_client.EzofisClient.fetch_document", broken_fetch_document)

    response = client.post("/chat", json={"session_id": "s-summary-fail", "message": "summarize document DOC-999"})

    assert response.status_code == 502
    assert "Traceback" not in response.text
    assert "should-never-leak" not in response.text
    assert "detail" in response.json()


def test_summary_with_no_document_reference_falls_back_to_message(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post("/chat", json={"session_id": "s-summary-no-ref", "message": "summarize"})

    assert response.status_code == 200
    # "summarize" itself is the only id-shaped token, so it becomes the
    # (mocked, always-succeeds) document reference.
    assert response.json()["document_id"] == "summarize"
