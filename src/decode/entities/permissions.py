"""Shared value objects for the permission gate (ADR-0003 §1-3).

Two frozen models cross the loop / gate / TUI boundary:

* :class:`PermissionRequest` — *what* the gate is being asked about: the tool name, a
  human-readable argument summary, and the call's :class:`~decode.permissions.types.ToolKind`
  (which, with the active mode, decides allow/ask/deny). It carries the Pydantic AI
  ``tool_call_id`` so a decision can be routed back to the exact deferred call it answers.
* :class:`PermissionDecision` — the gate's verdict: an :class:`PermissionOutcome`
  (``allow`` / ``ask`` / ``deny``) plus the :class:`~decode.permissions.types.PermissionMode`
  it was evaluated under (default ``DEFAULT``), and an optional human-facing ``reason`` (e.g. a
  denial message fed back to the model). An ``ALLOW`` / ``DENY`` outcome may come straight from
  the gate (auto-allow / auto-deny by mode) or from the human via the resolver on an ``ASK``.

These live in ``entities/`` (not ``permissions/``) because they are the contract the agent
loop, the gate, and the TUI all share — frozen + slotted so they are cheap and safe to pass
across the queue/stream boundary, mirroring :mod:`decode.entities.events`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from decode.permissions.types import PermissionMode, ToolKind


class PermissionOutcome(enum.Enum):
    """The three verdicts the gate can return (ADR-0003 §1).

    ``ASK`` means "route to the human". ``ALLOW`` / ``DENY`` are terminal verdicts the gate may
    decide directly (mode-driven auto-allow / auto-deny) or that the resolver produces from the
    human's answer. (Note: this is the *outcome* ``ASK`` — the M1 ``ASK`` *mode* value is gone.)
    """

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """A gated tool call the gate (and possibly the human) is asked to approve.

    ``args`` is an already-rendered, human-readable summary of the call arguments (the loop
    serializes the tool-call args before constructing the request — the gate never sees raw
    argument objects). ``kind`` is the tool's :class:`~decode.permissions.types.ToolKind`: the
    gate evaluates it against the active mode (default ``OTHER`` — the safe, ask/deny-leaning
    classification for an unclassified call). ``subject`` is the per-kind string that allow/deny
    Permission Rules glob against (ADR-0003 §4): ``bash`` → the command, file tools → the path,
    ``web_fetch`` → the url, everything else → the tool name; the loop fills it via
    :func:`decode.permissions.rules.subject_for` and it defaults to ``""``. ``tool_call_id`` ties
    the request to the Pydantic AI deferred call it came from (``None`` for ad-hoc checks).
    """

    tool_name: str
    args: str
    kind: ToolKind = ToolKind.OTHER
    subject: str = ""
    tool_call_id: str | None = None

    @property
    def read_only(self) -> bool:
        """Whether this call is read-only (derived: ``kind is READ_ONLY``)."""
        return self.kind is ToolKind.READ_ONLY


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    """The gate's verdict on a :class:`PermissionRequest` (ADR-0003 §1,3).

    ``outcome`` is the verdict; ``mode`` is the :class:`~decode.permissions.types.PermissionMode`
    it was evaluated under (default ``DEFAULT``); ``reason`` is an optional human-facing message
    — used for the denial message fed back to the model. Construct via the :meth:`allow` /
    :meth:`ask` / :meth:`deny` factories rather than the raw fields.
    """

    outcome: PermissionOutcome
    mode: PermissionMode = PermissionMode.DEFAULT
    reason: str | None = None

    @classmethod
    def allow(cls, *, mode: PermissionMode = PermissionMode.DEFAULT) -> PermissionDecision:
        """A terminal *allow* verdict (the tool will execute)."""
        return cls(outcome=PermissionOutcome.ALLOW, mode=mode)

    @classmethod
    def deny(
        cls, *, mode: PermissionMode = PermissionMode.DEFAULT, reason: str | None = None
    ) -> PermissionDecision:
        """A terminal *deny* verdict (a denial message is returned to the model)."""
        return cls(outcome=PermissionOutcome.DENY, mode=mode, reason=reason)

    @classmethod
    def ask(cls, *, mode: PermissionMode = PermissionMode.DEFAULT) -> PermissionDecision:
        """An *ask* verdict: defer to the human (mutating tools under default/edit)."""
        return cls(outcome=PermissionOutcome.ASK, mode=mode)
