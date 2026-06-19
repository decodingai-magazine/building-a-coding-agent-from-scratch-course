"""On-exit memory write-back: one cheap summary appended to ``MEMORY.md`` (ADR-0002 §8).

The deliberately minimal M1 memory-write loop. When a session ends, one cheap Gemini call
summarizes the whole conversation into a **single sentence**, which is appended (dated) to the
project-root ``MEMORY.md`` and trimmed back to the configured caps. Next session,
:func:`decode.memory.service.assemble_memory` picks that line up and injects it into the prompt —
so the agent carries a thin thread of memory across sessions.

This is a teaching stepping-stone, not production memory: a forked-agent extractor, topic files,
a recall selector, and real compaction all arrive in **M4**. The one design rule that earns its
keep now is that :func:`summarize_session` — the cheap-summary helper — stays clean and reusable,
because **M4 compaction grows from exactly this seam**.

Three layers, each independently testable:

* :func:`summarize_session` — the one cheap LLM call. Renders the conversation to a plain-text
  transcript and asks a tiny one-shot :class:`~pydantic_ai.Agent` to summarize it in one sentence.
  Returns the sentence, or ``None`` when there is nothing to summarize (empty/trivial conversation)
  or the call fails. Model-agnostic: ``model_or_settings`` is either a concrete
  :class:`~pydantic_ai.models.Model` (tests pass ``TestModel`` / ``FunctionModel`` — no network)
  or the :class:`~decode.config.settings.Settings` from which the production Gemini model is built.
* :func:`append_session_summary` — pure filesystem. Appends a dated bullet to ``cwd/MEMORY.md``
  (the **project root** = the launch ``cwd``; created if absent) and trims the file to
  ``settings.memory_max_lines`` / ``settings.memory_max_bytes``, **keeping the most-recent lines**.
  ``now`` is injected (timezone-aware UTC) so the dated line is deterministic in tests.
* :func:`extract_on_exit` — the shutdown orchestrator. Summarize → if non-``None``, append.
  **Fully non-fatal**: everything is wrapped so it can never raise and never block process exit;
  it no-ops on an empty conversation or a missing ``GEMINI_API_KEY``, and logs at warning on any
  failure.
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

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model

logger = logging.getLogger(__name__)

# The model-maintained memory file the write-back appends to (and that assemble_memory caps).
_MEMORY_FILENAME = "MEMORY.md"

# The one-shot summarizer's instruction. Cheap and blunt on purpose: one sentence, no preamble,
# so the appended line stays a single readable bullet. Reused verbatim by M4 compaction.
_SUMMARIZE_INSTRUCTIONS = (
    "You summarize a finished coding session for a developer's project memory. Read the "
    "transcript below and reply with ONE plain sentence capturing what was worked on or decided "
    "— no preamble, no bullet points, no markdown, just the sentence."
)


def _utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime (the one clock call, patched in tests)."""
    return datetime.now(UTC)


async def summarize_session(
    messages: list[ModelMessage], *, model_or_settings: Model | Settings
) -> str | None:
    """Summarize a session into one sentence with a single cheap LLM call (ADR-0002 §8).

    Renders ``messages`` to a plain-text transcript (user prompts + assistant text) and asks a
    tiny one-shot :class:`~pydantic_ai.Agent` to compress it into one sentence. Returns that
    sentence, or ``None`` when:

    * the conversation is **empty or trivial** (no user/assistant text) — no call is made; or
    * the model returns **blank** text; or
    * the call **fails** for any reason — swallowed and logged, never raised (this helper is
      called on the exit path and must stay non-fatal).

    ``model_or_settings`` decouples the helper from a provider: pass a concrete
    :class:`~pydantic_ai.models.Model` (tests use ``TestModel`` / ``FunctionModel``, so CI makes
    no network call) or the :class:`~decode.config.settings.Settings` from which the Gemini model
    is built for production. This is the reusable cheap-summary seam M4 compaction grows from.
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
    """Append a dated summary line to ``cwd/MEMORY.md`` and trim it to the caps (ADR-0002 §8).

    "Project root" is the launch ``cwd``: the file written is ``cwd/MEMORY.md`` (created if
    absent). The summary is appended as a dated bullet — ``- {YYYY-MM-DD}: {summary}`` using the
    UTC date from ``now`` — then the whole file is trimmed to ``settings.memory_max_lines`` lines
    AND ``settings.memory_max_bytes`` bytes, **keeping the most-recent lines** (the oldest are
    dropped first), so the model-maintained file can never grow without bound.

    ``now`` is injected and **must be timezone-aware** (UTC) — a naive datetime is rejected, the
    same boundary rule the rest of the package follows. Filesystem reads/writes are local and the
    tool layer is sequential in v1 (ADR-0002 §7,10), so this stays sync.
    """
    if now.tzinfo is None:
        raise ValueError("now must be a timezone-aware (UTC) datetime, not naive")

    memory = cwd / _MEMORY_FILENAME
    existing = memory.read_text(encoding="utf-8") if memory.is_file() else ""

    dated_line = f"- {now.astimezone(UTC):%Y-%m-%d}: {summary}"
    lines = [*existing.splitlines(), dated_line]

    trimmed = _trim_keeping_recent(
        lines, max_lines=settings.memory_max_lines, max_bytes=settings.memory_max_bytes
    )
    memory.write_text(trimmed + "\n", encoding="utf-8")


async def extract_on_exit(messages: list[ModelMessage], cwd: Path) -> None:
    """Summarize the session and append it to ``MEMORY.md`` — fully non-fatal (ADR-0002 §8).

    The shutdown orchestrator: summarize the conversation, and if that yields a sentence, append
    it (dated) to the project-root ``MEMORY.md``. It must **never raise and never block process
    exit**, so the whole body is guarded and:

    * **no-ops on an empty conversation** (nothing to summarize); and
    * **no-ops when ``GEMINI_API_KEY`` is unset** (no cheap call to make — a headless / unconfigured
      run leaves no trace); and
    * **logs at warning and swallows** any failure from the summary call or the file write.
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
        logger.debug("wrote session summary to %s", cwd / _MEMORY_FILENAME)
    except Exception:
        logger.warning("memory write-back on exit failed; continuing shutdown", exc_info=True)


def _resolve_model(model_or_settings: Model | Settings) -> Model | GoogleModel:
    """Return a concrete model: pass a ``Model`` through, or build Gemini from ``Settings``.

    Tests inject a ``Model`` (``TestModel`` / ``FunctionModel``) so the summary call makes no
    network request; production passes :data:`~decode.config.settings.settings`, from which the
    same ``google-gla`` Gemini model the factory uses is built (config-driven id + key).
    """
    if isinstance(model_or_settings, Settings):
        provider = GoogleProvider(api_key=model_or_settings.gemini_api_key.get_secret_value())
        return GoogleModel(model_or_settings.gemini_model, provider=provider)
    return model_or_settings


def _render_transcript(messages: list[ModelMessage]) -> str:
    """Render the conversation to a plain-text ``role: text`` transcript (empty if nothing to say).

    Pulls user-prompt text from requests and assistant text from responses (tool calls / results
    carry no readable session content and are skipped). Returns ``""`` when the conversation has
    no text at all — the signal that there is nothing worth summarizing, so no call is made.
    """
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


def _trim_keeping_recent(lines: list[str], *, max_lines: int, max_bytes: int) -> str:
    """Return the **tail** of ``lines`` capped by line count then by byte count (line-aligned).

    The write-back keeps the *most-recent* memory, so trimming drops from the **front**: keep at
    most ``max_lines`` trailing whole lines, then drop further leading whole lines until the UTF-8
    byte length is within ``max_bytes``. If even the last line alone exceeds ``max_bytes`` we keep
    that one whole line — the freshest note is never split away to nothing. Mirrors the
    head-keeping clip in :func:`decode.memory.service._clip_to_budget`, but tail-keeping.
    """
    tail = lines[-max_lines:] if max_lines > 0 else lines[-1:]
    while len(tail) > 1 and len("\n".join(tail).encode("utf-8")) > max_bytes:
        tail = tail[1:]
    return "\n".join(tail)
