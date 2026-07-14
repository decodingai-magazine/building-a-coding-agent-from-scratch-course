"""Unit tests for the ``agent`` fan-out tool + the in-process Explore-subagent runner (ADR-0017).

Covers the structural guards (empty list / width cap), the input contract (the hardened model-facing
tool description + the deterministic per-prompt substance guard, ADR-0017 §3), the harness
``asyncio.gather`` fan-out, the labelled aggregation, the shared per-child byte budget, per-child
failure isolation, the OUTPUT contract (the bad-report predicate + exactly one nudged retry,
ADR-0017 §7), plus the
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
    UserPromptPart,
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
from decode.tools.truncate import truncate

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


def _child_spawn_prompt(messages: list[ModelMessage]) -> str:
    """The prompt a CHILD was spawned with, read off its own transcript (how it spots the nudge)."""
    return next(
        (
            str(part.content)
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ),
        "",
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
    """A CHILD-context behavior returning ``text`` and calling NO tool — BAD by ADR-0017 §7-ii."""
    return lambda messages, info: ModelResponse(parts=[TextPart(content=text)])


def _tool_called(messages: list[ModelMessage], name: str) -> bool:
    """Whether the child already CALLED ``name`` (the exact scan the bad-report predicate makes)."""
    return any(
        isinstance(part, ToolCallPart) and part.tool_name == name
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
    )


def _child_globs_then_reports(text: str):
    """A CHILD-context behavior that READS CODE (a real ``glob``) and then reports ``text`` — GOOD.

    The report contract's floor (ADR-0017 §7-ii): a child that never called a tool answered from
    model memory, so every test wanting a *good* child must have it touch a read-only tool first.
    Branches on the glob CALL, not its return, so an empty ``tmp_path`` (``glob`` then raises
    ``ModelRetry``: "no files match") does not trap the child in a retry loop.
    """

    def child(messages, info):
        if not _tool_called(messages, "glob"):
            return ModelResponse(
                parts=[ToolCallPart(tool_name="glob", args={"pattern": "**/*.py"})]
            )
        return ModelResponse(parts=[TextPart(content=text)])

    return child


def _child_model(text: str) -> FunctionModel:
    """A standalone model for a direct (parent-less) child run: it globs, then reports ``text``."""
    return _model(_child_globs_then_reports(text))


async def test_spawn_through_the_loop_folds_the_child_report_and_never_prompts(agent, tmp_path):
    """A parent ``agent(...)`` spawn runs inline: the child report folds back, no permission prompt."""
    emitted: list[events.Event] = []
    deps = _loop_deps(emitted.append, gate=PermissionGate(), cwd=tmp_path)
    handler = AgentTurnHandler(agent, deps=deps)

    ctx = TurnContext(0, "explore the repo", emitted.append)
    with agent.override(model=_fanout_model(_child_globs_then_reports("CHILD-REPORT"))):
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
    with agent.override(model=_fanout_model(_child_globs_then_reports("CHILD-REPORT"))):
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
        return _child_globs_then_reports("CHILD-REPORT")(messages, info)

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


# the hardened tool description (ADR-0017 §3) — what the MODEL reads before it writes a prompt


def test_the_registered_tool_description_states_the_input_contract(mocker):
    """The model-facing description carries the per-prompt shape, the 3-angle push, and the cap.

    pydantic-ai lifts the function docstring into the tool schema, so this is literally what the
    parent model reads when deciding what to put in ``prompts`` — assert on the REGISTERED tool's
    description, not on the source (that is the artefact that reaches the model).
    """
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    built = build_agent()

    description = built._function_toolset.tools[AGENT_TOOL_NAME].description
    assert description
    lowered = description.lower()
    # (a) the per-prompt shape: question + scope + what the report must contain.
    assert "question" in lowered
    assert "scope" in lowered
    assert "report" in lowered
    # (b) the fan-out push, verbatim enough that a model cannot miss it (ADR-0017 §1,3).
    assert "3 DISTINCT angles" in description
    # (c) the one-element-list case and (d) the width cap of 6 — stated as a NUMBER, not as the
    # Python constant's name (the model cannot resolve ``MAX_FANOUT_PROMPTS``).
    assert "one-element list" in lowered
    assert str(agent_module.MAX_FANOUT_PROMPTS) in description
    assert "MAX_FANOUT_PROMPTS" not in description


# direct: the substance guard (deterministic, cheap, pre-spawn — ADR-0017 §3)

# Genuinely lazy prompts: too thin to brief anyone with. Every one MUST be rejected — this is the
# failure the guard exists to prevent. All of them fall under the substance floor.
_LAZY_PROMPTS = [
    "explore",
    "explore the repo",
    "look around",
    "the codebase",
    "go look at the code",
    "tell me about this project",
    "dig in",
]

# THE ANTI-WHACK-A-MOLE BATTERY: realistic, well-formed exploration briefs a parent model
# plausibly writes. EVERY ONE MUST PASS THE GUARD — a false reject is the dangerous failure mode
# (it burns a model turn and eats AGENT_TOOL_RETRIES), so any change that re-narrows the guard
# into a prompt GRADER instead of a substance FLOOR fails here, loudly. These are the phrasings two
# demolished keyword-grader designs actually rejected; ADR-0017 §3 tells that story once, and this
# battery is what stops the next edit from quietly re-narrowing the guard to fit one failing example.

# QA round 1 — imperative phrasings.
_ROUND_1_PROMPTS = [
    "Summarize the retry logic in src/decode/tools/agent.py including the ModelRetry budget and "
    "report the file:line for each check.",
    "List all Pydantic models defined under src/decode/entities/ and report their field names with "
    "file:line citations.",
    "Document the permission gate's allow/ask/deny branches in src/decode/permissions/ and report "
    "each decision with file:line evidence.",
    "Enumerate every tool registered in src/decode/tools/registry.py and report its name and kind "
    "with file:line evidence.",
    "Map the sandbox executor seam across src/decode/sandbox/ and report each backend's entry "
    "points with file:line evidence.",
]

# QA round 2 — the phrasings no widened word list ever caught ("note", "describe", "break down",
# "walk through", "dig into", "chart"): every widened list invites the next miss.
_ROUND_2_PROMPTS = [
    "Outline the tool registration flow in src/decode/tools/registry.py and note which module each "
    "tool lives in with file:line references.",
    "Where does the settings singleton get constructed? Search src/decode/config/settings.py and "
    "describe the lazy-init pattern used.",
    "Break down the retry budget for the bash tool across src/decode/tools/bash.py and produce a "
    "short summary of each guard clause.",
    "Walk through the sandbox handback flow under src/decode/sandbox/handback.py and cite the git "
    "commands issued, in order.",
    "Chart every Pydantic-AI tool registered by the harness, searching src/decode/tools/, and give "
    "me a table of tool name to file.",
    "Dig into the compaction trigger inside src/decode/context/ and tell me the token threshold, "
    "citing the exact line.",
    "Inspect how the TUI renders streaming tokens under src/decode/tui/ and report the render "
    "loop's entry point with file:line evidence.",
    "Confirm whether the memory loader in src/decode/memory/ reads AGENTS.md before MEMORY.md, and "
    "back up your finding with file:line detail.",
]

# More phrasings, spanning the styles a keyword guard is worst at. The no-punctuation one names no
# path, asks no "?", and uses no question/scope/report keyword — yet is a perfectly good brief.
_VARIED_PROMPTS = [
    # interrogative, no path token at all
    "Which module owns conversation compaction and what is the token threshold that triggers it?",
    # imperative, no punctuation, no path token, no interrogative, no report keyword
    "Tell me every place the harness shells out to git during hand back and quote the exact commands",
    # phrasal verb, no report keyword
    "Poke around the observability wiring in src/decode/observability/ and pin down where the Opik "
    "span is opened.",
    # terse but substantive — just over the floor
    "Trace the ASK path through src/decode/permissions/gate.py; cite line numbers.",
    # a declarative statement of need: no imperative verb, no question mark
    "I need the exact ordering of instructions injected into the system prompt, starting from "
    "src/decode/agent/factory.py.",
    # bare colloquial imperative
    "Go read the Kitaru runtime wiring under src/decode/runtime/ and tell me how a checkpoint is "
    "written.",
]

_WELL_FORMED_PROMPTS = [
    _PROMPT_A,
    _PROMPT_B,
    _PROMPT_C,
    *_prompts(3),
    *_ROUND_1_PROMPTS,
    *_ROUND_2_PROMPTS,
    *_VARIED_PROMPTS,
]


@pytest.mark.parametrize("prompt", _LAZY_PROMPTS)
def test_a_lazy_prompt_is_rejected(prompt):
    """The guard's reason to exist: a prompt too thin to brief a colleague with never spawns a child."""
    assert agent_module._fault(prompt) == agent_module._TERSE


@pytest.mark.parametrize("prompt", _WELL_FORMED_PROMPTS)
def test_a_well_formed_prompt_is_never_rejected(prompt):
    """THE regression pin: every realistic brief passes, whatever its phrasing (QA rounds 1 + 2).

    Interrogative, imperative, phrasal-verb, declarative, colloquial, punctuation-free, terse — the
    guard must not care. It is a substance FLOOR, not a grader.
    """
    assert agent_module._fault(prompt) is None


@pytest.mark.parametrize("prompt", _WELL_FORMED_PROMPTS)
async def test_a_well_formed_prompt_reaches_the_fan_out_through_the_tool(prompt, tmp_path, mocker):
    """The same battery THROUGH the real tool call: no ``ModelRetry``, the child actually spawns.

    QA proved the false rejects were real at the tool level, not a predicate-only artefact — so the
    pin lives at the tool level too.
    """
    tracker = _ConcurrencyTracker()
    mocker.patch.object(agent_module, "_require_main_agent", return_value=tracker)

    out = await agent_module.agent(_tool_ctx(tmp_path), [prompt])

    assert tracker.spawns == 1  # no ModelRetry: the child ran
    assert len(_sections(out)) == 1


def test_the_substance_floor_is_the_only_rejection_criterion():
    """Word count decides, and nothing else — no keyword set gets a vote (ADR-0017 §3).

    A prompt one word under the floor is rejected; the same prompt one word over it passes, even
    with no "?", no path token and no report word. This is what stops the guard from drifting back
    into a keyword grader.
    """
    under = " ".join(["word"] * (agent_module.MIN_PROMPT_WORDS - 1))
    over = " ".join(["word"] * agent_module.MIN_PROMPT_WORDS)

    assert agent_module._fault(under) == agent_module._TERSE
    assert agent_module._fault(over) is None


async def test_the_nag_names_the_floor_and_never_invents_a_missing_part(tmp_path, mocker):
    """The nag must be TRUE: it names the floor, and claims nothing about question/scope/report.

    Regression (QA round 1): a 7-word prompt carrying all three parts was told all three were
    missing. A model fed a false "missing" claim cannot fix the real problem — the prompt is thin.
    """
    terse = "Trace gate.py's ASK path and report evidence."  # 7 words, all three parts present
    spawn = mocker.patch.object(agent_module, "_require_main_agent")

    with pytest.raises(ModelRetry) as excinfo:
        await agent_module.agent(_tool_ctx(tmp_path), [terse])

    message = str(excinfo.value)
    assert agent_module._TERSE in message  # names the floor…
    assert str(agent_module.MIN_PROMPT_WORDS) in message
    # …and the per-prompt problem line accuses it of nothing else.
    problem_line = message.split('- Prompt 1 ("')[1]
    assert "missing" not in problem_line.lower()
    spawn.assert_not_called()


def test_the_guard_is_pure_and_deterministic_on_repeat():
    """Same input, same outcome — no LLM, no I/O, no clock, no randomness (ADR-0017 §3)."""
    assert agent_module._fault("explore") == agent_module._fault("explore")
    assert agent_module._fault(_PROMPT_A) is agent_module._fault(_PROMPT_A) is None


async def test_an_under_specified_prompt_raises_model_retry_naming_its_index_and_what_is_missing(
    tmp_path, mocker
):
    """``prompts=["explore"]`` → ``ModelRetry`` naming prompt 1 and what it lacks; NO child spawns."""
    spawn = mocker.patch.object(agent_module, "_require_main_agent")
    ctx = _tool_ctx(tmp_path)

    with pytest.raises(ModelRetry) as excinfo:
        await agent_module.agent(ctx, ["explore"])

    message = str(excinfo.value)
    assert "prompt 1" in message.lower()  # WHICH prompt (1-based, as the sections are labelled)
    assert "explore" in message  # quoted back, so the model knows what to fix
    assert agent_module._TERSE in message  # WHAT is wrong — the one true fault
    # and the coaching the model needs to rewrite it: the three-part shape it must supply.
    lowered = message.lower()
    assert "question" in lowered and "scope" in lowered and "report" in lowered
    spawn.assert_not_called()  # the guard fires BEFORE the fan-out: no child, no semaphore slot


async def test_a_well_specified_list_passes_the_substance_guard_and_spawns(tmp_path, mocker):
    """Well-formed prompts are never nagged: the fan-out runs and folds one section per prompt."""
    tracker = _ConcurrencyTracker()
    mocker.patch.object(agent_module, "_require_main_agent", return_value=tracker)
    ctx = _tool_ctx(tmp_path)

    out = await agent_module.agent(ctx, [_PROMPT_A, _PROMPT_B, _PROMPT_C])

    assert tracker.spawns == 3
    assert len(_sections(out)) == 3


async def test_a_mixed_list_is_rejected_as_a_whole_with_only_the_bad_index_named(tmp_path, mocker):
    """One bad angle poisons the call: nothing spawns, and the nag names the bad index only."""
    spawn = mocker.patch.object(agent_module, "_require_main_agent")
    ctx = _tool_ctx(tmp_path)

    with pytest.raises(ModelRetry) as excinfo:
        await agent_module.agent(ctx, [_PROMPT_A, "explore the repo", _PROMPT_C])

    message = str(excinfo.value)
    assert "prompt 2" in message.lower()
    assert "prompt 1" not in message.lower() and "prompt 3" not in message.lower()
    assert "explore the repo" in message  # quotes the offender back, so the model knows what to fix
    spawn.assert_not_called()  # a whole-call rejection: not even the two good angles spawn


async def test_the_substance_guard_is_deterministic_through_the_tool(tmp_path, mocker):
    """Twice the same call → twice the same rejection message (no child either time)."""
    spawn = mocker.patch.object(agent_module, "_require_main_agent")
    ctx = _tool_ctx(tmp_path)

    messages: list[str] = []
    for _ in range(2):
        with pytest.raises(ModelRetry) as excinfo:
            await agent_module.agent(ctx, [_PROMPT_A, "explore"])
        messages.append(str(excinfo.value))

    assert messages[0] == messages[1]
    spawn.assert_not_called()


async def test_the_substance_guard_fires_before_agent_run_is_ever_called(agent, tmp_path, mocker):
    """Spy the spawn seam itself: a lazy prompt means ``agent.run`` is never awaited (§3)."""
    run = mocker.patch.object(agent, "run")
    agent_module.set_main_agent(agent)
    ctx = _tool_ctx(tmp_path)

    with pytest.raises(ModelRetry):
        await agent_module.agent(ctx, ["explore"])

    run.assert_not_called()


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


async def test_a_multi_line_prompt_still_folds_a_single_line_heading(tmp_path, mocker):
    """A REAL model writes a three-part, multi-line brief — its heading must still be ONE line (§5).

    The regression test task 108's live smoke was missing: every hermetic prompt was single-line, so
    nothing caught the fold embedding a ``"QUESTION: …\\nSCOPE: …\\nREPORT: …"`` prompt verbatim into
    ``## Subagent i — "…"`` — a Markdown heading ends at the FIRST newline, so the closing quote landed
    two lines down and a human reading the raw transcript saw a broken heading + an orphan quote.
    ``_sections`` parses with the STRICT single-line ``^## Subagent (\\d+) — "(.*)"$``: it can only
    match if the whole label, closing quote included, sits on one line.
    """
    multi_line = (
        "QUESTION: How does the permission gate decide allow/ask/deny?\n"
        "SCOPE: src/decode/permissions/\n"
        "WHAT THE REPORT MUST CONTAIN: the decision path, with file:line evidence."
    )
    mocker.patch.object(agent_module, "_require_main_agent", return_value=_EchoAgent())
    ctx = _tool_ctx(tmp_path)

    out = await agent_module.agent(ctx, [multi_line])

    heading, body = _sections(out)[0]  # strict single-line parse — no match, no section
    assert heading == " ".join(multi_line.split())  # collapsed: one line, every word kept
    assert "\n" not in heading
    assert body == f"report for: {multi_line}"  # …and the CHILD's report is the untouched brief


async def test_the_child_is_briefed_with_the_models_original_uncollapsed_prompt(tmp_path, mocker):
    """Only the HEADING is collapsed: the child gets the model's brief byte-for-byte, newlines and all.

    Collapsing the prompt handed to ``agent.run()`` would flatten the three-part brief the tool
    description asks the model for — the child would read one run-on line instead of QUESTION / SCOPE /
    REPORT on their own lines. The fix is a rendering concern and must stop at the label.
    """
    multi_line = (
        "QUESTION: How is tool output truncated?\nSCOPE: src/decode/tools/\n"
        "WHAT THE REPORT MUST CONTAIN: the caps and where they are applied, with file:line evidence."
    )
    scripted = _ScriptedAgent({multi_line: [_report("REPORT")]})
    mocker.patch.object(agent_module, "_require_main_agent", return_value=scripted)
    ctx = _tool_ctx(tmp_path)

    await agent_module.agent(ctx, [multi_line])

    assert scripted.prompts == [multi_line]  # exactly what the model wrote — not " ".join(split())


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
    """Each child's report is capped to ``subagent_result_max_bytes // len(prompts)`` — total stays flat.

    Asserted as EXACT equality against :func:`_budgeted`, not as ``<= per_child``: an upper bound also
    holds when a child is quietly short-changed, so it cannot catch a fold that spends a few of the
    child's bytes on harness overhead.
    """
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
        assert body == _budgeted(big, per_child=per_child)  # the WHOLE share, to the byte
        assert body != big  # …and the report really was over the cap (the assertion has teeth)


async def test_each_child_is_truncated_at_exactly_its_share_of_the_budget(
    tmp_path, mocker, monkeypatch
):
    """The budget ARGUMENT is exactly ``subagent_result_max_bytes // len(prompts)`` — not merely "under".

    The sharpest form of the §6 + §9 contract, and the one that needs no reasoning about line
    granularity: spy the shared ``truncate()`` the tool calls per child and assert the ``max_bytes`` it
    is handed. A fold that shaved even ONE byte off a child's share — e.g. reserving room for the
    Synthesis Footer instead of appending it on top — turns this red immediately, where an upper-bound
    assertion on the resulting text would stay green.
    """
    monkeypatch.setattr(settings, "subagent_result_max_bytes", 300, raising=False)
    # Lazily imported inside ``_spawn_child``, so patch it at its source module (resolved per call).
    spy = mocker.patch("decode.tools.truncate.truncate", wraps=truncate)
    mocker.patch.object(
        agent_module, "_require_main_agent", return_value=_StubAgent(_report("REPORT"))
    )
    prompts = [_PROMPT_A, _PROMPT_B, _PROMPT_C]

    await agent_module.agent(_tool_ctx(tmp_path), prompts)

    assert spy.call_count == len(prompts)  # once per child, never once for the whole fold
    assert [call.kwargs["max_bytes"] for call in spy.call_args_list] == [300 // 3] * 3
    assert all(call.kwargs["max_lines"] == settings.max_output_lines for call in spy.call_args_list)


async def test_a_single_child_still_gets_the_whole_byte_budget(tmp_path, mocker, monkeypatch):
    """One prompt → the divisor is 1, so a lone child keeps the full ``subagent_result_max_bytes``."""
    monkeypatch.setattr(settings, "subagent_result_max_bytes", 100, raising=False)
    big = "\n".join(f"finding number {i}" for i in range(500))
    mocker.patch.object(agent_module, "_require_main_agent", return_value=_StubAgent(_report(big)))
    ctx = _tool_ctx(tmp_path)

    out = await agent_module.agent(ctx, [_PROMPT_A])

    body = _sections(out)[0][1]
    assert body == _budgeted(big, per_child=100)  # the undivided budget, to the byte
    assert 0 < len(body.encode("utf-8")) <= 100


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


# direct: the OUTPUT contract — the bad-report predicate + exactly ONE nudged retry (ADR-0017 §7)


async def test_an_empty_report_is_retried_once_with_the_nudge_and_the_good_retry_folds(
    tmp_path, mocker
):
    """Empty first, good second: exactly 2 spawns for that prompt, and the RETRY's report folds."""
    scripted = _ScriptedAgent({_PROMPT_A: [_report("   \n  "), _report("GOOD-RETRY-REPORT")]})
    mocker.patch.object(agent_module, "_require_main_agent", return_value=scripted)

    out = await agent_module.agent(_tool_ctx(tmp_path), [_PROMPT_A])

    assert scripted.spawns(_PROMPT_A) == 2
    assert _sections(out) == [(_PROMPT_A, "GOOD-RETRY-REPORT")]


async def test_the_retry_prompt_is_the_original_prompt_plus_the_nudge(tmp_path, mocker):
    """The re-spawn carries the ORIGINAL prompt AND the nudge — the child is told what went wrong."""
    scripted = _ScriptedAgent({_PROMPT_A: [_report(""), _report("GOOD")]})
    mocker.patch.object(agent_module, "_require_main_agent", return_value=scripted)

    await agent_module.agent(_tool_ctx(tmp_path), [_PROMPT_A])

    first, second = scripted.prompts
    assert first == _PROMPT_A  # attempt 1: the model's prompt, untouched
    assert _PROMPT_A in second  # attempt 2: the SAME brief…
    assert agent_module._RETRY_NUDGE in second  # …plus the harness's nudge
    assert second == _PROMPT_A + agent_module._RETRY_NUDGE


def test_the_retry_nudge_is_a_module_constant_that_says_what_was_wrong():
    """The nudge is a module constant (no Settings knob) and names BOTH failure modes + the fix (§7)."""
    assert not hasattr(settings, "subagent_retry_nudge")
    lowered = agent_module._RETRY_NUDGE.lower()
    assert "unusable" in lowered  # what was wrong…
    assert "empty" in lowered and "read" in lowered  # …in both its forms (empty / cited no code)
    assert "tools" in lowered  # …and the fix: USE YOUR TOOLS
    assert "file:line" in lowered  # …with the evidence the report contract (105) demands


async def test_a_twice_bad_child_folds_the_failure_note_and_never_spawns_a_third_time(
    tmp_path, mocker, caplog
):
    """Bad twice → the explicit failure note, EXACTLY 2 spawns (never 3) — a broken child is bounded."""
    scripted = _ScriptedAgent({_PROMPT_A: [_report(""), _report("  ")]})
    mocker.patch.object(agent_module, "_require_main_agent", return_value=scripted)

    with caplog.at_level("WARNING", logger="decode.tools.agent"):
        out = await agent_module.agent(_tool_ctx(tmp_path), [_PROMPT_A])

    assert scripted.spawns(_PROMPT_A) == 2  # the cap: one attempt + one retry, then give up
    assert _sections(out) == [(_PROMPT_A, agent_module._NO_USABLE_REPORT_NOTE)]
    # Both the retry and the give-up are visible to an operator — at WARNING, by index.
    assert "retry" in caplog.text.lower()
    assert "subagent 1" in caplog.text.lower()


async def test_a_child_that_called_a_tool_and_reported_is_never_retried(tmp_path, mocker):
    """A good report is spawned ONCE: the predicate must not cost a healthy child a second run."""
    scripted = _ScriptedAgent({_PROMPT_A: [_report("REPORT-A")]})  # a 2nd attempt would blow up
    mocker.patch.object(agent_module, "_require_main_agent", return_value=scripted)

    out = await agent_module.agent(_tool_ctx(tmp_path), [_PROMPT_A])

    assert scripted.spawns(_PROMPT_A) == 1
    assert _sections(out) == [(_PROMPT_A, "REPORT-A")]


async def test_a_zero_tool_call_child_is_bad_even_though_it_returned_text(tmp_path, mocker):
    """The hallucination tell (§7-ii): text is present, but the child read NO code — retried anyway."""
    scripted = _ScriptedAgent(
        {
            _PROMPT_A: [
                _report("I recall decode uses a gate.", tool_call=False),
                _report("EVIDENCED"),
            ]
        }
    )
    mocker.patch.object(agent_module, "_require_main_agent", return_value=scripted)

    out = await agent_module.agent(_tool_ctx(tmp_path), [_PROMPT_A])

    assert scripted.spawns(_PROMPT_A) == 2
    assert _sections(out) == [(_PROMPT_A, "EVIDENCED")]  # the memory-only answer never folds


async def test_a_deferred_tool_requests_output_routes_through_the_same_retry_machinery(
    tmp_path, mocker
):
    """Defensive: a ``DeferredToolRequests`` output is BAD — it retries, and folds the note if bad twice."""
    scripted = _ScriptedAgent({_PROMPT_A: [_deferred(), _report("RECOVERED")]})
    mocker.patch.object(agent_module, "_require_main_agent", return_value=scripted)

    out = await agent_module.agent(_tool_ctx(tmp_path), [_PROMPT_A])

    assert scripted.spawns(_PROMPT_A) == 2
    assert _sections(out) == [(_PROMPT_A, "RECOVERED")]  # never the raw object

    twice = _ScriptedAgent({_PROMPT_B: [_deferred(), _deferred()]})
    mocker.patch.object(agent_module, "_require_main_agent", return_value=twice)

    out = await agent_module.agent(_tool_ctx(tmp_path), [_PROMPT_B])

    assert twice.spawns(_PROMPT_B) == 2
    assert _sections(out) == [(_PROMPT_B, agent_module._NO_USABLE_REPORT_NOTE)]


async def test_a_twice_bad_child_leaves_its_siblings_intact_and_in_order(tmp_path, mocker):
    """Sibling isolation: one child's retry/give-up cycle is PRIVATE to its own gather slot (§5,7).

    The bad child sits in the MIDDLE of a 3-wide fan-out, so a shared-state bug would show up as a
    swapped section, a nudged sibling prompt, or a sibling report replaced by the note.
    """
    scripted = _ScriptedAgent(
        {
            _PROMPT_A: [_report("REPORT-A")],
            _PROMPT_B: [_report(""), _report("", tool_call=False)],
            _PROMPT_C: [_report("REPORT-C")],
        }
    )
    mocker.patch.object(agent_module, "_require_main_agent", return_value=scripted)

    out = await agent_module.agent(_tool_ctx(tmp_path), [_PROMPT_A, _PROMPT_B, _PROMPT_C])

    assert _sections(out) == [
        (_PROMPT_A, "REPORT-A"),
        (_PROMPT_B, agent_module._NO_USABLE_REPORT_NOTE),
        (_PROMPT_C, "REPORT-C"),
    ]
    # …and only the BAD child paid for a retry: the healthy siblings ran exactly once each.
    assert scripted.spawns(_PROMPT_A) == 1
    assert scripted.spawns(_PROMPT_B) == 2
    assert scripted.spawns(_PROMPT_C) == 1


async def test_the_retry_report_respects_the_per_child_byte_budget(tmp_path, mocker, monkeypatch):
    """A retry's report is truncated exactly like a first attempt's — the budget is not a first-try perk."""
    monkeypatch.setattr(settings, "subagent_result_max_bytes", 300, raising=False)
    big = "\n".join(f"finding number {i}" for i in range(500))
    scripted = _ScriptedAgent(
        {
            _PROMPT_A: [_report(""), _report(big)],  # the RETRY hands back the oversized report
            _PROMPT_B: [_report("REPORT-B")],
            _PROMPT_C: [_report("REPORT-C")],
        }
    )
    mocker.patch.object(agent_module, "_require_main_agent", return_value=scripted)

    out = await agent_module.agent(_tool_ctx(tmp_path), [_PROMPT_A, _PROMPT_B, _PROMPT_C])

    retried_body = _sections(out)[0][1]
    assert retried_body == _budgeted(big, per_child=300 // 3)  # the SAME share as any first attempt
    assert retried_body != big  # …and the retry's report really was over the cap


async def test_a_retry_never_re_runs_the_substance_guard_over_the_nudged_prompt(tmp_path, mocker):
    """The 104 interaction: the guard runs ONCE, over the MODEL's prompts — never over a retry (§3,7).

    The nudged prompt is harness-authored, so nagging the model about it would be nonsense (the model
    never wrote it). Pinned on the seam: ``_check_substance`` is called exactly once, with exactly the
    model's prompt list, even though a child retried.
    """
    spy = mocker.spy(agent_module, "_check_substance")
    scripted = _ScriptedAgent({_PROMPT_A: [_report(""), _report("GOOD")]})
    mocker.patch.object(agent_module, "_require_main_agent", return_value=scripted)

    await agent_module.agent(_tool_ctx(tmp_path), [_PROMPT_A])

    assert scripted.spawns(_PROMPT_A) == 2  # the retry did happen…
    assert spy.call_count == 1  # …and the guard still ran exactly once
    assert spy.call_args.args[0] == [_PROMPT_A]
    # Belt-and-braces: even IF it were re-run, the nudged prompt passes the floor (it is longer).
    assert agent_module._fault(_PROMPT_A + agent_module._RETRY_NUDGE) is None


async def test_a_bad_report_body_never_reaches_a_warning_log_line(tmp_path, mocker, caplog):
    """WARNING carries the index, never the child's (possibly huge, possibly garbage) report body."""
    scripted = _ScriptedAgent(
        {_PROMPT_A: [_report("SECRET-GARBAGE-BODY", tool_call=False), _report("ok")]}
    )
    mocker.patch.object(agent_module, "_require_main_agent", return_value=scripted)

    with caplog.at_level("WARNING", logger="decode.tools.agent"):
        await agent_module.agent(_tool_ctx(tmp_path), [_PROMPT_A])

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warnings, "a retry must be visible to an operator"
    assert all("SECRET-GARBAGE-BODY" not in r.getMessage() for r in warnings)


# loop-driven: the predicate against a REAL child run (a real ``AgentRunResult.all_messages()``)


async def test_a_real_child_that_reads_code_then_reports_is_never_retried(agent, tmp_path):
    """Through the real Agent: a child that globs then reports is GOOD — exactly one spawn (§7)."""
    spawns: list[str] = []
    real_run = agent.run

    async def spy_run(prompt, **kwargs):
        spawns.append(prompt)
        return await real_run(prompt, **kwargs)

    agent.run = spy_run
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent, deps=_loop_deps(emitted.append, gate=PermissionGate(), cwd=tmp_path)
    )
    ctx = TurnContext(0, "explore the repo", emitted.append)

    with agent.override(model=_fanout_model(_child_globs_then_reports("EVIDENCED-REPORT"))):
        await _drive(handler, ctx)

    assert len(spawns) == 1  # a healthy child is spawned once…
    assert any(
        "EVIDENCED-REPORT" in r for r in _tool_return_strings(handler)
    )  # …and its report folds


async def test_a_real_text_only_child_is_retried_then_gives_up_with_the_note(agent, tmp_path):
    """Through the real Agent: a child that answers from memory (no tool call) is BAD — 2 spawns, note.

    This is the predicate against a genuine ``AgentRunResult.all_messages()`` transcript, not a stub:
    the text is non-empty, so ONLY the zero-tool-call scan can catch it.
    """
    spawns: list[str] = []
    real_run = agent.run

    async def spy_run(prompt, **kwargs):
        spawns.append(prompt)
        return await real_run(prompt, **kwargs)

    agent.run = spy_run
    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent, deps=_loop_deps(emitted.append, gate=PermissionGate(), cwd=tmp_path)
    )
    ctx = TurnContext(0, "explore the repo", emitted.append)

    with agent.override(model=_fanout_model(_child_returns_text("decode surely uses a gate."))):
        await _drive(handler, ctx)

    assert len(spawns) == 2  # one attempt + ONE retry, never a third
    assert spawns[1] == spawns[0] + agent_module._RETRY_NUDGE
    folded = "\n".join(_tool_return_strings(handler))
    assert agent_module._NO_USABLE_REPORT_NOTE in folded
    assert (
        "decode surely uses a gate." not in folded
    )  # the memory-only answer never reaches the parent


async def test_a_real_child_that_reads_code_only_on_the_nudged_retry_folds_its_retry_report(
    agent, tmp_path
):
    """The full §7 arc through a real run: bad first attempt → nudge → the child reads → good fold."""
    (tmp_path / "sample.py").write_text("x = 1\n", encoding="utf-8")
    spawns: list[str] = []
    real_run = agent.run

    async def spy_run(prompt, **kwargs):
        spawns.append(prompt)
        return await real_run(prompt, **kwargs)

    agent.run = spy_run

    def child(messages, info):
        # The child sees its OWN spawn prompt: it only bothers reading code once nudged.
        nudged = agent_module._RETRY_NUDGE in _child_spawn_prompt(messages)
        if not nudged:
            return ModelResponse(parts=[TextPart(content="From memory: it uses a gate.")])  # BAD
        return _child_globs_then_reports("REPORT-AFTER-NUDGE")(messages, info)

    emitted: list[events.Event] = []
    handler = AgentTurnHandler(
        agent, deps=_loop_deps(emitted.append, gate=PermissionGate(), cwd=tmp_path)
    )
    ctx = TurnContext(0, "explore the repo", emitted.append)
    with agent.override(model=_fanout_model(child)):
        await _drive(handler, ctx)

    assert len(spawns) == 2
    assert any("REPORT-AFTER-NUDGE" in r for r in _tool_return_strings(handler))


# direct: the Synthesis Footer (ADR-0017 §9)


def test_the_synthesis_footer_is_a_module_constant_stating_the_whole_contract():
    """The footer is a module constant (no Settings knob, no .env entry) and says all four things."""
    assert not hasattr(settings, "subagent_synthesis_footer")
    lowered = agent_module.SYNTHESIS_FOOTER.lower()
    # 1. compile the N reports into ONE answer — the parent is the synthesizer (§5).
    assert "compile" in lowered and "one answer" in lowered
    # 2. prose PLUS a text diagram of the structure found.
    assert "prose" in lowered and "diagram" in lowered
    # 3. ASCII / box-drawing is the DEFAULT flavour…
    assert "ascii" in lowered and "box-drawing" in lowered
    # 4. …Mermaid only for a genuine graph, because a terminal renders it as raw source.
    assert "mermaid" in lowered and "only" in lowered
    assert "terminal" in lowered and "raw source" in lowered


async def test_a_one_wide_fold_still_carries_the_footer_after_its_only_section(tmp_path, mocker):
    """ALWAYS appended (decision-locked, §9): a one-element list is not exempt."""
    mocker.patch.object(
        agent_module, "_require_main_agent", return_value=_StubAgent(_report("REPORT-A"))
    )

    out = await agent_module.agent(_tool_ctx(tmp_path), [_PROMPT_A])

    assert out.endswith(agent_module.SYNTHESIS_FOOTER)  # after the LAST (here only) section
    assert out.index("REPORT-A") < out.index(agent_module.SYNTHESIS_FOOTER)
    assert out.count(agent_module.SYNTHESIS_FOOTER) == 1  # once per RESULT, not once per section


async def test_an_n_wide_fold_carries_exactly_one_footer_after_the_last_section(tmp_path, mocker):
    """N sections, ONE footer — and it trails the last one, so the model reads it last (§9)."""
    prompts = [_PROMPT_A, _PROMPT_B, _PROMPT_C]
    mocker.patch.object(agent_module, "_require_main_agent", return_value=_EchoAgent())

    out = await agent_module.agent(_tool_ctx(tmp_path), prompts)

    assert out.endswith(agent_module.SYNTHESIS_FOOTER)
    assert out.count(agent_module.SYNTHESIS_FOOTER) == 1
    # The footer sits AFTER every section heading, including the last.
    assert out.rindex('## Subagent 3 — "') < out.index(agent_module.SYNTHESIS_FOOTER)
    assert _sections(out) == [(p, f"report for: {p}") for p in prompts]  # sections still intact


async def test_the_footer_is_appended_even_when_a_section_carries_a_failure_note(tmp_path, mocker):
    """A degraded fold needs the synthesis instruction MOST — both §7 notes keep their footer."""
    from pydantic_ai.exceptions import UsageLimitExceeded

    class _OneRaisesOneIsTwiceBad:
        async def run(self, prompt, *, deps, usage_limits):
            if prompt.startswith(_PROMPT_B):
                raise UsageLimitExceeded("the request limit was exceeded")
            if prompt.startswith(_PROMPT_C):
                return _report("", tool_call=False)  # bad on BOTH attempts → the give-up note
            return _report(f"report for: {prompt}")

    mocker.patch.object(agent_module, "_require_main_agent", return_value=_OneRaisesOneIsTwiceBad())

    out = await agent_module.agent(_tool_ctx(tmp_path), [_PROMPT_A, _PROMPT_B, _PROMPT_C])

    bodies = [body for _prompt, body in _sections(out)]
    assert bodies[1] == agent_module._CHILD_FAILED_NOTE
    assert bodies[2] == agent_module._NO_USABLE_REPORT_NOTE
    assert out.endswith(agent_module.SYNTHESIS_FOOTER)


async def test_the_footer_never_eats_a_childs_byte_budget(tmp_path, mocker, monkeypatch):
    """Appended AFTER truncation (§9): every child still gets ``max_bytes // len(prompts)``, whole.

    The footer is harness overhead on TOP of the ~16 KB of reports — it must never be paid for out
    of a child's share, which would make the fold's evidence hostage to the instruction's length.

    Pinned by EQUALITY, not by ``<= per_child``: the bug this guards against is precisely a *modest*
    theft (a fold that reserves the footer's ~750 bytes out of the children's budget), and every such
    fold is still under the cap. Two equalities, because truncation is LINE-aligned and a small theft
    can land inside a line: the section bodies must each equal a full-budget truncation, AND the
    ``max_bytes`` handed to ``truncate()`` must be the undivided share to the byte — so the footer
    costing a child even ONE byte turns this red.
    """
    monkeypatch.setattr(settings, "subagent_result_max_bytes", 300, raising=False)
    big = "\n".join(f"finding number {i}" for i in range(500))
    spy = mocker.patch("decode.tools.truncate.truncate", wraps=truncate)
    mocker.patch.object(agent_module, "_require_main_agent", return_value=_StubAgent(_report(big)))
    prompts = [_PROMPT_A, _PROMPT_B, _PROMPT_C]

    out = await agent_module.agent(_tool_ctx(tmp_path), prompts)

    per_child = 300 // len(prompts)
    # Not one byte of any child's share was spent on the footer.
    assert [call.kwargs["max_bytes"] for call in spy.call_args_list] == [per_child] * 3
    expected = _budgeted(big, per_child=per_child)
    bodies = [body for _prompt, body in _sections(out)]
    # Every child got its FULL, undiminished share — the footer took nothing off any section body,
    # not off the last one either (each is byte-identical to the same full-budget truncation).
    assert bodies == [expected, expected, expected]
    assert expected != big  # the report really was over the cap (the equality has teeth)
    # …while the footer itself is present, whole, and paid for ON TOP of the budget.
    assert out.endswith(agent_module.SYNTHESIS_FOOTER)
    assert len(out.encode("utf-8")) > settings.subagent_result_max_bytes


# stand-in main agents + the section parser


def _report(text: str, *, tool_call: bool = True) -> SimpleNamespace:
    """A stand-in ``AgentRunResult``: ``output`` is ``text``, ``all_messages()`` its transcript.

    The runner reads BOTH (ADR-0017 §7): the output text, and the transcript it scans for a
    ``ToolCallPart``. ``tool_call=True`` (the default) is a GOOD child — it read some code before
    reporting; ``tool_call=False`` is the hallucination tell: a report answered from model memory.
    """
    transcript: list[ModelMessage] = []
    if tool_call:
        transcript.append(
            ModelResponse(
                parts=[ToolCallPart(tool_name="read", args={"path": "src/decode/cli.py"})]
            )
        )
    transcript.append(ModelResponse(parts=[TextPart(content=text)]))
    return SimpleNamespace(output=text, all_messages=lambda: transcript)


def _deferred() -> SimpleNamespace:
    """A stand-in result whose ``output`` is the defensive :class:`DeferredToolRequests` (BAD, §7)."""
    from pydantic_ai import DeferredToolRequests

    return SimpleNamespace(output=DeferredToolRequests(), all_messages=list)


class _StubAgent:
    """A stand-in main agent handing every child the SAME canned result."""

    def __init__(self, result):
        self._result = result

    async def run(self, prompt, *, deps, usage_limits):
        return self._result


class _ScriptedAgent:
    """A stand-in main agent handing each prompt a SCRIPTED result per attempt (ADR-0017 §7).

    ``script`` maps an ORIGINAL spawn prompt to the results its successive attempts get, so a test
    says "empty first, good second" literally. A retry arrives as ``original + nudge``, hence the
    prefix match — and ``spawns()`` counts the attempts for that prompt, which is how "exactly 2,
    never 3" is pinned.
    """

    def __init__(self, script: dict[str, list[SimpleNamespace]]) -> None:
        self._script = {prompt: list(results) for prompt, results in script.items()}
        self.prompts: list[str] = []

    async def run(self, prompt, *, deps, usage_limits):
        await asyncio.sleep(
            0
        )  # yield, so a broken (sequential) gather still cannot reorder results
        self.prompts.append(prompt)
        for original, results in self._script.items():
            if prompt.startswith(original):
                assert results, f"a third attempt was spawned for {original!r} — the cap is 2"
                return results.pop(0)
        raise AssertionError(f"unscripted spawn prompt: {prompt!r}")

    def spawns(self, original: str) -> int:
        """How many attempts ran for ``original`` (its first attempt + any retry)."""
        return sum(1 for prompt in self.prompts if prompt.startswith(original))


class _EchoAgent:
    """A stand-in main agent whose child report echoes its own spawn prompt (proves section pairing)."""

    async def run(self, prompt, *, deps, usage_limits):
        await asyncio.sleep(0)  # yield, so a broken (sequential) gather still can't reorder results
        return _report(f"report for: {prompt}")


def _budgeted(report: str, *, per_child: int) -> str:
    """EXACTLY what a child's section body must be: ``report`` through the shared ``truncate()`` idiom
    at its own share of the budget (ADR-0017 §6).

    The byte tests assert section bodies against THIS, not against ``len(body) <= per_child``. An upper
    bound is satisfied by any theft: a fold that quietly spent a few of a child's bytes on harness
    overhead (say, reserving room for the Synthesis Footer) would still be "under the cap" and stay
    green. Equality pins the budget the child is actually owed — ``subagent_result_max_bytes //
    len(prompts)``, whole — so a single byte shaved off it turns this red. It re-derives the expected
    text through the same ``truncate()`` the tool uses, so what is asserted is the BUDGET handed to it,
    never a re-implementation of the truncation algorithm.

    ``.strip()``ed to match :func:`_sections`, which strips each body at the section boundary (the fold
    joins sections with a blank line). Truncation is LINE-aligned, so equality here catches any theft
    big enough to drop a line; :func:`test_each_child_is_truncated_at_exactly_its_share_of_the_budget`
    pins the budget ARGUMENT itself, which catches a theft of even one byte.
    """
    return truncate(report, max_lines=settings.max_output_lines, max_bytes=per_child).text.strip()


_SECTION_RE = re.compile(r'^## Subagent (\d+) — "(.*)"$', re.MULTILINE)


def _sections(aggregate: str) -> list[tuple[str, str]]:
    """Parse the labelled aggregate into ``(prompt, body)`` pairs — the model-facing contract (§5).

    Asserts the headings are 1-based and contiguous, so a mis-numbered fold fails loudly here. The
    Synthesis Footer (§9) is stripped FIRST: it trails the last section but belongs to no section, so
    every body assertion below (notably the §6 byte budgets) stays about the CHILD's report alone.
    Stripping asserts its presence, so every fold test is also a footer test.
    """
    assert aggregate.endswith(agent_module.SYNTHESIS_FOOTER), aggregate
    aggregate = aggregate[: -len(agent_module.SYNTHESIS_FOOTER)]
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
