"""The gated ``todo_write`` tool — the in-memory TodoWrite task list (ADR-0002 §7).

``todo_write`` lets the model maintain a short to-do list **within a session** so it (and the
developer watching the TUI) can track multi-step work. It follows TodoWrite *replace* semantics:
the model passes the **full desired list** every call and the tool overwrites the store with it —
there are no incremental add/remove/complete operations. Each item is a
:class:`~decode.entities.task.Task` (``id`` / ``content`` / ``status``), validated on construction,
so a malformed status never reaches the store.

The store is :data:`decode.agent.deps.AgentDeps.task_store` — a per-run ``list[Task]`` the tool
rewrites **in place** (clear + extend, so the same list object the loop/TUI hold stays current),
then announces with a :class:`~decode.entities.events.TaskListUpdated` event so the TUI redraws the
checklist. The event carries already-status-marked lines (``[x]`` completed, ``[~]`` in progress,
``[ ]`` pending) so the renderer shows a sensible checklist without re-deriving the markers.

It self-gates via :class:`pydantic_ai.ApprovalRequired` (like every tool that takes the deferred
path), but it is classified :class:`~decode.permissions.types.ToolKind.READ_ONLY` in the registry
(ADR-0003 §2): an in-memory checklist has no disk/exec side effect, so the gate **auto-allows** it
under every mode — it must stay usable in plan mode (where the plan agent builds its checklist) and
need not prompt anywhere. In-memory, per-run only — no cross-session persistence (later).
"""

from __future__ import annotations

import logging

from pydantic_ai import ApprovalRequired, RunContext

from decode.agent.deps import AgentDeps
from decode.entities import events
from decode.entities.task import Task

logger = logging.getLogger(__name__)

TODO_WRITE_TOOL_NAME = "todo_write"
# An in-memory checklist with no disk/exec side effect → READ_ONLY (ADR-0003 §2): the gate
# auto-allows it under every mode (incl. plan), so it never prompts.
TODO_WRITE_READ_ONLY = True

# Status -> checklist marker the TUI renders. A small, stable mapping so the renderer stays a pure
# string formatter and never has to know the Task status vocabulary.
_STATUS_MARKERS: dict[str, str] = {
    "pending": "[ ]",
    "in_progress": "[~]",
    "completed": "[x]",
}


def todo_write(ctx: RunContext[AgentDeps], tasks: list[Task]) -> str:
    """Replace the session task list with ``tasks`` and redraw it in the TUI (ADR-0002 §7).

    ``tasks`` is the **full desired list** (TodoWrite replace semantics — pass every task every
    call, not just the changes); each item is validated by :class:`~decode.entities.task.Task`.
    The per-run store on ``ctx.deps.task_store`` is overwritten in place and a
    :class:`~decode.entities.events.TaskListUpdated` event is emitted so the checklist redraws.

    Gated (ADR-0002 §3): raises :class:`pydantic_ai.ApprovalRequired` until the call is approved —
    and *before* the store is touched — so a denied call never mutates the task list. Returns a
    short confirmation string the model sees on its next leg.
    """
    if not ctx.tool_call_approved:
        logger.debug("todo_write requires approval (%d task(s))", len(tasks))
        raise ApprovalRequired

    # Replace in place so the loop/TUI keep the same list object (clear + extend, not rebind).
    ctx.deps.task_store[:] = tasks
    ctx.deps.emit(events.TaskListUpdated(tasks=_checklist_lines(tasks)))
    logger.debug("todo_write replaced the task store with %d task(s)", len(tasks))
    return f"Updated task list ({len(tasks)} task(s))."


def _checklist_lines(tasks: list[Task]) -> tuple[str, ...]:
    """Render each task as a status-marked checklist line for the TaskListUpdated event.

    e.g. ``Task(id="2", content="build", status="in_progress")`` -> ``"[~] build"``. The TUI
    renderer then shows these lines verbatim, so the status vocabulary lives here (next to the
    Task model) rather than being re-derived in the TUI.
    """
    return tuple(f"{_STATUS_MARKERS[task.status]} {task.content}" for task in tasks)
