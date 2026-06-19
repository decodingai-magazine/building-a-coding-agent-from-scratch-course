"""Unit tests for :mod:`decode.agent.loop` — the real Pydantic AI turn handler.

ADR-0002 §1-4: the loop drives ``agent.iter()`` as the harness
:data:`~decode.harness.runner.TurnHandler`. It streams model nodes into
:mod:`decode.entities.events`, yields the ``MODEL_REQUEST`` / ``WOULD_STOP`` boundaries the
:class:`~decode.harness.runner.Runner` expects, drains steering into ``message_history``
before each model request, and carries ``message_history`` across turns. Task 005 adds the
deferred-tool legs: when a leg resolves to ``DeferredToolRequests`` the loop emits
``PermissionRequested``, resolves each gated call through the gate + the async resolver,
builds ``DeferredToolResults`` (approve / ``ToolDenied``), drains steering at the resume
boundary, and resumes until the output is a plain ``str``.

No network: every test runs the agent against ``TestModel`` / ``FunctionModel`` and asserts
on the events the loop emitted and the boundaries it yielded. The agent is built once with a
dummy key and the model is swapped per test via ``agent.override(model=...)``.
"""

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from decode.agent.deps import AgentDeps, PermissionResolver, UserQuestionResolver
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import Boundary, Runner, TurnContext
from decode.permissions.gate import PermissionGate
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
) -> AgentDeps:
    """Build AgentDeps with the task-005 gate + resolvers wired (defaults to deny / no user)."""
    return AgentDeps(
        cwd=Path("."),
        emit=emit,
        gate=PermissionGate(),
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
    """The handler is a real TurnHandler: the Runner can drive it to completion."""
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
    """A model failure must raise out of the handler so the Runner emits an AgentError."""
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
    """A model that emits thinking content streams ThinkingDelta events, kept separate."""
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


# --- task 005: the permission gate via the deferred-tool flow -------------------------------


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


async def test_gated_tool_pauses_and_emits_permission_requested(agent):
    """ADR-0002 §3: a gated call pauses the run and surfaces a PermissionRequested event."""
    emitted: list[events.Event] = []
    asked: list[PermissionRequest] = []

    async def approving_resolver(request: PermissionRequest) -> PermissionDecision:
        asked.append(request)
        return PermissionDecision.allow()

    handler = AgentTurnHandler(
        agent, deps=_deps(emitted.append, resolve_permission=approving_resolver)
    )

    with agent.override(model=_noop_then_text()):
        await _drive_collecting(handler, _ctx(0, "please noop", emitted))()

    perms = [e for e in emitted if isinstance(e, events.PermissionRequested)]
    assert perms, "a gated tool must emit a PermissionRequested event"
    assert perms[0].name == "noop"
    # The gate's ASK was routed to the resolver with a faithful request.
    assert asked and asked[0].tool_name == "noop"
    assert asked[0].read_only is False
    assert asked[0].tool_call_id == perms[0].tool_call_id


async def test_approval_resumes_and_executes_the_tool(agent):
    """An APPROVE resumes the run; the tool executes and the turn ends with text."""
    emitted: list[events.Event] = []

    async def approving_resolver(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow()

    handler = AgentTurnHandler(
        agent, deps=_deps(emitted.append, resolve_permission=approving_resolver)
    )

    with agent.override(model=_noop_then_text(final_text="finished after approve")):
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


async def test_denial_feeds_a_tooldenied_result_back_to_the_model(agent):
    """A DENY returns a denial tool-result the model sees on the next leg (ADR-0002 §3)."""
    emitted: list[events.Event] = []

    async def denying_resolver(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.deny(reason="nope, not allowed")

    captured: list[list[ModelMessage]] = []

    handler = AgentTurnHandler(
        agent, deps=_deps(emitted.append, resolve_permission=denying_resolver)
    )

    with agent.override(model=_noop_then_text(final_text="ok, understood", captured=captured)):
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


async def test_steering_message_reaches_the_model_at_the_deferred_resume(agent):
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
        agent, deps=_deps(emitted.append, resolve_permission=approving_resolver)
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

    with agent.override(model=_noop_then_text(final_text="done", captured=captured)):
        await _run()

    # The steering message reached the model on the resume leg (the second model request).
    assert len(captured) >= 2, "expected a resume leg after the deferred pause"
    resume_user_messages = _user_messages_seen(captured[-1])
    assert any("STEER-AT-RESUME-123" in m for m in resume_user_messages)


async def test_single_flight_lock_spans_the_whole_multi_leg_deferred_turn(agent):
    """ADR-0002 §4: the single-flight lock spans the full deferred (multi-leg) turn.

    Driven through the *real* Runner: a gated tool makes the turn fragment into a request
    leg + a deferred resume leg. While the turn is in flight a second submit must enqueue
    (steering), never start a parallel turn; the lock holds until the resume leg finishes.
    """
    emitted: list[events.Event] = []

    async def approving_resolver(request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision.allow()

    handler = AgentTurnHandler(
        agent, deps=_deps(emitted.append, resolve_permission=approving_resolver)
    )

    with agent.override(model=_noop_then_text(final_text="multi-leg done")):
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


# --- task 011: the blocking ask_user tool (NOT gated; rides the decision channel) -----------


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
