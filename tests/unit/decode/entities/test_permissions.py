"""Unit tests for the permission domain models (``decode.entities.permissions``).

ADR-0002 §3: a :class:`~decode.entities.permissions.PermissionRequest` describes the gated
tool call the gate is asked about; a :class:`~decode.entities.permissions.PermissionDecision`
is the gate's verdict (``allow`` / ``ask`` / ``deny``) plus the mode it was evaluated under.
These are frozen value objects passed across the loop / gate / TUI boundary, so the tests
pin the shape, the defaults, and immutability.
"""

import dataclasses

import pytest

from decode.entities.permissions import PermissionDecision, PermissionOutcome, PermissionRequest
from decode.permissions.types import PermissionMode


def test_permission_request_carries_tool_name_args_and_read_only():
    request = PermissionRequest(
        tool_name="write_file",
        args="{'path': 'a.txt'}",
        read_only=False,
        tool_call_id="call-1",
    )

    assert request.tool_name == "write_file"
    assert request.args == "{'path': 'a.txt'}"
    assert request.read_only is False
    assert request.tool_call_id == "call-1"


def test_permission_request_read_only_defaults_to_false():
    # Tools opt *in* to read-only; the safe default for an unflagged tool is "mutating".
    request = PermissionRequest(tool_name="noop", args="")

    assert request.read_only is False
    assert request.tool_call_id is None


def test_permission_decision_allow_and_deny_constructors():
    allow = PermissionDecision.allow()
    deny = PermissionDecision.deny(reason="user said no")

    assert allow.outcome is PermissionOutcome.ALLOW
    assert allow.reason is None
    assert deny.outcome is PermissionOutcome.DENY
    assert deny.reason == "user said no"


def test_permission_decision_ask_carries_mode():
    # The gate's verdict in v1 is always ASK under the ASK mode (ADR-0002 §3).
    decision = PermissionDecision.ask(mode=PermissionMode.ASK)

    assert decision.outcome is PermissionOutcome.ASK
    assert decision.mode is PermissionMode.ASK


def test_permission_decision_default_mode_is_ask():
    assert PermissionDecision.allow().mode is PermissionMode.ASK


def test_permission_outcome_has_allow_ask_deny():
    # ADR-0002 §3: the gate returns allow / ask / deny.
    assert {o.value for o in PermissionOutcome} == {"allow", "ask", "deny"}


@pytest.mark.parametrize("model_type", [PermissionRequest, PermissionDecision])
def test_permission_models_are_frozen(model_type):
    assert dataclasses.is_dataclass(model_type)
    assert model_type.__dataclass_params__.frozen is True


def test_permission_request_is_immutable():
    request = PermissionRequest(tool_name="noop", args="")

    with pytest.raises(dataclasses.FrozenInstanceError):
        request.tool_name = "mutated"  # type: ignore[misc]
