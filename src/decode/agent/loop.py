"""The real Pydantic AI turn handler: ``agent.iter()`` driven as the harness seam.

ADR-0002 §1-4. :class:`AgentTurnHandler` is the :data:`~decode.harness.runner.TurnHandler`
the :class:`~decode.harness.runner.Runner` drives. One *harness turn* fragments into one or
more *legs*; the handler:

* **yields ``MODEL_REQUEST`` before each leg's model request** and receives back the steering
  messages the runner drained. On a prompt leg it folds them into the user prompt; on a
  **deferred resume leg** (no prompt) it appends them to ``message_history`` as a user message
  *before* the resume, so the model sees the steering on that leg (boundary-inject, never
  mid-stream — §4). This is what closes task 004's carryover: steering at a *real* deferred
  resume reaches the model;
* **streams each model node** with ``node.stream(...)``, mapping text → ``AssistantTextDelta``
  and thinking → ``ThinkingDelta`` via the event sink on
  :class:`~decode.agent.deps.AgentDeps`;
* **routes gated tool calls through the permission gate (§3).** When a leg resolves to
  :class:`~pydantic_ai.DeferredToolRequests` (a tool raised ``ApprovalRequired``), it asks the
  gate for a policy decision (v1 → always *ask*), emits a
  :class:`~decode.entities.events.PermissionRequested` event, resolves the *ask* into the
  human's allow/deny verdict via ``deps.resolve_permission`` (the deferred-pause seam task 011
  AskUser reuses), then builds :class:`~pydantic_ai.DeferredToolResults` — ``True`` to approve,
  :class:`~pydantic_ai.ToolDenied` to feed a denial message back to the model — and **resumes**
  the run with ``deferred_tool_results=`` + ``message_history=``. It loops until the output is
  a plain ``str``;
* **carries ``message_history`` across turns** — after every leg it replaces its history with
  ``run.all_messages()``;
* **yields ``WOULD_STOP`` at the end of a turn** and, if the runner hands back a follow-up,
  runs one more leg with that follow-up as the next user prompt.

The runner's single-flight lock spans the whole multi-leg turn (the handler never returns
control to the runner except at the ``MODEL_REQUEST`` / ``WOULD_STOP`` boundaries), so a
deferred pause + resume is still one atomic turn.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from pydantic_ai import Agent, DeferredToolRequests, ToolDenied
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.tools import DeferredToolResults

from decode.agent.deps import AgentDeps
from decode.entities import events
from decode.entities.permissions import PermissionOutcome, PermissionRequest
from decode.harness.runner import Boundary, TurnContext
from decode.tools import is_read_only

if TYPE_CHECKING:
    from decode.context.session_log import SessionLog

logger = logging.getLogger(__name__)


class AgentTurnHandler:
    """Drive ``agent.iter()`` as the harness turn handler, carrying history across turns.

    One instance per REPL session: it owns the cross-turn ``message_history``. Calling it
    with a :class:`~decode.harness.runner.TurnContext` returns the async generator the runner
    drives (so an instance satisfies the ``TurnHandler`` callable type).

    When a :class:`~decode.context.session_log.SessionLog` is wired (``session_log=``), the
    handler appends each turn's **new** messages to the JSONL log at the would-stop boundary
    (ADR-0002 §9), so ``decode --resume`` can replay the session. The log is optional: a
    headless / test run with no log left wired runs unchanged. A resumed session seeds
    ``message_history`` (and the persisted-count cursor) so resume continues the conversation
    without re-persisting the replayed prefix.
    """

    def __init__(
        self,
        agent: Agent[AgentDeps, str | DeferredToolRequests],
        *,
        deps: AgentDeps,
        session_log: SessionLog | None = None,
        message_history: list[ModelMessage] | None = None,
    ) -> None:
        self._agent = agent
        self._deps = deps
        # The running conversation, carried across harness turns (ADR-0002 §1). A resumed
        # session seeds it with the replayed history (``--resume``, task 014).
        self.message_history: list[ModelMessage] = list(message_history or [])
        # The append-only JSONL session log (ADR-0002 §9); ``None`` disables persistence.
        self._session_log = session_log
        # How many messages have already been persisted: the seeded (replayed) prefix counts as
        # persisted so resume never re-writes it, and each turn appends only the messages beyond
        # this cursor.
        self._persisted_count = len(self.message_history)

    async def __call__(self, ctx: TurnContext) -> AsyncGenerator[Boundary, list[str]]:
        """Run one harness turn as a sequence of legs (ADR-0002 §3-4).

        Each model request is preceded by a ``MODEL_REQUEST`` boundary where the runner
        drains steering. A leg either resolves to plain text (the turn would stop) or to a
        deferred-tool request (route through the gate, then resume with the results). A
        follow-up drained at ``WOULD_STOP`` continues the turn as one more prompt leg.
        """
        next_prompt: str | None = ctx.prompt
        pending_results: DeferredToolResults | None = None

        while True:
            # --- model-request boundary: drain steering, then make the request (§4) ---
            steering = yield Boundary.MODEL_REQUEST

            if pending_results is not None:
                # Deferred resume leg: there is no new prompt, so append any steering to the
                # history as a user message *before* the resume (closes task 004's carryover).
                self._append_steering(steering)
                output = await self._run_leg(ctx, deferred_results=pending_results)
                pending_results = None
            else:
                prompt = self._compose_prompt(next_prompt, steering)
                next_prompt = None
                if prompt is None:
                    # Nothing to ask the model: stop cleanly (drain follow-up below).
                    output = ""
                    self._persist_turn()
                    follow_ups = yield Boundary.WOULD_STOP
                    if not follow_ups:
                        return
                    next_prompt = "\n".join(follow_ups)
                    continue
                output = await self._run_leg(ctx, prompt=prompt)

            if isinstance(output, DeferredToolRequests):
                # A gated tool paused the run: resolve approvals and loop back to resume.
                pending_results = await self._resolve_deferred(ctx, output)
                continue

            # --- would-stop boundary: persist this turn, then drain follow-up (§4, §9) ---
            self._persist_turn()
            follow_ups = yield Boundary.WOULD_STOP
            if not follow_ups:
                return
            next_prompt = "\n".join(follow_ups)

    @staticmethod
    def _compose_prompt(base: str | None, steering: list[str]) -> str | None:
        """Fold drained steering into the prompt for the upcoming model request.

        Steering arrives at the model-request boundary and must reach the model on *this*
        leg (§4). On a prompt leg there is no in-flight run to interrupt, so we prepend the
        steering to the leg's user prompt; ``None`` means there is nothing to send.
        """
        parts = [*steering]
        if base is not None:
            parts.append(base)
        if not parts:
            return None
        return "\n".join(parts)

    def _append_steering(self, steering: list[str]) -> None:
        """Append drained steering to the history as a user message before a deferred resume.

        A deferred resume leg carries ``deferred_tool_results`` and no new ``user_prompt``, so
        steering cannot ride the prompt — it is appended directly to ``message_history`` as a
        :class:`~pydantic_ai.messages.UserPromptPart`, which the model then sees on the resume
        leg (ADR-0002 §4; closes the task-004 carryover).
        """
        if not steering:
            return
        content = "\n".join(steering)
        logger.debug("appending steering to history before deferred resume: %r", content)
        self.message_history.append(ModelRequest(parts=[UserPromptPart(content=content)]))

    def _persist_turn(self) -> None:
        """Append the messages added since the last persist to the session log (ADR-0002 §9).

        Called at each would-stop boundary: the slice of ``message_history`` beyond the
        persisted-count cursor is *this* turn's new messages, which are appended as one typed
        JSONL line and the cursor advanced. A no-op when no session log is wired or the turn
        produced nothing new (an empty slice writes nothing — the log stays append-only and
        clean). Persistence must never break the turn, so a write failure is logged and
        swallowed rather than propagated.
        """
        if self._session_log is None:
            return
        new_messages = self.message_history[self._persisted_count :]
        if not new_messages:
            return
        try:
            self._session_log.append_turn(new_messages)
        except OSError:
            logger.warning("failed to persist turn to session log", exc_info=True)
            return
        self._persisted_count = len(self.message_history)

    async def _run_leg(
        self,
        ctx: TurnContext,
        *,
        prompt: str | None = None,
        deferred_results: DeferredToolResults | None = None,
    ) -> str | DeferredToolRequests:
        """Run one ``agent.iter()`` leg; return its output and update history.

        A *prompt* leg passes ``user_prompt``; a *deferred resume* leg passes
        ``deferred_tool_results`` and no prompt. Either way the model nodes are streamed into
        events and the whole conversation is carried into ``message_history``.
        """
        async with self._agent.iter(
            prompt,
            deps=self._deps,
            message_history=self.message_history,
            deferred_tool_results=deferred_results,
        ) as run:
            async for node in run:
                if Agent.is_model_request_node(node):
                    await self._stream_model_node(ctx, node, run)
        # Carry the whole conversation (prior history + this leg) into the next turn.
        self.message_history = run.all_messages()
        return run.result.output

    async def _resolve_deferred(
        self, ctx: TurnContext, requests: DeferredToolRequests
    ) -> DeferredToolResults:
        """Turn a deferred pause into ``DeferredToolResults`` via the gate + the resolver (§3).

        For each approval-required call: ask the gate for a policy decision (v1 → always
        *ask*), emit a :class:`~decode.entities.events.PermissionRequested` event, then resolve
        the human's verdict via ``deps.resolve_permission``. An allow maps to ``True``; a deny
        maps to :class:`~pydantic_ai.ToolDenied` so the denial message is returned to the model
        on the resume leg. External-execution calls are not used in v1 (no such tools yet).
        """
        approvals: dict[str, bool | ToolDenied] = {}
        for call in requests.approvals:
            decision = await self._decide(ctx, call)
            if decision == "allow":
                approvals[call.tool_call_id] = True
            else:
                approvals[call.tool_call_id] = ToolDenied(decision)
        return requests.build_results(approvals=approvals)

    async def _decide(self, ctx: TurnContext, call: ToolCallPart) -> str:
        """Decide one gated call; return ``"allow"`` or a denial message string.

        Asks the gate for the policy verdict (v1 → ``ASK``), surfaces the request to the user
        via a ``PermissionRequested`` event, and routes the *ask* to ``deps.resolve_permission``
        for the human's terminal allow/deny.
        """
        args_summary = call.args_as_json_str()
        request = PermissionRequest(
            tool_name=call.tool_name,
            args=args_summary,
            read_only=is_read_only(call.tool_name),
            tool_call_id=call.tool_call_id,
        )
        # The gate is the policy object (v1 always asks); the human resolves the ask.
        self._deps.gate.check(request)
        ctx.emit(
            events.PermissionRequested(
                tool_call_id=call.tool_call_id, name=call.tool_name, args=args_summary
            )
        )
        decision = await self._deps.resolve_permission(request)
        if decision.outcome is PermissionOutcome.ALLOW:
            logger.debug("permission allowed for tool=%s", call.tool_name)
            return "allow"
        reason = decision.reason or "The tool call was denied."
        logger.debug("permission denied for tool=%s reason=%r", call.tool_name, reason)
        return reason

    async def _stream_model_node(self, ctx: TurnContext, node: object, run: object) -> None:
        """Stream one model-request node, emitting text/thinking deltas as they arrive."""
        async with node.stream(run.ctx) as request_stream:  # type: ignore[attr-defined]
            async for event in request_stream:
                self._emit_for_stream_event(ctx, event)

    def _emit_for_stream_event(self, ctx: TurnContext, event: object) -> None:
        """Map one Pydantic AI stream event to a canonical decode event (or ignore it).

        Handles the initial part (``PartStartEvent``) and incremental updates
        (``PartDeltaEvent``) for both text and thinking; everything else (tool-call parts,
        final-result markers) is irrelevant to the streamed text/thinking and skipped.
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
