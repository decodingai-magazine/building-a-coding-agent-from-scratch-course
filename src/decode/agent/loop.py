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
* **streams each call-tools node** (``Agent.is_call_tools_node``), mapping Pydantic AI's
  ``FunctionToolCallEvent`` → :class:`~decode.entities.events.ToolCallStarted` (deduped per
  ``tool_call_id`` so a gated call — which is replayed on the resume leg — only announces once)
  and ``FunctionToolResultEvent`` → :class:`~decode.entities.events.ToolResult`, so the live
  REPL renders a tool panel on completion (ADR-0002 §6). A deferred (gated) call streams only
  the *call* event on the pause leg; its *result* event lands on the approved/denied resume leg;
* **routes gated tool calls through the permission gate (ADR-0003 §3).** When a leg resolves to
  :class:`~pydantic_ai.DeferredToolRequests` (a tool raised ``ApprovalRequired``), it asks the
  gate for the mode x kind verdict and **honors it**: an ``ALLOW`` runs the tool with no prompt;
  a ``DENY`` returns the gate's reason; an ``ASK`` emits a
  :class:`~decode.entities.events.PermissionRequested` event and resolves into the human's
  allow/deny verdict via ``deps.resolve_permission`` (the deferred-pause seam task 011 AskUser
  reuses). It then builds :class:`~pydantic_ai.DeferredToolResults` — ``True`` to approve,
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
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.tools import DeferredToolResults
from pydantic_ai.usage import RunUsage

from decode.agent.deps import AgentDeps
from decode.config.settings import Settings, settings
from decode.context.compaction import (
    build_summary_message,
    microcompact,
    should_compact,
    split_tail,
    summarize_for_compaction,
)
from decode.entities import events
from decode.entities.permissions import PermissionOutcome, PermissionRequest
from decode.harness.runner import Boundary, TurnContext
from decode.permissions.rules import subject_for
from decode.tools import tool_kind

if TYPE_CHECKING:
    from pydantic_ai.models import Model

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

    When a compaction model/settings seam is wired (``compaction_model_or_settings=``), the
    handler runs the window-relative two-tier compaction cascade at each would-stop boundary
    (ADR-0006 §3-7): full compaction (an LLM summary, persisted as a checkpoint) at the higher
    level, microcompaction (no LLM, in-memory only) at the lower one. The seam is optional and
    defaults to ``None`` — a headless / test run leaves the whole cascade off and behaves exactly
    as before. The same :meth:`compact` is the body of ``/compact`` (task 045), and
    :attr:`last_input_tokens` is the clean read the TUI fill gauge uses (task 047).
    """

    def __init__(
        self,
        agent: Agent[AgentDeps, str | DeferredToolRequests],
        *,
        deps: AgentDeps,
        session_log: SessionLog | None = None,
        message_history: list[ModelMessage] | None = None,
        compaction_model_or_settings: Model | Settings | None = None,
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
        # The compaction summarizer source (ADR-0006 §4): a concrete ``Model`` (tests inject
        # ``FunctionModel`` — no network) or the ``Settings`` Gemini is built from. ``None``
        # disables the whole auto-compaction cascade (the headless / test default).
        self._compaction_model_or_settings = compaction_model_or_settings
        # Provider-reported input tokens of the most recent leg — the compaction trigger source
        # (ADR-0006 §3) and the TUI fill gauge read (task 047). ``0`` until the first leg runs.
        self._last_input_tokens = 0
        # tool_call_ids that have already emitted a ToolCallStarted. A gated call is streamed on
        # both the deferred-pause leg and the resume leg, so we announce each call only once.
        self._announced_tool_calls: set[str] = set()

    @property
    def last_input_tokens(self) -> int:
        """The provider-reported input-token count of the most recent leg (``0`` before any leg).

        The clean public read the TUI footer fill gauge (task 047) uses, so it never reaches into
        the private attribute. Populated after each leg from ``run.usage().input_tokens`` (``usage``
        is a method on the run in pydantic-ai 1.x — ADR-0009 — returning a ``RunUsage`` whose
        ``input_tokens`` field is the same provider-authoritative number the compaction trigger
        reads, ADR-0006 §3).
        """
        return self._last_input_tokens

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

            # --- would-stop boundary: persist this turn, run the compaction cascade, then drain
            # follow-up (§4, §9; ADR-0006 §3-7) ---
            self._persist_turn()
            await self._maybe_auto_compact()
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

    async def _maybe_auto_compact(self) -> None:
        """Run the window-relative two-tier compaction cascade at would-stop (ADR-0006 §3-7).

        Fires only when compaction is **wired** (a model/settings seam was injected); with
        ``compaction_model_or_settings is None`` (the headless / test default) the whole cascade is
        skipped, so the handler behaves exactly as before. Reads the window, both per-tier reserves,
        and the enabled flag from the ``settings`` singleton and runs the **cheapest applicable**
        tier: full compaction (the LLM tier) when usage crosses the full level, else microcompaction
        (no LLM) when it crosses the lower micro level, else nothing. Full is checked first because
        its level — ``window*(1-compaction_reserve_fraction)`` (80% full on the defaults) — sits
        *above* micro's ``window*(1-microcompaction_reserve_fraction)`` (60%), since the larger micro
        reserve fires earlier. Microcompaction's saving shows up on the *next* turn's measurement —
        this turn's ``input_tokens`` was already measured on the leg.
        """
        if self._compaction_model_or_settings is None:
            return
        usage = RunUsage(input_tokens=self._last_input_tokens)
        full = should_compact(
            usage,
            window=settings.compaction_context_window_tokens,
            reserve=settings.compaction_reserve_fraction,
            enabled=settings.compaction_enabled,
        )
        micro = should_compact(
            usage,
            window=settings.compaction_context_window_tokens,
            reserve=settings.microcompaction_reserve_fraction,
            enabled=settings.compaction_enabled,
        )
        if full:
            await self.compact()
        elif micro:
            self._microcompact()

    async def compact(self) -> bool:
        """Full compaction: summarize older history, keep a recent verbatim tail (ADR-0006 §4-6).

        The LLM tier — also the body of ``/compact`` (task 045). Makes one cheap summarizer call,
        picks the recent tail with :func:`~decode.context.compaction.split_tail`, and replaces the
        running history with ``[summary_message, *tail]`` (a prior summary, if any, rides as the
        head so successive compactions merge for free). Returns ``False`` — a no-op that leaves the
        history untouched — when there is nothing to compact: a ``split`` of ``0`` (the whole history
        already fits the recent-tail budget — checked FIRST, so a no-op never spends a summarizer call)
        or a ``None`` summary (an empty / trivial history or a failed summarizer call). On success, when
        a session log is wired the new ``[summary, *tail]``
        is written as one ``compaction`` checkpoint (an ``OSError`` is logged and swallowed, like
        :meth:`_persist_turn` — persistence never breaks the turn), the persisted-count cursor is
        reset to the new length so the next turn appends only its own messages, and a
        :class:`~decode.entities.events.ContextCompacted` event is emitted.
        """
        split = split_tail(
            self.message_history, keep_recent_tokens=settings.compaction_keep_recent_tokens
        )
        if split == 0:
            return False
        skeleton = await summarize_for_compaction(
            self.message_history, model_or_settings=self._compaction_model_or_settings
        )
        if skeleton is None:
            return False
        before_tokens = self._last_input_tokens
        summary_message = build_summary_message(skeleton)
        tail = self.message_history[split:]
        if self._session_log is not None:
            try:
                self._session_log.append_compaction(summary_message, tail)
            except OSError:
                logger.warning("failed to persist compaction checkpoint", exc_info=True)
        self.message_history = [summary_message, *tail]
        self._persisted_count = len(self.message_history)
        self._deps.emit(
            events.ContextCompacted(before_tokens=before_tokens, kept_messages=len(tail))
        )
        return True

    def clear(self) -> None:
        """Reset the conversation to empty — the body of ``/clear`` (compaction-to-zero).

        Wipes the cross-turn ``message_history`` (the model starts fresh on the next turn), zeroes
        the persisted-count cursor and the footer-gauge token read, and drops the announced-tool-
        call dedup set. When a session log is wired, one ``clear`` marker line is appended FIRST so
        a later ``--resume`` replays to the post-clear state instead of resurrecting the wiped
        turns (the same discard-and-restart replay path a compaction checkpoint uses, restarting
        from ``[]`` — the file stays append-only). Persistence never breaks the command: an
        ``OSError`` is logged and swallowed, like :meth:`_persist_turn`. The summarize-before-wipe
        memory write-back lives at the call site (``tui/app.py``), which owns exit-path parity.
        """
        if self._session_log is not None:
            try:
                self._session_log.append_clear()
            except OSError:
                logger.warning("failed to persist the clear marker", exc_info=True)
        self.message_history = []
        self._persisted_count = 0
        self._last_input_tokens = 0
        self._announced_tool_calls.clear()

    def _microcompact(self) -> None:
        """Microcompaction: blank old tool-output bodies, **in memory only** (ADR-0006 §3a).

        The no-LLM tier, auto-only (there is no manual trigger). Rebuilds the history with each old
        ``ToolReturnPart`` / ``RetryPromptPart`` body (everything before the recent-tail boundary)
        replaced by a placeholder via :func:`~decode.context.compaction.microcompact`; a no-op
        (nothing elided) leaves the history untouched and emits nothing. It deliberately does **not**
        touch the session log and does **not** move the persisted-count cursor: the elided messages
        sit below the cursor and were already persisted in full fidelity, so the log keeps full
        fidelity and a resume replays the full history and re-microcompacts (ADR-0006 §3a). Emits a
        :class:`~decode.entities.events.ContextMicrocompacted` event.
        """
        new_messages, elided = microcompact(
            self.message_history, keep_recent_tokens=settings.compaction_keep_recent_tokens
        )
        if elided == 0:
            return
        self.message_history = new_messages
        self._deps.emit(
            events.ContextMicrocompacted(elided_count=elided, before_tokens=self._last_input_tokens)
        )

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
                elif Agent.is_call_tools_node(node):
                    await self._stream_tool_node(ctx, node, run)
        # Carry the whole conversation (prior history + this leg) into the next turn.
        self.message_history = run.all_messages()
        # Record this leg's provider-reported input tokens: the compaction trigger (ADR-0006 §3) and
        # the TUI fill gauge (task 047) read it. The last leg of a multi-leg turn carries the largest
        # history, so this is the right would-stop measure. ``usage`` is a *method* on the run in
        # pydantic-ai 1.x (it was a property on ``run.result.usage`` under 2.0 — ADR-0009); the
        # ``RunUsage.input_tokens`` field keeps the exact same meaning and ``int`` type.
        self._last_input_tokens = run.usage().input_tokens
        # Keep the persisted-count cursor valid when pydantic-ai *coalesces* adjacent same-role
        # messages of the prior history while building this leg's request — notably the two adjacent
        # ``ModelRequest``s a full compaction leaves (the synthetic summary head + the tail's
        # user-turn boundary), which merge into one on the next leg and shrink the persisted prefix.
        # Clamp the cursor to the count of messages preceding this leg's *new* ones so the next
        # persist can never slice past — and silently drop — a freshly produced message. A no-op
        # whenever the prefix was not restructured (the common, non-compacted case).
        persisted_floor = len(self.message_history) - len(run.result.new_messages())
        self._persisted_count = min(self._persisted_count, persisted_floor)
        return run.result.output

    async def _resolve_deferred(
        self, ctx: TurnContext, requests: DeferredToolRequests
    ) -> DeferredToolResults:
        """Turn a deferred pause into ``DeferredToolResults`` via the gate + the resolver (§3).

        For each approval-required call: ask the gate for the mode x kind verdict and **honor
        it** (ADR-0003 §3). An allow (auto or human) maps to ``True``; a deny (auto or human)
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
        """Decide one gated call; return ``"allow"`` or a denial message string (ADR-0003 §3).

        Builds the request with the tool's :class:`~decode.permissions.types.ToolKind` and the
        per-kind **subject** (``bash`` → command, file tools → path, ``web_fetch`` → url; ADR-0003
        §4) that allow/deny rules glob against, asks the gate, and **honors the verdict**: an
        ``ALLOW`` runs the tool with no prompt and no event; a ``DENY`` returns the gate's reason
        (the model sees it on the resume leg) with no prompt; an ``ASK`` surfaces a
        ``PermissionRequested`` event and routes to ``deps.resolve_permission`` for the human's
        terminal allow/deny (M1's path).
        """
        args_summary = call.args_as_json_str()
        request = PermissionRequest(
            tool_name=call.tool_name,
            args=args_summary,
            kind=tool_kind(call.tool_name),
            subject=subject_for(call.tool_name, args_summary),
            tool_call_id=call.tool_call_id,
        )
        decision = self._deps.gate.check(request)
        if decision.outcome is PermissionOutcome.ALLOW:
            logger.debug("permission auto-allowed for tool=%s", call.tool_name)
            return "allow"
        if decision.outcome is PermissionOutcome.DENY:
            reason = decision.reason or "The tool call was denied."
            logger.debug("permission auto-denied for tool=%s reason=%r", call.tool_name, reason)
            return reason
        # ASK: surface the request and route it to the human resolver (M1's path).
        return await self._ask_human(ctx, call, request, args_summary)

    async def _ask_human(
        self,
        ctx: TurnContext,
        call: ToolCallPart,
        request: PermissionRequest,
        args_summary: str,
    ) -> str:
        """Surface an ``ASK`` to the human and return ``"allow"`` or the denial reason."""
        ctx.emit(
            events.PermissionRequested(
                tool_call_id=call.tool_call_id, name=call.tool_name, args=args_summary
            )
        )
        decision = await self._deps.resolve_permission(request)
        if decision.outcome is PermissionOutcome.ALLOW:
            logger.debug("permission allowed by human for tool=%s", call.tool_name)
            return "allow"
        reason = decision.reason or "The tool call was denied."
        logger.debug("permission denied by human for tool=%s reason=%r", call.tool_name, reason)
        return reason

    async def _stream_model_node(self, ctx: TurnContext, node: object, run: object) -> None:
        """Stream one model-request node, emitting text/thinking deltas as they arrive."""
        async with node.stream(run.ctx) as request_stream:  # type: ignore[attr-defined]
            async for event in request_stream:
                self._emit_for_stream_event(ctx, event)

    async def _stream_tool_node(self, ctx: TurnContext, node: object, run: object) -> None:
        """Stream one call-tools node, emitting ToolCallStarted / ToolResult (ADR-0002 §6).

        Pydantic AI's call-tools node streams a ``FunctionToolCallEvent`` when a tool call
        begins and a ``FunctionToolResultEvent`` when it returns. We map those to the canonical
        :class:`~decode.entities.events.ToolCallStarted` / :class:`~decode.entities.events.ToolResult`
        so the live REPL renders a tool panel on completion. A gated call is streamed on both
        the deferred-pause leg and the resume leg, so the started event is deduped per
        ``tool_call_id`` (the result only ever lands on the resume leg).
        """
        async with node.stream(run.ctx) as tool_stream:  # type: ignore[attr-defined]
            async for event in tool_stream:
                self._emit_for_tool_event(ctx, event)

    def _emit_for_tool_event(self, ctx: TurnContext, event: object) -> None:
        """Map one Pydantic AI tool-stream event to a canonical decode tool event (or ignore it).

        ``FunctionToolCallEvent`` → :class:`~decode.entities.events.ToolCallStarted` (once per
        ``tool_call_id``); ``FunctionToolResultEvent`` → :class:`~decode.entities.events.ToolResult`.
        ``ok`` is ``False`` whenever the tool did not succeed — i.e. a ``RetryPromptPart`` (the
        tool errored/retried) **or** a ``ToolReturnPart`` whose ``outcome`` is not ``"success"``.
        Crucially, pydantic-ai returns a *gate deny* as a ``ToolReturnPart`` with
        ``outcome == "denied"`` (its content is the denial reason), not a ``RetryPromptPart``; a
        plain return has ``outcome == "success"``. So a bare ``isinstance(result, ToolReturnPart)``
        check would mis-render a denial as a green success panel — we key ``ok`` off ``outcome``
        instead, matching the event contract (``events.py``: "ok is False … or was denied").
        Everything else is irrelevant to the tool panel and skipped.
        """
        if isinstance(event, FunctionToolCallEvent):
            call = event.part
            if call.tool_call_id in self._announced_tool_calls:
                return
            self._announced_tool_calls.add(call.tool_call_id)
            ctx.emit(
                events.ToolCallStarted(
                    tool_call_id=call.tool_call_id,
                    name=call.tool_name,
                    args=call.args_as_json_str(),
                )
            )
        elif isinstance(event, FunctionToolResultEvent):
            result = event.part
            if isinstance(result, ToolReturnPart):
                # outcome ∈ {"success", "failed", "denied"}; only "success" is ok. A gate deny
                # arrives here (not as a RetryPromptPart) with outcome == "denied".
                ok = result.outcome == "success"
                output = result.model_response_str()
            elif isinstance(result, RetryPromptPart):
                ok = False
                output = result.model_response()
            else:  # pragma: no cover - defensive: the union is exhausted above
                ok = False
                output = str(getattr(result, "content", ""))
            ctx.emit(
                events.ToolResult(
                    tool_call_id=result.tool_call_id,
                    name=result.tool_name or "",
                    output=output,
                    ok=ok,
                )
            )

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
