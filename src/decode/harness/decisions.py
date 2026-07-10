"""The mid-turn decision channel: one input surface, a pending-decision mode (ADR-0002 §3-4).

The TUI has exactly one input surface; opening a second concurrent ``prompt_async()`` to collect a
mid-turn answer is illegal (prompt_toolkit asserts a single running ``Application``) and deadlocks
the REPL. A mid-turn requester (permission resolver, ``AskUser``) instead awaits an
:class:`asyncio.Future` via :meth:`DecisionChannel.request`; while it is pending the input loop
routes the next submitted line into :meth:`~DecisionChannel.resolve`. Only one decision is ever
pending at a time (the single-flight lock guarantees it); :meth:`~DecisionChannel.cancel` unblocks
a pending requester on abort/shutdown so it can fall back to its safe default.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class DecisionChannel:
    """A one-at-a-time channel for a mid-turn request the human must answer (ADR-0002 §3-4)."""

    def __init__(self) -> None:
        self._pending: asyncio.Future[str] | None = None

    @property
    def pending(self) -> bool:
        """True when a requester is awaiting a line (the input loop must route to it)."""
        return self._pending is not None and not self._pending.done()

    async def request(self) -> str:
        """Await the next submitted line as the answer to a mid-turn request.

        Returns the raw line the user typed (the caller parses it). Raises :class:`RuntimeError`
        if a decision is already pending and re-raises :class:`asyncio.CancelledError` on
        :meth:`cancel`.
        """
        if self.pending:
            raise RuntimeError("a decision is already pending on this channel")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending = future
        logger.debug("decision channel: awaiting a line")
        try:
            return await future
        finally:
            self._pending = None

    def resolve(self, line: str) -> bool:
        """Fulfil the pending request with ``line``.

        Returns ``True`` when a request was waiting, ``False`` when nothing was pending (so the
        caller knows to handle the line normally).
        """
        future = self._pending
        if future is None or future.done():
            return False
        logger.debug("decision channel: resolving with %r", line)
        future.set_result(line)
        return True

    def cancel(self) -> None:
        """Cancel a pending request so the requester falls back to its safe default (deny).

        A no-op when nothing is pending.
        """
        future = self._pending
        if future is None or future.done():
            return
        logger.debug("decision channel: cancelling the pending request")
        future.cancel()
