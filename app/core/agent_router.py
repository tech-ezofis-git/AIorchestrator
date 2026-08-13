"""Routes a classified Intent to the agent that handles it.

Phase 1 registered only the Chat agent. Phase 2 registered Search, Phase 3a
registered Summary and Insight, Phase 3b registered OCR and Forecast,
Phase 3c registered AP, and Phase 3d registers Mail — all the same way
Phase 1 said they would: `agent_router.register(Intent.X, x_agent.handle)`
in app/main.py's lifespan, no changes needed to this file's routing logic.
Every Intent value is now registered; the NotImplementedError path below
still guards any future Intent added to the enum before it's registered.
"""
from typing import Any, Awaitable, Callable

from app.agents.chat_agent import ChatAgent
from app.core.intent_router import Intent

# Handlers return a dict, e.g. {"reply": str, "usage": dict | None} for Chat.
# Future agents (Search, Summary, ...) may shape their dict differently —
# the Response Composer is what knows how to turn it into an API response.
AgentHandler = Callable[..., Awaitable[dict[str, Any]]]


class AgentRouter:
    def __init__(self, chat_agent: ChatAgent):
        self._handlers: dict[Intent, AgentHandler] = {
            Intent.CHAT: chat_agent.handle,
        }

    def register(self, intent: Intent, handler: AgentHandler) -> None:
        """Extension point for Phase 3 agents (Search, Summary, Insight, ...)."""
        self._handlers[intent] = handler

    async def route(
        self,
        intent: Intent,
        *,
        session_id: str,
        message: str,
        history: list[dict],
        **kwargs,
    ) -> dict[str, Any]:
        handler = self._handlers.get(intent)
        if handler is None:
            # TODO(phase-3): implement agents for the remaining capabilities.
            raise NotImplementedError(f"No agent registered for intent '{intent.value}' yet.")
        return await handler(
            session_id=session_id, message=message, history=history, **kwargs
        )
