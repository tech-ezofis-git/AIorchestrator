"""The Chat agent — Phase 1's only wired-up capability, extended in Phase
5a with durable, cross-session memory (Chat only; every other intent is
untouched).

Two branches:
  - An explicit "remember that ..." (etc.) trigger routes into a WRITE
    flow: one LLM call (ResponseComposer.synthesize_memory_fact) extracts
    a clean, storable fact from the free-form instruction, then it's
    persisted via the Dispatcher's `store_memory` tool. This is the one
    call on the Chat path that genuinely earns its cost — unlike Chat's
    normal single pass-through call, "remember X" instructions are
    conversational and need parsing into a clean fact. store_memory
    failures are NOT caught here — ToolExecutionError propagates up
    through AgentRouter/main.py exactly like every other tool failure in
    this app, so there is never a false "I'll remember that" when
    nothing was actually stored (rule 6).
  - Every OTHER Chat message first fetches the user's most recent
    remembered facts via the Dispatcher's `fetch_memories` tool (capped,
    most recent `_MEMORY_INJECT_LIMIT`) and folds them into the SAME LLM
    call Chat already makes, as an extra system message — never a second
    LLM call (rule 8). `fetch_memories`/`MemoryStore.fetch_recent` never
    raises (see app/control/memory_store.py) — a failed read silently
    yields no memories, so Chat's normal response is unaffected (rule 7).

Memory is scoped by `user_id`, taken from the existing mocked
`MockPermissionProvider.get_user_context(session_id).user_id` (Phase 4a)
— not `session_id` — since the whole point is persistence across
sessions. No real EZOFIS identity system is invented; this reuses the
existing mock as-is.
"""
import logging
from typing import Any, Protocol

from app.core.dispatcher import Dispatcher
from app.core.response_composer import ResponseComposer
from app.llm.adapter import LLMAdapter

logger = logging.getLogger("orchestrator.chat_agent")

_SYSTEM_PROMPT = (
    "You are the AI assistant for EZOFIS, an enterprise document and "
    "workflow platform. Be helpful, concise, and accurate."
)

# Deliberately narrow and explicit — not a broad heuristic. A message must
# contain one of these phrases (case-insensitive substring match) to
# trigger the memory-write path instead of ordinary chat.
_MEMORY_WRITE_TRIGGERS = (
    "remember that",
    "please remember",
    "for future reference",
    "don't forget that",
    "dont forget that",
)

# How many of the user's most recent remembered facts get folded into
# Chat's prompt. A fixed, documented cap so prompt size can't grow
# unboundedly as a user accumulates memories over time.
_MEMORY_INJECT_LIMIT = 5


class UserContextProvider(Protocol):
    async def get_user_context(self, session_id: str) -> Any: ...  # object with a `.user_id` attribute


def _is_memory_write_trigger(message: str) -> bool:
    normalized = message.strip().lower()
    return any(trigger in normalized for trigger in _MEMORY_WRITE_TRIGGERS)


class ChatAgent:
    def __init__(
        self,
        llm_adapter: LLMAdapter,
        dispatcher: Dispatcher,
        response_composer: ResponseComposer,
        user_context_provider: UserContextProvider,
    ):
        self._llm = llm_adapter
        self._dispatcher = dispatcher
        self._response_composer = response_composer
        self._user_context_provider = user_context_provider

    async def handle(self, *, session_id: str, message: str, history: list[dict[str, str]], **_: object) -> dict:
        """Returns {"reply": str, "usage": dict | None}."""
        user_context = await self._user_context_provider.get_user_context(session_id)
        user_id = user_context.user_id

        if _is_memory_write_trigger(message):
            return await self._handle_memory_write(user_id=user_id, message=message)

        return await self._handle_normal_chat(user_id=user_id, message=message, history=history)

    async def _handle_memory_write(self, *, user_id: str, message: str) -> dict:
        synthesis = await self._response_composer.synthesize_memory_fact(instruction=message)
        fact = synthesis["content"].strip()

        # store_memory is requires_confirmation=False, so a direct
        # dispatch() call is correct (unlike Mail's send_email). Any
        # failure raises ToolExecutionError, which propagates unchanged —
        # no try/except here on purpose (rule 6).
        await self._dispatcher.dispatch("store_memory", {"user_id": user_id, "fact": fact})

        return {
            "reply": f"Got it, I'll remember that: {fact}",
            "usage": synthesis["usage"],
        }

    async def _handle_normal_chat(self, *, user_id: str, message: str, history: list[dict[str, str]]) -> dict:
        memories = await self._fetch_memories(user_id)

        messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
        messages.extend(history)
        if memories:
            facts_block = "\n".join(f"- {fact}" for fact in memories)
            messages.append(
                {
                    "role": "system",
                    "content": f"Known facts about this user from previous sessions:\n{facts_block}",
                }
            )
        messages.append({"role": "user", "content": message})

        result = await self._llm.chat_completion(messages)
        return {"reply": result["content"], "usage": result["usage"]}

    async def _fetch_memories(self, user_id: str) -> list[str]:
        # No try/except here: fetch_memories/MemoryStore.fetch_recent
        # never raises (graceful degradation happens one layer down —
        # see module docstring), so dispatch() can only fail here for a
        # genuine wiring bug (ToolNotFoundError), which should surface
        # like any other internal bug, not be swallowed.
        result = await self._dispatcher.dispatch("fetch_memories", {"user_id": user_id, "limit": _MEMORY_INJECT_LIMIT})
        return result["facts"]
