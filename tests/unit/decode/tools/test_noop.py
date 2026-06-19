"""Unit tests for the trivial gated ``noop`` tool (``decode.tools.noop``).

ADR-0002 §3,7: ``noop`` is the *one* gated tool task 005 ships to exercise the whole
permission-gate-via-deferred-tools path end to end (real file/bash tools are 006-008). It
echoes its input but **requires approval**: it raises
:class:`pydantic_ai.ApprovalRequired` whenever the run context has not been approved, which
is what makes a leg resolve to ``DeferredToolRequests`` so the gate can ask the human.

These tests pin the tool's two states (unapproved → raises; approved → echoes) and its
read-only registration, without going through the model.
"""

from pathlib import Path

import pytest
from pydantic_ai import ApprovalRequired, RunContext

from decode.agent.deps import AgentDeps
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.gate import PermissionGate
from decode.tools import noop as noop_module


async def _deny_resolver(request: PermissionRequest) -> PermissionDecision:
    return PermissionDecision.deny()


def _ctx(*, approved: bool) -> RunContext[AgentDeps]:
    deps = AgentDeps(
        cwd=Path("."),
        emit=lambda _e: None,
        gate=PermissionGate(),
        resolve_permission=_deny_resolver,
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
