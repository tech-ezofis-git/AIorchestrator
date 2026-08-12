"""Proves the Phase 3d confirmation gate — the core safety property of
this phase:
  - A requires_confirmation=True tool CANNOT be executed via a direct
    Dispatcher.dispatch() call. Dedicated test, not incidental coverage
    through the Mail happy path (rule 22).
  - dispatch_confirmed() is the only path that executes it.
  - The full draft -> confirm flow works end-to-end via the HTTP API.
  - An unknown/expired/already-confirmed action id is a clean 404, not a
    crash.
  - A pending-action-store outage on confirm is an explicit 503.
"""
import pytest

from app.core.dispatcher import Dispatcher, ToolRequiresConfirmationError
from app.models.tool_schema import ToolSchema

_GATED_SCHEMA = ToolSchema(
    name="gated_tool",
    description="A tool that requires confirmation, for testing the gate.",
    parameters={"type": "object", "properties": {}},
    requires_confirmation=True,
)

_UNGATED_SCHEMA = ToolSchema(
    name="ungated_tool",
    description="An ordinary read-only tool, for contrast.",
    parameters={"type": "object", "properties": {}},
    requires_confirmation=False,
)


async def test_dispatch_refuses_a_tool_that_requires_confirmation():
    """The core safety property: dispatch() must never execute a gated
    tool, under any arguments."""
    dispatcher = Dispatcher()
    calls = []

    async def handler(**kwargs):
        calls.append(kwargs)
        return {"sent": True}

    dispatcher.register_tool(_GATED_SCHEMA, handler)

    with pytest.raises(ToolRequiresConfirmationError):
        await dispatcher.dispatch("gated_tool", {"anything": "at all"})

    # The handler must never have run.
    assert calls == []


async def test_dispatch_confirmed_executes_a_gated_tool():
    dispatcher = Dispatcher()
    calls = []

    async def handler(**kwargs):
        calls.append(kwargs)
        return {"sent": True}

    dispatcher.register_tool(_GATED_SCHEMA, handler)

    result = await dispatcher.dispatch_confirmed("gated_tool", {"x": 1})

    assert result == {"sent": True}
    assert calls == [{"x": 1}]


async def test_dispatch_still_works_normally_for_ungated_tools():
    dispatcher = Dispatcher()

    async def handler(**kwargs):
        return "ok"

    dispatcher.register_tool(_UNGATED_SCHEMA, handler)

    assert await dispatcher.dispatch("ungated_tool", {}) == "ok"


async def test_dispatch_confirmed_also_works_for_ungated_tools():
    """dispatch_confirmed() isn't restricted to gated tools — the safety
    property is that dispatch() refuses gated ones, not that
    dispatch_confirmed() refuses ungated ones. Its only caller in this
    codebase (the confirm endpoint) only ever runs tools named in a
    validated pending action, which today only Mail creates."""
    dispatcher = Dispatcher()

    async def handler(**kwargs):
        return "ok"

    dispatcher.register_tool(_UNGATED_SCHEMA, handler)

    assert await dispatcher.dispatch_confirmed("ungated_tool", {}) == "ok"


def _install_fake_llm(monkeypatch):
    async def fake_chat_completion(self, messages):
        return {
            "content": "Subject: Quarterly Report\nBody:\nHi Jane, please find the report attached.",
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        }

    monkeypatch.setattr("app.llm.adapter.LLMAdapter.chat_completion", fake_chat_completion)


def test_mail_draft_then_confirm_end_to_end(client, monkeypatch):
    _install_fake_llm(monkeypatch)

    draft_response = client.post(
        "/chat",
        json={"session_id": "s-mail", "message": "send an email to jane@example.com about the quarterly report"},
    )
    assert draft_response.status_code == 200
    draft_body = draft_response.json()
    assert draft_body["mail_draft"]["recipient"] == "jane@example.com"
    assert draft_body["mail_draft"]["subject"] == "Quarterly Report"
    action_id = draft_body["mail_draft"]["action_id"]
    assert action_id

    confirm_response = client.post(f"/actions/{action_id}/confirm", params={"session_id": "s-mail"})
    assert confirm_response.status_code == 200
    confirm_body = confirm_response.json()
    assert confirm_body["status"] == "executed"
    assert confirm_body["tool_name"] == "send_email"
    assert confirm_body["result"]["recipient"] == "jane@example.com"
    assert confirm_body["result"]["status"] == "sent_mock"
    # The confirm response never echoes the body back either.
    assert "body" not in confirm_body["result"]

    # Confirming again must fail cleanly — the pending action was consumed.
    second_confirm = client.post(f"/actions/{action_id}/confirm", params={"session_id": "s-mail"})
    assert second_confirm.status_code == 404


def test_confirm_unknown_action_id_returns_clean_404_not_a_crash(client):
    response = client.post("/actions/does-not-exist/confirm", params={"session_id": "s-confirm-404"})

    assert response.status_code == 404
    assert "Traceback" not in response.text


def test_confirm_returns_503_when_pending_action_store_unavailable(client, monkeypatch):
    """Same discipline as every other Redis-backed store in this app: an
    outage on confirm must be an explicit 503, not a silent failure or a
    crash."""
    import app.main as main_module

    async def broken_get(*args, **kwargs):
        raise ConnectionError("simulated redis outage")

    monkeypatch.setattr(main_module.app.state.pending_action_store._redis, "get", broken_get)

    response = client.post("/actions/some-id/confirm", params={"session_id": "s-confirm-503"})

    assert response.status_code == 503
    assert "Traceback" not in response.text


def test_confirm_with_wrong_session_id_gets_same_response_as_unknown_action_id(client, monkeypatch):
    """Session-binding patch: a pending action can only be confirmed by
    the session that drafted it. A different session's confirm attempt
    must get the IDENTICAL response to a genuinely unknown action_id —
    not a distinct error, which would leak which action_ids are valid —
    and must never reach the Dispatcher.
    """
    _install_fake_llm(monkeypatch)

    draft_response = client.post(
        "/chat",
        json={"session_id": "session-a", "message": "send an email to jane@example.com about the report"},
    )
    action_id = draft_response.json()["mail_draft"]["action_id"]

    dispatch_confirmed_calls = []

    async def tracking_dispatch_confirmed(self, tool_name, arguments):
        dispatch_confirmed_calls.append(tool_name)
        return {}

    monkeypatch.setattr("app.core.dispatcher.Dispatcher.dispatch_confirmed", tracking_dispatch_confirmed)

    unknown_response = client.post("/actions/does-not-exist-at-all/confirm", params={"session_id": "session-b"})
    wrong_session_response = client.post(f"/actions/{action_id}/confirm", params={"session_id": "session-b"})

    assert wrong_session_response.status_code == unknown_response.status_code == 404
    assert wrong_session_response.json() == unknown_response.json()
    assert dispatch_confirmed_calls == []


def test_confirm_with_wrong_session_id_does_not_consume_the_pending_action(client, monkeypatch):
    """A wrong-session confirm attempt must not burn the legitimate
    owner's pending action — it must still be confirmable afterward, not
    a casualty of someone else's (wrong or malicious) guess."""
    _install_fake_llm(monkeypatch)

    draft_response = client.post(
        "/chat",
        json={"session_id": "session-a", "message": "send an email to jane@example.com about the report"},
    )
    action_id = draft_response.json()["mail_draft"]["action_id"]

    wrong_session_response = client.post(f"/actions/{action_id}/confirm", params={"session_id": "session-b"})
    assert wrong_session_response.status_code == 404

    correct_session_response = client.post(f"/actions/{action_id}/confirm", params={"session_id": "session-a"})
    assert correct_session_response.status_code == 200
    assert correct_session_response.json()["status"] == "executed"
