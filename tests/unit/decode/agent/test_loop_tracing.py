"""Unit tests for the per-turn Opik root span the loop opens (ADR-0014 §4, task 092).

:meth:`AgentTurnHandler.__call__` runs its whole turn inside ONE ``observability.root_span``
named ``chat_turn`` carrying ``session_id`` as the Opik ``thread_id``. ``root_span`` is patched
with a recording context manager (no logfire, no network) to assert: opened once per turn, right
name + thread id, and closed exactly once — on a normal turn, a gated multi-leg turn, an abort,
and a mid-leg exception. The agent runs against ``FunctionModel`` via ``agent.override``; the
real-span nesting / usage assertions live in ``tests/integration/test_opik_repl_trace.py``.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from support.noop_helper import register_noop

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import Runner
from decode.permissions.gate import PermissionGate
from decode.tui.app import InputIntent


class _SpanRecorder:
    """A stand-in for ``observability.root_span`` that records every open + enter/exit.

    Each call records the ``(name, thread_id)`` it was opened with and returns a fresh context
    manager; entering/exiting bumps :attr:`enters` / :attr:`exits`, so a test can prove the span was
    opened once and closed **exactly once** (the load-bearing abort-safety property).
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.enters = 0
        self.exits = 0

    def __call__(
        self, name: str, *, thread_id: str | None = None, input: str | None = None
    ) -> contextlib.AbstractContextManager[None]:
        self.calls.append((name, thread_id))
        return self._span()

    @contextlib.contextmanager
    def _span(self) -> Iterator[None]:
        self.enters += 1
        try:
            yield
        finally:
            self.exits += 1


@pytest.fixture
def agent(mocker):
    """A real `decode` agent built with a dummy key (never used: tests override the model)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    return build_agent()


@pytest.fixture
def recorder(mocker) -> _SpanRecorder:
    """Patch the loop's ``observability.root_span`` with a recording context manager."""
    rec = _SpanRecorder()
    mocker.patch("decode.agent.loop.observability.root_span", new=rec)
    return rec


async def _allow(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.allow()


async def _no_user(question: str) -> str:  # pragma: no cover - never called here
    raise AssertionError("ask_user must not be reached in these tests")


def _deps(gate: PermissionGate | None = None) -> AgentDeps:
    return AgentDeps(
        cwd=Path("."),
        emit=lambda event: None,
        gate=gate or PermissionGate(),
        resolve_permission=_allow,
        resolve_user_question=_no_user,
    )


def _text_model(*words: str) -> FunctionModel:
    """A FunctionModel that streams ``words`` as text deltas then ends the leg (turn stops)."""

    async def stream_function(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        for word in words:
            yield word

    return FunctionModel(stream_function=stream_function)


def _gated_then_text_model() -> FunctionModel:
    """First leg streams a gated ``noop`` call (defers); every later leg streams closing text."""
    state = {"calls": 0}

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        state["calls"] += 1
        if state["calls"] == 1:
            yield {0: DeltaToolCall(name="noop", json_args='{"text": "hi"}')}
        else:
            yield "done"

    return FunctionModel(stream_function=stream_function)


_BOOM = "boom-in-model"


def _raising_model() -> FunctionModel:
    """A FunctionModel whose stream yields one token then raises mid-leg (a model failure)."""

    async def stream_function(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "partial "
        raise RuntimeError(_BOOM)

    return FunctionModel(stream_function=stream_function)


async def _run_one_turn(runner: Runner, prompt: str) -> None:
    await runner.submit(prompt, InputIntent.STEER)
    await runner.wait_idle()


async def test_call_opens_one_chat_turn_root_span_with_the_session_id_as_thread_id(agent, recorder):
    handler = AgentTurnHandler(agent, deps=_deps(), session_id="sess-42")
    runner = Runner(handler, on_event=lambda event: None)

    with agent.override(model=_text_model("hello ", "world")):
        await _run_one_turn(runner, "hi")

    # Exactly one root span, named ``chat_turn``, carrying the wired session id as ``thread_id``.
    assert recorder.calls == [("chat_turn", "sess-42")]
    # Opened once and closed EXACTLY once (entered/exited a single time).
    assert recorder.enters == 1
    assert recorder.exits == 1


async def test_session_id_defaults_to_none_thread_id_when_not_wired(agent, recorder):
    # A headless/test handler built with no session id passes ``thread_id=None`` (nullcontext-safe).
    handler = AgentTurnHandler(agent, deps=_deps())
    runner = Runner(handler, on_event=lambda event: None)

    with agent.override(model=_text_model("ok")):
        await _run_one_turn(runner, "hi")

    assert recorder.calls == [("chat_turn", None)]
    assert recorder.enters == 1
    assert recorder.exits == 1


async def test_a_gated_multi_leg_turn_stays_inside_one_root_span(agent, recorder):
    register_noop(agent)
    handler = AgentTurnHandler(agent, deps=_deps(), session_id="sess-gate")
    runner = Runner(handler, on_event=lambda event: None)

    with agent.override(model=_gated_then_text_model()):
        await _run_one_turn(runner, "do the gated thing")

    # The pause leg + the approved resume leg are ONE turn == ONE root span, closed once.
    assert recorder.calls == [("chat_turn", "sess-gate")]
    assert recorder.enters == 1
    assert recorder.exits == 1


async def test_two_turns_open_two_root_spans_sharing_the_thread_id(agent, recorder):
    handler = AgentTurnHandler(agent, deps=_deps(), session_id="sess-two")
    runner = Runner(handler, on_event=lambda event: None)

    with agent.override(model=_text_model("first")):
        await _run_one_turn(runner, "turn one")
    with agent.override(model=_text_model("second")):
        await _run_one_turn(runner, "turn two")

    # One root span per turn, both carrying the same session thread id.
    assert recorder.calls == [("chat_turn", "sess-two"), ("chat_turn", "sess-two")]
    assert recorder.enters == 2
    assert recorder.exits == 2


async def test_abort_closes_the_root_span_exactly_once(agent, recorder):
    """Abort (runner sets ``_abort`` + ``aclose()``) must unwind the ``with`` — one open, one close.

    The runner throws ``GeneratorExit`` into the ``__call__`` generator suspended at its first
    ``yield`` (inside the ``with``); the span must close exactly once and never leak. Runs under the
    suite-wide ``filterwarnings=["error"]``, so an ignored-``GeneratorExit`` warning would fail it.
    """
    handler = AgentTurnHandler(agent, deps=_deps(), session_id="sess-abort")
    finished: list[object] = []
    runner = Runner(handler, on_event=finished.append)

    with agent.override(model=_text_model("never gets here")):
        await runner.submit("go", InputIntent.STEER)
        runner.abort()  # cooperative abort while pinned before the first leg
        await runner.wait_idle()

    # The span was opened once (before the first yield) and closed exactly once on the abort unwind.
    assert recorder.calls == [("chat_turn", "sess-abort")]
    assert recorder.enters == 1
    assert recorder.exits == 1
    # Sanity: the turn really did take the abort path.
    aborted = [e for e in finished if getattr(e, "kind", "") == "turn_finished"]
    assert aborted and aborted[-1].aborted is True


async def test_exception_mid_leg_closes_the_root_span_exactly_once(agent, recorder):
    """An exception mid-leg unwinds the ``with`` — one open, one close — and surfaces as AgentError.

    The third spec-named unwind path (normal return / abort / EXCEPTION): a model failure inside the
    leg propagates OUT through ``observability.root_span``'s ``__exit__`` (closing the span) *before*
    the runner's ``except Exception`` catches it and emits an ``AgentError``. The span must close
    exactly once and never leak; the turn is NOT an abort (``aborted is False``) and the original
    error message surfaces unchanged. Runs under the suite-wide ``filterwarnings=["error"]``, so an
    ignored unwind warning would fail it.
    """
    handler = AgentTurnHandler(agent, deps=_deps(), session_id="sess-boom")
    seen: list[object] = []
    runner = Runner(handler, on_event=seen.append)

    with agent.override(model=_raising_model()):
        await _run_one_turn(runner, "make the model blow up")

    # Opened once (before the first yield) and closed EXACTLY once on the exception unwind.
    assert recorder.calls == [("chat_turn", "sess-boom")]
    assert recorder.enters == 1
    assert recorder.exits == 1
    # The model failure surfaced as an AgentError carrying the original message — not swallowed.
    errors = [e for e in seen if getattr(e, "kind", "") == "agent_error"]
    assert errors and errors[-1].message == _BOOM
    # An error, NOT an abort: the turn finished with aborted False (distinguishes it from AC6).
    finished = [e for e in seen if getattr(e, "kind", "") == "turn_finished"]
    assert finished and finished[-1].aborted is False
