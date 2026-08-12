"""fetch_memories tool — ToolSchema-conformant wrapper around
MemoryStore.fetch_recent (real Postgres-backed logic, not mocked — see
app/control/memory_store.py). requires_confirmation=False: a read-only
lookup.

This handler never raises: MemoryStore.fetch_recent's contract is to
catch any failure internally and return an empty list (see rule 7 in the
Phase 5a spec — a failed memory read must degrade gracefully, not break
Chat's normal response). Registered with the Dispatcher in
app/main.py's lifespan; called only via Dispatcher.dispatch() from
app/agents/chat_agent.py's normal-chat branch.
"""
from app.control.memory_store import MemoryStore
from app.models.tool_schema import ToolSchema

FETCH_MEMORIES_SCHEMA = ToolSchema(
    name="fetch_memories",
    description="Fetch the user's most recent remembered facts.",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "The user whose facts to fetch."},
            "limit": {"type": "integer", "description": "Maximum number of facts to return, most recent first."},
        },
        "required": ["user_id", "limit"],
    },
    requires_confirmation=False,
)


def make_fetch_memories_handler(memory_store: MemoryStore):
    """Returns an async handler closing over `memory_store`, matching the
    Dispatcher's ToolImplementation signature (**kwargs -> Any)."""

    async def handler(*, user_id: str, limit: int) -> dict:
        facts = await memory_store.fetch_recent(user_id=user_id, limit=limit)
        return {"user_id": user_id, "facts": facts}

    return handler
