"""Unit tests for :mod:`decode.memory.extract` — the on-exit memory write-back (ADR-0002 §8).

The extractor is the deliberately minimal M1 write-back: one cheap LLM call summarizes the
session into a single sentence, which is appended (dated) to the project-root ``MEMORY.md`` and
trimmed to the configured caps. It is the seam M4 compaction grows from, so the cheap-summary
helper (:func:`~decode.memory.extract.summarize_session`) stays clean and reusable.

Three layers are exercised independently:

* :func:`summarize_session` — the one cheap LLM call. Driven by ``TestModel`` /
  ``FunctionModel`` (no network): returns the model's sentence, returns ``None`` on an
  empty/trivial conversation (no call made), and returns ``None`` when the call fails.
* :func:`append_session_summary` — pure filesystem. Creates ``cwd/.decode/MEMORY.md`` (and its
  ``.decode/`` parent) if absent, appends a dated line, and trims the file to
  ``settings.memory_max_lines`` / ``settings.memory_max_bytes`` keeping the most-recent lines.
  ``now`` is injected (timezone-aware UTC) for determinism.
* :func:`extract_on_exit` — the orchestrator. Fully non-fatal: it must never raise, even when the
  summarizer blows up, so it can never block process exit.

A final round-trip proves a written summary is discoverable by
:func:`decode.memory.service.assemble_memory` on the next session.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from decode.config.settings import settings
from decode.memory import extract
from decode.memory.extract import (
    append_session_summary,
    compress_memory_file,
    extract_on_exit,
    summarize_session,
)
from decode.memory.service import assemble_memory

_NOW = datetime(2026, 6, 19, 12, 30, tzinfo=UTC)


def _memory(cwd: Path) -> Path:
    """The harness MEMORY.md path under ``cwd`` (Fix 1: ``cwd/.decode/MEMORY.md``)."""
    return cwd / ".decode" / "MEMORY.md"


def _conversation(user: str, assistant: str) -> list[ModelMessage]:
    """A minimal two-message conversation: one user prompt, one assistant text reply."""
    return [
        ModelRequest(parts=[UserPromptPart(content=user)]),
        ModelResponse(parts=[TextPart(content=assistant)]),
    ]


# --------------------------------------------------------------------------------------------
# summarize_session — the one cheap LLM call (driven by TestModel / FunctionModel, no network)
# --------------------------------------------------------------------------------------------


async def test_summarize_session_returns_the_model_sentence():
    # A real (non-trivial) conversation: the cheap call returns the model's one-sentence summary.
    messages = _conversation("add pagination to the users endpoint", "done, added a limit param")
    model = TestModel(custom_output_text="Added pagination to the users endpoint.")

    summary = await summarize_session(messages, model_or_settings=model)

    assert summary == "Added pagination to the users endpoint."


async def test_summarize_session_feeds_the_conversation_to_the_model():
    # The transcript the model summarizes must carry the actual conversation content, so the
    # summary is about *this* session and not a blank prompt.
    seen: list[str] = []

    async def capture(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt_parts = [
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart) and isinstance(part.content, str)
        ]
        seen.append("\n".join(prompt_parts))
        return ModelResponse(parts=[TextPart(content="a one sentence summary")])

    messages = _conversation("rename the widget module", "renamed it to gadget")
    await summarize_session(messages, model_or_settings=FunctionModel(capture))

    transcript = "\n".join(seen)
    assert "rename the widget module" in transcript
    assert "renamed it to gadget" in transcript


async def test_summarize_session_returns_none_on_empty_conversation():
    # Nothing to summarize: no model call is made and None is returned (caller writes nothing).
    called = False

    async def must_not_run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal called
        called = True
        return ModelResponse(parts=[TextPart(content="should not happen")])

    summary = await summarize_session([], model_or_settings=FunctionModel(must_not_run))

    assert summary is None
    assert called is False


async def test_summarize_session_returns_none_on_trivial_conversation():
    # A conversation with no actual text content (no user/assistant words) is trivial → None.
    trivial = [ModelRequest(parts=[UserPromptPart(content="   ")])]
    called = False

    async def must_not_run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal called
        called = True
        return ModelResponse(parts=[TextPart(content="should not happen")])

    summary = await summarize_session(trivial, model_or_settings=FunctionModel(must_not_run))

    assert summary is None
    assert called is False


async def test_summarize_session_returns_none_when_the_call_fails():
    # A failing model call must be swallowed and reported as None (non-fatal), not raised.
    async def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("model exploded")

    messages = _conversation("do the thing", "did the thing")
    summary = await summarize_session(messages, model_or_settings=FunctionModel(boom))

    assert summary is None


async def test_summarize_session_returns_none_when_the_model_returns_blank():
    # An empty/whitespace summary is treated as "nothing to write" (None), not an empty line.
    messages = _conversation("do the thing", "did the thing")
    model = TestModel(custom_output_text="   ")

    summary = await summarize_session(messages, model_or_settings=model)

    assert summary is None


# --------------------------------------------------------------------------------------------
# append_session_summary — pure filesystem (tmp_path), dated line + cap trim, injected `now`
# --------------------------------------------------------------------------------------------


def test_append_creates_memory_md_under_decode_dir_when_absent(tmp_path: Path):
    append_session_summary(tmp_path, "Did some work", now=_NOW)

    memory = _memory(tmp_path)
    assert memory.is_file()
    # The harness MEMORY.md lands under <cwd>/.decode (Fix 1), not at the project root.
    assert not (tmp_path / "MEMORY.md").exists()
    assert "Did some work" in memory.read_text(encoding="utf-8")


def test_append_writes_a_dated_line(tmp_path: Path):
    append_session_summary(tmp_path, "Added pagination", now=_NOW)

    content = _memory(tmp_path).read_text(encoding="utf-8")
    # A dated bullet: the UTC date (YYYY-MM-DD) and the summary on one line.
    assert "- 2026-06-19: Added pagination" in content


def test_append_keeps_existing_content_and_adds_below(tmp_path: Path):
    memory = _memory(tmp_path)
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text("- 2026-06-01: earlier note\n", encoding="utf-8")

    append_session_summary(tmp_path, "later note", now=_NOW)

    content = memory.read_text(encoding="utf-8")
    assert "earlier note" in content
    assert "later note" in content
    # The new line lands after the existing one.
    assert content.index("earlier note") < content.index("later note")


def test_append_rejects_a_naive_datetime(tmp_path: Path):
    # ADR/AGENTS discipline: datetimes are timezone-aware UTC; a naive `now` is rejected.
    naive = datetime(2026, 6, 19, 12, 30)
    with pytest.raises(ValueError, match="timezone-aware"):
        append_session_summary(tmp_path, "x", now=naive)


def test_append_trims_to_the_line_cap_keeping_most_recent(tmp_path: Path):
    # Pre-fill well past the line cap with tiny lines (byte cap never bites); appending one more
    # must drop the OLDEST lines, keeping the most recent + the just-appended summary.
    memory = _memory(tmp_path)
    memory.parent.mkdir(parents=True, exist_ok=True)
    overflow = settings.memory_max_lines + 50
    memory.write_text(
        "\n".join(f"- 2026-01-01: note{i}" for i in range(overflow)) + "\n",
        encoding="utf-8",
    )

    append_session_summary(tmp_path, "the newest summary", now=_NOW)

    lines = _memory(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) <= settings.memory_max_lines
    # The just-appended summary survives (it is the most recent).
    assert any("the newest summary" in line for line in lines)
    # The oldest pre-existing notes are dropped.
    assert not any("note0" in line for line in lines)
    # A recent pre-existing note survives.
    assert any(f"note{overflow - 1}" in line for line in lines)


def test_append_trims_to_the_byte_cap_keeping_most_recent(tmp_path: Path):
    # Few but huge lines so the BYTE cap bites first; the most-recent content must survive.
    memory = _memory(tmp_path)
    memory.parent.mkdir(parents=True, exist_ok=True)
    big = "z" * (settings.memory_max_bytes // 2)
    memory.write_text(f"- old: {big}\n- mid: {big}\n", encoding="utf-8")

    append_session_summary(tmp_path, "fresh", now=_NOW)

    content = _memory(tmp_path).read_text(encoding="utf-8")
    assert len(content.encode("utf-8")) <= settings.memory_max_bytes
    # The just-appended (most recent) line survives the byte trim.
    assert "fresh" in content


# --------------------------------------------------------------------------------------------
# extract_on_exit — orchestration; fully non-fatal (must never raise, never block exit)
# --------------------------------------------------------------------------------------------


async def test_extract_on_exit_writes_a_summary(tmp_path: Path, mocker):
    # Happy path: with a key set, summarize returns a sentence → appended (dated) to MEMORY.md.
    from pydantic import SecretStr

    mocker.patch.object(extract.settings, "gemini_api_key", SecretStr("test-key"), create=False)
    mocker.patch.object(
        extract, "summarize_session", mocker.AsyncMock(return_value="Built the thing")
    )
    fixed_now = mocker.patch.object(extract, "_utc_now", return_value=_NOW)

    await extract_on_exit(_conversation("build it", "built"), tmp_path)

    fixed_now.assert_called_once()
    content = _memory(tmp_path).read_text(encoding="utf-8")
    assert "- 2026-06-19: Built the thing" in content


async def test_extract_on_exit_writes_nothing_when_summary_is_none(tmp_path: Path, mocker):
    # With a key set, a None summary (model declined / failed) → no file is created.
    from pydantic import SecretStr

    mocker.patch.object(extract.settings, "gemini_api_key", SecretStr("test-key"), create=False)
    mocker.patch.object(extract, "summarize_session", mocker.AsyncMock(return_value=None))

    await extract_on_exit(_conversation("hi", "hello"), tmp_path)

    assert not _memory(tmp_path).exists()


async def test_extract_on_exit_is_a_noop_on_empty_conversation(tmp_path: Path, mocker):
    # An empty conversation short-circuits before the LLM call (no summarize, no write).
    spy = mocker.patch.object(extract, "summarize_session", mocker.AsyncMock(return_value="x"))

    await extract_on_exit([], tmp_path)

    spy.assert_not_called()
    assert not _memory(tmp_path).exists()


async def test_extract_on_exit_is_a_noop_without_an_api_key(tmp_path: Path, mocker):
    # No GEMINI_API_KEY → no cheap call is attempted; never blocks exit, never writes.
    from pydantic import SecretStr

    mocker.patch.object(extract.settings, "gemini_api_key", SecretStr(""), create=False)
    spy = mocker.patch.object(extract, "summarize_session", mocker.AsyncMock(return_value="x"))

    await extract_on_exit(_conversation("do it", "done"), tmp_path)

    spy.assert_not_called()
    assert not _memory(tmp_path).exists()


async def test_extract_on_exit_never_raises_when_summarize_blows_up(tmp_path: Path, mocker):
    # The whole point: a raising summarizer must be swallowed (logged) so exit is never blocked.
    from pydantic import SecretStr

    mocker.patch.object(extract.settings, "gemini_api_key", SecretStr("test-key"), create=False)
    mocker.patch.object(
        extract, "summarize_session", mocker.AsyncMock(side_effect=RuntimeError("kaboom"))
    )

    # Must return cleanly (no exception propagates out of the shutdown path).
    await extract_on_exit(_conversation("do it", "done"), tmp_path)

    assert not _memory(tmp_path).exists()


async def test_extract_on_exit_logs_a_warning_when_summarize_blows_up(tmp_path: Path, mocker):
    from pydantic import SecretStr

    mocker.patch.object(extract.settings, "gemini_api_key", SecretStr("test-key"), create=False)
    mocker.patch.object(
        extract, "summarize_session", mocker.AsyncMock(side_effect=RuntimeError("kaboom"))
    )
    warn = mocker.patch.object(extract.logger, "warning")

    await extract_on_exit(_conversation("do it", "done"), tmp_path)

    warn.assert_called_once()


async def test_extract_on_exit_never_raises_when_append_blows_up(tmp_path: Path, mocker):
    # A filesystem failure on append is also non-fatal (must not block exit).
    from pydantic import SecretStr

    mocker.patch.object(extract.settings, "gemini_api_key", SecretStr("test-key"), create=False)
    mocker.patch.object(extract, "summarize_session", mocker.AsyncMock(return_value="a summary"))
    mocker.patch.object(extract, "append_session_summary", side_effect=OSError("disk full"))

    await extract_on_exit(_conversation("do it", "done"), tmp_path)


# --------------------------------------------------------------------------------------------
# extract_on_exit — the second-level MEMORY.md compression hook (task 046 / ADR-0006 §8)
# --------------------------------------------------------------------------------------------


async def test_extract_on_exit_compresses_after_append_when_enabled(tmp_path: Path, mocker):
    # The hook fires AFTER the dated-bullet append, gated by ``memory_compression_enabled``.
    from pydantic import SecretStr

    mocker.patch.object(extract.settings, "gemini_api_key", SecretStr("test-key"), create=False)
    mocker.patch.object(extract.settings, "memory_compression_enabled", True, create=False)
    mocker.patch.object(extract, "summarize_session", mocker.AsyncMock(return_value="Built it"))
    mocker.patch.object(extract, "_utc_now", return_value=_NOW)

    order: list[str] = []
    mocker.patch.object(
        extract,
        "append_session_summary",
        side_effect=lambda *a, **k: order.append("append"),
    )
    compress = mocker.patch.object(
        extract,
        "compress_memory_file",
        mocker.AsyncMock(side_effect=lambda *a, **k: order.append("compress")),
    )

    await extract_on_exit(_conversation("build it", "built"), tmp_path)

    # Compression runs, with the launch cwd and the production settings, strictly after the append.
    compress.assert_awaited_once_with(tmp_path, model_or_settings=extract.settings)
    assert order == ["append", "compress"]


async def test_extract_on_exit_skips_compression_when_disabled(tmp_path: Path, mocker):
    # With the flag off, behaviour is unchanged: the dated bullet is appended, no compression call.
    from pydantic import SecretStr

    mocker.patch.object(extract.settings, "gemini_api_key", SecretStr("test-key"), create=False)
    mocker.patch.object(extract.settings, "memory_compression_enabled", False, create=False)
    mocker.patch.object(extract, "summarize_session", mocker.AsyncMock(return_value="Built it"))
    mocker.patch.object(extract, "_utc_now", return_value=_NOW)
    compress = mocker.patch.object(extract, "compress_memory_file", mocker.AsyncMock())

    await extract_on_exit(_conversation("build it", "built"), tmp_path)

    compress.assert_not_called()
    # Drop-oldest-only path is intact: the summary still lands as a dated bullet.
    assert "- 2026-06-19: Built it" in _memory(tmp_path).read_text(encoding="utf-8")


async def test_extract_on_exit_never_raises_when_compression_blows_up(tmp_path: Path, mocker):
    # The compression hook is fully non-fatal: a raising compressor must not block process exit,
    # and the already-appended dated bullet survives (compression runs after the append).
    from pydantic import SecretStr

    mocker.patch.object(extract.settings, "gemini_api_key", SecretStr("test-key"), create=False)
    mocker.patch.object(extract.settings, "memory_compression_enabled", True, create=False)
    mocker.patch.object(extract, "summarize_session", mocker.AsyncMock(return_value="Built it"))
    mocker.patch.object(extract, "_utc_now", return_value=_NOW)
    mocker.patch.object(
        extract, "compress_memory_file", mocker.AsyncMock(side_effect=RuntimeError("kaboom"))
    )

    # Must return cleanly (no exception propagates out of the shutdown path).
    await extract_on_exit(_conversation("build it", "built"), tmp_path)

    assert "- 2026-06-19: Built it" in _memory(tmp_path).read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------------
# compress_memory_file — second-level LLM compression at the 200-line cap (task 046)
# --------------------------------------------------------------------------------------------


def _over_cap_memory(cwd: Path, *, lines: int) -> Path:
    """Seed ``cwd/.decode/MEMORY.md`` with ``lines`` tiny dated bullets (byte cap never bites)."""
    memory = _memory(cwd)
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text(
        "\n".join(f"- 2026-01-01: note{i}" for i in range(lines)) + "\n", encoding="utf-8"
    )
    return memory


async def test_compress_rewrites_an_at_cap_file_under_the_line_cap(tmp_path: Path):
    # A file AT the 200-line cap is rewritten (via FunctionModel, no network) UNDER the cap.
    memory = _over_cap_memory(tmp_path, lines=settings.memory_max_lines)
    deduped = "\n".join(f"- 2026-01-01: merged fact {i}" for i in range(5))

    async def compress(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=deduped)])

    changed = await compress_memory_file(tmp_path, model_or_settings=FunctionModel(compress))

    assert changed is True
    lines = memory.read_text(encoding="utf-8").splitlines()
    assert len(lines) < settings.memory_max_lines
    assert any("merged fact 0" in line for line in lines)


async def test_compress_feeds_the_existing_bullets_to_the_model(tmp_path: Path):
    # The model must see the current MEMORY.md content, so it dedupes *this* file, not a blank.
    _over_cap_memory(tmp_path, lines=settings.memory_max_lines)
    seen: list[str] = []

    async def capture(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(
            "\n".join(
                part.content
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart) and isinstance(part.content, str)
            )
        )
        return ModelResponse(parts=[TextPart(content="- 2026-01-01: merged")])

    await compress_memory_file(tmp_path, model_or_settings=FunctionModel(capture))

    transcript = "\n".join(seen)
    assert "note0" in transcript
    assert f"note{settings.memory_max_lines - 1}" in transcript


async def test_compress_is_a_noop_under_the_line_cap_no_call_and_file_untouched(tmp_path: Path):
    # Below 200 lines: NO LLM call is made and the file is left byte-for-byte unchanged.
    memory = _over_cap_memory(tmp_path, lines=settings.memory_max_lines - 1)
    original = memory.read_bytes()
    called = False

    async def must_not_run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal called
        called = True
        return ModelResponse(parts=[TextPart(content="should not happen")])

    changed = await compress_memory_file(tmp_path, model_or_settings=FunctionModel(must_not_run))

    assert changed is False
    assert called is False
    assert memory.read_bytes() == original


async def test_compress_is_a_noop_when_the_file_is_missing(tmp_path: Path):
    # No MEMORY.md yet → no call, return False, nothing created.
    called = False

    async def must_not_run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal called
        called = True
        return ModelResponse(parts=[TextPart(content="should not happen")])

    changed = await compress_memory_file(tmp_path, model_or_settings=FunctionModel(must_not_run))

    assert changed is False
    assert called is False
    assert not _memory(tmp_path).exists()


async def test_compress_falls_back_to_drop_oldest_when_the_call_fails(tmp_path: Path):
    # A failing model call must be swallowed (never raised), return False, and leave the file as the
    # drop-oldest clamp already left it — still within both caps.
    memory = _over_cap_memory(tmp_path, lines=settings.memory_max_lines)
    before = memory.read_bytes()

    async def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("model exploded")

    changed = await compress_memory_file(tmp_path, model_or_settings=FunctionModel(boom))

    assert changed is False
    assert memory.read_bytes() == before
    content = memory.read_text(encoding="utf-8")
    assert len(content.splitlines()) <= settings.memory_max_lines
    assert len(content.encode("utf-8")) <= settings.memory_max_bytes


async def test_compress_falls_back_to_drop_oldest_when_the_call_is_blank(tmp_path: Path):
    # A blank/whitespace model result is treated as "nothing to write": return False, file untouched.
    memory = _over_cap_memory(tmp_path, lines=settings.memory_max_lines)
    before = memory.read_bytes()

    async def blank(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content="   ")])

    changed = await compress_memory_file(tmp_path, model_or_settings=FunctionModel(blank))

    assert changed is False
    assert memory.read_bytes() == before


async def test_compress_hard_clamps_an_oversized_model_result_by_line_cap(tmp_path: Path):
    # Even a misbehaving model that returns MORE than the line cap is hard-clamped back under it.
    memory = _over_cap_memory(tmp_path, lines=settings.memory_max_lines)
    overflow = settings.memory_max_lines + 50
    fat = "\n".join(f"- 2026-01-01: rewritten{i}" for i in range(overflow))

    async def too_many(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=fat)])

    changed = await compress_memory_file(tmp_path, model_or_settings=FunctionModel(too_many))

    assert changed is True
    content = memory.read_text(encoding="utf-8")
    assert len(content.splitlines()) <= settings.memory_max_lines
    assert len(content.encode("utf-8")) <= settings.memory_max_bytes


async def test_compress_hard_clamps_an_oversized_model_result_by_byte_cap(tmp_path: Path):
    # A model that returns fat lines blowing the byte cap is hard-clamped (keep="tail") under it.
    memory = _over_cap_memory(tmp_path, lines=settings.memory_max_lines)
    big = "y" * 1_000
    fat = "\n".join(f"- 2026-01-01: {big}{i}" for i in range(settings.memory_max_lines + 50))

    async def too_big(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content=fat)])

    changed = await compress_memory_file(tmp_path, model_or_settings=FunctionModel(too_big))

    assert changed is True
    content = memory.read_text(encoding="utf-8")
    assert len(content.splitlines()) <= settings.memory_max_lines
    assert len(content.encode("utf-8")) <= settings.memory_max_bytes


# --------------------------------------------------------------------------------------------
# Round-trip: a written summary is discoverable by assemble_memory next session (ADR-0002 §8)
# --------------------------------------------------------------------------------------------


def test_written_summary_is_picked_up_by_assemble_memory_next_session(tmp_path: Path):
    # Session N writes a dated summary; session N+1's assemble_memory must surface it.
    append_session_summary(tmp_path, "Wired memory write-back on exit", now=_NOW)

    assembled = assemble_memory(tmp_path)

    assert "Wired memory write-back on exit" in assembled
    assert "2026-06-19" in assembled
    # It rides under MEMORY.md's provenance header (the file assemble_memory caps), now under
    # the consolidated <cwd>/.decode dir (Fix 1).
    assert f"# From {_memory(tmp_path).resolve()}" in assembled
