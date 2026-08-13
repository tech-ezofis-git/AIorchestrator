"""End-to-end: `summary` intent -> Dispatcher -> fetch_document (mocked
EZOFIS, never called directly by the agent) -> Response Composer synthesis
-> a summary citing the source document id. Plus document-job JSON shape
and the tool-failure path.
"""
import json


_STRUCTURED_SUMMARY = {
    "confidence_score": 82.0,
    "document_type": "Letter",
    "document_title": "Broker Appointment Letter",
    "document_language": "English",
    "document_summary": (
        "This is a broker appointment letter from EFG Hermes Oman notifying "
        "Muscat Stock Exchange of two newly appointed brokers."
    ),
    "key_facts_extracted": [
        "Issuer: EFG Hermes Oman LLC",
        "Date: 2023/08/27",
        "Reference: EFG/10/2023",
    ],
    "ocr_text": "THIS SHOULD BE REPLACED BY PADDLE TEXT",
}


def _install_fake_llm(monkeypatch, content=None):
    payload = content if content is not None else "This document covers the PTO policy in brief."

    async def fake_chat_completion(self, messages):
        return {
            "content": payload,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)


_SUMMARY_KEYS = {
    "confidence_score",
    "document_type",
    "document_title",
    "document_language",
    "document_summary",
    "key_facts_extracted",
    "ocr_text",
}
_REMOVED_SUMMARY_KEYS = {
    "compliance_and_risk_assessment",
    "ai_recommendations",
    "supplier_trend_insight",
}


def _assert_locked_summary_shape(payload: dict):
    assert _SUMMARY_KEYS <= set(payload)
    assert _REMOVED_SUMMARY_KEYS.isdisjoint(payload)
    assert isinstance(payload["confidence_score"], (int, float))
    assert isinstance(payload["document_type"], str)
    assert isinstance(payload["document_title"], str)
    assert isinstance(payload["document_language"], str)
    assert isinstance(payload["document_summary"], str)
    assert isinstance(payload["key_facts_extracted"], list)
    assert isinstance(payload["ocr_text"], str)


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


def test_summary_document_job_from_filepath(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-sum-doc",
            "intent": "summary",
            "payload": {
                "filepath": "container/invoice.pdf",
                "pageno": "1",
                "model": "qwen3.5-9b",
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Document summary generated successfully."
    result = body["summary_result"]
    _assert_locked_summary_shape(result)
    assert result["document_summary"] == "This document covers the PTO policy in brief."
    assert "Placeholder OCR text" in result["ocr_text"]
    assert body["document_id"] == "container/invoice.pdf"
    assert body["token_usage"]["total_tokens"] == 15
    assert body["ocr_result"] is None
    assert result["source_reference"] == "container/invoice.pdf"


def _minimal_docx_bytes(paragraphs: list[str]) -> bytes:
    import io
    import zipfile

    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        zf.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def test_summary_multipart_file_upload(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post(
        "/chat",
        data={
            "session_id": "s-sum-mp",
            "intent": "summary",
            "pageno": "1",
        },
        files={"file": ("note.pdf", b"Invoice text for summary", "application/pdf")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Document summary generated successfully."
    _assert_locked_summary_shape(body["summary_result"])
    assert body["summary_result"]["document_summary"] == "This document covers the PTO policy in brief."
    assert body["document_id"] == "note.pdf"
    assert body["ocr_result"] is None
    assert body["summary_result"]["source_reference"] == "note.pdf"


def test_summary_extract_failure_does_not_hallucinate(client, monkeypatch):
    llm_calls = []

    async def tracking_chat_completion(self, messages):
        llm_calls.append(messages)
        return {"content": "should not summarize", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", tracking_chat_completion)

    async def broken_run_ocr(self, *args, **kwargs):
        from app.integrations.ocr_engine import OcrEngineError

        raise OcrEngineError("boom")

    monkeypatch.setattr("app.integrations.ocr_engine.OcrEngineClient.run_ocr", broken_run_ocr)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-sum-fail-ocr",
            "intent": "summary",
            "payload": {"filepath": "container/missing.pdf"},
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    result = body["summary_result"]
    _assert_locked_summary_shape(result)
    assert "can't summarize" in body["reply"].lower() or "couldn" in body["reply"].lower()
    assert result["ocr_text"] == ""
    assert result["key_facts_extracted"] == []
    assert result["confidence_score"] == 0.0
    assert llm_calls == []
    assert body["document_id"] == "container/missing.pdf"


def test_summary_document_job_locked_json_from_llm(client, monkeypatch):
    _install_fake_llm(monkeypatch, content=json.dumps(_STRUCTURED_SUMMARY))

    response = client.post(
        "/chat",
        json={
            "session_id": "s-sum-json",
            "intent": "summary",
            "payload": {"filepath": "container/letter.pdf", "pageno": "1"},
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Document summary generated successfully."
    result = body["summary_result"]
    _assert_locked_summary_shape(result)
    assert result["confidence_score"] == 82.0
    assert result["document_type"] == "Letter"
    assert result["document_title"] == "Broker Appointment Letter"
    assert result["document_language"] == "English"
    assert result["document_summary"] == _STRUCTURED_SUMMARY["document_summary"]
    assert result["key_facts_extracted"] == _STRUCTURED_SUMMARY["key_facts_extracted"]
    assert "Placeholder OCR text" in result["ocr_text"]
    assert "THIS SHOULD BE REPLACED" not in result["ocr_text"]
    assert result["source_reference"] == "container/letter.pdf"


def test_summary_unwraps_truncated_model_json_missing_brace():
    from app.agents.summary_agent import _document_job_result

    truncated = (
        '{"confidence_score":95.0,"document_type":"Invoice","document_title":"Internet Service Invoice",'
        '"document_language":"English","document_summary":"This is an invoice from Niss.",'
        '"key_facts_extracted":["Issuer: Niss","Total Amount: 1770.00"]'
    )
    assert not truncated.endswith("}")
    result = _document_job_result(
        {
            "confidence_score": 0.0,
            "document_type": "",
            "document_title": "",
            "document_language": "",
            "document_summary": truncated,
            "key_facts_extracted": [],
            "ocr_text": "Niss Internet Services",
        },
        source="container/invoice.pdf",
        usage=None,
    )
    body = result["summary_result"]
    assert body["confidence_score"] == 95.0
    assert body["document_summary"] == "This is an invoice from Niss."
    assert body["key_facts_extracted"] == ["Issuer: Niss", "Total Amount: 1770.00"]


def test_summary_agent_unwraps_stuffed_payload_string():
    from app.agents.summary_agent import _document_job_result

    stuffed = json.dumps(
        {
            "confidence_score": 95.0,
            "document_type": "Invoice",
            "document_title": "Internet Service Invoice",
            "document_language": "English",
            "document_summary": "This is an invoice from Niss Internet Services Private Limited.",
            "key_facts_extracted": ["Issuer: Niss Internet Services Private Limited", "Total Amount: 1770.00"],
        }
    )
    result = _document_job_result(
        {
            "confidence_score": 0.0,
            "document_type": "",
            "document_title": "",
            "document_language": "",
            "document_summary": stuffed,
            "key_facts_extracted": [],
            "ocr_text": "Niss Internet Services Private Limited",
        },
        source="container/invoice.pdf",
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )
    assert result["reply"] == "Document summary generated successfully."
    body = result["summary_result"]
    assert body["confidence_score"] == 95.0
    assert body["document_summary"].startswith("This is an invoice")
    assert body["key_facts_extracted"][0].startswith("Issuer:")
    assert body["ocr_text"].startswith("Niss")
    assert body["source_reference"] == "container/invoice.pdf"


def test_summary_unwraps_json_stuffed_in_document_summary(client, monkeypatch):
    stuffed = json.dumps(
        {
            "confidence_score": 95.0,
            "document_type": "Invoice",
            "document_title": "Internet Service Invoice",
            "document_language": "English",
            "document_summary": (
                "This is an invoice from Niss Internet Services Private Limited "
                "to EZOFIS SOFTWARE CONSULTANCY PRIVATE LIMITED for internet service charges."
            ),
            "key_facts_extracted": [
                "Issuer: Niss Internet Services Private Limited",
                "Invoice Number: INV/26-27/002140",
                "Total Amount: 1770.00",
            ],
        },
        ensure_ascii=False,
    )
    # Mimic the broken shape: the model JSON arrives as a raw string field.
    _install_fake_llm(monkeypatch, content=json.dumps({"document_summary": stuffed}))

    response = client.post(
        "/chat",
        json={
            "session_id": "s-sum-stuffed",
            "intent": "summary",
            "payload": {"filepath": "container/invoice.pdf", "pageno": "1"},
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Document summary generated successfully."
    result = body["summary_result"]
    assert result["confidence_score"] == 95.0
    assert result["document_summary"].startswith("This is an invoice")
    assert not result["document_summary"].lstrip().startswith("{")
    assert "INV/26-27/002140" in result["key_facts_extracted"][1]
    assert result["document_type"] == "Invoice"
    assert result["document_title"] == "Internet Service Invoice"
    assert result["document_language"] == "English"
    assert "Placeholder OCR text" in result["ocr_text"]


def test_summary_unwraps_double_encoded_llm_json(client, monkeypatch):
    _install_fake_llm(monkeypatch, content=json.dumps(json.dumps(_STRUCTURED_SUMMARY)))

    response = client.post(
        "/chat",
        json={
            "session_id": "s-sum-double",
            "intent": "summary",
            "payload": {"filepath": "container/letter.pdf", "pageno": "1"},
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Document summary generated successfully."
    result = body["summary_result"]
    assert result["confidence_score"] == 82.0
    assert result["document_summary"] == _STRUCTURED_SUMMARY["document_summary"]
    assert result["key_facts_extracted"] == _STRUCTURED_SUMMARY["key_facts_extracted"]
    assert not result["document_summary"].lstrip().startswith("{")


def test_summary_prompt_is_type_dynamic_not_invoice_only():
    from app.core.response_composer import _FILE_SUMMARY_JSON_SYSTEM_PROMPT

    prompt = _FILE_SUMMARY_JSON_SYSTEM_PROMPT.lower()
    assert "infer the document type" in prompt
    assert "insurance" in prompt
    assert "never call it an invoice unless" in prompt
    assert "document_type" in prompt
    assert "document_title" in prompt
    assert "document_language" in prompt
    assert "compliance_and_risk_assessment" not in prompt
    assert "ai_recommendations" not in prompt
    assert "supplier_trend_insight" not in prompt


def test_summary_preserves_insurance_wording_from_model(client, monkeypatch):
    insurance = {
        "confidence_score": 88.0,
        "document_type": "Insurance Policy",
        "document_title": "Motor Insurance Policy",
        "document_language": "English",
        "document_summary": (
            "This is a motor insurance policy issued by ABC General Insurance "
            "covering the insured vehicle for the stated period."
        ),
        "key_facts_extracted": [
            "Insurer: ABC General Insurance",
            "Policy Number: POL-77821",
            "Coverage: Own damage and third party",
        ],
        "ocr_text": "SHOULD BE REPLACED",
    }
    _install_fake_llm(monkeypatch, content=json.dumps(insurance))

    response = client.post(
        "/chat",
        json={
            "session_id": "s-sum-ins",
            "intent": "summary",
            "payload": {
                "ocr_text": "ABC General Insurance\nMotor Policy POL-77821\nOwn damage and third party"
            },
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["summary_result"]
    assert "insurance" in result["document_summary"].lower()
    assert "invoice" not in result["document_summary"].lower()
    assert result["document_type"] == "Insurance Policy"
    assert result["document_title"] == "Motor Insurance Policy"
    assert result["document_language"] == "English"
    assert result["key_facts_extracted"][0].startswith("Insurer:")
    assert "POL-77821" in result["ocr_text"]


def test_summary_invalid_pageno_rejected(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "s-sum-page",
            "intent": "summary",
            "payload": {"filepath": "container/file.pdf", "pageno": "9"},
        },
    )
    assert response.status_code == 400


def test_summary_from_direct_ocr_text_skips_paddle(client, monkeypatch):
    _install_fake_llm(monkeypatch, content=json.dumps(_STRUCTURED_SUMMARY))
    ocr_calls = []

    async def tracking_run_ocr(self, *args, **kwargs):
        ocr_calls.append((args, kwargs))
        raise AssertionError("run_ocr must not run when ocr_text is supplied")

    monkeypatch.setattr("app.integrations.ocr_engine.OcrEngineClient.run_ocr", tracking_run_ocr)

    supplied = "Niss Internet Services Private Limited\nInvoice Number: INV/26-27/002140\nTotal: 1770.00"
    response = client.post(
        "/chat",
        json={
            "session_id": "s-sum-ocr-text",
            "intent": "summary",
            "payload": {"ocr_text": supplied, "model": "qwen3.5-9b"},
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Document summary generated successfully."
    result = body["summary_result"]
    _assert_locked_summary_shape(result)
    assert result["ocr_text"] == supplied
    assert "Placeholder OCR text" not in result["ocr_text"]
    assert result["source_reference"] == "ocr_text"
    assert body["document_id"] == "ocr_text"
    assert body["ocr_result"] is None
    assert ocr_calls == []


def test_summary_ocr_text_wins_over_filepath(client, monkeypatch):
    _install_fake_llm(monkeypatch)
    ocr_calls = []

    async def tracking_run_ocr(self, *args, **kwargs):
        ocr_calls.append(kwargs)
        return {"text": "Placeholder OCR text", "source_reference": "blob"}

    monkeypatch.setattr("app.integrations.ocr_engine.OcrEngineClient.run_ocr", tracking_run_ocr)

    supplied = "Direct OCR line from caller."
    response = client.post(
        "/chat",
        json={
            "session_id": "s-sum-ocr-wins",
            "intent": "summary",
            "payload": {
                "filepath": "container/invoice.pdf",
                "pageno": "1",
                "ocr_text": supplied,
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary_result"]["ocr_text"] == supplied
    assert body["summary_result"]["source_reference"] == "ocr_text"
    assert ocr_calls == []


def test_summary_multipart_ocr_text(client, monkeypatch):
    _install_fake_llm(monkeypatch)
    ocr_calls = []

    async def tracking_run_ocr(self, *args, **kwargs):
        ocr_calls.append(True)
        raise AssertionError("run_ocr must not run for multipart ocr_text")

    monkeypatch.setattr("app.integrations.ocr_engine.OcrEngineClient.run_ocr", tracking_run_ocr)

    response = client.post(
        "/chat",
        data={
            "session_id": "s-sum-mp-text",
            "intent": "summary",
            "ocr_text": "Pasted invoice text for summary",
        },
        files={"file": ("note.pdf", b"should not be extracted", "application/pdf")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Document summary generated successfully."
    assert body["summary_result"]["ocr_text"] == "Pasted invoice text for summary"
    assert body["document_id"] == "ocr_text"
    assert ocr_calls == []


def test_summary_empty_ocr_text_with_no_file_is_legacy_or_rejected(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-sum-empty-text",
            "intent": "summary",
            "payload": {"ocr_text": "   "},
        },
    )

    # Whitespace-only is not a document job; hallway uses the default message.
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary_result"] is None


def test_summary_docx_upload_skips_paddle(client, monkeypatch):
    _install_fake_llm(monkeypatch, content=json.dumps(_STRUCTURED_SUMMARY))
    paddle_calls = []

    async def tracking_extract(self, *args, **kwargs):
        paddle_calls.append(kwargs)
        raise AssertionError("Paddle must not run for .docx")

    monkeypatch.setattr(
        "app.integrations.ocr_engine.OcrEngineClient._call_extract_text",
        tracking_extract,
    )

    docx = _minimal_docx_bytes(
        [
            "ABC General Insurance",
            "Motor Policy POL-77821",
            "Own damage and third party",
        ]
    )
    response = client.post(
        "/chat",
        data={"session_id": "s-sum-docx", "intent": "summary"},
        files={
            "file": (
                "policy.docx",
                docx,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Document summary generated successfully."
    result = body["summary_result"]
    assert "ABC General Insurance" in result["ocr_text"]
    assert "POL-77821" in result["ocr_text"]
    assert "Placeholder OCR text" not in result["ocr_text"]
    assert body["document_id"] == "policy.docx"
    assert paddle_calls == []


def test_summary_invalid_docx_fails_closed(client, monkeypatch):
    llm_calls = []

    async def tracking_chat_completion(self, messages):
        llm_calls.append(messages)
        return {"content": "should not summarize", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", tracking_chat_completion)

    response = client.post(
        "/chat",
        data={"session_id": "s-sum-bad-docx", "intent": "summary"},
        files={"file": ("broken.docx", b"not-a-zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "can't summarize" in body["reply"].lower() or "couldn" in body["reply"].lower()
    assert body["summary_result"]["ocr_text"] == ""
    assert llm_calls == []


def test_summary_legacy_doc_is_not_supported(client, monkeypatch):
    llm_calls = []

    async def tracking_chat_completion(self, messages):
        llm_calls.append(messages)
        return {"content": "should not summarize", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", tracking_chat_completion)

    response = client.post(
        "/chat",
        data={"session_id": "s-sum-legacy-doc", "intent": "summary"},
        files={"file": ("letter.doc", b"OLE-fake", "application/msword")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary_result"]["ocr_text"] == ""
    assert llm_calls == []
