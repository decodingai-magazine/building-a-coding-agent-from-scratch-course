"""The runner: turn lifecycle, single-flight phase machine, drains, cooperative abort.

This is the heart of the harness (ADR-0002 §4-5). It owns:

* a **phase machine** — ``idle -> dispatching -> running -> idle`` — guarded by a
  **single-flight lock** that spans an entire (possibly multi-leg) turn. The phase is set
  *synchronously, before the first ``await``* in :meth:`Runner.submit`, so a racing second
  submit observes "busy" with no window to start a parallel turn;
* the **two drain points** — steering is drained *before each model-request leg*;
  follow-up is drained *only at the would-stop boundary* (ADR-0002 §4);
* a **cooperative-abort flag** — :meth:`Runner.abort` (``Esc``) makes the turn stop at the
  *next* boundary, keep the history emitted so far, clear the queues, and return to idle
  (ADR-0002 §5). It never interrupts an in-flight stream or tool.

The agent loop is decoupled behind the **turn handler**: an async generator that ``yield``s
a :class:`Boundary` at each model-request / would-stop point and receives back the messages
the runner drained for it. :class:`decode.agent.loop.AgentTurnHandler` (task 004) is the real
Pydantic AI implementation; the runner stays agnostic of it so the seam remains testable with
a controllable stub handler (see ``tests/unit/decode/harness/test_runner.py``).
"""

from __future__ import annotations

import asyncio
import enum
import logging
from collections.abc import AsyncGenerator, Callable
from typing import TYPE_CHECKING

from decode.entities import events
from decode.harness.queue import InteractionQueues

if TYPE_CHECKING:
    from decode.tui.app import InputIntent

logger = logging.getLogger(__name__)

EventSink = Callable[[events.Event], None]


class Phase(enum.Enum):
    """The single-flight phase machine (ADR-0002 §4).

    ``DISPATCHING`` is the synchronous window between accepting a turn and the turn task
    actually starting — it exists so the busy state is observable *before the first await*.
    """

    IDLE = "idle"
    DISPATCHING = "dispatching"
    RUNNING = "running"


class Boundary(enum.Enum):
    """A point in a turn where the runner may inject queued input or stop (ADR-0002 §4).

    The turn handler ``yield``s one of these to hand control back to the runner:

    * ``MODEL_REQUEST`` — about to make a model request; the runner drains **steering** and
      sends the drained messages back into the handler.
    * ``WOULD_STOP`` — the turn would end here; the runner drains **follow-up** and sends it
      back. A non-empty follow-up continues the turn; an empty one ends it.

    The abort flag is checked at *every* boundary, so a turn always stops cooperatively at
    one of these points — never mid-stream or mid-tool.
    """

    MODEL_REQUEST = "model_request"
    WOULD_STOP = "would_stop"


class TurnContext:
    """What a turn handler may do while the runner drives it.

    Currently just :meth:`emit` (push an event to the TUI). It is a class rather than a
    bare callback so task 004 can widen it (history, model handle, gate) without changing
    every handler signature.
    """

    __slots__ = ("_sink", "prompt", "turn_id")

    def __init__(self, turn_id: int, prompt: str, sink: EventSink) -> None:
        self.turn_id = turn_id
        self.prompt = prompt
        self._sink = sink

    def emit(self, event: events.Event) -> None:
        """Stream one event to the TUI."""
        self._sink(event)


# A turn handler is an async generator: it ``yield``s a Boundary and is sent back the list
# of user messages the runner drained at that boundary.
TurnHandler = Callable[[TurnContext], AsyncGenerator[Boundary, list[str]]]


class Runner:
    """Drives turns one at a time, draining the queues and honoring abort at boundaries."""

    def __init__(self, turn_handler: TurnHandler, *, on_event: EventSink) -> None:
        self._turn_handler = turn_handler
        self._on_event = on_event
        self.queues = InteractionQueues()
        self._phase = Phase.IDLE
        self._abort = False
        self._turn_task: asyncio.Task[None] | None = None
        self._next_turn_id = 0

    @property
    def phase(self) -> Phase:
        """The current phase of the single-flight machine."""
        return self._phase

    @property
    def active_turns(self) -> int:
        """How many turns are in flight (0 or 1 — single-flight)."""
        return 1 if self._turn_task is not None and not self._turn_task.done() else 0

    async def submit(self, text: str, intent: InputIntent) -> None:
        """Route one user submission by intent (ADR-0002 §4).

        On idle this *starts* a turn; while busy it enqueues the text on the steering or
        follow-up queue (never a parallel turn). The phase is set **synchronously here,
        before the first ``await``**, so a racing submit cannot slip a second turn in.
        """
        # Import locally to avoid a module-level cycle (app imports the runner).
        from decode.tui.app import InputIntent as _InputIntent

        if self.active_turns:
            if intent is _InputIntent.FOLLOW_UP:
                await self.queues.follow_up.put(text)
            else:
                await self.queues.steering.put(text)
            return

        # --- single-flight critical section: no await before the phase is set ---
        self._phase = Phase.DISPATCHING
        self._abort = False
        turn_id = self._next_turn_id
        self._next_turn_id += 1
        self._turn_task = asyncio.ensure_future(self._run_turn(turn_id, text))
        # ------------------------------------------------------------------------

    def abort(self) -> None:
        """Set the cooperative-abort flag (``Esc``); the turn stops at the next boundary."""
        self._abort = True

    async def wait_idle(self) -> None:
        """Block until the in-flight turn (if any) has finished and the runner is idle."""
        task = self._turn_task
        if task is not None:
            await task

    async def _run_turn(self, turn_id: int, prompt: str) -> None:
        """Drive one whole turn: phase RUNNING, then loop the handler's boundaries."""
        self._phase = Phase.RUNNING
        ctx = TurnContext(turn_id, prompt, self._on_event)
        self._on_event(events.TurnStarted(turn_id=turn_id, prompt=prompt))
        aborted = False

        agen = self._turn_handler(ctx)
        try:
            sent: list[str] = []
            boundary = await agen.asend(None)
            while True:
                if self._abort:
                    aborted = True
                    break
                if boundary is Boundary.MODEL_REQUEST:
                    sent = self.queues.drain_steering()
                elif boundary is Boundary.WOULD_STOP:
                    sent = self.queues.drain_follow_up()
                else:  # pragma: no cover - defensive
                    raise RuntimeError(f"unknown boundary: {boundary!r}")
                try:
                    boundary = await agen.asend(sent)
                except StopAsyncIteration:
                    break
        except Exception as exc:
            logger.exception("turn %d failed", turn_id)
            self._on_event(events.AgentError(message=str(exc)))
        finally:
            await agen.aclose()
            if aborted:
                # ADR-0002 §5: keep completed history, drop pending input, back to idle.
                self.queues.clear()
            self._on_event(events.TurnFinished(turn_id=turn_id, aborted=aborted))
            self._phase = Phase.IDLE
            self._abort = False
            self._turn_task = None
