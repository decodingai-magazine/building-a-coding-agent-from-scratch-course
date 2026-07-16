"""Unit tests for the orchestration controls (``decode.tools.orchestration``) — ADR-0003 §8.

Both tools are ungated. Direct tests pin the mode flips, the surfaced approval cue, y/N
parsing, and the clean ``ModelRetry`` on headless/cancelled approval; loop-driven tests ride
the real ``build_agent`` + ``AgentTurnHandler`` + gate (scripted ``FunctionModel``, no network)
to prove a write is denied in PLAN, auto-allowed after an approved ``exit_plan_mode`` (EDIT),
and still denied after a rejected one — with no ``PermissionRequested`` from either tool.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from decode.agent.deps import AgentDeps, UserQuestionResolver
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import Boundary, TurnContext
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode
from decode.tools import orchestration
from decode.tools.askuser import NoInteractiveUserError

# direct-call harness


async def _deny_permission_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


async def _no_user_resolver(question: str) -> str:
    raise NoInteractiveUserError("no interactive user in this test")


def _ctx(
    *,
    gate: PermissionGate,
    resolve_user_question: UserQuestionResolver = _no_user_resolver,
    emit: object = None,
) -> RunContext[AgentDeps]:
    sink = emit if emit is not None else (lambda _e: None)
    deps = AgentDeps(
        cwd=Path("."),
        emit=sink,  # type: ignore[arg-type]
        gate=gate,
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=resolve_user_question,
    )
    return RunContext(deps=deps, model=None, usage=None, tool_call_approved=False)  # type: ignore[arg-type]


# enter_plan_mode (direct)


async def test_enter_plan_mode_sets_plan_and_confirms():
    gate = PermissionGate()  # starts DEFAULT
    ctx = _ctx(gate=gate)

    result = await orchestration.enter_plan_mode(ctx)

    assert gate.mode is PermissionMode.PLAN
    assert "plan mode" in result.lower()
    assert "exit_plan_mode" in result


async def test_enter_plan_mode_is_callable_from_any_mode():
    # Even already in EDIT, entering plan mode just flips to PLAN (ungated, no gate involved).
    gate = PermissionGate(mode=PermissionMode.EDIT)
    ctx = _ctx(gate=gate)

    await orchestration.enter_plan_mode(ctx)

    assert gate.mode is PermissionMode.PLAN


async def test_enter_plan_mode_tolerates_and_ignores_a_premature_plan_argument():
    # The tool name primes a model to pass a ``plan`` (as exit_plan_mode wants); accepting-and-
    # ignoring it flips to PLAN and returns the same confirmation, never a schema-validation crash.
    gate = PermissionGate()
    ctx = _ctx(gate=gate)

    result = await orchestration.enter_plan_mode(ctx, plan="a whole plan the model jumped ahead to")

    assert gate.mode is PermissionMode.PLAN
    assert "exit_plan_mode" in result  # redirected: present the plan via exit_plan_mode instead


# exit_plan_mode (direct)


async def test_exit_plan_mode_approve_switches_to_edit_and_confirms():
    gate = PermissionGate(mode=PermissionMode.PLAN)

    async def approve(question: str) -> str:
        return "y"

    ctx = _ctx(gate=gate, resolve_user_question=approve)
    result = await orchestration.exit_plan_mode(ctx, plan="step 1; step 2")

    assert gate.mode is PermissionMode.EDIT
    assert "approved" in result.lower()


async def test_exit_plan_mode_deny_stays_in_plan_and_asks_to_refine():
    gate = PermissionGate(mode=PermissionMode.PLAN)

    async def deny(question: str) -> str:
        return "n"

    ctx = _ctx(gate=gate, resolve_user_question=deny)
    result = await orchestration.exit_plan_mode(ctx, plan="my plan")

    assert gate.mode is PermissionMode.PLAN
    assert "not approved" in result.lower()
    assert "exit_plan_mode" in result


async def test_exit_plan_mode_surfaces_the_plan_and_approval_cue():
    emitted: list[events.Event] = []
    gate = PermissionGate(mode=PermissionMode.PLAN)

    async def approve(question: str) -> str:
        return "yes"

    ctx = _ctx(gate=gate, resolve_user_question=approve, emit=emitted.append)
    await orchestration.exit_plan_mode(ctx, plan="THE PLAN BODY")

    asks = [e for e in emitted if isinstance(e, events.AskUserRequested)]
    assert asks, "exit_plan_mode must surface the plan + cue via an AskUserRequested event"
    assert "THE PLAN BODY" in asks[0].question
    assert "[y/N]" in asks[0].question


async def test_exit_plan_mode_passes_the_plan_and_cue_to_the_resolver():
    seen: list[str] = []
    gate = PermissionGate(mode=PermissionMode.PLAN)

    async def resolver(question: str) -> str:
        seen.append(question)
        return "n"

    ctx = _ctx(gate=gate, resolve_user_question=resolver)
    await orchestration.exit_plan_mode(ctx, plan="research notes")

    assert len(seen) == 1
    assert "research notes" in seen[0]
    assert "Approve this plan" in seen[0]


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES", "  yes  "])
async def test_exit_plan_mode_treats_yes_variants_as_approval(answer):
    gate = PermissionGate(mode=PermissionMode.PLAN)

    async def resolver(question: str) -> str:
        return answer

    ctx = _ctx(gate=gate, resolve_user_question=resolver)
    await orchestration.exit_plan_mode(ctx, plan="p")

    assert gate.mode is PermissionMode.EDIT


@pytest.mark.parametrize("answer", ["n", "no", "", "maybe", "approve later", "yeah"])
async def test_exit_plan_mode_treats_everything_else_as_deny(answer):
    # The cue is ``[y/N]``: anything that is not an explicit yes keeps the safe (deny) default.
    gate = PermissionGate(mode=PermissionMode.PLAN)

    async def resolver(question: str) -> str:
        return answer

    ctx = _ctx(gate=gate, resolve_user_question=resolver)
    await orchestration.exit_plan_mode(ctx, plan="p")

    assert gate.mode is PermissionMode.PLAN


async def test_exit_plan_mode_headless_raises_model_retry_and_stays_plan():
    # No interactive user → the resolver raises NoInteractiveUserError; exit_plan_mode maps it to a
    # model-readable ModelRetry and leaves the mode untouched (never silently exits plan mode).
    gate = PermissionGate(mode=PermissionMode.PLAN)
    ctx = _ctx(gate=gate, resolve_user_question=_no_user_resolver)

    with pytest.raises(ModelRetry):
        await orchestration.exit_plan_mode(ctx, plan="p")

    assert gate.mode is PermissionMode.PLAN


async def test_exit_plan_mode_cancelled_raises_model_retry_and_stays_plan():
    # A cancelled approval (turn aborted / REPL shutting down) maps to a clean ModelRetry — never a
    # hang — and the mode is untouched.
    gate = PermissionGate(mode=PermissionMode.PLAN)

    async def cancelling(question: str) -> str:
        raise asyncio.CancelledError

    ctx = _ctx(gate=gate, resolve_user_question=cancelling)

    with pytest.raises(ModelRetry):
        await orchestration.exit_plan_mode(ctx, plan="p")

    assert gate.mode is PermissionMode.PLAN


async def test_orchestration_tool_names_are_stable():
    assert orchestration.ENTER_PLAN_MODE_TOOL_NAME == "enter_plan_mode"
    assert orchestration.EXIT_PLAN_MODE_TOOL_NAME == "exit_plan_mode"
    assert orchestration.SLEEP_TOOL_NAME == "sleep"


# loop-driven harness


@pytest.fixture
def agent(mocker):
    """A real `decode` agent built with a dummy key (never used: tests override the model)."""
    mocker.patch(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), create=False
    )
    return build_agent()


def _loop_deps(emit, *, gate: PermissionGate, cwd: Path, answer: str | None = None) -> AgentDeps:
    async def resolve_user_question(question: str) -> str:
        assert answer is not None, "this test did not script an exit_plan_mode answer"
        return answer

    return AgentDeps(
        cwd=cwd,
        emit=emit,
        gate=gate,
        resolve_permission=_deny_permission_resolver,
        resolve_user_question=resolve_user_question,
    )


def _scripted_model(steps: list[DeltaToolCall]) -> FunctionModel:
    """Stream one scripted tool call per model request, then plain text once the steps run out.

    Ungated tools (``enter_plan_mode`` / ``exit_plan_mode``) execute inline within a single
    ``agent.iter`` leg, so the model is re-requested after each tool return; a gated tool
    (``write``) instead resolves the leg to ``DeferredToolRequests`` for the gate to decide.
    """
    state = {"i": 0}

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        i = state["i"]
        if i >= len(steps):
            yield "done"
            return
        state["i"] += 1
        yield {0: steps[i]}

    return FunctionModel(stream_function=stream_function)


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


def _write_call(path: str) -> DeltaToolCall:
    return DeltaToolCall(name="write", json_args=json.dumps({"path": path, "content": "x"}))


async def test_enter_plan_mode_through_the_loop_denies_a_subsequent_write(agent, tmp_path):
    """ADR-0003 §8: enter_plan_mode sets PLAN through the real loop; a later write is DENIED."""
    emitted: list[events.Event] = []
    gate = PermissionGate()  # DEFAULT
    deps = _loop_deps(emitted.append, gate=gate, cwd=tmp_path)
    handler = AgentTurnHandler(agent, deps=deps)

    steps = [
        DeltaToolCall(name="enter_plan_mode", json_args="{}"),
        _write_call("blocked.txt"),
    ]
    ctx = TurnContext(0, "make a plan then write", emitted.append)
    with agent.override(model=_scripted_model(steps)):
        await _drive(handler, ctx)

    # The gate landed in PLAN and the write never hit disk (plan mode denies mutations).
    assert gate.mode is PermissionMode.PLAN
    assert not (tmp_path / "blocked.txt").exists(), "a write in plan mode must be denied"
    # The denial reason (plan mode) reached the model as a tool result.
    returns = _tool_return_strings(handler)
    assert any("plan mode" in r.lower() for r in returns)
    # enter_plan_mode is ungated: it never produced a permission prompt.
    assert not [e for e in emitted if isinstance(e, events.PermissionRequested)]


async def test_enter_plan_mode_with_a_plan_arg_does_not_crash_the_turn(agent, tmp_path):
    """Regression (demo-5): a model that calls enter_plan_mode with a ``plan`` must not brick.

    Before the fix ``enter_plan_mode`` took no args, so ``{"plan": ...}`` failed pydantic-ai arg
    validation (``extra_forbidden``); the model re-guessed the same shape until the retry budget
    was exhausted and the turn crashed with ``UnexpectedModelBehavior``. The call must now succeed,
    land the gate in PLAN, and let the turn finish.
    """
    emitted: list[events.Event] = []
    gate = PermissionGate()  # DEFAULT
    deps = _loop_deps(emitted.append, gate=gate, cwd=tmp_path)
    handler = AgentTurnHandler(agent, deps=deps)

    steps = [
        DeltaToolCall(
            name="enter_plan_mode", json_args=json.dumps({"plan": "explore, then build"})
        ),
    ]
    ctx = TurnContext(0, "plan a feature", emitted.append)
    with agent.override(model=_scripted_model(steps)):
        await _drive(handler, ctx)

    assert gate.mode is PermissionMode.PLAN  # the call took effect instead of crashing
    assert not [e for e in emitted if isinstance(e, events.AgentError)]
    returns = _tool_return_strings(handler)
    assert any(
        "exit_plan_mode" in r for r in returns
    )  # got the confirmation, not a validation error


async def test_exit_plan_mode_approve_through_the_loop_allows_a_subsequent_write(agent, tmp_path):
    """ADR-0003 §8: an approved exit_plan_mode lands in EDIT; a later write auto-ALLOWS."""
    emitted: list[events.Event] = []
    gate = PermissionGate(mode=PermissionMode.PLAN)
    deps = _loop_deps(emitted.append, gate=gate, cwd=tmp_path, answer="y")
    handler = AgentTurnHandler(agent, deps=deps)

    steps = [
        DeltaToolCall(name="exit_plan_mode", json_args=json.dumps({"plan": "do the thing"})),
        _write_call("allowed.txt"),
    ]
    ctx = TurnContext(0, "present the plan", emitted.append)
    with agent.override(model=_scripted_model(steps)):
        await _drive(handler, ctx)

    # Approval switched the gate to EDIT, so the FILE_EDIT write auto-allowed and created the file.
    assert gate.mode is PermissionMode.EDIT
    assert (tmp_path / "allowed.txt").read_text(encoding="utf-8") == "x"
    # No human permission prompt: exit_plan_mode is ungated and the edit auto-allowed in EDIT mode.
    assert not [e for e in emitted if isinstance(e, events.PermissionRequested)]


async def test_exit_plan_mode_deny_through_the_loop_keeps_a_write_denied(agent, tmp_path):
    """ADR-0003 §8: a denied exit_plan_mode stays PLAN; a later write is still DENIED."""
    emitted: list[events.Event] = []
    gate = PermissionGate(mode=PermissionMode.PLAN)
    deps = _loop_deps(emitted.append, gate=gate, cwd=tmp_path, answer="n")
    handler = AgentTurnHandler(agent, deps=deps)

    steps = [
        DeltaToolCall(name="exit_plan_mode", json_args=json.dumps({"plan": "still rough"})),
        _write_call("still-blocked.txt"),
    ]
    ctx = TurnContext(0, "present the plan", emitted.append)
    with agent.override(model=_scripted_model(steps)):
        await _drive(handler, ctx)

    assert gate.mode is PermissionMode.PLAN
    assert not (tmp_path / "still-blocked.txt").exists(), "a denied plan keeps writes blocked"


async def test_exit_plan_mode_is_ungated_and_callable_in_plan_mode(agent, tmp_path):
    """exit_plan_mode rides the ask_user channel (AskUserRequested), never the permission gate."""
    emitted: list[events.Event] = []
    gate = PermissionGate(mode=PermissionMode.PLAN)
    deps = _loop_deps(emitted.append, gate=gate, cwd=tmp_path, answer="n")
    handler = AgentTurnHandler(agent, deps=deps)

    steps = [DeltaToolCall(name="exit_plan_mode", json_args=json.dumps({"plan": "p"}))]
    ctx = TurnContext(0, "present the plan", emitted.append)
    with agent.override(model=_scripted_model(steps)):
        await _drive(handler, ctx)

    # It surfaced its approval via the ask_user channel (AskUserRequested), not a PermissionRequested
    # prompt — proving it is ungated even while the session is in plan mode.
    assert [e for e in emitted if isinstance(e, events.AskUserRequested)]
    assert not [e for e in emitted if isinstance(e, events.PermissionRequested)]


def _tool_return_strings(handler: AgentTurnHandler) -> list[str]:
    """Every tool-return content string in the handler's accumulated history."""
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    return [
        str(part.content)
        for message in handler.message_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
