"""Unit tests for :mod:`decode.agents.select` — applying a selected Agent persona.

ADR-0003 §7: selecting an agent is the one place the three pieces of active state are set
together — ``deps.active_agent`` (the persona the factory's instructions hook + ``prepare=``
read), the gate's **mode** (reset to the agent's default), and the gate's **agent rule set**
(loaded from the agent's catalog ``allow`` / ``deny`` so e.g. code-reviewer's ``bash(git *)``
takes effect). :func:`select_agent` does exactly that and returns the resolved
:class:`~decode.entities.agent_def.AgentDef`.
"""

from pathlib import Path

import pytest

from decode.agent.deps import AgentDeps
from decode.agents.loader import load_agent
from decode.agents.select import select_agent
from decode.entities.permissions import (
    PermissionDecision,
    PermissionOutcome,
    PermissionRequest,
)
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode, ToolKind


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny(reason="test default deny")


async def _no_user_resolver(question: str) -> str:
    raise RuntimeError("no interactive user in this test")


def _deps(gate: PermissionGate) -> AgentDeps:
    return AgentDeps(
        cwd=Path("."),
        emit=lambda _e: None,
        gate=gate,
        resolve_permission=_deny_resolver,
        resolve_user_question=_no_user_resolver,
    )


def _bash(subject: str) -> PermissionRequest:
    return PermissionRequest(tool_name="bash", args="", kind=ToolKind.OTHER, subject=subject)


def test_select_agent_sets_deps_active_agent():
    gate = PermissionGate()
    deps = _deps(gate)

    selected = select_agent("plan", deps=deps, gate=gate)

    assert selected.name == "plan"
    assert deps.active_agent is selected


def test_select_agent_returns_the_resolved_agent_def():
    gate = PermissionGate()
    deps = _deps(gate)

    selected = select_agent("code-reviewer", deps=deps, gate=gate)

    assert selected == load_agent("code-reviewer")


def test_select_plan_resets_the_gate_mode_to_plan():
    gate = PermissionGate()  # starts in DEFAULT
    deps = _deps(gate)

    select_agent("plan", deps=deps, gate=gate)

    assert gate.mode is PermissionMode.PLAN


def test_select_build_resets_the_gate_mode_to_default():
    gate = PermissionGate(PermissionMode.PLAN)  # start somewhere else
    deps = _deps(gate)

    select_agent("build", deps=deps, gate=gate)

    assert gate.mode is PermissionMode.DEFAULT


def test_select_code_reviewer_loads_its_git_allow_rule():
    # ADR-0003 §4,7 acceptance: code-reviewer's `bash(git *)` auto-allows `git diff`, but a
    # non-git bash command still ASKs (the agent rule does not blanket-allow bash).
    gate = PermissionGate()
    deps = _deps(gate)

    select_agent("code-reviewer", deps=deps, gate=gate)

    assert gate.check(_bash("git diff")).outcome is PermissionOutcome.ALLOW
    assert gate.check(_bash("rm x")).outcome is PermissionOutcome.ASK


def test_selecting_a_new_agent_replaces_the_prior_agents_rules():
    # Switching off code-reviewer drops its git allow rule (no lingering rules across switches).
    gate = PermissionGate()
    deps = _deps(gate)
    select_agent("code-reviewer", deps=deps, gate=gate)
    assert gate.check(_bash("git diff")).outcome is PermissionOutcome.ALLOW

    select_agent("build", deps=deps, gate=gate)  # build (a primary) carries no git rule

    assert gate.check(_bash("git diff")).outcome is PermissionOutcome.ASK


def test_select_unknown_agent_raises_value_error_listing_available_agents():
    gate = PermissionGate()
    deps = _deps(gate)

    with pytest.raises(ValueError, match="no such agent 'nope'"):
        select_agent("nope", deps=deps, gate=gate)

    # The deps / gate are left untouched by a failed selection (build default, DEFAULT mode).
    assert deps.active_agent.name == "build"
    assert gate.mode is PermissionMode.DEFAULT


def test_select_explore_subagent_is_rejected_and_leaves_state_untouched():
    # ADR-0013 §3: explore is a subagent — it cannot be selected as the main agent. The rejection
    # (primaries only) happens before any mutation, so ``deps`` / ``gate`` are untouched and the
    # REPL's ``/agent`` stays alive.
    gate = PermissionGate()
    deps = _deps(gate)

    with pytest.raises(ValueError) as excinfo:
        select_agent("explore", deps=deps, gate=gate)

    assert "available agents: build, code-reviewer, plan" in str(excinfo.value)
    assert deps.active_agent.name == "build"  # untouched — still the default persona
    assert gate.mode is PermissionMode.DEFAULT
