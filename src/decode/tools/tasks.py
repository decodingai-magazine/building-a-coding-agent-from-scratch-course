"""The gated ``todo_write`` tool — the in-memory TodoWrite task list (ADR-0002 §7).

TodoWrite *replace* semantics: the model passes the full desired list every call and the tool
rewrites ``AgentDeps.task_store`` in place, then emits a ``TaskListUpdated`` event with
status-marked lines so the TUI redraws. Self-gates via :class:`pydantic_ai.ApprovalRequired` but
is classified READ_ONLY (no disk/exec side effect), so the gate auto-allows it in every mode.
In-memory, per-run only.
"""

from __future__ import annotations

import logging

from pydantic_ai import ApprovalRequired, RunContext

from decode.agent.deps import AgentDeps
from decode.entities import events
from decode.entities.task import Task
from decode.tools.approval import needs_approval

logger = logging.getLogger(__name__)

TODO_WRITE_TOOL_NAME = "todo_write"

# Status -> checklist marker, so the TUI renderer never has to know the Task status vocabulary.
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
    if needs_approval(ctx):
        logger.debug("todo_write requires approval (%d task(s))", len(tasks))
        raise ApprovalRequired

    # Replace in place so the loop/TUI keep the same list object (clear + extend, not rebind).
    ctx.deps.task_store[:] = tasks
    ctx.deps.emit(events.TaskListUpdated(tasks=_checklist_lines(tasks)))
    logger.debug("todo_write replaced the task store with %d task(s)", len(tasks))
    return f"Updated task list ({len(tasks)} task(s))."


def _checklist_lines(tasks: list[Task]) -> tuple[str, ...]:
    """Render each task as a status-marked checklist line (e.g. ``"[~] build"``) for the event."""
    return tuple(f"{_STATUS_MARKERS[task.status]} {task.content}" for task in tasks)
