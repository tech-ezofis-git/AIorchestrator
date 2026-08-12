"""Unit tests for the permission gate, plus HTTP-level tests proving a
denied request never reaches the Dispatcher — on both /chat and
/actions/{action_id}/confirm (call-count assertions, not just the
response code).
"""
from app.control.permissions import (
    MockPermissionProvider,
    PermissionDeniedError,
    UserContext,
    check_confirm_permission,
    check_permission,
)
from app.core.intent_router import Intent

import pytest


def test_default_user_context_allows_every_intent():
    context = UserContext()
    for intent in Intent:
        check_permission(context, intent)  # must not raise


def test_check_permission_denies_intent_not_in_allowed_set():
    context = UserContext(allowed_intents={Intent.CHAT})

    with pytest.raises(PermissionDeniedError):
        check_permission(context, Intent.MAIL)


def test_check_permission_allows_intent_in_allowed_set():
    context = UserContext(allowed_intents={Intent.CHAT, Intent.SEARCH})

    check_permission(context, Intent.SEARCH)  # must not raise


def test_check_confirm_permission_denies_when_flag_false():
    context = UserContext(can_confirm_actions=False)

    with pytest.raises(PermissionDeniedError):
        check_confirm_permission(context)


def test_check_confirm_permission_allows_by_default():
    check_confirm_permission(UserContext())  # must not raise


async def test_mock_permission_provider_returns_configured_context():
    custom_context = UserContext(user_id="alice", allowed_intents={Intent.CHAT})
    provider = MockPermissionProvider(default_context=custom_context)

    result = await provider.get_user_context("any-session-id")

    assert result is custom_context


def test_chat_returns_403_when_permission_denied_and_dispatcher_never_invoked(client, monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(
        main_module.app.state,
        "permission_provider",
        MockPermissionProvider(default_context=UserContext(allowed_intents=set())),
    )

    dispatch_calls = []

    async def tracking_dispatch(self, tool_name, arguments):
        dispatch_calls.append(tool_name)
        return {}

    monkeypatch.setattr("app.core.dispatcher.Dispatcher.dispatch", tracking_dispatch)

    response = client.post("/chat", json={"session_id": "s-denied", "message": "summarize document DOC-1"})

    assert response.status_code == 403
    assert "Traceback" not in response.text
    assert dispatch_calls == []


def test_confirm_returns_403_when_confirm_permission_denied_and_dispatcher_never_invoked(client, monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(
        main_module.app.state,
        "permission_provider",
        MockPermissionProvider(default_context=UserContext(can_confirm_actions=False)),
    )

    dispatch_confirmed_calls = []

    async def tracking_dispatch_confirmed(self, tool_name, arguments):
        dispatch_confirmed_calls.append(tool_name)
        return {}

    monkeypatch.setattr("app.core.dispatcher.Dispatcher.dispatch_confirmed", tracking_dispatch_confirmed)

    consume_calls = []

    async def tracking_consume(self, action_id):
        consume_calls.append(action_id)
        return None

    monkeypatch.setattr("app.core.pending_actions.PendingActionStore.consume", tracking_consume)

    response = client.post("/actions/some-action/confirm", params={"session_id": "s-confirm-denied"})

    assert response.status_code == 403
    assert "Traceback" not in response.text
    # Permission must block BEFORE the pending action is looked up, and
    # the Dispatcher must never be invoked.
    assert consume_calls == []
    assert dispatch_confirmed_calls == []
