"""The two interaction queues that back the Priority Gate (ADR-0002 §4).

**steering** is drained before each model-request leg (boundary-inject, never mid-stream);
**follow-up** only at the would-stop boundary, continuing the conversation as the next leg of the
same turn. Both are plain ``asyncio.Queue``s of user-text strings.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass(slots=True)
class InteractionQueues:
    """The steering + follow-up queues for one harness session; the runner owns the drain timing."""

    steering: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    follow_up: asyncio.Queue[str] = field(default_factory=asyncio.Queue)

    def drain_steering(self) -> list[str]:
        """Non-blocking: remove and return every queued steering message, oldest first."""
        return _drain(self.steering)

    def drain_follow_up(self) -> list[str]:
        """Non-blocking: remove and return every queued follow-up message, oldest first."""
        return _drain(self.follow_up)

    def clear(self) -> None:
        """Discard everything in both queues (used on cooperative abort, ADR-0002 §5)."""
        _drain(self.steering)
        _drain(self.follow_up)


def _drain(queue: asyncio.Queue[str]) -> list[str]:
    items: list[str] = []
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return items
