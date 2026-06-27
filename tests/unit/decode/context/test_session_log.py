"""Unit tests for :mod:`decode.context.session_log` — the JSONL session log (ADR-0002 §9).

The session log is the M1 persistence layer: an **append-only JSONL** file per session. Line 0
is a typed ``session`` header (version, session id, cwd, created_at UTC); every later line is a
typed ``messages`` batch carrying one turn's ``new_messages()`` serialized via Pydantic AI's
:data:`~pydantic_ai.messages.ModelMessagesTypeAdapter`. ``load`` / ``load_latest`` replay the
file back into a ``list[ModelMessage]`` that seeds ``decode --resume``.

Five behaviours are exercised:

* **header** — opening a session writes line 0 as a typed ``session`` object with deterministic
  ``now`` / ``uuid`` (injected) and creates ``sessions_dir`` if absent;
* **append_turn** — each turn's new messages append one typed ``messages`` line; the file is
  never rewritten (append-only);
* **load round-trip** — a real short agent run (``TestModel`` / ``FunctionModel``, no network)
  produces real ``ModelMessage`` objects; persisting then reloading yields an equal, usable list;
* **compaction** — a full compaction appends one typed ``compaction`` checkpoint line carrying
  the serialized summary + kept tail (ADR-0006 §1); replay restarts the history from
  ``[summary, *tail]`` at that line and continues, so a compacted file replays to the *compacted*
  history, successive checkpoints land on the last one, and a malformed checkpoint degrades to
  the un-compacted history (never raised);
* **resilience** — a truncated / garbage trailing line (a crash mid-write) is skipped, not
  raised; an empty session (header only) loads to ``[]``; ``load_latest`` picks the newest file.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from decode.context import session_log
from decode.context.session_log import SessionLog

_NOW = datetime(2026, 6, 19, 12, 30, 45, tzinfo=UTC)
_UUID = UUID("12345678-1234-5678-1234-567812345678")


def _conversation(user: str, assistant: str) -> list[ModelMessage]:
    """A minimal two-message turn: one user prompt, one assistant text reply."""
    return [
        ModelRequest(parts=[UserPromptPart(content=user)]),
        ModelResponse(parts=[TextPart(content=assistant)]),
    ]


def _summary_message(text: str = "# Conversation summary\n\n## Goal\nship it") -> ModelMessage:
    """A synthetic full-compaction summary head: one ``ModelRequest`` / ``UserPromptPart`` (§4)."""
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _read_lines(path: Path) -> list[str]:
    """The non-empty lines of a session file, in order."""
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


# --------------------------------------------------------------------------------------------
# create — header line 0 (typed, deterministic now/uuid) + sessions_dir creation
# --------------------------------------------------------------------------------------------


def test_create_makes_the_sessions_dir_when_absent(tmp_path: Path):
    sessions_dir = tmp_path / "nested" / "sessions"
    assert not sessions_dir.exists()

    SessionLog.create(sessions_dir, cwd=tmp_path, now=_NOW, session_id=_UUID)

    assert sessions_dir.is_dir()


def test_create_writes_a_typed_header_as_line_zero(tmp_path: Path):
    import json

    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)

    lines = _read_lines(log.path)
    assert len(lines) == 1  # only the header so far
    header = json.loads(lines[0])
    assert header["type"] == "session"
    assert header["version"] == 1
    assert header["session_id"] == str(_UUID)
    assert header["cwd"] == str(tmp_path)
    assert header["created_at"] == _NOW.isoformat()


def test_create_names_the_file_from_the_timestamp_and_uuid(tmp_path: Path):
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)

    # The filename embeds the UTC timestamp (sortable) and the uuid, with a .jsonl suffix.
    assert log.path.suffix == ".jsonl"
    assert str(_UUID) in log.path.name
    assert log.path.parent == tmp_path


# --------------------------------------------------------------------------------------------
# append_turn — typed message batches appended, append-only (header never rewritten)
# --------------------------------------------------------------------------------------------


def test_append_turn_appends_a_typed_messages_line(tmp_path: Path):
    import json

    turn = _conversation("hello", "hi there")
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    log.append_turn(turn)

    lines = _read_lines(log.path)
    assert len(lines) == 2  # header + one turn
    entry = json.loads(lines[1])
    assert entry["type"] == "messages"
    # The messages payload round-trips through the Pydantic AI type adapter.
    restored = ModelMessagesTypeAdapter.validate_python(entry["messages"])
    assert restored == turn


def test_append_turn_is_append_only_and_keeps_the_header(tmp_path: Path):
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    header_before = _read_lines(log.path)[0]

    log.append_turn(_conversation("first", "1"))
    log.append_turn(_conversation("second", "2"))

    lines = _read_lines(log.path)
    assert len(lines) == 3  # header + two turns, nothing rewritten
    assert lines[0] == header_before  # the header line is untouched


def test_append_turn_ignores_an_empty_batch(tmp_path: Path):
    # A turn that produced no new messages writes nothing (no blank/typed-empty line).
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    log.append_turn([])

    assert len(_read_lines(log.path)) == 1  # header only


# --------------------------------------------------------------------------------------------
# load — round-trip a real agent run (TestModel, no network) back to message_history
# --------------------------------------------------------------------------------------------


async def test_load_round_trips_a_real_agent_run(tmp_path: Path):
    # Drive a real (offline) agent turn to produce genuine ModelMessage objects, persist the
    # run's new messages, reload them, and assert the replayed history equals the original.
    agent: Agent[None, str] = Agent(TestModel(custom_output_text="all done"))
    result = await agent.run("do the work")
    original = result.new_messages()

    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    log.append_turn(original)

    replayed = session_log.load(log.path)

    assert replayed == original


async def test_load_round_trips_multiple_turns_in_order(tmp_path: Path):
    # Two persisted turns replay as one flat history in turn order (what --resume seeds).
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    turn_one = _conversation("first question", "first answer")
    turn_two = _conversation("second question", "second answer")
    log.append_turn(turn_one)
    log.append_turn(turn_two)

    replayed = session_log.load(log.path)

    assert replayed == [*turn_one, *turn_two]


def test_load_returns_empty_for_a_header_only_session(tmp_path: Path):
    # A session that ran no turns (header only) replays to an empty history.
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)

    assert session_log.load(log.path) == []


# --------------------------------------------------------------------------------------------
# append_compaction — one typed checkpoint line (summary + tail), header/prior turns untouched
# --------------------------------------------------------------------------------------------


def test_append_compaction_appends_exactly_one_typed_compaction_line(tmp_path: Path):
    import json

    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    log.append_turn(_conversation("q1", "a1"))
    header_line, turn_line = _read_lines(log.path)  # header + the prior turn

    summary = _summary_message()
    tail = _conversation("recent q", "recent a")
    log.append_compaction(summary, tail)

    lines = _read_lines(log.path)
    assert len(lines) == 3  # header + prior turn + exactly one compaction line
    assert lines[0] == header_line  # header untouched
    assert lines[1] == turn_line  # prior turn untouched (append-only)
    entry = json.loads(lines[2])
    assert entry["type"] == "compaction"
    # Both halves round-trip through the same Pydantic AI type adapter append_turn uses.
    assert ModelMessagesTypeAdapter.validate_python(entry["summary"]) == [summary]
    assert ModelMessagesTypeAdapter.validate_python(entry["tail"]) == tail


# --------------------------------------------------------------------------------------------
# load — a compaction checkpoint restarts history from [summary, *tail], in file order
# --------------------------------------------------------------------------------------------


def test_compact_then_resume_replays_the_compacted_history(tmp_path: Path):
    # header -> turn1 -> turn2 -> turn3 -> compaction(summary, tail=[turn3]) replays to the
    # compacted history [summary, *turn3] -- NOT the full verbatim transcript.
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    turn1 = _conversation("q1", "a1")
    turn2 = _conversation("q2", "a2")
    turn3 = _conversation("q3", "a3")
    for turn in (turn1, turn2, turn3):
        log.append_turn(turn)
    summary = _summary_message()
    log.append_compaction(summary, turn3)

    replayed = session_log.load(log.path)

    assert replayed == [summary, *turn3]


def test_turns_after_a_compaction_continue_the_compacted_history(tmp_path: Path):
    # ... -> compaction(summary, tail) -> turn4 replays to [summary, *tail, *turn4].
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    log.append_turn(_conversation("q1", "a1"))
    tail = _conversation("q2", "a2")
    log.append_turn(tail)
    summary = _summary_message()
    log.append_compaction(summary, tail)
    turn4 = _conversation("q4", "a4")
    log.append_turn(turn4)

    replayed = session_log.load(log.path)

    assert replayed == [summary, *tail, *turn4]


def test_successive_compactions_replay_to_the_second_checkpoint(tmp_path: Path):
    # Two checkpoints: the second discards the first (and its summary/turns); only the second
    # checkpoint plus later turns survive -- successive compactions merge for free at the log.
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    log.append_turn(_conversation("q1", "a1"))
    summary_one = _summary_message("# summary one")
    log.append_compaction(summary_one, _conversation("q1", "a1"))
    turn2 = _conversation("q2", "a2")
    log.append_turn(turn2)
    summary_two = _summary_message("# summary two")
    log.append_compaction(summary_two, turn2)
    turn3 = _conversation("q3", "a3")
    log.append_turn(turn3)

    replayed = session_log.load(log.path)

    assert replayed == [summary_two, *turn2, *turn3]


def test_load_tolerates_a_truncated_compaction_line(tmp_path: Path):
    # A crash mid-write of a checkpoint line: the truncated compaction line is skipped, replay
    # degrades to the un-compacted history -- never applied half-way, never raised.
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    turn1 = _conversation("q1", "a1")
    turn2 = _conversation("q2", "a2")
    log.append_turn(turn1)
    log.append_turn(turn2)
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"compaction","summary":[{"parts":[{"con')  # truncated JSON

    replayed = session_log.load(log.path)

    assert replayed == [*turn1, *turn2]


def test_load_tolerates_a_malformed_compaction_payload(tmp_path: Path):
    # Valid JSON, type == compaction, but the type adapter rejects the payload shape: skipped
    # (logged at debug, not raised), degrading to the un-compacted history.
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    turn1 = _conversation("q1", "a1")
    log.append_turn(turn1)
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"compaction","summary":"not-a-message-list","tail":[]}\n')

    replayed = session_log.load(log.path)

    assert replayed == [*turn1]


def test_load_latest_replays_through_a_compacted_file(tmp_path: Path):
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    log.append_turn(_conversation("q1", "a1"))
    summary = _summary_message()
    tail = _conversation("q2", "a2")
    log.append_compaction(summary, tail)

    replayed = session_log.load_latest(tmp_path)

    assert replayed == [summary, *tail]


def test_resolve_session_finds_and_replays_a_compacted_file(tmp_path: Path):
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    summary = _summary_message()
    tail = _conversation("q", "a")
    log.append_compaction(summary, tail)

    found = session_log.resolve_session(tmp_path, str(_UUID))

    assert found == log.path
    assert session_log.load(found) == [summary, *tail]


# --------------------------------------------------------------------------------------------
# resilience — a truncated/garbage trailing line (crash mid-write) is tolerated, not raised
# --------------------------------------------------------------------------------------------


def test_load_tolerates_a_truncated_trailing_line(tmp_path: Path):
    # Simulate a crash mid-write: a good turn, then a half-written garbage line. load must
    # return the good messages and skip the corrupt tail instead of raising.
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    good = _conversation("solid turn", "ok")
    log.append_turn(good)
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"messages","messages":[{"parts":[{"con')  # truncated JSON

    replayed = session_log.load(log.path)

    assert replayed == good


def test_load_tolerates_a_garbage_non_json_trailing_line(tmp_path: Path):
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    good = _conversation("solid turn", "ok")
    log.append_turn(good)
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write("not json at all\n")

    assert session_log.load(log.path) == good


def test_load_skips_a_corrupt_header(tmp_path: Path):
    # A garbage header line is skipped too: load never raises on a malformed file.
    path = tmp_path / "broken.jsonl"
    path.write_text("totally broken header\n", encoding="utf-8")

    assert session_log.load(path) == []


# --------------------------------------------------------------------------------------------
# load_latest — pick the most recent session file by the timestamped name
# --------------------------------------------------------------------------------------------


def test_load_latest_picks_the_newest_session(tmp_path: Path):
    # Two sessions with different timestamps; load_latest replays the newer one's history.
    older = SessionLog.create(
        tmp_path,
        cwd=tmp_path,
        now=datetime(2026, 6, 19, 8, 0, 0, tzinfo=UTC),
        session_id=UUID("00000000-0000-0000-0000-000000000001"),
    )
    older.append_turn(_conversation("old work", "old"))
    newer = SessionLog.create(
        tmp_path,
        cwd=tmp_path,
        now=datetime(2026, 6, 19, 18, 0, 0, tzinfo=UTC),
        session_id=UUID("00000000-0000-0000-0000-000000000002"),
    )
    newer_turn = _conversation("new work", "new")
    newer.append_turn(newer_turn)

    replayed = session_log.load_latest(tmp_path)

    assert replayed == newer_turn


def test_load_latest_returns_none_when_no_session_exists(tmp_path: Path):
    # A friendly "nothing to resume" signal for cli --resume: None, not an exception.
    assert session_log.load_latest(tmp_path) is None


def test_load_latest_returns_none_when_dir_is_absent(tmp_path: Path):
    assert session_log.load_latest(tmp_path / "does-not-exist") is None


def test_load_by_session_id_finds_the_matching_file(tmp_path: Path):
    # cli --resume <session-id> resolves the file whose name embeds that id.
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)
    log.append_turn(_conversation("named resume", "ok"))

    found = session_log.resolve_session(tmp_path, str(_UUID))

    assert found == log.path


def test_resolve_session_accepts_a_filename(tmp_path: Path):
    log = SessionLog.create(tmp_path, cwd=tmp_path, now=_NOW, session_id=_UUID)

    assert session_log.resolve_session(tmp_path, log.path.name) == log.path


def test_resolve_session_returns_none_when_not_found(tmp_path: Path):
    assert session_log.resolve_session(tmp_path, "nope") is None


# --------------------------------------------------------------------------------------------
# clock — the injected default now is timezone-aware UTC (the package-wide boundary rule)
# --------------------------------------------------------------------------------------------


def test_default_now_is_timezone_aware_utc():
    now = session_log._utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == UTC.utcoffset(None)


def test_create_rejects_a_naive_now(tmp_path: Path):
    naive = datetime(2026, 6, 19, 12, 30)
    with pytest.raises(ValueError, match="timezone-aware"):
        SessionLog.create(tmp_path, cwd=tmp_path, now=naive, session_id=_UUID)
