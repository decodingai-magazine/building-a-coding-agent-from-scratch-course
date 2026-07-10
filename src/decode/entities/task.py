"""The in-memory TodoWrite task item the model maintains within a session (ADR-0002 §7).

:class:`Task` is one entry in the per-run list the ``todo_write`` tool rewrites — frozen +
slotted like the other entities, but validating ``status`` loudly since the model supplies it.
The list is mutable per run; each :class:`Task` is immutable. Cross-session persistence is out
of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

# The three states a task can be in. ``get_args`` derives the validation set from this single
# annotation so the allowed values are declared in exactly one place.
TaskStatus = Literal["pending", "in_progress", "completed"]
_VALID_STATUSES: frozenset[str] = frozenset(get_args(TaskStatus))


@dataclass(frozen=True, slots=True)
class Task:
    """One TodoWrite task: a short ``content`` description with a tracked ``status`` (ADR-0002 §7).

    ``id`` is model-supplied; ``status`` defaults to ``pending``. Construction validates both
    ``status`` and ``content`` so the model cannot store a malformed task.
    """

    id: str
    content: str
    status: TaskStatus = "pending"

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            allowed = ", ".join(sorted(_VALID_STATUSES))
            raise ValueError(f"invalid task status {self.status!r}; expected one of: {allowed}")
        if not self.content.strip():
            raise ValueError("task content must be a non-empty description")
