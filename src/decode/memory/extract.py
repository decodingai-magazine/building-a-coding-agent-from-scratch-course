"""On-exit memory write-back: one cheap summary appended to ``MEMORY.md`` (ADR-0002 §8).

On session end one cheap LLM call (:func:`summarize_session`) compresses the conversation into
a single sentence, which :func:`append_session_summary` appends (dated) to
``cwd/.decode/MEMORY.md`` and trims to the configured caps; the next session injects it back
via :func:`decode.memory.service.assemble_memory`. :func:`compress_memory_file` is the second
level (ADR-0006 §8): at the line cap one LLM call dedupes the bullets, drop-oldest remaining
the guaranteed fallback. :func:`extract_on_exit` orchestrates on shutdown, fully non-fatal.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from decode.config.settings import Settings, settings
from decode.memory.files import harness_memory_path
from decode.memory.service import clip_lines_to_budget

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model

logger = logging.getLogger(__name__)

# One-shot summarizer instruction: one sentence, no preamble — a single readable bullet.
_SUMMARIZE_INSTRUCTIONS = (
    "You summarize a finished coding session for a developer's project memory. Read the "
    "transcript below and reply with ONE plain sentence capturing what was worked on or decided "
    "— no preamble, no bullet points, no markdown, just the sentence."
)

# Second-level compression instruction (ADR-0006 §8): rewrite the capped MEMORY.md shorter,
# keeping the dated-bullet form.
_COMPRESS_INSTRUCTIONS = (
    "You compress a developer's project memory file. It is a list of dated bullet lines "
    "(`- YYYY-MM-DD: ...`), each recording what a past coding session worked on or decided, and it "
    "has grown to its size cap. Rewrite it SHORTER and higher-signal: merge duplicate or "
    "superseded notes, keep durable facts and decisions, drop one-off/ephemeral chatter, and "
    "preserve the dated-bullet form (one `- YYYY-MM-DD: ...` fact per line, oldest first). Aim well "
    "under the original length. Reply with ONLY the rewritten bullet lines — no preamble, no "
    "headings, no commentary, no markdown fences."
)


def _utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime (the one clock call, patched in tests)."""
    return datetime.now(UTC)


async def summarize_session(
    messages: list[ModelMessage], *, model_or_settings: Model | Settings
) -> str | None:
    """Summarize a session into one sentence with a single cheap LLM call (ADR-0002 §8).

    Returns the sentence, or ``None`` on an empty/trivial conversation (no call made), a blank
    result, or a failed call (logged, never raised — this runs on the exit path).
    ``model_or_settings``: a concrete :class:`~pydantic_ai.models.Model` (tests — no network)
    or :class:`~decode.config.settings.Settings` (production Gemini).
    """
    transcript = _render_transcript(messages)
    if not transcript:
        return None

    model = _resolve_model(model_or_settings)
    agent: Agent[None, str] = Agent(model, instructions=_SUMMARIZE_INSTRUCTIONS)
    try:
        result = await agent.run(transcript)
    except Exception:
        logger.warning("session summary call failed; skipping memory write-back", exc_info=True)
        return None

    summary = result.output.strip()
    return summary or None


def append_session_summary(cwd: Path, summary: str, *, now: datetime) -> None:
    """Append a dated ``- YYYY-MM-DD: …`` bullet to ``cwd/.decode/MEMORY.md`` and trim to the caps.

    The file (and its ``.decode/`` parent) is created if absent; trimming keeps the
    most-recent lines within ``settings.memory_max_lines`` / ``settings.memory_max_bytes``.
    ``now`` is injected and must be timezone-aware UTC — naive is rejected (ADR-0002 §8).
    """
    if now.tzinfo is None:
        raise ValueError("now must be a timezone-aware (UTC) datetime, not naive")

    memory = harness_memory_path(cwd)
    memory.parent.mkdir(parents=True, exist_ok=True)
    existing = memory.read_text(encoding="utf-8") if memory.is_file() else ""

    dated_line = f"- {now.astimezone(UTC):%Y-%m-%d}: {summary}"
    lines = [*existing.splitlines(), dated_line]

    trimmed = clip_lines_to_budget(
        lines,
        max_lines=settings.memory_max_lines,
        max_bytes=settings.memory_max_bytes,
        keep="tail",
    )
    memory.write_text(trimmed + "\n", encoding="utf-8")


async def compress_memory_file(cwd: Path, *, model_or_settings: Model | Settings) -> bool:
    """Compress ``MEMORY.md`` at the line cap with one cheap LLM call — the second level (ADR-0006 §8).

    Below ``settings.memory_max_lines`` (or with no file): ``False``, no call, file untouched.
    At the cap: one call dedupes the bullets; a non-blank result is written back and
    hard-clamped by :func:`~decode.memory.service.clip_lines_to_budget` (``True``); a failed or
    blank call keeps the drop-oldest file (``False``). Fully non-fatal — never raises, and
    drop-oldest remains the guaranteed fallback, so the cap is always enforced.
    """
    try:
        memory = harness_memory_path(cwd)
        if not memory.is_file():
            return False
        existing = memory.read_text(encoding="utf-8")
        if len(existing.splitlines()) < settings.memory_max_lines:
            return False  # not at the cap yet — no call, file untouched

        model = _resolve_model(model_or_settings)
        agent: Agent[None, str] = Agent(model, instructions=_COMPRESS_INSTRUCTIONS)
        result = await agent.run(existing)
        compressed = result.output.strip()
        if not compressed:
            return False  # blank result — keep the drop-oldest file (fallback)

        clamped = clip_lines_to_budget(
            compressed.splitlines(),
            max_lines=settings.memory_max_lines,
            max_bytes=settings.memory_max_bytes,
            keep="tail",
        )
        memory.write_text(clamped + "\n", encoding="utf-8")
        return True
    except Exception:
        logger.warning(
            "memory compression failed; keeping the drop-oldest MEMORY.md", exc_info=True
        )
        return False


async def extract_on_exit(messages: list[ModelMessage], cwd: Path) -> None:
    """Summarize the session and append it to ``MEMORY.md`` — fully non-fatal (ADR-0002 §8).

    The shutdown orchestrator: summarize → append → (if
    ``settings.memory_compression_enabled``) :func:`compress_memory_file`. Never raises and
    never blocks process exit: no-ops on an empty conversation or a missing
    ``GEMINI_API_KEY``, and logs at warning and swallows any failure.
    """
    try:
        if not messages:
            return
        if not settings.gemini_api_key.get_secret_value():
            logger.debug("no GEMINI_API_KEY set; skipping memory write-back on exit")
            return

        summary = await summarize_session(messages, model_or_settings=settings)
        if summary is None:
            return
        append_session_summary(cwd, summary, now=_utc_now())
        logger.debug("wrote session summary to %s", harness_memory_path(cwd))

        if settings.memory_compression_enabled:
            await compress_memory_file(cwd, model_or_settings=settings)
    except Exception:
        logger.warning("memory write-back on exit failed; continuing shutdown", exc_info=True)


def _resolve_model(model_or_settings: Model | Settings) -> Model | GoogleModel:
    """Pass a ``Model`` through, or build the config-driven Gemini model from ``Settings``."""
    if isinstance(model_or_settings, Settings):
        provider = GoogleProvider(api_key=model_or_settings.gemini_api_key.get_secret_value())
        return GoogleModel(model_or_settings.gemini_model, provider=provider)
    return model_or_settings


def _render_transcript(messages: list[ModelMessage]) -> str:
    """Role-prefixed plain-text transcript of user/assistant text; ``""`` when there is none."""
    lines: list[str] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    text = part.content.strip()
                    if text:
                        lines.append(f"User: {text}")
        elif isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, TextPart) and part.content.strip():
                    lines.append(f"Assistant: {part.content.strip()}")
    return "\n".join(lines)
