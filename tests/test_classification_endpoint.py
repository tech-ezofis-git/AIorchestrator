"""Classification intent: OCR text / file / filepath → locked classification_result."""
import json


_LOCKED = {
    "confidence_score",
    "document_type",
    "rationale",
    "suggested_labels",
    "ocr_text",
}


def _install_fake_llm(monkeypatch, content=None):
    payload = content if content is not None else json.dumps(
        {
            "confidence_score": 91.0,
            "document_type": "Invoice",
            "rationale": "The text includes an invoice number and a payable total.",
            "suggested_labels": ["invoice", "accounts-payable"],
        }
    )

    async def fake_chat_completion(self, messages, **_kwargs):
        return {
            "content": payload,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)


def _assert_locked(payload: dict):
    assert _LOCKED <= set(payload)
    assert isinstance(payload["confidence_score"], (int, float))
    assert isinstance(payload["document_type"], str)
    assert isinstance(payload["rationale"], str)
    assert isinstance(payload["suggested_labels"], list)
    assert isinstance(payload["ocr_text"], str)


def test_classification_from_ocr_text(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-class-ocr",
            "intent": "classification",
            "payload": {
                "ocr_text": "Invoice Number: INV/26-27/002140\nTotal: 1770.00",
                "model": "qwen3.5-9b",
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Document classification generated successfully."
    result = body["classification_result"]
    _assert_locked(result)
    assert result["document_type"] == "Invoice"
    assert result["suggested_labels"] == ["invoice", "accounts-payable"]
    assert "INV/26-27/002140" in result["ocr_text"]
    assert body["document_id"] == "ocr_text"
    assert body["summary_result"] is None
    assert body["token_usage"]["total_tokens"] == 15


def test_classification_multipart_file_upload(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post(
        "/chat",
        data={
            "session_id": "s-class-mp",
            "intent": "classification",
            "pageno": "1",
        },
        files={"file": ("note.pdf", b"Invoice text for classification", "application/pdf")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    result = body["classification_result"]
    _assert_locked(result)
    assert result["document_type"] == "Invoice"
    assert body["document_id"] == "note.pdf"
    assert result["source_reference"] == "note.pdf"


def test_classification_from_filepath(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-class-doc",
            "intent": "classification",
            "payload": {
                "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
                "filepath": "invoice.pdf",
                "pageno": "1",
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    result = body["classification_result"]
    _assert_locked(result)
    assert "Placeholder OCR text" in result["ocr_text"]
    assert body["document_id"] == "invoice.pdf"


def test_classification_keyword_message(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post(
        "/chat",
        json={"session_id": "s-class-kw", "message": "classify document DOC-123"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["document_id"] == "DOC-123"
    _assert_locked(body["classification_result"])
    assert body["classification_result"]["document_type"] == "Invoice"
