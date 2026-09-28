"""An empty structuring reply from the primary model must fall back, not return empty fields."""
import json

import fitz
import pytest

from app.llm.adapter import LLMAdapterError
from app.ocr_skills.extract_fields import run as extract_fields_skill


class _EmptyLLM:
    async def chat_completion(self, messages, **_kwargs):
        return {"content": "", "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}}


@pytest.mark.asyncio
async def test_extract_fields_raises_on_empty_reply():
    with pytest.raises(LLMAdapterError):
        await extract_fields_skill(
            llm=_EmptyLLM(),
            instruction="",
            ocr_text="Invoice No: INV-9",
            parameters=["Invoice No,SHORT_TEXT"],
            tableparameters=[],
            page_label="1",
        )


def test_ocr_empty_primary_reply_uses_fallback_model(client, monkeypatch):
    calls = []

    async def fake_completion(self, messages, **kwargs):
        calls.append(kwargs.get("model"))
        if len(calls) == 1:
            return {"content": "", "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}}
        return {
            "content": json.dumps({"ocrResult": [{"name": "Invoice No", "value": "INV-9", "type": "SHORT_TEXT"}]}),
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_completion)

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Invoice No: INV-9")
    response = client.post(
        "/chat",
        data={
            "session_id": "s-empty-reply",
            "intent": "ocr",
            "pageno": "1",
            "parameters": json.dumps(["Invoice No,SHORT_TEXT"]),
            "tableparameters": "[]",
        },
        files={"file": ("inv.pdf", doc.tobytes(), "application/pdf")},
    )

    assert response.status_code == 200, response.text
    assert len(calls) == 2
    assert calls[1] != calls[0]
    assert response.json()["ocr_result"]["ocrResult"][0]["value"] == "INV-9"
