"""Chatbot catalog-list + NL query rewrite."""
from app.chatbot.query_rewrite import detect_catalog_list, rewrite_search_query
from app.global_search.types import SearchHit

TENANT = "3EE0E334-CCB9-4DFF-968A-9BAAE71A5231"


def test_detect_what_are_repo_available():
    assert detect_catalog_list("What are repo available ?") == "repositories"
    assert detect_catalog_list("list all repositories") == "repositories"
    assert detect_catalog_list("show me workflows") == "workflows"
    assert detect_catalog_list("what forms are available") == "forms"


def test_detect_does_not_list_when_topic_keywords_present():
    assert detect_catalog_list("find forms for invoice ACME") is None
    assert detect_catalog_list("repository Accounts Payable") is None
    assert detect_catalog_list("INV-2026-6001") is None


def test_rewrite_strips_filler_keeps_keywords():
    assert rewrite_search_query("What are repo available ?") == "repo"
    assert rewrite_search_query("find invoices for ACME") == "invoices ACME"


def test_chatbot_lists_repositories(client):
    dispatcher = client.app.state.dispatcher
    called = []

    async def fake_repos(**kwargs):
        called.append(kwargs)
        return [
            SearchHit(
                type="repository",
                entity_type="repository",
                entity_id="11111111-1111-1111-1111-111111111111",
                entity_name="Accounts Payable",
                name="Accounts Payable",
                matched_field="Name",
                matched_value="Accounts Payable",
                id={
                    "repositoryId": "11111111-1111-1111-1111-111111111111",
                    "repositoryName": "Accounts Payable",
                },
            ).model_dump()
        ]

    async def boom(**kwargs):
        raise AssertionError(f"unexpected tool call: {kwargs}")

    for name in (
        "search_repo_metadata",
        "search_repo_rag",
        "search_workflows",
        "search_forms",
        "search_comments",
        "search_tickets",
    ):
        dispatcher._implementations[name] = boom
    dispatcher._implementations["search_repositories"] = fake_repos

    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-list-repos",
            "intent": "chatbot",
            "message": "What are repo available ?",
            "payload": {"tenantId": TENANT},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "repositories" in body["reply"].lower()
    hits = body["chatbot_result"]["hits"]
    assert len(hits) == 1
    assert hits[0]["name"] == "Accounts Payable"
    assert called and called[0].get("query") == ""
    blocks = body["chatbot_result"]["text"]["blocks"]
    assert any(b.get("type") == "repo_picker" for b in blocks)
    assert any(b.get("type") == "cards" for b in blocks)
