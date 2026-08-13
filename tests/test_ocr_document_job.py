"""OCR document-job tests: intent routing, pageno, locked JSON, multipart, blob paths."""
import json

import pytest


def test_legacy_ocr_keyword_path_still_works(client, monkeypatch):
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
    assert body["reply"] == body["ocr_result"]["text"]
    assert llm_calls == []


def test_explicit_ocr_document_job_locked_json_shape(client, monkeypatch):
    async def fake_completion(self, messages):
        return {
            "content": json.dumps(
                {
                    "ocrResult": [
                        {"name": "Invoice No", "value": "INV/26-27/002140", "type": "SHORT_TEXT"},
                        {"name": "Due Date", "value": "2026-05-20", "type": "DATE"},
                    ],
                    "tableResult": [],
                }
            ),
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_completion)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-doc",
            "intent": "ocr",
            "instruction": "Region: India. Normalize DATE fields to YYYY-MM-DD.",
            "payload": {
                "filepath": r"ezts2e3b7b3738a34f94878ea006dad93230\INV26-27002140.pdf",
                "pageno": "1",
                "parameters": ["Invoice No,SHORT_TEXT", "Due Date,DATE"],
                "tableparameters": [],
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    reply = json.loads(body["reply"])
    assert "ocrResult" in reply
    assert reply["ocrResult"][0]["name"] == "Invoice No"
    assert reply["ocrResult"][0]["value"] == "INV/26-27/002140"
    assert reply["ocr_json"]["ocrResult"] == reply["ocrResult"]
    assert "tokens" in reply
    assert "ocr_text" in reply
    assert reply["ocr_text"]
    assert body["ocr_result"]["ocrResult"] == reply["ocrResult"]
    assert "model_used" not in reply


def test_ocr_fail_returns_null_fields_no_hallucination(client, monkeypatch):
    async def broken_run_ocr(self, *args, **kwargs):
        from app.integrations.ocr_engine import OcrEngineError

        raise OcrEngineError("boom")

    monkeypatch.setattr("app.integrations.ocr_engine.OcrEngineClient.run_ocr", broken_run_ocr)

    llm_calls = []

    async def tracking_chat_completion(self, messages):
        llm_calls.append(messages)
        return {"content": "{}", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", tracking_chat_completion)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-fail",
            "intent": "ocr",
            "payload": {
                "filepath": "container/file.pdf",
                "parameters": ["Invoice No,SHORT_TEXT", "Due Date,DATE"],
            },
        },
    )

    assert response.status_code == 200, response.text
    reply = json.loads(response.json()["reply"])
    assert reply["ocr_text"] == ""
    assert reply["ocrResult"] == [
        {"name": "Invoice No", "value": None, "type": "SHORT_TEXT"},
        {"name": "Due Date", "value": None, "type": "DATE"},
    ]
    assert llm_calls == []


def test_invalid_pageno_rejected(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "s-page",
            "intent": "ocr",
            "payload": {"filepath": "container/file.pdf", "pageno": "9"},
        },
    )
    assert response.status_code == 400


def test_unknown_intent_rejected(client):
    response = client.post(
        "/chat",
        json={"session_id": "s-x", "intent": "nope", "message": "hi"},
    )
    assert response.status_code == 400


def test_multipart_file_upload_ocr(client, monkeypatch):
    async def fake_completion(self, messages):
        return {
            "content": json.dumps(
                {
                    "ocrResult": [
                        {"name": "Title", "value": "Demo", "type": "SHORT_TEXT"},
                    ]
                }
            ),
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_completion)

    response = client.post(
        "/chat",
        data={
            "session_id": "s-mp",
            "intent": "ocr",
            "pageno": "1",
            "parameters": json.dumps(["Title,SHORT_TEXT"]),
            "tableparameters": "[]",
        },
        files={"file": ("note.txt", b"Title: Demo invoice text", "text/plain")},
    )

    assert response.status_code == 200, response.text
    reply = json.loads(response.json()["reply"])
    assert reply["ocrResult"][0]["value"] == "Demo"


def test_filepath_alone_without_ocr_intent_does_not_force_ocr(client, monkeypatch):
    async def fake_completion(self, messages):
        return {
            "content": "hello from chat",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_completion)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-noforce",
            "message": "Hello there",
            "payload": {"filepath": "container/file.pdf"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "hello from chat"
    assert body["ocr_result"] is None


def test_resolve_pageno_helpers():
    from app.agents.ocr_helpers import InvalidOcrPageError, parse_blob_filepath, resolve_pageno

    assert resolve_pageno(None).start == 1
    assert resolve_pageno("-1", max_pages=5).end == 5
    with pytest.raises(InvalidOcrPageError):
        resolve_pageno("0")

    ref = parse_blob_filepath(
        r"ezts2e3b7b3738a34f94878ea006dad93230\INV26-27002140.pdf",
        allowed_host_suffixes=[".blob.core.windows.net"],
    )
    assert ref.container == "ezts2e3b7b3738a34f94878ea006dad93230"
    assert ref.blob_name == "INV26-27002140.pdf"
