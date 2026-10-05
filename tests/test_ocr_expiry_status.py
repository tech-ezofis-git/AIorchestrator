"""OCR expiry status: "Active · ..." / "Expired · ..." on expiry-type fields only."""
import json
from datetime import date, timedelta

import pytest

from app.ocr_skills.expiry_status import apply_expiry_status, expiry_status, is_expiry_field

TODAY = date(2026, 9, 30)


@pytest.mark.parametrize(
    ("days", "status"),
    [
        (400, "Active · 1 year"),
        (300, "Active · 10 months"),
        (30, "Active · 1 month"),
        (10, "Active · 10 days"),
        (1, "Active · 1 day"),
        (0, "Active · today"),
        (-1, "Expired · 1 day"),
        (-75, "Expired · 2 months"),
    ],
)
def test_expiry_status(days, status):
    value = (TODAY + timedelta(days=days)).isoformat()
    assert expiry_status(value, today=TODAY) == {"status": status}


@pytest.mark.parametrize(
    ("value", "status"),
    [("2018-09-11", "Expired · 8 years"), ("2036-09-30", "Active · 10 years")],
)
def test_expiry_status_examples(value, status):
    assert expiry_status(value, today=TODAY) == {"status": status}


@pytest.mark.parametrize("value", [None, "", "11/09/2018", "2018-02-30", "not a date"])
def test_unreadable_expiry_has_no_status(value):
    assert expiry_status(value, today=TODAY) == {"status": None}


@pytest.mark.parametrize(
    "name",
    ["Expiry Date", "Date of Expiry", "expiry_date", "ExpirationDate", "Valid Until", "Valid Till", "Passport Expiry Date"],
)
def test_expiry_field_names_are_recognised(name):
    assert is_expiry_field(name)


@pytest.mark.parametrize("name", ["InvoiceDate", "Date of Issue", "Due Date", "Date of Birth", "Expiry Type"])
def test_other_field_names_are_not_expiry(name):
    assert not is_expiry_field(name)


def test_only_expiry_fields_get_status():
    fields = [
        {"name": "InvoiceDate", "value": "2026-09-11", "type": "DATE"},
        {"name": "expiry_date", "value": "2018-09-11", "type": "date"},
    ]
    out = apply_expiry_status(fields, today=TODAY)
    assert out[0] == fields[0]
    assert out[1] == {**fields[1], "status": "Expired · 8 years"}


def test_first_readable_expiry_field_is_renamed():
    fields = [
        {"name": "Visa Expiry Date", "value": "unreadable", "type": "date"},
        {"name": "expiryDate", "value": "2015-09-22", "type": "SHORT_TEXT"},
        {"name": "validUntil", "value": "2036-09-30", "type": "date"},
    ]
    out = apply_expiry_status(fields, today=TODAY, rename_to="DocumentStatus")
    assert [f["name"] for f in out] == ["Visa Expiry Date", "DocumentStatus", "validUntil"]
    assert out[1] == {"name": "DocumentStatus", "value": "2015-09-22", "type": "SHORT_TEXT", "status": "Expired · 11 years"}
    assert out[2]["status"] == "Active · 10 years"


def test_requested_retention_end_date_is_filled_from_hidden_expiry(client, monkeypatch):
    td3 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nL898902C36UTO7408122F1204159ZE184226B<<<<<10"
    prompts = []

    async def fake_completion(self, messages, **_kwargs):
        prompts.append(messages[-1]["content"])
        return {
            "content": json.dumps({"ocrResult": [
                {"name": "DocumentType", "value": "P", "type": "SHORT_TEXT"},
                {"name": "DocumentStatus", "value": "2021-04-15", "type": "SHORT_TEXT"},
                {"name": "RetentionEndDate", "value": None, "type": "DATE"},
                {"name": "Expiry Date", "value": "2021-04-15", "type": "DATE"},
            ]}),
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_completion)
    params = json.dumps(["DocumentType,SHORT_TEXT", "DocumentStatus,SHORT_TEXT", "RetentionEndDate,DATE"])
    response = client.post(
        "/chat",
        data={"session_id": "s-docstatus", "intent": "ocr", "pageno": "1", "parameters": params, "tableparameters": "[]"},
        files={"file": ("passport.txt", f"PASSPORT\n{td3}\n".encode(), "text/plain")},
    )

    assert response.status_code == 200, response.text
    result = response.json()["ocr_result"]
    fields = result["ocrResult"]
    assert [f["name"] for f in fields] == ["DocumentType", "DocumentStatus", "RetentionEndDate"]
    doc_type, doc_status, retention = fields
    assert result["document_type"] == "Passport"
    assert doc_type["value"] == "Passport"
    assert "status" not in doc_status
    # The check-digit-verified MRZ expiry wins over the model's reading.
    assert retention["value"] == "2012-04-15"
    assert retention["type"] == "DATE"
    assert retention["status"].startswith("Expired · ")
    assert "Expiry Date" in prompts[0]


def test_chat_ocr_adds_status_to_expiry_field(client, monkeypatch):
    td3 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nL898902C36UTO7408122F1204159ZE184226B<<<<<10"

    async def fake_completion(self, messages, **_kwargs):
        return {
            "content": json.dumps({"ocrResult": [
                {"name": "Date of Issue", "value": "2002-04-16", "type": "DATE"},
                {"name": "Date of Expiry", "value": "2021-04-15", "type": "DATE"},
            ]}),
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_completion)
    response = client.post(
        "/chat",
        data={"session_id": "s-exp", "intent": "ocr", "pageno": "1", "parameters": "[]", "tableparameters": "[]"},
        files={"file": ("passport.txt", f"PASSPORT\n{td3}\n".encode(), "text/plain")},
    )

    assert response.status_code == 200, response.text
    issue, expiry = json.loads(response.json()["reply"])["ocrResult"]
    assert "status" not in issue
    # The check-digit-verified MRZ expiry (2012-04-15) replaces the LLM's value before the status.
    assert expiry["name"] == "RetentionEndDate"
    assert expiry["value"] == "2012-04-15"
    assert expiry["status"].startswith("Expired · ")
    assert set(expiry) == {"name", "value", "type", "status"}
