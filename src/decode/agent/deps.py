"""The dependency object Pydantic AI injects into the agent run (ADR-0002 §1-3, ADR-0012 §6).

:class:`AgentDeps` is what ``agent.iter(deps=...)`` hands every tool call and instructions hook
as ``ctx.deps``. Field meaning, one line each:

* ``cwd`` — the tool scope: file/search tools + ``bash`` resolve paths against it (the Workspace in a sandbox mode).
* ``harness_home`` — the artifact root (launch cwd) for sessions/memory/skills/settings; defaults to ``cwd``.
* ``emit`` — the sink the loop streams :mod:`decode.entities.events` through to the TUI.
* ``gate`` — the :class:`~decode.permissions.gate.PermissionGate` policy: allow/ask/deny per tool call.
* ``resolve_permission`` — async hook turning a gate *ask* into the human's allow/deny verdict.
* ``resolve_user_question`` — async hook for ``ask_user``; rides the SAME single decision channel (never a second input surface); the headless default raises so it fails cleanly.
* ``task_store`` — the per-run TodoWrite list the ``todo_write`` tool rewrites in place.
* ``active_agent`` — the selected persona (prompt + tool allowlist), reassigned by ``/agent`` and read fresh per turn.
* ``headless_durable_waits`` — headless HITL flag (ADR-0008 §3): mutating tools raise ``ApprovalRequired`` → a durable wait.

Plain callable fields (not methods) let tools and the loop share one event channel and one
decision channel without importing the harness or the TUI.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.entities.task import Task
from decode.permissions.gate import PermissionGate

if TYPE_CHECKING:
    # Typing-only import: a runtime import would form a cycle (agents loader → decode.tools →
    # this module); the __future__ annotations keep the field annotation a string.
    from decode.entities.agent_def import AgentDef


def _default_active_agent() -> AgentDef:
    """The default persona: ``build`` (full tool set; ADR-0003 §7). Lazy import avoids the cycle."""
    from decode.agents.loader import load_agent

    return load_agent("build")


EventSink = Callable[[events.Event], None]
# Resolve a gated tool call into the human's allow/deny verdict (async: blocks on the TUI).
PermissionResolver = Callable[[PermissionRequest], Awaitable[PermissionDecision]]
# Resolve an ``ask_user`` question into the human's typed answer, on the same decision channel.
UserQuestionResolver = Callable[[str], Awaitable[str]]


@dataclass(slots=True)
class AgentDeps:
    """What the agent run carries: cwd, event sink, gate, two decision resolvers, task store.

    Not frozen: the sink may be rebound and ``active_agent`` is reassigned by an ``/agent``
    switch; the factories keep mutable defaults per-instance.
    """

    cwd: Path
    emit: EventSink
    gate: PermissionGate
    resolve_permission: PermissionResolver
    resolve_user_question: UserQuestionResolver
    task_store: list[Task] = field(default_factory=list)
    active_agent: AgentDef = field(default_factory=_default_active_agent)
    headless_durable_waits: bool = False
    # Harness-Home artifact root (ADR-0012 §6); ``None`` defaults to ``cwd`` in __post_init__.
    harness_home: Path | None = None

    def __post_init__(self) -> None:
        """Default ``harness_home`` to ``cwd`` when unset — the back-compat equal-roots case (§6)."""
        if self.harness_home is None:
            self.harness_home = self.cwd
