"""The in-memory TodoWrite task item the model maintains within a session (ADR-0002 §7).

:class:`Task` is one entry in the per-run task list the ``todo_write`` tool rewrites. It mirrors
the frozen + slotted style of :mod:`decode.entities.events` and :mod:`decode.entities.permissions`
(cheap, hashable, safe to pass across the queue/stream boundary), but it **validates** its
``status`` against the three allowed states: the model supplies these values, so a bad one must be
rejected loudly at construction rather than silently stored.

Kept deliberately narrow — ``id`` / ``content`` / ``status``, nothing more. The *list* of tasks is
mutable per run (the tool replaces it in place; see :data:`decode.agent.deps.AgentDeps.task_store`);
each individual :class:`Task` is immutable. Cross-session persistence and claude-code's richer
``TaskRegistry`` / background-job state machine are explicitly out of scope for M1.
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

    ``id`` is a model-supplied identifier (a short string the model picks, e.g. ``"1"``); ``content``
    is the human-readable task description; ``status`` is one of ``pending`` / ``in_progress`` /
    ``completed`` and defaults to ``pending``. Construction validates both ``status`` (rejects any
    value outside the three states) and ``content`` (rejects blank text) so the model cannot store a
    malformed task.
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
