"""Integration capstone: the REPL per-turn Opik trace shape through the REAL stack (ADR-0014 §4).

Drives the **real** ``build_agent()`` + :class:`~decode.harness.runner.Runner` +
:class:`~decode.agent.loop.AgentTurnHandler` + permission gate, swapping only the model boundary
(``agent.override(model=FunctionModel(...))``) and capturing spans with ``logfire.testing``'s in-memory
exporter (``capfire``). It proves the whole trace tree a turn produces, end to end, with no key and no
network:

* one ``chat_turn`` root span per turn, carrying ``thread_id`` = the session id, with the pydantic-ai
  ``chat`` (model-request) + ``running tool`` spans nested under it;
* a leaf ``chat`` span carries token usage (``gen_ai.usage.input_tokens`` > 0);
* a gated tool's approve/resume leg stays in the SAME root span (one trace spans the pause + resume);
* two turns emit two roots sharing the ``thread_id``;
* an in-turn compaction call nests under that turn's root (rides free via global instrumentation);
* **abort safety** — a turn aborted mid-flight closes the root span exactly once (no leak), asserted
  under the suite-wide ``filterwarnings=["error"]``;
* **inactive** — with tracing off, a full turn emits ZERO spans.

Activation mirrors the task-091 in-memory pattern: ``tracing._active`` is forced ``True`` and pydantic-ai
is instrumented directly (a fake ``opik_api_key`` is set for fidelity) rather than through
``init_tracing`` — whose real ``logfire.configure`` would replace ``capfire``'s exporter and could flush
to the network. An autouse fixture saves/restores the global instrumentation + module flag so nothing
leaks across tests.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import logfire
import pytest
from logfire.testing import (
    CaptureLogfire,
    capfire,  # noqa: F401 — imported so pytest registers the in-memory fixture
)
from pydantic import SecretStr
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from support.noop_helper import register_noop

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.config.settings import settings
from decode.entities import events
from decode.harness.runner import Runner
from decode.observability import tracing
from decode.observability.tracing import reset_tracing
from decode.permissions.gate import PermissionGate
from decode.tui.app import InputIntent

_SESSION_ID = "00000000-0000-0000-0000-0000000000aa"
_READ_TARGET = "notes.txt"
_READ_CONTENTS = "remember to trace this turn"


@pytest.fixture(autouse=True)
def _fake_gemini_key(mocker):
    """Let ``build_agent`` construct the Gemini provider offline (the model is overridden)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )


@pytest.fixture(autouse=True)
def _isolate_tracing_state() -> Iterator[None]:
    """Save/restore the GLOBAL pydantic-ai instrumentation + the module flag, so nothing leaks.

    ``instrument_pydantic_ai`` mutates the process-global ``Agent._instrument_default``; without this
    an active-tracing test would leave every later test's agents instrumented (the inactive test would
    then see spans). Captured before any test body runs (autouse) and restored after.
    """
    prior_instrument = Agent._instrument_default
    prior_active = tracing._active
    yield
    Agent.instrument_all(prior_instrument)
    tracing._active = prior_active
    reset_tracing()


@pytest.fixture
def active_tracing(monkeypatch, capfire) -> CaptureLogfire:  # noqa: F811
    """Turn tracing ON against ``capfire``'s in-memory exporter and instrument pydantic-ai.

    ``capfire`` configures logfire with the in-memory exporter FIRST (fixture dependency), then we
    instrument pydantic-ai so its model/tool spans emit into it and set ``_active`` so ``root_span``
    opens real spans. A fake ``opik_api_key`` is set only for fidelity with the production activation
    trigger — the span path never reads it. Restored by :func:`_isolate_tracing_state`.
    """
    monkeypatch.setattr(settings, "opik_api_key", SecretStr("fake-opik-key"), raising=False)
    monkeypatch.setattr(tracing, "_active", True)
    logfire.instrument_pydantic_ai()
    return capfire


# --- scripted models (no network) --------------------------------------------------------------


def _last_request_has_tool_return(messages: list[ModelMessage]) -> bool:
    """True when the latest request carries a tool result (i.e. this is a resume leg)."""
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            return any(isinstance(part, ToolReturnPart) for part in message.parts)
    return False


def _tool_then_text_model(tool_call: DeltaToolCall) -> FunctionModel:
    """Leg 1 streams ``tool_call``; every resume leg streams closing text (turn stops)."""

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        if _last_request_has_tool_return(messages):
            yield "the turn is done"
            return
        yield {0: tool_call}

    return FunctionModel(stream_function=stream_function)


def _read_model() -> FunctionModel:
    return _tool_then_text_model(
        DeltaToolCall(name="read", json_args=json.dumps({"path": _READ_TARGET}))
    )


def _gated_noop_model() -> FunctionModel:
    return _tool_then_text_model(DeltaToolCall(name="noop", json_args=json.dumps({"text": "hi"})))


def _text_model(*words: str) -> FunctionModel:
    """A single-leg text turn (input_tokens fixed at 50)."""

    async def stream_function(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        for word in words:
            yield word

    return FunctionModel(stream_function=stream_function)


def _skeleton_summarizer() -> FunctionModel:
    """A non-streaming FunctionModel standing in for the in-turn compaction summarizer call."""

    async def fill(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(content="# Conversation summary\n\n## Goal\nMARKER\n")]
        )

    return FunctionModel(fill)


_BOOM = "boom-in-model"


def _raising_model() -> FunctionModel:
    """A FunctionModel whose stream yields one token then raises mid-leg (a model failure)."""

    async def stream_function(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "partial "
        raise RuntimeError(_BOOM)

    return FunctionModel(stream_function=stream_function)


# --- harness helpers ---------------------------------------------------------------------------


def _deps(working_dir: Path, sink: list[events.Event]) -> AgentDeps:
    return AgentDeps(
        cwd=working_dir,
        emit=sink.append,
        gate=PermissionGate(),  # default mode: read-only tools auto-allow, no resolver needed
        resolve_permission=None,
        resolve_user_question=None,
    )


async def _run_turn(runner: Runner, prompt: str) -> None:
    await runner.submit(prompt, InputIntent.STEER)
    await runner.wait_idle()


def _chat_turn_roots(spans: list[dict]) -> list[dict]:
    return [s for s in spans if s["name"] == "chat_turn"]


def _model_spans(spans: list[dict]) -> list[dict]:
    """The pydantic-ai model-request (``chat``) spans — NOT the ``chat_turn`` root (underscore)."""
    return [s for s in spans if s["name"].startswith("chat ")]


def _tool_spans(spans: list[dict]) -> list[dict]:
    return [s for s in spans if s["name"] == "running tool"]


# --- tests -------------------------------------------------------------------------------------


async def test_single_turn_is_one_chat_turn_root_with_nested_model_and_tool_spans(
    active_tracing, tmp_path
):
    """AC1 + AC3: one ``chat_turn`` root (thread_id = session id); ``chat`` + tool spans nest; usage."""
    working_dir = tmp_path / "ws"
    working_dir.mkdir()
    (working_dir / _READ_TARGET).write_text(_READ_CONTENTS, encoding="utf-8")
    sink: list[events.Event] = []
    agent = build_agent()
    handler = AgentTurnHandler(agent, deps=_deps(working_dir, sink), session_id=_SESSION_ID)
    runner = Runner(handler, on_event=sink.append)

    with agent.override(model=_read_model()):
        await _run_turn(runner, "read the notes file")

    spans = active_tracing.exporter.exported_spans_as_dict()
    roots = _chat_turn_roots(spans)
    assert len(roots) == 1, [s["name"] for s in spans]
    root = roots[0]
    assert root["parent"] is None, "the chat_turn span must be the trace root"
    assert root["attributes"]["thread_id"] == _SESSION_ID
    # The root carries the turn's input + final output so Opik populates the TRACE-level input/output
    # (what the Thread view renders as the user/assistant message pair — ADR-0014 §4).
    assert root["attributes"]["input"] == "read the notes file"
    assert root["attributes"]["output"] == "the turn is done"
    trace_id = root["context"]["trace_id"]

    model_spans = _model_spans(spans)
    tool_spans = _tool_spans(spans)
    assert model_spans, [s["name"] for s in spans]
    assert tool_spans, "the read tool must produce a 'running tool' span"
    # Everything the turn produced nests under the one root: same trace, and not a root itself.
    for span in model_spans + tool_spans:
        assert span["context"]["trace_id"] == trace_id
        assert span["parent"] is not None

    # AC3: a leaf model span carries token usage (> 0).
    input_tokens = [s["attributes"].get("gen_ai.usage.input_tokens") for s in model_spans]
    assert any(tokens and tokens > 0 for tokens in input_tokens), input_tokens


async def test_gated_tool_approve_and_resume_stay_in_one_trace(active_tracing, tmp_path):
    """AC2: a gated tool's pause + approved resume leg ride ONE ``chat_turn`` trace."""
    approvals: list[object] = []

    async def approve(request):
        approvals.append(request)
        from decode.entities.permissions import PermissionDecision

        return PermissionDecision.allow()

    working_dir = tmp_path / "ws"
    working_dir.mkdir()
    sink: list[events.Event] = []
    agent = build_agent()
    register_noop(agent)
    deps = AgentDeps(
        cwd=working_dir,
        emit=sink.append,
        gate=PermissionGate(),
        resolve_permission=approve,
        resolve_user_question=None,
    )
    handler = AgentTurnHandler(agent, deps=deps, session_id=_SESSION_ID)
    runner = Runner(handler, on_event=sink.append)

    with agent.override(model=_gated_noop_model()):
        await _run_turn(runner, "run the gated tool")

    assert approvals, "the gated noop must have been routed to the resolver"
    spans = active_tracing.exporter.exported_spans_as_dict()
    roots = _chat_turn_roots(spans)
    assert len(roots) == 1, "the pause + resume legs are ONE turn == ONE root span"
    trace_id = roots[0]["context"]["trace_id"]
    # Both legs' model spans + the tool span share the single trace (turn latency spans the wait).
    nested = _model_spans(spans) + _tool_spans(spans)
    assert len(nested) >= 2, [s["name"] for s in spans]
    for span in nested:
        assert span["context"]["trace_id"] == trace_id


async def test_two_turns_emit_two_roots_sharing_the_thread_id(active_tracing, tmp_path):
    """AC4: two turns in one session → two ``chat_turn`` roots sharing the session thread id."""
    working_dir = tmp_path / "ws"
    working_dir.mkdir()
    sink: list[events.Event] = []
    agent = build_agent()
    handler = AgentTurnHandler(agent, deps=_deps(working_dir, sink), session_id=_SESSION_ID)
    runner = Runner(handler, on_event=sink.append)

    with agent.override(model=_text_model("first answer")):
        await _run_turn(runner, "turn one")
    with agent.override(model=_text_model("second answer")):
        await _run_turn(runner, "turn two")

    roots = _chat_turn_roots(active_tracing.exporter.exported_spans_as_dict())
    assert len(roots) == 2
    assert {r["attributes"]["thread_id"] for r in roots} == {_SESSION_ID}
    # Two distinct traces (one per turn), both under the same conversation thread.
    assert len({r["context"]["trace_id"] for r in roots}) == 2


async def test_in_turn_compaction_nests_under_the_turn_root(active_tracing, tmp_path, monkeypatch):
    """AC5: a compaction summarizer call fired at would-stop nests under the turn's root span."""
    # Force full compaction: window=60 → full level int(60*0.80)=48 ≤ 50 (the fixed FunctionModel
    # estimate); the huge prompt makes split_tail keep only the final turn as the recent tail.
    monkeypatch.setattr("decode.agent.loop.settings.compaction_context_window_tokens", 60)
    monkeypatch.setattr("decode.agent.loop.settings.compaction_keep_recent_tokens", 10)
    working_dir = tmp_path / "ws"
    working_dir.mkdir()
    sink: list[events.Event] = []
    agent = build_agent()
    handler = AgentTurnHandler(
        agent,
        deps=_deps(working_dir, sink),
        session_id=_SESSION_ID,
        message_history=[
            ModelRequest(parts=[UserPromptPart(content="earlier")]),
            ModelResponse(parts=[TextPart(content="earlier answer")]),
        ],
        compaction_model_or_settings=_skeleton_summarizer(),
    )
    runner = Runner(handler, on_event=sink.append)

    with agent.override(model=_text_model("keep working on the task " * 100)):
        await _run_turn(runner, "keep working on the task " * 100)

    # Compaction actually fired inside the turn.
    assert [e for e in sink if isinstance(e, events.ContextCompacted)], "compaction must fire"

    spans = active_tracing.exporter.exported_spans_as_dict()
    roots = _chat_turn_roots(spans)
    assert len(roots) == 1
    trace_id = roots[0]["context"]["trace_id"]
    model_spans = _model_spans(spans)
    # The turn's model call AND the summarizer's — both nest under the one root.
    assert len(model_spans) >= 2, [s["name"] for s in spans]
    for span in model_spans:
        assert span["context"]["trace_id"] == trace_id
        assert span["parent"] is not None


async def test_abort_closes_the_root_span_exactly_once(active_tracing, tmp_path):
    """AC6: an aborted turn closes the ``chat_turn`` span exactly once — no leak (filterwarnings=error).

    The runner throws ``GeneratorExit`` into ``__call__`` suspended at its first ``yield`` (inside the
    ``with``); a captured span is one that ENDED, so exactly one exported ``chat_turn`` proves it
    closed once and never leaked (a leaked span is never exported).
    """
    working_dir = tmp_path / "ws"
    working_dir.mkdir()
    sink: list[events.Event] = []
    agent = build_agent()
    handler = AgentTurnHandler(agent, deps=_deps(working_dir, sink), session_id=_SESSION_ID)
    runner = Runner(handler, on_event=sink.append)

    with agent.override(model=_text_model("never reached")):
        await runner.submit("go", InputIntent.STEER)
        runner.abort()  # cooperative abort while pinned before the first leg
        await runner.wait_idle()

    roots = _chat_turn_roots(active_tracing.exporter.exported_spans_as_dict())
    assert len(roots) == 1, "the root span must be closed exactly once on abort"
    assert roots[0]["attributes"]["thread_id"] == _SESSION_ID
    finished = [e for e in sink if isinstance(e, events.TurnFinished)]
    assert finished and finished[-1].aborted is True  # sanity: the abort path really ran


async def test_exception_mid_leg_closes_the_root_span_exactly_once(active_tracing, tmp_path):
    """A model failure mid-leg closes the ``chat_turn`` span exactly once and surfaces as AgentError.

    The third spec-named unwind path (normal return / abort / EXCEPTION) through the REAL stack: the
    raise unwinds ``logfire.span.__exit__`` (closing the root) *before* the runner's ``except
    Exception`` maps it to an ``AgentError``. A captured span is one that ENDED, so exactly one
    exported ``chat_turn`` proves it closed once and never leaked; the error still surfaces unchanged
    (neither swallowed nor altered by the span). Under the suite-wide ``filterwarnings=["error"]``.
    """
    working_dir = tmp_path / "ws"
    working_dir.mkdir()
    sink: list[events.Event] = []
    agent = build_agent()
    handler = AgentTurnHandler(agent, deps=_deps(working_dir, sink), session_id=_SESSION_ID)
    runner = Runner(handler, on_event=sink.append)

    with agent.override(model=_raising_model()):
        await _run_turn(runner, "make the model blow up")

    roots = _chat_turn_roots(active_tracing.exporter.exported_spans_as_dict())
    assert len(roots) == 1, "the root span must be closed exactly once on the exception unwind"
    assert roots[0]["attributes"]["thread_id"] == _SESSION_ID
    # The model failure surfaced as an AgentError carrying the original message — not swallowed.
    errors = [e for e in sink if isinstance(e, events.AgentError)]
    assert errors and errors[-1].message == _BOOM
    # An error, not an abort: the turn finished with aborted False (distinguishes it from AC6).
    finished = [e for e in sink if isinstance(e, events.TurnFinished)]
    assert finished and finished[-1].aborted is False


async def test_inactive_turn_emits_zero_spans(capfire, tmp_path):  # noqa: F811
    """AC7: with tracing OFF (no key, not instrumented), a full turn emits ZERO spans.

    No ``active_tracing`` fixture: ``tracing._active`` stays False (so ``root_span`` is a nullcontext)
    and pydantic-ai is never instrumented — exactly production with no ``OPIK_API_KEY``. capfire still
    gives an exporter, so the assertion observes the *absence* of any span.
    """
    assert tracing._active is False
    working_dir = tmp_path / "ws"
    working_dir.mkdir()
    (working_dir / _READ_TARGET).write_text(_READ_CONTENTS, encoding="utf-8")
    sink: list[events.Event] = []
    agent = build_agent()
    handler = AgentTurnHandler(agent, deps=_deps(working_dir, sink), session_id=_SESSION_ID)
    runner = Runner(handler, on_event=sink.append)

    with agent.override(model=_read_model()):
        await _run_turn(runner, "read the notes file")

    assert capfire.exporter.exported_spans_as_dict() == [], "an inactive turn must emit no spans"
