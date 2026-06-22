"""Unit tests for :class:`decode.permissions.gate.PermissionGate`.

ADR-0002 §3: the gate is the **policy** object. Its v1 policy is *ask on every tool call*
— ``check()`` always returns ``ASK`` under the ``ASK`` mode, regardless of the tool's
``read_only`` flag (the flag is recorded for M3's auto-allow but ignored for the v1
decision). The gate does **not** own the terminal UI; resolving an ASK into allow/deny is
the resolver's job.
"""

import pytest

from decode.entities.permissions import PermissionOutcome, PermissionRequest
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode


@pytest.fixture
def gate() -> PermissionGate:
    return PermissionGate()


def test_gate_always_asks_for_a_mutating_tool(gate):
    decision = gate.check(PermissionRequest(tool_name="write_file", args="", read_only=False))

    assert decision.outcome is PermissionOutcome.ASK
    assert decision.mode is PermissionMode.ASK


def test_gate_still_asks_for_a_read_only_tool_in_v1(gate):
    # read_only is recorded but NOT auto-allowed in v1 (that is M3). It still asks.
    decision = gate.check(PermissionRequest(tool_name="read_file", args="", read_only=True))

    assert decision.outcome is PermissionOutcome.ASK


def test_gate_mode_is_ask(gate):
    assert gate.mode is PermissionMode.ASK


def test_gate_check_never_returns_allow_or_deny_in_v1(gate):
    # The gate never auto-decides in v1; the human (via the resolver) does.
    for read_only in (True, False):
        decision = gate.check(PermissionRequest(tool_name="any", args="x", read_only=read_only))
        assert decision.outcome is PermissionOutcome.ASK
