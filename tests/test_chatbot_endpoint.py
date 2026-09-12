"""Chatbot Phase 1: intent gate + Global Search reuse + CHATBOT.md blocks."""
import asyncio

from app.chatbot.intent_gate import classify_chatbot_intent
from app.core.intent_router import Intent, IntentRouter
from app.global_search.types import SearchHit


def test_chatbot_requires_tenant(client):
    response = client.post(
        "/chat",
        json={"session_id": "s-cb-notenant", "intent": "chatbot", "message": "ABC"},
    )
    assert response.status_code == 400
    assert "tenantId" in response.json()["detail"]


def test_chatbot_greeting_gate_no_search(client):
    dispatcher = client.app.state.dispatcher
    called = []

    async def boom(**kwargs):
        called.append(1)
        return []

    for name in (
        "search_repositories",
        "search_workflows",
        "search_repo_metadata",
        "search_repo_rag",
        "search_forms",
        "search_comments",
        "search_tickets",
    ):
        dispatcher._implementations[name] = boom

    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-hi",
            "intent": "chatbot",
            "message": "hello",
            "payload": {"tenantId": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "Hello" in body["reply"]
    assert body["chatbot_result"]["hits"] == []
    assert body["chatbot_result"]["action"] is None
    assert called == []


def test_chatbot_help_gate(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-help",
            "intent": "chatbot",
            "payload": {"tenantId": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["chatbot_result"]["query"] == "help"
    assert body["chatbot_result"]["hits"] == []
    blocks = body["chatbot_result"]["text"]["blocks"]
    assert blocks[0]["type"] == "paragraph"


def test_chatbot_out_of_scope_gate(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-oos",
            "intent": "chatbot",
            "message": "what's the weather today",
            "payload": {"tenantId": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231"},
        },
    )
    assert response.status_code == 200, response.text
    assert "outside" in response.json()["reply"].lower()
    assert response.json()["chatbot_result"]["hits"] == []


def test_chatbot_search_blocks_and_hits(client):
    dispatcher = client.app.state.dispatcher

    async def fake_repos(**kwargs):
        return [
            SearchHit(
                type="repository",
                entity_type="repository",
                entity_id="11111111-1111-1111-1111-111111111111",
                entity_name="ABC Repository",
                name="ABC Repository",
                matched_field="Name",
                matched_value="ABC Repository",
                id={
                    "repositoryId": "11111111-1111-1111-1111-111111111111",
                    "repositoryName": "ABC Repository",
                },
            ).model_dump()
        ]

    async def fake_workflows(**kwargs):
        return [
            SearchHit(
                type="workflow",
                entity_type="workflow",
                entity_id="22222222-2222-2222-2222-222222222222",
                entity_name="ABC Approval Workflow",
                name="ABC Approval Workflow",
                matched_field="Name",
                matched_value="ABC Approval Workflow",
                id={
                    "workflowId": "22222222-2222-2222-2222-222222222222",
                    "workflowName": "ABC Approval Workflow",
                    "instanceId": "",
                    "requestNo": "",
                },
            ).model_dump()
        ]

    async def fake_meta(**kwargs):
        return [
            {
                "type": "document",
                "entity_type": "document",
                "entity_id": "65BA76B0-11B8-4CA2-BE18-40BA6FDC871C",
                "entity_name": "HR_01.pdf",
                "matched_field": "description",
                "matched_value": "Appointment Letter",
                "description": "Appointment Letter",
                "matchSource": "field",
                "ifileName": "HR_01.pdf",
                "name": "Human Resources",
                "id": {
                    "itemId": "65BA76B0-11B8-4CA2-BE18-40BA6FDC871C",
                    "repositoryId": "FE663435-B5E1-4EA5-A710-071C9E5DA5F2",
                    "repositoryName": "Human Resources",
                    "workflowId": 0,
                    "workflowName": "",
                    "instanceId": "",
                    "requestNo": "REQ-9",
                },
            }
        ]

    async def fake_rag(**kwargs):
        return []

    async def fake_forms(**kwargs):
        return []

    dispatcher._implementations["search_repositories"] = fake_repos
    dispatcher._implementations["search_workflows"] = fake_workflows
    dispatcher._implementations["search_repo_metadata"] = fake_meta
    dispatcher._implementations["search_repo_rag"] = fake_rag
    dispatcher._implementations["search_forms"] = fake_forms

    async def empty_extra(**kwargs):
        return []

    dispatcher._implementations["search_comments"] = empty_extra
    dispatcher._implementations["search_tickets"] = empty_extra

    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-search",
            "intent": "chatbot",
            "query": "ABC",
            "tenantId": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Found 3 matches."
    result = body["chatbot_result"]
    assert len(result["hits"]) == 3
    types = {h["type"] for h in result["hits"]}
    assert types == {"document", "repository", "workflow"}
    blocks = result["text"]["blocks"]
    assert blocks[0]["type"] == "paragraph"
    assert any(b["type"] == "bullets" for b in blocks)
    cards = next(b for b in blocks if b["type"] == "cards")
    assert len(cards["items"]) == 3
    assert result["action"]["browse_request"]["repositoryId"]
    assert result["actionTo"] == "Repository"
    assert result["actionContext"]["repositoryId"]
    doc = next(h for h in result["hits"] if h["type"] == "document")
    assert doc["id"]["requestNo"] == "REQ-9"
    assert doc["ifileName"] == "HR_01.pdf"


def test_chatbot_query_alias_and_specific_id(client):
    dispatcher = client.app.state.dispatcher
    seen = {}

    async def empty(**kwargs):
        seen.update(kwargs)
        return []

    for name in (
        "search_repositories",
        "search_workflows",
        "search_repo_metadata",
        "search_repo_rag",
        "search_forms",
        "search_comments",
        "search_tickets",
    ):
        dispatcher._implementations[name] = empty

    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-alias",
            "intent": "chatbot",
            "payload": {
                "query": "EMP10245",
                "tenantId": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231",
                "specificId": "FE663435-B5E1-4EA5-A710-071C9E5DA5F2",
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["chatbot_result"]["query"] == "EMP10245"
    assert body["chatbot_result"]["specificId"] == "FE663435-B5E1-4EA5-A710-071C9E5DA5F2"
    assert body["chatbot_result"]["hits"] == []
    assert "No matches" in body["reply"]
    assert seen.get("specific_id") == "FE663435-B5E1-4EA5-A710-071C9E5DA5F2"


def test_chatbot_search_includes_comments_and_tickets(client):
    dispatcher = client.app.state.dispatcher

    async def empty(**kwargs):
        return []

    async def fake_comments(**kwargs):
        return [
            SearchHit(
                type="comment",
                entity_type="comment",
                entity_id="dddddddd-dddd-dddd-dddd-dddddddddddd",
                entity_name="Please approve INV-2026-6001",
                name="Please approve INV-2026-6001",
                matched_field="comments",
                matched_value="Please approve INV-2026-6001",
                description="Please approve INV-2026-6001",
                id={
                    "commentId": "dddddddd-dddd-dddd-dddd-dddddddddddd",
                    "workflowId": "aabbccdd-1111-2222-3333-444444444444",
                    "workflowName": "AP Invoice Approval",
                    "instanceId": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                    "requestNo": "REQ-9001",
                    "stage": "",
                },
            ).model_dump()
        ]

    async def fake_tickets(**kwargs):
        return [
            SearchHit(
                type="ticket",
                entity_type="ticket",
                entity_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
                entity_name="REQ-9001",
                name="REQ-9001",
                matched_field="reference_number",
                matched_value="REQ-9001",
                requestNo="REQ-9001",
                id={
                    "workflowId": "aabbccdd-1111-2222-3333-444444444444",
                    "workflowName": "AP Invoice Approval",
                    "instanceId": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                    "requestNo": "REQ-9001",
                    "stage": "Finance Review",
                    "sourceTable": "workflow_instances_aabbccdd",
                },
            ).model_dump()
        ]

    for name in (
        "search_repositories",
        "search_workflows",
        "search_repo_metadata",
        "search_repo_rag",
        "search_forms",
    ):
        dispatcher._implementations[name] = empty
    dispatcher._implementations["search_comments"] = fake_comments
    dispatcher._implementations["search_tickets"] = fake_tickets

    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-p2",
            "intent": "chatbot",
            "message": "REQ-9001",
            "payload": {"tenantId": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231"},
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["chatbot_result"]
    types = {h["type"] for h in result["hits"]}
    assert "comment" in types
    assert "ticket" in types
    cards = next(b for b in result["text"]["blocks"] if b["type"] == "cards")
    card_types = {c.get("type") for c in cards["items"]}
    assert "comment" in card_types
    assert "ticket" in card_types
    ticket = next(h for h in result["hits"] if h["type"] == "ticket")
    assert ticket["id"]["stage"] == "Finance Review"
    assert ticket["id"]["requestNo"] == "REQ-9001"


def test_intent_gate_classifier_unit():
    assert classify_chatbot_intent("hello").kind == "greeting"
    assert classify_chatbot_intent("help").kind == "help"
    assert classify_chatbot_intent("tell me a joke").kind == "out_of_scope"
    assert classify_chatbot_intent("INV-2026-6001").kind == "database_question"


def test_intent_router_chatbot_triggers():
    router = IntentRouter()
    assert asyncio.run(router.classify("open chatbot please")) == Intent.CHATBOT
    assert asyncio.run(router.classify("search for invoices")) != Intent.CHATBOT
