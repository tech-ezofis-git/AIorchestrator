"""End-to-end: `ap` intent -> Dispatcher -> fetch_invoice_status (mocked
EZOFIS, never called directly by the agent) -> Response Composer synthesis
-> a plain-language answer citing the invoice reference. Plus the
ambiguous/missing-reference refusal path — this is the higher-stakes
agent (financial data), so it must fail closed: no confident match means
no tool call and no guess, not just happy-path coverage.
"""
def _install_fake_llm(monkeypatch):
    async def fake_chat_completion(self, messages):
        return {
            "content": "Invoice INV-1234 is currently Approved for $1,234.56, due 2026-07-01.",
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)


def test_ap_intent_with_well_formed_reference_returns_synthesized_status(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post(
        "/chat", json={"session_id": "s-ap", "message": "what's the status of invoice INV-1234"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Invoice INV-1234 is currently Approved for $1,234.56, due 2026-07-01."
    assert body["invoice_reference"] == "INV-1234"
    assert body["token_usage"]["total_tokens"] == 30
    # Other intents' fields stay absent for AP.
    assert body["chunk_ids"] is None
    assert body["document_id"] is None
    assert body["ocr_result"] is None
    assert body["forecast_result"] is None
    assert body["ap_result"] is None


def test_ap_intent_with_ambiguous_reference_fails_closed_with_no_tool_call(client, monkeypatch):
    tool_calls = []

    async def tracking_fetch_invoice_status(self, invoice_reference):
        tool_calls.append(invoice_reference)
        return {"invoice_reference": invoice_reference, "status": "Approved", "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.fetch_invoice_status", tracking_fetch_invoice_status
    )

    llm_calls = []

    async def tracking_chat_completion(self, messages):
        llm_calls.append(messages)
        return {"content": "should not be called", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", tracking_chat_completion)

    response = client.post(
        "/chat", json={"session_id": "s-ap-ambiguous", "message": "what's the status of my invoice"}
    )

    assert response.status_code == 200
    body = response.json()
    assert "couldn't identify" in body["reply"].lower()
    assert body["invoice_reference"] is None
    assert body["token_usage"] is None
    # Fail closed: no tool call, no LLM call, no guessed data.
    assert tool_calls == []
    assert llm_calls == []


def test_ap_intent_with_no_reference_at_all_returns_clarification(client):
    response = client.post("/chat", json={"session_id": "s-ap-none", "message": "invoice please"})

    assert response.status_code == 200
    body = response.json()
    assert "couldn't identify" in body["reply"].lower()
    assert body["invoice_reference"] is None


def test_ap_reference_extraction_rejects_underspecified_numbers(client, monkeypatch):
    """Documents the conservative pattern: "INV" prefix + 3+ digits. A
    bare number with no "INV" prefix, or an "INV" reference with too few
    digits, doesn't count as confident — must still fail closed rather
    than guess."""
    tool_calls = []

    async def tracking_fetch_invoice_status(self, invoice_reference):
        tool_calls.append(invoice_reference)
        return {"invoice_reference": invoice_reference, "mock": True}

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.fetch_invoice_status", tracking_fetch_invoice_status
    )

    # No "INV" prefix at all.
    response = client.post("/chat", json={"session_id": "s-ap-bare-number", "message": "invoice 12345"})
    assert response.status_code == 200
    assert "couldn't identify" in response.json()["reply"].lower()

    # "INV" prefix present but only 2 digits — below the 3-digit threshold.
    response = client.post("/chat", json={"session_id": "s-ap-short-inv", "message": "status of invoice INV12"})
    assert response.status_code == 200
    assert "couldn't identify" in response.json()["reply"].lower()

    assert tool_calls == []


def test_ap_tool_failure_returns_502(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    async def broken_fetch_invoice_status(self, invoice_reference):
        raise RuntimeError("simulated EZOFIS AP outage")

    monkeypatch.setattr(
        "app.integrations.ezofis_client.EzofisClient.fetch_invoice_status", broken_fetch_invoice_status
    )

    response = client.post("/chat", json={"session_id": "s-ap-fail", "message": "status of invoice INV-9999"})

    assert response.status_code == 502
    assert "Traceback" not in response.text
