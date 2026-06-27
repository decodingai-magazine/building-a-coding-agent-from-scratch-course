"""Unit tests for :mod:`decode.context.compaction` — the pure compaction primitives (ADR-0006 §3-5).

These are the **network-free** building blocks the handler (task 044) and ``/compact`` (task 045)
orchestrate; this module owns only the math + message surgery, no wiring. Each primitive is
exercised independently and every LLM call is driven by ``FunctionModel`` / ``TestModel`` so CI
stays offline:

* :func:`reserve_threshold` — ``int(window * (1 - reserve))`` with the ``[0,1]`` / positive-window
  guards.
* :func:`should_compact` — the window-relative predicate shared by both tiers (``input_tokens == 0``
  is the safe "don't fire" fallback).
* :func:`summarize_for_compaction` — the one full-tier LLM call producing the fixed Markdown
  skeleton; ``None`` (never raises) on an empty conversation or a failing call.
* :func:`build_summary_message` — the synthetic head ``ModelRequest`` framing.
* :func:`split_tail` — the boundary-snapped recent-tail cut (never starts on an orphaned tool
  result).
* :func:`microcompact` — the no-LLM tier that blanks old tool-output bodies (idempotent, never
  removes a part, originals untouched).
"""

import dataclasses

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from decode.context import compaction
from decode.context.compaction import (
    _MICRO_PLACEHOLDER,
    build_summary_message,
    microcompact,
    reserve_threshold,
    should_compact,
    split_tail,
    summarize_for_compaction,
)

# The fixed skeleton a real summarizer fills (ADR-0006 §4); the FunctionModel below returns it
# verbatim so the test asserts the seven headings flow straight through the helper.
_SKELETON = """# Conversation summary

## Goal
Ship pagination on the users endpoint.

## Constraints & Preferences
Keep the change small; reuse existing limit param.

## Progress
Done: wired the query.
In Progress: tests.
Blocked: none.

## Key Decisions
Page size capped at 100.

## Next Steps
Add a regression test.

## Critical Context
The endpoint is at /api/users.
"""

_SKELETON_HEADINGS = [
    "# Conversation summary",
    "## Goal",
    "## Constraints & Preferences",
    "## Progress",
    "## Key Decisions",
    "## Next Steps",
    "## Critical Context",
]


# --------------------------------------------------------------------------------------------
# message builders (shared by the split_tail / microcompact tests)
# --------------------------------------------------------------------------------------------


def _user(text: str) -> ModelRequest:
    """A user-turn request (a ``ModelRequest`` carrying a ``UserPromptPart``) — a turn boundary."""
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    """An assistant text response."""
    return ModelResponse(parts=[TextPart(content=text)])


def _tool_call(name: str, call_id: str, *, args: str = "{}") -> ModelResponse:
    """An assistant response that issues a single tool call."""
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args, tool_call_id=call_id)])


def _tool_return(name: str, call_id: str, content: str) -> ModelRequest:
    """A request that returns a single tool result (the part microcompaction blanks)."""
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=name, content=content, tool_call_id=call_id)]
    )


# --------------------------------------------------------------------------------------------
# reserve_threshold — int(window * (1 - reserve)) with guards
# --------------------------------------------------------------------------------------------


def test_reserve_threshold_full_and_micro_fractions():
    # The two configured tiers against the default 1M window: full fires at 80% full, micro at 60%.
    assert reserve_threshold(1_000_000, 0.20) == 800_000
    assert reserve_threshold(1_000_000, 0.40) == 600_000


def test_reserve_threshold_floors():
    # A non-round product floors (int truncation), never rounds up.
    assert reserve_threshold(10, 0.33) == 6  # int(10 * 0.67) == int(6.7) == 6


def test_reserve_threshold_accepts_the_inclusive_bounds():
    # reserve == 0 reserves nothing (fires at the full window); reserve == 1 reserves everything.
    assert reserve_threshold(1_000_000, 0.0) == 1_000_000
    assert reserve_threshold(1_000_000, 1.0) == 0


@pytest.mark.parametrize("reserve", [-0.01, 1.01, 2.0])
def test_reserve_threshold_rejects_reserve_outside_unit_interval(reserve: float):
    with pytest.raises(ValueError, match="reserve"):
        reserve_threshold(1_000_000, reserve)


@pytest.mark.parametrize("window", [0, -1])
def test_reserve_threshold_rejects_non_positive_window(window: int):
    with pytest.raises(ValueError, match="window"):
        reserve_threshold(window, 0.20)


# --------------------------------------------------------------------------------------------
# should_compact — the window-relative predicate shared by both tiers (built RunUsage, no network)
# --------------------------------------------------------------------------------------------


def test_should_compact_true_at_the_full_level():
    # Exactly at window*(1-full_reserve) the full tier fires (>=, inclusive).
    usage = RunUsage(input_tokens=800_000)
    assert should_compact(usage, window=1_000_000, reserve=0.20, enabled=True) is True


def test_should_compact_true_at_the_micro_level():
    # The same predicate serves the micro tier with the larger reserve (fires earlier, at 60%).
    usage = RunUsage(input_tokens=600_000)
    assert should_compact(usage, window=1_000_000, reserve=0.40, enabled=True) is True


def test_should_compact_false_just_below_the_level():
    usage = RunUsage(input_tokens=799_999)
    assert should_compact(usage, window=1_000_000, reserve=0.20, enabled=True) is False


def test_should_compact_false_when_disabled_even_over_the_level():
    # `enabled=False` suppresses the automatic cascade regardless of how full the window is.
    usage = RunUsage(input_tokens=999_999)
    assert should_compact(usage, window=1_000_000, reserve=0.20, enabled=False) is False


def test_should_compact_false_on_zero_tokens_fallback():
    # Unpopulated usage (input_tokens == 0) is the safe "don't fire" fallback — no window math.
    usage = RunUsage(input_tokens=0)
    assert should_compact(usage, window=1_000_000, reserve=0.20, enabled=True) is False


# --------------------------------------------------------------------------------------------
# summarize_for_compaction — one LLM call → fixed skeleton; None (never raises) on empty/failure
# --------------------------------------------------------------------------------------------


async def test_summarize_for_compaction_returns_the_filled_skeleton():
    # A real conversation: the call returns the fixed skeleton with all seven headings present.
    messages: list[ModelMessage] = [
        _user("add pagination to the users endpoint"),
        _assistant("done, added a limit param"),
    ]

    async def fill(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=_SKELETON)])

    summary = await summarize_for_compaction(messages, model_or_settings=FunctionModel(fill))

    assert summary is not None
    for heading in _SKELETON_HEADINGS:
        assert heading in summary


async def test_summarize_for_compaction_feeds_a_transcript_with_tool_activity():
    # The transcript handed to the model carries user/assistant text AND a note of tool activity,
    # so the summary reflects what actually happened (reuses the extract.py role-prefixed style).
    seen: list[str] = []

    async def capture(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompts = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart) and isinstance(part.content, str)
        ]
        seen.append("\n".join(prompts))
        return ModelResponse(parts=[TextPart(content=_SKELETON)])

    messages: list[ModelMessage] = [
        _user("read the config file"),
        _tool_call("read_file", "c1"),
        _tool_return("read_file", "c1", "name = decode"),
        _assistant("the project is named decode"),
    ]
    await summarize_for_compaction(messages, model_or_settings=FunctionModel(capture))

    transcript = "\n".join(seen)
    assert "read the config file" in transcript
    assert "the project is named decode" in transcript
    assert "read_file" in transcript  # the brief tool-activity note


async def test_summarize_for_compaction_returns_none_on_empty_conversation():
    # Nothing to summarize → no model call, None returned (caller compacts nothing).
    called = False

    async def must_not_run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal called
        called = True
        return ModelResponse(parts=[TextPart(content=_SKELETON)])

    summary = await summarize_for_compaction([], model_or_settings=FunctionModel(must_not_run))

    assert summary is None
    assert called is False


async def test_summarize_for_compaction_returns_none_when_the_call_raises():
    # A failing summarizer is swallowed and reported as None (never raises — full compaction
    # must degrade safely to "no compaction this turn").
    async def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("model exploded")

    messages: list[ModelMessage] = [_user("do the thing"), _assistant("did the thing")]

    summary = await summarize_for_compaction(messages, model_or_settings=FunctionModel(boom))

    assert summary is None


async def test_summarize_for_compaction_logs_a_warning_when_the_call_raises(mocker):
    # The swallowed failure is logged at warning so the operator can see compaction was skipped.
    warn = mocker.patch.object(compaction.logger, "warning")

    async def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("model exploded")

    messages: list[ModelMessage] = [_user("do the thing"), _assistant("did the thing")]
    await summarize_for_compaction(messages, model_or_settings=FunctionModel(boom))

    warn.assert_called_once()


async def test_summarize_for_compaction_returns_none_when_the_model_returns_blank():
    # A blank summary is "nothing to compact into", not an empty head message.
    messages: list[ModelMessage] = [_user("do the thing"), _assistant("did the thing")]
    model = TestModel(custom_output_text="   ")

    summary = await summarize_for_compaction(messages, model_or_settings=model)

    assert summary is None


# --------------------------------------------------------------------------------------------
# build_summary_message — the synthetic head ModelRequest / UserPromptPart
# --------------------------------------------------------------------------------------------


def test_build_summary_message_shape_and_framing():
    message = build_summary_message(_SKELETON)

    assert isinstance(message, ModelRequest)
    assert len(message.parts) == 1
    part = message.parts[0]
    assert isinstance(part, UserPromptPart)
    assert isinstance(part.content, str)
    # The skeleton rides verbatim inside a "this is a summary of the compacted earlier
    # conversation" framing so the model reads it as context, not a fresh user instruction.
    assert _SKELETON in part.content
    assert "compacted" in part.content.lower()


# --------------------------------------------------------------------------------------------
# split_tail — largest boundary-snapped recent tail (never starts on an orphaned tool result)
# --------------------------------------------------------------------------------------------


def test_split_tail_returns_zero_when_everything_fits():
    # A short history under the budget keeps the whole thing — the tail begins at index 0.
    messages: list[ModelMessage] = [
        _user("q0"),
        _assistant("a0"),
        _user("q1"),
        _assistant("a1"),
    ]
    assert split_tail(messages, keep_recent_tokens=1_000) == 0


def test_split_tail_returns_len_when_nothing_fits():
    # A zero budget keeps nothing — the tail begins past the end (handler keeps only the summary).
    messages: list[ModelMessage] = [_user("a real question"), _assistant("a real answer")]
    assert split_tail(messages, keep_recent_tokens=0) == len(messages)


def test_split_tail_snaps_back_to_a_user_turn_boundary_no_orphan():
    # The naive token cut lands BETWEEN a ToolCallPart and its ToolReturnPart (the call's big args
    # blow the budget). split_tail must snap back to the enclosing user-turn boundary so the kept
    # tail starts at a UserPromptPart and never at an orphaned tool result.
    big_args = "x" * 4_000  # ~1000 est tokens — far over the budget below
    messages: list[ModelMessage] = [
        _user("q0"),  # 0  — turn 1 boundary
        _tool_call("read", "c0"),  # 1
        _tool_return("read", "c0", "old read output"),  # 2
        _assistant("a0"),  # 3
        _user("q1"),  # 4  — turn 2 boundary (the snap target)
        _tool_call("write", "c1", args=big_args),  # 5  — big args break the budget here
        _tool_return("write", "c1", "recent write output"),  # 6
        _assistant("a1"),  # 7
    ]

    boundary = split_tail(messages, keep_recent_tokens=100)

    # Snapped back to turn 2's user boundary, not left at the naive cut (index 6, an orphan).
    assert boundary == 4
    tail = messages[boundary:]
    head = tail[0]
    assert isinstance(head, ModelRequest)
    assert any(isinstance(part, UserPromptPart) for part in head.parts)
    # No orphaned tool result at the head of the kept tail.
    assert not any(isinstance(part, ToolReturnPart) for part in head.parts)


# --------------------------------------------------------------------------------------------
# microcompact — blank old tool-output bodies only; idempotent; originals untouched; never removes
# --------------------------------------------------------------------------------------------


def _straddling_history() -> list[ModelMessage]:
    """Two turns; turn 1 is dominated by a huge assistant message so a 200-token budget keeps only
    turn 2 (boundary snaps to index 4). Turn 1 holds a complete read tool-pair (old); turn 2 holds
    a write tool-pair (recent)."""
    return [
        _user("q0"),  # 0  — old
        _tool_call("read", "c0"),  # 1  — old (call)
        _tool_return("read", "c0", "OLD-READ-OUTPUT padded to be a real body"),  # 2  — old (return)
        _assistant("BIG " + "z" * 4_000),  # 3  — old, huge: forces the boundary to index 4
        _user("q1"),  # 4  — recent boundary
        _tool_call("write", "c1"),  # 5  — recent (call)
        _tool_return("write", "c1", "RECENT-WRITE-OUTPUT keep me verbatim"),  # 6  — recent (return)
        _assistant("a1"),  # 7  — recent
    ]


def test_microcompact_blanks_only_old_tool_outputs():
    messages = _straddling_history()

    new_messages, elided = microcompact(messages, keep_recent_tokens=200)

    # Exactly one old ToolReturnPart was blanked.
    assert elided == 1
    old_return = new_messages[2].parts[0]
    assert isinstance(old_return, ToolReturnPart)
    assert old_return.content == _MICRO_PLACEHOLDER
    # The recent tool output and all non-tool parts are untouched.
    recent_return = new_messages[6].parts[0]
    assert isinstance(recent_return, ToolReturnPart)
    assert recent_return.content == "RECENT-WRITE-OUTPUT keep me verbatim"
    # No message added or removed.
    assert len(new_messages) == len(messages)


def test_microcompact_does_not_mutate_the_originals():
    messages = _straddling_history()
    original_old_body = messages[2].parts[0].content

    new_messages, _ = microcompact(messages, keep_recent_tokens=200)

    # The blanked part is a fresh object; the caller's original list/parts are unchanged.
    assert messages[2].parts[0].content == original_old_body
    assert new_messages[2] is not messages[2]
    assert new_messages[2].parts[0] is not messages[2].parts[0]


def test_microcompact_is_idempotent():
    messages = _straddling_history()

    once, first = microcompact(messages, keep_recent_tokens=200)
    twice, second = microcompact(once, keep_recent_tokens=200)

    assert first == 1
    # Second pass finds nothing new to blank and returns the input list unchanged.
    assert second == 0
    assert twice is once


def test_microcompact_returns_input_and_zero_when_nothing_to_elide():
    # Everything fits the budget → nothing is "old" → no elision, the input is returned as-is.
    messages: list[ModelMessage] = [
        _user("q0"),
        _tool_call("read", "c0"),
        _tool_return("read", "c0", "small output"),
        _assistant("a0"),
    ]

    new_messages, elided = microcompact(messages, keep_recent_tokens=1_000_000)

    assert elided == 0
    assert new_messages is messages


def test_microcompact_keeps_both_parts_of_a_tool_pair_present():
    # microcompact only blanks content — it never removes a part — so a tool-call/result pair can
    # never be orphaned: the old read call AND its (now-blanked) return both survive.
    messages = _straddling_history()

    new_messages, _ = microcompact(messages, keep_recent_tokens=200)

    call_parts = [
        part
        for message in new_messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]
    return_parts = [
        part
        for message in new_messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    # Both pairs (read + write) intact: 2 calls, 2 returns — nothing dropped.
    assert {part.tool_name for part in call_parts} == {"read", "write"}
    assert {part.tool_name for part in return_parts} == {"read", "write"}


def test_microcompact_blanks_old_retry_prompt_parts():
    # RetryPromptPart bodies are elided the same way as tool returns when they sit in the old
    # region (they carry error feedback that is dead weight once the turn moved on).
    messages: list[ModelMessage] = [
        _user("q0"),
        ModelResponse(parts=[ToolCallPart(tool_name="write", args="{}", tool_call_id="c0")]),
        ModelRequest(
            parts=[RetryPromptPart(content="invalid path", tool_name="write", tool_call_id="c0")]
        ),
        _assistant("BIG " + "z" * 4_000),  # forces the boundary to the recent turn
        _user("q1"),
        _assistant("a1"),
    ]

    new_messages, elided = microcompact(messages, keep_recent_tokens=200)

    assert elided == 1
    retry = new_messages[2].parts[0]
    assert isinstance(retry, RetryPromptPart)
    assert retry.content == _MICRO_PLACEHOLDER


def test_micro_placeholder_constant():
    # The placeholder is the ADR-0006 §3a wording (asserted so a downstream rename is caught here).
    assert _MICRO_PLACEHOLDER == "[tool output elided by microcompaction]"
    # It is rebuilt via dataclasses.replace, so a blanked part stays a real ToolReturnPart.
    part = ToolReturnPart(tool_name="t", content="real", tool_call_id="c")
    blanked = dataclasses.replace(part, content=_MICRO_PLACEHOLDER)
    assert isinstance(blanked, ToolReturnPart)
    assert blanked.content == _MICRO_PLACEHOLDER
