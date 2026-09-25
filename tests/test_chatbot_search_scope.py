"""Chatbot keyword scope: repository, workflow, both, or other."""
from app.chatbot.search_plan import plan_from_model_json, plan_from_rules, tools_for_plan
from app.chatbot.search_plan import (
    SearchPlan,
    continue_from_history,
    lookup_override_plan,
    match_repository_id,
    pending_lookup_from_history,
    repository_choice_phrase,
    repository_phrase_from_history,
    searches_anywhere,
)
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


def test_rules_plan_documents_tickets_and_bare_document_ask():
    docs = plan_from_rules("documents from 6001")
    assert docs.target == "documents"
    assert docs.query == "6001"
    assert tools_for_plan(docs) == ("search_repo_metadata", "search_repo_rag")

    text = plan_from_rules("Search a Text of APEX")
    assert text.target == "both"
    assert text.query == "APEX"

    ticket = plan_from_rules("find ticket REQ-12")
    assert ticket.target == "tickets"
    assert ticket.query == "REQ-12"
    assert "search_workflows" not in tools_for_plan(ticket)
    assert "search_tickets" in tools_for_plan(ticket)

    bare = plan_from_rules("Search my documents")
    assert bare.target == "ask_term"
    assert bare.query == ""
    locked = plan_from_rules("Search my documents", specific_id="repo-1")
    assert locked.target == "ask_term"
    apex = plan_from_rules("need document from APEX")
    assert apex.target == "documents"
    assert apex.query == "APEX"
    request = plan_from_rules("find request REQ-12")
    assert request.target == "tickets"
    assert request.query == "REQ-12"


def test_model_plan_keeps_apex_and_drops_filler():
    plan = plan_from_model_json(
        {"target": "documents", "query": "Text APEX"},
        message="Search a Text of APEX",
        specific_id="",
    )
    assert plan is not None
    assert plan.target == "documents"
    assert plan.query == "APEX"
    assert plan.source == "model"


def test_search_my_documents_has_no_lookup_value():
    assert classify_search_scope("Search my documents") == "repository"
    assert search_text_for_scope("Search my documents", "repository") == ""


def test_repository_name_match_allows_case_and_catalog_list():
    repo_id = "11111111-1111-1111-1111-111111111111"
    hits = [
        {
            "entity_id": repo_id,
            "entity_name": "ACCOUNTS PAYABLE",
            "id": {"repositoryId": repo_id, "repositoryName": "ACCOUNTS PAYABLE"},
        },
        {
            "entity_id": "22222222-2222-2222-2222-222222222222",
            "entity_name": "Legal",
            "id": {"repositoryId": "22222222-2222-2222-2222-222222222222", "repositoryName": "Legal"},
        },
    ]
    assert match_repository_id("Accounts Payable", hits) == repo_id
    assert match_repository_id("Legal", hits) == "22222222-2222-2222-2222-222222222222"


def test_content_of_apex_is_a_document_lookup_not_a_repository_choice():
    assert repository_choice_phrase("find the content of APEX") == ""
    assert repository_choice_phrase("APEX") == ""
    assert repository_choice_phrase("at Accounts Payable") == "Accounts Payable"
    assert searches_anywhere("Search anywhere")
    plan = plan_from_rules("find the content of APEX")
    assert plan.target == "documents"
    assert plan.query == "APEX"
    history = [
        {"role": "user", "content": "find the content of APEX"},
        {"role": "assistant", "content": "Which repository should I search?"},
        {"role": "user", "content": "at Accounts Payable"},
        {"role": "assistant", "content": "This repository is selected."},
    ]
    assert pending_lookup_from_history(history).query == "APEX"
    assert repository_phrase_from_history(history) == "Accounts Payable"
    assert pending_lookup_from_history(
        history + [{"role": "user", "content": "APEX"}]
    ).query == "APEX"


def test_document_phrase_keeps_the_lookup_when_the_model_asks_for_a_repository():
    assert repository_choice_phrase("documents in 6001") == ""
    assert repository_choice_phrase("check at Accounts Payable") == "Accounts Payable"
    asked = SearchPlan(target="ask_repository", query="", source="model")
    kept = lookup_override_plan(asked, "documents in 6001")
    assert kept.target == "documents"
    assert kept.query == "6001"
    assert pending_lookup_from_history(
        [
            {"role": "user", "content": "documents in 6001"},
            {"role": "assistant", "content": "Which repository should I search?"},
        ]
    ).query == "6001"


def test_history_keeps_apex_and_switches_a_request_to_workflows():
    history = [
        {"role": "user", "content": "need document from APEX"},
        {"role": "assistant", "content": "I found 2 documents."},
    ]
    documents = continue_from_history(
        plan_from_rules("need document from APEX"),
        "need document from APEX",
        [],
    )
    assert documents.target == "documents"
    assert documents.query == "APEX"
    follow = continue_from_history(plan_from_rules("search the request"), "search the request", history)
    assert follow.target == "tickets"
    assert follow.query == "APEX"
    assert follow.source == "history"


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


def test_search_my_documents_asks_for_a_term(client):
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
    assert "which repository" not in body["reply"].lower()
    assert "what should i look for" in body["reply"].lower()
    assert called == []
    assert body["chatbot_result"]["hits"] == []
    assert not any(b.get("type") == "repo_picker" for b in body["chatbot_result"]["text"]["blocks"])


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
    assert "what should i look for" in body["reply"].lower()
    assert body["chatbot_result"]["specificId"] in (None, "")


def test_model_routes_text_search_and_writes_the_reply(client, monkeypatch):
    monkeypatch.setenv("CHATBOT_SEARCH_LLM", "true")
    dispatcher = client.app.state.dispatcher
    called = []

    async def fake_chat(messages, **kwargs):
        system = messages[0]["content"]
        if "route an EZOFIS search" in system:
            return {
                "content": '{"target":"documents","query":"APEX"}',
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }
        return {
            "content": "I found the document HR_01.pdf for APEX.",
            "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
        }

    monkeypatch.setattr(client.app.state.llm_adapter, "chat_completion", fake_chat)

    async def fake_meta(**kwargs):
        called.append(("search_repo_metadata", kwargs.get("query")))
        return [
            SearchHit(
                type="document",
                entity_type="document",
                entity_id="65BA76B0-11B8-4CA2-BE18-40BA6FDC871C",
                entity_name="HR_01.pdf",
                ifileName="HR_01.pdf",
                name="HR_01.pdf",
                id={"itemId": "65BA76B0-11B8-4CA2-BE18-40BA6FDC871C"},
            ).model_dump()
        ]

    dispatcher._implementations["search_repo_metadata"] = fake_meta
    for name in (
        "search_repo_rag",
        "search_repositories",
        "search_workflows",
        "search_forms",
        "search_comments",
        "search_tickets",
    ):
        dispatcher._implementations[name] = await_handler(name, called)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-model-apex",
            "intent": "chatbot",
            "message": "Search a Text of APEX",
            "payload": {"tenantId": TENANT},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reply"] == "I found the document HR_01.pdf for APEX."
    assert body["chatbot_result"]["query"] == "APEX"
    assert body["token_usage"]["total_tokens"] == 15
    assert ("search_repo_metadata", "APEX") in called
    assert ("search_repo_rag", "APEX") in called
    assert not any(name == "search_tickets" for name, _query in called)
    cards = next(b for b in body["chatbot_result"]["text"]["blocks"] if b["type"] == "cards")
    assert cards["items"][0]["title"] == "HR_01.pdf"


def test_documents_in_6001_searches_when_the_model_asks_for_a_repository(client, monkeypatch):
    monkeypatch.setenv("CHATBOT_SEARCH_LLM", "true")
    dispatcher = client.app.state.dispatcher
    called = []

    async def fake_chat(messages, **kwargs):
        return {"content": '{"target":"ask_repository","query":""}', "usage": {}}

    monkeypatch.setattr(client.app.state.llm_adapter, "chat_completion", fake_chat)

    async def fake_meta(**kwargs):
        called.append(("search_repo_metadata", kwargs.get("query"), kwargs.get("specific_id")))
        return []

    dispatcher._implementations["search_repo_metadata"] = fake_meta
    for name in (
        "search_repo_rag",
        "search_repositories",
        "search_workflows",
        "search_forms",
        "search_comments",
        "search_tickets",
    ):
        dispatcher._implementations[name] = await_handler(name, called)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-docs-in-6001",
            "intent": "chatbot",
            "message": "documents in 6001",
            "payload": {"tenantId": TENANT},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "which repository" not in body["reply"].lower()
    assert body["chatbot_result"]["query"] == "6001"
    assert ("search_repo_metadata", "6001", "") in called or (
        "search_repo_metadata",
        "6001",
        None,
    ) in called


def test_need_document_from_apex_searches_every_repository(client, monkeypatch):
    monkeypatch.setenv("CHATBOT_SEARCH_LLM", "true")
    dispatcher = client.app.state.dispatcher
    called = []
    repo_id = "a6169a5c-1468-4fb5-90a8-aaaaaaaaaaaa"

    async def fake_chat(messages, **kwargs):
        system = messages[0]["content"]
        if "route an EZOFIS search" in system:
            return {"content": '{"target":"ask_repository","query":""}', "usage": {}}
        return {"content": "I found documents for APEX.", "usage": {}}

    monkeypatch.setattr(client.app.state.llm_adapter, "chat_completion", fake_chat)

    async def fake_meta(**kwargs):
        called.append(("search_repo_metadata", kwargs.get("query"), kwargs.get("specific_id") or ""))
        return [
            SearchHit(
                type="document",
                entity_type="document",
                entity_id="65BA76B0-11B8-4CA2-BE18-40BA6FDC871C",
                entity_name="APEX.pdf",
                ifileName="APEX.pdf",
                name="APEX.pdf",
                id={
                    "itemId": "65BA76B0-11B8-4CA2-BE18-40BA6FDC871C",
                    "repositoryId": repo_id,
                    "repositoryName": "Domestic Supplier",
                },
            ).model_dump()
        ]

    async def fake_tickets(**kwargs):
        called.append(("search_tickets", kwargs.get("query"), kwargs.get("specific_id") or ""))
        return []

    dispatcher._implementations["search_repo_metadata"] = fake_meta
    dispatcher._implementations["search_tickets"] = fake_tickets
    for name in (
        "search_repo_rag",
        "search_repositories",
        "search_workflows",
        "search_forms",
        "search_comments",
    ):
        dispatcher._implementations[name] = await_handler(name, called)

    response = client.post(
        "/chat",
        json={
            "session_id": "s-cb-apex-all-repos",
            "intent": "chatbot",
            "message": "need document from APEX",
            "payload": {"tenantId": TENANT, "specificId": repo_id},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["chatbot_result"]["query"] == "APEX"
    assert body["chatbot_result"]["specificId"] in (None, "")
    assert "which repository" not in body["reply"].lower()
    assert ("search_repo_metadata", "APEX", "") in called
    assert not any(name == "search_repositories" for name, *_rest in called)
    assert not any(name == "search_tickets" for name, *_rest in called)
    cards = next(b for b in body["chatbot_result"]["text"]["blocks"] if b["type"] == "cards")
    assert cards["items"][0]["title"] == "APEX.pdf"
    assert repo_id not in str(next(b for b in body["chatbot_result"]["text"]["blocks"] if "Filter" in str(b.get("title"))))


def test_request_follow_up_searches_workflows_with_the_earlier_value(client, monkeypatch):
    monkeypatch.setenv("CHATBOT_SEARCH_LLM", "true")
    dispatcher = client.app.state.dispatcher
    called = []

    async def fake_chat(messages, **kwargs):
        system = messages[0]["content"]
        if "route an EZOFIS search" in system:
            return {"content": '{"target":"ask_repository","query":""}', "usage": {}}
        return {"content": "No matches.", "usage": {}}

    monkeypatch.setattr(client.app.state.llm_adapter, "chat_completion", fake_chat)

    async def fake_meta(**kwargs):
        called.append(("search_repo_metadata", kwargs.get("query"), kwargs.get("specific_id") or ""))
        return []

    async def fake_tickets(**kwargs):
        called.append(("search_tickets", kwargs.get("query"), kwargs.get("specific_id") or ""))
        return []

    dispatcher._implementations["search_repo_metadata"] = fake_meta
    dispatcher._implementations["search_tickets"] = fake_tickets
    for name in (
        "search_repo_rag",
        "search_repositories",
        "search_workflows",
        "search_forms",
        "search_comments",
    ):
        dispatcher._implementations[name] = await_handler(name, called)

    session = "s-cb-apex-then-request"
    payload = {"tenantId": TENANT}

    def post(message):
        return client.post(
            "/chat",
            json={"session_id": session, "intent": "chatbot", "message": message, "payload": payload},
        )

    first = post("need document from APEX")
    assert first.status_code == 200, first.text
    assert first.json()["chatbot_result"]["query"] == "APEX"
    assert ("search_repo_metadata", "APEX", "") in called

    called.clear()
    second = post("search the request")
    assert second.status_code == 200, second.text
    body = second.json()
    assert "which repository" not in body["reply"].lower()
    assert body["chatbot_result"]["query"] == "APEX"
    assert body["chatbot_result"]["specificId"] in (None, "")
    assert ("search_tickets", "APEX", "") in called
    assert not any(name == "search_repo_metadata" for name, *_rest in called)
    assert not any(name == "search_repositories" for name, *_rest in called)


def await_handler(name, called):
    async def handler(**kwargs):
        called.append((name, kwargs.get("query")))
        return []

    return handler
