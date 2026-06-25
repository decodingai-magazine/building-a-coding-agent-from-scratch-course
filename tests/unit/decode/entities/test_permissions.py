"""Unit tests for the permission domain models (``decode.entities.permissions``).

ADR-0003 §1-2: a :class:`~decode.entities.permissions.PermissionRequest` describes the gated
tool call the gate is asked about — now carrying the call's
:class:`~decode.permissions.types.ToolKind` (replacing the M1 ``read_only`` bool); a
:class:`~decode.entities.permissions.PermissionDecision` is the gate's verdict (``allow`` /
``ask`` / ``deny``) plus the :class:`~decode.permissions.types.PermissionMode` it was evaluated
under (default ``DEFAULT``). These are frozen value objects passed across the loop / gate / TUI
boundary, so the tests pin the shape, the defaults, and immutability.
"""

import dataclasses

import pytest

from decode.entities.permissions import PermissionDecision, PermissionOutcome, PermissionRequest
from decode.permissions.types import PermissionMode, ToolKind


def test_permission_request_carries_tool_name_args_and_kind():
    request = PermissionRequest(
        tool_name="write",
        args="{'path': 'a.txt'}",
        kind=ToolKind.FILE_EDIT,
        tool_call_id="call-1",
    )

    assert request.tool_name == "write"
    assert request.args == "{'path': 'a.txt'}"
    assert request.kind is ToolKind.FILE_EDIT
    assert request.tool_call_id == "call-1"


def test_permission_request_kind_defaults_to_other():
    # Unflagged calls are treated as ``OTHER`` (the safe, ask/deny-leaning default).
    request = PermissionRequest(tool_name="noop", args="")

    assert request.kind is ToolKind.OTHER
    assert request.tool_call_id is None


def test_permission_request_subject_defaults_to_empty():
    # The subject (matched against allow/deny rule patterns, task 018) defaults to "".
    request = PermissionRequest(tool_name="noop", args="")

    assert request.subject == ""


def test_permission_request_carries_the_subject():
    request = PermissionRequest(
        tool_name="bash", args='{"command": "rm -rf x"}', subject="rm -rf x"
    )

    assert request.subject == "rm -rf x"


def test_permission_decision_allow_and_deny_constructors():
    allow = PermissionDecision.allow()
    deny = PermissionDecision.deny(reason="user said no")

    assert allow.outcome is PermissionOutcome.ALLOW
    assert allow.reason is None
    assert deny.outcome is PermissionOutcome.DENY
    assert deny.reason == "user said no"


def test_permission_decision_ask_carries_mode():
    decision = PermissionDecision.ask(mode=PermissionMode.DEFAULT)

    assert decision.outcome is PermissionOutcome.ASK
    assert decision.mode is PermissionMode.DEFAULT


def test_permission_decision_default_mode_is_default():
    assert PermissionDecision.allow().mode is PermissionMode.DEFAULT


def test_permission_outcome_has_allow_ask_deny():
    # ADR-0003 §1: the gate returns allow / ask / deny. The ASK *outcome* stays (the ASK *mode*
    # value is the thing that was removed).
    assert {o.value for o in PermissionOutcome} == {"allow", "ask", "deny"}


@pytest.mark.parametrize("model_type", [PermissionRequest, PermissionDecision])
def test_permission_models_are_frozen(model_type):
    assert dataclasses.is_dataclass(model_type)
    assert model_type.__dataclass_params__.frozen is True


def test_permission_request_is_immutable():
    request = PermissionRequest(tool_name="noop", args="")

    with pytest.raises(dataclasses.FrozenInstanceError):
        request.tool_name = "mutated"  # type: ignore[misc]
