"""The bypass-aware gated-tool approval predicate (ADR-0003 §1,3; ADR-0008 §2,3).

:func:`decode.tools.approval.needs_approval` is the single guard every gated tool opens with. It
must keep the interactive modes deferring (so decode's loop resolves them through the gate), let the
headless ``bypass`` posture run tools inline (so ``KitaruAgent.run_sync`` executes them instead of
the Kitaru adapter turning the deferral into a wait), and — in the **headless HITL** posture
(``headless_durable_waits``) — apply the read-only-allow floor itself so read-only tools run inline
while mutating tools defer into a durable approval wait.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode
from decode.tools.approval import needs_approval


def _ctx(
    *,
    approved: bool,
    mode: PermissionMode,
    headless_durable_waits: bool = False,
    tool_name: str = "write",
):
    """A minimal stand-in for ``RunContext[AgentDeps]`` carrying just what the predicate reads."""
    return SimpleNamespace(
        tool_call_approved=approved,
        tool_name=tool_name,
        deps=SimpleNamespace(
            gate=PermissionGate(mode=mode), headless_durable_waits=headless_durable_waits
        ),
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


@pytest.mark.parametrize("tool_name", ["read", "glob", "grep", "web_fetch", "todo_write", "lsp"])
def test_headless_durable_waits_runs_read_only_tools_inline(tool_name):
    """Headless HITL: a read-only tool runs inline (the gate would auto-allow it — no durable wait)."""
    ctx = _ctx(
        approved=False,
        mode=PermissionMode.DEFAULT,
        headless_durable_waits=True,
        tool_name=tool_name,
    )
    assert needs_approval(ctx) is False


@pytest.mark.parametrize("tool_name", ["write", "edit", "bash"])
def test_headless_durable_waits_defers_mutating_tools(tool_name):
    """Headless HITL: a mutating tool defers so the adapter turns its ApprovalRequired into a wait."""
    ctx = _ctx(
        approved=False,
        mode=PermissionMode.DEFAULT,
        headless_durable_waits=True,
        tool_name=tool_name,
    )
    assert needs_approval(ctx) is True
