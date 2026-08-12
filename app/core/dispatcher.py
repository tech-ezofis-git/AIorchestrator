"""Tool-call dispatch — implemented for real since Phase 3a; Phase 3d adds
an architectural confirmation gate.

Maintains a registry mapping ToolSchema.name -> handler. Agents call tools
through `dispatch()`, never by calling the underlying integration client
directly — that indirection is the entire point of the ToolSchema contract
(app/models/tool_schema.py) existing: every tool, regardless of what it
wraps, gets registered and invoked the same generic way.

Confirmation gate (Phase 3d): a tool whose ToolSchema sets
requires_confirmation=True (currently just send_email) CANNOT be run via
dispatch() — that raises ToolRequiresConfirmationError, unconditionally,
regardless of caller. This is enforced here, architecturally, not left to
agents to remember to check. The only way such a tool actually executes is
`dispatch_confirmed()`, which only the POST /actions/{action_id}/confirm
endpoint calls, and only after validating a pending action was created for
it (see app/core/pending_actions.py). This generalizes to any future
requires_confirmation=True tool without further Dispatcher changes — the
gate is the flag, not a hardcoded tool name.

Failure discipline matches the rest of the app (Redis -> 503, pgvector ->
503, LLM/embedding provider -> 502): an unregistered tool name or a
handler exception never crashes or silently no-ops — both become a typed,
catchable error. See app/main.py for how these map to HTTP responses.
"""
import logging
from typing import Any, Awaitable, Callable

from app.models.tool_schema import ToolSchema

logger = logging.getLogger("orchestrator.dispatcher")

ToolImplementation = Callable[..., Awaitable[Any]]


class ToolNotFoundError(Exception):
    """Raised when a tool name that was never registered is looked up
    (via dispatch() or dispatch_confirmed()). This is an internal wiring
    bug (an agent referencing a tool that doesn't exist), not a
    user-input problem — see app/main.py's catch-all handler for how it
    surfaces as a generic 500."""


class ToolExecutionError(Exception):
    """Raised when a registered tool's handler raises. Wraps the failure so
    a typed, catchable error crosses the Dispatcher boundary instead of an
    arbitrary exception type — and never repeats the handler's raw
    message, which could echo back provider response internals."""


class ToolRequiresConfirmationError(Exception):
    """Raised when dispatch() is called directly for a tool whose
    ToolSchema has requires_confirmation=True. This is not an environment
    failure or a caller-input problem to recover from — it's a signal that
    calling code is trying to bypass the confirm-before-execute gate,
    which is never permitted, regardless of caller or arguments. Such
    tools may only run via dispatch_confirmed(), which in this codebase
    only POST /actions/{action_id}/confirm calls, only after validating a
    pending action (see app/core/pending_actions.py)."""


class Dispatcher:
    """Registry + invocation point for tools."""

    def __init__(self):
        self._schemas: dict[str, ToolSchema] = {}
        self._implementations: dict[str, ToolImplementation] = {}

    def register_tool(self, schema: ToolSchema, implementation: ToolImplementation) -> None:
        self._schemas[schema.name] = schema
        self._implementations[schema.name] = implementation

    def list_schemas(self) -> list[ToolSchema]:
        return list(self._schemas.values())

    async def dispatch(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Look up and execute a registered tool's handler — except tools
        whose schema requires confirmation, which this refuses to run
        directly (see ToolRequiresConfirmationError and
        dispatch_confirmed below).

        TODO(phase-4+): validate `arguments` against the tool's JSON
        schema before invoking the implementation.
        """
        schema = self._schemas.get(tool_name)
        if schema is None or tool_name not in self._implementations:
            logger.warning("tool_dispatch_unregistered", extra={"tool_name": tool_name})
            raise ToolNotFoundError(f"Unknown tool '{tool_name}'.")

        if schema.requires_confirmation:
            logger.warning("tool_dispatch_blocked_requires_confirmation", extra={"tool_name": tool_name})
            raise ToolRequiresConfirmationError(
                f"Tool '{tool_name}' requires confirmation and cannot be dispatched directly."
            )

        return await self._execute(tool_name, arguments)

    async def dispatch_confirmed(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Executes a tool regardless of its requires_confirmation flag.

        This is NOT a general-purpose bypass callers can reach for —
        there is no gate-bypass check inside this method beyond the tool
        existing. The gate is `dispatch()` refusing; the "approval" is
        this codebase only ever calling this method from one place (the
        POST /actions/{action_id}/confirm endpoint), and only after
        `PendingActionStore` has independently confirmed a pending action
        exists for this exact tool call. Do not call this from an agent.
        """
        if tool_name not in self._implementations:
            logger.warning("tool_dispatch_unregistered", extra={"tool_name": tool_name})
            raise ToolNotFoundError(f"Unknown tool '{tool_name}'.")
        return await self._execute(tool_name, arguments)

    async def _execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        implementation = self._implementations[tool_name]
        try:
            return await implementation(**arguments)
        except Exception as exc:
            logger.warning(
                "tool_dispatch_failed",
                extra={"tool_name": tool_name, "error_type": type(exc).__name__},
            )
            raise ToolExecutionError(f"Tool '{tool_name}' failed to execute.") from exc
