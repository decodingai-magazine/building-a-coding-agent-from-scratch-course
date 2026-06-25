"""Unit tests for :class:`decode.permissions.gate.PermissionGate`.

ADR-0003 §1,3: the gate is now a **real decision**. ``check(request)`` evaluates the active
:class:`~decode.permissions.types.PermissionMode` against the request's
:class:`~decode.permissions.types.ToolKind` and returns ALLOW / ASK / DENY (no rules yet — that
is task 018). The mode is mutable via :meth:`PermissionGate.set_mode`. The gate is still
policy-only: it never owns the terminal UI; turning an ASK into a human verdict is the resolver's
job.
"""

import pytest

from decode.entities.permissions import PermissionOutcome, PermissionRequest
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode, ToolKind


@pytest.fixture
def gate() -> PermissionGate:
    return PermissionGate()


def _request(kind: ToolKind) -> PermissionRequest:
    return PermissionRequest(tool_name="t", args="", kind=kind)


def test_gate_defaults_to_default_mode(gate):
    assert gate.mode is PermissionMode.DEFAULT


# --- read-only requests auto-ALLOW under every mode (ADR-0003 §1) ---------------------------


@pytest.mark.parametrize(
    "mode",
    [PermissionMode.DEFAULT, PermissionMode.PLAN, PermissionMode.EDIT, PermissionMode.BYPASS],
)
def test_read_only_request_allows_under_every_mode(gate, mode):
    gate.set_mode(mode)

    decision = gate.check(_request(ToolKind.READ_ONLY))

    assert decision.outcome is PermissionOutcome.ALLOW
    assert decision.mode is mode


# --- file-edit requests: ASK (default), ALLOW (edit), DENY (plan), ALLOW (bypass) -----------


def test_file_edit_asks_under_default(gate):
    decision = gate.check(_request(ToolKind.FILE_EDIT))

    assert decision.outcome is PermissionOutcome.ASK
    assert decision.mode is PermissionMode.DEFAULT


def test_file_edit_allows_under_edit(gate):
    gate.set_mode(PermissionMode.EDIT)

    decision = gate.check(_request(ToolKind.FILE_EDIT))

    assert decision.outcome is PermissionOutcome.ALLOW


def test_file_edit_denies_under_plan_with_exit_plan_mode_reason(gate):
    gate.set_mode(PermissionMode.PLAN)

    decision = gate.check(_request(ToolKind.FILE_EDIT))

    assert decision.outcome is PermissionOutcome.DENY
    assert decision.reason is not None
    assert "exit_plan_mode" in decision.reason


def test_file_edit_allows_under_bypass(gate):
    gate.set_mode(PermissionMode.BYPASS)

    decision = gate.check(_request(ToolKind.FILE_EDIT))

    assert decision.outcome is PermissionOutcome.ALLOW


# --- other (bash) requests: ASK (default), ASK (edit), DENY (plan), ALLOW (bypass) ----------


def test_other_asks_under_default(gate):
    decision = gate.check(_request(ToolKind.OTHER))

    assert decision.outcome is PermissionOutcome.ASK


def test_other_asks_under_edit(gate):
    # Edit mode auto-allows file edits but NOT other mutating tools (bash) — those still ask.
    gate.set_mode(PermissionMode.EDIT)

    decision = gate.check(_request(ToolKind.OTHER))

    assert decision.outcome is PermissionOutcome.ASK


def test_other_denies_under_plan_with_exit_plan_mode_reason(gate):
    gate.set_mode(PermissionMode.PLAN)

    decision = gate.check(_request(ToolKind.OTHER))

    assert decision.outcome is PermissionOutcome.DENY
    assert decision.reason is not None
    assert "exit_plan_mode" in decision.reason


def test_other_allows_under_bypass(gate):
    gate.set_mode(PermissionMode.BYPASS)

    decision = gate.check(_request(ToolKind.OTHER))

    assert decision.outcome is PermissionOutcome.ALLOW


# --- the mode is mutable (ADR-0003 §3) ------------------------------------------------------


def test_set_mode_makes_the_mode_mutable(gate):
    gate.set_mode(PermissionMode.PLAN)

    assert gate.mode is PermissionMode.PLAN
    # A read-only call is still allowed; a bash call is now denied (plan is read-only).
    assert gate.check(_request(ToolKind.READ_ONLY)).outcome is PermissionOutcome.ALLOW
    assert gate.check(_request(ToolKind.OTHER)).outcome is PermissionOutcome.DENY
