"""The two interaction queues that back the Priority Gate (ADR-0002 §4).

decode separates two kinds of user input that arrive *while a turn is running*:

* **steering** — drained by the runner *before each model-request leg*, so the message is
  injected at the next boundary and redirects the turn without interrupting an in-flight
  stream or tool (boundary-inject, never mid-stream).
* **follow-up** — drained *only at the would-stop boundary*, so it continues the
  conversation as the next leg of the same turn rather than steering the current one.

Both are plain ``asyncio.Queue``s of user-text strings. Keeping them as a tiny named pair
(rather than two bare queues threaded through the runner) makes the two drain policies
explicit at the call sites in :mod:`decode.harness.runner`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass(slots=True)
class InteractionQueues:
    """The steering + follow-up queues for one harness session.

    Each holds the raw user text submitted while a turn is in flight. The runner owns the
    *when* of draining (see module docstring); this type only owns the *storage*.
    """

    steering: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    follow_up: asyncio.Queue[str] = field(default_factory=asyncio.Queue)

    def drain_steering(self) -> list[str]:
        """Remove and return every queued steering message, oldest first.

        Non-blocking: returns ``[]`` when empty. Called by the runner before each
        model-request leg so newly-arrived steering lands at the next boundary.
        """
        return _drain(self.steering)

    def drain_follow_up(self) -> list[str]:
        """Remove and return every queued follow-up message, oldest first.

        Non-blocking: returns ``[]`` when empty. Called by the runner only at the
        would-stop boundary so a follow-up continues the turn instead of steering it.
        """
        return _drain(self.follow_up)

    def clear(self) -> None:
        """Discard everything in both queues (used on cooperative abort, ADR-0002 §5)."""
        _drain(self.steering)
        _drain(self.follow_up)


def _drain(queue: asyncio.Queue[str]) -> list[str]:
    """Synchronously pop all currently-queued items, oldest first."""
    items: list[str] = []
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return items
