"""Context-compaction capstone (ADR-0006): setup → micro → full → wrap-up through the real stack.

Proves both window-relative compaction tiers end to end: real build_agent + Runner +
AgentTurnHandler (two-tier auto-compaction wired), real render_event on every event, real
SessionLog persist + ``--resume`` replay of a compacted log. Swapped/faked: a scripted
streaming FunctionModel plays the model, a second FunctionModel returns the fixed summary
skeleton, GEMINI_API_KEY is faked so build_agent constructs, and the session log dir is
redirected under tmp_path. Fully offline — no network, no API key, no skipif.

Tier arithmetic: the token source is the LAST populated ``ModelResponse.usage`` of the leg
(ADR-0018 §2), NOT the cumulative RunUsage summed over tool rounds. A plain streaming FunctionModel
pegs every response at a fixed 50 input tokens, so the scripted model (:class:`_ScriptedModel`)
forces each turn's per-request input usage to its tier target: SETUP 50, MICRO 100, FULL 150,
wrap-up 50 — the LAST response of each turn genuinely crosses its line. Window patched to 150 with
default reserves (0.40/0.20) → micro line 90, full line 120, so the turns cross the tiers in order;
a huge prompt forces each kept tail to be exactly the final turn.
"""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import SecretStr
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.usage import RequestUsage
from rich.console import Console

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from pydantic_ai.models import ModelRequestParameters, StreamedResponse
    from pydantic_ai.settings import ModelSettings

import decode.agent.loop as loop
from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler, _leg_input_tokens
from decode.context import session_log
from decode.context.compaction import (
    _MICRO_PLACEHOLDER,
    CompactOutcome,
    estimate_history_tokens,
    split_tail,
)
from decode.context.session_log import SessionLog
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import Runner
from decode.permissions.gate import PermissionGate
from decode.tui import render

# --- window / tier arithmetic (defaults: micro reserve 0.40, full reserve 0.20) -------------
_WINDOW = 150  # micro line = int(150*0.6) = 90; full line = int(150*0.8) = 120
_KEEP_RECENT = 10  # tiny so the kept tail is just the final turn (the rest is "old")
_USAGE_PER_LEG = 50  # a no-tier turn: _ScriptedModel forces the last response's input to 50
_USAGE_SETUP = 50  # SETUP turn's last response → below the micro line (no tier)
_USAGE_MICRO = 100  # MICRO turn's last response → in [90, 120): microcompaction only
_USAGE_FULL = 150  # FULL turn's last response → >= 120: full compaction

# A prompt far larger than the keep-recent budget, so each tier's kept tail is just the final turn.
_HUGE = "keep working on the task " * 100

# Markers that read the conversation as a transcript.
_SETUP_PATH = "setup-note.txt"
_SETUP_BODY = "remember to prove compaction end to end"
_SETUP_RESULT = f"Wrote {_SETUP_PATH!r} ({len(_SETUP_BODY)} characters)."  # the elided tool output
_SKELETON_GOAL = "COMPACTED-SUMMARY-MARKER"
_FAKE_SKELETON = f"# Conversation summary\n\n## Goal\n{_SKELETON_GOAL}\n## Next Steps\nNone\n"

# Per-turn tags the scripted model maps to a plan; embedded in each driven prompt.
_TAG_SETUP = "SETUP"
_TAG_MICRO = "MICRO"
_TAG_FULL = "FULL"


# --- the scripted model: one plan per turn, driven by the latest user prompt's tag ----------


def _last_user_text(messages: list[ModelMessage]) -> str:
    """The most recent user-prompt text (the tag that selects this turn's plan)."""
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content
    return ""


def _tool_calls_since_user(messages: list[ModelMessage]) -> int:
    """How many tool calls have already fired since the latest user prompt (this turn's progress)."""
    count = 0
    for message in reversed(messages):
        if isinstance(message, ModelRequest) and any(
            isinstance(part, UserPromptPart) for part in message.parts
        ):
            break
        if isinstance(message, ModelResponse):
            count += sum(1 for part in message.parts if isinstance(part, ToolCallPart))
    return count


def _scripted_input_tokens(messages: list[ModelMessage]) -> int:
    """The per-request input tokens this turn's LAST response must report, keyed by its prompt tag.

    The token source is the last populated ``ModelResponse.usage`` (ADR-0018 §2), so pinning the
    input usage per turn is what lands each turn's final response in its intended tier band.
    """
    text = _last_user_text(messages)
    if _TAG_FULL in text:
        return _USAGE_FULL
    if _TAG_MICRO in text:
        return _USAGE_MICRO
    if _TAG_SETUP in text:
        return _USAGE_SETUP
    return _USAGE_PER_LEG  # the wrap-up / plain-text turn


def _plan_for(text: str) -> list[DeltaToolCall]:
    """The ordered tool calls a turn issues, selected by its prompt tag.

    ``SETUP`` issues one gated ``write`` (the gated call/result pair); ``MICRO`` one inline
    ``sleep``; ``FULL`` two inline ``sleep``s; the wrap-up turn issues none (plain text). The
    per-turn input usage is forced by :class:`_ScriptedModel` (SETUP/wrap-up 50, MICRO 100, FULL 150).
    """
    if _TAG_SETUP in text:
        return [
            DeltaToolCall(
                name="write",
                json_args=json.dumps({"path": _SETUP_PATH, "content": _SETUP_BODY}),
            )
        ]
    if _TAG_FULL in text:
        return [DeltaToolCall(name="sleep", json_args=json.dumps({"seconds": 0}))] * 2
    if _TAG_MICRO in text:
        return [DeltaToolCall(name="sleep", json_args=json.dumps({"seconds": 0}))]
    return []


async def _scripted_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[object]:
    """Walk each turn's plan, streaming one tool call per fresh leg, then plain text to stop.

    On each model-request leg it counts how many of this turn's planned tool calls have already
    fired and streams the next one; once the plan is exhausted it streams plain text so the turn
    reaches its would-stop boundary. Streaming (not returning) so the loop's node streamer runs.
    """
    plan = _plan_for(_last_user_text(messages))
    done = _tool_calls_since_user(messages)
    if done < len(plan):
        yield {0: plan[done]}
        return
    yield "done"


class _ScriptedModel(FunctionModel):
    """The scripted streaming model with per-request input usage forced to the turn's tier target.

    A plain streaming FunctionModel estimates a FIXED 50 input tokens per response; under the
    last-response token source (ADR-0018 §2) every leg would then read 50 and no tier would ever
    fire. ``request_stream`` overrides the streamed response's input usage with the value this
    turn's tag targets, so the LAST response of each turn genuinely crosses (or stays below) its
    tier line — exercising the real ``_leg_input_tokens`` on real messages.
    """

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[object] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        target = _scripted_input_tokens(messages)
        async with super().request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as response:
            # Replace the fixed-50 input estimate; streaming still adds output tokens on top, and
            # the leg's LAST ModelResponse ends up with usage.input_tokens == target.
            response._usage = RequestUsage(input_tokens=target)
            yield response


def _scripted_model() -> _ScriptedModel:
    """The scripted model for the capstone (see :class:`_ScriptedModel`)."""
    return _ScriptedModel(stream_function=_scripted_stream)


def _skeleton_summarizer() -> FunctionModel:
    """A non-streaming FunctionModel returning the fixed skeleton (the full-compaction leg)."""

    async def fill(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=_FAKE_SKELETON)])

    return FunctionModel(fill)


class _ScriptedResolvers:
    """The scripted human: approve every gated call (only the setup ``write`` reaches here)."""

    def __init__(self) -> None:
        self.permission_requests: list[PermissionRequest] = []

    async def resolve_permission(self, request: PermissionRequest) -> PermissionDecision:
        self.permission_requests.append(request)
        return PermissionDecision.allow()

    async def resolve_user_question(self, question: str) -> str:  # pragma: no cover - never called
        raise AssertionError("ask_user is not exercised by the compaction capstone")


# --- small history helpers ------------------------------------------------------------------


def _tool_return_contents(history: list[ModelMessage]) -> list[str]:
    """Every ToolReturnPart content string in ``history`` (to inspect blanking / fidelity)."""
    return [
        str(part.content)
        for message in history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def _has_orphan_tool_return(history: list[ModelMessage]) -> bool:
    """True if any ToolReturnPart has no matching earlier ToolCallPart (an orphaned result)."""
    seen_call_ids: set[str] = set()
    for message in history:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, ToolCallPart):
                    seen_call_ids.add(part.tool_call_id)
        elif isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_call_id not in seen_call_ids:
                    return True
    return False


def _log_line_types(log: SessionLog) -> list[str]:
    """The ``type`` discriminant of every JSONL line in the session log."""
    return [
        json.loads(line)["type"]
        for line in log.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _persisted_transcript_len(log: SessionLog) -> int:
    """The full pre-compaction transcript length: every ``messages`` line's message count summed.

    This is what the replayed history *would* be if the ``compaction`` checkpoint did not collapse
    the prefix — so a replayed length below it proves :func:`load` returned the *compacted* history.
    """
    total = 0
    for line in log.path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("type") == "messages":
            total += len(entry["messages"])
    return total


async def _run_turn(runner: Runner, prompt: str) -> None:
    """Submit one prompt and drive the runner to idle (one whole turn)."""
    from decode.tui.app import InputIntent

    await runner.submit(prompt, InputIntent.STEER)
    await runner.wait_idle()


async def test_compaction_capstone_micro_full_persist_resume(tmp_path, monkeypatch):
    """Drive setup → micro → full → wrap-up through the real stack; assert the whole cascade."""
    # --- arrange: a real working tree + a redirected session log, both under tmp ---
    working_dir = tmp_path / "workspace"
    working_dir.mkdir()
    sessions_dir = tmp_path / "sessions"

    # build_agent constructs the Gemini provider from the key even though the model is overridden.
    monkeypatch.setattr(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), raising=False
    )
    # A single small window + tiny keep-recent budget; reserves stay at their DEFAULTS (0.40/0.20),
    # so the tiers fire purely off the growing, provider-reported usage (ADR-0006 §3).
    monkeypatch.setattr(loop.settings, "compaction_context_window_tokens", _WINDOW)
    monkeypatch.setattr(loop.settings, "compaction_keep_recent_tokens", _KEEP_RECENT)

    # Every event flows through the REAL renderer into a Rich buffer: an unhandled event kind would
    # raise here and fail the turn, so this proves the new compaction render paths end to end.
    render_buffer = io.StringIO()
    console = Console(file=render_buffer, force_terminal=False, width=100)
    emitted: list[events.Event] = []

    def on_event(event: events.Event) -> None:
        emitted.append(event)
        console.print(render.render_event(event))

    resolvers = _ScriptedResolvers()
    agent = build_agent()
    deps = AgentDeps(
        cwd=working_dir,
        emit=on_event,
        gate=PermissionGate(),
        resolve_permission=resolvers.resolve_permission,
        resolve_user_question=resolvers.resolve_user_question,
    )
    log = SessionLog.create(
        sessions_dir,
        cwd=working_dir,
        now=datetime(2026, 6, 26, 9, 0, tzinfo=UTC),
        session_id=UUID("00000000-0000-0000-0000-00000000c0a7"),
    )
    handler = AgentTurnHandler(
        agent,
        deps=deps,
        session_log=log,
        compaction_model=_skeleton_summarizer(),
    )
    runner = Runner(handler, on_event=on_event)

    # --- act: walk the scripted conversation; capture state at the tier boundaries -----------
    with agent.override(model=_scripted_model()):
        # 1. setup: a gated write (approved) — usage 50, no tier; lays down the gated pair.
        await _run_turn(runner, f"{_TAG_SETUP} create the setup note")
        assert handler.last_input_tokens == _USAGE_SETUP
        assert _SETUP_RESULT in _tool_return_contents(handler.message_history)

        # 2. micro: one inline sleep — usage 100, in [90, 120) → microcompaction.
        await _run_turn(runner, f"{_TAG_MICRO} sleep a beat {_HUGE}")
        assert handler.last_input_tokens == _USAGE_MICRO
        history_after_micro = list(handler.message_history)
        # Snapshot the on-disk compaction-line count AT the micro turn (before full runs), so AC1 can
        # prove micro persisted nothing per-turn rather than inferring it from the end-state total.
        compaction_lines_after_micro = _log_line_types(log).count("compaction")

        # 3. full: two inline sleeps — usage 150, >= 120 → full compaction. The moment it lands the
        # gauge drops off the provider's pre-compaction 150 to the chars≈/4 estimate of the kept
        # [summary, *tail] (ADR-0018 §4) — the footer falls without waiting for the next leg.
        await _run_turn(runner, f"{_TAG_FULL} sleep twice {_HUGE}")
        compacted_history = list(handler.message_history)
        assert handler.last_input_tokens == estimate_history_tokens(compacted_history)
        assert (
            handler.last_input_tokens != _USAGE_FULL
        )  # no longer the pre-compaction provider number
        persisted_count_after_full = handler._persisted_count  # the cursor right after compaction
        compacted_on_disk = session_log.load(log.path)  # [summary, *tail] from the checkpoint

        # 4. wrap-up: plain text — usage 50, no tier; lands after the checkpoint. The next leg's
        # provider number overwrites the post-compaction estimate (ADR-0018 §4: no compaction loop).
        await _run_turn(runner, "wrap up and summarize what is left")
        assert handler.last_input_tokens == _USAGE_PER_LEG

    # AC 1 — MICRO tier: in-memory blanking, the ContextMicrocompacted event, NO compaction line,
    # and full fidelity on disk.
    micro_events = [e for e in emitted if isinstance(e, events.ContextMicrocompacted)]
    assert len(micro_events) == 1, "exactly one microcompaction fired (the usage-100 turn)"
    assert micro_events[0].elided_count == 1  # only the setup turn's now-old write result
    assert micro_events[0].before_tokens == _USAGE_MICRO

    # The setup turn's write result was blanked IN MEMORY right after the micro turn.
    micro_returns = _tool_return_contents(history_after_micro)
    assert _MICRO_PLACEHOLDER in micro_returns, "the old write result was blanked in memory"
    assert _SETUP_RESULT not in micro_returns, "the original write body is gone from memory"

    # Microcompaction is in-memory only: the snapshot taken AT the micro turn proves it persisted no
    # compaction line (per-turn evidence, not inferred from the end-state total below).
    assert compaction_lines_after_micro == 0, (
        "micro wrote no compaction line (snapshot at its turn)"
    )
    # And the end state still carries exactly the one line the later FULL tier wrote.
    assert _log_line_types(log).count("compaction") == 1, (
        "only the later FULL tier writes a compaction line; micro writes none"
    )
    raw_log = log.path.read_text(encoding="utf-8")
    assert _SETUP_RESULT in raw_log, "the log keeps the original full tool output"
    assert _MICRO_PLACEHOLDER not in raw_log, "the micro placeholder never reaches disk"

    # AC 2 — FULL tier: the ContextCompacted event, [summary, *tail], _persisted_count == len,
    # exactly one compaction line.
    full_events = [e for e in emitted if isinstance(e, events.ContextCompacted)]
    assert len(full_events) == 1, "exactly one full compaction fired (the usage-150 turn)"
    assert full_events[0].before_tokens == _USAGE_FULL

    # The running history is exactly [summary_message, *tail]: a synthetic summary head framing the
    # skeleton, then the recent verbatim tail (the FULL turn).
    summary_head = compacted_history[0]
    assert isinstance(summary_head, ModelRequest)
    head_text = "".join(str(getattr(part, "content", "")) for part in summary_head.parts)
    assert "Summary of the earlier conversation" in head_text
    assert _SKELETON_GOAL in head_text
    assert full_events[0].kept_messages == len(compacted_history) - 1
    # The cursor is reset to the compacted length so the next turn re-persists nothing (captured
    # right after the full compaction, before the wrap-up turn advances it).
    assert persisted_count_after_full == len(compacted_history)
    # Exactly one compaction checkpoint line on disk (only full compaction persists, ADR-0006 §6).
    assert _log_line_types(log).count("compaction") == 1

    # AC 4 — NO ORPHAN: the compacted tail never starts on an orphaned tool result (the tail snaps
    # to a user-turn boundary, ADR-0006 §5). Asserted on both the live and replayed history.
    assert not _has_orphan_tool_return(compacted_history), "live compacted tail has no orphan"

    # AC 3 — RESUME: replaying the SAME log yields the COMPACTED history (summary + tail), NOT the
    # full pre-compaction transcript; the wrap-up turn replays as [summary, *tail, *later].
    replayed = session_log.load(log.path)
    # It replays the compacted history, not the full transcript: shorter than every persisted
    # message, and it carries the summary head — not the dropped older turns.
    assert len(replayed) < _persisted_transcript_len(log), "replay is the compacted history"
    replay_head = replayed[0]
    assert isinstance(replay_head, ModelRequest)
    assert _SKELETON_GOAL in "".join(str(getattr(p, "content", "")) for p in replay_head.parts)
    # The dropped older turns (the setup write) are summarized away, not replayed verbatim.
    assert _SETUP_RESULT not in _tool_return_contents(replayed)

    # The wrap-up turn appended AFTER the checkpoint replays as [summary, *tail, *later-turn]: the
    # compacted prefix is preserved verbatim and the later turn extends it.
    assert replayed[: len(compacted_on_disk)] == compacted_on_disk, "compacted prefix preserved"
    later = replayed[len(compacted_on_disk) :]
    assert later, "the post-compaction wrap-up turn extends the replayed history"
    later_prompts = [
        str(part.content)
        for message in later
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert any("wrap up and summarize" in prompt for prompt in later_prompts)
    # And the replayed compacted+later history is still orphan-free.
    assert not _has_orphan_tool_return(replayed), "replayed tail has no orphan"

    # The real renderer ran on every event without raising; the compaction transcript is visible.
    rendered = render_buffer.getvalue()
    assert "microcompacted context" in rendered, "the microcompaction line renders in the TUI"
    # "compacted context" is a substring of "microcompacted context", so assert a fragment unique to
    # the FULL line (render._render_context_compacted: "… → summary + N recent messages.").
    assert "recent messages)" in rendered, "the full-compaction line renders in the TUI"

    # Only the setup ``write`` ever reached the human resolver (the single gated call/result pair);
    # the ungated inline ``sleep``s never prompt (ADR-0003 §8).
    assert [r.tool_name for r in resolvers.permission_requests] == ["write"]
    # The approved write actually hit disk (the gated pair ran for real).
    assert (working_dir / _SETUP_PATH).read_text(encoding="utf-8") == _SETUP_BODY


# =============================================================================================
# Single-long-turn capstone (ADR-0018): the ORIGINAL bug shape — one user prompt, dozens of tool
# rounds in ONE turn, no user-turn boundary except index 0. The multi-turn capstone above never
# exercised this: each of its turns is short, so ``split_tail`` always had a late user-turn
# boundary to snap to. Here the whole turn is one leg with ~16 tool call/return rounds, so the OLD
# user-turn-only snap collapsed the cut to 0 and BOTH tiers no-op'd — the exact session that
# ``.decode/sessions/20260722T181859Z…jsonl`` recorded with 1 prompt + 63 tool messages and no
# ``compaction`` line ever. These tests pin the composed fix (tasks 125-128) on that shape.
# =============================================================================================

# ~15+ tool call/return rounds inside ONE turn (the original bug shape had 63 tool messages).
_LONG_TURN_ROUNDS = 16
# Keep a few recent messages so the cut lands INSIDE the long turn (not at 0, not at the end).
_KEEP_RECENT_LONG = 40
# Per-request input on each tool-round response; the cumulative RunUsage would SUM these (~Nx),
# so a value here far above the last response's occupancy exposes a cumulative-source regression.
_MID_INPUT = 60
# FULL scenario: the LAST response's occupancy crosses the full line (int(150*0.8) == 120).
_FULL_LAST_INPUT = 95
_FULL_LAST_CACHE = 30  # 95 + 30 == 125 >= 120 → full; cache_read counts as occupancy (ADR-0018 §2)
# MICRO scenario: the LAST response's occupancy lands between micro (90) and full (120).
_MICRO_LAST_INPUT = 70
_MICRO_LAST_CACHE = 25  # 70 + 25 == 95 ∈ [90, 120) → microcompaction only
# A follow-up leg's occupancy — below both tiers, so it only OVERWRITES the post-compaction gauge.
_FOLLOWUP_INPUT = 50
# A prompt far larger than the keep-recent budget, so the single long turn's prefix is "old".
_LONG_PROMPT = f"work the long task through many steps {_HUGE}"


def _make_long_turn_stream(rounds: int):
    """A streaming plan that issues ``rounds`` inline ``sleep`` calls in ONE turn, then plain text.

    Ungated ``sleep`` never pauses the leg, so all ``rounds`` tool rounds run inside a single
    ``agent.iter()`` leg — the single-long-turn shape (one user prompt, dozens of tool messages,
    exactly one user-turn boundary at index 0). ``rounds == 0`` yields text immediately (a plain
    follow-up leg).
    """

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[object]:
        done = _tool_calls_since_user(messages)
        if done < rounds:
            yield {0: DeltaToolCall(name="sleep", json_args=json.dumps({"seconds": 0}))}
            return
        yield "done"

    return stream


class _LongTurnModel(FunctionModel):
    """Scripted single-long-turn model with per-response usage forced (ADR-0018 §2).

    Every tool-round response reports ``mid_input`` input tokens; the FINAL (plain-text) response
    reports ``final_input`` + ``final_cache``. Under the last-response token source the leg's
    occupancy is that final pair — NOT the cumulative sum across the ``rounds`` responses, which
    would overcount ~Nx. Forcing a distinct, larger last value is what lands the turn in its tier.
    """

    def __init__(
        self, *, rounds: int, mid_input: int, final_input: int, final_cache: int = 0
    ) -> None:
        super().__init__(stream_function=_make_long_turn_stream(rounds))
        self._rounds = rounds
        self._mid_input = mid_input
        self._final_input = final_input
        self._final_cache = final_cache

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[object] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        # The plan is exhausted once ``_rounds`` tool calls have fired → this is the final response.
        done = _tool_calls_since_user(messages)
        if done >= self._rounds:
            usage = RequestUsage(
                input_tokens=self._final_input, cache_read_tokens=self._final_cache
            )
        else:
            usage = RequestUsage(input_tokens=self._mid_input)
        async with super().request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as response:
            response._usage = usage
            yield response


def _synthetic_long_turn_history(rounds: int, *, last_usage: RequestUsage) -> list[ModelMessage]:
    """Construct the single-long-turn history directly (message construction only, ADR-0018 §1).

    One big user prompt (the "old" prefix), then ``rounds`` intact ``sleep`` call/return pairs, then
    a final plain-text response carrying ``last_usage``. Exactly one user-turn boundary (index 0),
    so the OLD user-turn-only snap collapses ``split_tail`` to 0; the fix snaps to a ``ModelResponse``
    boundary inside the turn instead. Each tool-round response carries populated (mid) usage so the
    last-response walk in :func:`_leg_input_tokens` must skip them to reach ``last_usage``.
    """
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content=_HUGE)])]
    for index in range(rounds):
        call_id = f"sleep-{index}"
        messages.append(
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name="sleep", args='{"seconds": 0}', tool_call_id=call_id)
                ],
                usage=RequestUsage(input_tokens=_MID_INPUT),
            )
        )
        messages.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="sleep",
                        content="Slept for 0.0 seconds.",
                        tool_call_id=call_id,
                    )
                ]
            )
        )
    messages.append(ModelResponse(parts=[TextPart(content="done")], usage=last_usage))
    return messages


def _tool_pairs_intact(history: list[ModelMessage]) -> bool:
    """True iff every ToolCallPart in ``history`` has its ToolReturnPart, and vice versa."""
    call_ids = {
        part.tool_call_id
        for message in history
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    }
    return_ids = {
        part.tool_call_id
        for message in history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }
    return call_ids == return_ids


def _long_turn_setup(tmp_path, monkeypatch, *, subdir: str, with_compaction: bool):
    """Build a real handler + runner for a single-long-turn run under ``tmp_path/subdir``.

    Mirrors the multi-turn capstone's wiring (real ``build_agent`` + ``Runner`` + ``AgentTurnHandler``,
    real ``render_event`` on every event, a redirected ``SessionLog``), with the small window and
    tiny keep-recent budget the tier arithmetic needs. Fully offline: a fake key lets ``build_agent``
    construct and the model is overridden per run. ``with_compaction`` wires the stub summarizer.
    """
    working_dir = tmp_path / subdir / "workspace"
    working_dir.mkdir(parents=True)
    sessions_dir = tmp_path / subdir / "sessions"

    monkeypatch.setattr(
        "decode.agent.factory.settings.gemini_api_key", SecretStr("test-key"), raising=False
    )
    monkeypatch.setattr(loop.settings, "compaction_context_window_tokens", _WINDOW)
    monkeypatch.setattr(loop.settings, "compaction_keep_recent_tokens", _KEEP_RECENT_LONG)

    render_buffer = io.StringIO()
    console = Console(file=render_buffer, force_terminal=False, width=100)
    emitted: list[events.Event] = []

    def on_event(event: events.Event) -> None:
        emitted.append(event)
        console.print(render.render_event(event))

    resolvers = _ScriptedResolvers()
    agent = build_agent()
    deps = AgentDeps(
        cwd=working_dir,
        emit=on_event,
        gate=PermissionGate(),
        resolve_permission=resolvers.resolve_permission,
        resolve_user_question=resolvers.resolve_user_question,
    )
    log = SessionLog.create(
        sessions_dir,
        cwd=working_dir,
        now=datetime(2026, 7, 22, 18, 18, 59, tzinfo=UTC),
        session_id=UUID("00000000-0000-0000-0000-00000000108e"),
    )
    handler = AgentTurnHandler(
        agent,
        deps=deps,
        session_log=log,
        compaction_model=_skeleton_summarizer() if with_compaction else None,
    )
    runner = Runner(handler, on_event=on_event)
    return agent, handler, runner, emitted, render_buffer, log


async def test_single_long_turn_primitives_on_the_original_shape(tmp_path, monkeypatch):
    """The four fixes, pinned on a synthetic single-long-turn history (no run) — ADR-0018 §1-4.

    Message construction only: one user prompt + ~16 intact ``sleep`` pairs + a final text response.
    Asserts (125) ``split_tail`` cuts at a ``ModelResponse`` boundary INSIDE the turn (never 0);
    (126) ``_leg_input_tokens`` is the LAST response's ``input + cache_read``, not the cumulative
    sum nor the first response; (127) ``compact()`` returns ``CompactOutcome.COMPACTED`` then
    ``NOTHING_TO_COMPACT``; (128) the post-compaction gauge is the chars≈/4 estimate of the kept
    ``[summary, *tail]``.
    """
    monkeypatch.setattr(loop.settings, "compaction_context_window_tokens", _WINDOW)
    monkeypatch.setattr(loop.settings, "compaction_keep_recent_tokens", _KEEP_RECENT_LONG)

    last_usage = RequestUsage(input_tokens=_FULL_LAST_INPUT, cache_read_tokens=_FULL_LAST_CACHE)
    history = _synthetic_long_turn_history(_LONG_TURN_ROUNDS, last_usage=last_usage)
    # Sanity: the shape is one turn — exactly one user-turn boundary, at index 0.
    assert (
        sum(
            1
            for message in history
            if isinstance(message, ModelRequest)
            and any(isinstance(part, UserPromptPart) for part in message.parts)
        )
        == 1
    )

    # (125) The cut snaps to a ModelResponse boundary INSIDE the turn — never the collapse-to-0 the
    # old user-turn-only snap produced on this exact shape.
    split = split_tail(history, keep_recent_tokens=_KEEP_RECENT_LONG)
    assert 0 < split < len(history), "single long turn finds a cut inside itself (not 0)"
    assert isinstance(history[split], ModelResponse), "the cut is at a ModelResponse boundary"
    assert _tool_pairs_intact(history[split:]), "the kept tail keeps every call/result pair intact"
    assert not _has_orphan_tool_return(history[split:])

    # (126) The leg's occupancy is the LAST populated response's own usage — NOT the cumulative sum
    # over the 16 tool-round responses, and NOT the first response walking from the front.
    assert _leg_input_tokens(history) == _FULL_LAST_INPUT + _FULL_LAST_CACHE
    assert _leg_input_tokens(history) < _MID_INPUT * _LONG_TURN_ROUNDS, "not the cumulative sum"
    assert _leg_input_tokens(history) != _MID_INPUT, "not the first response's usage"

    # (127 + 128) ``/compact`` on this history is a real COMPACTED outcome, and the gauge drops to
    # the estimate of the kept history the instant it lands.
    emitted: list[events.Event] = []
    deps = AgentDeps(
        cwd=tmp_path,
        emit=emitted.append,
        gate=PermissionGate(),
        resolve_permission=_ScriptedResolvers().resolve_permission,
        resolve_user_question=_ScriptedResolvers().resolve_user_question,
    )
    handler = AgentTurnHandler(
        build_agent(),
        deps=deps,
        message_history=history,
        compaction_model=_skeleton_summarizer(),
    )
    handler._last_input_tokens = _FULL_LAST_INPUT + _FULL_LAST_CACHE

    outcome = await handler.compact()
    assert outcome is CompactOutcome.COMPACTED, "a non-trivial long turn compacts (not a no-op)"
    compacted = handler.message_history
    assert isinstance(compacted[0], ModelRequest)
    head_text = "".join(str(getattr(part, "content", "")) for part in compacted[0].parts)
    assert _SKELETON_GOAL in head_text and "Summary of the earlier conversation" in head_text
    assert isinstance(compacted[1], ModelResponse), "tail opens on a ModelResponse (ADR-0018 §1)"
    assert _tool_pairs_intact(compacted[1:]) and not _has_orphan_tool_return(compacted)
    # (128) Gauge := chars≈/4 estimate of the kept [summary, *tail], NOT the pre-compaction 125.
    assert handler.last_input_tokens == estimate_history_tokens(compacted)
    assert handler.last_input_tokens != _FULL_LAST_INPUT + _FULL_LAST_CACHE
    assert [type(e) for e in emitted] == [events.ContextCompacted]

    # (127) /compact on a trivial history that fully fits the keep-recent budget is
    # NOTHING_TO_COMPACT — a distinct outcome from a summarizer failure (the old bool could not
    # tell them apart) and from COMPACTED above.
    handler.message_history = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        ModelResponse(parts=[TextPart(content="ok")]),
    ]
    assert await handler.compact() is CompactOutcome.NOTHING_TO_COMPACT


async def test_single_long_turn_auto_full_compaction(tmp_path, monkeypatch):
    """Auto FULL compaction fires on ONE long turn through the real handler — ADR-0018 §1-4.

    Drives one user prompt + 16 inline ``sleep`` rounds (one leg) with the last response pegged
    above the full line. At would-stop the cascade triggers off that LAST response's usage (125),
    ``split_tail`` cuts at a ``ModelResponse`` boundary inside the turn, history becomes
    ``[summary, *tail]`` with every pair intact, a ``compaction`` line is persisted, and
    ``ContextCompacted`` is emitted. Then the gauge drops to the kept-history estimate (128) and a
    follow-up leg overwrites it with the provider number (128). This is the shape the original bug
    silently no-op'd on.
    """
    agent, handler, runner, emitted, render_buffer, log = _long_turn_setup(
        tmp_path, monkeypatch, subdir="full", with_compaction=True
    )

    with agent.override(
        model=_LongTurnModel(
            rounds=_LONG_TURN_ROUNDS,
            mid_input=_MID_INPUT,
            final_input=_FULL_LAST_INPUT,
            final_cache=_FULL_LAST_CACHE,
        )
    ):
        await _run_turn(runner, _LONG_PROMPT)

    # --- ASSERTION 1: auto full compaction fired on the single long turn -----------------------
    full_events = [e for e in emitted if isinstance(e, events.ContextCompacted)]
    assert len(full_events) == 1, "exactly one full compaction fired on the one long turn"
    # The trigger read the LAST response's occupancy (125 == 95 + 30), NOT the cumulative sum over
    # the 16 tool rounds — a cumulative source would report a far larger before_tokens (pins 126).
    assert full_events[0].before_tokens == _FULL_LAST_INPUT + _FULL_LAST_CACHE
    assert full_events[0].before_tokens < _MID_INPUT * _LONG_TURN_ROUNDS

    compacted = list(handler.message_history)
    # History is exactly [summary, *tail]: a summary head, then the recent verbatim tail.
    assert isinstance(compacted[0], ModelRequest)
    head_text = "".join(str(getattr(part, "content", "")) for part in compacted[0].parts)
    assert "Summary of the earlier conversation" in head_text and _SKELETON_GOAL in head_text
    # The cut landed INSIDE the turn: fewer messages than the full pre-compaction transcript, and
    # the tail opens on a ModelResponse boundary (the fix; the old snap would collapse to 0).
    assert 1 < len(compacted) < 2 * _LONG_TURN_ROUNDS + 2
    assert isinstance(compacted[1], ModelResponse), (
        "the kept tail opens on a ModelResponse boundary"
    )
    assert full_events[0].kept_messages == len(compacted) - 1
    assert _tool_pairs_intact(compacted[1:]), "every kept call/result pair survives intact"
    assert not _has_orphan_tool_return(compacted), "the compacted tail has no orphaned tool return"
    # Exactly one compaction checkpoint on disk (ADR-0006 §6).
    assert _log_line_types(log).count("compaction") == 1

    # --- ASSERTION 2 (post-compaction): the gauge dropped to the kept-history estimate (128) ---
    assert handler.last_input_tokens == estimate_history_tokens(compacted)
    assert handler.last_input_tokens != _FULL_LAST_INPUT + _FULL_LAST_CACHE

    # Resume replays the COMPACTED history (summary + tail), not the full pre-compaction transcript.
    replayed = session_log.load(log.path)
    assert len(replayed) < _persisted_transcript_len(log), "replay is the compacted history"
    assert _SKELETON_GOAL in "".join(str(getattr(p, "content", "")) for p in replayed[0].parts)
    assert not _has_orphan_tool_return(replayed)

    # --- ASSERTION 2 (follow-up leg): the provider number overwrites the estimate (128) --------
    with agent.override(
        model=_LongTurnModel(rounds=0, mid_input=_MID_INPUT, final_input=_FOLLOWUP_INPUT)
    ):
        await _run_turn(runner, "wrap up")
    assert handler.last_input_tokens == _FOLLOWUP_INPUT, "the next leg's provider number takes over"
    # The follow-up was below both tiers, so no further compaction fired.
    assert len([e for e in emitted if isinstance(e, events.ContextCompacted)]) == 1

    # The full-compaction line rendered in the TUI without the renderer raising on any event.
    assert "recent messages)" in render_buffer.getvalue()


async def test_single_long_turn_microcompaction(tmp_path, monkeypatch):
    """Micro tier on the SAME single-long-turn shape — ADR-0018 §1-2, ADR-0006 §3a.

    Same one-prompt, 16-round turn, but the last response's occupancy is tuned BETWEEN the micro
    and full lines. Microcompaction fires with ``elided > 0`` (the fix: ``split_tail`` finds a cut
    inside the turn, so there are old tool returns to blank — the old snap would elide nothing), the
    gauge stays on the last-response number (micro never reseeds it), and the JSONL log keeps full
    fidelity: no ``compaction`` line, the cursor unmoved, the placeholder in memory only.
    """
    agent, handler, runner, emitted, render_buffer, log = _long_turn_setup(
        tmp_path, monkeypatch, subdir="micro", with_compaction=True
    )

    with agent.override(
        model=_LongTurnModel(
            rounds=_LONG_TURN_ROUNDS,
            mid_input=_MID_INPUT,
            final_input=_MICRO_LAST_INPUT,
            final_cache=_MICRO_LAST_CACHE,
        )
    ):
        await _run_turn(runner, _LONG_PROMPT)

    # --- ASSERTION 3: microcompaction fired, elided > 0, no full compaction --------------------
    micro_events = [e for e in emitted if isinstance(e, events.ContextMicrocompacted)]
    assert len(micro_events) == 1, "exactly one microcompaction fired on the one long turn"
    assert micro_events[0].elided_count > 0, (
        "the cut inside the turn left old tool returns to blank"
    )
    # before_tokens is the LAST response's occupancy (95 == 70 + 25), not the cumulative sum (126).
    assert micro_events[0].before_tokens == _MICRO_LAST_INPUT + _MICRO_LAST_CACHE
    assert micro_events[0].before_tokens < _MID_INPUT * _LONG_TURN_ROUNDS
    assert not any(isinstance(e, events.ContextCompacted) for e in emitted), (
        "full tier did not fire"
    )

    # --- ASSERTION 2 (during turn): the gauge is the last-response number, not the cumulative sum
    assert handler.last_input_tokens == _MICRO_LAST_INPUT + _MICRO_LAST_CACHE
    assert handler.last_input_tokens < _MID_INPUT * _LONG_TURN_ROUNDS

    # The old tool outputs were blanked IN MEMORY only.
    assert _MICRO_PLACEHOLDER in _tool_return_contents(handler.message_history)

    # --- ASSERTION 3: the JSONL log keeps FULL fidelity — no compaction line, cursor unmoved ----
    assert _log_line_types(log).count("compaction") == 0, "micro persists no compaction line"
    assert _MICRO_PLACEHOLDER not in log.path.read_text(encoding="utf-8"), (
        "placeholder stays in memory"
    )
    # The cursor tracked the full history and micro never collapsed it: every message is on disk.
    assert (
        _persisted_transcript_len(log) == handler._persisted_count == len(handler.message_history)
    )

    # The microcompaction line rendered in the TUI.
    assert "microcompacted context" in render_buffer.getvalue()
