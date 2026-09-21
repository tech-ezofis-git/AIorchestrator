"""POST /prompts suggests a dashboard message from the items table."""
import asyncio

from app.llm import LLMError
from app.prompts import fallback_prompt, generate_prompt
from tests.test_api import REPO, TENANT, FakeStore, _client


def test_prompts_from_repository(monkeypatch):
    async def fake_chat_json(**_kwargs):
        return {"prompt": "Build an AP dashboard with overdue and match status."}

    monkeypatch.setattr("app.prompts.chat_json", fake_chat_json)
    client = _client()
    response = client.post(
        "/prompts",
        json={"session_id": "p1", "tenant_id": TENANT, "repository_id": REPO},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["prompt"] == "Build an AP dashboard with overdue and match status."
    assert body["repository_id"] == REPO
    assert body["repository_name"] == "AP Invoices"
    assert body["table"] == "repository.Items_38b1b6dd"


def test_prompts_from_workflow_id(monkeypatch):
    async def fake_chat_json(**_kwargs):
        return {"prompt": "Build a workflow dashboard."}

    monkeypatch.setattr("app.prompts.chat_json", fake_chat_json)
    client = _client()
    response = client.post(
        "/prompts",
        json={
            "session_id": "p2",
            "tenant_id": TENANT,
            "workflow_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["repository_id"] == REPO
    assert body["workflow_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert "dashboard" in body["prompt"].lower()


def test_prompts_requires_tenant():
    client = _client()
    response = client.post("/prompts", json={"session_id": "p3", "repository_id": REPO})
    assert response.status_code == 400
    assert "tenant_id" in response.json()["detail"]


def test_prompts_falls_back_when_model_fails(monkeypatch):
    async def boom(**_kwargs):
        raise LLMError("no model")

    monkeypatch.setattr("app.prompts.chat_json", boom)
    client = _client()
    response = client.post(
        "/prompts",
        json={"session_id": "p4", "tenant_id": TENANT, "repository_id": REPO},
    )
    assert response.status_code == 200, response.text
    prompt = response.json()["prompt"].lower()
    assert "ap invoices" in prompt or "payable" in prompt or "dashboard" in prompt
    assert "overdue" in prompt or "outstanding" in prompt


def test_fallback_prompt_uses_ap_columns():
    text = fallback_prompt(
        target={"repository_name": "Accounts Payable"},
        columns=["Id", "Supplier", "InvoiceAmount", "DueDate", "MatchedStatus"],
        occupancy={"InvoiceAmount": 80.0, "MatchedStatus": 100.0, "DueDate": 90.0, "Status": 0.0},
    )
    blob = text.lower()
    assert "accounts payable" in blob
    assert "outstanding" in blob or "overdue" in blob
    assert "filesize" not in blob


def test_generate_prompt_async(monkeypatch):
    async def fake_chat_json(**_kwargs):
        return {"prompt": "Show overdue invoices by supplier."}

    monkeypatch.setattr("app.prompts.chat_json", fake_chat_json)
    result = asyncio.run(
        generate_prompt(store=FakeStore(), tenant_id=TENANT, repository_id=REPO)
    )
    assert result["prompt"] == "Show overdue invoices by supplier."
    assert result["repository_id"] == REPO
