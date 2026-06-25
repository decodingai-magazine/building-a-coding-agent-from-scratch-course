"""The permission gate: the allow/ask/deny **policy** object (ADR-0003 §1,3).

:class:`PermissionGate` answers one question — given a
:class:`~decode.entities.permissions.PermissionRequest`, should the tool run? In Milestone 2 the
gate is a **real decision**: :meth:`PermissionGate.check` evaluates the active
:class:`~decode.permissions.types.PermissionMode` against the request's
:class:`~decode.permissions.types.ToolKind` and returns ALLOW / ASK / DENY (ADR-0003 §1). No
project/agent rules yet — that precedence layer (``deny → allow → mode``) lands in task 018; this
ships the *mode decision* floor. The mode is mutable via :meth:`set_mode`.

The gate is **policy only** — it does *not* prompt the user or own the terminal UI. On an ``ASK``
the loop turns the verdict into the human's terminal allow/deny via the resolver on
:class:`~decode.agent.deps.AgentDeps`; an ``ALLOW`` / ``DENY`` the gate decides directly runs (or
refuses) the tool with no prompt.
"""

from __future__ import annotations

import logging

from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.permissions.types import PermissionMode, ToolKind

logger = logging.getLogger(__name__)

# The reason returned when a mutating tool is denied in plan mode — it tells the model to present
# its plan and call ``exit_plan_mode`` rather than try to act.
_PLAN_DENY_REASON = "Plan mode is read-only — present your plan and call exit_plan_mode."


class PermissionGate:
    """Decide allow/ask/deny for a tool call by mode x kind (ADR-0003 §1,3).

    Holds the active :class:`~decode.permissions.types.PermissionMode` (mutable via
    :meth:`set_mode`); the gate default is ``DEFAULT``. Stateless beyond that mode, so one
    instance is shared for a whole session.
    """

    def __init__(self, mode: PermissionMode = PermissionMode.DEFAULT) -> None:
        self._mode = mode

    @property
    def mode(self) -> PermissionMode:
        """The mode the gate evaluates under (``DEFAULT`` at startup)."""
        return self._mode

    def set_mode(self, mode: PermissionMode) -> None:
        """Switch the active mode (the TUI / orchestration tools mutate it mid-session)."""
        logger.debug("gate mode %s -> %s", self._mode.value, mode.value)
        self._mode = mode

    def check(self, request: PermissionRequest) -> PermissionDecision:
        """Return the gate's verdict for ``request`` by evaluating mode x kind (ADR-0003 §1).

        * ``BYPASS`` → ALLOW everything.
        * read-only kind → ALLOW under every mode.
        * ``PLAN`` → DENY any mutation (reason points at ``exit_plan_mode``).
        * ``EDIT`` → ALLOW file edits, ASK for other mutations (``bash``).
        * ``DEFAULT`` → ASK for any mutation.
        """
        decision = self._decide(request.kind)
        logger.debug(
            "gate.check tool=%s kind=%s mode=%s -> %s",
            request.tool_name,
            request.kind.value,
            self._mode.value,
            decision.outcome.value,
        )
        return decision

    def _decide(self, kind: ToolKind) -> PermissionDecision:
        """The pure mode x kind decision (no rules yet — task 018 adds the rule layer)."""
        mode = self._mode
        if mode is PermissionMode.BYPASS:
            return PermissionDecision.allow(mode=mode)
        if kind is ToolKind.READ_ONLY:
            return PermissionDecision.allow(mode=mode)
        # A mutating tool (FILE_EDIT or OTHER) below this point.
        if mode is PermissionMode.PLAN:
            return PermissionDecision.deny(mode=mode, reason=_PLAN_DENY_REASON)
        if mode is PermissionMode.EDIT and kind is ToolKind.FILE_EDIT:
            return PermissionDecision.allow(mode=mode)
        # DEFAULT (any mutation) and EDIT (non-file-edit, i.e. bash) ask the human.
        return PermissionDecision.ask(mode=mode)
