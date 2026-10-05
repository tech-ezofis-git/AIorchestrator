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
    "keywords",
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
    assert "repository_id" not in payload and "repository_name" not in payload
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
    assert result["candidates"][0]["repository_id"] == _REPO
    assert result["candidates"][0]["repository_name"] == "Shipping Agency Files"
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
    assert result["candidates"] == []
    assert "rationale" not in result
    assert "did not return any folder from this tenant's catalog" in result["error"]
    assert body["reply"] == result["error"]


def _post_di(client, session_id: str):
    return client.post(
        "/chat",
        json={
            "session_id": session_id,
            "intent": "document_intelligent",
            "payload": {
                "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
                "ocr_text": "Bill of Lading BL-99 Vessel Ocean Star",
            },
        },
    )


def test_empty_model_reply_is_reported_without_guessing(client, monkeypatch):
    _install_fake_llm(monkeypatch, "")
    _install_catalog(monkeypatch)

    response = _post_di(client, "s-di-empty")

    assert response.status_code == 200, response.text
    body = response.json()
    result = body["document_intelligent_result"]
    assert result["candidates"] == []
    assert result["keywords"] == []
    assert "returned an empty response" in result["error"]
    assert result["model"]
    assert body["reply"] == result["error"]


def test_invalid_json_model_reply_is_reported(client, monkeypatch):
    _install_fake_llm(monkeypatch, "Sorry, I cannot help with that.")
    _install_catalog(monkeypatch)

    result = _post_di(client, "s-di-badjson").json()["document_intelligent_result"]

    assert result["candidates"] == []
    assert "not valid JSON: 'Sorry, I cannot help with that.'" in result["error"]


def test_unreachable_model_is_reported(client, monkeypatch):
    from app.llm.adapter import LLMAdapterError

    async def failing_chat_completion(self, messages, **_kwargs):
        raise LLMAdapterError("The language model provider is currently unavailable.")

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", failing_chat_completion)
    _install_catalog(monkeypatch)

    response = _post_di(client, "s-di-down")

    assert response.status_code == 200, response.text
    result = response.json()["document_intelligent_result"]
    assert result["candidates"] == []
    assert "is unreachable: The language model provider is currently unavailable." in result["error"]


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
    assert [c["keywords"] for c in result["candidates"]] == [
        ["Bill of Lading", "Shipper", "Receiver Name", "Freight Charge", "Vessel"],
        ["Freight Charge"],
    ]
    assert result["keywords"] == ["Bill of Lading", "Shipper", "Receiver Name", "Freight Charge", "Vessel"]
    assert [c["rationale"] for c in result["candidates"]] == [
        "This document has Bill of Lading, Shipper and Receiver Name details that match Shipping Agency.",
        "This document has Freight Charge details that match Freight Billing.",
    ]


def test_code_never_suggests_folders_the_model_did_not_pick():
    from app.document_intelligent_skills.lock import locked_payload

    catalog = [
        {"repository_id": _REPO, "repository_name": "Shipping Agency", "fields": ["Vessel", "Shipper"]},
        {"repository_id": "22222222-2222-2222-2222-222222222222", "repository_name": "Invoices",
         "fields": ["Invoice Number", "Total", "Due Date"]},
    ]
    text = "Invoice Number: 42\nDue Date: 2026-10-01\nTotal: 900\nShipped by vessel"
    result = locked_payload(ocr_text=text, catalog=catalog, confidence_score=30, candidates=[])

    assert result["candidates"] == []
    assert result["keywords"] == []


def test_score_agrees_with_rationale_for_non_english_text():
    from app.document_intelligent_skills.lock import locked_payload

    catalog = [
        {"repository_id": _REPO, "repository_name": "Shipping Agency Files",
         "fields": ["Vessel", "IMO Number", "ETA", "ETD"]},
        {"repository_id": "22222222-2222-2222-2222-222222222222", "repository_name": "FTL",
         "fields": ["Company Name", "Order Number"]},
    ]
    text = "نموذج استفسار عن رسو السفينة\nاسم السفينة: MV Sea Horizon\nرقم IMO 9648712\nETA 03 June\nETD 04 June"
    result = locked_payload(
        ocr_text=text,
        catalog=catalog,
        confidence_score=85,
        repository_id=_REPO,
        candidates=[
            {"repository_id": "22222222-2222-2222-2222-222222222222", "score": 70, "matched_fields": ["Invented"]},
            {"repository_id": _REPO, "score": 85, "matched_fields": ["Vessel", "imo number", "Not A Field"]},
        ],
    )

    shipping, ftl = result["candidates"]
    assert shipping["repository_name"] == "Shipping Agency Files"
    assert shipping["keywords"] == ["ETA", "ETD", "Vessel", "IMO Number"]
    assert shipping["rationale"] == "This document has ETA, ETD and Vessel details that match Shipping Agency Files."
    assert ftl["keywords"] == []
    assert ftl["rationale"] == "This document may also fit FTL, the model's next closest folder."


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
    assert result["keywords"] == []
    assert "rationale" not in result


def test_model_pick_by_ref_and_loose_name_is_kept_even_with_low_confidence():
    from app.document_intelligent_skills.lock import locked_payload

    result = locked_payload(
        ocr_text="Invoice No 42 Total 900 Vessel Ocean Star",
        catalog=_CATALOG,
        confidence_score=40,
        ref="R2",
        candidates=[
            {"ref": "R2", "score": 40, "matched_fields": ["Invoice No"]},
            {"repository_name": "shipping agency file", "score": 10},
        ],
    )

    names = [c["repository_name"] for c in result["candidates"]]
    assert names == ["Invoices", "Shipping Agency Files"]


def test_model_pick_with_no_shared_fields_is_skipped(client, monkeypatch):
    _install_fake_llm(
        monkeypatch,
        json.dumps(
            {
                "ref": "R2",
                "confidence_score": 20,
                "candidates": [{"ref": "R2", "repository_name": "Invoices", "score": 20}],
            }
        ),
    )
    _install_catalog(monkeypatch)

    body = _post_di(client, "s-di-noevidence").json()

    assert body["document_intelligent_result"]["candidates"] == []
    assert "error" not in body["document_intelligent_result"]
    assert body["reply"] == "This document doesn't match any of your folders."


def test_keyword_match_accepts_common_abbreviations():
    from app.document_intelligent_skills.lock import locked_payload

    catalog = [
        {"repository_id": _REPO, "repository_name": "AP Bills",
         "fields": ["Invoice Number", "Quantity Ordered", "Purchase Order"]},
    ]
    result = locked_payload(
        ocr_text="Inv No: 42\nQty ordered: 5", catalog=catalog, candidates=[{"repository_id": _REPO, "score": 80}]
    )

    assert result["candidates"][0]["keywords"] == ["Invoice Number", "Quantity Ordered"]


def test_lone_number_field_does_not_match_the_word_no():
    from app.document_intelligent_skills.lock import locked_payload

    catalog = [{"repository_id": _REPO, "repository_name": "Misc", "fields": ["Number"]}]
    result = locked_payload(
        ocr_text="There is no data here", catalog=catalog, candidates=[{"repository_id": _REPO, "score": 20}]
    )

    assert result["candidates"] == []


def test_ocr_failure_is_reported(client, monkeypatch):
    _install_fake_llm(monkeypatch)
    _install_catalog(monkeypatch)

    async def failing_dispatch(self, tool, args):
        raise RuntimeError("Paddle OCR timed out")

    monkeypatch.setattr("app.core.dispatcher.Dispatcher.dispatch", failing_dispatch)

    response = client.post(
        "/chat",
        data={
            "session_id": "s-di-ocrfail",
            "intent": "document_intelligent",
            "tenant_id": "2e3b7b37-38a3-4f94-878e-a006dad93230",
            "pageno": "1",
        },
        files={"file": ("scan.png", b"\x89PNG fake", "image/png")},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"].startswith("Text could not be extracted")
    assert body["document_intelligent_result"]["error"] == "OCR failed: Paddle OCR timed out"
    assert body["document_intelligent_result"]["candidates"] == []


def test_document_intelligent_multipart(client, monkeypatch):
    _install_fake_llm(
        monkeypatch,
        json.dumps(
            {
                "confidence_score": 88.0,
                "ref": "R1",
                "candidates": [{"ref": "R1", "score": 88.0, "matched_fields": ["BL Number"]}],
            }
        ),
    )
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
    assert result["candidates"][0]["repository_id"] == _REPO
    assert result["source_reference"] == "note.pdf"
