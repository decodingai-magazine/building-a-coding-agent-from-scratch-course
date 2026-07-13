"""Unit tests for the ``agent`` fan-out tool + the in-process Explore-subagent runner (ADR-0017).

Covers the structural guards (empty list / width cap), the harness ``asyncio.gather`` fan-out, the
labelled aggregation, the shared per-child byte budget, per-child failure isolation, plus the
ADR-0013 invariants re-pinned under the new shape: the set-once main-Agent seam, fresh narrowed
read-only child deps, no usage threading, the ``UsageLimits`` cap, the concurrency semaphore,
persona grants, and kitaru-free imports. Direct tests use a hand-built :class:`RunContext` / stub
main agent; loop-driven tests ride the real ``build_agent`` + ``AgentTurnHandler`` with one scripted
:class:`FunctionModel` driving BOTH parent and child (``override`` is contextvar-scoped), branching
on whether the ``agent`` tool is visible. No network anywhere.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.usage import RunUsage

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.config.settings import settings
from decode.entities import events
from decode.harness.runner import Boundary, TurnContext
from decode.permissions.gate import PermissionGate
from decode.permissions.types import ToolKind
from decode.tools import KNOWN_TOOL_NAMES, tool_kind
from decode.tools import agent as agent_module
from decode.tools.agent import AGENT_TOOL_NAME
from decode.tools.registry import TOOL_SPECS

# Well-formed exploration prompts (question + scope + expected report content) — the shape the
# hardened tool description asks for, so these survive the substance guard task 104 adds later.
_PROMPT_A = (
    "How does the permission gate decide allow/ask/deny? Search src/decode/permissions/ and report "
    "the decision path with file:line evidence."
)
_PROMPT_B = (
    "How does the sandbox executor seam dispatch bash? Search src/decode/sandbox/ and report the "
    "backends and their entry points with file:line evidence."
)
_PROMPT_C = (
    "How is tool output truncated before it reaches the model? Search src/decode/tools/ and report "
    "the caps and where they are applied, with file:line evidence."
)


def _prompts(n: int) -> list[str]:
    """``n`` distinct well-formed exploration prompts (each carries question + scope + report ask)."""
    return [
        f"How does subsystem {i} of decode work? Search src/decode/ for its module and report its "
        f"entry points and call flow with file:line evidence."
        for i in range(n)
    ]


# direct-call harness


async def _direct_deny_permission(request):
    return None  # never consulted by the read-only tool; identity is what tests assert


async def _direct_no_user(question: str) -> str:
    raise AssertionError("the agent tool must not ask the user a question")


def _tool_ctx(cwd: Path, *, usage: RunUsage | None = None) -> RunContext[AgentDeps]:
    """A hand-built :class:`RunContext` whose deps stand in for the PARENT run's deps."""
    deps = AgentDeps(
        cwd=cwd,
        emit=lambda _e: None,  # type: ignore[arg-type]
        gate=PermissionGate(),
        resolve_permission=_direct_deny_permission,  # type: ignore[arg-type]
        resolve_user_question=_direct_no_user,
    )
    return RunContext(deps=deps, model=None, usage=usage, tool_call_approved=False)  # type: ignore[arg-type]


# seam: name + signature + set-once + require-when-unset


def test_agent_tool_name_is_stable():
    assert agent_module.AGENT_TOOL_NAME == "agent"


def test_agent_takes_ctx_and_prompts_only():
    import inspect

    # ADR-0017 §1: ONE call carries the whole fan-out — a list of prompts, one per Explore child.
    params = list(inspect.signature(agent_module.agent).parameters)
    assert params == ["ctx", "prompts"]


def test_require_main_agent_raises_a_clear_error_when_unset():
    # A misconfiguration (build_agent never called set_main_agent) must be a clear error, not a
    # None dereference — like bash's executor seam.
    agent_module.reset_main_agent()
    with pytest.raises(RuntimeError, match="set_main_agent"):
        agent_module._require_main_agent()


def test_set_main_agent_installs_the_seam(mocker):
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    built = build_agent()
    agent_module.set_main_agent(built)

    assert agent_module._require_main_agent() is built


# registry: kind + known names


def test_agent_is_registered_as_a_read_only_spec():
    by_name = {spec.name: spec for spec in TOOL_SPECS}
    assert "agent" in by_name
    # READ_ONLY → runs inline, never prompts, auto-allows in every mode (ADR-0013 §5).
    assert by_name["agent"].kind is ToolKind.READ_ONLY
    assert by_name["agent"].func is agent_module.agent


def test_agent_registers_with_a_raised_retry_budget(mocker):
    """The ``agent`` tool must register with ``retries >= 2`` (ADR-0017 §3).

    pydantic-ai's per-tool retry budget defaults to 1, so two consecutive ``ModelRetry`` nags (the
    width cap, then a substance nag) would abort the whole run with ``UnexpectedModelBehavior``
    instead of coaching the model. Pinned on the spec AND on the tool as registered on the Agent.
    """
    by_name = {spec.name: spec for spec in TOOL_SPECS}
    assert by_name["agent"].retries is not None
    assert by_name["agent"].retries >= 2

    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    built = build_agent()
    assert built._function_toolset.tools["agent"].max_retries == by_name["agent"].retries


def test_agent_is_a_known_tool_name():
    assert "agent" in KNOWN_TOOL_NAMES


def test_tool_kind_agent_is_read_only():
    from decode.tools.registry import TOOL_KIND

    assert TOOL_KIND["agent"] is ToolKind.READ_ONLY
    assert tool_kind("agent") is ToolKind.READ_ONLY


def test_build_agent_registers_agent_and_sets_the_seam(mocker):
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    built = build_agent()

    assert "agent" in built._function_toolset.tools
    # The factory wired the seam after registration (ADR-0013 §6): the tool can spawn from it.
    assert agent_module._require_main_agent() is built


# persona grants: build / plan / code-reviewer YES, explore NO


def test_primary_agents_grant_agent_tool_and_explore_never_does():
    # ADR-0013 §4: the ``agent`` tool is granted to every PRIMARY persona, never to the explore
    # subagent (recursion default-deny — a child cannot spawn a child).
    from decode.agents.loader import load_builtin_agents

    agents = load_builtin_agents()
    assert set(agents) == {"build", "plan", "explore", "code-reviewer"}
    for name in ("build", "plan", "code-reviewer"):
        assert "agent" in agents[name].tools, f"{name} must grant the agent tool (ADR-0013 §4)"
    assert "agent" not in agents["explore"].tools  # never — recursion default-deny


# loop-driven harness: one FunctionModel drives parent AND child


@pytest.fixture
def agent(mocker):
    """A real `decode` agent built with a dummy key (never used: tests override the model).

    ``build_agent`` also installs it as the subagent-spawn seam (``set_main_agent``), so the nested
    child ``agent.run()`` re-enters THIS object under the same ``override(model=…)`` context.
    """
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    return build_agent()


def _loop_deps(emit, *, gate: PermissionGate, cwd: Path) -> AgentDeps:
    return AgentDeps(
        cwd=cwd,
        emit=emit,
        gate=gate,
        resolve_permission=_direct_deny_permission,  # type: ignore[arg-type]
        resolve_user_question=_direct_no_user,
    )


def _tool_returned(messages: list[ModelMessage], name: str) -> bool:
    """Whether ``messages`` already carries a tool-return for ``name`` (a completed tool call)."""
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name == name
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


def _to_deltas(response: ModelResponse) -> AsyncIterator[object]:
    """Yield the streaming deltas for a non-streamed :class:`ModelResponse` (text / tool-call deltas)."""
    for part in response.parts:
        if isinstance(part, TextPart):
            yield part.content
        elif isinstance(part, ToolCallPart):
            args = part.args if isinstance(part.args, str) else json.dumps(part.args)
            yield {0: DeltaToolCall(name=part.tool_name, json_args=args)}


def _model(function) -> FunctionModel:
    """A :class:`FunctionModel` serving BOTH the non-streamed child ``agent.run()`` and the streamed
    parent ``agent.iter``: ``function`` is the single source of truth, the stream form is derived."""

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        for delta in _to_deltas(function(messages, info)):
            yield delta

    return FunctionModel(function, stream_function=stream_function)


def _fanout_model(child_fn, *, spawn_prompts=(_PROMPT_A,), parent_final="DONE"):
    """A :class:`FunctionModel` driving the PARENT (ONE fan-out call, then finish) and the CHILDREN.

    It branches on whether the ``agent`` tool is visible: the parent sees it — it emits ONE
    ``agent(prompts=[…])`` call on its first request (the harness spawns the children), then returns
    text; a child does NOT (``prepare=`` hid it), so control falls to the ``child_fn`` behavior the
    individual test scripts. One model object drives both because ``agent.override(model=…)`` is
    contextvar-scoped (ADR-0013 §6, ADR-0017 §1).
    """

    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        visible = {t.name for t in info.function_tools}
        if AGENT_TOOL_NAME in visible:  # PARENT context
            if not _tool_returned(messages, AGENT_TOOL_NAME):
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name=AGENT_TOOL_NAME, args={"prompts": list(spawn_prompts)}
                        )
                    ]
                )
            return ModelResponse(parts=[TextPart(content=parent_final)])
        return child_fn(messages, info)  # CHILD context

    return _model(function)


async def _drive(handler: AgentTurnHandler, ctx: TurnContext) -> None:
    """Drive the handler generator to completion, draining nothing at each boundary."""
    agen = handler(ctx)
    boundary = await agen.asend(None)
    while True:
        assert isinstance(boundary, Boundary)
        try:
            boundary = await agen.asend([])
        except StopAsyncIteration:
            break
    await agen.aclose()


def _tool_return_strings(handler: AgentTurnHandler) -> list[str]:
    return [
        str(part.content)
        for message in handler.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def _child_returns_text(text: str):
    """A CHILD-context behavior (used inside :func:`_fanout_model`) that returns ``text`` as output."""
    return lambda messages, info: ModelResponse(parts=[TextPart(content=text)])


def _child_model(text: str) -> FunctionModel:
    """A standalone model for a direct (parent-less) child run: it always returns ``text`` as output."""
    return _model(lambda messages, info: ModelResponse(parts=[TextPart(content=text)]))


async def test_spawn_through_the_loop_folds_the_child_report_and_never_prompts(agent, tmp_path):
    """A parent ``agent(...)`` spawn runs inline: the child report folds back, no permission prompt."""
    emitted: list[events.Event] = []
    deps = _loop_deps(emitted.append, gate=PermissionGate(), cwd=tmp_path)
    handler = AgentTurnHandler(agent, deps=deps)

    ctx = TurnContext(0, "explore the repo", emitted.append)
    with agent.override(model=_fanout_model(_child_returns_text("CHILD-REPORT"))):
        await _drive(handler, ctx)

    # The child's final text folded back as the ``agent`` tool result...
    returns = _tool_return_strings(handler)
    assert any("CHILD-REPORT" in r for r in returns)
    # ...with NO permission prompt: the agent tool is READ_ONLY and runs inline (ADR-0013 §5).
    assert not [e for e in emitted if isinstance(e, events.PermissionRequested)]


async def test_spawn_builds_fresh_narrowed_read_only_child_deps(agent, tmp_path):
    """The runner constructs FRESH deps: distinct gate + task_store, silent emit, explore persona."""
    captured: dict[str, object] = {}
    real_run = agent.run

    async def spy_run(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return await real_run(prompt, **kwargs)

    agent.run = spy_run  # only the CHILD spawn goes through agent.run; the parent uses agent.iter

    emitted: list[events.Event] = []
    parent_deps = _loop_deps(emitted.append, gate=PermissionGate(), cwd=tmp_path)
    handler = AgentTurnHandler(agent, deps=parent_deps)
    ctx = TurnContext(0, "explore the repo", emitted.append)
    with agent.override(model=_fanout_model(_child_returns_text("CHILD-REPORT"))):
        await _drive(handler, ctx)

    kwargs = captured["kwargs"]
    child_deps = kwargs["deps"]
    assert isinstance(child_deps, AgentDeps)
    # Fresh, isolated collaborators — never the parent's mutable gate / task_store (ADR-0013 §5).
    assert child_deps.gate is not parent_deps.gate
    assert child_deps.task_store is not parent_deps.task_store
    assert child_deps.task_store == []
    # The fresh gate is in BYPASS: a plain ``agent.run()`` has no loop to resolve a deferred approval,
    # so read-only tools must run inline (never raise ApprovalRequired) — ADR-0013 §2,5.
    from decode.permissions.types import PermissionMode

    assert child_deps.gate.mode is PermissionMode.BYPASS
    # Silent in the TUI (ADR-0013 §8): the child's emit is the module no-op sink, not the parent's.
    assert child_deps.emit is agent_module._silent_emit
    assert child_deps.emit is not parent_deps.emit
    # The child IS the explore subagent; scope (cwd / harness_home) is inherited from the parent.
    assert child_deps.active_agent.name == "explore"
    assert child_deps.active_agent.subagent is True
    assert child_deps.cwd == parent_deps.cwd
    assert child_deps.harness_home == parent_deps.harness_home
    # Bounded by the request cap, and WITHOUT threading the parent usage (ADR-0013 §7,10).
    assert kwargs["usage_limits"].request_limit == settings.subagent_max_requests
    assert "usage" not in kwargs


async def test_child_run_does_not_thread_parent_usage(agent, tmp_path):
    """No ``usage=ctx.usage``: the child's requests/tokens stay out of the parent's gauge (§7,10)."""
    parent_usage = RunUsage()
    ctx = _tool_ctx(tmp_path, usage=parent_usage)

    with agent.override(model=_child_model("REPORT")):
        out = await agent_module.agent(ctx, [_PROMPT_A])

    assert "REPORT" in out
    # The child made a model request, but it was NOT folded into the passed-in parent usage.
    assert parent_usage.requests == 0
    assert parent_usage.input_tokens == 0


async def test_child_toolset_is_exactly_read_glob_grep_lsp(agent, tmp_path):
    """Recursion is impossible: the child sees exactly {read, glob, grep, lsp} — no ``agent``."""
    seen: list[set[str]] = []

    def capture(messages, info):
        seen.append({t.name for t in info.function_tools})
        return ModelResponse(parts=[TextPart(content="CHILD-REPORT")])

    emitted: list[events.Event] = []
    deps = _loop_deps(emitted.append, gate=PermissionGate(), cwd=tmp_path)
    handler = AgentTurnHandler(agent, deps=deps)
    ctx = TurnContext(0, "explore the repo", emitted.append)
    with agent.override(model=_fanout_model(capture)):
        await _drive(handler, ctx)

    assert seen, "the child leg must have run"
    assert seen[0] == {"read", "glob", "grep", "lsp"}
    assert AGENT_TOOL_NAME not in seen[0]  # the agent tool is hidden from the child (no recursion)


async def test_child_read_only_tool_runs_without_touching_any_resolver(agent, tmp_path, mocker):
    """A child ``glob`` executes with NO gate / Decision-Channel involvement (deny resolvers unused)."""
    (tmp_path / "sample.py").write_text("x = 1\n", encoding="utf-8")

    deny_perm = mocker.patch.object(agent_module, "_deny_permission_resolver")
    deny_user = mocker.patch("decode.tools.askuser.deny_user_question_resolver")

    def child_globs_then_reports(messages, info):
        if not _tool_returned(messages, "glob"):
            return ModelResponse(
                parts=[ToolCallPart(tool_name="glob", args={"pattern": "**/*.py"})]
            )
        return ModelResponse(parts=[TextPart(content="CHILD-REPORT")])

    emitted: list[events.Event] = []
    deps = _loop_deps(emitted.append, gate=PermissionGate(), cwd=tmp_path)
    handler = AgentTurnHandler(agent, deps=deps)
    ctx = TurnContext(0, "explore the repo", emitted.append)
    with agent.override(model=_fanout_model(child_globs_then_reports)):
        await _drive(handler, ctx)

    # The child read-only tool executed and the report folded back...
    assert any("CHILD-REPORT" in r for r in _tool_return_strings(handler))
    # ...and neither deny resolver was ever invoked: a read-only child never reaches the gate.
    deny_perm.assert_not_called()
    deny_user.assert_not_called()


# direct: the structural guards (deterministic, pre-spawn — ADR-0017 §2)


async def test_empty_prompts_raises_model_retry_before_any_child_spawns(tmp_path, mocker):
    """``prompts=[]`` → ``ModelRetry`` naming the fix, and NOT ONE child is spawned."""
    spawn = mocker.patch.object(agent_module, "_require_main_agent")
    ctx = _tool_ctx(tmp_path)

    with pytest.raises(ModelRetry, match="at least one"):
        await agent_module.agent(ctx, [])

    spawn.assert_not_called()  # the guard fires BEFORE the fan-out


async def test_more_than_six_prompts_raises_model_retry_telling_the_model_to_consolidate(
    tmp_path, mocker
):
    """A 7-wide fan-out → ``ModelRetry`` asking the model to consolidate; no child spawns (§2)."""
    spawn = mocker.patch.object(agent_module, "_require_main_agent")
    ctx = _tool_ctx(tmp_path)

    with pytest.raises(ModelRetry, match=r"(?i)consolidate"):
        await agent_module.agent(ctx, _prompts(agent_module.MAX_FANOUT_PROMPTS + 1))

    spawn.assert_not_called()


async def test_the_width_cap_is_six_and_six_prompts_pass_the_guard(tmp_path, mocker):
    """The cap is a module constant (6): exactly six prompts fan out — six labelled sections back."""
    assert agent_module.MAX_FANOUT_PROMPTS == 6
    mocker.patch.object(agent_module, "_require_main_agent", return_value=_StubAgent(_report("ok")))
    ctx = _tool_ctx(tmp_path)

    out = await agent_module.agent(ctx, _prompts(6))

    assert len(_sections(out)) == 6


# direct: the labelled aggregation (ADR-0017 §5)


async def test_one_prompt_folds_one_labelled_section(tmp_path, mocker):
    """A one-element list is a single-child exploration: exactly one ``## Subagent 1 — "…"`` section."""
    mocker.patch.object(
        agent_module, "_require_main_agent", return_value=_StubAgent(_report("REPORT-A"))
    )
    ctx = _tool_ctx(tmp_path)

    out = await agent_module.agent(ctx, [_PROMPT_A])

    assert out.startswith(f'## Subagent 1 — "{_PROMPT_A}"')
    assert _sections(out) == [(_PROMPT_A, "REPORT-A")]


async def test_n_prompts_fold_n_sections_in_prompt_order_each_headed_by_its_own_prompt(
    tmp_path, mocker
):
    """Sections are 1-based, in PROMPT order, each heading carrying its own prompt verbatim (§5)."""
    prompts = [_PROMPT_A, _PROMPT_B, _PROMPT_C]
    mocker.patch.object(
        agent_module, "_require_main_agent", return_value=_EchoAgent()
    )  # child report = its own prompt
    ctx = _tool_ctx(tmp_path)

    out = await agent_module.agent(ctx, prompts)

    assert _sections(out) == [(p, f"report for: {p}") for p in prompts]
    assert '## Subagent 1 — "' in out and '## Subagent 3 — "' in out


async def test_duplicate_prompts_are_not_deduped(tmp_path, mocker):
    """Two identical prompts → two children → two sections (dedupe is a prompt-quality issue, §2)."""
    tracker = _ConcurrencyTracker()
    mocker.patch.object(agent_module, "_require_main_agent", return_value=tracker)
    ctx = _tool_ctx(tmp_path)

    out = await agent_module.agent(ctx, [_PROMPT_A, _PROMPT_A])

    assert _sections(out) == [(_PROMPT_A, "report"), (_PROMPT_A, "report")]
    assert tracker.spawns == 2


# direct: the shared context budget (ADR-0017 §6)


async def test_each_child_report_is_truncated_to_the_shared_byte_budget(
    tmp_path, mocker, monkeypatch
):
    """Each child's report is capped to ``subagent_result_max_bytes // len(prompts)`` — total stays flat."""
    monkeypatch.setattr(settings, "subagent_result_max_bytes", 300, raising=False)
    big = "\n".join(f"finding number {i}" for i in range(500))  # many short lines, way over the cap
    mocker.patch.object(agent_module, "_require_main_agent", return_value=_StubAgent(_report(big)))
    prompts = [_PROMPT_A, _PROMPT_B, _PROMPT_C]
    ctx = _tool_ctx(tmp_path)

    out = await agent_module.agent(ctx, prompts)

    per_child = 300 // len(prompts)
    bodies = [body for _prompt, body in _sections(out)]
    assert len(bodies) == 3
    for body in bodies:
        assert len(body.encode("utf-8")) <= per_child  # the shared budget, split evenly
        assert body != big
        assert big.startswith(body)  # the kept head is a line-aligned prefix of the report


async def test_a_single_child_still_gets_the_whole_byte_budget(tmp_path, mocker, monkeypatch):
    """One prompt → the divisor is 1, so a lone child keeps the full ``subagent_result_max_bytes``."""
    monkeypatch.setattr(settings, "subagent_result_max_bytes", 100, raising=False)
    big = "\n".join(f"finding number {i}" for i in range(500))
    mocker.patch.object(agent_module, "_require_main_agent", return_value=_StubAgent(_report(big)))
    ctx = _tool_ctx(tmp_path)

    out = await agent_module.agent(ctx, [_PROMPT_A])

    body = _sections(out)[0][1]
    assert 0 < len(body.encode("utf-8")) <= 100
    assert big.startswith(body)


# direct: per-child failure isolation (ADR-0017 §5) + the defensive deferred case


async def test_a_child_that_raises_gets_a_failure_note_and_its_siblings_still_fold(
    tmp_path, mocker, caplog
):
    """A raising child NEVER discards its siblings: its section carries a failure note, theirs survive."""
    from pydantic_ai.exceptions import UsageLimitExceeded

    class _OneChildExplodes:
        async def run(self, prompt, *, deps, usage_limits):
            if prompt == _PROMPT_B:
                raise UsageLimitExceeded("the request limit was exceeded")
            return _report(f"report for: {prompt}")

    mocker.patch.object(agent_module, "_require_main_agent", return_value=_OneChildExplodes())
    ctx = _tool_ctx(tmp_path)

    with caplog.at_level("WARNING", logger="decode.tools.agent"):
        out = await agent_module.agent(ctx, [_PROMPT_A, _PROMPT_B, _PROMPT_C])

    sections = _sections(out)
    assert len(sections) == 3
    # The failed angle is honest about failing — and named, so the parent model can re-plan.
    assert sections[1][0] == _PROMPT_B
    assert "failed" in sections[1][1].lower()
    # ...while both siblings' reports folded intact (no exception escaped the tool).
    assert sections[0][1] == f"report for: {_PROMPT_A}"
    assert sections[2][1] == f"report for: {_PROMPT_C}"
    assert "UsageLimitExceeded" in caplog.text  # the exception was logged, not swallowed silently


async def test_deferred_tool_requests_output_returns_a_fallback_note(tmp_path, mocker):
    """Defensive: a ``DeferredToolRequests`` child output → a short note as that child's section (§8)."""
    from pydantic_ai import DeferredToolRequests

    stub = SimpleNamespace(output=DeferredToolRequests())
    mocker.patch.object(agent_module, "_require_main_agent", return_value=_StubAgent(stub))
    ctx = _tool_ctx(tmp_path)

    out = await agent_module.agent(ctx, [_PROMPT_A])

    body = _sections(out)[0][1]
    assert "could not complete" in body.lower()


# stand-in main agents + the section parser


def _report(text: str) -> SimpleNamespace:
    """A stand-in ``AgentRunResult`` whose ``output`` is ``text`` (what the runner reads)."""
    return SimpleNamespace(output=text)


class _StubAgent:
    """A stand-in main agent handing every child the SAME canned result."""

    def __init__(self, result):
        self._result = result

    async def run(self, prompt, *, deps, usage_limits):
        return self._result


class _EchoAgent:
    """A stand-in main agent whose child report echoes its own spawn prompt (proves section pairing)."""

    async def run(self, prompt, *, deps, usage_limits):
        await asyncio.sleep(0)  # yield, so a broken (sequential) gather still can't reorder results
        return _report(f"report for: {prompt}")


_SECTION_RE = re.compile(r'^## Subagent (\d+) — "(.*)"$', re.MULTILINE)


def _sections(aggregate: str) -> list[tuple[str, str]]:
    """Parse the labelled aggregate into ``(prompt, body)`` pairs — the model-facing contract (§5).

    Also asserts the headings are 1-based and contiguous, so a mis-numbered fold fails loudly here.
    """
    matches = list(_SECTION_RE.finditer(aggregate))
    assert [m.group(1) for m in matches] == [str(i) for i in range(1, len(matches) + 1)], aggregate
    bounds = [m.start() for m in matches] + [len(aggregate)]
    return [
        (match.group(2), aggregate[match.end() : bounds[i + 1]].strip())
        for i, match in enumerate(matches)
    ]


# direct: the concurrency semaphore


class _ConcurrencyTracker:
    """A stand-in main agent whose ``run`` records the peak number of concurrent children."""

    def __init__(self) -> None:
        self.live = 0
        self.max_concurrent = 0
        self.spawns = 0

    async def run(self, prompt, *, deps, usage_limits):
        self.live += 1
        self.spawns += 1
        self.max_concurrent = max(self.max_concurrent, self.live)
        await asyncio.sleep(0.05)  # hold the slot so overlap is observable
        self.live -= 1
        return _report("report")


async def test_semaphore_bounds_concurrent_children(mocker, monkeypatch, tmp_path):
    """With ``subagent_max_parallel=2`` at most two of a 6-wide fan-out's children run at once.

    The width cap (6) and the concurrency ceiling are DIFFERENT limits (ADR-0017 §2): one call may
    carry six prompts, but the per-loop semaphore — acquired per child ATTEMPT — still admits only
    ``subagent_max_parallel`` at a time (here 2, then 2, then 2).
    """
    monkeypatch.setattr(settings, "subagent_max_parallel", 2, raising=False)
    agent_module._reset_semaphores()  # rebuild the per-loop semaphore at the patched size
    tracker = _ConcurrencyTracker()
    mocker.patch.object(agent_module, "_require_main_agent", return_value=tracker)
    ctx = _tool_ctx(tmp_path)

    out = await agent_module.agent(ctx, _prompts(6))

    assert tracker.spawns == 6
    assert len(_sections(out)) == 6
    assert tracker.max_concurrent <= settings.subagent_max_parallel
    assert tracker.max_concurrent == 2  # concurrency reached — but never exceeded — the cap


# import hygiene: the agent tool + the cli stay kitaru-free


def test_importing_the_agent_tool_and_cli_stays_kitaru_free():
    """Importing the ``agent`` tool (and the REPL cli) must not pull in kitaru (ADR-0013; ADR-0008)."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import decode.tools.agent, decode.cli, sys; assert 'kitaru' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
