"""The real Pydantic AI turn handler: ``agent.iter()`` driven as the harness seam.

ADR-0002 §1-2,4. :class:`AgentTurnHandler` is the :data:`~decode.harness.runner.TurnHandler`
the :class:`~decode.harness.runner.Runner` drives. One *harness turn* fragments into one or
more *legs* (``MODEL_REQUEST`` → run ``agent.iter()`` → ``WOULD_STOP``); the handler:

* **yields ``MODEL_REQUEST`` before each leg's model request** and receives back the steering
  messages the runner drained. It appends them as user messages to ``message_history`` *before*
  the request, so the model sees them on this leg (boundary-inject, never mid-stream — §4);
* **streams each model node** with ``node.stream(...)``, mapping ``PartDeltaEvent`` /
  ``PartStartEvent`` text → :class:`~decode.entities.events.AssistantTextDelta` and thinking →
  :class:`~decode.entities.events.ThinkingDelta`, via the event sink on
  :class:`~decode.agent.deps.AgentDeps`;
* **carries ``message_history`` across turns** — after every leg it replaces its history with
  ``run.all_messages()``, so the next harness turn continues the same conversation;
* **yields ``WOULD_STOP`` at the end of a leg** and, if the runner hands back a follow-up,
  runs one more leg with that follow-up as the next user prompt.

Chat-only has **no tools**, so a leg is a single model request with text output — there is no
real ``DeferredToolRequests`` to resume yet. The steering-before-the-request drain is wired
and tested here; validating a steering user-message appended at a *real deferred resume* is
left to task 005 (see the task log).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.messages import (
    ModelMessage,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)

from decode.agent.deps import AgentDeps
from decode.entities import events
from decode.harness.runner import Boundary, TurnContext

logger = logging.getLogger(__name__)


class AgentTurnHandler:
    """Drive ``agent.iter()`` as the harness turn handler, carrying history across turns.

    One instance per REPL session: it owns the cross-turn ``message_history``. Calling it
    with a :class:`~decode.harness.runner.TurnContext` returns the async generator the runner
    drives (so an instance satisfies the ``TurnHandler`` callable type).
    """

    def __init__(
        self, agent: Agent[AgentDeps, str | DeferredToolRequests], *, deps: AgentDeps
    ) -> None:
        self._agent = agent
        self._deps = deps
        # The running conversation, carried across harness turns (ADR-0002 §1).
        self.message_history: list[ModelMessage] = []

    async def __call__(self, ctx: TurnContext) -> AsyncGenerator[Boundary, list[str]]:
        """Run one harness turn as a sequence of model-request legs (ADR-0002 §4).

        The runner sends back the messages it drained at each boundary: steering at
        ``MODEL_REQUEST`` (appended to history before the request), follow-up at
        ``WOULD_STOP`` (continues the turn as the next leg).
        """
        next_prompt: str | None = ctx.prompt

        while True:
            # --- model-request boundary: drain steering, then make the request (§4) ---
            steering = yield Boundary.MODEL_REQUEST
            prompt = self._compose_prompt(next_prompt, steering)
            next_prompt = None
            if prompt is None:
                # Nothing to ask the model (no prompt, no steering): stop cleanly.
                follow_ups = yield Boundary.WOULD_STOP
                if not follow_ups:
                    return
                next_prompt = "\n".join(follow_ups)
                continue

            await self._run_leg(ctx, prompt)

            # --- would-stop boundary: drain follow-up; a follow-up adds one more leg (§4) ---
            follow_ups = yield Boundary.WOULD_STOP
            if not follow_ups:
                return
            next_prompt = "\n".join(follow_ups)

    @staticmethod
    def _compose_prompt(base: str | None, steering: list[str]) -> str | None:
        """Fold drained steering into the prompt for the upcoming model request.

        Steering arrives at the model-request boundary and must reach the model on *this*
        leg (§4). With no tools there is no in-flight run to interrupt, so we prepend the
        steering to the leg's user prompt; ``None`` means there is nothing to send.
        """
        parts = [*steering]
        if base is not None:
            parts.append(base)
        if not parts:
            return None
        return "\n".join(parts)

    async def _run_leg(self, ctx: TurnContext, prompt: str) -> None:
        """Run one ``agent.iter()`` leg, streaming nodes into events and updating history."""
        async with self._agent.iter(
            prompt, deps=self._deps, message_history=self.message_history
        ) as run:
            async for node in run:
                if Agent.is_model_request_node(node):
                    await self._stream_model_node(ctx, node, run)
        # Carry the whole conversation (prior history + this leg) into the next turn.
        self.message_history = run.all_messages()

    async def _stream_model_node(self, ctx: TurnContext, node: object, run: object) -> None:
        """Stream one model-request node, emitting text/thinking deltas as they arrive."""
        async with node.stream(run.ctx) as request_stream:  # type: ignore[attr-defined]
            async for event in request_stream:
                self._emit_for_stream_event(ctx, event)

    def _emit_for_stream_event(self, ctx: TurnContext, event: object) -> None:
        """Map one Pydantic AI stream event to a canonical decode event (or ignore it).

        Handles the initial part (``PartStartEvent``) and incremental updates
        (``PartDeltaEvent``) for both text and thinking; everything else (tool-call parts,
        final-result markers) is irrelevant to chat-only and skipped.
        """
        if isinstance(event, PartStartEvent):
            part = event.part
            if isinstance(part, TextPart) and part.content:
                ctx.emit(events.AssistantTextDelta(text=part.content))
            elif isinstance(part, ThinkingPart) and part.content:
                ctx.emit(events.ThinkingDelta(text=part.content))
        elif isinstance(event, PartDeltaEvent):
            delta = event.delta
            if isinstance(delta, TextPartDelta) and delta.content_delta:
                ctx.emit(events.AssistantTextDelta(text=delta.content_delta))
            elif isinstance(delta, ThinkingPartDelta) and delta.content_delta:
                ctx.emit(events.ThinkingDelta(text=delta.content_delta))
