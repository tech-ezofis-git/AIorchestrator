"""Session history (Redis) + EZOFIS user context (stub).

All I/O here is async — Redis via redis.asyncio, EZOFIS via the (mocked)
integrations client.

Redis failures (connection errors, timeouts, ...) are caught here, logged,
and re-raised as SessionStoreUnavailableError. Conversation history must
never be silently dropped while the request still reports success, so this
module never swallows a Redis failure into an empty/unsaved history —
callers (see app/main.py) are expected to turn this into a 503.
"""
import json
import logging
from typing import Any, Optional

from redis.asyncio import Redis

from app.integrations.ezofis_client import EzofisClient

logger = logging.getLogger("orchestrator.context_manager")

_HISTORY_KEY_PREFIX = "orchestrator:session"


class SessionStoreUnavailableError(Exception):
    """Raised when Redis (the session store) can't be read from or written
    to. Safe to surface to API callers as a generic 503 — never wraps the
    underlying Redis exception's message."""


class ContextManager:
    def __init__(self, redis_client: Redis, ttl_seconds: int, ezofis_client: Optional[EzofisClient] = None):
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds
        self._ezofis = ezofis_client or EzofisClient()

    @staticmethod
    def _history_key(session_id: str) -> str:
        return f"{_HISTORY_KEY_PREFIX}:{session_id}:history"

    async def get_history(self, session_id: str) -> list[dict[str, str]]:
        """Load or create session history. Returns [] for a brand-new
        session. Raises SessionStoreUnavailableError if Redis can't be
        reached — a Redis outage must fail the request, not silently
        degrade to an empty history while still returning success."""
        try:
            raw = await self._redis.get(self._history_key(session_id))
        except Exception as exc:
            logger.warning("redis_get_failed", extra={"session_id": session_id})
            raise SessionStoreUnavailableError("Session store is currently unavailable.") from exc
        if not raw:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Corrupt/unexpected payload (not a Redis outage) — treat as a
            # fresh session rather than fail the request.
            return []

    async def append_turn(self, session_id: str, role: str, content: str) -> None:
        """Append one turn ({role, content}) to session history and refresh
        the TTL. Raises SessionStoreUnavailableError if Redis can't be
        reached, for either the read or the write half of this operation."""
        history = await self.get_history(session_id)  # raises SessionStoreUnavailableError on its own
        history.append({"role": role, "content": content})
        try:
            await self._redis.set(
                self._history_key(session_id),
                json.dumps(history),
                ex=self._ttl_seconds,
            )
        except Exception as exc:
            logger.warning("redis_append_failed", extra={"session_id": session_id})
            raise SessionStoreUnavailableError("Session store is currently unavailable.") from exc

    async def get_user_context(self, user_id: str) -> dict[str, Any]:
        """EZOFIS user context lookup — currently backed by mock data.

        Not yet called from the Phase 1 /chat flow (no user_id on the
        request), but wired here so agent_router/chat_agent can start
        using it as soon as auth/user identity is threaded through.
        """
        return await self._ezofis.get_user_context(user_id)
