"""Permission checking — the third gate, run after intent classification
but before the agent is invoked (permission is per-capability, so it
can't be checked before the intent is known; see app/main.py for the
full pipeline order).

TODO(ezofis-permissions): no real EZOFIS permission data, roles, or auth
API exists yet. `MockPermissionProvider` is a placeholder returning a
fixed `UserContext`; when real EZOFIS auth/permission data is available,
replace `get_user_context` with a real lookup (by user id, session, or
auth token — mechanism TBD). No other module should need to change; they
only depend on this class's method signature and `UserContext`'s shape.
"""
import logging
from typing import Optional

from app.core.intent_router import Intent

logger = logging.getLogger("orchestrator.permissions")


class PermissionDeniedError(Exception):
    """Raised when a user context isn't permitted to perform the gated
    action — a specific intent on /chat, or confirming a pending action on
    POST /actions/{action_id}/confirm. Safe to surface as a generic 403;
    this exception's own message (which may name the intent) is for
    server-side logs only — app/main.py uses a fixed, generic detail text
    for the caller."""


class UserContext:
    """TODO(ezofis-permissions): replace with real EZOFIS user/role data
    once an auth mechanism exists. For now, a minimal mock shape: a flat
    set of allowed intents plus a separate flag for confirming pending
    actions (confirm has no single intent to check — see
    check_confirm_permission below). Every one of the 8 intents is allowed
    by default — the point of this phase is that the gate exists and is
    provably enforced (see tests/test_permissions.py), not that the mock
    data is elaborate.
    """

    def __init__(
        self,
        user_id: str = "anonymous",
        allowed_intents: Optional[set[Intent]] = None,
        can_confirm_actions: bool = True,
    ):
        self.user_id = user_id
        self.allowed_intents = allowed_intents if allowed_intents is not None else set(Intent)
        self.can_confirm_actions = can_confirm_actions


class MockPermissionProvider:
    """TODO(ezofis-permissions): replace with a real EZOFIS permission/role
    lookup. For now, always returns the same configured `UserContext`
    regardless of `session_id` — the parameter exists so this method's
    signature already matches what a real lookup would need."""

    def __init__(self, default_context: Optional[UserContext] = None):
        self._default_context = default_context or UserContext()

    async def get_user_context(self, session_id: str) -> UserContext:
        return self._default_context


def check_permission(user_context: UserContext, intent: Intent) -> None:
    """Raises PermissionDeniedError if `intent` isn't in
    `user_context.allowed_intents`. Returns None otherwise."""
    if intent not in user_context.allowed_intents:
        logger.info("permission_denied", extra={"user_id": user_context.user_id, "intent": intent.value})
        raise PermissionDeniedError(f"User '{user_context.user_id}' is not permitted to use intent '{intent.value}'.")


def check_confirm_permission(user_context: UserContext) -> None:
    """Raises PermissionDeniedError if `user_context` isn't permitted to
    confirm pending actions at all. Deliberately intent-agnostic: the
    confirm endpoint gates BEFORE looking up which tool a pending action
    maps to (fail-fast, same as every other gate here), so it can't check
    a specific intent's permission the way /chat does."""
    if not user_context.can_confirm_actions:
        logger.info("confirm_permission_denied", extra={"user_id": user_context.user_id})
        raise PermissionDeniedError(f"User '{user_context.user_id}' is not permitted to confirm actions.")
