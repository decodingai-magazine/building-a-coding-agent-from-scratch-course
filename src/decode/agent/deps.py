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

Later tasks widen this dataclass further (the session log in 014) — those fields land with the
task that uses them. Keeping ``emit`` / ``resolve_permission`` / ``resolve_user_question`` plain
callable fields (not methods) means tools and the loop share one event channel and one decision
channel without importing the harness or the TUI.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.entities.task import Task
from decode.permissions.gate import PermissionGate

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
    session log later). ``task_store`` uses a ``default_factory`` so each run gets its own empty
    list (no mutable-default aliasing across instances).
    """

    cwd: Path
    emit: EventSink
    gate: PermissionGate
    resolve_permission: PermissionResolver
    resolve_user_question: UserQuestionResolver
    task_store: list[Task] = field(default_factory=list)
