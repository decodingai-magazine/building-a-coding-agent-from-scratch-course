"""Explore-subagents capstone (ADR-0013, ADR-0017): parallel fan-out through the FULL real stack.

Proves the ``agent`` tool's N-way fan-out end to end — ONE ``agent(prompts=[…])`` call, N
concurrent children, one labelled aggregate: real build_agent (per-agent tool narrowing + the
set-once subagent-spawn seam), real ``agent`` tool + in-process runner (harness gather, per-loop
semaphore, fresh BYPASS child deps, per-child UsageLimits, shared-budget truncate fold), real
Runner + AgentTurnHandler + gate, real render_event on every event (silent-until-done
asserted), real SessionLog persist + ``--resume`` replay (child transcripts proven
ephemeral), and real child read/glob/grep against a tmp_path tree. Swapped/faked: one
scripted FunctionModel drives parent AND children (``agent.override`` is contextvar-scoped,
so it covers the child's nested run); GEMINI_API_KEY is faked so build_agent constructs.
The hermetic tests pin: bounded genuine overlap, permission-free children, byte-cap
truncation, parent-only usage gauge, recursion default-deny, output validation (a
zero-tool-call child retried once with the nudge, then noted — ADR-0017 §7), ephemeral
transcripts + resume,
and the headless cache-disable contract (bash only, never agent — kitaru-skipif). Offline vs
live: everything runs with no network/key except test_live_gemini_fanout_smoke, skipif-gated
on GEMINI_API_KEY.
"""

from __future__ import annotations

import asyncio
import gc
import inspect
import io
import json
import re
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import SecretStr
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
from decode.context import session_log
from decode.context.session_log import SessionLog
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import Runner
from decode.permissions.gate import PermissionGate
from decode.tools import agent as agent_module
from decode.tools.agent import AGENT_TOOL_NAME, SYNTHESIS_FOOTER
from decode.tui import render

# Markers the scripted model streams, so the assertions read as a transcript.
_PARENT_FINAL = "fan-out complete"
_CHILD_REPORT = "explore-subagent report"


# Is the durable runtime importable (kitaru + the local ZenML stack)? Mirrors the runtime capstone's
# probe (``test_runtime_capstone.py``): the headless contract-pin test imports ``runtime.flow`` (which
# pulls in kitaru), so when an environment cannot host the runtime that ONE test SKIPS rather than
# fails. The always-run hermetic slice above never imports kitaru.
try:  # pragma: no cover - import-time capability probe
    import kitaru as _kitaru  # noqa: F401
    import zenml.client as _zenml_client  # noqa: F401

    _RUNTIME_IMPORTABLE = True
except Exception:  # pragma: no cover - only on an incompatible environment
    _RUNTIME_IMPORTABLE = False


def _configured_gemini_key() -> str:
    """The real ``GEMINI_API_KEY`` (env / ``.env``), captured at import — before the autouse scrub.

    The rootdir ``_no_real_provider_key`` fixture deletes the env var and blanks the settings singleton
    for every test, so the live smoke cannot read the key at run time. We snapshot it here at collection
    (before any fixture runs) from a fresh :class:`~decode.config.settings.Settings` — which reads the
    process env and ``.env`` exactly as the app does — and the live smoke re-injects it in its body.
    """
    return Settings().gemini_api_key.get_secret_value()


_LIVE_GEMINI_KEY = _configured_gemini_key()


# Fixtures


@pytest.fixture
def _fake_gemini_key(mocker):
    """Let ``build_agent`` construct the Gemini provider offline (the model is always overridden)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )


@pytest.fixture
def parent_agent(_fake_gemini_key):
    """The REAL ``decode`` agent (build persona by default), wired with the subagent-spawn seam.

    ``build_agent`` installs this object via ``set_main_agent`` (ADR-0013 §6), so the nested child
    ``agent.run()`` re-enters THIS agent under the same ``override(model=…)`` context — one scripted
    :class:`FunctionModel` therefore drives parent AND children.
    """
    return build_agent()


# Scripted-model plumbing — one FunctionModel drives the streamed parent AND the non-streamed child.


def _tool_returned(messages: list[ModelMessage], name: str) -> bool:
    """Whether ``messages`` already carries a tool-return for ``name`` (a completed tool call)."""
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == name
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


def _first_user_prompt(messages: list[ModelMessage]) -> str:
    """The first user-prompt string in ``messages`` — how a child branches on its spawn prompt."""
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    return str(part.content)
    return ""


def _to_deltas(response: ModelResponse) -> AsyncIterator[object]:
    """Yield the streaming deltas for a non-streamed :class:`ModelResponse`.

    Each :class:`ToolCallPart` gets its OWN index so a single response carrying N tool calls streams
    as N distinct calls — this is what lets the parent emit an N-way fan-out in one turn.
    """
    tool_index = 0
    for part in response.parts:
        if isinstance(part, TextPart):
            yield part.content
        elif isinstance(part, ToolCallPart):
            args = part.args if isinstance(part.args, str) else json.dumps(part.args)
            yield {tool_index: DeltaToolCall(name=part.tool_name, json_args=args)}
            tool_index += 1


def _function_model(function: Callable[..., object]) -> FunctionModel:
    """A :class:`FunctionModel` serving BOTH the non-streamed child ``agent.run()`` and the streamed
    parent ``agent.iter`` from one ``function`` (sync or async — the child rendezvous test needs async).
    """
    is_async = inspect.iscoroutinefunction(function)

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        response = await function(messages, info) if is_async else function(messages, info)
        for delta in _to_deltas(response):
            yield delta

    return FunctionModel(function, stream_function=stream_function)


def _child_prompt(index: int) -> str:
    """One well-formed exploration prompt: the question, the scope to search, the report to produce."""
    return (
        f"How does subsystem {index} of this repository work? Search the source tree for its module "
        f"and report its entry points and call flow with file:line evidence."
    )


def _fan_out(n_children: int) -> ModelResponse:
    """The parent's first response: ONE ``agent(prompts=[…])`` call spawning ``n_children`` (ADR-0017 §1).

    The fan-out no longer depends on the model volunteering N tool calls — one call carries the N
    angles and the HARNESS gathers the children.
    """
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=AGENT_TOOL_NAME,
                args={"prompts": [_child_prompt(i) for i in range(n_children)]},
            )
        ]
    )


_SECTION_RE = re.compile(r'^## Subagent \d+ — ".*"$', re.MULTILINE)


def _sections(aggregate: str) -> list[str]:
    """The ``## Subagent i — "…"`` section headings in one folded aggregate (ADR-0017 §5)."""
    return _SECTION_RE.findall(aggregate)


def _section_bodies(aggregate: str) -> list[str]:
    """Each section's body — what the per-child byte budget caps (ADR-0017 §6).

    The Synthesis Footer (§9) trails the last section but belongs to no child, so it is stripped
    first: it is harness overhead ON TOP of the shared budget, never a bite out of a child's share.
    """
    assert aggregate.endswith(SYNTHESIS_FOOTER), aggregate
    aggregate = aggregate[: -len(SYNTHESIS_FOOTER)]
    matches = list(_SECTION_RE.finditer(aggregate))
    bounds = [m.start() for m in matches] + [len(aggregate)]
    return [aggregate[m.end() : bounds[i + 1]].strip() for i, m in enumerate(matches)]


# Recording harness — a sink that renders every event through the REAL render_event, plus resolvers
# that must never be consulted (a read-only fan-out prompts for nothing).


class _RecordingSink:
    """Records every event AND renders it through the real :func:`render_event` (proving the path).

    If any event kind were unhandled, ``render_event`` would raise and fail the turn — so routing the
    whole turn through here proves the render path end to end, exactly like the M1 capstone.
    """

    def __init__(self) -> None:
        self.events: list[events.Event] = []
        self._buffer = io.StringIO()
        self._console = Console(file=self._buffer, force_terminal=False, width=100)

    def __call__(self, event: events.Event) -> None:
        self.events.append(event)
        self._console.print(render.render_event(event))

    def tool_call_names(self) -> set[str]:
        return {e.name for e in self.events if isinstance(e, events.ToolCallStarted)}

    def tool_result_names(self) -> set[str]:
        return {e.name for e in self.events if isinstance(e, events.ToolResult)}

    def tool_calls(self) -> list[events.ToolCallStarted]:
        return [e for e in self.events if isinstance(e, events.ToolCallStarted)]

    def permission_events(self) -> list[events.PermissionRequested]:
        return [e for e in self.events if isinstance(e, events.PermissionRequested)]

    @property
    def rendered(self) -> str:
        return self._buffer.getvalue()


class _RecordingResolvers:
    """The parent's decision resolvers — which a permission-free fan-out must never invoke.

    A read-only fan-out (``agent`` + children are all READ_ONLY) never routes an ``ASK`` to the human,
    so both of these stay empty. They deny / return a sentinel if ever called so a regression fails
    loudly instead of hanging.
    """

    def __init__(self) -> None:
        self.permission_requests: list[PermissionRequest] = []
        self.questions: list[str] = []

    async def resolve_permission(self, request: PermissionRequest) -> PermissionDecision:
        self.permission_requests.append(request)
        return PermissionDecision.deny(reason="a read-only subagent fan-out must never prompt")

    async def resolve_user_question(self, question: str) -> str:
        self.questions.append(question)
        return "a read-only subagent fan-out must never ask"


def _parent_deps(sink: _RecordingSink, resolvers: _RecordingResolvers, cwd: Path) -> AgentDeps:
    """The PARENT run's deps — the default (build) persona, which grants the ``agent`` tool."""
    return AgentDeps(
        cwd=cwd,
        emit=sink,
        gate=PermissionGate(),
        resolve_permission=resolvers.resolve_permission,
        resolve_user_question=resolvers.resolve_user_question,
    )


async def _run_turn(runner: Runner, prompt: str) -> None:
    """Submit one prompt and drive the runner to idle (one whole fan-out turn)."""
    from decode.tui.app import InputIntent

    await runner.submit(prompt, InputIntent.STEER)
    await runner.wait_idle()


def _folded_reports(handler: AgentTurnHandler) -> list[str]:
    """Every ``agent`` tool result folded back into the parent history (the children's reports)."""
    return [
        str(part.content)
        for message in handler.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == AGENT_TOOL_NAME
    ]


def _tool_calls_in_history(messages: list[ModelMessage], name: str) -> list[ToolCallPart]:
    """Every ``name`` tool CALL recorded in ``messages`` (used to prove no child transcript leaked)."""
    return [
        part
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart) and part.tool_name == name
    ]


# 1. Parallel fan-out — the N children genuinely overlap, bounded by ``subagent_max_parallel``.


async def test_parallel_fanout_overlaps_and_is_bounded_by_subagent_max_parallel(
    parent_agent, tmp_path, monkeypatch
):
    """ONE ``agent(prompts=[…])`` call fans out concurrently, capped by ``subagent_max_parallel``.

    The stronger ADR-0017 §1,4 claim: the parallelism is a HARNESS guarantee (the tool's own
    ``asyncio.gather``), not a model courtesy — the parent emits exactly ONE tool call and the
    children still overlap. Sets a low cap and one call carrying ``2 * cap`` prompts. Each child
    rendezvous at an :class:`asyncio.Barrier` sized to the cap: the barrier only trips when ``cap``
    children are *simultaneously* inside (a sequential fan-out would never gather ``cap`` waiters and
    would time out), while the per-loop semaphore — taken per child attempt — guarantees no more than
    ``cap`` are ever inside. So the observed peak equals the cap exactly. It also confirms the spawn
    is permission-free and silent-until-done in one shot: no ``PermissionRequested`` fires, and only
    the ``agent`` tool surfaces on the parent sink (the children run silent).
    """
    (tmp_path / "one.py").write_text(
        "x = 1\n", encoding="utf-8"
    )  # something for the children to read
    cap = 2
    n_children = 2 * cap  # a multiple of the cap so the barrier trips in clean waves (no deadlock)
    monkeypatch.setattr(settings, "subagent_max_parallel", cap)
    agent_module._reset_semaphores()  # rebuild the per-loop semaphore at the patched cap

    barrier = asyncio.Barrier(cap)
    concurrency = {"live": 0, "peak": 0}

    async def rendezvous() -> None:
        concurrency["live"] += 1
        concurrency["peak"] = max(concurrency["peak"], concurrency["live"])
        try:
            # Trips only when ``cap`` children are here together; a non-concurrent run would hang, so
            # bound it — a timeout is a clean failure (broken fan-out), never an infinite hang.
            await asyncio.wait_for(barrier.wait(), timeout=5.0)
        finally:
            concurrency["live"] -= 1

    async def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        visible = {tool.name for tool in info.function_tools}
        if AGENT_TOOL_NAME in visible:  # PARENT context
            if not _tool_returned(messages, AGENT_TOOL_NAME):
                return _fan_out(n_children)
            return ModelResponse(parts=[TextPart(content=_PARENT_FINAL)])
        # CHILD context: rendezvous (proves genuine overlap), read some code — a report backed by
        # ZERO tool calls is BAD and would be retried (ADR-0017 §7) — then hand back the report.
        if not _tool_returned(messages, "glob"):
            await rendezvous()
            return ModelResponse(
                parts=[ToolCallPart(tool_name="glob", args={"pattern": "**/*.py"})]
            )
        return ModelResponse(parts=[TextPart(content=_CHILD_REPORT)])

    sink = _RecordingSink()
    resolvers = _RecordingResolvers()
    handler = AgentTurnHandler(parent_agent, deps=_parent_deps(sink, resolvers, tmp_path))
    runner = Runner(handler, on_event=sink)
    with parent_agent.override(model=_function_model(model)):
        await _run_turn(runner, "explore several areas in parallel")

    # Genuine concurrency reached the cap — and the semaphore never let it exceed the cap.
    assert concurrency["peak"] == cap
    assert concurrency["peak"] <= settings.subagent_max_parallel
    # ONE aggregate folded back, carrying one labelled section per prompt (ADR-0017 §5).
    folded = _folded_reports(handler)
    assert len(folded) == 1
    aggregate = folded[0]
    assert len(_sections(aggregate)) == n_children
    assert aggregate.count(_CHILD_REPORT) == n_children
    # Permission-free: no prompt event, and the human resolver was never consulted (READ_ONLY inline).
    assert sink.permission_events() == []
    assert resolvers.permission_requests == []
    # Silent-until-done: ONLY the ``agent`` tool surfaced on the parent sink — no child event leaked.
    assert sink.tool_call_names() == {AGENT_TOOL_NAME}
    assert sink.tool_result_names() == {AGENT_TOOL_NAME}
    assert len(sink.tool_calls()) == 1  # ONE call did the whole fan-out (the harness gathered it)
    # The parent's final text streamed and rendered through the real renderer.
    assert _PARENT_FINAL in sink.rendered


# 2. Permission-free read-only children — real read/glob/grep, no resolver, reports fold back.


async def test_children_run_real_read_only_tools_without_touching_any_resolver(
    parent_agent, tmp_path, mocker
):
    """One call, three children each driving a DIFFERENT real read-only tool; no resolver; reports fold.

    Proves the ADR-0013 §5 "permissions come free" claim end to end: the ``agent`` tool auto-allows
    (inline, no prompt), and a child's real ``read`` / ``glob`` / ``grep`` runs under the fresh BYPASS
    gate — so it never raises ``ApprovalRequired``, never reaches the gate's human path, and never
    touches either deny resolver. Each child's final text folds back as the ``agent`` tool result, and
    the children's own tool events stay off the parent sink (silent-until-done, from the tool angle).
    """
    (tmp_path / "alpha.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (tmp_path / "beta.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hello world\n", encoding="utf-8")

    # A read-only child must reach NEITHER deny resolver (ADR-0013 §5): spy both and assert unused.
    deny_perm = mocker.patch.object(agent_module, "_deny_permission_resolver")
    deny_user = mocker.patch("decode.tools.askuser.deny_user_question_resolver")

    def _child_tool(prompt: str) -> tuple[str, ToolCallPart]:
        if "glob" in prompt:
            return "glob", ToolCallPart(tool_name="glob", args={"pattern": "**/*.py"})
        if "grep" in prompt:
            return "grep", ToolCallPart(
                tool_name="grep", args={"pattern": "def ", "glob": "**/*.py"}
            )
        return "read", ToolCallPart(tool_name="read", args={"path": "notes.txt"})

    child_prompts = [
        "Which Python modules exist here? Use glob over **/*.py and report every path you find.",
        "Where are functions defined? Use grep for 'def ' across **/*.py and report each hit with "
        "its file:line.",
        "What do the project notes say? Use read on notes.txt and report its contents verbatim.",
    ]

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        visible = {tool.name for tool in info.function_tools}
        if AGENT_TOOL_NAME in visible:  # PARENT: ONE call, one child per read-only tool
            if not _tool_returned(messages, AGENT_TOOL_NAME):
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name=AGENT_TOOL_NAME, args={"prompts": list(child_prompts)}
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart(content=_PARENT_FINAL)])
        # CHILD: run the assigned real read-only tool, then report which tool it used.
        tool_name, call = _child_tool(_first_user_prompt(messages))
        if not _tool_returned(messages, tool_name):
            return ModelResponse(parts=[call])
        return ModelResponse(parts=[TextPart(content=f"{_CHILD_REPORT} via {tool_name}")])

    sink = _RecordingSink()
    resolvers = _RecordingResolvers()
    handler = AgentTurnHandler(parent_agent, deps=_parent_deps(sink, resolvers, tmp_path))
    runner = Runner(handler, on_event=sink)
    with parent_agent.override(model=_function_model(model)):
        await _run_turn(runner, "explore three areas")

    # ONE aggregate folded back, carrying all three children's reports — one per read-only tool.
    folded = _folded_reports(handler)
    assert len(folded) == 1
    aggregate = folded[0]
    assert len(_sections(aggregate)) == 3
    for child_tool in ("glob", "grep", "read"):
        assert f"{_CHILD_REPORT} via {child_tool}" in aggregate
    # The real read-only tools ran — yet NO resolver (parent human, child perm-deny, child ask-deny)
    # was ever consulted, and no permission prompt surfaced.
    assert resolvers.permission_requests == []
    deny_perm.assert_not_called()
    deny_user.assert_not_called()
    assert sink.permission_events() == []
    # Silent-until-done: the children's glob/grep/read events never reached the parent sink.
    assert sink.tool_call_names() == {AGENT_TOOL_NAME}
    for child_tool in ("glob", "grep", "read"):
        assert child_tool not in sink.tool_call_names()
        assert child_tool not in sink.tool_result_names()


# 3. Result folding is truncated to the byte cap through the fold.


async def test_child_report_is_truncated_to_the_byte_cap_through_the_fold(
    parent_agent, tmp_path, monkeypatch
):
    """Two children SHARE the byte budget: each report is capped to ``max_bytes // N`` (ADR-0017 §6).

    Each child returns a report far over a small byte cap; the ``agent`` tool folds each through the
    shared ``truncate()`` idiom at the DIVIDED budget, so the fold's total cost is width-independent
    (~``subagent_result_max_bytes`` at any width) — capped at a line boundary, head preserved — and it
    renders through the real renderer.
    """
    (tmp_path / "one.py").write_text(
        "x = 1\n", encoding="utf-8"
    )  # something for the children to read
    monkeypatch.setattr(settings, "subagent_result_max_bytes", 128)
    n_children = 2
    per_child = 128 // n_children
    head = "explore-subagent HEAD line"
    long_report = head + "\n" + "\n".join(f"detail line {i}" for i in range(50))

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        visible = {tool.name for tool in info.function_tools}
        if AGENT_TOOL_NAME in visible:
            if not _tool_returned(messages, AGENT_TOOL_NAME):
                return _fan_out(n_children)
            return ModelResponse(parts=[TextPart(content=_PARENT_FINAL)])
        if not _tool_returned(messages, "glob"):  # read code first: a tool-less report is BAD (§7)
            return ModelResponse(
                parts=[ToolCallPart(tool_name="glob", args={"pattern": "**/*.py"})]
            )
        return ModelResponse(parts=[TextPart(content=long_report)])

    sink = _RecordingSink()
    resolvers = _RecordingResolvers()
    handler = AgentTurnHandler(parent_agent, deps=_parent_deps(sink, resolvers, tmp_path))
    runner = Runner(handler, on_event=sink)
    with parent_agent.override(model=_function_model(model)):
        await _run_turn(runner, "explore everything")

    folded = _folded_reports(handler)
    assert len(folded) == 1
    bodies = _section_bodies(folded[0])
    assert len(bodies) == n_children
    for body in bodies:
        assert (
            len(body.encode("utf-8")) <= per_child
        )  # the SHARED budget, split across the children
        assert head in body  # the line-aligned head survived
        assert body != long_report  # content was dropped
    assert head in sink.rendered  # and the truncated panel rendered through the real renderer


# 4. No usage threading — the parent gauge excludes the children's request/token counts.


async def test_parent_usage_gauge_excludes_child_counts(parent_agent, tmp_path):
    """No child spawn threads ``usage=ctx.usage`` — the parent's ``last_input_tokens`` stays parent-only.

    Spies the child-spawn seam (``agent.run``) and asserts no spawn carried a ``usage`` kwarg — the
    exact mechanism ADR-0013 §7 specifies. Because the child's usage object is never the parent's, the
    children's requests/tokens *cannot* enter the parent's ``run.usage()`` / ``last_input_tokens`` gauge
    (which the footer + compaction trigger read, §10); each child is bounded by the request cap instead.
    The children here each make an EXTRA model leg (glob → report), so a threaded run would visibly
    inflate the parent — it does not.
    """
    (tmp_path / "one.py").write_text("x = 1\n", encoding="utf-8")

    spawn_kwargs: list[dict[str, object]] = []
    real_run = parent_agent.run

    async def spy_run(prompt, **kwargs):
        spawn_kwargs.append(
            kwargs
        )  # only the CHILD spawn goes through agent.run (parent uses iter)
        return await real_run(prompt, **kwargs)

    parent_agent.run = spy_run

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        visible = {tool.name for tool in info.function_tools}
        if AGENT_TOOL_NAME in visible:
            if not _tool_returned(messages, AGENT_TOOL_NAME):
                return _fan_out(3)
            return ModelResponse(parts=[TextPart(content=_PARENT_FINAL)])
        if not _tool_returned(messages, "glob"):
            return ModelResponse(parts=[ToolCallPart(tool_name="glob", args={"pattern": "**/*"})])
        return ModelResponse(parts=[TextPart(content=_CHILD_REPORT)])

    sink = _RecordingSink()
    resolvers = _RecordingResolvers()
    handler = AgentTurnHandler(parent_agent, deps=_parent_deps(sink, resolvers, tmp_path))
    runner = Runner(handler, on_event=sink)
    with parent_agent.override(model=_function_model(model)):
        await _run_turn(runner, "explore three areas")

    assert len(spawn_kwargs) == 3  # three children were spawned
    # The mechanism (ADR-0013 §7): no spawn threaded the parent usage, and each was runaway-capped.
    assert all("usage" not in kwargs for kwargs in spawn_kwargs)
    assert all(
        kwargs["usage_limits"].request_limit == settings.subagent_max_requests
        for kwargs in spawn_kwargs
    )
    # The parent gauge is live and, by the above, measured ONLY the parent's own legs.
    assert handler.last_input_tokens > 0


# 5. Recursion default-deny — a child's visible toolset excludes ``agent``.


async def test_child_toolset_excludes_agent_recursion_default_deny(parent_agent, tmp_path):
    """A child sees exactly ``{read, glob, grep, lsp}`` — never ``agent`` — so it cannot spawn (§6).

    Recursion is structurally impossible with no depth counter: the child runs as ``active_agent=explore``,
    whose ``tools`` omit ``agent``, so ADR-0003 §6-7's ``prepare=`` hides it from the child's toolset.
    """
    (tmp_path / "one.py").write_text("x = 1\n", encoding="utf-8")
    seen: list[set[str]] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        visible = {tool.name for tool in info.function_tools}
        if AGENT_TOOL_NAME in visible:  # PARENT
            if not _tool_returned(messages, AGENT_TOOL_NAME):
                return _fan_out(1)
            return ModelResponse(parts=[TextPart(content=_PARENT_FINAL)])
        seen.append(visible)  # CHILD: capture what the child can see
        if not _tool_returned(messages, "glob"):  # read code first: a tool-less report is BAD (§7)
            return ModelResponse(
                parts=[ToolCallPart(tool_name="glob", args={"pattern": "**/*.py"})]
            )
        return ModelResponse(parts=[TextPart(content=_CHILD_REPORT)])

    sink = _RecordingSink()
    resolvers = _RecordingResolvers()
    handler = AgentTurnHandler(parent_agent, deps=_parent_deps(sink, resolvers, tmp_path))
    runner = Runner(handler, on_event=sink)
    with parent_agent.override(model=_function_model(model)):
        await _run_turn(runner, "explore")

    assert seen, "the child leg must have run"
    assert seen[0] == {"read", "glob", "grep", "lsp"}
    assert AGENT_TOOL_NAME not in seen[0]  # the agent tool is hidden from the child (no recursion)


# 5b. Output validation — a hallucinating child is retried ONCE, then noted; its sibling is untouched.


async def test_a_hallucinating_child_is_retried_once_then_noted_while_its_sibling_folds(
    parent_agent, tmp_path
):
    """One child answers from MEMORY (no tool call) → one nudged retry → the failure note (ADR-0017 §7).

    The full validate-retry-give-up arc through the real stack: the bad child's report is non-empty,
    so ONLY the zero-tool-call scan of the real ``AgentRunResult.all_messages()`` can catch it. It is
    spawned exactly twice — the second time with the harness's nudge appended — and then gives up with
    :data:`~decode.tools.agent._NO_USABLE_REPORT_NOTE`, while its healthy sibling (which really globs)
    folds its report intact, in prompt order. A broken child costs 2 spawns, never a runaway loop, and
    never its siblings' results.
    """
    (tmp_path / "one.py").write_text("x = 1\n", encoding="utf-8")
    hallucinated = "From memory, decode surely uses a gate."

    spawns: list[str] = []
    real_run = parent_agent.run

    async def spy_run(prompt, **kwargs):
        spawns.append(prompt)  # every child ATTEMPT (first try or retry) goes through here
        return await real_run(prompt, **kwargs)

    parent_agent.run = spy_run
    bad_prompt, good_prompt = _child_prompt(0), _child_prompt(1)

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        visible = {tool.name for tool in info.function_tools}
        if AGENT_TOOL_NAME in visible:  # PARENT: ONE call, two angles
            if not _tool_returned(messages, AGENT_TOOL_NAME):
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name=AGENT_TOOL_NAME, args={"prompts": [bad_prompt, good_prompt]}
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart(content=_PARENT_FINAL)])
        # CHILD: the first angle NEVER reads code (bad, twice over); the second one globs first.
        if _first_user_prompt(messages).startswith(bad_prompt):
            return ModelResponse(parts=[TextPart(content=hallucinated)])
        if not _tool_returned(messages, "glob"):
            return ModelResponse(
                parts=[ToolCallPart(tool_name="glob", args={"pattern": "**/*.py"})]
            )
        return ModelResponse(parts=[TextPart(content=_CHILD_REPORT)])

    sink = _RecordingSink()
    resolvers = _RecordingResolvers()
    handler = AgentTurnHandler(parent_agent, deps=_parent_deps(sink, resolvers, tmp_path))
    runner = Runner(handler, on_event=sink)
    with parent_agent.override(model=_function_model(model)):
        await _run_turn(runner, "explore two areas")

    # The bad angle was spawned EXACTLY twice — the retry carrying the original prompt + the nudge.
    bad_attempts = [p for p in spawns if p.startswith(bad_prompt)]
    assert len(bad_attempts) == 2
    assert bad_attempts[0] == bad_prompt
    assert bad_attempts[1] == bad_prompt + agent_module._RETRY_NUDGE
    # …while the healthy sibling ran exactly once (a retry is private to the bad child's slot).
    assert len([p for p in spawns if p.startswith(good_prompt)]) == 1

    folded = _folded_reports(handler)
    assert len(folded) == 1
    bodies = _section_bodies(folded[0])
    assert len(bodies) == 2  # section order stays PROMPT order
    assert (
        bodies[0] == agent_module._NO_USABLE_REPORT_NOTE
    )  # the give-up note, honest to the parent
    assert (
        hallucinated not in folded[0]
    )  # the memory-only answer never reaches the parent's context
    assert _CHILD_REPORT in bodies[1]  # the sibling's evidenced report folded intact


# 5c. The Synthesis Footer reaches the PARENT model's own context, on every aggregate.


async def test_the_synthesis_footer_reaches_the_parent_model_after_the_last_section(
    parent_agent, tmp_path
):
    """The footer is on the tool return the PARENT MODEL actually reads (ADR-0017 §9).

    Not merely on the string the tool returns: the proof runs through the real Runner, so what is
    asserted is the ``ToolReturnPart`` content in the message list handed BACK to the parent model on
    its next turn — the only place the instruction can do its job. It trails the last section (read
    last, applies to everything above it) and is appended to EVERY aggregate, one-child folds included.
    """
    (tmp_path / "one.py").write_text("x = 1\n", encoding="utf-8")
    parent_saw: list[str] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        visible = {tool.name for tool in info.function_tools}
        if AGENT_TOOL_NAME in visible:  # PARENT
            if not _tool_returned(messages, AGENT_TOOL_NAME):
                return _fan_out(1)  # a ONE-child fan-out: the footer is not conditional on width
            parent_saw.extend(
                part.content
                for message in messages
                for part in getattr(message, "parts", [])
                if isinstance(part, ToolReturnPart) and part.tool_name == AGENT_TOOL_NAME
            )
            return ModelResponse(parts=[TextPart(content=_PARENT_FINAL)])
        if not _tool_returned(messages, "glob"):  # CHILD: read code, then report
            return ModelResponse(
                parts=[ToolCallPart(tool_name="glob", args={"pattern": "**/*.py"})]
            )
        return ModelResponse(parts=[TextPart(content=_CHILD_REPORT)])

    sink = _RecordingSink()
    resolvers = _RecordingResolvers()
    handler = AgentTurnHandler(parent_agent, deps=_parent_deps(sink, resolvers, tmp_path))
    runner = Runner(handler, on_event=sink)
    with parent_agent.override(model=_function_model(model)):
        await _run_turn(runner, "explore one area")

    assert len(parent_saw) == 1
    aggregate = parent_saw[0]
    assert aggregate.endswith(SYNTHESIS_FOOTER)  # …after the last (here: only) section
    assert aggregate.index(_CHILD_REPORT) < aggregate.index(SYNTHESIS_FOOTER)
    assert aggregate.count(SYNTHESIS_FOOTER) == 1  # once per RESULT, never once per section
    assert _folded_reports(handler) == parent_saw  # the tool's return IS what the model read


# 6. Ephemeral child transcripts — the history + JSONL log carry only the spawn calls + summaries.


async def test_ephemeral_child_transcripts_survive_resume(parent_agent, tmp_path, monkeypatch):
    """The parent history + JSONL log carry only the ONE spawn call + its fold; ``--resume`` works.

    A child transcript (its glob call/return) is discarded — only each child's final text folds back.
    So the parent history and the session log carry the single ``agent`` fan-out call + its labelled
    aggregate and NOTHING from inside the children, and :func:`session_log.load` replays byte-for-byte
    into a fresh handler (ADR-0013 §8, ADR-0017 §1).
    """
    (tmp_path / "one.py").write_text("x = 1\n", encoding="utf-8")
    sessions_dir = tmp_path / "sessions"
    fixed_now = datetime(2026, 7, 5, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(session_log, "_utc_now", lambda: fixed_now)

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        visible = {tool.name for tool in info.function_tools}
        if AGENT_TOOL_NAME in visible:
            if not _tool_returned(messages, AGENT_TOOL_NAME):
                return _fan_out(2)
            return ModelResponse(parts=[TextPart(content=_PARENT_FINAL)])
        if not _tool_returned(messages, "glob"):
            return ModelResponse(
                parts=[ToolCallPart(tool_name="glob", args={"pattern": "**/*.py"})]
            )
        return ModelResponse(parts=[TextPart(content=_CHILD_REPORT)])

    log = SessionLog.create(
        sessions_dir,
        cwd=tmp_path,
        now=fixed_now,
        session_id=UUID("00000000-0000-0000-0000-0000000005a9"),
    )
    sink = _RecordingSink()
    resolvers = _RecordingResolvers()
    handler = AgentTurnHandler(
        parent_agent, deps=_parent_deps(sink, resolvers, tmp_path), session_log=log
    )
    runner = Runner(handler, on_event=sink)
    with parent_agent.override(model=_function_model(model)):
        await _run_turn(runner, "explore two areas")

    # The parent history carries ONE spawn call + its 2-section fold — but NO child transcript.
    folded = _folded_reports(handler)
    assert len(folded) == 1
    assert len(_sections(folded[0])) == 2
    assert len(_tool_calls_in_history(handler.message_history, AGENT_TOOL_NAME)) == 1
    assert (
        _tool_calls_in_history(handler.message_history, "glob") == []
    )  # child transcript discarded

    # The JSONL log replays byte-for-byte into a fresh handler (``--resume`` just works).
    replayed = session_log.load(log.path)
    assert replayed == handler.message_history
    fresh = AgentTurnHandler(
        parent_agent,
        deps=_parent_deps(_RecordingSink(), _RecordingResolvers(), tmp_path),
        message_history=replayed,
    )
    assert fresh.message_history == handler.message_history
    # The log carries no child tool calls either — child transcripts were never persisted.
    assert _tool_calls_in_history(replayed, "glob") == []
    assert session_log.load_latest(sessions_dir) == handler.message_history


# 7. Headless no-special-casing (contract pin) — the flow cache-disables ONLY bash, never agent.


@pytest.mark.filterwarnings("ignore:'crypt' is deprecated:DeprecationWarning")
@pytest.mark.filterwarnings("ignore:There is no current event loop:DeprecationWarning")
@pytest.mark.skipif(
    not _RUNTIME_IMPORTABLE,
    reason="the durable runtime (kitaru + zenml) is not importable in this environment",
)
def test_headless_flow_cache_disable_set_covers_only_bash_never_agent(monkeypatch):
    """The headless flow's replay-safety config cache-disables only ``bash`` — never ``agent`` (§9).

    A read-only child's summary is deterministic and side-effect-free, so its checkpoint is replay-safe
    under the default caching — unlike a sandbox ``bash`` (real shell side effects), which the flow
    cache-disables so a ``decode replay`` re-executes it (ADR-0011 §5). This pins that contract at the
    source of truth (``runtime/flow.py::_build_runtime_agent``) without booting a flow: it patches the
    ``KitaruAgent`` + ``build_agent`` seams to capture the kwargs and asserts the cache-disable set.
    """
    import decode.runtime.flow as flow_mod
    from decode.tools.bash import BASH_TOOL_NAME

    captured: dict[str, object] = {}

    class _SpyKitaruAgent:
        def __init__(self, agent, **kwargs):
            captured.clear()
            captured.update(kwargs)

    monkeypatch.setattr(flow_mod, "KitaruAgent", _SpyKitaruAgent)
    monkeypatch.setattr(flow_mod, "build_agent", lambda **kwargs: object())

    # ``none`` mode: nothing is cache-disabled — the default caching stands for every tool (incl. agent).
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", "none")
    flow_mod._build_runtime_agent()
    assert "tool_checkpoint_config_by_name" not in captured

    # A sandbox mode cache-disables ONLY ``bash`` — ``agent`` is never in the set (replay-safe summary).
    monkeypatch.setattr(flow_mod.settings, "sandbox_mode", "docker")
    flow_mod._build_runtime_agent()
    cache_disabled = captured["tool_checkpoint_config_by_name"]
    assert set(cache_disabled) == {BASH_TOOL_NAME}
    assert AGENT_TOOL_NAME not in cache_disabled


# 8. Live-Gemini fan-out smoke — one real fan-out; SKIPPED when GEMINI_API_KEY is unset.


@pytest.mark.filterwarnings("ignore::ResourceWarning")
@pytest.mark.skipif(
    not _LIVE_GEMINI_KEY,
    reason="GEMINI_API_KEY is unset — the live Gemini fan-out smoke is skipped",
)
async def test_live_gemini_fanout_smoke(monkeypatch):
    """One REAL Gemini fan-out over this repo — presence-only (children ran, reports back, no prompt).

    Re-injects the real key snapshotted at import (the autouse fixture scrubbed it), builds the REAL
    agent, and asks the ``build`` persona to spawn Explore subagents over two *named* source files of
    this repo in parallel. Asserts only *presence* — at least one child actually ran and its report
    folded back, and the read-only fan-out never prompted — not exact content (a live model is
    non-deterministic). Naming concrete, definitely-present files steers each child to a reliable
    ``read`` (a guessy ``glob`` can miss and abort the parallel leg — the children share the parent's
    fault domain, ADR-0013 §1). The ``flow_mode=True`` build hands Gemini a keep-alive-free HTTP client
    (``_flow_mode_http_client``) so no pooled socket lingers to trip ``filterwarnings=["error"]`` after
    the test; the fan-out mechanism (seam, semaphore, tool narrowing) is identical to the interactive
    build.
    """
    monkeypatch.setattr(factory.settings, "gemini_api_key", SecretStr(_LIVE_GEMINI_KEY))
    monkeypatch.setattr(factory.settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "subagent_max_requests", 10)  # bound each child's real model legs

    agent = build_agent(flow_mode=True)  # real Gemini, keep-alive-free client, + the spawn seam

    child_prompts: list[str] = []
    real_run = agent.run

    async def spy_run(prompt, **kwargs):
        child_prompts.append(prompt)  # each real explore child spawns through here
        return await real_run(prompt, **kwargs)

    agent.run = spy_run

    sink = _RecordingSink()
    resolvers = _RecordingResolvers()
    repo_root = Path(__file__).resolve().parents[2]  # read THIS repo, read-only
    handler = AgentTurnHandler(agent, deps=_parent_deps(sink, resolvers, repo_root))
    runner = Runner(handler, on_event=sink)

    prompt = (
        "Use the explore subagents (the `agent` tool) to investigate two files of this repository IN "
        "PARALLEL: make ONE `agent` call whose `prompts` list holds two prompts — the first asking a "
        "subagent to read and summarize `src/decode/tools/truncate.py`, the second asking a subagent "
        "to read and summarize `src/decode/config/settings.py`. Give each prompt a focused question "
        "that names its file, then combine the two summaries the tool returns. Explore only by reading "
        "the named files with the subagents; do not write files or run shell commands."
    )
    await _run_turn(runner, prompt)

    # Presence only: at least one explore child ran and its report folded back as an ``agent`` result.
    assert child_prompts, "the model must have spawned at least one explore subagent"
    assert _folded_reports(handler), (
        "at least one child report must fold back as an agent tool result"
    )
    # And the read-only fan-out never prompted for permission.
    assert sink.permission_events() == []
    assert resolvers.permission_requests == []

    gc.collect()  # finalize any straggler within this test's scope (belt-and-braces with keep-alive-off)
