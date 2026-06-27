"""The context-compaction capstone: one scripted conversation through the FULL real stack.

This is the living proof for the context-compaction feature (ADR-0006) — and it doubles as
documentation, in the style of :mod:`tests.integration.test_milestone1_capstone`. It drives a
multi-turn conversation that crosses **both** window-relative compaction tiers through the **real**
wiring, swapping out only the network boundary:

* the real :func:`decode.agent.factory.build_agent` (the real flat tool registry + the real
  deferred-tool / permission seam);
* the real :class:`decode.harness.runner.Runner` + :class:`decode.agent.loop.AgentTurnHandler`,
  with the real two-tier auto-compaction cascade wired (``compaction_model_or_settings=``) so the
  *real* :meth:`~decode.agent.loop.AgentTurnHandler._maybe_auto_compact` ⇄ ``compact()`` ⇄
  ``microcompact()`` chain runs at the would-stop boundary (ADR-0006 §3-7);
* the real :func:`decode.tui.render.render_event` on every emitted event (so the new
  ``ContextMicrocompacted`` / ``ContextCompacted`` render paths are proven not to crash);
* the real :class:`decode.context.session_log.SessionLog` + :func:`decode.context.session_log.load`
  (so the JSONL ``messages`` / ``compaction`` lines are written and the ``--resume`` replay of a
  *compacted* log is proven).

**No network, no API key.** The model is a scripted
:class:`~pydantic_ai.models.function.FunctionModel` (``GEMINI_API_KEY`` is faked only so
``build_agent`` constructs) and the full-compaction summarizer is a second ``FunctionModel`` that
returns the fixed skeleton. The session log dir is redirected under ``tmp_path``.

**How the two tiers fire deterministically (the token arithmetic).** pydantic-ai's *streaming*
``FunctionModel`` reports a **fixed** ``input_tokens`` of 50 *per model-request leg*
(``FunctionStreamedResponse`` estimates the request from an empty message list), and through
decode's deferred-tool architecture every *gated* tool call splits the run into a pause + resume
across two ``agent.iter`` runs — so a gated turn's measured usage is just the final resume leg (50).
The **ungated** ``sleep`` control tool (ADR-0003 §8) instead runs *inline* within a single
``agent.iter`` run, so each ``sleep`` call adds one model-request leg to that run's aggregate. That
is the knob this capstone uses to make the **real** measured ``input_tokens`` genuinely grow as the
conversation does more work, against a single fixed (patched-small) window:

* 0 inline sleeps → 1 leg  → ``input_tokens == 50``
* 1 inline sleep  → 2 legs → ``input_tokens == 100``
* 2 inline sleeps → 3 legs → ``input_tokens == 150``

With the window patched to 150 and the reserves at their **defaults** (micro 0.40 / full 0.20):

* micro line = ``int(150 * (1 - 0.40)) == 90``
* full line  = ``int(150 * (1 - 0.20)) == 120``

so the scripted turns cross the tiers in order:

1. **setup** — a gated ``write`` (approved): usage 50 < 90 → no tier. Lays down the gated
   tool call/result pair whose result the micro tier later blanks.
2. **micro** — one inline ``sleep``: usage 100, in ``[90, 120)`` → **microcompaction** blanks the
   setup turn's now-old ``write`` result *in memory*; nothing is persisted.
3. **full** — two inline ``sleep``s: usage 150, ``>= 120`` → **full compaction** summarizes the
   older turns and keeps a recent verbatim tail, persisting one ``compaction`` checkpoint.
4. **wrap-up** — plain text: usage 50 < 90 → no tier. The turn that lands *after* the compaction
   checkpoint, so the resume replay proves ``[summary, *tail, *later-turn]``.

Each "recent tail" cut is forced with a huge driven prompt (>> the patched keep-recent budget), so
the kept tail is exactly the final turn and every earlier message is "old" — no fragile token
arithmetic on the tail (the trigger reads provider-authoritative usage; the tail uses the coarse
char estimate).
"""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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
from rich.console import Console

import decode.agent.loop as loop
from decode.agent.deps import AgentDeps
from decode.agent.factory import build_agent
from decode.agent.loop import AgentTurnHandler
from decode.context import session_log
from decode.context.compaction import _MICRO_PLACEHOLDER
from decode.context.session_log import SessionLog
from decode.entities import events
from decode.entities.permissions import PermissionDecision, PermissionRequest
from decode.harness.runner import Runner
from decode.permissions.gate import PermissionGate
from decode.tui import render

# --- window / tier arithmetic (defaults: micro reserve 0.40, full reserve 0.20) -------------
_WINDOW = 150  # micro line = int(150*0.6) = 90; full line = int(150*0.8) = 120
_KEEP_RECENT = 10  # tiny so the kept tail is just the final turn (the rest is "old")
_USAGE_PER_LEG = 50  # pydantic-ai's streaming FunctionModel reports a fixed 50 input tokens/leg
_USAGE_SETUP = 50  # gated write → final resume leg only
_USAGE_MICRO = 100  # 1 inline sleep → 2 legs
_USAGE_FULL = 150  # 2 inline sleeps → 3 legs

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


def _plan_for(text: str) -> list[DeltaToolCall]:
    """The ordered tool calls a turn issues, selected by its prompt tag.

    ``SETUP`` issues one gated ``write`` (the gated call/result pair); ``MICRO`` one inline
    ``sleep`` (2 legs → usage 100); ``FULL`` two inline ``sleep``s (3 legs → usage 150); the
    wrap-up turn issues none (plain text → usage 50).
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


def _scripted_model() -> FunctionModel:
    """A streaming FunctionModel that walks each turn's plan, one tool call per fresh leg.

    On each model-request leg it counts how many of this turn's planned tool calls have already
    fired and streams the next one; once the plan is exhausted it streams plain text so the turn
    reaches its would-stop boundary. Streaming (not returning) so the loop's node streamer runs.
    """

    async def stream_function(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[object]:
        plan = _plan_for(_last_user_text(messages))
        done = _tool_calls_since_user(messages)
        if done < len(plan):
            yield {0: plan[done]}
            return
        yield "done"

    return FunctionModel(stream_function=stream_function)


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
        compaction_model_or_settings=_skeleton_summarizer(),
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

        # 3. full: two inline sleeps — usage 150, >= 120 → full compaction.
        await _run_turn(runner, f"{_TAG_FULL} sleep twice {_HUGE}")
        assert handler.last_input_tokens == _USAGE_FULL
        compacted_history = list(handler.message_history)
        persisted_count_after_full = handler._persisted_count  # the cursor right after compaction
        compacted_on_disk = session_log.load(log.path)  # [summary, *tail] from the checkpoint

        # 4. wrap-up: plain text — usage 50, no tier; lands after the checkpoint.
        await _run_turn(runner, "wrap up and summarize what is left")
        assert handler.last_input_tokens == _USAGE_PER_LEG

    # ========================================================================================
    # AC 1 — MICRO tier: in-memory blanking, the ContextMicrocompacted event, NO compaction line,
    #        and full fidelity on disk.
    # ========================================================================================
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

    # ========================================================================================
    # AC 2 — FULL tier: the ContextCompacted event, [summary, *tail], _persisted_count == len,
    #        exactly one compaction line.
    # ========================================================================================
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

    # ========================================================================================
    # AC 4 — NO ORPHAN: the compacted tail never starts on an orphaned tool result (the tail snaps
    #        to a user-turn boundary, ADR-0006 §5). Asserted on both the live and replayed history.
    # ========================================================================================
    assert not _has_orphan_tool_return(compacted_history), "live compacted tail has no orphan"

    # ========================================================================================
    # AC 3 — RESUME: replaying the SAME log yields the COMPACTED history (summary + tail), NOT the
    #        full pre-compaction transcript; the wrap-up turn replays as [summary, *tail, *later].
    # ========================================================================================
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

    # ========================================================================================
    # The real renderer ran on every event without raising; the compaction transcript is visible.
    # ========================================================================================
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
