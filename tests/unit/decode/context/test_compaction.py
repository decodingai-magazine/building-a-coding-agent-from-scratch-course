"""Unit tests for :mod:`decode.context.compaction` — the pure compaction primitives (ADR-0006 §3-5).

Covers ``reserve_threshold`` math + guards, the ``should_compact`` predicate, the
``summarize_for_compaction`` LLM call (fixed skeleton; None, never raises, on empty/failure),
``build_summary_message`` framing, the boundary-snapped ``split_tail`` cut, and the no-LLM
``microcompact`` tier (idempotent, never removes a part, originals untouched). Every LLM call
is a ``FunctionModel`` / ``TestModel`` — no network.
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
    estimate_history_tokens,
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


# message builders shared by the split_tail / microcompact tests


def _user(text: str) -> ModelRequest:
    """A user-turn request (a ``ModelRequest`` carrying a ``UserPromptPart``) — a turn boundary."""
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _tool_call(name: str, call_id: str, *, args: str = "{}") -> ModelResponse:
    """An assistant response that issues a single tool call."""
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args, tool_call_id=call_id)])


def _tool_return(name: str, call_id: str, content: str) -> ModelRequest:
    """A request that returns a single tool result (the part microcompaction blanks)."""
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=name, content=content, tool_call_id=call_id)]
    )


# reserve_threshold


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


# should_compact


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


# summarize_for_compaction


async def test_summarize_for_compaction_returns_the_filled_skeleton():
    # A real conversation: the call returns the fixed skeleton with all seven headings present.
    messages: list[ModelMessage] = [
        _user("add pagination to the users endpoint"),
        _assistant("done, added a limit param"),
    ]

    async def fill(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=_SKELETON)])

    summary = await summarize_for_compaction(messages, model=FunctionModel(fill))

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
    await summarize_for_compaction(messages, model=FunctionModel(capture))

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

    summary = await summarize_for_compaction([], model=FunctionModel(must_not_run))

    assert summary is None
    assert called is False


async def test_summarize_for_compaction_returns_none_when_the_call_raises():
    # A failing summarizer is swallowed and reported as None (never raises — full compaction
    # must degrade safely to "no compaction this turn").
    async def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("model exploded")

    messages: list[ModelMessage] = [_user("do the thing"), _assistant("did the thing")]

    summary = await summarize_for_compaction(messages, model=FunctionModel(boom))

    assert summary is None


async def test_summarize_for_compaction_logs_a_warning_when_the_call_raises(mocker):
    # The swallowed failure is logged at warning so the operator can see compaction was skipped.
    warn = mocker.patch.object(compaction.logger, "warning")

    async def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("model exploded")

    messages: list[ModelMessage] = [_user("do the thing"), _assistant("did the thing")]
    await summarize_for_compaction(messages, model=FunctionModel(boom))

    warn.assert_called_once()


async def test_summarize_for_compaction_returns_none_when_the_model_returns_blank():
    # A blank summary is "nothing to compact into", not an empty head message.
    messages: list[ModelMessage] = [_user("do the thing"), _assistant("did the thing")]
    model = TestModel(custom_output_text="   ")

    summary = await summarize_for_compaction(messages, model=model)

    assert summary is None


async def test_summarize_for_compaction_builds_no_provider_model(mocker):
    # ADR-0018 §5: compaction rides the Provider Seam — the module accepts a built Model and never
    # constructs a provider model of its own. The old ``Settings → GoogleModel`` branch is gone, so
    # a summarize call must not build a GoogleModel/GoogleProvider anywhere (an openrouter/modal
    # user without GEMINI_API_KEY used to hit exactly that hardcode).
    import pydantic_ai.models.google as google_module
    import pydantic_ai.providers.google as google_provider_module

    google_model = mocker.spy(google_module, "GoogleModel")
    google_provider = mocker.spy(google_provider_module, "GoogleProvider")

    async def fill(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=_SKELETON)])

    messages: list[ModelMessage] = [_user("do the thing"), _assistant("did the thing")]
    await summarize_for_compaction(messages, model=FunctionModel(fill))

    google_model.assert_not_called()
    google_provider.assert_not_called()


def test_compaction_module_imports_no_google_or_settings_for_model_construction():
    # AC (regression): the module drops its Google + Settings-for-model imports so no import cycle
    # and no provider hardcode can sneak back in (ADR-0018 §5).
    assert not hasattr(compaction, "GoogleModel")
    assert not hasattr(compaction, "GoogleProvider")
    assert not hasattr(compaction, "Settings")
    assert not hasattr(compaction, "_resolve_model")


# build_summary_message


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


# split_tail


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


def _has_tool_return(message: ModelMessage) -> bool:
    """Whether ``message`` is a ModelRequest carrying a ToolReturnPart/RetryPromptPart."""
    return isinstance(message, ModelRequest) and any(
        isinstance(part, ToolReturnPart | RetryPromptPart) for part in message.parts
    )


def _long_agentic_turn(pairs: int = 20, *, body_chars: int = 400) -> list[ModelMessage]:
    """One user turn followed by ``pairs`` ToolCall/ToolReturn rounds — the failing session shape
    (ADR-0018 §1: 1 prompt + dozens of tool messages, exactly one user-turn boundary at index 0).

    Each tool-return body is ``body_chars`` chars (~``body_chars/4`` est tokens), so the bodies
    together dwarf any small ``keep_recent_tokens`` budget.
    """
    messages: list[ModelMessage] = [_user("do a big research task")]
    for index in range(pairs):
        call_id = f"c{index}"
        messages.append(_tool_call("read", call_id))
        messages.append(_tool_return("read", call_id, "R" * body_chars))
    return messages


def test_split_tail_returns_positive_index_for_a_single_long_agentic_turn():
    # REGRESSION (ADR-0018 §1, root cause 1): a single long agentic turn has exactly one
    # user-turn boundary (index 0), so the OLD snap-back collapsed to 0 = "everything fits" and
    # compaction no-op'd. The redefined Compaction Boundary (any ModelResponse) must find a cut.
    messages = _long_agentic_turn()

    cut = split_tail(messages, keep_recent_tokens=100)

    assert cut > 0


@pytest.mark.parametrize("keep_recent_tokens", [50, 100, 200, 400, 800])
def test_split_tail_cut_lands_on_a_valid_boundary_never_an_orphan(keep_recent_tokens: int):
    # For a spread of budgets, the returned index always lands on a valid Compaction Boundary:
    # messages[cut] is a ModelResponse or a tool-return-free ModelRequest — never a request
    # carrying a ToolReturnPart/RetryPromptPart (which would orphan its matching call).
    messages = _long_agentic_turn()

    cut = split_tail(messages, keep_recent_tokens=keep_recent_tokens)

    if cut < len(messages):
        head = messages[cut]
        assert isinstance(head, ModelResponse) or not _has_tool_return(head)
        assert not _has_tool_return(head)


@pytest.mark.parametrize("keep_recent_tokens", [50, 100, 200, 400, 800])
def test_split_tail_keeps_every_tool_pair_intact_across_the_cut(keep_recent_tokens: int):
    # Every ToolReturnPart in the kept tail has its matching ToolCallPart (same tool_call_id) also
    # in the tail — cutting AT a ModelResponse never splits a call/result pair.
    messages = _long_agentic_turn()

    cut = split_tail(messages, keep_recent_tokens=keep_recent_tokens)
    tail = messages[cut:]

    call_ids = {
        part.tool_call_id
        for message in tail
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    }
    return_ids = {
        part.tool_call_id
        for message in tail
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }
    assert return_ids <= call_ids


def test_split_tail_snaps_back_to_a_model_response_boundary_no_orphan():
    # The naive token cut lands BETWEEN a ToolCallPart and its ToolReturnPart (the call's big args
    # blow the budget). split_tail must snap back to the nearest valid Compaction Boundary — the
    # preceding ModelResponse — so the kept tail keeps the call/result pair intact and never starts
    # on an orphaned tool result (ADR-0018 §1).
    big_args = "x" * 4_000  # ~1000 est tokens — far over the budget below
    messages: list[ModelMessage] = [
        _user("q0"),  # 0  — user-turn boundary
        _tool_call("read", "c0"),  # 1
        _tool_return("read", "c0", "old read output"),  # 2
        _assistant("a0"),  # 3
        _user("q1"),  # 4  — user-turn boundary
        _tool_call("write", "c1", args=big_args),  # 5  — big args break the budget here (a cut)
        _tool_return("write", "c1", "recent write output"),  # 6
        _assistant("a1"),  # 7
    ]

    boundary = split_tail(messages, keep_recent_tokens=100)

    # Snapped back to the nearest ModelResponse (index 5), keeping the write call/result pair whole
    # — not left at the naive cut (index 6, an orphaned return).
    assert boundary == 5
    head = messages[boundary]
    assert isinstance(head, ModelResponse)
    # No orphaned tool result at the head of the kept tail.
    assert not _has_tool_return(head)


def test_split_tail_still_snaps_to_a_user_turn_boundary_when_that_is_nearest():
    # A user-turn ModelRequest remains a valid cut: when the nearest boundary at/below the raw cut
    # is a user turn (no ModelResponse sits between), split_tail lands on it — multi-turn histories
    # behave as before, never coarser.
    big = "z" * 4_000  # ~1000 est tokens
    messages: list[ModelMessage] = [
        _user("q0"),  # 0
        _assistant("a0 " + big),  # 1  — huge, breaks the budget
        _user("q1"),  # 2  — user-turn boundary (the snap target)
        _assistant("a1"),  # 3
    ]

    boundary = split_tail(messages, keep_recent_tokens=100)

    assert boundary == 2
    head = messages[boundary]
    assert isinstance(head, ModelRequest)
    assert any(isinstance(part, UserPromptPart) for part in head.parts)


# estimate_history_tokens (ADR-0018 §4): the public chars≈/4 sum that sizes the tail AND seeds the
# post-compaction gauge — one divisor, no second estimator.


def test_estimate_history_tokens_empty_is_zero():
    # No messages → nothing to occupy the window → 0 (the seed a just-cleared history would read).
    assert estimate_history_tokens([]) == 0


def test_estimate_history_tokens_sums_chars_over_four():
    # Known content → the per-message floor(chars / 4) summed: 40//4 + 24//4 == 10 + 6 == 16.
    messages: list[ModelMessage] = [_user("x" * 40), _assistant("y" * 24)]

    assert estimate_history_tokens(messages) == 16


def test_estimate_history_tokens_shares_the_tail_estimator():
    # Single source of truth: the public sum is exactly the per-message _estimate_tokens split_tail
    # sizes the tail with (one divisor, _CHARS_PER_TOKEN) — never a second, divergent estimator.
    messages: list[ModelMessage] = [
        _user("hello world"),
        _tool_call("read", "c0"),
        _tool_return("read", "c0", "some tool output body"),
        _assistant("a longer assistant reply here"),
    ]

    assert estimate_history_tokens(messages) == sum(
        compaction._estimate_tokens(message) for message in messages
    )


# microcompact


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


def test_microcompact_elides_inside_a_single_long_agentic_turn():
    # REGRESSION (ADR-0018 §1, root cause 1): microcompact shares split_tail, so on a single long
    # turn it used to snap the boundary to 0 and elide nothing. With the redefined Compaction
    # Boundary it now finds a cut inside the turn and blanks the old tool outputs before it.
    messages = _long_agentic_turn()

    new_messages, elided = microcompact(messages, keep_recent_tokens=100)

    assert elided > 0
    boundary = split_tail(messages, keep_recent_tokens=100)
    # Only parts before the boundary were blanked; the kept tail is byte-identical to the input.
    assert new_messages[boundary:] == messages[boundary:]
    # Every blanked tool return sits in the old region (before the boundary).
    for index, message in enumerate(new_messages):
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.content == _MICRO_PLACEHOLDER:
                    assert index < boundary


def test_micro_placeholder_constant():
    # The placeholder is the ADR-0006 §3a wording (asserted so a downstream rename is caught here).
    assert _MICRO_PLACEHOLDER == "[tool output elided by microcompaction]"
    # It is rebuilt via dataclasses.replace, so a blanked part stays a real ToolReturnPart.
    part = ToolReturnPart(tool_name="t", content="real", tool_call_id="c")
    blanked = dataclasses.replace(part, content=_MICRO_PLACEHOLDER)
    assert isinstance(blanked, ToolReturnPart)
    assert blanked.content == _MICRO_PLACEHOLDER
