"""Postgres-backed durable memory store (Phase 5a) — cross-session facts,
scoped by `user_id` (not `session_id`; the whole point is persistence
across sessions), written only via Chat's explicit "remember that ..."
trigger (see app/agents/chat_agent.py) and read to enrich Chat's existing
LLM call.

Two deliberately different failure disciplines, matching the Phase 1
Redis correction's reasoning:
  - `store()` fails LOUD: any failure raises MemoryStoreUnavailableError,
    never a silent success — the caller must never be told "I'll
    remember that" when nothing was actually persisted.
  - `fetch_recent()` fails SOFT: any failure is logged as a warning and
    treated as "no memories" — a failed read doesn't misrepresent
    anything to the caller, and Chat's normal response must not break
    because memory enrichment couldn't happen this time.
"""
import logging
from typing import Any, Protocol

logger = logging.getLogger("orchestrator.memory_store")


class MemoryStoreUnavailableError(Exception):
    """Raised when a memory WRITE fails (Postgres down, ...). Safe to
    surface to API callers as a generic error via the Dispatcher's
    existing ToolExecutionError wrapping — never swallowed, since a lost
    write must never be reported back as "remembered"."""


class DBExecutor(Protocol):
    async def execute(self, query: str, *args: Any) -> Any: ...
    async def fetch(self, query: str, *args: Any) -> list[Any]: ...


class MemoryStore:
    def __init__(self, db: DBExecutor):
        self._db = db

    async def store(self, *, user_id: str, fact: str) -> None:
        """Persist one fact for `user_id`. Raises
        MemoryStoreUnavailableError on any failure — never a silent
        success."""
        try:
            await self._db.execute(
                "INSERT INTO memories (user_id, fact) VALUES ($1, $2)",
                user_id,
                fact,
            )
        except Exception as exc:
            logger.warning("memory_store_write_failed", extra={"user_id": user_id, "error_type": type(exc).__name__})
            raise MemoryStoreUnavailableError("Memory store is currently unavailable.") from exc

        # Outcome only — never the fact's actual content.
        logger.info("memory_stored", extra={"user_id": user_id, "outcome": "stored"})

    async def fetch_recent(self, *, user_id: str, limit: int) -> list[str]:
        """Returns up to `limit` of `user_id`'s most recent facts, newest
        first. Never raises — any failure is logged as a warning and
        treated as "no memories" (see module docstring)."""
        try:
            rows = await self._db.fetch(
                "SELECT fact FROM memories WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
                user_id,
                limit,
            )
        except Exception as exc:
            logger.warning("memory_store_read_failed", extra={"user_id": user_id, "error_type": type(exc).__name__})
            return []
        return [row["fact"] for row in rows]
