"""Unit tests for :mod:`decode.agent.loop` — the real Pydantic AI turn handler (ADR-0002 §1-4).

Covers the streamed-node → event mapping, the ``MODEL_REQUEST`` / ``WOULD_STOP`` boundaries,
steering drained into ``message_history``, the deferred-tool permission legs (approve / deny /
resume), ``ask_user``, session-log persistence, and the two-tier auto-compaction cascade.
No network: the agent is built once with a dummy key and every test swaps in a ``TestModel`` /
``FunctionModel`` via ``agent.override(model=...)``.
"""

import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import SecretStr
from pydantic_ai import Agent, ApprovalRequired, ModelRetry, RunContext, UnexpectedModelBehavior
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage, RunUsage
from support.noop_helper import register_noop

from decode.agent import loop
from decode.agent.deps import AgentDeps, PermissionResolver, UserQuestionResolver
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.context import compaction, session_log
from decode.context.session_log import SessionLog
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import Boundary, Runner, TurnContext
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode, ToolKind
from decode.tools.askuser import NoInteractiveUserError
from decode.tui.app import InputIntent


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    """Default resolver for tests that don't exercise the gate: always deny."""
    return PermissionDecision.deny(reason="test default deny")


async def _no_user_resolver(question: str) -> str:
    """Default ask_user resolver for tests that don't exercise it: raise (no interactive user)."""
    raise NoInteractiveUserError("no interactive user in this test")


def _deps(
    emit,
    *,
    resolve_permission: PermissionResolver = _deny_resolver,
    resolve_user_question: UserQuestionResolver = _no_user_resolver,
    gate: PermissionGate | None = None,
) -> AgentDeps:
    """Build AgentDeps with the gate + resolvers wired (defaults to deny / no user).

    ``gate`` lets a test pick the active :class:`~decode.permissions.gate.PermissionGate` mode
    (default ``DEFAULT``) so it can drive auto-allow / auto-deny through the real loop.
    """
    return AgentDeps(
        cwd=Path("."),
        emit=emit,
        gate=gate or PermissionGate(),
        resolve_permission=resolve_permission,
        resolve_user_question=resolve_user_question,
    )


@pytest.fixture
def agent(mocker):
    """A real `decode` agent built with a dummy key (never used: tests override the model)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    return build_agent()


@pytest.fixture
def gated_agent(agent):
    """The production agent plus the TEST-ONLY gated ``noop`` (support.noop_helper.register_noop).

    The permission/loop tests drive a minimal gated flow with the scaffolding ``noop`` tool,
    which is intentionally NOT in the production registry (task 016) and lives under
    ``tests/support``. Registering it here on the test agent keeps those tests independent of the
    production tool set.
    """
    register_noop(agent)
    return agent


def _stream_words(*words: str):
    """A FunctionModel stream that yields ``words`` as successive text deltas, then stops."""

    async def stream_function(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        for word in words:
            yield word

    return stream_function


def _drive_collecting(handler: AgentTurnHandler, ctx: TurnContext):
    """Drive a handler generator to completion, collecting the boundaries it yields.

    Mimics the runner: always drains nothing (sends ``[]`` back) at each boundary.
    """

    async def _run() -> list[Boundary]:
        boundaries: list[Boundary] = []
        agen = handler(ctx)
        boundary = await agen.asend(None)
        while True:
            boundaries.append(boundary)
            try:
                boundary = await agen.asend([])
            except StopAsyncIteration:
                break
        await agen.aclose()
        return boundaries

    return _run


def _ctx(turn_id: int, prompt: str, sink: list[events.Event]) -> TurnContext:
    return TurnContext(turn_id, prompt, sink.append)


async def test_streamed_text_becomes_assistant_text_deltas(agent):
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append))

    with agent.override(model=FunctionModel(stream_function=_stream_words("Hello, ", "world"))):
        boundaries = await _drive_collecting(handler, _ctx(0, "hi", emitted))()

    text = "".join(e.text for e in emitted if isinstance(e, events.AssistantTextDelta))
    assert text == "Hello, world"
    # The loop hits a model-request boundary and then a would-stop boundary.
    assert Boundary.MODEL_REQUEST in boundaries
    assert boundaries[-1] is Boundary.WOULD_STOP


async def test_first_boundary_is_model_request(agent):
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append))

    with agent.override(model=TestModel(call_tools=[], custom_output_text="ok")):
        boundaries = await _drive_collecting(handler, _ctx(0, "hi", emitted))()

    assert boundaries[0] is Boundary.MODEL_REQUEST


async def test_message_history_carries_across_turns(agent):
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append))

    with agent.override(model=TestModel(call_tools=[], custom_output_text="first")):
        await _drive_collecting(handler, _ctx(0, "turn one", emitted))()
    after_first = len(handler.message_history)
    assert after_first > 0  # the first turn populated history

    with agent.override(model=TestModel(call_tools=[], custom_output_text="second")):
        await _drive_collecting(handler, _ctx(1, "turn two", emitted))()

    # The second turn appended to the *same* history rather than starting fresh.
    assert len(handler.message_history) > after_first
    # The very first message is still the first turn's user prompt.
    first_request = handler.message_history[0]
    assert isinstance(first_request, ModelRequest)
    assert any("turn one" in str(getattr(p, "content", "")) for p in first_request.parts)


async def test_steering_is_appended_before_the_model_request(agent):
    """ADR-0002 §4: steering drained at the model-request boundary lands in the history.

    The handler receives the drained steering messages back from the runner *at* the
    model-request boundary and must append them as user messages to ``message_history``
    before issuing the request, so the model sees them on this leg.
    """
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append))

    captured: list[list[ModelMessage]] = []

    async def capturing_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        captured.append(list(messages))
        yield "done"

    async def _run() -> None:
        agen = handler(_ctx(0, "please do X", emitted))
        boundary = await agen.asend(None)
        assert boundary is Boundary.MODEL_REQUEST
        # The runner injects steering at the model-request boundary, then drains nothing
        # at the remaining boundaries until the handler finishes.
        with contextlib.suppress(StopAsyncIteration):
            await agen.asend(["actually do Y"])
            while True:
                await agen.asend([])
        await agen.aclose()

    with agent.override(model=FunctionModel(stream_function=capturing_stream)):
        await _run()

    # The steering message reached the model on the request it preceded.
    flat = " ".join(
        str(getattr(p, "content", "")) for msgs in captured for m in msgs for p in m.parts
    )
    assert "actually do Y" in flat


async def test_handler_plugs_into_the_runner_end_to_end(agent):
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append))

    with agent.override(model=TestModel(call_tools=[], custom_output_text="all good")):
        runner = Runner(handler, on_event=emitted.append)
        await runner.submit("hello", InputIntent.STEER)
        await runner.wait_idle()

    kinds = [e.kind for e in emitted]
    assert kinds[0] == "turn_started"
    assert kinds[-1] == "turn_finished"
    assert "assistant_text_delta" in kinds


async def test_model_error_propagates_so_runner_surfaces_it(agent, mocker):
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append))

    async def boom_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        raise RuntimeError("model exploded")
        yield ""  # pragma: no cover - generator marker

    with agent.override(model=FunctionModel(stream_function=boom_stream)):
        runner = Runner(handler, on_event=emitted.append)
        await runner.submit("hello", InputIntent.STEER)
        await runner.wait_idle()

    errors = [e for e in emitted if isinstance(e, events.AgentError)]
    assert errors and "model exploded" in errors[-1].message


async def test_thinking_deltas_become_thinking_events(agent):
    from pydantic_ai.models.function import DeltaThinkingPart

    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append))

    async def thinking_stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        yield {0: DeltaThinkingPart(content="let me think")}
        yield "the answer"

    with agent.override(model=FunctionModel(stream_function=thinking_stream)):
        await _drive_collecting(handler, _ctx(0, "hi", emitted))()

    thinking = "".join(e.text for e in emitted if isinstance(e, events.ThinkingDelta))
    answer = "".join(e.text for e in emitted if isinstance(e, events.AssistantTextDelta))
    assert "let me think" in thinking
    assert "the answer" in answer


# task 016: tool-call panels (ToolCallStarted / ToolResult) emitted from the loop


async def test_tool_call_emits_started_and_result_events_and_renders_a_panel(agent):
    """ADR-0002 §6: a tool-calling turn emits ToolCallStarted + ToolResult and renders a panel.

    A non-gated tool call streams through the call-tools node; the loop must emit a
    ``ToolCallStarted`` (name + args summary) when the call begins and a ``ToolResult`` (the
    tool's output) when it returns, so the live REPL renders a tool panel on completion. The
    rendered ``ToolResult`` is the bordered Rich panel the user actually sees.
    """
    from rich.console import Console
    from rich.panel import Panel

    from decode.tui.render import render_event

    @agent.tool_plain
    def echo_tool(text: str) -> str:
        return f"echo: {text}"

    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append))

    with agent.override(model=TestModel(call_tools=["echo_tool"], custom_output_text="done")):
        await _drive_collecting(handler, _ctx(0, "use the tool", emitted))()

    started = [e for e in emitted if isinstance(e, events.ToolCallStarted)]
    results = [e for e in emitted if isinstance(e, events.ToolResult)]
    assert len(started) == 1, "the tool call must be announced exactly once"
    assert started[0].name == "echo_tool"
    assert "text" in started[0].args  # the args summary carries the call arguments
    assert len(results) == 1, "the tool result must be emitted exactly once"
    assert results[0].name == "echo_tool"
    assert results[0].ok is True
    assert "echo:" in results[0].output
    # The started and result events correlate by tool_call_id.
    assert started[0].tool_call_id == results[0].tool_call_id

    # The ToolResult renders as the bordered Rich panel the user sees in the REPL.
    panel = render_event(results[0])
    assert isinstance(panel, Panel)
    buf = Console(width=80, file=__import__("io").StringIO())
    buf.print(panel)
    rendered = buf.file.getvalue()
    assert "echo_tool" in rendered  # the panel title names the tool
    assert "echo:" in rendered  # the panel body carries the tool output


async def test_gated_tool_is_announced_once_across_the_deferred_resume(gated_agent):
    """A gated call is replayed on the resume leg, but ToolCallStarted is emitted only once.

    The deferred-pause leg streams the call event; the approved resume leg streams it again
    plus the result. The loop dedupes the started event per ``tool_call_id`` so the user sees a
    single announce and a single result panel (no flicker, ADR-0002 §6).
    """
    emitted: list[events.Event] = []

    async def approving_resolver(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow()

    handler = AgentTurnHandler(
        gated_agent, deps=_deps(emitted.append, resolve_permission=approving_resolver)
    )

    with gated_agent.override(model=_noop_then_text(final_text="finished")):
        await _drive_collecting(handler, _ctx(0, "please noop", emitted))()

    started = [e for e in emitted if isinstance(e, events.ToolCallStarted) and e.name == "noop"]
    results = [e for e in emitted if isinstance(e, events.ToolResult) and e.name == "noop"]
    assert len(started) == 1, "a gated call must be announced exactly once across both legs"
    assert len(results) == 1, "the gated call's result lands once on the resume leg"
    assert results[0].ok is True
    assert "noop: hi" in results[0].output


# task 005: the permission gate via the deferred-tool flow


def _noop_then_text(
    final_text: str = "all done", *, captured: list[list[ModelMessage]] | None = None
):
    """A streaming FunctionModel that calls ``noop`` on the first leg, then returns text.

    The loop streams every model node, so the model must stream. First model request →
    a streamed ``noop`` tool call (which raises ``ApprovalRequired`` until the call is
    approved, so the leg resolves to ``DeferredToolRequests``). Every later request (the
    approved/denied resume leg) → streamed text, so the turn terminates. If ``captured`` is
    given, each leg's incoming messages are recorded into it (for steering/denial assertions).
    """
    state = {"calls": 0}

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        state["calls"] += 1
        if captured is not None:
            captured.append(list(messages))
        if state["calls"] == 1:
            yield {0: DeltaToolCall(name="noop", json_args='{"text": "hi"}')}
        else:
            yield final_text

    return FunctionModel(stream_function=stream_function)


def _user_messages_seen(messages: list[ModelMessage]) -> list[str]:
    """Every user-prompt content string in a message list (for steering assertions)."""
    seen: list[str] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    seen.append(str(part.content))
    return seen


async def test_gated_tool_pauses_and_emits_permission_requested(gated_agent):
    emitted: list[events.Event] = []
    asked: list[PermissionRequest] = []

    async def approving_resolver(request: PermissionRequest) -> PermissionDecision:
        asked.append(request)
        return PermissionDecision.allow()

    handler = AgentTurnHandler(
        gated_agent, deps=_deps(emitted.append, resolve_permission=approving_resolver)
    )

    with gated_agent.override(model=_noop_then_text()):
        await _drive_collecting(handler, _ctx(0, "please noop", emitted))()

    perms = [e for e in emitted if isinstance(e, events.PermissionRequested)]
    assert perms, "a gated tool must emit a PermissionRequested event"
    assert perms[0].name == "noop"
    # The gate's ASK was routed to the resolver with a faithful request.
    assert asked and asked[0].tool_name == "noop"
    assert asked[0].kind is ToolKind.OTHER
    assert asked[0].tool_call_id == perms[0].tool_call_id


async def test_approval_resumes_and_executes_the_tool(gated_agent):
    emitted: list[events.Event] = []

    async def approving_resolver(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow()

    handler = AgentTurnHandler(
        gated_agent, deps=_deps(emitted.append, resolve_permission=approving_resolver)
    )

    with gated_agent.override(model=_noop_then_text(final_text="finished after approve")):
        await _drive_collecting(handler, _ctx(0, "please noop", emitted))()

    # The tool actually ran: its echoed return is in history as a ToolReturnPart.
    returns = [
        p.content
        for m in handler.message_history
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert "noop: hi" in returns
    # The turn terminated with plain assistant text (output is a str, not deferred).
    answer = "".join(e.text for e in emitted if isinstance(e, events.AssistantTextDelta))
    assert "finished after approve" in answer


async def test_denial_feeds_a_tooldenied_result_back_to_the_model(gated_agent):
    emitted: list[events.Event] = []

    async def denying_resolver(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.deny(reason="nope, not allowed")

    captured: list[list[ModelMessage]] = []

    handler = AgentTurnHandler(
        gated_agent, deps=_deps(emitted.append, resolve_permission=denying_resolver)
    )

    with gated_agent.override(
        model=_noop_then_text(final_text="ok, understood", captured=captured)
    ):
        await _drive_collecting(handler, _ctx(0, "please noop", emitted))()

    # The denial message reached the model on the resume leg as a tool result.
    resume_leg = captured[-1]
    denial_returns = [
        str(p.content)
        for m in resume_leg
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert any("nope, not allowed" in d for d in denial_returns)
    # The tool did NOT execute: its echo is never returned.
    assert not any("noop: hi" in d for d in denial_returns)


async def test_denied_tool_emits_a_failed_result_and_renders_a_red_panel(gated_agent):
    """A DENY must surface as ToolResult(ok=False) and render the RED "(failed)" panel.

    pydantic-ai returns a gate deny as a ``ToolReturnPart`` with ``outcome == "denied"`` (content
    = the denial reason), NOT a ``RetryPromptPart``. The loop must key ``ok`` off ``outcome`` so a
    denial does not masquerade as a green success panel (events.py: "ok is False … or was
    denied"; render.py: ok=False → red "(failed)" panel). This is the regression gap the existing
    message-history-only denial test never covered.
    """
    from rich.console import Console
    from rich.panel import Panel

    from decode.tui.render import render_event

    emitted: list[events.Event] = []

    async def denying_resolver(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.deny(reason="nope, not allowed")

    handler = AgentTurnHandler(
        gated_agent, deps=_deps(emitted.append, resolve_permission=denying_resolver)
    )

    with gated_agent.override(model=_noop_then_text(final_text="ok, understood")):
        await _drive_collecting(handler, _ctx(0, "please noop", emitted))()

    # The denied tool emits exactly one ToolResult, and it is NOT ok.
    results = [e for e in emitted if isinstance(e, events.ToolResult) and e.name == "noop"]
    assert len(results) == 1, "the denied gated call still emits a single ToolResult"
    assert results[0].ok is False, "a denied tool must emit ToolResult(ok=False), not a success"
    # The tool body never ran, so its echo is absent from the rendered outcome.
    assert "noop: hi" not in results[0].output

    # render_event of that result is the RED "(failed)" panel the user must see for a denial.
    panel = render_event(results[0])
    assert isinstance(panel, Panel)
    assert panel.border_style == "red"  # denial → red border (not green success)
    buf = Console(width=80, file=__import__("io").StringIO())
    buf.print(panel)
    rendered = buf.file.getvalue()
    assert "noop (failed)" in rendered  # the title flags the failure, not a bare success title


async def test_steering_message_reaches_the_model_at_the_deferred_resume(gated_agent):
    """Closes task 004's carryover: steering appended at a real deferred resume is seen.

    A gated call pauses the run; before the resume leg the runner drains steering at the
    model-request boundary. That steering must be appended to history as a user message so
    the model sees it on the resume leg (ADR-0002 §4).
    """
    emitted: list[events.Event] = []

    async def approving_resolver(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow()

    captured: list[list[ModelMessage]] = []

    handler = AgentTurnHandler(
        gated_agent, deps=_deps(emitted.append, resolve_permission=approving_resolver)
    )

    async def _run() -> None:
        agen = handler(_ctx(0, "please noop", emitted))
        boundary = await agen.asend(None)
        # Leg 1: model-request boundary, drains nothing, runs the gated leg (pauses).
        assert boundary is Boundary.MODEL_REQUEST
        # The deferred resume goes back through a MODEL_REQUEST boundary; inject steering
        # there, exactly as the runner would after draining its steering queue.
        sent: list[str] = []
        with contextlib.suppress(StopAsyncIteration):
            while True:
                boundary = await agen.asend(sent)
                sent = ["STEER-AT-RESUME-123"] if boundary is Boundary.MODEL_REQUEST else []
        await agen.aclose()

    with gated_agent.override(model=_noop_then_text(final_text="done", captured=captured)):
        await _run()

    # The steering message reached the model on the resume leg (the second model request).
    assert len(captured) >= 2, "expected a resume leg after the deferred pause"
    resume_user_messages = _user_messages_seen(captured[-1])
    assert any("STEER-AT-RESUME-123" in m for m in resume_user_messages)


async def test_single_flight_lock_spans_the_whole_multi_leg_deferred_turn(gated_agent):
    """ADR-0002 §4: the single-flight lock spans the full deferred (multi-leg) turn.

    Driven through the *real* Runner: a gated tool makes the turn fragment into a request
    leg + a deferred resume leg. While the turn is in flight a second submit must enqueue
    (steering), never start a parallel turn; the lock holds until the resume leg finishes.
    """
    emitted: list[events.Event] = []

    async def approving_resolver(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow()

    handler = AgentTurnHandler(
        gated_agent, deps=_deps(emitted.append, resolve_permission=approving_resolver)
    )

    with gated_agent.override(model=_noop_then_text(final_text="multi-leg done")):
        runner = Runner(handler, on_event=emitted.append)
        await runner.submit("please noop", InputIntent.STEER)
        # Exactly one turn in flight across the whole multi-leg (deferred) turn.
        assert runner.active_turns == 1
        await runner.wait_idle()

    kinds = [e.kind for e in emitted]
    assert kinds[0] == "turn_started"
    assert "permission_requested" in kinds
    assert kinds[-1] == "turn_finished"
    answer = "".join(e.text for e in emitted if isinstance(e, events.AssistantTextDelta))
    assert "multi-leg done" in answer


# task 017: the gate verdict is honored in the loop (auto-allow / auto-deny / ask)


async def test_auto_allow_runs_a_read_only_tool_without_prompting(agent, tmp_path):
    """ADR-0003 §3: under DEFAULT mode a read-only tool auto-ALLOWs — no prompt, no event.

    Driven through the real ``read`` tool (READ_ONLY in the registry) against a real file. The
    resolver is a *guard* that fails the test if it is ever called, and ``PermissionRequested``
    must never be emitted: an auto-allow runs the tool with neither.
    """
    (tmp_path / "data.txt").write_text("AUTO-ALLOWED-CONTENT", encoding="utf-8")

    emitted: list[events.Event] = []
    resolver_calls: list[PermissionRequest] = []

    async def guard_resolver(request: PermissionRequest) -> PermissionDecision:
        resolver_calls.append(request)  # pragma: no cover - must never run on an auto-allow
        return PermissionDecision.allow()

    deps = AgentDeps(
        cwd=tmp_path,
        emit=emitted.append,
        gate=PermissionGate(),  # DEFAULT mode
        resolve_permission=guard_resolver,
        resolve_user_question=_no_user_resolver,
    )
    handler = AgentTurnHandler(agent, deps=deps)

    with agent.override(model=_read_then_text("data.txt", final_text="read done")):
        await _drive_collecting(handler, _ctx(0, "read the file", emitted))()

    # The tool ran (its content is in history) but neither the resolver nor the event fired.
    returns = [
        str(p.content)
        for m in handler.message_history
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert any("AUTO-ALLOWED-CONTENT" in r for r in returns), "the read tool must have run"
    assert resolver_calls == [], "an auto-allowed call must NOT call resolve_permission"
    assert not [e for e in emitted if isinstance(e, events.PermissionRequested)], (
        "an auto-allowed call must NOT emit PermissionRequested"
    )


async def test_auto_deny_feeds_the_reason_back_without_prompting(gated_agent):
    """ADR-0003 §3: under PLAN mode a mutating tool auto-DENYs — reason to the model, no prompt.

    ``noop`` is an OTHER-kind gated tool; under PLAN it is denied by the gate with the
    exit_plan_mode reason. The loop must feed that reason back as the tool result (a model-visible
    ``ToolReturnPart``) without calling the resolver or emitting ``PermissionRequested``.
    """
    emitted: list[events.Event] = []
    resolver_calls: list[PermissionRequest] = []

    async def guard_resolver(request: PermissionRequest) -> PermissionDecision:
        resolver_calls.append(request)  # pragma: no cover - must never run on an auto-deny
        return PermissionDecision.allow()

    gate = PermissionGate()
    gate.set_mode(PermissionMode.PLAN)
    captured: list[list[ModelMessage]] = []
    handler = AgentTurnHandler(
        gated_agent,
        deps=_deps(emitted.append, resolve_permission=guard_resolver, gate=gate),
    )

    with gated_agent.override(model=_noop_then_text(final_text="understood", captured=captured)):
        await _drive_collecting(handler, _ctx(0, "please noop", emitted))()

    # The denial reason reached the model as a tool result on the resume leg...
    resume_leg = captured[-1]
    denial_returns = [
        str(p.content)
        for m in resume_leg
        if isinstance(m, ModelRequest)
        for p in m.parts
        if isinstance(p, ToolReturnPart)
    ]
    assert any("exit_plan_mode" in d for d in denial_returns), (
        "the deny reason must reach the model"
    )
    # ...the tool body never ran...
    assert not any("noop: hi" in d for d in denial_returns)
    # ...and neither the resolver nor the PermissionRequested event fired (auto-deny, no prompt).
    assert resolver_calls == [], "an auto-denied call must NOT call resolve_permission"
    assert not [e for e in emitted if isinstance(e, events.PermissionRequested)], (
        "an auto-denied call must NOT emit PermissionRequested"
    )


async def test_ask_still_prompts_for_a_mutating_tool_under_default(gated_agent):
    """ADR-0003 §1: under DEFAULT mode a mutating (OTHER) tool still ASKs the human.

    This is the unchanged path: ``noop`` is OTHER, DEFAULT mode → ASK → the resolver is called
    and a ``PermissionRequested`` event is emitted, exactly as in M1.
    """
    emitted: list[events.Event] = []
    asked: list[PermissionRequest] = []

    async def approving_resolver(request: PermissionRequest) -> PermissionDecision:
        asked.append(request)
        return PermissionDecision.allow()

    handler = AgentTurnHandler(
        gated_agent, deps=_deps(emitted.append, resolve_permission=approving_resolver)
    )

    with gated_agent.override(model=_noop_then_text(final_text="done")):
        await _drive_collecting(handler, _ctx(0, "please noop", emitted))()

    assert asked and asked[0].tool_name == "noop", "a mutating tool under DEFAULT must still ask"
    assert [e for e in emitted if isinstance(e, events.PermissionRequested)], (
        "an ASK must emit PermissionRequested"
    )


def _read_then_text(
    path: str, final_text: str = "all done", *, captured: list[list[ModelMessage]] | None = None
):
    """A streaming FunctionModel that calls ``read`` on the first leg, then returns text.

    ``read`` is READ_ONLY in the registry, so under DEFAULT mode it auto-allows: the loop runs it
    with no prompt. Every later request streams ``final_text`` so the turn terminates.
    """
    state = {"calls": 0}

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        state["calls"] += 1
        if captured is not None:
            captured.append(list(messages))
        if state["calls"] == 1:
            yield {0: DeltaToolCall(name="read", json_args=f'{{"path": "{path}"}}')}
        else:
            yield final_text

    return FunctionModel(stream_function=stream_function)


# task 011: the blocking ask_user tool (NOT gated; rides the decision channel)


def _ask_user_then_text(
    question: str, final_text: str = "thanks", *, captured: list[list[ModelMessage]] | None = None
):
    """A streaming FunctionModel that calls ``ask_user`` on the first leg, then returns text.

    First model request → a streamed ``ask_user`` tool call with ``question``. Unlike ``noop``,
    ``ask_user`` is NOT gated (it never raises ``ApprovalRequired``); it blocks the run inside
    the tool body on ``ctx.deps.resolve_user_question`` and returns the human's answer as the
    tool result. Every later request → streamed ``final_text`` so the turn terminates. If
    ``captured`` is given, each leg's incoming messages are recorded (to inspect the tool result).
    """
    state = {"calls": 0}

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        state["calls"] += 1
        if captured is not None:
            captured.append(list(messages))
        if state["calls"] == 1:
            yield {0: DeltaToolCall(name="ask_user", json_args=f'{{"question": "{question}"}}')}
        else:
            yield final_text

    return FunctionModel(stream_function=stream_function)


async def test_ask_user_answer_reaches_the_model_as_the_tool_result(agent):
    """ADR-0002 §2,7: a forced ask_user call surfaces the question and the typed answer.

    A fake ``resolve_user_question`` supplies the answer; it must (a) be emitted as an
    ``AskUserRequested`` event so the TUI renders the question, and (b) come back to the model
    as the ``ask_user`` tool result on the next leg. ask_user is NOT gated, so there is no
    ``PermissionRequested`` event for it.
    """
    emitted: list[events.Event] = []
    asked: list[str] = []

    async def answering_resolver(question: str) -> str:
        asked.append(question)
        return "use the staging database"

    captured: list[list[ModelMessage]] = []
    handler = AgentTurnHandler(
        agent, deps=_deps(emitted.append, resolve_user_question=answering_resolver)
    )

    model = _ask_user_then_text("which database?", final_text="ok", captured=captured)
    with agent.override(model=model):
        await _drive_collecting(handler, _ctx(0, "set up the db", emitted))()

    # The question was surfaced to the TUI as an AskUserRequested event.
    asks = [e for e in emitted if isinstance(e, events.AskUserRequested)]
    assert asks and asks[0].question == "which database?"
    assert asked == ["which database?"]  # the tool routed the question to the resolver

    # ask_user is NOT gated: no PermissionRequested event was emitted for it.
    assert not [e for e in emitted if isinstance(e, events.PermissionRequested)]

    # The typed answer reached the model as the ask_user tool result on the resume leg.
    resume_leg = captured[-1]
    returns = [
        str(part.content)
        for message in resume_leg
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert any("use the staging database" in r for r in returns)


async def test_ask_user_model_retries_headless_and_the_turn_still_finishes(agent):
    """Headless (no interactive user): ask_user ModelRetries, the model recovers, turn ends.

    With the headless resolver the tool raises a ``ModelRetry`` ("no interactive user");
    Pydantic AI feeds that back to the model, which then answers with plain text. The turn must
    finish cleanly (never hang) — the headless-safety guarantee.
    """
    emitted: list[events.Event] = []

    async def headless_resolver(question: str) -> str:
        raise NoInteractiveUserError("no interactive user is attached")

    handler = AgentTurnHandler(
        agent, deps=_deps(emitted.append, resolve_user_question=headless_resolver)
    )

    model = _ask_user_then_text("are you there?", final_text="proceeding without you")
    with agent.override(model=model):
        runner = Runner(handler, on_event=emitted.append)
        await runner.submit("do the thing", InputIntent.STEER)
        await runner.wait_idle()

    kinds = [e.kind for e in emitted]
    assert kinds[0] == "turn_started"
    assert kinds[-1] == "turn_finished"  # the turn finished, no hang
    answer = "".join(e.text for e in emitted if isinstance(e, events.AssistantTextDelta))
    assert "proceeding without you" in answer


# task 014: session-log persistence (append each turn's new messages)


async def test_handler_persists_each_turn_to_the_session_log(agent, tmp_path):
    """ADR-0002 §9: when a SessionLog is wired, each turn appends its new messages.

    The handler is given a real :class:`~decode.context.session_log.SessionLog`; after a turn
    finishes, the session file must carry that turn's messages so a later ``--resume`` replays
    them. Two turns produce a replay equal to the handler's accumulated ``message_history``.
    """
    from uuid import UUID

    from decode.context import session_log
    from decode.context.session_log import SessionLog

    emitted: list[events.Event] = []
    log = SessionLog.create(
        tmp_path,
        cwd=tmp_path,
        now=__import__("datetime").datetime(2026, 6, 19, 12, 0, tzinfo=__import__("datetime").UTC),
        session_id=UUID("00000000-0000-0000-0000-0000000000aa"),
    )
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append), session_log=log)

    with agent.override(model=TestModel(call_tools=[], custom_output_text="first")):
        await _drive_collecting(handler, _ctx(0, "turn one", emitted))()
    with agent.override(model=TestModel(call_tools=[], custom_output_text="second")):
        await _drive_collecting(handler, _ctx(1, "turn two", emitted))()

    # The replayed history equals what the handler accumulated across both turns.
    replayed = session_log.load(log.path)
    assert replayed == handler.message_history


async def test_handler_works_without_a_session_log(agent):
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append))  # no session_log

    with agent.override(model=TestModel(call_tools=[], custom_output_text="ok")):
        await _drive_collecting(handler, _ctx(0, "hi", emitted))()

    assert handler.message_history  # the turn still populated history


async def test_session_log_persists_only_new_messages_per_turn(agent, tmp_path):
    """Each appended batch is that turn's *new* messages, not the whole history re-dumped.

    The file is append-only and a turn's line carries only the messages added on that turn, so
    the second turn's batch does not contain the first turn's user prompt.
    """
    import json
    from uuid import UUID

    from decode.context.session_log import SessionLog

    emitted: list[events.Event] = []
    log = SessionLog.create(
        tmp_path,
        cwd=tmp_path,
        now=__import__("datetime").datetime(2026, 6, 19, 12, 0, tzinfo=__import__("datetime").UTC),
        session_id=UUID("00000000-0000-0000-0000-0000000000bb"),
    )
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append), session_log=log)

    with agent.override(model=TestModel(call_tools=[], custom_output_text="one")):
        await _drive_collecting(handler, _ctx(0, "ALPHA-PROMPT", emitted))()
    with agent.override(model=TestModel(call_tools=[], custom_output_text="two")):
        await _drive_collecting(handler, _ctx(1, "BETA-PROMPT", emitted))()

    lines = [ln for ln in log.path.read_text(encoding="utf-8").splitlines() if ln]
    assert len(lines) == 3  # header + two turn batches
    second_turn_raw = json.dumps(json.loads(lines[2]))
    assert "BETA-PROMPT" in second_turn_raw
    assert "ALPHA-PROMPT" not in second_turn_raw  # only this turn's new messages


# task 044: the window-relative two-tier auto-compaction cascade
#
# Driven through the real loop with FunctionModel/TestModel — no network. A streaming
# FunctionModel reports a FIXED ``input_tokens`` of 50 (pydantic-ai's FunctionStreamedResponse
# estimates the request from an empty message list in ``__post_init__``), so patching the window
# alone lands the measured usage in a chosen tier band deterministically:
#   * window=60 → full level int(60*0.80)=48 ≤ 50           → full compaction fires.
#   * window=70 → full level int(70*0.80)=56 > 50, micro level int(70*0.60)=42 ≤ 50 → micro only.
#   * window=200 → micro level int(200*0.60)=120 > 50       → neither tier fires.
# The recent-tail cut is forced with a HUGE driven prompt (>> keep_recent_tokens), so the kept
# tail is exactly the final turn and everything earlier is "old" — no fragile token arithmetic.

# pydantic-ai's streaming FunctionModel always reports this fixed per-leg input-token estimate.
_FUNCTION_MODEL_INPUT_TOKENS = 50
# A prompt far larger than the patched keep_recent budget, so split_tail's kept tail is just the
# final turn (the snap-back boundary) and every earlier message is "old".
_HUGE_PROMPT = "keep working on the task " * 100
# The skeleton the summarizer FunctionModel returns; build_summary_message frames it as the head.
_FAKE_SKELETON = "# Conversation summary\n\n## Goal\nCOMPACTED-SUMMARY-MARKER\n"


def _user_msg(text: str) -> ModelRequest:
    """A user-turn request (a turn boundary split_tail can snap to)."""
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant_msg(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _tool_call_msg(name: str, call_id: str) -> ModelResponse:
    """An assistant response issuing one tool call (pairs with a tool-return below)."""
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args="{}", tool_call_id=call_id)])


def _tool_return_msg(name: str, call_id: str, content: str) -> ModelRequest:
    """A request returning one tool result — the part microcompaction blanks."""
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=name, content=content, tool_call_id=call_id)]
    )


def _text_model(*words: str) -> FunctionModel:
    """A streaming FunctionModel that yields ``words`` then stops (input_tokens fixed at 50)."""
    return FunctionModel(stream_function=_stream_words(*words))


def _skeleton_summarizer() -> FunctionModel:
    """A non-streaming FunctionModel that returns the fixed skeleton (the summarizer leg)."""

    async def fill(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=_FAKE_SKELETON)])

    return FunctionModel(fill)


def _raising_summarizer() -> FunctionModel:
    """A summarizer Model whose call raises — the no-network seam for a failed summarizer leg.

    ``summarize_for_compaction`` catches the exception and returns ``None``, so ``compact()`` maps
    it to ``CompactOutcome.SUMMARIZER_FAILED`` (ADR-0018 §3).
    """

    async def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("summarizer offline")

    return FunctionModel(boom)


def _fresh_log(tmp_path: Path, tag: str) -> SessionLog:
    """A deterministic SessionLog under ``tmp_path`` (fixed clock + id for stable filenames)."""
    return SessionLog.create(
        tmp_path,
        cwd=tmp_path,
        now=datetime(2026, 6, 26, 12, 0, tzinfo=UTC),
        session_id=UUID(f"00000000-0000-0000-0000-0000000000{tag}"),
    )


def _tool_return_contents(history: list[ModelMessage]) -> list[str]:
    """Every ToolReturnPart content string in ``history`` (to inspect blanking)."""
    return [
        str(part.content)
        for message in history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def _log_line_types(log: SessionLog) -> list[str]:
    """The ``type`` discriminant of every JSONL line in the session log."""
    import json

    types: list[str] = []
    for line in log.path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            types.append(json.loads(line).get("type"))
    return types


async def test_run_leg_captures_input_tokens_and_property_exposes_it(agent):
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append))

    # Default before any leg is 0 (the safe "don't fire" usage fallback).
    assert handler.last_input_tokens == 0

    with agent.override(model=_text_model("hello", " world")):
        await _drive_collecting(handler, _ctx(0, "a prompt with a few words", emitted))()

    # A non-zero capture, exposed through the public property (the gauge's clean read).
    assert handler.last_input_tokens > 0
    assert handler.last_input_tokens == handler._last_input_tokens


# task 126 (ADR-0018 §2): the gauge reads the LAST ModelResponse's per-request usage, not the
# CUMULATIVE RunUsage summed across every tool round. The streaming FunctionModel estimates a
# fixed per-request input, so exact per-response usages are driven through a stubbed run — the
# ``or a stubbed run`` seam the task calls out — while the helper is also unit-tested directly.


class _StubRun:
    """A minimal stand-in for a pydantic-ai ``agent.iter`` run with pre-set messages + usage.

    ``all_messages()`` returns the per-response ``ModelResponse``s (each with its own usage) and
    ``usage()`` returns the CUMULATIVE ``RunUsage`` (the old, wrong source) — so a test can assert
    the handler reads the former, not the latter. Yields no nodes: the streaming loop is a no-op.
    """

    def __init__(self, messages: list[ModelMessage], cumulative_input: int) -> None:
        self._messages = messages
        self._cumulative_input = cumulative_input
        self.result = SimpleNamespace(output="done", new_messages=lambda: list(messages))

    def all_messages(self) -> list[ModelMessage]:
        return list(self._messages)

    def usage(self) -> RunUsage:
        return RunUsage(input_tokens=self._cumulative_input)

    def __aiter__(self) -> "_StubRun":
        return self

    async def __anext__(self) -> object:
        raise StopAsyncIteration


class _StubIterCM:
    """The async context manager ``agent.iter(...)`` returns, wrapping a :class:`_StubRun`."""

    def __init__(self, run: _StubRun) -> None:
        self._run = run

    async def __aenter__(self) -> _StubRun:
        return self._run

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _drive_stub_leg(
    agent: Agent, mocker, messages: list[ModelMessage], *, cumulative_input: int
) -> AgentTurnHandler:
    """Run ONE leg over a stubbed run with the given per-response ``messages`` + cumulative usage."""
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append))
    run = _StubRun(messages, cumulative_input)
    mocker.patch.object(handler._agent, "iter", return_value=_StubIterCM(run))
    return handler


async def test_leg_gauge_reads_last_response_not_cumulative_usage(agent, mocker):
    """Regression (ADR-0018 §2): 3 responses at 100/220/350 → gauge is 350, not 670 cumulative."""
    messages: list[ModelMessage] = [
        _user_msg("go"),
        ModelResponse(parts=[TextPart(content="a")], usage=RequestUsage(input_tokens=100)),
        ModelResponse(parts=[TextPart(content="b")], usage=RequestUsage(input_tokens=220)),
        ModelResponse(parts=[TextPart(content="c")], usage=RequestUsage(input_tokens=350)),
    ]
    handler = _drive_stub_leg(agent, mocker, messages, cumulative_input=670)

    await handler._run_leg(_ctx(0, "go", []), prompt="go")

    # The last response's own request usage — NOT 100+220+350 = 670 (the cumulative RunUsage).
    assert handler.last_input_tokens == 350


def test_leg_input_tokens_last_populated_response_wins():
    """A later UNPOPULATED (default) usage does not clobber the last populated one."""
    messages: list[ModelMessage] = [
        _user_msg("first"),
        ModelResponse(parts=[TextPart(content="a")], usage=RequestUsage(input_tokens=100)),
        ModelResponse(parts=[TextPart(content="b")], usage=RequestUsage(input_tokens=350)),
        ModelResponse(parts=[TextPart(content="c")]),  # default usage → input_tokens == 0
    ]

    assert loop._leg_input_tokens(messages) == 350


def test_leg_input_tokens_adds_cache_read_tokens():
    """Cached prompt tokens still occupy context: input_tokens + cache_read_tokens."""
    messages: list[ModelMessage] = [
        _user_msg("first"),
        ModelResponse(
            parts=[TextPart(content="a")],
            usage=RequestUsage(input_tokens=300, cache_read_tokens=50),
        ),
    ]

    assert loop._leg_input_tokens(messages) == 350


def test_leg_input_tokens_all_unpopulated_is_zero():
    """No ModelResponse with populated usage anywhere → 0 (ADR-0006 §3 safe fallback)."""
    messages: list[ModelMessage] = [
        _user_msg("first"),
        ModelResponse(parts=[TextPart(content="a")]),  # default usage
        _user_msg("second"),
        ModelResponse(parts=[TextPart(content="b")]),  # default usage
    ]

    assert loop._leg_input_tokens(messages) == 0


async def test_all_unpopulated_usage_leg_gauges_zero_and_never_compacts(agent, mocker):
    """A whole leg of unpopulated usages → gauge 0 → the cascade fires nothing, even at a tiny window."""
    mocker.patch.object(loop.settings, "compaction_context_window_tokens", 1)  # would fire if > 0
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)

    messages: list[ModelMessage] = [
        _user_msg("go"),
        ModelResponse(parts=[TextPart(content="a")]),  # default usage
        ModelResponse(parts=[TextPart(content="b")]),  # default usage
    ]
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        compaction_model=_skeleton_summarizer(),
    )
    run = _StubRun(messages, cumulative_input=100)  # cumulative is non-zero; per-response is 0
    mocker.patch.object(handler._agent, "iter", return_value=_StubIterCM(run))

    await handler._run_leg(_ctx(0, "go", emitted), prompt="go")
    assert handler.last_input_tokens == 0
    await handler._maybe_auto_compact()

    assert not [
        e for e in emitted if isinstance(e, events.ContextCompacted | events.ContextMicrocompacted)
    ]


async def test_full_tier_compacts_through_the_turn(agent, tmp_path, mocker):
    mocker.patch.object(loop.settings, "compaction_context_window_tokens", 60)  # full band
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)
    log = _fresh_log(tmp_path, "01")

    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        session_log=log,
        message_history=[_user_msg("first"), _assistant_msg("first answer")],
        compaction_model=_skeleton_summarizer(),
    )

    with agent.override(model=_text_model("ok")):
        await _drive_collecting(handler, _ctx(0, _HUGE_PROMPT, emitted))()

    # History is replaced with [summary_message, *tail]; the head frames the skeleton.
    head = handler.message_history[0]
    assert isinstance(head, ModelRequest)
    head_text = "".join(str(getattr(p, "content", "")) for p in head.parts)
    assert "Summary of the earlier conversation" in head_text
    assert "COMPACTED-SUMMARY-MARKER" in head_text
    # The cursor is reset to the compacted length so the next turn re-persists nothing.
    assert handler._persisted_count == len(handler.message_history)
    # A compaction checkpoint line was written (full compaction persists, ADR-0006 §6).
    assert "compaction" in _log_line_types(log)
    # The ContextCompacted event carries the pre-compaction tokens and the kept-message count.
    compacted = [e for e in emitted if isinstance(e, events.ContextCompacted)]
    assert len(compacted) == 1
    assert compacted[0].before_tokens == _FUNCTION_MODEL_INPUT_TOKENS
    assert compacted[0].kept_messages == len(handler.message_history) - 1


async def test_middle_tier_microcompacts_through_the_turn(agent, tmp_path, mocker):
    mocker.patch.object(loop.settings, "compaction_context_window_tokens", 70)  # micro band
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)
    log = _fresh_log(tmp_path, "02")

    seed: list[ModelMessage] = [
        _user_msg("kickoff"),
        _tool_call_msg("read", "c1"),
        _tool_return_msg("read", "c1", "ORIGINAL-TOOL-BODY " + "z " * 200),
        _assistant_msg("first answer"),
        _user_msg("second"),
        _assistant_msg("second answer"),
    ]
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        session_log=log,
        message_history=list(seed),
        compaction_model=_skeleton_summarizer(),
    )
    count_before = len(seed) + 2  # the driven turn adds a user prompt + an assistant response

    with agent.override(model=_text_model("ok")):
        await _drive_collecting(handler, _ctx(0, _HUGE_PROMPT, emitted))()

    # The old tool-output body was blanked IN MEMORY; the message count is unchanged.
    assert len(handler.message_history) == count_before
    assert compaction._MICRO_PLACEHOLDER in _tool_return_contents(handler.message_history)
    assert not any(
        "ORIGINAL-TOOL-BODY" in c for c in _tool_return_contents(handler.message_history)
    )
    # Microcompaction does NOT write a compaction line, and leaves the persisted cursor where the
    # turn's persist left it (== len: the elided messages were already persisted in full fidelity).
    assert "compaction" not in _log_line_types(log)
    assert handler._persisted_count == len(handler.message_history)
    # Exactly one ContextMicrocompacted, carrying the elided count + the pre-compaction tokens.
    micro = [e for e in emitted if isinstance(e, events.ContextMicrocompacted)]
    assert len(micro) == 1
    assert micro[0].elided_count == 1
    assert micro[0].before_tokens == _FUNCTION_MODEL_INPUT_TOKENS
    # No full compaction happened at this tier.
    assert not [e for e in emitted if isinstance(e, events.ContextCompacted)]


async def test_microcompaction_keeps_full_fidelity_on_disk(agent, tmp_path, mocker):
    marker = "PERSISTED-TOOL-BODY-MARKER"
    (tmp_path / "data.txt").write_text(marker + " and a few words to read", encoding="utf-8")
    mocker.patch.object(loop.settings, "compaction_context_window_tokens", 70)  # micro band
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 50)
    log = _fresh_log(tmp_path, "03")

    emitted: list[events.Event] = []
    deps = AgentDeps(
        cwd=tmp_path,
        emit=emitted.append,
        gate=PermissionGate(),  # DEFAULT mode → the read tool auto-allows, no prompt
        resolve_permission=_deny_resolver,
        resolve_user_question=_no_user_resolver,
    )
    handler = AgentTurnHandler(
        agent, deps=deps, session_log=log, compaction_model=_skeleton_summarizer()
    )

    # Turn 1: a read-tool turn persists the FULL tool output to the log (recent → micro no-op).
    with agent.override(model=_read_then_text("data.txt", final_text="read done")):
        await _drive_collecting(handler, _ctx(0, "read the file", emitted))()
    # Turn 2: a huge prompt pushes the tool output out of the recent tail → micro blanks it.
    with agent.override(model=_text_model("ok")):
        await _drive_collecting(handler, _ctx(1, _HUGE_PROMPT, emitted))()

    # In memory the tool output is blanked.
    assert [e for e in emitted if isinstance(e, events.ContextMicrocompacted)]
    assert compaction._MICRO_PLACEHOLDER in _tool_return_contents(handler.message_history)
    assert not any(marker in c for c in _tool_return_contents(handler.message_history))
    # On disk the log keeps the ORIGINAL full tool output and never the placeholder (assert bytes).
    raw = log.path.read_text(encoding="utf-8")
    assert marker in raw
    assert compaction._MICRO_PLACEHOLDER not in raw
    assert "compaction" not in _log_line_types(log)
    # Replay reconstructs the FULL history (original tool body, not the placeholder).
    replayed = session_log.load(log.path)
    assert any(marker in c for c in _tool_return_contents(replayed))
    assert compaction._MICRO_PLACEHOLDER not in _tool_return_contents(replayed)


async def test_no_repersist_after_full_compaction(agent, tmp_path, mocker):
    # A large window keeps the auto-cascade quiet; compaction is driven explicitly via compact().
    mocker.patch.object(loop.settings, "compaction_context_window_tokens", 10_000_000)
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)
    log = _fresh_log(tmp_path, "04")

    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        session_log=log,
        message_history=[
            _user_msg("OLDEST-TURN-MARKER first"),
            _assistant_msg("first answer"),
            _user_msg(_HUGE_PROMPT),
            _assistant_msg("recent answer"),
        ],
        compaction_model=_skeleton_summarizer(),
    )

    assert await handler.compact() is compaction.CompactOutcome.COMPACTED
    # The dropped oldest turn is gone from the running history.
    assert not any(
        "OLDEST-TURN-MARKER" in str(getattr(p, "content", ""))
        for m in handler.message_history
        if isinstance(m, ModelRequest)
        for p in m.parts
    )

    with agent.override(model=_text_model("ok")):
        await _drive_collecting(handler, _ctx(0, "NEXT-TURN-MARKER", emitted))()

    import json

    lines = [ln for ln in log.path.read_text(encoding="utf-8").splitlines() if ln]
    last_line_raw = json.dumps(json.loads(lines[-1]))
    assert json.loads(lines[-1])["type"] == "messages"  # a plain turn append, not a checkpoint
    assert "NEXT-TURN-MARKER" in last_line_raw  # only this turn's new messages
    assert "OLDEST-TURN-MARKER" not in last_line_raw  # the compacted prefix is not re-persisted


async def test_clear_wipes_history_writes_the_marker_and_resets_the_cursor(agent, tmp_path):
    """The ``/clear`` body: history → ``[]``, cursor/gauge → 0, one marker line, resume replays empty."""
    import json

    from decode.context import session_log as session_log_mod

    log = _fresh_log(tmp_path, "05")
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        session_log=log,
        message_history=[_user_msg("wiped-prompt"), _assistant_msg("wiped answer")],
    )
    handler._last_input_tokens = 1234  # a prior leg's measurement — the footer gauge source

    handler.clear()

    assert handler.message_history == []
    assert handler._persisted_count == 0
    assert handler.last_input_tokens == 0  # the footer gauge resets with the history
    lines = [ln for ln in log.path.read_text(encoding="utf-8").splitlines() if ln]
    assert json.loads(lines[-1])["type"] == "clear"  # the marker rode the append-only log
    assert session_log_mod.load(log.path) == []  # --resume replays to the post-clear state


async def test_clear_without_a_session_log_still_resets(agent):
    # The log is optional (headless/test handlers): clear() must reset without one, no raise.
    handler = AgentTurnHandler(agent, deps=_deps(lambda _e: None), message_history=[_user_msg("x")])

    handler.clear()

    assert handler.message_history == []
    assert handler._persisted_count == 0


async def test_below_both_tiers_is_a_no_op(agent, tmp_path, mocker):
    mocker.patch.object(loop.settings, "compaction_context_window_tokens", 200)  # below both
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)

    emitted: list[events.Event] = []
    seed: list[ModelMessage] = [
        _user_msg("kickoff"),
        _tool_return_msg("read", "c1", "ORIGINAL-TOOL-BODY " + "z " * 100),
        _user_msg("second"),
        _assistant_msg("second answer"),
    ]
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        message_history=list(seed),
        compaction_model=_skeleton_summarizer(),
    )

    with agent.override(model=_text_model("ok")):
        await _drive_collecting(handler, _ctx(0, _HUGE_PROMPT, emitted))()

    assert not [
        e for e in emitted if isinstance(e, events.ContextCompacted | events.ContextMicrocompacted)
    ]
    # The seeded tool output is untouched (no blanking).
    assert any("ORIGINAL-TOOL-BODY" in c for c in _tool_return_contents(handler.message_history))


async def test_disabled_flag_skips_the_cascade(agent, mocker):
    mocker.patch.object(loop.settings, "compaction_context_window_tokens", 60)  # full band
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)
    mocker.patch.object(loop.settings, "compaction_enabled", False)

    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        message_history=[_user_msg("first"), _assistant_msg("answer")],
        compaction_model=_skeleton_summarizer(),
    )

    with agent.override(model=_text_model("ok")):
        await _drive_collecting(handler, _ctx(0, _HUGE_PROMPT, emitted))()

    assert not [
        e for e in emitted if isinstance(e, events.ContextCompacted | events.ContextMicrocompacted)
    ]


async def test_zero_tokens_never_compacts(agent, mocker):
    mocker.patch.object(loop.settings, "compaction_context_window_tokens", 1)  # tiny → would fire
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)

    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        message_history=[_user_msg("first"), _assistant_msg("answer")],
        compaction_model=_skeleton_summarizer(),
    )

    # No leg has run, so last_input_tokens is 0; the cascade must not fire on a bogus zero.
    assert handler.last_input_tokens == 0
    await handler._maybe_auto_compact()

    assert not [
        e for e in emitted if isinstance(e, events.ContextCompacted | events.ContextMicrocompacted)
    ]


async def test_compact_returns_nothing_to_compact_on_trivial_history(agent):
    # split == 0 (a no-op) is checked FIRST — a trivial history never spends a summarizer call.
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        message_history=[_user_msg("just one short turn"), _assistant_msg("ok")],
        compaction_model=_skeleton_summarizer(),
    )

    assert await handler.compact() is compaction.CompactOutcome.NOTHING_TO_COMPACT
    assert not [e for e in emitted if isinstance(e, events.ContextCompacted)]


# task 128 (ADR-0018 §4): a successful compaction drops the gauge IMMEDIATELY to the chars≈/4
# estimate of the kept [summary, *tail]; the next leg's provider number overwrites it, and the
# non-COMPACTED outcomes never touch the gauge.


def _compactable_history() -> list[ModelMessage]:
    """A history with a droppable old prefix and a recent tail (compaction lands, tail is small)."""
    return [
        _user_msg("first"),
        _assistant_msg("answer"),
        _user_msg(_HUGE_PROMPT),
        _assistant_msg("recent"),
    ]


async def test_compaction_seeds_the_gauge_with_the_kept_history_estimate(agent, mocker):
    """Regression (ADR-0018 §4): a successful compact() drops the gauge to the chars≈/4 estimate of
    the new [summary, *tail], strictly below the pre-compaction provider number — red before the
    seed line, which left the gauge reading the stale pre-compaction value until the next leg."""
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        message_history=_compactable_history(),
        compaction_model=_skeleton_summarizer(),
    )
    handler._last_input_tokens = 999_999  # a prior leg's provider number: the ~85%-full footer

    assert await handler.compact() is compaction.CompactOutcome.COMPACTED

    # The gauge now reads the estimate of exactly the kept history — the single-source-of-truth
    # helper, not a second estimator.
    assert handler.last_input_tokens == compaction.estimate_history_tokens(handler.message_history)
    # ...and it dropped: the footer falls the instant /compact lands (understates, never inflates).
    assert handler.last_input_tokens < 999_999


async def test_next_leg_overwrites_the_post_compaction_estimate(agent, mocker):
    """The estimate is transient: the next leg's provider-authoritative number overwrites it, so a
    compact→trigger→compact loop can never start from the estimate (rides task 126's stub seam)."""
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        message_history=_compactable_history(),
        compaction_model=_skeleton_summarizer(),
    )

    assert await handler.compact() is compaction.CompactOutcome.COMPACTED
    seeded = handler.last_input_tokens
    assert seeded == compaction.estimate_history_tokens(handler.message_history)

    # The next leg reports its own per-request usage; _run_leg overwrites the estimate with it.
    messages: list[ModelMessage] = [
        _user_msg("go"),
        ModelResponse(parts=[TextPart(content="a")], usage=RequestUsage(input_tokens=4242)),
    ]
    run = _StubRun(messages, cumulative_input=4242)
    mocker.patch.object(handler._agent, "iter", return_value=_StubIterCM(run))

    await handler._run_leg(_ctx(0, "go", emitted), prompt="go")

    assert handler.last_input_tokens == 4242
    assert handler.last_input_tokens != seeded


async def test_nothing_to_compact_leaves_the_gauge_untouched(agent):
    """A NOTHING_TO_COMPACT no-op must not seed the gauge — history is untouched, so is the footer."""
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        message_history=[_user_msg("just one short turn"), _assistant_msg("ok")],
        compaction_model=_skeleton_summarizer(),
    )
    handler._last_input_tokens = 777

    assert await handler.compact() is compaction.CompactOutcome.NOTHING_TO_COMPACT
    assert handler.last_input_tokens == 777


async def test_summarizer_failed_leaves_the_gauge_untouched(agent, mocker):
    """A SUMMARIZER_FAILED degrade must not seed the gauge — history is untouched, so is the footer."""
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        message_history=_compactable_history(),
        compaction_model=_raising_summarizer(),
    )
    handler._last_input_tokens = 555

    assert await handler.compact() is compaction.CompactOutcome.SUMMARIZER_FAILED
    assert handler.last_input_tokens == 555


async def test_compact_returns_summarizer_failed_when_summary_is_blank(agent, mocker):
    # split > 0 implies a non-trivial transcript, so a blank summary is a summarizer failure,
    # NOT "nothing to compact" (ADR-0018 §3).
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)
    emitted: list[events.Event] = []
    history: list[ModelMessage] = [
        _user_msg("first"),
        _assistant_msg("answer"),
        _user_msg(_HUGE_PROMPT),
        _assistant_msg("recent"),
    ]
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        message_history=list(history),
        compaction_model=TestModel(custom_output_text="   "),  # blank → None summary
    )

    assert await handler.compact() is compaction.CompactOutcome.SUMMARIZER_FAILED
    assert handler.message_history == history  # untouched
    assert not [e for e in emitted if isinstance(e, events.ContextCompacted)]


async def test_compact_returns_summarizer_failed_when_the_call_raises(agent, mocker):
    # The failing-summarizer seam: a Model whose call raises → summarize_for_compaction returns
    # None (never re-raises) → SUMMARIZER_FAILED, history untouched.
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)
    emitted: list[events.Event] = []
    history: list[ModelMessage] = [
        _user_msg("first"),
        _assistant_msg("answer"),
        _user_msg(_HUGE_PROMPT),
        _assistant_msg("recent"),
    ]
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        message_history=list(history),
        compaction_model=_raising_summarizer(),
    )

    assert await handler.compact() is compaction.CompactOutcome.SUMMARIZER_FAILED
    assert handler.message_history == history  # untouched
    assert not [e for e in emitted if isinstance(e, events.ContextCompacted)]


async def test_auto_full_trigger_fired_but_failed_logs_one_info_line(agent, mocker, caplog):
    # Auto path: full trigger fired but the summarizer failed → exactly ONE INFO breadcrumb
    # naming the outcome; the turn is never interrupted (degrade-don't-break).
    mocker.patch.object(loop.settings, "compaction_context_window_tokens", 100)  # full at >= 80
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        message_history=[
            _user_msg("first"),
            _assistant_msg("answer"),
            _user_msg(_HUGE_PROMPT),
            _assistant_msg("recent"),
        ],
        compaction_model=_raising_summarizer(),
    )
    handler._last_input_tokens = 90  # in the full band → full trigger fires

    with caplog.at_level(logging.INFO, logger="decode.agent.loop"):
        await handler._maybe_auto_compact()

    info = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(info) == 1
    assert "SUMMARIZER_FAILED" in info[0].getMessage()
    assert not [e for e in emitted if isinstance(e, events.ContextCompacted)]


async def test_auto_micro_trigger_fired_but_zero_elided_logs_one_info_line(agent, mocker, caplog):
    # Auto path: micro trigger fired but nothing was eligible to elide → exactly ONE INFO line.
    mocker.patch.object(loop.settings, "compaction_context_window_tokens", 100)  # micro 60, full 80
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent,
        deps=_deps(emitted.append),
        # No tool output anywhere → microcompact elides nothing even when it fires.
        message_history=[_user_msg("first"), _assistant_msg("answer")],
        compaction_model=_skeleton_summarizer(),
    )
    handler._last_input_tokens = 70  # micro band (>= 60, < 80) → micro fires, full does not

    with caplog.at_level(logging.INFO, logger="decode.agent.loop"):
        await handler._maybe_auto_compact()

    info = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(info) == 1
    assert not [e for e in emitted if isinstance(e, events.ContextMicrocompacted)]


async def test_none_seam_disables_cascade_even_with_a_tiny_window(agent, tmp_path, mocker):
    """AC (hard regression): compaction_model=None disables the whole cascade.

    Even with the window patched so a wired handler would fully compact, the unwired handler must
    behave exactly as before: history grows normally, the turn persists, and no compaction event
    is emitted.
    """
    mocker.patch.object(loop.settings, "compaction_context_window_tokens", 1)  # would fire if wired
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)
    log = _fresh_log(tmp_path, "05")

    emitted: list[events.Event] = []
    handler = AgentTurnHandler(  # no compaction_model → None (the default)
        agent,
        deps=_deps(emitted.append),
        session_log=log,
        message_history=[_user_msg("first"), _assistant_msg("answer")],
    )

    with agent.override(model=_text_model("ok")):
        await _drive_collecting(handler, _ctx(0, _HUGE_PROMPT, emitted))()

    assert not [
        e for e in emitted if isinstance(e, events.ContextCompacted | events.ContextMicrocompacted)
    ]
    assert "compaction" not in _log_line_types(log)
    # History carried across normally (seed + this turn's two new messages), nothing dropped.
    assert len(handler.message_history) == 4
    head = handler.message_history[0]
    assert isinstance(head, ModelRequest)
    assert any(getattr(p, "content", None) == "first" for p in head.parts)


# demo-2-bug-hunt regression: a leg that crashes (tool ModelRetry budget exhausted) must not
# lose its messages, must not brick every later prompt, and must still reach the session log.


def _register_flaky(agent, *, retries: int = 1) -> None:
    """Register the TEST-ONLY gated ``flaky`` tool: it defers ONCE, then always ``ModelRetry``\\ s.

    A stand-in for the demo-2-bug-hunt ``edit`` whose ``old_string`` never matches: the first
    call is gated (pausing the leg, which puts the unprocessed call into the handler's history),
    every later call runs inline — like a gate allow-rule written on approval — and fails, so the
    budget is exhausted *within* the resume leg and the crash lands mid-leg.
    """
    deferred_once: list[str] = []

    def flaky(ctx: RunContext[AgentDeps], text: str) -> str:
        if not ctx.tool_call_approved and not deferred_once:
            deferred_once.append(ctx.tool_call_id)
            raise ApprovalRequired
        raise ModelRetry("flaky failed; try again.")

    agent.tool(flaky, retries=retries)


def _flaky_forever() -> FunctionModel:
    """A streaming model that (re-)issues a ``flaky`` call on the first leg and after every nag.

    First leg (user prompt) → call ``flaky``; a ``RetryPromptPart`` nag → call it again; anything
    else → text. With ``retries=1`` the second approved failure exceeds the budget and pydantic-ai
    raises ``UnexpectedModelBehavior`` mid-leg.
    """

    async def stream_function(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator:
        last_parts = messages[-1].parts
        if any(isinstance(p, UserPromptPart | RetryPromptPart) for p in last_parts):
            yield {0: DeltaToolCall(name="flaky", json_args='{"text": "x"}')}
        else:
            yield "gave up"

    return FunctionModel(stream_function=stream_function)


async def _allow_all(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.allow()


async def _crash_flaky_turn(agent, handler: AgentTurnHandler, emitted: list[events.Event]) -> None:
    """Drive one turn that crashes with ``exceeded max retries`` on the approved resume leg."""
    with (
        agent.override(model=_flaky_forever()),
        pytest.raises(UnexpectedModelBehavior, match="exceeded max retries"),
    ):
        await _drive_collecting(handler, _ctx(0, "please flaky", emitted))()


async def test_crashed_resume_leg_keeps_its_messages(agent):
    """A leg that raises must still land its accumulated messages in ``message_history``.

    Before the fix the crashed resume leg's messages (the tool return nag, the model's second
    call) evaporated — the handler's history froze at the previous leg, hiding what happened
    from persistence and forensics.
    """
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append, resolve_permission=_allow_all))
    _register_flaky(agent)

    await _crash_flaky_turn(agent, handler, emitted)

    # The crashed resume leg's RetryPromptPart nag survived into the handler's history.
    nags = [
        p
        for m in handler.message_history
        for p in m.parts
        if isinstance(p, RetryPromptPart) and p.tool_name == "flaky"
    ]
    assert nags, "the crashed leg's messages must be captured, not lost"


async def test_crashed_turn_does_not_brick_later_prompts(agent):
    """THE demo-2-bug-hunt bricking: after a crashed resume leg every prompt died.

    The crash left history ending in an unprocessed tool call; pydantic-ai then rejected every
    new prompt with "Cannot provide a new user prompt when the message history contains
    unprocessed tool calls." The handler must heal the dangling call so the next turn runs.
    """
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append, resolve_permission=_allow_all))
    _register_flaky(agent)

    await _crash_flaky_turn(agent, handler, emitted)

    with agent.override(model=TestModel(call_tools=[], custom_output_text="recovered")):
        await _drive_collecting(handler, _ctx(1, "are you alive?", emitted))()

    text = "".join(e.text for e in emitted if isinstance(e, events.AssistantTextDelta))
    assert "recovered" in text
    # The dangling call was healed with a synthesized interrupted-tool return.
    healed = [
        p
        for m in handler.message_history
        for p in m.parts
        if isinstance(p, ToolReturnPart)
        and p.tool_name == "flaky"
        and "interrupted" in str(p.content)
    ]
    assert healed, "the unprocessed tool call must be healed before the next prompt leg"


async def test_crashed_turn_still_reaches_the_session_log(agent, tmp_path):
    """A crashed turn must persist what it has — demo-2-bug-hunt left a header-only session file.

    ``WOULD_STOP`` (the only persist point before the fix) is never reached when a leg raises,
    so the whole turn vanished from the log: no ``--resume``, no forensics. The turn's captured
    messages must land in the session file even when it crashes.
    """
    emitted: list[events.Event] = []
    log = _fresh_log(tmp_path, "aa")
    handler = AgentTurnHandler(
        agent, deps=_deps(emitted.append, resolve_permission=_allow_all), session_log=log
    )
    _register_flaky(agent)

    await _crash_flaky_turn(agent, handler, emitted)

    replayed = session_log.load(log.path)
    calls = [
        p
        for m in replayed
        for p in m.parts
        if isinstance(p, ToolCallPart) and p.tool_name == "flaky"
    ]
    assert calls, "the crashed turn's messages must be persisted for --resume and forensics"


async def test_resumed_poisoned_history_is_healed_on_the_next_prompt(agent):
    """A ``--resume`` of a crash-persisted log seeds history ending in an unprocessed call.

    The heal must cover the seeded path too: a handler *constructed* with a poisoned history
    (not just one poisoned mid-session) accepts the next prompt.
    """
    poisoned: list[ModelMessage] = [
        _user_msg("fix the bug"),
        ModelResponse(
            parts=[ToolCallPart(tool_name="edit", args='{"path": "x.py"}', tool_call_id="call-1")]
        ),
    ]
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=_deps(emitted.append), message_history=poisoned)

    with agent.override(model=TestModel(call_tools=[], custom_output_text="healed and running")):
        await _drive_collecting(handler, _ctx(0, "hello again", emitted))()

    text = "".join(e.text for e in emitted if isinstance(e, events.AssistantTextDelta))
    assert "healed and running" in text


# The compaction trigger reads THIS run's window, not the configured one (task 123)


async def test_compaction_fires_on_the_runs_resolved_window_not_the_configured_one(
    agent, tmp_path, mocker
):
    """``deps.context_window_tokens`` wins over ``settings`` — that is the point of the seam.

    Settings claims a huge window (nothing would ever compact); the run's actual model resolved a
    tiny one. This is the ``decode run --model <smaller-window-id>`` case: compaction MUST fire.
    """
    mocker.patch.object(loop.settings, "compaction_context_window_tokens", 10_000_000)
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)
    log = _fresh_log(tmp_path, "a1")

    emitted: list[events.Event] = []
    deps = _deps(emitted.append)
    deps.context_window_tokens = 60  # the resolved window for the run's real model: full band
    handler = AgentTurnHandler(
        agent,
        deps=deps,
        session_log=log,
        message_history=[_user_msg("first"), _assistant_msg("first answer")],
        compaction_model=_skeleton_summarizer(),
    )

    with agent.override(model=_text_model("ok")):
        await _drive_collecting(handler, _ctx(0, _HUGE_PROMPT, emitted))()

    assert [e for e in emitted if isinstance(e, events.ContextCompacted)]


async def test_an_unresolved_window_still_falls_back_to_the_configured_setting(
    agent, tmp_path, mocker
):
    """``context_window_tokens=None`` (any deps built without the seam) keeps the old behaviour."""
    mocker.patch.object(loop.settings, "compaction_context_window_tokens", 60)  # full band
    mocker.patch.object(loop.settings, "compaction_keep_recent_tokens", 10)
    log = _fresh_log(tmp_path, "a2")

    emitted: list[events.Event] = []
    deps = _deps(emitted.append)
    assert deps.context_window_tokens is None  # the default: nothing resolved it
    handler = AgentTurnHandler(
        agent,
        deps=deps,
        session_log=log,
        message_history=[_user_msg("first"), _assistant_msg("first answer")],
        compaction_model=_skeleton_summarizer(),
    )

    with agent.override(model=_text_model("ok")):
        await _drive_collecting(handler, _ctx(0, _HUGE_PROMPT, emitted))()

    assert [e for e in emitted if isinstance(e, events.ContextCompacted)]
