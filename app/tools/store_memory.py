"""store_memory tool — ToolSchema-conformant wrapper around
MemoryStore.store (real Postgres-backed logic, not mocked — see
app/control/memory_store.py). requires_confirmation=False: this is a
durable write, but not a side effect with real-world consequences the way
send_email is, so it doesn't need the confirm-before-execute gate.

Registered with the Dispatcher in app/main.py's lifespan. Called only via
Dispatcher.dispatch() from app/agents/chat_agent.py's memory-write branch
— ChatAgent never calls MemoryStore directly, same indirection every
other tool in this app goes through.
"""
from app.control.memory_store import MemoryStore
from app.models.tool_schema import ToolSchema

STORE_MEMORY_SCHEMA = ToolSchema(
    name="store_memory",
    description="Persist a durable fact about the user, for recall in future sessions.",
    parameters={
        "type": "object",
        "properties": {
            "user_id": {"type": "string", "description": "The user this fact belongs to."},
            "fact": {"type": "string", "description": "The clean, storable fact to remember."},
        },
        "required": ["user_id", "fact"],
    },
    requires_confirmation=False,
)


def make_store_memory_handler(memory_store: MemoryStore):
    """Returns an async handler closing over `memory_store`, matching the
    Dispatcher's ToolImplementation signature (**kwargs -> Any)."""

    async def handler(*, user_id: str, fact: str) -> dict:
        await memory_store.store(user_id=user_id, fact=fact)
        return {"user_id": user_id, "fact": fact, "status": "stored"}

    return handler
