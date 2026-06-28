"""The dependency object Pydantic AI injects into the agent run (ADR-0002 §1-3).

Pydantic AI passes whatever you hand to ``agent.iter(deps=...)`` into every tool call and
instruction function as ``ctx.deps``. :class:`AgentDeps` is that object for ``decode``.

It carries:

* ``cwd`` — the working directory the agent operates in (tools resolve paths against it);
* ``emit`` — a sink the loop calls to stream :mod:`decode.entities.events` to the TUI;
* ``gate`` — the :class:`~decode.permissions.gate.PermissionGate` *policy* object (task 005):
  given a tool call it returns allow/ask/deny (always *ask* in v1);
* ``resolve_permission`` — the async hook the loop calls to turn the gate's *ask* into the
  human's terminal allow/deny verdict (task 005). The TUI supplies it; a headless caller
  supplies a safe default (deny). It rides the same single mid-turn decision channel that
  ``resolve_user_question`` does.
* ``resolve_user_question`` — the async hook the ``ask_user`` tool (task 011) calls to ask the
  human a free-form question and get their typed line back as the tool result. It rides the
  **same single** :class:`~decode.harness.decisions.DecisionChannel` as ``resolve_permission``
  (one input surface — never a second ``prompt_async()``); the channel's single-flight invariant
  guarantees a permission ask and an ``ask_user`` ask can never be pending at once. The TUI
  supplies the interactive resolver; a headless caller supplies the default that raises so
  ``ask_user`` fails cleanly (never hangs) when no interactive user is attached.
* ``task_store`` — the per-run TodoWrite task list (task 009): the list the ``todo_write`` tool
  rewrites in place and the loop/TUI render. A plain mutable ``list[Task]`` (not frozen) because
  the model maintains it within a session; each :class:`~decode.entities.task.Task` is itself
  immutable. In-memory and per run — no cross-session persistence (that is later).
* ``active_agent`` — the selected Agent persona (ADR-0003 §7): the :class:`~decode.entities.agent_def.AgentDef`
  whose system prompt the factory's instructions hook injects and whose ``tools`` allowlist the
  factory's per-tool ``prepare=`` callback reads to hide disallowed tools. **Mutable** (the field
  is not frozen): the agent-selection helper / a ``/agent`` switch reassigns it and the loop/factory
  read it fresh per turn, so switching agents changes the prompt + tool set on the next turn with no
  agent rebuild. Defaults to the ``build`` persona (the full-tool set) so a deps built without one
  behaves exactly as Milestone 1 did.
* ``headless_durable_waits`` — the Headless Runtime HITL flag (ADR-0008 §3). ``False`` for every
  interactive run (and the 058 bypass run), so :func:`decode.tools.approval.needs_approval` keeps
  its mode-binary behaviour byte-for-byte. ``True`` **only** in the gating headless flow (task 059):
  there is no interactive loop to run the gate, so a gated tool must decide *itself* whether to
  pause — read-only tools run inline, mutating tools raise ``ApprovalRequired`` which the Kitaru
  adapter turns into a durable ``kitaru.wait()``. Defaults ``False`` so nothing outside the runtime
  changes.

Later tasks widen this dataclass further (the session log in 014) — those fields land with the
task that uses them. Keeping ``emit`` / ``resolve_permission`` / ``resolve_user_question`` plain
callable fields (not methods) means tools and the loop share one event channel and one decision
channel without importing the harness or the TUI.
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
    # Imported only for typing: a runtime import here would form a cycle (the agents loader pulls
    # in ``decode.tools``, whose registry imports this module). ``from __future__ import
    # annotations`` keeps the field annotation a string, so the runtime never needs the symbol.
    from decode.entities.agent_def import AgentDef


def _default_active_agent() -> AgentDef:
    """The startup default Agent persona: the ``build`` persona (the full tool set; ADR-0003 §7).

    Imported lazily (not at module level) to avoid an import cycle: the agents loader pulls in
    :mod:`decode.tools`, whose registry imports this module. A deps built without an explicit
    ``active_agent`` thus behaves as Milestone 1 did (every tool available, ``default`` mode).
    """
    from decode.agents.loader import load_agent

    return load_agent("build")


EventSink = Callable[[events.Event], None]
# The decision channel: given a gated tool call, resolve the human's allow/deny verdict.
# Async because asking the human is I/O (it blocks on the TUI / a queue). It rides the same
# single mid-turn decision channel ``UserQuestionResolver`` does.
PermissionResolver = Callable[[PermissionRequest], Awaitable[PermissionDecision]]
# The same decision channel, for the ``ask_user`` tool (task 011): given the model's free-form
# question, resolve the human's typed answer line. Async (it blocks the turn on the human via
# the single input surface). The headless default raises so ``ask_user`` fails cleanly instead
# of hanging when no interactive user is attached.
UserQuestionResolver = Callable[[str], Awaitable[str]]


@dataclass(slots=True)
class AgentDeps:
    """What the agent run carries: cwd, event sink, gate, two decision resolvers, task store.

    Not frozen: the sink may be rebound (e.g. per turn) and the same object accretes mutable
    collaborators across tasks (gate; the ``task_store`` the ``todo_write`` tool rewrites; the
    ``active_agent`` an ``/agent`` switch reassigns; the session log later). ``task_store`` uses a
    ``default_factory`` so each run gets its own empty list (no mutable-default aliasing across
    instances); ``active_agent`` likewise defaults (via a factory) to the ``build`` persona.
    """

    cwd: Path
    emit: EventSink
    gate: PermissionGate
    resolve_permission: PermissionResolver
    resolve_user_question: UserQuestionResolver
    task_store: list[Task] = field(default_factory=list)
    active_agent: AgentDef = field(default_factory=_default_active_agent)
    headless_durable_waits: bool = False
