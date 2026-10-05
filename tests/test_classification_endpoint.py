"""Classification intent: OCR text / file / filepath → CLASSIFICATION contract in classification_result."""
import json
import re

from app.classification_skills.contract import model_id_from_display_name, normalize_document_type


_CONTRACT_KEYS = {
    "agent",
    "Classification Status",
    "Classification Completed",
    "source",
    "environment",
    "classification",
    "ocr_text",
    "ERROR CODE",
}

_ENV_PAYLOAD = {
    "envType": "live",
    "tenantId": 11,
    "workflowId": 81,
    "repositoryId": 9,
    "instanceId": "INS-7f3a9c21",
}


def _install_fake_llm(monkeypatch, content=None, calls=None):
    payload = content if content is not None else json.dumps(
        {
            "label": "INVOICE",
            "confidence": 0.91,
            "reason": "Page 1 is a freight invoice with amount due.",
        }
    )

    async def fake_chat_completion(self, messages, **kwargs):
        if calls is not None:
            calls.append({**kwargs, "messages": messages})
        return {
            "content": payload,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)


def _assert_contract(result: dict):
    assert set(result) == _CONTRACT_KEYS
    assert result["agent"] == "CLASSIFICATION"
    assert result["Classification Status"] in {"SUCCEEDED", "FAILED"}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", result["Classification Completed"])
    assert set(result["source"]) == {"blobPath"}
    assert set(result["environment"]) == {"envType", "tenantId", "workflowId", "repositoryId", "instanceId"}
    assert set(result["classification"]) == {"model", "documentType", "confidence"}


def test_classification_from_ocr_text(client, monkeypatch):
    calls = []
    _install_fake_llm(monkeypatch, calls=calls)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-class-ocr",
            "intent": "classification",
            "payload": {
                "ocr_text": "Invoice Number: INV/26-27/002140\nTotal: 1770.00",
                "model": "OpenAI GPT-4.1",
                **_ENV_PAYLOAD,
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Document classification generated successfully."
    result = body["classification_result"]
    _assert_contract(result)
    assert result["Classification Status"] == "SUCCEEDED"
    assert result["classification"] == {
        "model": "OpenAI GPT-4.1",
        "documentType": "INVOICE",
        "confidence": 0.91,
    }
    assert result["environment"] == _ENV_PAYLOAD
    assert result["ocr_text"] == "Invoice Number: INV/26-27/002140\nTotal: 1770.00"
    assert result["ERROR CODE"] is None
    assert calls and calls[0].get("model") == "gpt-4.1"
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
    _assert_contract(result)
    assert result["Classification Status"] == "SUCCEEDED"
    assert result["classification"]["documentType"] == "INVOICE"
    assert result["source"] == {"blobPath": "note.pdf"}
    assert body["document_id"] == "note.pdf"


def test_classification_multipart_with_environment_fields(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post(
        "/chat",
        data={
            "session_id": "s-class-mp-env",
            "intent": "classification",
            "model": "OpenAI GPT-4.1",
            **{k: str(v) for k, v in _ENV_PAYLOAD.items()},
        },
        files={"file": ("invoice_20260916_0034.pdf", b"Invoice text", "application/pdf")},
    )

    assert response.status_code == 200, response.text
    result = response.json()["classification_result"]
    _assert_contract(result)
    assert result["source"] == {"blobPath": "invoice_20260916_0034.pdf"}
    assert result["environment"] == _ENV_PAYLOAD
    assert result["classification"]["model"] == "OpenAI GPT-4.1"


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
    _assert_contract(result)
    assert result["source"] == {"blobPath": "invoice.pdf"}
    assert result["environment"]["tenantId"] == "2e3b7b37-38a3-4f94-878e-a006dad93230"
    assert body["document_id"] == "invoice.pdf"


def test_classification_low_confidence_fails(client, monkeypatch):
    _install_fake_llm(
        monkeypatch,
        content=json.dumps({"label": "INVOICE", "confidence": 0.41, "reason": "Weak invoice signals."}),
    )

    response = client.post(
        "/chat",
        json={
            "session_id": "s-class-low",
            "intent": "classification",
            "payload": {
                "blobPath": "OCR_Inbox/scan_20260916_0036.pdf",
                "ocr_text": "blurry scan text",
                "model": "OpenAI GPT-4.1",
                **_ENV_PAYLOAD,
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    result = body["classification_result"]
    _assert_contract(result)
    assert result["Classification Status"] == "FAILED"
    assert result["source"] == {"blobPath": "OCR_Inbox/scan_20260916_0036.pdf"}
    assert result["classification"]["documentType"] == "UNKNOWN"
    assert result["classification"]["confidence"] == 0.41
    assert result["ERROR CODE"] == (
        "Document type could not be determined with enough confidence (0.41 < 0.80)."
    )
    assert body["reply"].startswith("Document classification failed:")


def test_classification_no_text_fails(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    async def empty_run_ocr(self, *args, **kwargs):
        return {"text": "", "source_reference": kwargs.get("filepath")}

    monkeypatch.setattr("app.integrations.ocr_engine.OcrEngineClient.run_ocr", empty_run_ocr)

    response = client.post(
        "/chat",
        data={"session_id": "s-class-empty", "intent": "classification", **{k: str(v) for k, v in _ENV_PAYLOAD.items()}},
        files={"file": ("blank.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert response.status_code == 200, response.text
    result = response.json()["classification_result"]
    _assert_contract(result)
    assert result["Classification Status"] == "FAILED"
    assert result["classification"]["documentType"] == "UNKNOWN"
    assert result["classification"]["confidence"] == 0.0
    assert result["ocr_text"] == ""
    assert result["ERROR CODE"] == "No text could be extracted from the document."


def test_classification_llm_error_returns_failed_contract(client, monkeypatch):
    async def broken_chat_completion(self, messages, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", broken_chat_completion)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-class-err",
            "intent": "classification",
            "payload": {"ocr_text": "Invoice Number: 1", **_ENV_PAYLOAD},
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["classification_result"]
    _assert_contract(result)
    assert result["Classification Status"] == "FAILED"
    assert result["classification"]["documentType"] == "UNKNOWN"
    assert result["ERROR CODE"].startswith("Classification failed:")


def test_classification_keyword_message(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post(
        "/chat",
        json={"session_id": "s-class-kw", "message": "classify document DOC-123"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["document_id"] == "DOC-123"
    result = body["classification_result"]
    _assert_contract(result)
    assert result["classification"]["documentType"] == "INVOICE"
    assert result["source"] == {"blobPath": "DOC-123"}


def _classify_ocr_text(client, session_id: str) -> dict:
    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "intent": "classification",
            "payload": {"ocr_text": "some OCR text", **_ENV_PAYLOAD},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["classification_result"]


def test_classification_uses_client_prompt_from_skills_folder(client, monkeypatch):
    calls = []
    _install_fake_llm(monkeypatch, calls=calls)

    _classify_ocr_text(client, "s-class-prompt")

    system = calls[0]["messages"][0]["content"]
    assert "expert document classifier for logistics / freight / accounts-payable files" in system
    assert "Many files are an invoice PLUS attachments." in system
    assert "Invoice or credit note anywhere in the OCR → INVOICE or CREDIT_NOTE." in system
    assert "INVOICE, CREDIT_NOTE, STATEMENT, TERMS, BOL, PACKING_LIST, BLANK, OTHER" in system
    assert '{"label":"...","confidence":0.0,"reason":"..."}' in system


def test_classification_credit_note_is_not_an_invoice(client, monkeypatch):
    _install_fake_llm(
        monkeypatch,
        content=json.dumps({"label": "CREDIT_NOTE", "confidence": 0.93, "reason": "Credit memo with amount credited."}),
    )

    result = _classify_ocr_text(client, "s-class-cn")

    assert result["Classification Status"] == "FAILED"
    assert result["classification"]["documentType"] == "CREDIT_NOTE"
    assert result["classification"]["confidence"] == 0.93
    assert result["ERROR CODE"] == "No invoice detected in the document. Detected: CREDIT_NOTE."


def test_classification_non_invoice_fails(client, monkeypatch):
    _install_fake_llm(
        monkeypatch,
        content=json.dumps({"label": "OTHER", "confidence": 0.97, "reason": "A cover letter with no invoice."}),
    )

    response = client.post(
        "/chat",
        json={
            "session_id": "s-class-other",
            "intent": "classification",
            "payload": {"ocr_text": "Dear Sir, please find our company profile.", **_ENV_PAYLOAD},
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    result = body["classification_result"]
    _assert_contract(result)
    assert result["Classification Status"] == "FAILED"
    assert result["classification"]["documentType"] == "OTHER"
    assert result["ocr_text"] == "Dear Sir, please find our company profile."
    assert result["ERROR CODE"] == "No invoice detected in the document. Detected: OTHER."
    assert body["reply"] == (
        "Document classification failed: No invoice detected in the document. Detected: OTHER."
    )


def test_classification_blank_label_fails(client, monkeypatch):
    _install_fake_llm(
        monkeypatch,
        content=json.dumps({"label": "BLANK", "confidence": 0.99, "reason": "Only a logo header."}),
    )

    result = _classify_ocr_text(client, "s-class-blank")

    assert result["Classification Status"] == "FAILED"
    assert result["classification"]["documentType"] == "BLANK"
    assert result["ERROR CODE"] == "Document is blank or unreadable."


def test_classification_unsupported_label_fails(client, monkeypatch):
    _install_fake_llm(
        monkeypatch,
        content=json.dumps({"label": "RECEIPT", "confidence": 0.95, "reason": "Store receipt."}),
    )

    result = _classify_ocr_text(client, "s-class-bad-label")

    assert result["Classification Status"] == "FAILED"
    assert result["classification"]["documentType"] == "UNKNOWN"
    assert result["ERROR CODE"] == "Classifier returned an unsupported label: RECEIPT."


def test_model_display_name_mapping():
    assert model_id_from_display_name("OpenAI GPT-4.1") == "gpt-4.1"
    assert model_id_from_display_name("GPT-4.1 Mini") == "gpt-4.1-mini"
    assert model_id_from_display_name("ezofis-gpu-box") == "ezofis-gpu-box"
    assert model_id_from_display_name("") is None


def test_document_type_normalization():
    assert normalize_document_type("Invoice") == "INVOICE"
    assert normalize_document_type("Purchase Order") == "PURCHASE_ORDER"
    assert normalize_document_type("") == "UNKNOWN"
