"""Redis-backed store for pending confirmation-gated tool actions (Phase
3d: Mail is the first user). A pending action is created when a gated
tool's arguments are ready (e.g. Mail Agent has drafted subject+body) but
before it runs. Short Redis TTL: an action that's never confirmed just
expires — no cleanup job needed.

Same failure discipline as ContextManager's session store: Redis errors
are caught, logged, and re-raised as PendingActionStoreUnavailableError —
never silently swallowed into "action not found" (which would be
indistinguishable from a caller mistake) or a silent no-op.
"""
import logging
import uuid
from typing import Any, Optional, Protocol

from app.models.pending_action import PendingAction

logger = logging.getLogger("orchestrator.pending_actions")

_KEY_PREFIX = "orchestrator:pending_action"


class PendingActionStoreUnavailableError(Exception):
    """Raised when Redis (the pending-action store) can't be read from or
    written to. Safe to surface to API callers as a generic 503 — never
    wraps the underlying Redis exception's message."""


class RedisLike(Protocol):
    async def get(self, key: str) -> Any: ...
    async def set(self, key: str, value: str, ex: Optional[int] = None) -> Any: ...
    async def delete(self, key: str) -> Any: ...


class PendingActionStore:
    def __init__(self, redis_client: RedisLike, ttl_seconds: int):
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def _key(action_id: str) -> str:
        return f"{_KEY_PREFIX}:{action_id}"

    async def create(self, *, tool_name: str, arguments: dict[str, Any], session_id: str) -> PendingAction:
        action = PendingAction(
            action_id=str(uuid.uuid4()), tool_name=tool_name, arguments=arguments, session_id=session_id
        )
        try:
            await self._redis.set(self._key(action.action_id), action.model_dump_json(), ex=self._ttl_seconds)
        except Exception as exc:
            logger.warning("pending_action_create_failed", extra={"tool_name": tool_name})
            raise PendingActionStoreUnavailableError("Pending action store is currently unavailable.") from exc

        # Tool name + action id only — arguments (e.g. a drafted email
        # body) are never logged.
        logger.info("pending_action_created", extra={"action_id": action.action_id, "tool_name": tool_name})
        return action

    async def get(self, action_id: str) -> Optional[PendingAction]:
        """Returns the pending action, or None if it doesn't exist (never
        created, already consumed, or expired — all indistinguishable at
        this layer, which is fine: the caller-facing outcome is the same
        clean "not found" either way)."""
        try:
            raw = await self._redis.get(self._key(action_id))
        except Exception as exc:
            logger.warning("pending_action_get_failed", extra={"action_id": action_id})
            raise PendingActionStoreUnavailableError("Pending action store is currently unavailable.") from exc
        if not raw:
            return None
        try:
            return PendingAction.model_validate_json(raw)
        except Exception:
            logger.warning("pending_action_corrupt", extra={"action_id": action_id})
            return None

    async def consume(self, action_id: str) -> Optional[PendingAction]:
        """Fetch-then-delete: returns the pending action if it exists and
        deletes it (so it can't be confirmed twice), or None if it
        doesn't exist / already expired / already consumed."""
        action = await self.get(action_id)
        if action is None:
            return None
        try:
            await self._redis.delete(self._key(action_id))
        except Exception as exc:
            logger.warning("pending_action_consume_delete_failed", extra={"action_id": action_id})
            raise PendingActionStoreUnavailableError("Pending action store is currently unavailable.") from exc
        return action
