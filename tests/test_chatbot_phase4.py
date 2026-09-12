"""Chatbot Phase 4: NL action extract → propose → confirm (workflow/upload/ticket)."""
import asyncio
import base64
from types import SimpleNamespace

from app.chatbot.nl_actions import (
    compact_hits,
    extract_action_with_llm,
    looks_like_action,
    looks_like_confirm,
)

TENANT = "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231"
WF_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
REPO_ID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"


def test_looks_like_action_and_confirm():
    assert looks_like_action("start that workflow please")
    assert looks_like_action("upload to Accounts Payable repo")
    assert looks_like_action("start ticket with attachments")
    assert not looks_like_action("find invoice INV-1")
    assert looks_like_confirm("yes")
    assert looks_like_confirm("confirm")
    assert not looks_like_confirm("yes please start something else")


def test_compact_hits_keeps_ids():
    hits = [
        {
            "type": "workflow",
            "name": "AP Approval",
            "id": {
                "workflowId": WF_ID,
                "workflowName": "AP Approval",
                "repositoryId": REPO_ID,
            },
        }
    ]
    out = compact_hits(hits)
    assert out[0]["id"]["workflowId"] == WF_ID
    assert out[0]["name"] == "AP Approval"


def test_extract_action_with_llm_merges_upload(monkeypatch):
    async def fake_chat(messages):
        return {
            "content": (
                '{"tool":"chatbot_upload_repository_file",'
                f'"arguments":{{"repository_id":"{REPO_ID}"}},'
                '"clarification":null}'
            ),
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    llm = SimpleNamespace(chat_completion=fake_chat)
    blob = base64.b64encode(b"pdf").decode("ascii")
    result = asyncio.run(
        extract_action_with_llm(
            llm,
            message="upload this to that repo",
            hits=[{"type": "repository", "name": "AP", "id": {"repositoryId": REPO_ID}}],
            tenant_id=TENANT,
            upload_file={
                "file_name": "inv.pdf",
                "content_base64": blob,
                "content_type": "application/pdf",
            },
        )
    )
    assert result.tool == "chatbot_upload_repository_file"
    assert result.arguments["repository_id"] == REPO_ID
    assert result.arguments["file_name"] == "inv.pdf"
    assert result.arguments["content_base64"] == blob
    assert result.arguments["tenant_id"] == TENANT


def test_extract_accepts_create_user_phase5():
    async def fake_chat(messages):
        return {
            "content": (
                '{"tool":"chatbot_create_user",'
                '"arguments":{"email":"a@b.com","display_name":"A"},'
                '"clarification":null}'
            ),
            "usage": None,
        }

    llm = SimpleNamespace(chat_completion=fake_chat)
    result = asyncio.run(
        extract_action_with_llm(
            llm,
            message="create user a@b.com",
            hits=[],
            tenant_id=TENANT,
        )
    )
    assert result.tool == "chatbot_create_user"
    assert result.arguments["email"] == "a@b.com"


def test_chatbot_nl_start_workflow_proposes(client, monkeypatch):
    async def fake_chat(messages, **kwargs):
        return {
            "content": (
                '{"tool":"chatbot_start_workflow",'
                f'"arguments":{{"workflow_id":"{WF_ID}","context":"from nl"}},'
                '"clarification":null}'
            ),
            "usage": {"total_tokens": 3},
        }

    monkeypatch.setattr(client.app.state.llm_adapter, "chat_completion", fake_chat)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-nl-start",
            "intent": "chatbot",
            "message": "start that workflow",
            "payload": {
                "tenantId": TENANT,
                "recentHits": [
                    {
                        "type": "workflow",
                        "name": "AP Approval",
                        "id": {"workflowId": WF_ID, "workflowName": "AP Approval"},
                    }
                ],
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    pending = body["chatbot_result"]["pending_action"]
    assert pending["tool_name"] == "chatbot_start_workflow"
    assert pending["arguments"]["workflow_id"] == WF_ID
    assert body["chatbot_result"]["action"]["confirm_request"]["actionId"] == pending["action_id"]


def test_chatbot_nl_upload_proposes_and_confirm(client, monkeypatch):
    blob = base64.b64encode(b"%PDF mock").decode("ascii")

    async def fake_chat(messages, **kwargs):
        return {
            "content": (
                '{"tool":"chatbot_upload_repository_file",'
                f'"arguments":{{"repository_id":"{REPO_ID}"}},'
                '"clarification":null}'
            ),
            "usage": None,
        }

    async def fake_upload(**kwargs):
        return {
            "ok": True,
            "mock": True,
            "itemId": "00000000-0000-0000-0000-0000000000aa",
            "fileName": kwargs.get("file_name"),
            "repositoryId": kwargs.get("repository_id"),
        }

    monkeypatch.setattr(client.app.state.llm_adapter, "chat_completion", fake_chat)
    monkeypatch.setattr(client.app.state.ezofis_client, "upload_repository_file", fake_upload)

    propose = client.post(
        "/chat",
        json={
            "session_id": "s-cb-nl-upload",
            "intent": "chatbot",
            "message": "upload this to that repo",
            "payload": {
                "tenantId": TENANT,
                "recentHits": [
                    {
                        "type": "repository",
                        "name": "Accounts Payable",
                        "id": {"repositoryId": REPO_ID, "repositoryName": "Accounts Payable"},
                    }
                ],
                "uploadFile": {
                    "file_name": "mock.pdf",
                    "content_base64": blob,
                    "content_type": "application/pdf",
                },
            },
        },
    )
    assert propose.status_code == 200, propose.text
    pending = propose.json()["chatbot_result"]["pending_action"]
    assert pending["tool_name"] == "chatbot_upload_repository_file"
    assert pending["arguments"]["repository_id"] == REPO_ID
    assert "base64" in pending["arguments"]["content_base64"]
    action_id = pending["action_id"]

    confirm = client.post(f"/actions/{action_id}/confirm?session_id=s-cb-nl-upload")
    assert confirm.status_code == 200, confirm.text
    result = confirm.json()["result"]
    assert result["ok"] and result["itemId"]
    assert result["fileName"] == "mock.pdf"


def test_chatbot_nl_ticket_with_attachments_confirm(client, monkeypatch):
    blob = base64.b64encode(b"att").decode("ascii")

    async def fake_chat(messages, **kwargs):
        return {
            "content": (
                '{"tool":"chatbot_start_ticket_with_attachments",'
                f'"arguments":{{"workflow_id":"{WF_ID}","repository_id":"{REPO_ID}"}},'
                '"clarification":null}'
            ),
            "usage": None,
        }

    async def fake_ticket(**kwargs):
        return {
            "ok": True,
            "mock": True,
            "instanceId": "00000000-0000-0000-0000-0000000000bb",
            "workflowId": kwargs.get("workflow_id"),
            "repositoryId": kwargs.get("repository_id"),
            "fileName": kwargs.get("file_name"),
            "attachments": [{"ok": True}],
        }

    monkeypatch.setattr(client.app.state.llm_adapter, "chat_completion", fake_chat)
    monkeypatch.setattr(
        client.app.state.ezofis_client, "start_ticket_with_attachments", fake_ticket
    )

    propose = client.post(
        "/chat",
        json={
            "session_id": "s-cb-nl-ticket",
            "intent": "chatbot",
            "message": "start ticket with attachments",
            "payload": {
                "tenantId": TENANT,
                "recentHits": [
                    {
                        "type": "workflow",
                        "name": "Support Ticket",
                        "id": {"workflowId": WF_ID, "repositoryId": REPO_ID},
                    }
                ],
                "uploadFile": {
                    "file_name": "note.txt",
                    "content_base64": blob,
                    "content_type": "text/plain",
                },
            },
        },
    )
    assert propose.status_code == 200, propose.text
    pending = propose.json()["chatbot_result"]["pending_action"]
    assert pending["tool_name"] == "chatbot_start_ticket_with_attachments"
    assert pending["arguments"]["workflow_id"] == WF_ID
    assert pending["arguments"]["file_name"] == "note.txt"
    action_id = pending["action_id"]

    confirm = client.post(f"/actions/{action_id}/confirm?session_id=s-cb-nl-ticket")
    assert confirm.status_code == 200, confirm.text
    body = confirm.json()
    assert body["status"] == "executed"
    assert body["tool_name"] == "chatbot_start_ticket_with_attachments"
    assert body["result"]["instanceId"]


def test_chatbot_yes_with_pending_action_id_returns_confirm_hint(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-yes-hint",
            "intent": "chatbot",
            "message": "yes",
            "payload": {
                "tenantId": TENANT,
                "pendingActionId": "act-pending-1",
            },
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["chatbot_result"]
    assert result["action"]["confirm_request"]["actionId"] == "act-pending-1"
    assert result["pending_action"]["action_id"] == "act-pending-1"


def test_ezofis_client_start_ticket_mock(monkeypatch):
    from app.integrations.ezofis_client import EzofisClient

    client = EzofisClient()
    monkeypatch.setattr(client, "_live_enabled", lambda: False)
    blob = base64.b64encode(b"x").decode("ascii")
    result = asyncio.run(
        client.start_ticket_with_attachments(
            tenant_id=TENANT,
            workflow_id=WF_ID,
            repository_id=REPO_ID,
            file_name="a.txt",
            content_base64=blob,
        )
    )
    assert result["ok"] is True
    assert result["mock"] is True
    assert result["instanceId"]
