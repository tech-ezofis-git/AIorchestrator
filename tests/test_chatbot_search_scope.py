"""Chatbot keyword scope: repository, workflow, both, or other."""
from app.chatbot.search_scope import (
    classify_search_scope,
    search_text_for_scope,
    tools_for_scope,
)
from app.global_search.types import SearchHit

TENANT = "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231"


def test_classify_document_keywords_are_repository_only():
    assert classify_search_scope("find documents for ACME") == "repository"
    assert classify_search_scope("show me the file HR_01.pdf") == "repository"
    assert "search_workflows" not in tools_for_scope("repository")
    assert "search_forms" not in tools_for_scope("repository")
    assert "search_repositories" not in tools_for_scope("repository")
    assert "search_repo_metadata" in tools_for_scope("repository")


def test_classify_request_and_ticket_are_workflow():
    assert classify_search_scope("open the request for finance") == "workflow"
    assert classify_search_scope("find ticket REQ-12") == "workflow"
    tools = tools_for_scope("workflow")
    assert "search_workflows" in tools
    assert "search_tickets" in tools
    assert "search_repo_metadata" not in tools
    assert "search_forms" not in tools


def test_classify_invoice_po_and_document_number_search_both():
    assert classify_search_scope("invoice INV-2026-6001") == "both"
    assert classify_search_scope("PO-60001") == "both"
    assert classify_search_scope("document number 4451") == "both"
    assert classify_search_scope("find the file on that ticket") == "both"
    tools = tools_for_scope("both")
    assert "search_repo_metadata" in tools
    assert "search_workflows" in tools
    assert "search_forms" not in tools
    assert "search_repositories" not in tools


def test_search_my_documents_has_no_lookup_value():
    assert classify_search_scope("Search my documents") == "repository"
    assert search_text_for_scope("Search my documents", "repository") == ""


def test_document_phrase_searches_the_value_not_the_word_documents():
    assert classify_search_scope("documents from 6001") == "repository"
    assert search_text_for_scope("documents from 6001", "repository") == "6001"
    assert (
        search_text_for_scope("find the documents which contains 6001", "repository")
        == "6001"
    )
    assert search_text_for_scope("find documents for ACME", "repository") == "ACME"
    assert search_text_for_scope("find ticket REQ-12", "workflow") == "REQ-12"
    assert search_text_for_scope("invoice INV-2026-6001", "both") == "INV-2026-6001"


def test_classify_other_skips_forms_names_and_workflow_definitions():
    assert classify_search_scope("ACME supplier") == "other"
    assert classify_search_scope("EMP10245") == "other"
    tools = tools_for_scope("other")
    assert "search_repo_metadata" in tools
    assert "search_comments" in tools
    assert "search_tickets" in tools
    assert "search_forms" not in tools
    assert "search_repositories" not in tools
    assert "search_workflows" not in tools


def test_chatbot_document_query_does_not_call_workflow_or_forms(client):
    dispatcher = client.app.state.dispatcher
    called = []
    seen_query = {}

    def wrap(name):
        async def handler(**kwargs):
            called.append(name)
            seen_query["query"] = kwargs.get("query")
            return []

        return handler

    for name in (
        "search_repositories",
        "search_workflows",
        "search_repo_metadata",
        "search_repo_rag",
        "search_forms",
        "search_comments",
        "search_tickets",
    ):
        dispatcher._implementations[name] = wrap(name)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-scope-docs",
            "intent": "chatbot",
            "message": "documents from 6001",
            "payload": {"tenantId": TENANT},
        },
    )
    assert response.status_code == 200, response.text
    assert set(called) == {"search_repo_metadata", "search_repo_rag"}
    assert seen_query["query"] == "6001"
    assert response.json()["chatbot_result"]["query"] == "6001"


def test_search_my_documents_lists_repositories(client):
    dispatcher = client.app.state.dispatcher
    called = []

    async def fake_repos(**kwargs):
        called.append(("search_repositories", kwargs.get("query")))
        return [
            SearchHit(
                type="repository",
                entity_type="repository",
                entity_id="11111111-1111-1111-1111-111111111111",
                entity_name="Accounts Payable",
                name="Accounts Payable",
                id={
                    "repositoryId": "11111111-1111-1111-1111-111111111111",
                    "repositoryName": "Accounts Payable",
                },
            ).model_dump()
        ]

    def other(name):
        async def handler(**kwargs):
            called.append((name, kwargs.get("query")))
            return []

        return handler

    dispatcher._implementations["search_repositories"] = fake_repos
    for name in (
        "search_workflows",
        "search_repo_metadata",
        "search_repo_rag",
        "search_forms",
        "search_comments",
        "search_tickets",
    ):
        dispatcher._implementations[name] = other(name)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-search-my-docs",
            "intent": "chatbot",
            "message": "Search my documents",
            "payload": {"tenantId": TENANT},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "which repository" in body["reply"].lower()
    assert called == [("search_repositories", "")]
    blocks = body["chatbot_result"]["text"]["blocks"]
    picker = next(b for b in blocks if b["type"] == "repo_picker")
    assert picker["items"][0]["name"] == "Accounts Payable"


def test_search_my_documents_with_repo_asks_for_a_term(client):
    dispatcher = client.app.state.dispatcher
    called = []

    async def boom(**kwargs):
        called.append(kwargs)
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
            "session_id": "s-cb-search-my-docs-repo",
            "intent": "chatbot",
            "message": "Search my documents",
            "payload": {
                "tenantId": TENANT,
                "specificId": "FE663435-B5E1-4EA5-A710-071C9E5DA5F2",
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert called == []
    assert body["chatbot_result"]["hits"] == []
    assert "suggestion" in body["reply"].lower()
    assert "6001" in body["reply"]
    assert body["chatbot_result"]["specificId"] == "FE663435-B5E1-4EA5-A710-071C9E5DA5F2"
