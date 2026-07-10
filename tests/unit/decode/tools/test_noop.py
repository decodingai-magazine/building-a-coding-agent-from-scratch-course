"""Unit tests for the trivial gated ``noop`` tool (``support.noop_helper``) — ADR-0002 §3,7.

``noop`` is the TEST-ONLY gated echo tool (never registered by the shipped package). These
tests pin its two states (unapproved → raises ``ApprovalRequired``; approved → echoes) and its
not-read-only registration, without going through the model.
"""

from pathlib import Path

import pytest
from pydantic_ai import ApprovalRequired, RunContext
from support import noop_helper as noop_module

from decode.agent.deps import AgentDeps
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.gate import PermissionGate
from decode.tools.askuser import deny_user_question_resolver


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


def _ctx(*, approved: bool) -> RunContext[AgentDeps]:
    deps = AgentDeps(
        cwd=Path("."),
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
        resolve_user_question=deny_user_question_resolver,
    )
    return RunContext(deps=deps, model=None, usage=None, tool_call_approved=approved)  # type: ignore[arg-type]


def test_noop_requires_approval_when_not_approved():
    with pytest.raises(ApprovalRequired):
        noop_module.noop(_ctx(approved=False), text="hello")


def test_noop_echoes_its_input_when_approved():
    result = noop_module.noop(_ctx(approved=True), text="hello")

    assert result == "noop: hello"


def test_noop_is_registered_as_not_read_only():
    # noop "mutates" (it is the stand-in for write/edit/bash), so it is gated and asked.
    assert noop_module.NOOP_TOOL_NAME == "noop"
    assert noop_module.NOOP_READ_ONLY is False
