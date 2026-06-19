"""Shared value objects for the permission gate (ADR-0002 §3).

Two frozen models cross the loop / gate / TUI boundary:

* :class:`PermissionRequest` — *what* the gate is being asked about: the tool name, a
  human-readable argument summary, and the tool's ``read_only`` flag (recorded for M3's
  auto-allow, ignored by the v1 decision). It carries the Pydantic AI ``tool_call_id`` so a
  decision can be routed back to the exact deferred call it answers.
* :class:`PermissionDecision` — the gate's verdict: an :class:`PermissionOutcome`
  (``allow`` / ``ask`` / ``deny``) plus the :class:`~decode.permissions.types.PermissionMode`
  it was evaluated under, and an optional human-facing ``reason`` (e.g. a denial message fed
  back to the model). The v1 gate always returns ``ask``; the terminal allow/deny verdict
  comes from the human via the resolver and is built with :meth:`PermissionDecision.allow` /
  :meth:`PermissionDecision.deny`.

These live in ``entities/`` (not ``permissions/``) because they are the contract the agent
loop, the gate, and the TUI all share — frozen + slotted so they are cheap and safe to pass
across the queue/stream boundary, mirroring :mod:`decode.entities.events`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from decode.permissions.types import PermissionMode


class PermissionOutcome(enum.Enum):
    """The three verdicts the gate can return (ADR-0002 §3).

    ``ASK`` means "route to the human" — the v1 gate always returns this. ``ALLOW`` / ``DENY``
    are the terminal verdicts the resolver produces from the human's answer (and the seam
    M3's auto-allow modes will return directly without asking).
    """

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """A gated tool call the gate (and then the human) is asked to approve.

    ``args`` is an already-rendered, human-readable summary of the call arguments (the loop
    serializes the tool-call args before constructing the request — the gate never sees raw
    argument objects). ``read_only`` is the tool's self-declared flag: recorded here for M3's
    read-only auto-allow but ignored by the v1 ask-everything policy. ``tool_call_id`` ties
    the request to the Pydantic AI deferred call it came from (``None`` for ad-hoc checks).
    """

    tool_name: str
    args: str
    read_only: bool = False
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    """The gate's verdict on a :class:`PermissionRequest` (ADR-0002 §3).

    ``outcome`` is the verdict; ``mode`` is the :class:`~decode.permissions.types.PermissionMode`
    it was evaluated under (always ``ASK`` in v1); ``reason`` is an optional human-facing
    message — used for the denial message fed back to the model. Construct via the
    :meth:`allow` / :meth:`ask` / :meth:`deny` factories rather than the raw fields.
    """

    outcome: PermissionOutcome
    mode: PermissionMode = PermissionMode.ASK
    reason: str | None = None

    @classmethod
    def allow(
        cls, *, mode: PermissionMode = PermissionMode.ASK, reason: str | None = None
    ) -> PermissionDecision:
        """A terminal *allow* verdict (the tool will execute)."""
        return cls(outcome=PermissionOutcome.ALLOW, mode=mode, reason=reason)

    @classmethod
    def deny(
        cls, *, mode: PermissionMode = PermissionMode.ASK, reason: str | None = None
    ) -> PermissionDecision:
        """A terminal *deny* verdict (a denial message is returned to the model)."""
        return cls(outcome=PermissionOutcome.DENY, mode=mode, reason=reason)

    @classmethod
    def ask(cls, *, mode: PermissionMode = PermissionMode.ASK) -> PermissionDecision:
        """An *ask* verdict: defer to the human (the v1 gate's only answer)."""
        return cls(outcome=PermissionOutcome.ASK, mode=mode)
