"""Proves the Dispatcher's generic tool-registration/execution mechanism —
independent of any specific agent — including the unregistered-tool and
handler-exception error paths.
"""
import pytest

from app.core.dispatcher import Dispatcher, ToolExecutionError, ToolNotFoundError
from app.models.tool_schema import ToolSchema

_ECHO_SCHEMA = ToolSchema(
    name="echo",
    description="Echoes its input back.",
    parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    requires_confirmation=False,
)


async def test_dispatch_executes_registered_tool():
    dispatcher = Dispatcher()

    async def echo_handler(*, text: str) -> str:
        return f"echo: {text}"

    dispatcher.register_tool(_ECHO_SCHEMA, echo_handler)

    result = await dispatcher.dispatch("echo", {"text": "hello"})

    assert result == "echo: hello"


async def test_list_schemas_returns_registered_tools():
    dispatcher = Dispatcher()

    async def echo_handler(*, text: str) -> str:
        return text

    assert dispatcher.list_schemas() == []
    dispatcher.register_tool(_ECHO_SCHEMA, echo_handler)

    assert dispatcher.list_schemas() == [_ECHO_SCHEMA]


async def test_dispatch_unregistered_tool_raises_defined_error():
    dispatcher = Dispatcher()

    with pytest.raises(ToolNotFoundError):
        await dispatcher.dispatch("nonexistent_tool", {})


async def test_dispatch_wraps_handler_exceptions():
    dispatcher = Dispatcher()

    async def broken_handler(**kwargs):
        raise RuntimeError("simulated tool failure, secret=should-never-leak")

    dispatcher.register_tool(
        ToolSchema(name="broken", description="Always fails.", parameters={"type": "object", "properties": {}}),
        broken_handler,
    )

    with pytest.raises(ToolExecutionError) as exc_info:
        await dispatcher.dispatch("broken", {})

    # The handler's raw exception message must not leak through.
    assert "should-never-leak" not in str(exc_info.value)


async def test_dispatch_passes_arguments_through_as_kwargs():
    dispatcher = Dispatcher()
    received = {}

    async def handler(*, a: int, b: str) -> None:
        received["a"] = a
        received["b"] = b

    dispatcher.register_tool(
        ToolSchema(name="two_args", description="", parameters={"type": "object", "properties": {}}), handler
    )

    await dispatcher.dispatch("two_args", {"a": 1, "b": "x"})

    assert received == {"a": 1, "b": "x"}
