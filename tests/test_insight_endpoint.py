"""End-to-end: `insight` intent -> Dispatcher -> fetch_report_data (mocked
EZOFIS, never called directly by the agent) -> Response Composer synthesis
-> insights citing the source data points. Plus the tool-failure path.
"""
def _install_fake_llm(monkeypatch):
    async def fake_chat_completion(self, messages):
        return {
            "content": "Overdue invoices are a small share of open invoices.",
            "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)


def test_insight_intent_returns_insights_with_cited_data_points(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post("/chat", json={"session_id": "s-insight", "message": "give me insights on report RPT-456"})

    assert response.status_code == 200
    body = response.json()
    assert body["cited_data_points"] == [
        "Open Invoices",
        "Overdue Invoices",
        "Total Outstanding ($)",
        "Avg Days to Payment",
    ]
    assert body["reply"] == "Overdue invoices are a small share of open invoices."
    assert body["token_usage"]["total_tokens"] == 18
    # Search/Summary-only fields stay absent for Insight.
    assert body["chunk_ids"] is None
    assert body["document_id"] is None


def test_insight_tool_failure_returns_502(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    async def broken_fetch_report_data(self, report_id):
        raise RuntimeError("simulated EZOFIS outage, token=should-never-leak")

    monkeypatch.setattr("app.integrations.ezofis_client.EzofisClient.fetch_report_data", broken_fetch_report_data)

    response = client.post("/chat", json={"session_id": "s-insight-fail", "message": "insights on report RPT-999"})

    assert response.status_code == 502
    assert "Traceback" not in response.text
    assert "should-never-leak" not in response.text
    assert "detail" in response.json()
