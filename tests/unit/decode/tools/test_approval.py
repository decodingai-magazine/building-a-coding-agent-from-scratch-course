"""The bypass-aware gated-tool approval predicate (ADR-0003 §1,3; ADR-0008 §2).

:func:`decode.tools.approval.needs_approval` is the single guard every gated tool opens with. It
must keep the interactive modes deferring (so decode's loop resolves them through the gate) while
letting the headless ``bypass`` posture run tools inline (so ``KitaruAgent.run_sync`` executes them
instead of the Kitaru adapter turning the deferral into a wait).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode
from decode.tools.approval import needs_approval


def _ctx(*, approved: bool, mode: PermissionMode):
    """A minimal stand-in for ``RunContext[AgentDeps]`` carrying just what the predicate reads."""
    return SimpleNamespace(
        tool_call_approved=approved,
        deps=SimpleNamespace(gate=PermissionGate(mode=mode)),
    )


@pytest.mark.parametrize(
    "mode",
    [PermissionMode.DEFAULT, PermissionMode.PLAN, PermissionMode.EDIT],
)
def test_unapproved_call_defers_in_every_non_bypass_mode(mode):
    """An unapproved call still raises (defers) under default / plan / edit — interactive path."""
    assert needs_approval(_ctx(approved=False, mode=mode)) is True


def test_unapproved_call_runs_inline_under_bypass():
    """Under BYPASS an unapproved call does NOT defer — it runs inline (headless runtime path)."""
    assert needs_approval(_ctx(approved=False, mode=PermissionMode.BYPASS)) is False


@pytest.mark.parametrize(
    "mode",
    [PermissionMode.DEFAULT, PermissionMode.PLAN, PermissionMode.EDIT, PermissionMode.BYPASS],
)
def test_an_approved_call_never_defers(mode):
    """Once approved (a resume leg), the tool runs in any mode — the deferral is already resolved."""
    assert needs_approval(_ctx(approved=True, mode=mode)) is False
