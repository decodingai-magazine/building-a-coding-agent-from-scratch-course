"""The dependency object Pydantic AI injects into the agent run (ADR-0002 §1-2).

Pydantic AI passes whatever you hand to ``agent.iter(deps=...)`` into every tool call and
instruction function as ``ctx.deps``. :class:`AgentDeps` is that object for ``decode``.

Chat-only (task 004) needs only two things:

* ``cwd`` — the working directory the agent operates in (tools resolve paths against it);
* ``emit`` — a sink the loop calls to stream :mod:`decode.entities.events` to the TUI.

Later tasks widen this dataclass (the permission gate in 005, the task store in 009, the
session log in 014) — those fields are deliberately *not* added yet so each lands with the
task that uses it. Keeping ``emit`` a plain callable field (not a method) means tools and
the loop share one event channel without importing the harness.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from decode.entities import events

EventSink = Callable[[events.Event], None]


@dataclass(slots=True)
class AgentDeps:
    """What the agent run carries: the working directory and the event sink.

    Not frozen: the sink may be rebound (e.g. per turn) and later tasks add mutable
    collaborators (gate, task store) to the same object.
    """

    cwd: Path
    emit: EventSink
