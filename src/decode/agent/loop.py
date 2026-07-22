"""The Pydantic AI turn handler: ``agent.iter()`` driven as the harness seam.

:class:`AgentTurnHandler` is the ``TurnHandler`` the harness ``Runner`` drives. One harness
turn runs as one or more legs: it yields ``MODEL_REQUEST`` before each model request (folding
drained steering into the prompt, or into history on a deferred resume leg), streams model and
call-tools nodes into canonical :mod:`decode.entities.events`, routes gated calls through the
permission gate and resumes with ``DeferredToolResults``, carries ``message_history`` across
turns, and yields ``WOULD_STOP`` at the end (a follow-up runs one more leg). The runner's
single-flight lock spans the whole multi-leg turn. See ADR-0002 §1-4,6 and ADR-0003 §3.
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
    ModelResponse,
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

from decode import observability
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


def _leg_input_tokens(messages: list[ModelMessage]) -> int:
    """Input-token occupancy of a leg: the LAST populated ``ModelResponse.usage`` (ADR-0018 §2).

    Under pydantic-ai 1.95.1 (ADR-0009) ``RunUsage`` is CUMULATIVE across every request in a leg
    (one request per tool round), so summing it overcounts ~Nx for an N-round turn. The true
    context size is the last response's own per-request ``RequestUsage``: walk ``messages``
    BACKWARDS and take the first :class:`ModelResponse` whose ``usage.input_tokens > 0`` — later
    responses may carry default (unpopulated) usage, which must not clobber it. Value is
    ``input_tokens + cache_read_tokens`` (cached prompt tokens still occupy context). No populated
    response → ``0``, which ``should_compact`` treats as "don't fire" (ADR-0006 §3 safe fallback).
    """
    for message in reversed(messages):
        if isinstance(message, ModelResponse) and message.usage.input_tokens > 0:
            return message.usage.input_tokens + message.usage.cache_read_tokens
    return 0


class AgentTurnHandler:
    """Drive ``agent.iter()`` as the harness turn handler, carrying history across turns.

    One instance per REPL session: it owns the cross-turn ``message_history``; calling it with
    a :class:`~decode.harness.runner.TurnContext` returns the async generator the runner drives.
    Three optional seams, each ``None``-off so a headless/test run is unchanged: ``session_log=``
    appends each turn's new messages as JSONL for ``--resume`` (ADR-0002 §9); ``compaction_model_
    or_settings=`` enables the two-tier compaction cascade at would-stop (ADR-0006 §3-7; :meth:`compact`
    is also the body of ``/compact``); ``session_id=`` makes each turn one Opik ``chat_turn`` root
    span grouped into a per-session Thread (ADR-0014 §4).
    """

    def __init__(
        self,
        agent: Agent[AgentDeps, str | DeferredToolRequests],
        *,
        deps: AgentDeps,
        session_log: SessionLog | None = None,
        session_id: str | None = None,
        message_history: list[ModelMessage] | None = None,
        compaction_model_or_settings: Model | Settings | None = None,
    ) -> None:
        self._agent = agent
        self._deps = deps
        # Opik Thread id for this session's turns; ``None`` makes the root span a nullcontext.
        self._session_id = session_id
        # The running conversation, carried across turns; ``--resume`` seeds the replayed history.
        self.message_history: list[ModelMessage] = list(message_history or [])
        # Append-only JSONL session log (ADR-0002 §9); ``None`` disables persistence.
        self._session_log = session_log
        # Persisted-count cursor: the seeded prefix counts as persisted so resume never re-writes it.
        self._persisted_count = len(self.message_history)
        # Compaction summarizer source (Model or Settings, ADR-0006 §4); ``None`` disables the cascade.
        self._compaction_model_or_settings = compaction_model_or_settings
        # Provider-reported input tokens of the most recent leg (compaction trigger + TUI gauge).
        self._last_input_tokens = 0
        # tool_call_ids that already emitted ToolCallStarted: a gated call streams on both the
        # pause and resume legs, so announce each call only once.
        self._announced_tool_calls: set[str] = set()

    @property
    def last_input_tokens(self) -> int:
        """The last response's provider-reported request usage for the most recent leg (ADR-0018 §2).

        Not the leg's cumulative usage: it is the last populated ``ModelResponse.usage``
        (``input_tokens + cache_read_tokens``), so it tracks real context occupancy instead of
        overcounting ~Nx across N tool rounds. ``0`` before any leg (and when no response reported
        usage). The public read the TUI footer fill gauge uses; the compaction trigger reads the
        same number.
        """
        return self._last_input_tokens

    async def __call__(self, ctx: TurnContext) -> AsyncGenerator[Boundary, list[str]]:
        """Run one harness turn as a sequence of legs (ADR-0002 §3-4).

        Each model request is preceded by a ``MODEL_REQUEST`` boundary where the runner drains
        steering; a deferred-tool leg routes through the gate and resumes; a follow-up drained at
        ``WOULD_STOP`` runs one more prompt leg. The whole loop runs inside ONE ``chat_turn`` root
        span (ADR-0014 §4) so resume legs and follow-ups stay in the same trace; a ``nullcontext``
        when tracing is off. Abort-safe: the runner's ``agen.aclose()`` throws ``GeneratorExit``
        into the suspended ``yield``, unwinding the ``with`` and closing the span exactly once.
        """
        next_prompt: str | None = ctx.prompt
        pending_results: DeferredToolResults | None = None

        with observability.root_span(
            "chat_turn", thread_id=self._session_id, input=ctx.prompt
        ) as span:
            try:
                while True:
                    # --- model-request boundary: drain steering, then make the request (§4) ---
                    steering = yield Boundary.MODEL_REQUEST

                    if pending_results is not None:
                        # Deferred resume leg: no new prompt, so steering rides the history instead.
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
                        self._heal_dangling_tool_calls()
                        output = await self._run_leg(ctx, prompt=prompt)

                    if isinstance(output, DeferredToolRequests):
                        # A gated tool paused the run: resolve approvals and loop back to resume.
                        pending_results = await self._resolve_deferred(ctx, output)
                        continue

                    # --- would-stop boundary: persist, compaction cascade, drain follow-up ---
                    self._persist_turn()
                    await self._maybe_auto_compact()
                    # Final assistant text = the root span's output; a follow-up leg overwrites it.
                    observability.record_output(span, output)
                    follow_ups = yield Boundary.WOULD_STOP
                    if not follow_ups:
                        return
                    next_prompt = "\n".join(follow_ups)
            finally:
                # Crash/abort-safe (sync — a GeneratorExit context forbids awaits): a leg that
                # raised out of the loop still lands its captured messages in the session log;
                # a no-op after a normal would-stop already persisted them.
                self._persist_turn()

    @staticmethod
    def _compose_prompt(base: str | None, steering: list[str]) -> str | None:
        """Prepend drained steering to the leg's user prompt; ``None`` means nothing to send."""
        parts = [*steering]
        if base is not None:
            parts.append(base)
        if not parts:
            return None
        return "\n".join(parts)

    def _heal_dangling_tool_calls(self) -> None:
        """Heal a history ending in unprocessed tool calls before a new-prompt leg.

        A crashed leg (a tool's ``ModelRetry`` budget exhausted mid-resume) or an Esc-abort while
        a permission prompt is pending leaves the history's last message a ``ModelResponse`` whose
        tool calls never got results — pydantic-ai then rejects EVERY later prompt ("unprocessed
        tool calls"), bricking the session. Synthesize one interrupted-tool return per dangling
        call so the next leg is accepted; a deferred *resume* leg never comes through here (it
        needs the dangling call for its ``DeferredToolResults``). Covers a ``--resume`` of a
        crash-persisted log too — the seeded history heals on the first prompt.
        """
        if not self.message_history:
            return
        last = self.message_history[-1]
        if not isinstance(last, ModelResponse) or not last.tool_calls:
            return
        logger.warning(
            "healing %d unprocessed tool call(s) left by a crashed or aborted turn",
            len(last.tool_calls),
        )
        returns = [
            ToolReturnPart(
                tool_name=call.tool_name,
                tool_call_id=call.tool_call_id,
                content=(
                    "Tool call interrupted before completing (the turn crashed or was aborted); "
                    "re-issue it if still needed."
                ),
            )
            for call in last.tool_calls
        ]
        self.message_history.append(ModelRequest(parts=returns))

    def _append_steering(self, steering: list[str]) -> None:
        """Append steering to the history as a user message before a deferred resume.

        A resume leg carries no new ``user_prompt``, so steering cannot ride the prompt (ADR-0002 §4).
        """
        if not steering:
            return
        content = "\n".join(steering)
        logger.debug("appending steering to history before deferred resume: %r", content)
        self.message_history.append(ModelRequest(parts=[UserPromptPart(content=content)]))

    def _persist_turn(self) -> None:
        """Append the messages beyond the persisted-count cursor to the session log (ADR-0002 §9).

        No-op when no log is wired or nothing is new; a write failure is logged and swallowed —
        persistence must never break the turn.
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
        """Run the two-tier compaction cascade at would-stop (ADR-0006 §3-7); no-op when unwired.

        Full (LLM) is checked first because its trigger level sits above micro's — the larger micro
        reserve fires earlier; the cheapest applicable tier runs.
        """
        if self._compaction_model_or_settings is None:
            return
        usage = RunUsage(input_tokens=self._last_input_tokens)
        # The window of the model THIS run is actually using (``--model`` included, task 123);
        # ``None`` on the deps means no entrypoint resolved one, so use the configured default.
        window = self._deps.context_window_tokens or settings.compaction_context_window_tokens
        full = should_compact(
            usage,
            window=window,
            reserve=settings.compaction_reserve_fraction,
            enabled=settings.compaction_enabled,
        )
        micro = should_compact(
            usage,
            window=window,
            reserve=settings.microcompaction_reserve_fraction,
            enabled=settings.compaction_enabled,
        )
        if full:
            await self.compact()
        elif micro:
            self._microcompact()

    async def compact(self) -> bool:
        """Full compaction: replace history with ``[summary, *tail]`` (ADR-0006 §4-6).

        The LLM tier — also the body of ``/compact``. Returns ``False`` (history untouched) when
        there is nothing to compact: ``split == 0`` (checked FIRST, so a no-op never spends a
        summarizer call) or a ``None`` summary. On success a ``compaction`` checkpoint is written
        (``OSError`` logged and swallowed), the persisted-count cursor reset, and a
        ``ContextCompacted`` event emitted.
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
        """Reset the conversation to empty — the body of ``/clear``.

        Appends a ``clear`` marker FIRST (when a log is wired) so ``--resume`` replays to the
        post-clear state; ``OSError`` is logged and swallowed. The summarize-before-wipe memory
        write-back lives at the call site (``tui/app.py``).
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
        """Microcompaction: blank old tool-output bodies, in memory only (ADR-0006 §3a).

        The no-LLM auto-only tier. Deliberately touches neither the session log nor the cursor —
        the log keeps full fidelity and a resume re-microcompacts. Emits ``ContextMicrocompacted``.
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
        """Run one ``agent.iter()`` leg (prompt or deferred resume); return its output, update history."""
        async with self._agent.iter(
            prompt,
            deps=self._deps,
            message_history=self.message_history,
            deferred_tool_results=deferred_results,
        ) as run:
            try:
                async for node in run:
                    if Agent.is_model_request_node(node):
                        await self._stream_model_node(ctx, node, run)
                    elif Agent.is_call_tools_node(node):
                        await self._stream_tool_node(ctx, node, run)
            finally:
                # Carry the whole conversation (prior history + this leg) into the next turn —
                # in a ``finally`` so a leg that raises (a tool's ModelRetry budget exhausted)
                # keeps its accumulated messages instead of freezing history at the prior leg.
                self.message_history = run.all_messages()
                # This leg's context occupancy: the LAST response's provider-reported request
                # usage, NOT the cumulative RunUsage summed over every tool round (ADR-0018 §2 —
                # ``run.usage()`` accumulates ~Nx for N rounds). The compaction trigger + TUI
                # gauge both read this single number.
                self._last_input_tokens = _leg_input_tokens(self.message_history)
        # pydantic-ai may coalesce adjacent same-role prior messages (notably the two ModelRequests
        # a full compaction leaves), shrinking the persisted prefix. Clamp the cursor to the count
        # preceding this leg's new messages so the next persist never drops a fresh message.
        persisted_floor = len(self.message_history) - len(run.result.new_messages())
        self._persisted_count = min(self._persisted_count, persisted_floor)
        return run.result.output

    async def _resolve_deferred(
        self, ctx: TurnContext, requests: DeferredToolRequests
    ) -> DeferredToolResults:
        """Turn a deferred pause into ``DeferredToolResults`` via the gate + the resolver (ADR-0003 §3).

        Allow maps to ``True``; deny to ``ToolDenied`` so the model sees the reason on the resume leg.
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
        """Decide one gated call; return ``"allow"`` or a denial message string (ADR-0003 §3-4).

        Builds the request with the tool's kind + per-kind subject the rules glob against, then
        honors the gate's verdict: ALLOW → no prompt, DENY → the gate's reason, ASK → the human.
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
        """Stream one call-tools node, emitting ToolCallStarted / ToolResult (ADR-0002 §6)."""
        async with node.stream(run.ctx) as tool_stream:  # type: ignore[attr-defined]
            async for event in tool_stream:
                self._emit_for_tool_event(ctx, event)

    def _emit_for_tool_event(self, ctx: TurnContext, event: object) -> None:
        """Map one Pydantic AI tool-stream event to a canonical decode tool event (or ignore it).

        ``ok`` is keyed off ``ToolReturnPart.outcome`` (not a bare isinstance check): a gate deny
        arrives as a ``ToolReturnPart`` with ``outcome == "denied"``, which would otherwise render
        as a green success panel.
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
                # outcome ∈ {"success", "failed", "denied"}; only "success" is ok.
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
        """Map one Pydantic AI stream event (text/thinking start + delta) to a decode event."""
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
