"""Probe 08 — a genuinely multi-step ask is planned with ``todo_write`` (ADR-0002 §7; ADR-0017 §2,6).

Planning discipline (ADR-0002): faced with a task that has several distinct steps, the agent should
lay them out as a TodoWrite list before diving in, so its plan is visible and trackable. A small CLI
module is seeded and the prompt asks for three separate, non-trivial changes. The run passes when
``todo_write`` WAS called AND that call carried at least three items — proving the model actually
decomposed the work, not just called the tool with a single lump. :class:`ToolArgsMetric` inspects the
recorded ``tasks`` argument for the ``>= 3`` count; :class:`ToolCalledMetric` guards the tool was used
at all. Runs under the default ``BYPASS`` gate — ``todo_write`` is READ_ONLY and needs no approval.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evals.harness.metrics import MaxStepsMetric, ToolArgsMetric, ToolCalledMetric
from evals.regression.probe import RegressionProbe

_APP = "app.py"
_APP_BODY = (
    "import sys\n\n\ndef main():\n    print('hello')\n\n\nif __name__ == '__main__':\n    main()\n"
)

# The threshold for a "genuinely multi-step" plan: fewer than three items is not a decomposition.
_MIN_TODO_ITEMS = 3


def _has_min_items(args: dict[str, Any]) -> bool:
    """Whether the ``todo_write`` call carried at least :data:`_MIN_TODO_ITEMS` tasks."""
    tasks = args.get("tasks")
    return isinstance(tasks, list) and len(tasks) >= _MIN_TODO_ITEMS


def _fixture(workspace: Path) -> None:
    """Seed the small CLI module the multi-step change is planned against."""
    (workspace / _APP).write_text(_APP_BODY, encoding="utf-8")


PROBE = RegressionProbe(
    id="08-todo-planning",
    prompt=(
        f"I need three changes to {_APP}: add a --verbose flag, add input validation for the CLI "
        "arguments, and add a unit test covering main(). Plan the work as a todo list before you "
        "start."
    ),
    fixture=_fixture,
    metrics=[
        ToolCalledMetric("todo_write"),
        ToolArgsMetric(
            "todo_write",
            _has_min_items,
            description=f"at least {_MIN_TODO_ITEMS} todo items",
            name="todo_write_has_3_items",
        ),
        MaxStepsMetric(),
    ],
    max_requests=6,
    tags=["planning", "todo-write"],
)
