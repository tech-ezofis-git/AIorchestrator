"""End-to-end: `ocr` intent -> Dispatcher -> run_ocr (mocked engine, never
called directly by the agent) -> pass-through response, with NO synthesis
LLM call made (proven via a call-tracking fake, not just output shape).
"""
def test_ocr_intent_returns_extracted_text_and_confidence_with_no_llm_call(client, monkeypatch):
    llm_calls = []

    async def tracking_chat_completion(self, messages):
        llm_calls.append(messages)
        return {
            "content": "should not be called",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", tracking_chat_completion)

    response = client.post("/chat", json={"session_id": "s-ocr", "message": "run ocr on scan SCN-42"})

    assert response.status_code == 200
    body = response.json()
    assert body["ocr_result"]["source_reference"] == "SCN-42"
    assert 0.0 <= body["ocr_result"]["confidence"] <= 1.0
    assert body["reply"] == body["ocr_result"]["text"]
    assert body["token_usage"] is None
    # The whole point of OCR being pass-through: no synthesis LLM call.
    assert llm_calls == []
    # Other intents' fields stay absent for OCR.
    assert body["chunk_ids"] is None
    assert body["document_id"] is None
    assert body["cited_data_points"] is None
    assert body["forecast_result"] is None


def test_ocr_low_confidence_reference_is_flagged(client):
    response = client.post("/chat", json={"session_id": "s-ocr-blurry", "message": "run ocr on scan BLURRY-SCAN-1"})

    assert response.status_code == 200
    assert response.json()["ocr_result"]["confidence"] < 0.6


def test_ocr_same_reference_is_deterministic(client):
    first = client.post("/chat", json={"session_id": "s-ocr-a", "message": "run ocr on scan SCN-777"})
    second = client.post("/chat", json={"session_id": "s-ocr-b", "message": "run ocr on scan SCN-777"})

    assert first.json()["ocr_result"]["confidence"] == second.json()["ocr_result"]["confidence"]


def test_ocr_tool_failure_returns_502(client, monkeypatch):
    async def broken_run_ocr(self, reference):
        raise RuntimeError("simulated OCR engine outage")

    monkeypatch.setattr("app.integrations.ocr_engine.OcrEngineClient.run_ocr", broken_run_ocr)

    response = client.post("/chat", json={"session_id": "s-ocr-fail", "message": "run ocr on scan SCN-999"})

    assert response.status_code == 502
    assert "Traceback" not in response.text
