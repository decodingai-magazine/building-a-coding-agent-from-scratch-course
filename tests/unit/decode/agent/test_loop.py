"""Unit tests for :mod:`decode.agent.loop` — the real Pydantic AI turn handler.

ADR-0002 §1-2,4: the loop drives ``agent.iter()`` as the harness
:data:`~decode.harness.runner.TurnHandler`. It streams model nodes into
:mod:`decode.entities.events`, yields the ``MODEL_REQUEST`` / ``WOULD_STOP`` boundaries the
:class:`~decode.harness.runner.Runner` expects, drains steering into ``message_history``
before each model request, and carries ``message_history`` across turns.

No network: every test runs the agent against ``TestModel`` / ``FunctionModel`` and asserts
on the events the loop emitted and the boundaries it yielded. The agent is built once with a
dummy key and the model is swapped per test via ``agent.override(model=...)``.
"""

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai.messages import ModelMessage, ModelRequest
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.entities import events
from decode.harness.runner import Boundary, Runner, TurnContext
from decode.tui.app import InputIntent


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
    handler = AgentTurnHandler(agent, deps=AgentDeps(cwd=Path("."), emit=emitted.append))

    with agent.override(model=FunctionModel(stream_function=_stream_words("Hello, ", "world"))):
        boundaries = await _drive_collecting(handler, _ctx(0, "hi", emitted))()

    text = "".join(e.text for e in emitted if isinstance(e, events.AssistantTextDelta))
    assert text == "Hello, world"
    # The loop hits a model-request boundary and then a would-stop boundary.
    assert Boundary.MODEL_REQUEST in boundaries
    assert boundaries[-1] is Boundary.WOULD_STOP


async def test_first_boundary_is_model_request(agent):
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=AgentDeps(cwd=Path("."), emit=emitted.append))

    with agent.override(model=TestModel(custom_output_text="ok")):
        boundaries = await _drive_collecting(handler, _ctx(0, "hi", emitted))()

    assert boundaries[0] is Boundary.MODEL_REQUEST


async def test_message_history_carries_across_turns(agent):
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(agent, deps=AgentDeps(cwd=Path("."), emit=emitted.append))

    with agent.override(model=TestModel(custom_output_text="first")):
        await _drive_collecting(handler, _ctx(0, "turn one", emitted))()
    after_first = len(handler.message_history)
    assert after_first > 0  # the first turn populated history

    with agent.override(model=TestModel(custom_output_text="second")):
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
    handler = AgentTurnHandler(agent, deps=AgentDeps(cwd=Path("."), emit=emitted.append))

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
    handler = AgentTurnHandler(agent, deps=AgentDeps(cwd=Path("."), emit=emitted.append))

    with agent.override(model=TestModel(custom_output_text="all good")):
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
    handler = AgentTurnHandler(agent, deps=AgentDeps(cwd=Path("."), emit=emitted.append))

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
    handler = AgentTurnHandler(agent, deps=AgentDeps(cwd=Path("."), emit=emitted.append))

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
