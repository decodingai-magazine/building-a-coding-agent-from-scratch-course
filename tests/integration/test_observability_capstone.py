"""Opik-observability capstone (ADR-0014): one turn's whole span tree through the REAL stack.

Proves the integrated M10 tracing story (the per-AC span assertions live in
test_opik_repl_trace / test_opik_headless_trace): real build_agent (incl. the set-once
subagent-spawn seam), real Runner + AgentTurnHandler + gate + the per-turn ``root_span``,
real render_event on every event, real SessionLog (its session_id IS the Opik thread id),
and the one GLOBAL ``instrument_pydantic_ai``. Swapped/faked: one scripted FunctionModel
drives the parent turn AND the subagent children; capfire's in-memory TestExporter stands in
for the OTLP→Opik exporter; ``tracing._active`` is forced True with a fake opik_api_key
(init_tracing's real ``logfire.configure`` would replace capfire's exporter). The hermetic
tests pin: child spans nest in the parent turn trace with child tokens visible (the flagship,
ADR-0013 §9); one chat_turn tree with usage; in-turn compaction nests under the turn root;
no key → zero spans + a byte-identical event stream. Offline vs live: the hermetic slice
always runs with no network/key; only test_live_opik_export_smoke is skipif-gated on
OPIK_API_KEY + GEMINI_API_KEY.
"""

from __future__ import annotations

import gc
import io
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from uuid import UUID

import logfire
import pytest
from logfire.testing import (
    CaptureLogfire,
    TestExporter,
    capfire,  # noqa: F401 — imported so pytest registers the in-memory fixture
)
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from pydantic import SecretStr
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from rich.console import Console

import decode.agent.factory as factory
from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.config.settings import Settings, settings
from decode.context.session_log import SessionLog
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import Runner
from decode.observability import tracing
from decode.observability.tracing import reset_tracing
from decode.permissions.gate import PermissionGate
from decode.tools.agent import AGENT_TOOL_NAME
from decode.tui import render
from decode.tui.app import InputIntent

_SESSION_ID = "00000000-0000-0000-0000-0000000000e5"
_READ_TARGET = "notes.txt"
_READ_CONTENTS = "remember to trace this turn"
# Markers the scripted model streams, so the assertions read as a transcript.
_PARENT_FINAL = "fan-out complete"
_CHILD_REPORT = "explore-subagent report"


def _configured_key(field: str) -> str:
    """A real provider key (env / ``.env``), snapshotted at import — before the autouse scrubbers.

    The rootdir ``_no_real_provider_key`` / ``_no_opik_tracing`` fixtures blank ``gemini_api_key`` /
    ``opik_api_key`` on the settings singleton for every test, so the live smoke cannot read them at
    run time. We snapshot here at collection (before any fixture runs) from a fresh
    :class:`~decode.config.settings.Settings` — which reads the process env + ``.env`` exactly as the
    app does — and the live smoke re-injects them in its body (mirrors the M9 capstone).
    """
    return getattr(Settings(), field).get_secret_value()


_LIVE_OPIK_KEY = _configured_key("opik_api_key")
_LIVE_GEMINI_KEY = _configured_key("gemini_api_key")


# Fixtures — mirror the 092/093 span-test activation: capfire's in-memory exporter + a forced
# ``_active`` + direct global instrumentation, with an autouse save/restore so nothing leaks.


@pytest.fixture(autouse=True)
def _fake_gemini_key(mocker) -> None:
    """Let ``build_agent`` construct the Gemini provider offline (the model is always overridden)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )


@pytest.fixture(autouse=True)
def _isolate_tracing_state() -> Iterator[None]:
    """Save/restore the GLOBAL pydantic-ai instrumentation + the module flag, so nothing leaks.

    ``instrument_pydantic_ai`` mutates the process-global ``Agent._instrument_default``; without this
    an active-tracing test would leave every later test's agents instrumented (the no-op test would
    then see spans). Captured before any test body runs (autouse) and restored after — and this also
    unwinds the **live smoke**'s real ``init_tracing()`` activation (``reset_tracing`` clears the flag;
    the instrument default is restored) so the real global config never leaks into a later test.
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


# Scripted-model plumbing — one FunctionModel drives the streamed parent turn (``agent.iter``) AND
# the non-streamed subagent child (``agent.run``), from one ``function`` (adapted from the M9 capstone).


def _tool_returned(messages: list[ModelMessage], name: str) -> bool:
    """Whether ``messages`` already carries a tool-return for ``name`` (a completed tool call)."""
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == name
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


def _to_deltas(response: ModelResponse) -> AsyncIterator[object]:
    """Yield the streaming deltas for a non-streamed :class:`ModelResponse`.

    Each :class:`ToolCallPart` gets its OWN index so a single response carrying N tool calls streams
    as N distinct calls — this is what lets the parent emit an N-way ``agent(...)`` fan-out in one turn.
    """
    tool_index = 0
    for part in response.parts:
        if isinstance(part, TextPart):
            yield part.content
        elif isinstance(part, ToolCallPart):
            args = part.args if isinstance(part.args, str) else json.dumps(part.args)
            yield {tool_index: DeltaToolCall(name=part.tool_name, json_args=args)}
            tool_index += 1


def _function_model(function: Callable[..., ModelResponse]) -> FunctionModel:
    """A :class:`FunctionModel` serving BOTH the non-streamed child ``agent.run()`` and the streamed
    parent ``agent.iter`` from one ``function`` — the M9 dual-mode idiom (ADR-0013 §6).
    """

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        for delta in _to_deltas(function(messages, info)):
            yield delta

    return FunctionModel(function, stream_function=stream_function)


def _fan_out(n_children: int) -> ModelResponse:
    """The parent's first response: ONE ``agent(prompts=[…])`` call spawning ``n_children`` (ADR-0017 §1)."""
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=AGENT_TOOL_NAME,
                args={
                    "prompts": [
                        f"How does subsystem {i} of this repo work? Search the tree for its module "
                        f"and report its entry points with file:line evidence."
                        for i in range(n_children)
                    ]
                },
            )
        ]
    )


def _read_then_report(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Leg 1 calls the real ``read`` tool; the resume leg returns closing text (a one-tool turn)."""
    if not _tool_returned(messages, "read"):
        return ModelResponse(parts=[ToolCallPart(tool_name="read", args={"path": _READ_TARGET})])
    return ModelResponse(parts=[TextPart(content="i read the notes")])


def _fanout_then_children_glob(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Parent fans out ONE ``agent(prompts=[…])`` call spawning two children; each runs a real ``glob``.

    The child leg produces a child ``chat`` (model) span AND a child ``running tool`` (glob) span, both
    of which must nest inside the parent turn's ``chat_turn`` trace (ADR-0013 §9). The parent context is
    told apart from a child by whether the ``agent`` tool is visible (only the ``build`` persona has it).
    """
    visible = {tool.name for tool in info.function_tools}
    if AGENT_TOOL_NAME in visible:  # PARENT context
        if not _tool_returned(messages, AGENT_TOOL_NAME):
            return _fan_out(2)
        return ModelResponse(parts=[TextPart(content=_PARENT_FINAL)])
    # CHILD context: run a real read-only glob, then hand back the report.
    if not _tool_returned(messages, "glob"):
        return ModelResponse(parts=[ToolCallPart(tool_name="glob", args={"pattern": "**/*.py"})])
    return ModelResponse(parts=[TextPart(content=_CHILD_REPORT)])


def _big_text_turn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """A single-leg turn whose text is huge — enough to trip the in-turn compaction trigger."""
    return ModelResponse(parts=[TextPart(content="keep working on the task " * 100)])


def _skeleton_summarizer() -> FunctionModel:
    """A non-streaming FunctionModel standing in for the in-turn compaction summarizer call."""

    async def fill(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(content="# Conversation summary\n\n## Goal\nMARKER\n")]
        )

    return FunctionModel(fill)


# Recording harness — a sink that renders every event through the REAL render_event (proving the
# path), plus deny-if-called resolvers a read-only turn/fan-out must never invoke (M9 idiom).


class _RecordingSink:
    """Records every event AND renders it through the real :func:`render_event` (proving the path).

    If any event kind were unhandled, ``render_event`` would raise and fail the turn — so routing the
    whole turn through here proves the render path end to end while tracing is active.
    """

    def __init__(self) -> None:
        self.events: list[events.Event] = []
        self._buffer = io.StringIO()
        self._console = Console(file=self._buffer, force_terminal=False, width=100)

    def __call__(self, event: events.Event) -> None:
        self.events.append(event)
        self._console.print(render.render_event(event))

    def permission_events(self) -> list[events.PermissionRequested]:
        return [e for e in self.events if isinstance(e, events.PermissionRequested)]

    def type_sequence(self) -> list[str]:
        """The ordered event *types* — a deterministic, timestamp-free behavior fingerprint."""
        return [type(e).__name__ for e in self.events]

    @property
    def rendered(self) -> str:
        return self._buffer.getvalue()


class _RecordingResolvers:
    """The turn's decision resolvers — which a read-only turn / fan-out must never invoke.

    A read-only turn (``read`` / ``glob`` / ``agent`` are all READ_ONLY) never routes an ``ASK`` to a
    human, so both stay empty. They deny / return a sentinel if ever called so a regression fails loudly.
    """

    def __init__(self) -> None:
        self.permission_requests: list[PermissionRequest] = []
        self.questions: list[str] = []

    async def resolve_permission(self, request: PermissionRequest) -> PermissionDecision:
        self.permission_requests.append(request)
        return PermissionDecision.deny(reason="a read-only traced turn must never prompt")

    async def resolve_user_question(self, question: str) -> str:
        self.questions.append(question)
        return "a read-only traced turn must never ask"


def _deps(sink: _RecordingSink, resolvers: _RecordingResolvers, cwd: Path) -> AgentDeps:
    """Turn deps: the default (``build``) persona, which grants the ``agent`` tool."""
    return AgentDeps(
        cwd=cwd,
        emit=sink,
        gate=PermissionGate(),
        resolve_permission=resolvers.resolve_permission,
        resolve_user_question=resolvers.resolve_user_question,
    )


async def _run_turn(runner: Runner, prompt: str) -> None:
    """Submit one prompt and drive the runner to idle (one whole turn)."""
    await runner.submit(prompt, InputIntent.STEER)
    await runner.wait_idle()


# Span selectors + tree helpers


def _chat_turn_roots(spans: list[dict]) -> list[dict]:
    return [s for s in spans if s["name"] == "chat_turn"]


def _model_spans(spans: list[dict]) -> list[dict]:
    """The pydantic-ai model-request (``chat <model>``) spans — NOT the ``chat_turn`` root."""
    return [s for s in spans if s["name"].startswith("chat ")]


def _tool_spans(spans: list[dict]) -> list[dict]:
    return [s for s in spans if s["name"] == "running tool"]


def _by_span_id(spans: list[dict]) -> dict[int, dict]:
    return {s["context"]["span_id"]: s for s in spans}


def _descends_through_tool(span: dict, by_id: dict[int, dict]) -> bool:
    """True when ``span``'s ANCESTOR chain passes through a ``running tool`` span.

    A subagent child's spans (its ``agent run`` / ``chat`` / ``running tool`` legs) all sit *under* the
    parent's ``running tool`` span for the ``agent`` tool call, so they descend through a tool; the
    parent turn's own ``chat`` / ``running tool`` spans sit directly under the parent ``agent run`` and
    do not. This is how a child LLM/tool span is told apart from a parent one, structurally.
    """
    parent = span["parent"]
    while parent is not None:
        node = by_id.get(parent["span_id"])
        if node is None:
            return False
        if node["name"] == "running tool":
            return True
        parent = node["parent"]
    return False


# 1. THE FLAGSHIP — subagent child spans nest in the parent turn trace, child tokens visible (§9).


async def test_subagent_child_spans_nest_in_the_parent_turn_trace_with_child_token_usage(
    active_tracing, tmp_path
):
    """A parent turn's ``agent(...)`` fan-out nests the child model+tool spans in the turn trace (§9).

    Closes ADR-0013 §9 ("child token spend invisible until Opik lands (M10)") — the one assertion no
    other file makes. The parent turn fans out two ``agent(...)`` calls; each child runs a real
    read-only ``glob`` then reports. Because the children re-enter the SAME instrumented agent in the
    parent's task/contextvars (``asyncio.create_task`` copies the OTel context), their ``agent.run()``
    model AND tool spans nest INSIDE the parent turn's ONE ``chat_turn`` trace, and the child's own
    ``gen_ai.usage.*`` tokens ride the child LLM span — visible at last.
    """
    (tmp_path / "alpha.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (tmp_path / "beta.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    sink = _RecordingSink()
    resolvers = _RecordingResolvers()
    agent = build_agent()  # installs the set-once subagent-spawn seam (set_main_agent)
    handler = AgentTurnHandler(agent, deps=_deps(sink, resolvers, tmp_path), session_id=_SESSION_ID)
    runner = Runner(handler, on_event=sink)

    with agent.override(model=_function_model(_fanout_then_children_glob)):
        await _run_turn(runner, "explore two areas in parallel")

    # The fan-out completed and never prompted (read-only spawn auto-allows inline).
    assert _PARENT_FINAL in sink.rendered
    assert sink.permission_events() == []
    assert resolvers.permission_requests == []

    spans = active_tracing.exporter.exported_spans_as_dict()
    roots = _chat_turn_roots(spans)
    assert len(roots) == 1, [s["name"] for s in spans]
    root = roots[0]
    assert root["parent"] is None
    assert root["attributes"]["thread_id"] == _SESSION_ID
    trace_id = root["context"]["trace_id"]

    by_id = _by_span_id(spans)
    # The child spans: the ``chat`` (model) and ``running tool`` (glob) legs that descend through the
    # parent's ``agent`` tool span. Both kinds must be present — this is "child model/tool spans".
    child_model_spans = [s for s in _model_spans(spans) if _descends_through_tool(s, by_id)]
    child_tool_spans = [s for s in _tool_spans(spans) if _descends_through_tool(s, by_id)]
    assert child_model_spans, [s["name"] for s in spans]
    assert child_tool_spans, "each child's real glob must produce a nested 'running tool' span"

    # NESTING CLOSURE: every child span lives in the parent turn's ONE trace and is not a root itself.
    for span in child_model_spans + child_tool_spans:
        assert span["context"]["trace_id"] == trace_id
        assert span["parent"] is not None

    # THE §9 PAYOFF: the CHILD's own token usage is visible on the child LLM span (> 0).
    child_input_tokens = [
        s["attributes"].get("gen_ai.usage.input_tokens") for s in child_model_spans
    ]
    assert any(tokens and tokens > 0 for tokens in child_input_tokens), child_input_tokens

    # Sanity: the parent turn also has its OWN model spans (not misclassified as child ones).
    parent_model_spans = [s for s in _model_spans(spans) if not _descends_through_tool(s, by_id)]
    assert parent_model_spans, "the parent turn's own model legs must remain parent-scoped"


# 2. The integrated living-doc tree — one chat_turn root, nested spans, tokens (ADR-0014 §4).


async def test_full_turn_is_one_chat_turn_tree_with_nested_spans_and_usage(
    active_tracing, tmp_path
):
    """One turn → one ``chat_turn`` tree: root (thread_id = session id) → nested chat/tool spans + usage.

    The milestone-level restatement (one test, not the seven per-AC ones in 092): a real ``read`` turn
    through ``build_agent`` + ``Runner`` + ``AgentTurnHandler`` + the gate + the REAL ``SessionLog`` +
    ``render_event`` on every event. Exactly one ``chat_turn`` root carries the session id as its Opik
    ``thread_id``; the pydantic-ai ``chat`` + ``running tool`` spans nest under it; a leaf model span
    carries ``gen_ai.usage.input_tokens`` > 0. The session log's id IS the thread id (production wiring).
    """
    working_dir = tmp_path / "ws"
    working_dir.mkdir()
    (working_dir / _READ_TARGET).write_text(_READ_CONTENTS, encoding="utf-8")
    log = SessionLog.create(
        tmp_path / "sessions",
        cwd=working_dir,
        session_id=UUID(_SESSION_ID),
    )
    sink = _RecordingSink()
    resolvers = _RecordingResolvers()
    agent = build_agent()
    handler = AgentTurnHandler(
        agent,
        deps=_deps(sink, resolvers, working_dir),
        session_log=log,
        session_id=log.session_id,  # exactly how run_app wires the Opik thread id (ADR-0014 §4)
    )
    runner = Runner(handler, on_event=sink)

    with agent.override(model=_function_model(_read_then_report)):
        await _run_turn(runner, "read the notes file")

    spans = active_tracing.exporter.exported_spans_as_dict()
    roots = _chat_turn_roots(spans)
    assert len(roots) == 1, [s["name"] for s in spans]
    root = roots[0]
    assert root["parent"] is None, "the chat_turn span must be the trace root"
    assert root["attributes"]["thread_id"] == log.session_id == _SESSION_ID
    trace_id = root["context"]["trace_id"]

    model_spans = _model_spans(spans)
    tool_spans = _tool_spans(spans)
    assert model_spans, [s["name"] for s in spans]
    assert tool_spans, "the read tool must produce a 'running tool' span"
    for span in model_spans + tool_spans:
        assert span["context"]["trace_id"] == trace_id
        assert span["parent"] is not None

    input_tokens = [s["attributes"].get("gen_ai.usage.input_tokens") for s in model_spans]
    assert any(tokens and tokens > 0 for tokens in input_tokens), input_tokens
    # The render path handled every event of a traced turn without raising.
    assert "i read the notes" in sink.rendered


# 3. In-turn compaction rides free — the summarizer call nests under the turn root (ADR-0014 §4).


async def test_in_turn_compaction_nests_under_the_turn_root(active_tracing, tmp_path, monkeypatch):
    """A compaction summarizer call fired at the would-stop boundary nests under the turn's root span.

    Compaction is a free-rider: the same GLOBAL ``instrument_pydantic_ai`` traces its summarizer
    ``agent.run`` with no wiring, and because it fires inside ``AgentTurnHandler.__call__`` it nests
    under the turn's ``chat_turn`` root (ADR-0014 §4). Forces full compaction with a tiny window (the
    092 setup): ``window=60`` → full level ``int(60*0.80)=48`` ≤ the ~50-token FunctionModel estimate.
    """
    monkeypatch.setattr("decode.agent.loop.settings.compaction_context_window_tokens", 60)
    monkeypatch.setattr("decode.agent.loop.settings.compaction_keep_recent_tokens", 10)
    working_dir = tmp_path / "ws"
    working_dir.mkdir()
    sink = _RecordingSink()
    resolvers = _RecordingResolvers()
    agent = build_agent()
    handler = AgentTurnHandler(
        agent,
        deps=_deps(sink, resolvers, working_dir),
        session_id=_SESSION_ID,
        message_history=[
            ModelRequest(parts=[UserPromptPart(content="earlier")]),
            ModelResponse(parts=[TextPart(content="earlier answer")]),
        ],
        compaction_model=_skeleton_summarizer(),
    )
    runner = Runner(handler, on_event=sink)

    with agent.override(model=_function_model(_big_text_turn)):
        await _run_turn(runner, "keep working on the task " * 100)

    # Compaction actually fired inside the turn.
    assert [e for e in sink.events if isinstance(e, events.ContextCompacted)], (
        "compaction must fire"
    )

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


# 4. No-op proof — no key → init_tracing() False + ZERO spans + a byte-identical event stream (§1).


async def test_untraced_turn_is_a_noop_zero_spans_and_byte_identical_events(capfire, tmp_path):  # noqa: F811
    """No key → the real ``init_tracing()`` returns ``False`` and the identical turn emits ZERO spans.

    Mutation-proofs the activation guard (ADR-0014 §1): the ``if not key: return False`` presence check
    in ``init_tracing``. If it were dropped, ``init_tracing()`` would configure + instrument + return
    ``True`` (failing the ``is False`` assert) AND the untraced run would emit spans (failing the
    ``== []`` assert). The same read turn is then run with tracing ON, and the two event streams are
    compared: tracing adds spans but leaves the emitted event stream **byte-identical** — decode behaves
    the same whether or not ``OPIK_API_KEY`` is set.
    """
    # No ``active_tracing`` fixture: no key (the autouse conftest blanked it), nothing instrumented.
    assert tracing._active is False, "tracing state leaked from a prior test — isolation is broken"
    working_dir = tmp_path / "ws"
    working_dir.mkdir()
    (working_dir / _READ_TARGET).write_text(_READ_CONTENTS, encoding="utf-8")

    # THE GUARD: the real init_tracing() with no key builds nothing, instruments nothing, returns False.
    assert tracing.init_tracing() is False
    assert tracing.is_tracing_active() is False

    off_sink = _RecordingSink()
    off_resolvers = _RecordingResolvers()
    off_agent = build_agent()
    off_handler = AgentTurnHandler(
        off_agent, deps=_deps(off_sink, off_resolvers, working_dir), session_id=_SESSION_ID
    )
    off_runner = Runner(off_handler, on_event=off_sink)
    with off_agent.override(model=_function_model(_read_then_report)):
        await _run_turn(off_runner, "read the notes file")

    # ZERO spans while tracing is off — even though capfire supplies an exporter to observe with.
    assert capfire.exporter.exported_spans_as_dict() == [], "an untraced turn must emit no spans"
    assert (
        "i read the notes" in off_sink.rendered
    )  # the turn still ran fully (byte-identical behavior)

    # Now run the IDENTICAL turn with tracing ON and confirm the event stream is unchanged.
    logfire.instrument_pydantic_ai()
    tracing._active = True
    on_sink = _RecordingSink()
    on_resolvers = _RecordingResolvers()
    on_agent = build_agent()
    on_handler = AgentTurnHandler(
        on_agent, deps=_deps(on_sink, on_resolvers, working_dir), session_id=_SESSION_ID
    )
    on_runner = Runner(on_handler, on_event=on_sink)
    with on_agent.override(model=_function_model(_read_then_report)):
        await _run_turn(on_runner, "read the notes file")

    # Tracing on ⇒ spans now appear (proving the ON path is live) …
    assert _chat_turn_roots(capfire.exporter.exported_spans_as_dict()), (
        "the ON turn must emit spans"
    )
    # … yet the emitted event stream is byte-identical to the untraced run (tracing is transparent).
    assert on_sink.type_sequence() == off_sink.type_sequence()


# 5. Live Opik export smoke — ONE real Gemini turn + real Opik export; SKIPPED without both keys.


@pytest.mark.filterwarnings("ignore::ResourceWarning")
@pytest.mark.skipif(
    not (_LIVE_OPIK_KEY and _LIVE_GEMINI_KEY),
    reason="OPIK_API_KEY and GEMINI_API_KEY must both be set for the live Opik export smoke",
)
async def test_live_opik_export_smoke(monkeypatch, caplog, tmp_path):
    """ONE REAL Gemini turn exported to REAL Opik — presence only (export shipped; priceable attrs ride).

    The single non-hermetic proof: the real :func:`decode.observability.init_tracing` configures the
    real OTLP→Opik exporter, one short Gemini turn runs, and ``logfire.force_flush()`` pushes the batch
    to Opik. Correctness is Opik's server side, so this asserts only what is **locally visible**:

    * **export succeeded** — no OTLP-exporter ERROR was logged during the flush (a bad key / unreachable
      Opik logs one). ``logfire.force_flush()``'s bool is recorded but NOT asserted True: logfire's
      internal ``CheckSuppressInstrumentationProcessorWrapper.force_flush()`` returns ``False`` even on a
      clean flush, so the return value is not a reliable success signal — the no-error log is.
    * **priceable attributes ride the real Gemini span** — ``gen_ai.usage.*`` tokens (> 0) plus the model
      identity (``gen_ai.request.model`` / ``gen_ai.system``) Opik needs to price the call. Per the
      task's out-of-scope we do NOT hit the Opik API to assert cost: **cost appears in the Opik UI for
      priced Gemini models** (tokens-only is acceptable for open models — ADR-0014 §8). An in-memory
      ``TestExporter`` is tapped onto the same provider purely to read those attributes locally.

    Kept to ONE turn for cost hygiene (``flow_mode`` and its keep-alive-free client died with the
    Durable Flow's per-call event loops — ADR-0019 §1). The autouse
    ``_isolate_tracing_state`` (plus a best-effort ``reset_tracing`` here) unwinds the real activation so
    the global config never leaks into a later test.
    """
    monkeypatch.setattr(settings, "opik_api_key", SecretStr(_LIVE_OPIK_KEY), raising=False)
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(_LIVE_GEMINI_KEY))
    monkeypatch.setattr(factory.settings, "gemini_api_key", SecretStr(_LIVE_GEMINI_KEY))
    monkeypatch.setattr(settings, "llm_provider", "gemini")

    # REAL activation — the real OTLP→Opik exporter is configured (returns True with a key present).
    assert tracing.init_tracing() is True

    # Tap the SAME provider with an in-memory exporter so the Gemini span's attributes are readable
    # locally (the real export ships to Opik; we assert attributes here, never via the Opik API).
    tap = TestExporter()
    provider = logfire.DEFAULT_LOGFIRE_INSTANCE.config.get_tracer_provider()
    provider.add_span_processor(SimpleSpanProcessor(tap))

    sink = _RecordingSink()
    resolvers = _RecordingResolvers()
    agent = build_agent()  # real Gemini
    handler = AgentTurnHandler(agent, deps=_deps(sink, resolvers, tmp_path), session_id=_SESSION_ID)
    runner = Runner(handler, on_event=sink)

    with caplog.at_level(logging.ERROR):
        await _run_turn(runner, "Reply with exactly the single word: traced.")
        flushed = (
            logfire.force_flush()
        )  # push the batch to Opik; bool recorded, not asserted (quirk)

    # EXPORT SUCCEEDED: no OTLP exporter error logged during the run + flush (bad key / down → error).
    otlp_errors = [
        r
        for r in caplog.records
        if r.name.startswith("opentelemetry.exporter") and r.levelno >= logging.ERROR
    ]
    assert not otlp_errors, [r.getMessage() for r in otlp_errors]

    # PRICEABLE ATTRS on the REAL Gemini leaf span (tokens + model identity Opik prices server-side).
    chat_spans = _model_spans(tap.exported_spans_as_dict())
    assert chat_spans, "the real Gemini turn must emit at least one 'chat' model span"
    leaf = chat_spans[-1]
    assert leaf["attributes"].get("gen_ai.usage.input_tokens", 0) > 0, leaf["attributes"]
    assert leaf["attributes"].get("gen_ai.request.model"), (
        "model identity is needed for Opik pricing"
    )
    assert leaf["attributes"].get("gen_ai.system"), "provider identity is needed for Opik pricing"

    logging.getLogger(__name__).info("live Opik smoke: force_flush()=%s (not asserted)", flushed)
    reset_tracing()  # best-effort: the autouse fixture also unwinds the real activation
    gc.collect()  # finalize any straggler within this test's scope (belt-and-braces with keep-alive-off)
