"""Scripted ``FunctionModel`` builders shared by the eval-driver tests (ADR-0017 §4).

Each model streams one tool call per fresh leg and plain text on a resume leg — the capstone
pattern (``tests/integration/test_milestone1_capstone.py``). Injected as the agent's *base* model
by the ``install_model`` fixture so ``build_agent()`` builds a genuine decode agent (all tools, real
instructions hook) whose only fake part is the model — offline, no keys. A regular importable
module, not a ``conftest``: ``from support.eval_models import read_then_finish``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel


def _last_request_has_tool_return(messages: list[ModelMessage]) -> bool:
    """True when the most recent request carries a tool result (i.e. this is a resume leg)."""
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            return any(isinstance(part, ToolReturnPart) for part in message.parts)
    return False


def read_then_finish(path: str, final_text: str) -> FunctionModel:
    """A model that calls ``read(path)`` once, then finishes with ``final_text`` (capstone pattern)."""

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        if _last_request_has_tool_return(messages):
            yield final_text
            return
        yield {0: DeltaToolCall(name="read", json_args=json.dumps({"path": path}))}

    return FunctionModel(stream_function=stream_function)


def write_then_finish(path: str, content: str, final_text: str) -> FunctionModel:
    """A model that calls ``write(path, content)`` once, then finishes with ``final_text``.

    Used for the deny path: under ``DEFAULT`` gate mode the mutating ``write`` reaches the resolver.
    """

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        if _last_request_has_tool_return(messages):
            yield final_text
            return
        yield {
            0: DeltaToolCall(name="write", json_args=json.dumps({"path": path, "content": content}))
        }

    return FunctionModel(stream_function=stream_function)


def runaway_reader(path: str) -> FunctionModel:
    """A model that calls ``read(path)`` on every leg forever — only the request cap can stop it."""

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        yield {0: DeltaToolCall(name="read", json_args=json.dumps({"path": path}))}

    return FunctionModel(stream_function=stream_function)


def echo_line(text: str) -> FunctionModel:
    """A model that emits one plain-text line and calls no tools — for the history pre-fill probe."""

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        yield text

    return FunctionModel(stream_function=stream_function)
