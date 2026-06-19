"""The mid-turn decision channel: one input surface, a pending-decision mode (ADR-0002 §3-4).

A turn sometimes has to stop and ask the human a question *before it can continue* — the
permission gate (task 005) is the first instance; ``AskUser`` (task 011) is the next. The
TUI has exactly **one** input surface: the main ``prompt_async()`` loop, which must stay in
flight for the whole turn so the user can steer (ADR-0002 §4). Opening a *second* concurrent
``prompt_async()`` to collect the answer is illegal — prompt_toolkit guards a single
``Application`` with ``assert not self._is_running`` — and deadlocks the REPL.

:class:`DecisionChannel` is the seam that avoids that. A mid-turn requester (the permission
resolver, later ``AskUser``) calls :meth:`request` and **awaits an :class:`asyncio.Future`**
for the answer instead of opening its own prompt. While that future is pending the channel
is *awaiting a decision*; the main input loop checks :attr:`pending` and routes the next
submitted line into :meth:`resolve` (fulfilling the future) rather than treating it as
steering or a new turn. When nothing is pending the loop behaves exactly as before.

Only one decision is ever pending at a time — the single-flight lock means one turn runs at
a time, and the loop blocks on the resolver's awaited future before the turn can advance, so
there is no second concurrent requester to race. :meth:`cancel` unblocks a pending requester
(it raises :class:`asyncio.CancelledError` out of :meth:`request`) when the turn is aborted
or the REPL is shutting down, so the resolver can fall back to its safe default.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class DecisionChannel:
    """A one-at-a-time channel for a mid-turn request the human must answer (ADR-0002 §3-4).

    The requester (permission resolver / ``AskUser``) ``await``s :meth:`request`; the TUI's
    single input loop fulfils it via :meth:`resolve` with the next submitted line. This is the
    general "mid-turn human-in-the-loop" mechanism — permission approval is its first user.
    """

    def __init__(self) -> None:
        self._pending: asyncio.Future[str] | None = None

    @property
    def pending(self) -> bool:
        """True when a requester is awaiting a line (the input loop must route to it)."""
        return self._pending is not None and not self._pending.done()

    async def request(self) -> str:
        """Await the next submitted line as the answer to a mid-turn request.

        Creates the pending future, marks the channel *awaiting a decision*, and blocks until
        the input loop calls :meth:`resolve` (or :meth:`cancel`). Returns the raw line the
        user typed; the caller parses it (e.g. :func:`decode.tui.app.parse_permission_answer`).
        Raises :class:`RuntimeError` if a decision is already pending (single-flight invariant)
        and re-raises :class:`asyncio.CancelledError` if the request is cancelled.
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
        """Fulfil the pending request with ``line`` (called by the input loop).

        Returns ``True`` when a request was waiting and got the line, ``False`` when nothing
        was pending (so the caller knows to handle the line normally).
        """
        future = self._pending
        if future is None or future.done():
            return False
        logger.debug("decision channel: resolving with %r", line)
        future.set_result(line)
        return True

    def cancel(self) -> None:
        """Cancel a pending request (turn aborted / REPL shutting down).

        The awaiting :meth:`request` re-raises :class:`asyncio.CancelledError`, letting the
        requester fall back to its safe default (deny). A no-op when nothing is pending.
        """
        future = self._pending
        if future is None or future.done():
            return
        logger.debug("decision channel: cancelling the pending request")
        future.cancel()
