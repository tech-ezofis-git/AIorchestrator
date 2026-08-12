"""End-to-end: `mail` intent -> fail-closed recipient extraction -> LLM
drafts subject+body -> a pending action is created (NOT sent) -> response
includes the full draft + action_id. Plus the no-valid-recipient refusal
path — Mail is the only side-effecting agent, so this must fail closed
just like AP does for financial data, and needs its own test, not just
happy-path coverage. See tests/test_confirmation_flow.py for the
confirm-side of this flow and the core Dispatcher gate property.
"""
def _install_fake_llm(monkeypatch):
    async def fake_chat_completion(self, messages):
        return {
            "content": "Subject: Meeting Follow-up\nBody:\nHi, thanks for the meeting today.",
            "usage": {"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)


def test_mail_intent_with_valid_recipient_returns_draft_and_action_id_no_send(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    response = client.post(
        "/chat",
        json={"session_id": "s-mail", "message": "send an email to bob@example.com about the meeting"},
    )

    assert response.status_code == 200
    body = response.json()
    draft = body["mail_draft"]
    assert draft["recipient"] == "bob@example.com"
    assert draft["subject"] == "Meeting Follow-up"
    assert draft["body"] == "Hi, thanks for the meeting today."
    assert draft["action_id"]
    assert draft["action_id"] in body["reply"]
    assert "confirm" in body["reply"].lower()
    assert body["token_usage"]["total_tokens"] == 21
    # Other intents' fields stay absent for Mail.
    assert body["chunk_ids"] is None
    assert body["invoice_reference"] is None
    assert body["ocr_result"] is None


def test_mail_intent_with_no_valid_email_fails_closed(client, monkeypatch):
    llm_calls = []

    async def tracking_chat_completion(self, messages):
        llm_calls.append(messages)
        return {"content": "should not be called", "usage": None}

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", tracking_chat_completion)

    response = client.post("/chat", json={"session_id": "s-mail-ambiguous", "message": "send an email to my manager"})

    assert response.status_code == 200
    body = response.json()
    assert "couldn't find a valid email" in body["reply"].lower()
    assert body["mail_draft"] is None
    assert body["token_usage"] is None
    # Fail closed: no LLM call, no pending action, nothing reaches Redis.
    assert llm_calls == []


def test_mail_intent_with_no_recipient_at_all_returns_clarification(client):
    response = client.post("/chat", json={"session_id": "s-mail-none", "message": "compose an email"})

    assert response.status_code == 200
    body = response.json()
    assert "couldn't find a valid email" in body["reply"].lower()
    assert body["mail_draft"] is None


def test_mail_intent_not_triggered_by_bare_word_mail(client, monkeypatch):
    """Narrow trigger set: "check the mail room policy" doesn't match any
    action-verb trigger phrase, so it resolves to chat, not mail."""

    async def fake_chat_completion(self, messages):
        return {
            "content": "Here's the mail room policy info...",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)

    response = client.post("/chat", json={"session_id": "s-not-mail", "message": "check the mail room policy"})

    assert response.status_code == 200
    assert response.json()["mail_draft"] is None
