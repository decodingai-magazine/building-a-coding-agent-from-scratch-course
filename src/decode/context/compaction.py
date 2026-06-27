"""Conversation compaction core — the pure, network-free primitives (ADR-0006 §3-5).

This module owns only the **math and the message surgery**; the per-turn cascade and ``/compact``
that orchestrate it land in tasks 044/045. Two tiers share these primitives:

* **Window-relative trigger** — :func:`reserve_threshold` turns a window + reserve fraction into the
  token level a tier fires at, and :func:`should_compact` is the single predicate both tiers use
  (the handler passes the full reserve for full compaction and the larger micro reserve for
  microcompaction). ``input_tokens == 0`` (unpopulated usage) is the safe "don't fire" fallback.
* **Full compaction (LLM)** — :func:`summarize_for_compaction` makes one cheap call that fills the
  fixed Markdown skeleton (ADR-0006 §4), :func:`build_summary_message` wraps the filled skeleton in
  a synthetic head ``ModelRequest``, and :func:`split_tail` picks the recent verbatim tail, snapped
  back to a user-turn boundary so a tool-call/result pair is never split.
* **Microcompaction (no LLM)** — :func:`microcompact` reuses :func:`split_tail` to delimit "old,"
  then blanks each old ``ToolReturnPart`` / ``RetryPromptPart`` body with a placeholder via
  :func:`dataclasses.replace` (never mutating the caller's objects, never removing a part). It is
  idempotent and in-memory only — the JSONL log keeps full fidelity (ADR-0006 §3a).

The full summarizer is NEW and fuller than :func:`decode.memory.extract.summarize_session` (which
stays as-is for memory write-back), but reuses its ``_resolve_model`` pattern and transcript style,
so tests drive every LLM call with ``FunctionModel`` / ``TestModel`` and CI never touches the
network. **Tail sizing uses a coarse ``chars≈/4`` estimate — for the keep-recent cut and the micro
"old" boundary ONLY, never the trigger** (the trigger reads provider-authoritative usage).
"""

from __future__ import annotations

import dataclasses
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
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from decode.config.settings import Settings

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from pydantic_ai.usage import RunUsage

logger = logging.getLogger(__name__)

# The placeholder microcompaction writes over an old tool-output body (ADR-0006 §3a). It only
# blanks content — the part stays a real ToolReturnPart/RetryPromptPart — so a tool-call/result
# pair is never orphaned.
_MICRO_PLACEHOLDER = "[tool output elided by microcompaction]"

# Coarse bytes→tokens divisor for the tail estimate (chars≈/4). Tail sizing ONLY — never the
# trigger, which reads provider-authoritative usage (ADR-0006 §Consequences).
_CHARS_PER_TOKEN = 4

# The full-tier summarizer's instruction. Fuller than the one-sentence memory summary: it asks for
# the fixed skeleton (ADR-0006 §4) so a compacted conversation keeps a predictable, machine-stable
# shape the model can read back as context.
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

    ``window`` is the active model's input context window (a single configurable number, not a
    per-model table); ``reserve`` is the per-tier fraction kept free, so a tier fires when usage
    reaches ``window * (1 - reserve)``. Guards: ``window`` must be positive and ``reserve`` must
    lie in ``[0, 1]`` (``0`` reserves nothing → fires at the full window; ``1`` reserves
    everything → fires at ``0``).
    """
    if window <= 0:
        raise ValueError(f"window must be a positive token count, got {window}")
    if not 0 <= reserve <= 1:
        raise ValueError(f"reserve must be within [0, 1], got {reserve}")
    return int(window * (1 - reserve))


def should_compact(usage: RunUsage, *, window: int, reserve: float, enabled: bool) -> bool:
    """Whether a tier should fire this turn — the window-relative predicate shared by both tiers.

    Returns ``True`` only when ``enabled`` AND usage is populated (``input_tokens > 0``) AND
    ``input_tokens >= reserve_threshold(window, reserve)``. The handler calls this once per tier:
    the full reserve for full compaction and the larger micro reserve for microcompaction.

    **Safe fallback:** ``input_tokens == 0`` means usage was not populated this turn, so no window
    math is done and no tier fires (ADR-0006 §3) — better to skip a compaction than to fire on a
    bogus zero.
    """
    if not enabled:
        return False
    if usage.input_tokens <= 0:
        return False
    return usage.input_tokens >= reserve_threshold(window, reserve)


async def summarize_for_compaction(
    messages: list[ModelMessage], *, model_or_settings: Model | Settings
) -> str | None:
    """Summarize older history into the fixed skeleton with one cheap LLM call (ADR-0006 §4).

    Renders ``messages`` to a plain-text transcript (the :mod:`decode.memory.extract` role-prefixed
    style, plus a brief note of each tool call/result so the summary reflects what actually ran) and
    asks a one-shot :class:`~pydantic_ai.Agent` to fill the skeleton:
    ``# Conversation summary`` → ``## Goal`` → ``## Constraints & Preferences`` → ``## Progress``
    (Done / In Progress / Blocked) → ``## Key Decisions`` → ``## Next Steps`` → ``## Critical
    Context``. Returns the filled skeleton, or ``None`` when:

    * the conversation is **empty or trivial** (no transcript) — no call is made; or
    * the model returns **blank** text; or
    * the call **fails** for any reason — swallowed and logged at warning, never raised, so full
      compaction degrades safely to "no compaction this turn".

    ``model_or_settings`` decouples the helper from a provider exactly like
    :func:`decode.memory.extract.summarize_session`: pass a concrete
    :class:`~pydantic_ai.models.Model` (tests use ``TestModel`` / ``FunctionModel`` — no network) or
    the :class:`~decode.config.settings.Settings` from which the Gemini model is built for prod.
    """
    transcript = _render_transcript(messages)
    if not transcript:
        return None

    model = _resolve_model(model_or_settings)
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

    Post-compaction history is ``[summary_message, *tail]``; this builds the ``summary_message`` as a
    ``ModelRequest`` carrying a single ``UserPromptPart`` (the shape ``_append_steering`` builds),
    framed so the model reads the skeleton as a summary of the earlier, now-compacted conversation
    rather than a fresh user instruction. As element 0 it makes successive full compactions merge
    for free — no merge logic.
    """
    framed = f"Summary of the earlier conversation (older turns were compacted):\n\n{skeleton}"
    return ModelRequest(parts=[UserPromptPart(content=framed)])


def split_tail(messages: list[ModelMessage], *, keep_recent_tokens: int) -> int:
    """Index where the kept recent tail begins — the largest tail fitting ``keep_recent_tokens``,
    snapped back to a user-turn boundary (ADR-0006 §5).

    Walks back from the end accumulating a coarse ``chars≈/4`` per-message estimate until the next
    message would blow ``keep_recent_tokens``, then **snaps the cut back** to the nearest enclosing
    user-turn ``ModelRequest`` (one carrying a ``UserPromptPart``). That guarantees the tail starts
    at a real turn boundary and never at an orphaned ``ToolReturnPart`` / ``RetryPromptPart`` — a
    tool-call/result pair is never split. The estimate is **tail sizing only**, never the trigger.

    Returns ``0`` when the whole history fits (keep everything) and ``len(messages)`` when nothing
    fits (keep only the summary). When the budget cut lands mid-turn, snapping back may keep
    slightly more than the raw budget — the documented, bounded trade-off for never orphaning a
    tool result.
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

    # Snap the cut back to the enclosing user-turn boundary so the tail never starts on an
    # orphaned tool result. message[0] is normally a user request, so this terminates at 0 at worst.
    for index in range(cut, -1, -1):
        if _is_user_turn_boundary(messages[index]):
            return index
    return 0  # no boundary found → keep everything (safe degradation, never orphan)


def microcompact(
    messages: list[ModelMessage],
    *,
    keep_recent_tokens: int,
    placeholder: str = _MICRO_PLACEHOLDER,
) -> tuple[list[ModelMessage], int]:
    """Blank old tool-output bodies in-memory — the no-LLM tier (ADR-0006 §3a).

    Reuses :func:`split_tail` to delimit "old" (everything before the kept recent tail), then for
    each old message rebuilds every ``ToolReturnPart`` / ``RetryPromptPart`` with its ``content``
    replaced by ``placeholder`` via :func:`dataclasses.replace` (and rebuilds the enclosing message
    the same way) — **never mutating the caller's objects**. It only blanks content, never removes a
    message or part, so it can never orphan a tool-call/result pair. **Idempotent:** a part already
    holding the placeholder is left as-is and not counted.

    Returns ``(new_messages, elided_count)``; when nothing is elided it returns the **input list
    unchanged** as ``(messages, 0)`` so callers can cheaply detect a no-op.
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


def _resolve_model(model_or_settings: Model | Settings) -> Model | GoogleModel:
    """Return a concrete model: pass a ``Model`` through, or build Gemini from ``Settings``.

    Mirrors :func:`decode.memory.extract._resolve_model`: tests inject a ``Model``
    (``TestModel`` / ``FunctionModel``) so the summary call makes no network request; production
    passes :class:`~decode.config.settings.Settings`, from which the same ``google-gla`` Gemini
    model the factory uses is built (config-driven id + key).
    """
    if isinstance(model_or_settings, Settings):
        provider = GoogleProvider(api_key=model_or_settings.gemini_api_key.get_secret_value())
        return GoogleModel(model_or_settings.gemini_model, provider=provider)
    return model_or_settings


def _render_transcript(messages: list[ModelMessage]) -> str:
    """Render the conversation to a plain-text transcript (empty when there is nothing to say).

    Reuses the :mod:`decode.memory.extract` role-prefixed style (``User:`` / ``Assistant:``) and
    adds a **brief note of tool activity** (``[tool call: name]`` / ``[tool result: name]`` /
    ``[tool retry: name]``) — the bodies are dropped (they are what compaction is shedding), but the
    fact that a tool ran is signal the summary should reflect. Returns ``""`` when the conversation
    carries no text or tool activity at all — the signal that there is nothing worth summarizing.
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


def _estimate_tokens(message: ModelMessage) -> int:
    """Coarse ``chars≈/4`` token estimate for one message (tail sizing ONLY, never the trigger).

    Sums the character length of each part's textual content (tool-call ``args`` count too) and
    divides by :data:`_CHARS_PER_TOKEN`. pydantic-ai exposes only aggregate per-leg usage, so the
    recent-tail cut leans on this bounded approximation (ADR-0006 §Consequences).
    """
    chars = sum(_part_chars(part) for part in message.parts)
    return chars // _CHARS_PER_TOKEN


def _part_chars(part: object) -> int:
    """Character length of a part's textual payload (its ``content``, or a tool call's ``args``)."""
    content = getattr(part, "content", None)
    if content is not None:
        return len(content) if isinstance(content, str) else len(str(content))
    args = getattr(part, "args", None)
    if args is not None:
        return len(args) if isinstance(args, str) else len(str(args))
    return 0


def _is_user_turn_boundary(message: ModelMessage) -> bool:
    """Whether ``message`` starts a user turn — a ``ModelRequest`` carrying a ``UserPromptPart``."""
    return isinstance(message, ModelRequest) and any(
        isinstance(part, UserPromptPart) for part in message.parts
    )
