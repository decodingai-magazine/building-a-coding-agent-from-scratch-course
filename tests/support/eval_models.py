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

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
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


def bash_then_finish(command: str, final_text: str) -> FunctionModel:
    """A model that calls ``bash(command)`` once, then finishes with ``final_text``.

    ``bash`` awaits the executor seam directly (no file-tool thread hop), so it routes straight to the
    injected sandbox executor — the clean way to drive the benchmark sandbox from a scripted model.
    """

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        if _last_request_has_tool_return(messages):
            yield final_text
            return
        yield {0: DeltaToolCall(name="bash", json_args=json.dumps({"command": command}))}

    return FunctionModel(stream_function=stream_function)


def grep_then_finish(pattern: str, final_text: str) -> FunctionModel:
    """A model that calls ``grep(pattern)`` once, then finishes with ``final_text``.

    Drives the grep-vs-bash probe: the discipline being graded is that a content search uses the
    ``grep`` tool, not a ``bash`` shell-out.
    """

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        if _last_request_has_tool_return(messages):
            yield final_text
            return
        yield {0: DeltaToolCall(name="grep", json_args=json.dumps({"pattern": pattern}))}

    return FunctionModel(stream_function=stream_function)


def edit_then_finish(path: str, old_string: str, new_string: str, final_text: str) -> FunctionModel:
    """A model that calls ``edit(path, old_string, new_string)`` once, then finishes.

    Drives the edit-precision / diff-minimality probes: one surgical ``edit`` whose changed-line
    footprint the diff metric grades.
    """

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        if _last_request_has_tool_return(messages):
            yield final_text
            return
        yield {
            0: DeltaToolCall(
                name="edit",
                json_args=json.dumps(
                    {"path": path, "old_string": old_string, "new_string": new_string}
                ),
            )
        }

    return FunctionModel(stream_function=stream_function)


def web_fetch_then_finish(url: str, final_text: str) -> FunctionModel:
    """A model that calls ``web_fetch(url)`` once, then finishes with ``final_text``.

    Drives the web-fetch-discipline probe against the local ``http.server`` fixture — no real network.
    """

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        if _last_request_has_tool_return(messages):
            yield final_text
            return
        yield {0: DeltaToolCall(name="web_fetch", json_args=json.dumps({"url": url}))}

    return FunctionModel(stream_function=stream_function)


def lsp_diagnostics_then_finish(path: str, final_text: str) -> FunctionModel:
    """A model that calls ``lsp(op="diagnostics", path=path)`` once, then finishes with ``final_text``.

    Drives the lsp-diagnostics probe against a REAL ``ty`` language server (offline, no keys) — the tool
    spawns ``ty`` on the seeded file and returns its diagnostics before the model reports them.
    """

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        if _last_request_has_tool_return(messages):
            yield final_text
            return
        yield {
            0: DeltaToolCall(name="lsp", json_args=json.dumps({"op": "diagnostics", "path": path}))
        }

    return FunctionModel(stream_function=stream_function)


def enter_plan_mode_then_finish(final_text: str) -> FunctionModel:
    """A model that calls ``enter_plan_mode`` once, then finishes with ``final_text`` — no edits.

    Drives the plan-mode-discipline probe: the agent switches to plan mode and presents a plan instead
    of changing anything.
    """

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        if _last_request_has_tool_return(messages):
            yield final_text
            return
        yield {0: DeltaToolCall(name="enter_plan_mode", json_args=json.dumps({}))}

    return FunctionModel(stream_function=stream_function)


def todo_write_then_finish(contents: list[str], final_text: str) -> FunctionModel:
    """A model that calls ``todo_write`` with one task per entry in ``contents``, then finishes.

    Drives the todo-planning probe: each ``contents`` string becomes a pending
    :class:`~decode.entities.task.Task` (id + content + status), so the recorded call carries the
    ``tasks`` list a :class:`~evals.harness.metrics.ToolArgsMetric` inspects for a genuinely
    multi-step plan (``>= 3`` items).
    """
    tasks = [
        {"id": str(index), "content": content, "status": "pending"}
        for index, content in enumerate(contents, start=1)
    ]

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        if _last_request_has_tool_return(messages):
            yield final_text
            return
        yield {0: DeltaToolCall(name="todo_write", json_args=json.dumps({"tasks": tasks}))}

    return FunctionModel(stream_function=stream_function)


def skill_then_finish(name: str, final_text: str) -> FunctionModel:
    """A model that calls ``skill(name)`` once, then finishes with ``final_text``.

    Drives the skill-dispatch probe: the discipline graded is that the agent dispatches the RIGHT
    skill by name (a :class:`~evals.harness.metrics.ToolArgsMetric` on the ``name`` argument).
    """

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        if _last_request_has_tool_return(messages):
            yield final_text
            return
        yield {0: DeltaToolCall(name="skill", json_args=json.dumps({"name": name}))}

    return FunctionModel(stream_function=stream_function)


def agent_delegate_then_finish(
    child_prompt: str, final_text: str, child_report: str
) -> FunctionModel:
    """A parent that spawns the ``agent`` subagent once, then finishes; the child finishes at once.

    Drives the subagent-delegation probe. One scripted model plays BOTH roles because the ``agent``
    tool re-enters the SAME agent for the child (ADR-0013 §6), so ``build_agent()`` hands this model
    to parent and child alike. The two are told apart by their available tools: the read-only Explore
    child's toolset never includes the ``agent`` tool (``prepare=`` narrows it, ADR-0013 §1), so when
    ``agent`` is absent from ``info.function_tools`` this is the child — it returns ``child_report``
    immediately (a real Explore run would read + report). As the parent it spawns the child once, then
    finishes with ``final_text`` on the resume leg.

    Provides BOTH a streamed and a non-streamed callback: the eval driver streams the parent turn
    (``request_stream``), but the ``agent`` tool spawns the child via a nested ``agent.run()`` that
    issues a non-streamed ``request`` — so a stream-only ``FunctionModel`` would trip its "must receive
    a `function`" assertion on the child leg. Both callbacks share the parent/child branch.
    """

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        tool_names = {tool.name for tool in info.function_tools}
        if "agent" not in tool_names:  # the narrowed Explore child — hand back its report at once
            yield child_report
            return
        if _last_request_has_tool_return(messages):
            yield final_text
            return
        yield {0: DeltaToolCall(name="agent", json_args=json.dumps({"prompt": child_prompt}))}

    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        tool_names = {tool.name for tool in info.function_tools}
        if "agent" not in tool_names:  # the non-streamed child leg (nested agent.run())
            return ModelResponse(parts=[TextPart(content=child_report)])
        if _last_request_has_tool_return(messages):
            return ModelResponse(parts=[TextPart(content=final_text)])
        return ModelResponse(parts=[ToolCallPart(tool_name="agent", args={"prompt": child_prompt})])

    return FunctionModel(function, stream_function=stream_function)


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


def constant_text(text: str) -> FunctionModel:
    """A model that always answers ``text`` — on BOTH the streamed turn leg and a non-streamed call.

    The compaction-survival probe uses one model for two roles: it answers the recall prompt (the turn
    leg, which the eval driver STREAMS) and, when the driver wires compaction, it is also the summarizer
    the LLM compaction tier invokes via ``agent.run()`` (a NON-streamed request). A stream-only
    ``FunctionModel`` would trip the "must receive a `function`" assertion on that summarizer call, so
    this provides both callbacks, each yielding the same ``text``.
    """

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        yield text

    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=text)])

    return FunctionModel(function, stream_function=stream_function)


def crashing_model(message: str = "scripted model boom") -> FunctionModel:
    """A model that raises on its first request — the Runner swallows it into an ``AgentError``.

    Proves the driver surfaces a crashed turn: the :class:`~decode.harness.runner.Runner` catches the
    exception, emits ``events.AgentError``, and the run ends with an empty-but-valid history — the very
    ambiguity the eval driver's ``agent_error`` capture resolves (ADR-0017 §4; task 106).
    """

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        raise RuntimeError(message)
        yield ""  # unreachable; makes this a valid async generator

    return FunctionModel(stream_function=stream_function)
