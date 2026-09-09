"""Global Search: flat hits[], SEARCH_API-style identity, forms, RAG vs metadata."""
import asyncio

from app.core.intent_router import Intent, IntentRouter
from app.global_search.merge import build_result, merge_document_hits, status_reply
from app.global_search.types import SearchHit


def test_global_search_requires_tenant(client):
    response = client.post(
        "/chat",
        json={"session_id": "s-gs-notenant", "intent": "global_search", "message": "ABC"},
    )
    assert response.status_code == 400
    assert "tenantId" in response.json()["detail"]


def test_global_search_empty_query(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "s-gs-empty",
            "intent": "global_search",
            "payload": {"tenantId": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231"},
        },
    )
    assert response.status_code in (400, 422)


def test_global_search_search_api_payload_no_matches(client):
    response = client.post(
        "/chat",
        json={
            "session_id": "s-gs-empty-hits",
            "intent": "global_search",
            "query": "EMP10245",
            "tenantId": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "No matches."
    result = body["global_search_result"]
    assert result["query"] == "EMP10245"
    assert result["hits"] == []
    assert "groups" not in result


def test_global_search_flat_hits_and_field_wins_over_rag(client):
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
                description="",
                modifiedDateandtime="",
                dateandtime="",
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
                description="",
                modifiedDateandtime="",
                dateandtime="",
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
                "modifiedDateandtime": "2026-08-05",
                "dateandtime": "",
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
                    "requestNo": "",
                },
            }
        ]

    async def fake_rag(**kwargs):
        return [
            {
                "type": "document",
                "entity_type": "document",
                "entity_id": "65BA76B0-11B8-4CA2-BE18-40BA6FDC871C",
                "entity_name": "HR_01.pdf",
                "matched_field": "content",
                "matched_value": "OCR snippet about ABC",
                "description": "",
                "modifiedDateandtime": "",
                "dateandtime": "",
                "matchSource": "content",
                "ifileName": "HR_01.pdf",
                "name": "Human Resources",
                "id": {
                    "itemId": "65BA76B0-11B8-4CA2-BE18-40BA6FDC871C",
                    "repositoryId": "FE663435-B5E1-4EA5-A710-071C9E5DA5F2",
                    "repositoryName": "Human Resources",
                    "workflowId": 0,
                    "workflowName": "",
                    "instanceId": "",
                    "requestNo": "",
                },
            }
        ]

    async def fake_forms(**kwargs):
        return []

    dispatcher._implementations["search_repositories"] = fake_repos
    dispatcher._implementations["search_workflows"] = fake_workflows
    dispatcher._implementations["search_repo_metadata"] = fake_meta
    dispatcher._implementations["search_repo_rag"] = fake_rag
    dispatcher._implementations["search_forms"] = fake_forms

    response = client.post(
        "/chat",
        json={
            "session_id": "s-gs-cards",
            "intent": "global_search",
            "payload": {
                "query": "ABC",
                "tenantId": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231",
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "Found 3 matches."
    hits = body["global_search_result"]["hits"]
    assert "groups" not in body["global_search_result"]
    by_type = {}
    for hit in hits:
        by_type.setdefault(hit["type"], []).append(hit)
    assert len(by_type["repository"]) == 1
    assert len(by_type["workflow"]) == 1
    assert len(by_type["document"]) == 1
    doc = by_type["document"][0]
    assert doc["matchSource"] == "field"
    assert doc["id"]["itemId"] == "65BA76B0-11B8-4CA2-BE18-40BA6FDC871C"
    assert doc["ifileName"] == "HR_01.pdf"
    assert doc["description"] == "Appointment Letter"
    assert doc["modifiedDateandtime"] == "2026-08-05"
    assert "workspaceId" not in doc["id"]
    assert "processId" not in doc["id"]
    assert "instanceId" in doc["id"]


def test_global_search_specific_id_still_searches_all_tools(client):
    dispatcher = client.app.state.dispatcher
    called = []

    async def repos(**kwargs):
        called.append("search_repositories")
        return []

    async def wfs(**kwargs):
        called.append("search_workflows")
        return []

    async def meta(**kwargs):
        called.append("search_repo_metadata")
        assert kwargs.get("specific_id") == "FE663435-B5E1-4EA5-A710-071C9E5DA5F2"
        return []

    async def rag(**kwargs):
        called.append("search_repo_rag")
        return []

    async def forms(**kwargs):
        called.append("search_forms")
        return []

    dispatcher._implementations["search_repositories"] = repos
    dispatcher._implementations["search_workflows"] = wfs
    dispatcher._implementations["search_repo_metadata"] = meta
    dispatcher._implementations["search_repo_rag"] = rag
    dispatcher._implementations["search_forms"] = forms

    response = client.post(
        "/chat",
        json={
            "session_id": "s-gs-lock",
            "intent": "global_search",
            "payload": {
                "query": "QUALITY CERTIFICATE",
                "tenantId": "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231",
                "specificId": "FE663435-B5E1-4EA5-A710-071C9E5DA5F2",
            },
        },
    )
    assert response.status_code == 200, response.text
    assert set(called) == {
        "search_repo_metadata",
        "search_repo_rag",
        "search_repositories",
        "search_workflows",
        "search_forms",
    }


def test_rag_search_intent_still_wins_generic_search(client, monkeypatch):
    async def fake_embed(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def fake_chat_completion(self, messages):
        return {
            "content": "Based on the excerpts, hello. [1]",
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
        }

    monkeypatch.setattr("app.llm.embedding_adapter.EmbeddingAdapter.embed", fake_embed)
    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    response = client.post("/chat", json={"session_id": "s-rag", "message": "search for the PTO policy"})
    assert response.status_code == 200
    assert response.json().get("global_search_result") is None


def test_intent_router_global_search_before_rag():
    router = IntentRouter()
    assert asyncio.run(router.classify("global search ABC")) == Intent.GLOBAL_SEARCH
    assert asyncio.run(router.classify("search for the PTO policy")) == Intent.SEARCH
    assert asyncio.run(router.classify("find workflow ABC")) == Intent.GLOBAL_SEARCH


def test_merge_field_wins_over_content():
    field = [
        SearchHit(
            type="document",
            entity_type="document",
            entity_id="65BA76B0-11B8-4CA2-BE18-40BA6FDC871C",
            entity_name="HR_01.pdf",
            matchSource="field",
        )
    ]
    rag = [
        SearchHit(
            type="document",
            entity_type="document",
            entity_id="65BA76B0-11B8-4CA2-BE18-40BA6FDC871C",
            entity_name="HR_01.pdf",
            matchSource="content",
        ),
        SearchHit(
            type="document",
            entity_type="document",
            entity_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            entity_name="other.pdf",
            matchSource="content",
        ),
    ]
    merged = merge_document_hits(field, rag)
    assert merged[0].matchSource == "field"
    assert len(merged) == 2
    assert merged[1].matchSource == "content"
    result = build_result("ABC", merged)
    assert status_reply(result) == "Found 2 matches."
    assert len(result.hits) == 2
