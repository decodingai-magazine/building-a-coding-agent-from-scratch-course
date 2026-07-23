"""Conversation compaction core — the pure, network-free primitives. See ADR-0006 §3-5.

Two tiers share these: the window-relative trigger (:func:`reserve_threshold` +
:func:`should_compact`), full LLM compaction (:func:`summarize_for_compaction`,
:func:`build_summary_message`, :func:`split_tail`), and the no-LLM :func:`microcompact`.
The tail cut snaps back to a Compaction Boundary — a ``ModelResponse`` or a tool-return-free
``ModelRequest`` — so a tool-call/result pair is never split, and a single long agentic turn
still finds a cut (ADR-0018 §1 amends ADR-0006 §5).
Microcompaction is in-memory only — the JSONL log keeps full fidelity (ADR-0006 §3a).
The coarse ``chars≈/4`` estimate (:func:`estimate_history_tokens`) sizes the tail AND seeds the
post-compaction gauge; it never INFLATES the trigger — the trigger reads provider-authoritative
usage, and post-compaction the estimate can only understate, briefly, until the next leg reports
(ADR-0018 §4 softens ADR-0006's stronger "the estimate never drives the trigger").

The summarizer rides the existing Provider Seam (ADR-0018 §5): :func:`summarize_for_compaction`
takes a built :class:`~pydantic_ai.models.Model` — the ACTIVE provider's model, wired in at
``tui/app.py`` — so compaction works on gemini, openrouter, AND modal. This module builds no
provider model itself (the old ``Settings → GoogleModel`` branch is deleted); it stays network-free
and provider-agnostic, and the Model-instance argument doubles as the tests' no-network seam.
"""

from __future__ import annotations

import dataclasses
import enum
import logging
from typing import TYPE_CHECKING

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from pydantic_ai.usage import RunUsage

logger = logging.getLogger(__name__)


class CompactOutcome(enum.Enum):
    """The three-valued result of a full compaction — the Compaction Outcome (ADR-0018 §3).

    Replaces ``compact()``'s ambiguous bool: a bare ``False`` could not tell "there was nothing to
    compact" apart from "the summarizer call failed". ``/compact`` prints a distinct line per
    outcome (``SUMMARIZER_FAILED`` names ``.decode/logs/decode.log``; ``COMPACTED`` stays
    event-rendered) and the auto path logs one INFO breadcrumb when a trigger fired but did not
    land.
    """

    COMPACTED = "compacted"
    NOTHING_TO_COMPACT = "nothing_to_compact"
    SUMMARIZER_FAILED = "summarizer_failed"


# Microcompaction placeholder: blanks content only — the part stays real, never orphaned.
_MICRO_PLACEHOLDER = "[tool output elided by microcompaction]"

# Coarse chars→tokens divisor — tail sizing + the post-compaction gauge seed; never INFLATES the
# trigger, which reads provider-authoritative usage (ADR-0018 §4 softens ADR-0006 §Consequences).
_CHARS_PER_TOKEN = 4

# Full-tier summarizer instruction: fills the fixed skeleton (ADR-0006 §4).
_COMPACTION_INSTRUCTIONS = (
    "You compact an in-progress coding session so the assistant can keep working after the older "
    "turns are dropped. Read the transcript below and reply with ONLY this Markdown skeleton — "
    'every heading present, in this exact order, filled from the transcript (write "None" under '
    "any heading that does not apply). No preamble, no extra headings.\n\n"
    "# Conversation summary\n\n"
    "## Goal\n\n"
    "## Constraints & Preferences\n\n"
    "## Progress\n"
    "(Done / In Progress / Blocked)\n\n"
    "## Key Decisions\n\n"
    "## Next Steps\n\n"
    "## Critical Context\n"
)


def reserve_threshold(window: int, reserve: float) -> int:
    """The token level a tier fires at: ``int(window * (1 - reserve))`` (floor, ADR-0006 §3).

    ``window`` must be positive; ``reserve`` (the per-tier fraction kept free) must lie in
    ``[0, 1]``.
    """
    if window <= 0:
        raise ValueError(f"window must be a positive token count, got {window}")
    if not 0 <= reserve <= 1:
        raise ValueError(f"reserve must be within [0, 1], got {reserve}")
    return int(window * (1 - reserve))


def should_compact(usage: RunUsage, *, window: int, reserve: float, enabled: bool) -> bool:
    """Whether a tier should fire this turn — the window-relative predicate shared by both tiers.

    ``True`` only when ``enabled`` AND usage is populated (``input_tokens > 0``) AND
    ``input_tokens >= reserve_threshold(window, reserve)``. ``input_tokens == 0`` is the safe
    "don't fire" fallback for unpopulated usage (ADR-0006 §3).
    """
    if not enabled:
        return False
    if usage.input_tokens <= 0:
        return False
    return usage.input_tokens >= reserve_threshold(window, reserve)


async def summarize_for_compaction(messages: list[ModelMessage], *, model: Model) -> str | None:
    """Summarize older history into the fixed skeleton with one cheap LLM call (ADR-0006 §4).

    Returns the filled skeleton, or ``None`` on an empty/trivial transcript (no call made), a
    blank result, or a failed call (logged at warning, never raised — degrades to "no
    compaction this turn"). ``model`` is a built :class:`~pydantic_ai.models.Model` — in production
    the ACTIVE provider's model wired in at the call site so compaction rides the Provider Seam
    (ADR-0018 §5), in tests a ``FunctionModel`` / ``TestModel`` (no network).
    """
    transcript = _render_transcript(messages)
    if not transcript:
        return None

    agent: Agent[None, str] = Agent(model, instructions=_COMPACTION_INSTRUCTIONS)
    try:
        result = await agent.run(transcript)
    except Exception:
        logger.warning("compaction summary call failed; skipping full compaction", exc_info=True)
        return None

    summary = result.output.strip()
    return summary or None


def build_summary_message(skeleton: str) -> ModelRequest:
    """Wrap the filled skeleton in a synthetic head ``ModelRequest`` (ADR-0006 §4).

    Element 0 of the post-compaction ``[summary_message, *tail]`` history — as the head it
    makes successive full compactions merge for free.
    """
    framed = f"Summary of the earlier conversation (older turns were compacted):\n\n{skeleton}"
    return ModelRequest(parts=[UserPromptPart(content=framed)])


def split_tail(messages: list[ModelMessage], *, keep_recent_tokens: int) -> int:
    """Index where the kept recent tail begins — the largest tail fitting ``keep_recent_tokens``,
    snapped back to the nearest Compaction Boundary at or below the raw budget cut (ADR-0018 §1,
    amends ADR-0006 §5).

    A Compaction Boundary (see :func:`_is_compaction_boundary`) is a ``ModelResponse`` or a
    ``ModelRequest`` carrying no ``ToolReturnPart`` / ``RetryPromptPart``. The snap-back
    guarantees the tail never starts on an orphaned tool return — a tool-call/result pair is
    never split (a return's matching call sits in the immediately preceding ``ModelResponse``,
    so cutting AT a ``ModelResponse`` keeps the pair intact). It may keep slightly more than the
    raw budget. Unlike the old user-turn-only snap, a single long agentic turn (one user prompt
    + dozens of tool rounds) now finds a cut instead of collapsing to 0.

    Returns ``0`` when everything fits (or the nearest valid boundary genuinely is 0) and
    ``len(messages)`` when nothing fits. The ``chars≈/4`` estimate sizes the tail (and seeds the
    post-compaction gauge, :func:`estimate_history_tokens`); it never INFLATES the trigger
    (ADR-0018 §4).
    """
    if not messages:
        return 0

    # Largest budget-fitting tail, walking from the end.
    total = 0
    cut = len(messages)
    for index in range(len(messages) - 1, -1, -1):
        estimate = _estimate_tokens(messages[index])
        if total + estimate > keep_recent_tokens:
            break
        total += estimate
        cut = index

    if cut == 0:
        return 0  # everything fits
    if cut >= len(messages):
        return len(messages)  # nothing fits — keep only the summary

    # Snap the cut back to the nearest Compaction Boundary so the tail never starts on an orphaned
    # tool return. message[0] is normally a user request (a boundary), so this terminates at 0 at
    # worst — and only returns 0 when that nearest boundary genuinely is index 0.
    for index in range(cut, -1, -1):
        if _is_compaction_boundary(messages[index]):
            return index
    return 0  # no boundary found → keep everything (safe degradation, never orphan)


def microcompact(
    messages: list[ModelMessage],
    *,
    keep_recent_tokens: int,
    placeholder: str = _MICRO_PLACEHOLDER,
) -> tuple[list[ModelMessage], int]:
    """Blank old tool-output bodies in-memory — the no-LLM tier (ADR-0006 §3a).

    Rebuilds old ``ToolReturnPart`` / ``RetryPromptPart`` content via
    :func:`dataclasses.replace` — never mutating the caller's objects, never removing a
    message or part, so it can never orphan a tool-call/result pair. Idempotent: a part
    already holding the placeholder is left as-is and not counted. Returns
    ``(new_messages, elided_count)``; a no-op returns the input list unchanged as
    ``(messages, 0)``.
    """
    boundary = split_tail(messages, keep_recent_tokens=keep_recent_tokens)

    new_messages: list[ModelMessage] = []
    elided = 0
    for index, message in enumerate(messages):
        if index >= boundary or not isinstance(message, ModelRequest):
            new_messages.append(message)
            continue

        new_parts = list(message.parts)
        changed = False
        for position, part in enumerate(message.parts):
            if not isinstance(part, ToolReturnPart | RetryPromptPart):
                continue
            if part.content == placeholder:
                continue  # already elided — idempotent, uncounted
            new_parts[position] = dataclasses.replace(part, content=placeholder)
            elided += 1
            changed = True

        new_messages.append(dataclasses.replace(message, parts=new_parts) if changed else message)

    if elided == 0:
        return messages, 0
    return new_messages, elided


def _render_transcript(messages: list[ModelMessage]) -> str:
    """Role-prefixed plain-text transcript with brief tool-activity notes (bodies dropped).

    Returns ``""`` when there is nothing worth summarizing.
    """
    lines: list[str] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    text = part.content.strip()
                    if text:
                        lines.append(f"User: {text}")
                elif isinstance(part, ToolReturnPart):
                    lines.append(f"[tool result: {part.tool_name}]")
                elif isinstance(part, RetryPromptPart) and part.tool_name:
                    lines.append(f"[tool retry: {part.tool_name}]")
        elif isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, TextPart) and part.content.strip():
                    lines.append(f"Assistant: {part.content.strip()}")
                elif isinstance(part, ToolCallPart):
                    lines.append(f"[tool call: {part.tool_name}]")
    return "\n".join(lines)


def estimate_history_tokens(messages: list[ModelMessage]) -> int:
    """Coarse ``chars≈/4`` token estimate of a whole history — the sum over messages of the same
    per-message :func:`_estimate_tokens` that :func:`split_tail` sizes the tail with (ONE divisor,
    no second estimator). Empty → ``0``.

    Used both to size the recent tail and to seed the post-compaction gauge, so the footer drops to
    the same currency the cut is made in the instant a compaction lands (ADR-0018 §4). This is why
    the estimate now touches the gauge at all: post-compaction it can only understate real occupancy
    (the divisor is coarse and the next leg's provider number overwrites it), so it never INFLATES
    the trigger — ADR-0018 §4 softens ADR-0006's stronger "the estimate never drives the trigger".
    """
    return sum(_estimate_tokens(message) for message in messages)


def _estimate_tokens(message: ModelMessage) -> int:
    """Coarse ``chars≈/4`` token estimate for one message (tail sizing + the post-compaction gauge
    seed via :func:`estimate_history_tokens`; never INFLATES the trigger — ADR-0018 §4)."""
    chars = sum(_part_chars(part) for part in message.parts)
    return chars // _CHARS_PER_TOKEN


def _part_chars(part: object) -> int:
    content = getattr(part, "content", None)
    if content is not None:
        return len(content) if isinstance(content, str) else len(str(content))
    args = getattr(part, "args", None)
    if args is not None:
        return len(args) if isinstance(args, str) else len(str(args))
    return 0


def _is_compaction_boundary(message: ModelMessage) -> bool:
    """Whether ``message`` is a valid Compaction Boundary — a cut point (ADR-0018 §1).

    Valid at a ``ModelResponse``, or at a ``ModelRequest`` carrying no ``ToolReturnPart`` /
    ``RetryPromptPart`` (a user-turn or summary-head request). Never valid at a request carrying
    a tool return or retry: its matching call sits in the immediately preceding ``ModelResponse``,
    so cutting AT that response keeps every call/result pair intact. This subsumes the old
    user-turn-only boundary — every user-turn request is still a valid cut.
    """
    if isinstance(message, ModelResponse):
        return True
    if isinstance(message, ModelRequest):
        return not any(isinstance(part, ToolReturnPart | RetryPromptPart) for part in message.parts)
    return False
