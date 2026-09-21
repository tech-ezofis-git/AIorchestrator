"""Chatbot Phase 3: EzofisClient Core wrappers + confirm-before-execute."""
import asyncio
import base64

import pytest

from app.core.dispatcher import ToolRequiresConfirmationError
from app.integrations.ezofis_client import EzofisClient
from app.tools.chatbot_action_tools import CHATBOT_ACTION_TOOL_NAMES


TENANT = "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231"


def _mock_client(monkeypatch) -> EzofisClient:
    client = EzofisClient()
    monkeypatch.setattr(client, "_live_enabled", lambda: False)
    return client


def test_ezofis_client_start_workflow_mock(monkeypatch):
    client = _mock_client(monkeypatch)
    result = asyncio.run(
        client.start_workflow(tenant_id=TENANT, workflow_id="wf-1", context="chatbot")
    )
    assert result["ok"] is True
    assert result["mock"] is True
    assert result["instanceId"]
    assert result["workflowId"] == "wf-1"


def test_ezofis_client_upload_and_create_user_mock(monkeypatch):
    client = _mock_client(monkeypatch)
    blob = base64.b64encode(b"hello invoice").decode("ascii")

    async def run():
        up = await client.upload_repository_file(
            tenant_id=TENANT,
            repository_id="repo-1",
            file_name="inv.pdf",
            content_base64=blob,
        )
        user = await client.create_user(
            tenant_id=TENANT,
            email="new.user@example.com",
            display_name="New User",
            password="secret",
        )
        return up, user

    up, user = asyncio.run(run())
    assert up["ok"] and up["mock"] and up["itemId"]
    assert user["ok"] and user["mock"] and user["userId"]
    assert user["email"] == "new.user@example.com"


def test_chatbot_action_tools_require_confirmation(client):
    dispatcher = client.app.state.dispatcher

    async def refuse_all():
        for name in CHATBOT_ACTION_TOOL_NAMES:
            with pytest.raises(ToolRequiresConfirmationError):
                await dispatcher.dispatch(
                    name,
                    {
                        "tenant_id": TENANT,
                        "workflow_id": "x",
                        "repository_id": "r",
                        "file_name": "f.pdf",
                        "content_base64": "YQ==",
                        "email": "a@b.com",
                        "display_name": "A",
                    },
                )

    asyncio.run(refuse_all())


def test_chatbot_propose_start_workflow_creates_pending(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-propose-start",
            "intent": "chatbot",
            "message": "propose start",
            "payload": {
                "tenantId": TENANT,
                "propose_action": {
                    "tool": "chatbot_start_workflow",
                    "arguments": {
                        "workflow_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        "context": "from chatbot phase 3",
                    },
                },
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "action_id=" in body["reply"]
    result = body["chatbot_result"]
    pending = result["pending_action"]
    assert pending["tool_name"] == "chatbot_start_workflow"
    assert pending["status"] == "pending_confirmation"
    assert pending["arguments"]["workflow_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert result["action"]["confirm_request"]["actionId"] == pending["action_id"]
    assert any(b.get("type") == "bullets" for b in result["text"]["blocks"])


def test_chatbot_propose_create_user_redacts_password(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-propose-user",
            "intent": "chatbot",
            "payload": {
                "tenantId": TENANT,
                "propose_action": {
                    "tool": "chatbot_create_user",
                    "arguments": {
                        "email": "a@b.com",
                        "display_name": "A B",
                        "password": "super-secret",
                    },
                },
            },
        },
    )
    assert response.status_code == 200, response.text
    pending = response.json()["chatbot_result"]["pending_action"]
    assert pending["arguments"]["password"] == "***"
    assert "super-secret" not in response.text


def test_chatbot_propose_invalid_tool(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-bad-tool",
            "intent": "chatbot",
            "payload": {
                "tenantId": TENANT,
                "propose_action": {"tool": "send_email", "arguments": {}},
            },
        },
    )
    assert response.status_code == 200, response.text
    assert "Unknown chatbot action tool" in response.json()["reply"]
    assert response.json()["chatbot_result"].get("pending_action") is None


def test_chatbot_confirm_start_workflow_dry_run(client, monkeypatch):
    async def fake_start(**kwargs):
        return {
            "ok": True,
            "mock": True,
            "instanceId": "00000000-0000-0000-0000-000000000001",
            "workflowId": kwargs.get("workflow_id"),
        }

    monkeypatch.setattr(client.app.state.ezofis_client, "start_workflow", fake_start)

    propose = client.post(
        "/chat",
        json={
            "session_id": "s-cb-confirm-start",
            "intent": "chatbot",
            "payload": {
                "tenantId": TENANT,
                "propose_action": {
                    "tool": "chatbot_start_workflow",
                    "arguments": {"workflow_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"},
                },
            },
        },
    )
    assert propose.status_code == 200, propose.text
    action_id = propose.json()["chatbot_result"]["pending_action"]["action_id"]

    confirm = client.post(f"/actions/{action_id}/confirm?session_id=s-cb-confirm-start")
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body["status"] == "executed"
    assert body["tool_name"] == "chatbot_start_workflow"
    assert body["result"]["ok"] is True
    assert body["result"]["mock"] is True
    assert body["result"]["instanceId"]


def test_chatbot_confirm_upload_dry_run(client, monkeypatch):
    async def fake_upload(**kwargs):
        return {
            "ok": True,
            "mock": True,
            "itemId": "00000000-0000-0000-0000-000000000002",
            "fileName": kwargs.get("file_name"),
            "repositoryId": kwargs.get("repository_id"),
        }

    monkeypatch.setattr(client.app.state.ezofis_client, "upload_repository_file", fake_upload)

    blob = base64.b64encode(b"%PDF-1.4 mock").decode("ascii")
    propose = client.post(
        "/chat",
        json={
            "session_id": "s-cb-confirm-upload",
            "intent": "chatbot",
            "payload": {
                "tenantId": TENANT,
                "propose_action": {
                    "tool": "chatbot_upload_repository_file",
                    "arguments": {
                        "repository_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                        "file_name": "mock.pdf",
                        "content_base64": blob,
                    },
                },
            },
        },
    )
    assert propose.status_code == 200, propose.text
    pending = propose.json()["chatbot_result"]["pending_action"]
    assert "base64" in pending["arguments"]["content_base64"]
    action_id = pending["action_id"]

    confirm = client.post(f"/actions/{action_id}/confirm?session_id=s-cb-confirm-upload")
    assert confirm.status_code == 200, confirm.text
    result = confirm.json()["result"]
    assert result["ok"] and result["mock"]
    assert result["fileName"] == "mock.pdf"
