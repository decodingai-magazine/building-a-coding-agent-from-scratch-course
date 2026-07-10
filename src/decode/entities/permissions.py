"""Shared value objects for the permission gate (ADR-0003 §1-3).

:class:`PermissionRequest` (what the gate is asked about) and :class:`PermissionDecision` (its
verdict) are the frozen models crossing the loop / gate / TUI boundary. They live in
``entities/`` (not ``permissions/``) because all three layers share them.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from decode.permissions.types import PermissionMode, ToolKind


class PermissionOutcome(enum.Enum):
    """The three verdicts the gate can return (ADR-0003 §1).

    ``ASK`` routes to the human; ``ALLOW`` / ``DENY`` are terminal (from the gate directly, or
    from the resolver on an ``ASK``).
    """

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """A gated tool call the gate (and possibly the human) is asked to approve.

    ``args`` is an already-rendered summary (the gate never sees raw argument objects); ``kind``
    defaults to the safe ``OTHER``; ``subject`` is the per-kind string Permission Rules glob
    against (filled via :func:`decode.permissions.rules.subject_for`); ``tool_call_id`` ties the
    request to the Pydantic AI deferred call it came from (``None`` for ad-hoc checks).
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

    ``reason`` is an optional human-facing message (e.g. the denial fed back to the model).
    Construct via the :meth:`allow` / :meth:`ask` / :meth:`deny` factories.
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
