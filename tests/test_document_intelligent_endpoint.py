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
    "repository_id",
    "repository_name",
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
    assert "rationale" not in payload
    assert "confidence_score" not in payload
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
    assert result["candidates"][0]["rationale"] == "This document has Vessel details that match Shipping Agency Files."
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
    body = response.json()
    result = body["document_intelligent_result"]
    assert result["repository_id"] is None
    assert result["repository_name"] is None
    assert "rationale" not in result
    assert body["reply"] == "This document doesn't match any of your folders."


def test_rationale_lines_name_the_shared_details():
    from app.document_intelligent_skills.lock import locked_payload

    catalog = [
        {"repository_id": _REPO, "repository_name": "Shipping Agency",
         "fields": ["Shipper", "receiverName", "FREIGHT_CHARGE", "bill_of_lading", "Vessel"]},
        {"repository_id": "22222222-2222-2222-2222-222222222222", "repository_name": "Freight Billing",
         "fields": ["Invoice Number", "Freight Charge", "Due Date"]},
        {"repository_id": "33333333-3333-3333-3333-333333333333", "repository_name": "Contracts", "fields": []},
    ]
    text = "BILL OF LADING\nShipper: ACME\nReceiver Name: Globex\nFreight charge: 1,200\nVessel: Ocean Star"
    result = locked_payload(
        ocr_text=text,
        catalog=catalog,
        confidence_score=86,
        repository_id=_REPO,
        candidates=[
            {"repository_id": _REPO, "score": 86},
            {"repository_id": "22222222-2222-2222-2222-222222222222", "score": 48},
            {"repository_id": "33333333-3333-3333-3333-333333333333", "score": 20},
        ],
    )

    assert "rationale" not in result
    assert [c["rationale"] for c in result["candidates"]] == [
        "This document has Bill of Lading, Shipper and Receiver Name details that match Shipping Agency.",
        "This document has Freight Charge details that match Freight Billing.",
        "This document shares no specific details with Contracts; it was suggested from its overall content.",
    ]


def test_no_match_still_suggests_closest_folders():
    from app.document_intelligent_skills.lock import locked_payload

    catalog = [
        {"repository_id": _REPO, "repository_name": "Shipping Agency", "fields": ["Vessel", "Shipper"]},
        {"repository_id": "22222222-2222-2222-2222-222222222222", "repository_name": "Invoices",
         "fields": ["Invoice Number", "Total", "Due Date"]},
        {"repository_id": "33333333-3333-3333-3333-333333333333", "repository_name": "Contracts", "fields": ["Party"]},
    ]
    text = "Invoice Number: 42\nDue Date: 2026-10-01\nTotal: 900\nShipped by vessel"
    result = locked_payload(ocr_text=text, catalog=catalog, confidence_score=30, candidates=[])

    assert result["repository_id"] is None
    assert "confidence_score" not in result
    assert [c["repository_name"] for c in result["candidates"]] == ["Invoices", "Shipping Agency"]
    assert all(c["score"] < 55 for c in result["candidates"])
    assert result["candidates"][0]["rationale"] == (
        "This document has Invoice Number, Due Date and Total details that match Invoices."
    )


def test_store_loads_fields_from_repository_fields_table():
    import asyncio

    from app.document_intelligent.store import DocumentIntelligentStore

    class FakePool:
        async def fetch(self, sql, *args):
            if 'repository."Repositories"' in sql:
                return [{"id": _REPO, "name": "Shipping Agency Files"}]
            if 'repository."RepositoryFields"' in sql:
                return [
                    {"repository_id": _REPO, "name": "IMO Number", "column_name": "IMONumber"},
                    {"repository_id": _REPO, "name": "Vessel", "column_name": "Vessel"},
                ]
            raise RuntimeError("relation does not exist")

    class FakePools:
        async def acquire(self, tenant_id):
            return FakePool()

    catalog = asyncio.run(DocumentIntelligentStore(tenant_pools=FakePools()).list_catalog("t-1"))
    assert catalog[0]["fields"] == ["IMO Number", "Vessel"]


def test_empty_text_has_no_candidates():
    from app.document_intelligent_skills.lock import locked_payload

    result = locked_payload(ocr_text="", catalog=_CATALOG)
    assert result["candidates"] == []
    assert "rationale" not in result


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
