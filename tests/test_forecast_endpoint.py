"""End-to-end: `forecast` intent -> Dispatcher -> run_forecast (mocked
model, never called directly by the agent) -> Response Composer narrates
the raw numbers -> response includes both the narration and the numbers.
"""
def _install_fake_llm(monkeypatch):
    async def fake_chat_completion(self, messages):
        return {
            "content": "Revenue is projected to grow steadily over the next quarter.",
            "usage": {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)


def test_forecast_intent_returns_narration_and_raw_numbers(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post(
        "/chat", json={"session_id": "s-forecast", "message": "forecast revenue for next quarter"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Revenue is projected to grow steadily over the next quarter."
    assert body["forecast_result"]["metric"] == "revenue"
    assert body["forecast_result"]["horizon"] == "next quarter"
    assert len(body["forecast_result"]["predicted_values"]) == 4
    assert len(body["forecast_result"]["confidence_interval"]) == 4
    assert body["token_usage"]["total_tokens"] == 23
    # Other intents' fields stay absent for Forecast.
    assert body["chunk_ids"] is None
    assert body["document_id"] is None
    assert body["ocr_result"] is None


def test_forecast_same_metric_is_deterministic(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    first = client.post("/chat", json={"session_id": "s-forecast-a", "message": "forecast revenue"})
    second = client.post("/chat", json={"session_id": "s-forecast-b", "message": "forecast revenue"})

    assert (
        first.json()["forecast_result"]["predicted_values"]
        == second.json()["forecast_result"]["predicted_values"]
    )
    # No horizon phrase in the message -> falls back to the default.
    assert first.json()["forecast_result"]["horizon"] == "next quarter"


def test_forecast_tool_failure_returns_502(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    async def broken_run_forecast(self, metric, horizon):
        raise RuntimeError("simulated forecast model outage")

    monkeypatch.setattr("app.integrations.forecast_model.ForecastModelClient.run_forecast", broken_run_forecast)

    response = client.post("/chat", json={"session_id": "s-forecast-fail", "message": "forecast revenue"})

    assert response.status_code == 502
    assert "Traceback" not in response.text
