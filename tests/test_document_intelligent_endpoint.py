"""Document Intelligent: OCR/file + tenant repo catalog → locked result."""
import json

_REPO = "a6169a5c-1468-4fb5-90a9-220082a89f2a"
_CATALOG = [
    {
        "repository_id": _REPO,
        "repository_name": "Shipping Agency Files",
        "fields": ["BL Number", "Vessel"],
    },
    {
        "repository_id": "11111111-1111-1111-1111-111111111111",
        "repository_name": "Invoices",
        "fields": ["Invoice No", "Total"],
    },
]
_LOCKED = {
    "confidence_score",
    "repository_id",
    "repository_name",
    "rationale",
    "candidates",
    "ocr_text",
}


def _install_fake_llm(monkeypatch, content=None):
    payload = content if content is not None else json.dumps(
        {
            "confidence_score": 88.0,
            "repository_id": _REPO,
            "repository_name": "Shipping Agency Files",
            "rationale": "OCR has a bill of lading number.",
            "candidates": [
                {
                    "repository_id": _REPO,
                    "repository_name": "Shipping Agency Files",
                    "score": 88.0,
                }
            ],
        }
    )

    async def fake_chat_completion(self, messages, **_kwargs):
        return {
            "content": payload,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)


def _install_catalog(monkeypatch):
    async def fake_list_catalog(self, tenant_id: str):
        if not (tenant_id or "").strip():
            raise ValueError("payload.tenant_id is required for intent=document_intelligent.")
        return list(_CATALOG)

    monkeypatch.setattr(
        "app.document_intelligent.store.DocumentIntelligentStore.list_catalog",
        fake_list_catalog,
    )


def _assert_locked(payload: dict):
    assert _LOCKED <= set(payload)
    assert isinstance(payload["candidates"], list)


def test_document_intelligent_from_ocr_text(client, monkeypatch):
    _install_fake_llm(monkeypatch)
    _install_catalog(monkeypatch)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-di-ocr",
            "intent": "document_intelligent",
            "payload": {
                "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
                "ocr_text": "Bill of Lading BL-99 Vessel Ocean Star",
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Document repository inferred successfully."
    result = body["document_intelligent_result"]
    _assert_locked(result)
    assert result["repository_id"] == _REPO
    assert result["repository_name"] == "Shipping Agency Files"
    assert "BL-99" in result["ocr_text"]
    assert body["classification_result"] is None
    assert body["token_usage"]["total_tokens"] == 15


def test_document_intelligent_requires_tenant(client, monkeypatch):
    _install_fake_llm(monkeypatch)
    _install_catalog(monkeypatch)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-di-notenant",
            "intent": "document_intelligent",
            "payload": {"ocr_text": "Invoice Number INV-1"},
        },
    )

    assert response.status_code == 400
    assert "tenant_id" in response.json()["detail"]


def test_document_intelligent_rejects_unknown_repo(client, monkeypatch):
    _install_fake_llm(
        monkeypatch,
        json.dumps(
            {
                "confidence_score": 99.0,
                "repository_id": "99999999-9999-9999-9999-999999999999",
                "repository_name": "Invented Library",
                "rationale": "guess",
                "candidates": [],
            }
        ),
    )
    _install_catalog(monkeypatch)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-di-fake",
            "intent": "document_intelligent",
            "payload": {
                "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
                "ocr_text": "Some letter text",
            },
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["document_intelligent_result"]
    assert result["repository_id"] is None
    assert result["repository_name"] is None


def test_document_intelligent_multipart(client, monkeypatch):
    _install_fake_llm(monkeypatch)
    _install_catalog(monkeypatch)

    response = client.post(
        "/chat",
        data={
            "session_id": "s-di-mp",
            "intent": "document_intelligent",
            "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
            "pageno": "1",
        },
        files={"file": ("note.pdf", b"Bill of Lading text", "application/pdf")},
    )

    assert response.status_code == 200, response.text
    result = response.json()["document_intelligent_result"]
    _assert_locked(result)
    assert result["repository_id"] == _REPO
    assert result["source_reference"] == "note.pdf"
