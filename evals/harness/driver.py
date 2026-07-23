"""The one in-process agent driver every eval track reuses (ADR-0017 §4).

:func:`run_agent_once` drives the REAL ``build_agent()`` + ``AgentDeps`` + ``AgentTurnHandler`` +
``Runner`` — the exact stack ``tests/integration/test_milestone1_capstone.py`` proves — submits one
prompt, and drives it to idle. What comes back is an :class:`EvalRunRecord` read entirely from the
pydantic-ai message history: tool calls from ``ToolCallPart``s, usage summed from each
``ModelResponse.usage``. Grading never parses Opik traces (that would couple it to the
observability pipeline and lie under export lag).

Configurable per probe: the gate mode + optional rules, custom permission / question resolvers
(default = headless auto-deny, mirroring ``runtime/flow.py``), a pre-filled ``message_history`` (the
compaction probe needs it), and ``max_requests`` — a hard cap on model requests so a runaway run
stops gracefully instead of burning budget. :func:`run_agent_once_sync` wraps it in
:func:`asyncio.run` because Opik ``evaluate()`` task fns cannot be async.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.wrapper import WrapperModel

from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import Runner
from decode.permissions.gate import PermissionGate
from decode.permissions.types import PermissionMode
from decode.tools.askuser import deny_user_question_resolver

if TYPE_CHECKING:
    from pydantic_ai.models import Model, ModelRequestParameters
    from pydantic_ai.settings import ModelSettings

    from decode.agent.loop import AgentTurnHandler
    from decode.permissions.rules import RuleSet

logger = logging.getLogger(__name__)

# The text the request cap substitutes for the model's next call once the cap is reached, so the
# agent loop ends on a plain-text output (a graceful stop) instead of running forever.
CAP_STOP_TEXT = "Eval run stopped: reached the max model-request cap."


PermissionResolver = Callable[[PermissionRequest], Awaitable[PermissionDecision]]
UserQuestionResolver = Callable[[str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """One tool call the agent made: the tool name and its arguments (ADR-0017 §4).

    ``args`` is the decoded argument mapping when the ``ToolCallPart`` carries structured args, else
    the raw JSON string the model streamed — extracted from the message history, never a trace.
    """

    name: str
    args: dict[str, Any] | str


@dataclass(frozen=True, slots=True)
class EvalRunRecord:
    """Everything an eval metric needs about one run, read from the message history (ADR-0017 §4).

    * ``output`` — the agent's final assistant text.
    * ``messages`` — the whole pydantic-ai conversation (prior history + this run).
    * ``tool_calls`` — every ``ToolCallPart``, in order, as name + args.
    * ``steps`` — the model-request count (one per ``ModelResponse``).
    * ``input_tokens`` / ``output_tokens`` — summed from each ``ModelResponse.usage`` (the
      message-history equivalent of ``result.usage()``).
    * ``denied_tools`` — the tools the gate denied (``ToolReturnPart.outcome == "denied"``).
    * ``compaction_events`` — how many :class:`~decode.entities.events.ContextCompacted` /
      ``ContextMicrocompacted`` the run emitted, so the compaction-survival probe can prove the
      cascade actually FIRED (not merely that a large history was seeded). ``0`` for every run whose
      probe leaves ``enable_compaction`` off.
    * ``agent_error`` — the message of an :class:`~decode.entities.events.AgentError` the Runner
      surfaced when a turn crashed, else ``None``. The Runner swallows a turn exception into that
      event and returns an empty-but-valid history, so without this a crashed run is
      indistinguishable from a no-output run (the task-103 QA gap); a benchmark grades a crash as
      fail-with-reason instead of silently empty (ADR-0017 §4; task 106).
    """

    output: str
    messages: list[ModelMessage]
    tool_calls: list[ToolCallRecord]
    steps: int
    input_tokens: int
    output_tokens: int
    denied_tools: list[str] = field(default_factory=list)
    compaction_events: int = 0
    agent_error: str | None = None


async def _deny_permission_resolver(request: PermissionRequest) -> PermissionDecision:
    """The headless auto-deny default, mirroring ``runtime/flow.py`` (ADR-0017 §4).

    An eval run has no interactive approver, so an ``ASK`` the probe did not override is denied —
    the safe default. Probes that want an approval supply their own ``resolve_permission``.
    """
    logger.debug("eval driver denying permission for tool=%s", request.tool_name)
    return PermissionDecision.deny(reason="No interactive approver in the eval harness.")


def _cap_stop_stream() -> FunctionModel:
    """A one-shot :class:`FunctionModel` that streams :data:`CAP_STOP_TEXT` and nothing else.

    Substituted for the wrapped model once the request cap is hit, so the agent loop sees a
    plain-text response (no tool call) and stops on the next output — the graceful cap.
    """

    async def stream_function(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield CAP_STOP_TEXT

    return FunctionModel(stream_function=stream_function)


class _RequestCappedModel(WrapperModel):
    """Wrap a model, counting each generation and stopping gracefully past ``max_requests``.

    Each ``request_stream`` increments the counter; the first call beyond the cap delegates to
    :func:`_cap_stop_stream` instead of the wrapped model, so a runaway run ends on a plain-text
    response rather than crashing or burning budget. Non-generation calls (``count_tokens`` etc.)
    pass through the ``WrapperModel`` base unchanged.
    """

    def __init__(self, wrapped: Model, max_requests: int) -> None:
        super().__init__(wrapped)
        self._max_requests = max_requests
        self.request_count = 0
        self._stop_model = _cap_stop_stream()

    @asynccontextmanager
    async def request_stream(  # type: ignore[override]
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncIterator[Any]:
        self.request_count += 1
        target = self._stop_model if self.request_count > self._max_requests else self.wrapped
        async with target.request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as stream:
            yield stream


async def run_agent_once(
    prompt: str,
    *,
    cwd: Path,
    gate_mode: PermissionMode = PermissionMode.BYPASS,
    permission_rules: RuleSet | None = None,
    resolve_permission: PermissionResolver | None = None,
    resolve_user_question: UserQuestionResolver | None = None,
    message_history: list[ModelMessage] | None = None,
    max_requests: int | None = None,
    enable_compaction: bool = False,
) -> EvalRunRecord:
    """Drive one prompt through the real agent stack and return an :class:`EvalRunRecord` (ADR-0017 §4).

    Builds the REAL ``build_agent()``, real :class:`~decode.agent.deps.AgentDeps` (a headless no-op
    event sink, ``harness_home`` defaulting to ``cwd``, the gate in ``gate_mode`` with optional
    ``permission_rules``), a real :class:`~decode.agent.loop.AgentTurnHandler` seeded with
    ``message_history``, and a real :class:`~decode.harness.runner.Runner`; submits ``prompt`` and
    waits for idle. Resolvers default to the headless auto-deny pair; a probe overrides either.
    ``max_requests`` installs the :class:`_RequestCappedModel` seam so a runaway run stops
    gracefully. The record is read entirely from ``handler.message_history``.

    ``enable_compaction`` wires the auto-compaction cascade (ADR-0006): the summarizer source is the
    agent's OWN model, captured before the request-cap override — so it runs on whatever provider the
    agent runs on (real Gemini live, a scripted model offline), never a separate ``Settings``-built
    model that would phone home in an offline test. Off by default, so a probe that does not grade
    compaction never pays for a summarizer call.
    """
    from decode.agent.loop import AgentTurnHandler
    from decode.tui.app import InputIntent

    agent = build_agent()
    gate = PermissionGate(mode=gate_mode, user_rules=permission_rules)
    # Capture a crashed turn: the Runner swallows a turn exception into an ``AgentError`` event and
    # returns a valid-but-empty history, so the record would otherwise hide the failure (ADR-0017 §4).
    errors: list[str] = []
    compaction_events = 0

    def _capture_emit(event: object) -> None:
        nonlocal compaction_events
        if isinstance(event, events.AgentError):
            errors.append(event.message)
        elif isinstance(event, events.ContextCompacted | events.ContextMicrocompacted):
            compaction_events += 1

    deps = AgentDeps(
        cwd=cwd,
        emit=_capture_emit,
        gate=gate,
        resolve_permission=resolve_permission or _deny_permission_resolver,
        resolve_user_question=resolve_user_question or deny_user_question_resolver,
    )
    # The summarizer reuses the agent's base model (before any request-cap wrap) so a scripted
    # offline model doubles as the summarizer and no real network call is made in the unit suite.
    compaction_source = agent.model if enable_compaction else None
    handler: AgentTurnHandler = AgentTurnHandler(
        agent,
        deps=deps,
        message_history=message_history,
        compaction_model=compaction_source,
    )
    runner = Runner(handler, on_event=_capture_emit)

    from decode.services.lsp import service as lsp_service

    cap = (
        agent.override(model=_RequestCappedModel(agent.model, max_requests))
        if max_requests is not None
        else nullcontext()
    )
    try:
        with cap:
            await runner.submit(prompt, InputIntent.STEER)
            await runner.wait_idle()
    finally:
        # Reap any Language Server the run spawned (the ``lsp`` tool), IN this loop — a probe run is
        # sync (``run_agent_once_sync`` → ``asyncio.run``), so a caller's later ``asyncio.run`` teardown
        # would try to close a subprocess transport bound to this now-dead loop (an unclosed-transport
        # ResourceWarning). Each run also gets a fresh temp Workspace, so a cached client is stale
        # anyway. No-op (empty cache) for every probe that never touches ``lsp``. Idempotent, never raises.
        await lsp_service.shutdown_all()

    return _build_record(
        handler.message_history,
        agent_error=errors[0] if errors else None,
        compaction_events=compaction_events,
    )


def run_agent_once_sync(prompt: str, **kwargs: Any) -> EvalRunRecord:
    """Sync wrapper over :func:`run_agent_once` — Opik ``evaluate()`` task fns cannot be async."""
    return asyncio.run(run_agent_once(prompt, **kwargs))


def _build_record(
    messages: list[ModelMessage],
    *,
    agent_error: str | None = None,
    compaction_events: int = 0,
) -> EvalRunRecord:
    """Assemble the :class:`EvalRunRecord` from the pydantic-ai message history (ADR-0017 §4)."""
    tool_calls: list[ToolCallRecord] = []
    steps = 0
    input_tokens = 0
    output_tokens = 0
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        steps += 1
        input_tokens += message.usage.input_tokens or 0
        output_tokens += message.usage.output_tokens or 0
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                tool_calls.append(ToolCallRecord(name=part.tool_name, args=_tool_args(part)))
    denied_tools = [
        part.tool_name
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.outcome == "denied"
    ]
    return EvalRunRecord(
        output=_final_text(messages),
        messages=list(messages),
        tool_calls=tool_calls,
        steps=steps,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        denied_tools=denied_tools,
        compaction_events=compaction_events,
        agent_error=agent_error,
    )


def _tool_args(part: ToolCallPart) -> dict[str, Any] | str:
    """The decoded argument mapping for a ``ToolCallPart``, or its raw JSON string if not a mapping."""
    args = part.args_as_dict()
    return args if isinstance(args, dict) else part.args_as_json_str()


def _final_text(messages: list[ModelMessage]) -> str:
    """The agent's final assistant text: the last ``ModelResponse`` that carries any ``TextPart``."""
    for message in reversed(messages):
        if not isinstance(message, ModelResponse):
            continue
        texts = [
            part.content for part in message.parts if isinstance(part, TextPart) and part.content
        ]
        if texts:
            return "".join(texts)
    return ""
