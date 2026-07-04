"""Unit tests for the ``agent`` tool + the in-process Explore-subagent runner (ADR-0013 §1,5-9).

The ``agent`` tool is the model-callable spawn point: ``agent(prompt)`` re-enters the ONE built
Pydantic-AI :class:`~pydantic_ai.Agent` (reached via a set-once module seam, mirroring bash's
``_EXECUTOR``) as a nested ``agent.run()`` with **fresh, narrowed, read-only deps**
(``active_agent=explore``, a fresh gate + task_store, a no-op event sink). It is
:class:`~decode.permissions.types.ToolKind.READ_ONLY`, so it runs inline and never prompts; its
children are read-only too.

Two layers of test, mirroring ``test_skills.py``:

* **direct** — call ``agent`` (or the seam helpers) with a hand-built :class:`RunContext` / a stub
  main agent to pin the seam contract, the fresh-deps identity, the no-usage-threading rule, the
  ``UsageLimits`` cap, the report truncation, and the concurrency semaphore;
* **loop-driven** — drive ``agent`` through the *real* ``build_agent`` + ``AgentTurnHandler`` (model
  swapped for a scripted :class:`FunctionModel`, no network) so the whole spawn-and-fold path holds:
  a parent turn spawns a child, the child runs read-only with the ``agent`` tool hidden (recursion
  impossible), and the child's final text folds back as the tool result — emitting **no** permission
  prompt (READ_ONLY runs inline). One :func:`FunctionModel` drives BOTH parent and child on the same
  ``Agent`` (``override`` is contextvar-scoped), branching on whether ``agent`` is a visible tool.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from pydantic_ai import RunContext
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

# --- direct-call harness -----------------------------------------------------------------------


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


# --- seam: name + signature + set-once + require-when-unset -------------------------------------


def test_agent_tool_name_is_stable():
    assert agent_module.AGENT_TOOL_NAME == "agent"


def test_agent_takes_ctx_and_prompt_only():
    import inspect

    params = list(inspect.signature(agent_module.agent).parameters)
    assert params == ["ctx", "prompt"]


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


# --- registry: kind + known names --------------------------------------------------------------


def test_agent_is_registered_as_a_read_only_spec():
    by_name = {spec.name: spec for spec in TOOL_SPECS}
    assert "agent" in by_name
    # READ_ONLY → runs inline, never prompts, auto-allows in every mode (ADR-0013 §5).
    assert by_name["agent"].kind is ToolKind.READ_ONLY
    assert by_name["agent"].func is agent_module.agent


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


# --- persona grants: build / plan / code-reviewer YES, explore NO ------------------------------


def test_primary_agents_grant_agent_tool_and_explore_never_does():
    # ADR-0013 §4: the ``agent`` tool is granted to every PRIMARY persona, never to the explore
    # subagent (recursion default-deny — a child cannot spawn a child).
    from decode.agents.loader import load_builtin_agents

    agents = load_builtin_agents()
    assert set(agents) == {"build", "plan", "explore", "code-reviewer"}
    for name in ("build", "plan", "code-reviewer"):
        assert "agent" in agents[name].tools, f"{name} must grant the agent tool (ADR-0013 §4)"
    assert "agent" not in agents["explore"].tools  # never — recursion default-deny


# --- loop-driven harness: one FunctionModel drives parent AND child ----------------------------


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


def _fanout_model(child_fn, *, spawn_prompt="investigate the codebase", parent_final="DONE"):
    """A :class:`FunctionModel` driving the PARENT (spawn once, then finish) and the CHILD.

    It branches on whether the ``agent`` tool is visible: the parent sees it — it spawns a child on its
    first request, then returns text; the child does NOT (``prepare=`` hid it), so control falls to the
    ``child_fn`` behavior the individual test scripts. One model object drives both because
    ``agent.override(model=…)`` is contextvar-scoped (ADR-0013 §6).
    """

    def function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        visible = {t.name for t in info.function_tools}
        if AGENT_TOOL_NAME in visible:  # PARENT context
            if not _tool_returned(messages, AGENT_TOOL_NAME):
                return ModelResponse(
                    parts=[ToolCallPart(tool_name=AGENT_TOOL_NAME, args={"prompt": spawn_prompt})]
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
        out = await agent_module.agent(ctx, "investigate the config module")

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


# --- direct: report truncation -----------------------------------------------------------------


async def test_long_child_report_is_truncated_to_the_byte_cap(agent, tmp_path, monkeypatch):
    """A long child report is capped to ``subagent_result_max_bytes`` and returned as a plain str."""
    monkeypatch.setattr(settings, "subagent_result_max_bytes", 100, raising=False)
    big = "\n".join(f"finding number {i}" for i in range(500))  # many short lines, well over 100 B
    ctx = _tool_ctx(tmp_path)

    with agent.override(model=_child_model(big)):
        out = await agent_module.agent(ctx, "investigate everything")

    assert isinstance(out, str)
    assert len(out.encode("utf-8")) <= 100  # the byte cap bit
    assert out != big
    assert big.startswith(out)  # the kept head is a line-aligned prefix of the report


async def test_deferred_tool_requests_output_returns_a_fallback_note(agent, tmp_path, mocker):
    """Defensive: a ``DeferredToolRequests`` child output → a short note, never the object (§8)."""
    from pydantic_ai import DeferredToolRequests

    stub = SimpleNamespace(output=DeferredToolRequests())
    mocker.patch.object(agent_module, "_require_main_agent", return_value=_StubAgent(stub))
    ctx = _tool_ctx(tmp_path)

    out = await agent_module.agent(ctx, "investigate")

    assert isinstance(out, str)
    assert "could not complete" in out.lower()


class _StubAgent:
    def __init__(self, result):
        self._result = result

    async def run(self, prompt, *, deps, usage_limits):
        return self._result


# --- direct: the concurrency semaphore ---------------------------------------------------------


class _ConcurrencyTracker:
    """A stand-in main agent whose ``run`` records the peak number of concurrent children."""

    def __init__(self) -> None:
        self.live = 0
        self.max_concurrent = 0

    async def run(self, prompt, *, deps, usage_limits):
        self.live += 1
        self.max_concurrent = max(self.max_concurrent, self.live)
        await asyncio.sleep(0.05)  # hold the slot so overlap is observable
        self.live -= 1
        return SimpleNamespace(output="report")


async def test_semaphore_bounds_concurrent_children(mocker, monkeypatch, tmp_path):
    """With ``subagent_max_parallel=2`` at most two children run at once (the overlap counter caps)."""
    monkeypatch.setattr(settings, "subagent_max_parallel", 2, raising=False)
    agent_module._reset_semaphores()  # rebuild the per-loop semaphore at the patched size
    tracker = _ConcurrencyTracker()
    mocker.patch.object(agent_module, "_require_main_agent", return_value=tracker)

    ctxs = [_tool_ctx(tmp_path) for _ in range(6)]
    results = await asyncio.gather(*(agent_module.agent(c, "investigate") for c in ctxs))

    assert all("report" in r for r in results)
    assert tracker.max_concurrent <= settings.subagent_max_parallel
    assert tracker.max_concurrent == 2  # concurrency reached — but never exceeded — the cap


# --- import hygiene: the agent tool + the cli stay kitaru-free ----------------------------------


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
