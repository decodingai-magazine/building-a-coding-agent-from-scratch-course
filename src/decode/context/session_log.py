"""Append-only JSONL session log with replay (ADR-0002 §9).

The M1 persistence layer: every REPL session gets one JSONL file at
``{settings.sessions_dir}/{utc-ts}_{uuid}.jsonl``. **Line 0** is a typed ``session`` header
(``version``, ``session_id``, ``cwd``, ``created_at`` UTC); **every later line** is a typed
``messages`` batch carrying one turn's ``new_messages()`` serialized via Pydantic AI's
:data:`~pydantic_ai.messages.ModelMessagesTypeAdapter`. The file is a stream of typed lines,
**append-only and never rewritten** — a turn appends one line and that is the only mutation.

:class:`SessionLog` is the *writer*: :meth:`SessionLog.create` opens the file and writes the
header; :meth:`SessionLog.append_turn` appends one turn. The module-level :func:`load` /
:func:`load_latest` are the *reader*: they replay a file back into a ``list[ModelMessage]`` that
seeds ``decode --resume``. Replay is **tolerant** — a truncated or garbage line (a crash
mid-write leaves a half-line at the tail) is skipped, never raised — so a resume after a crash
recovers every whole turn that made it to disk.

Why ``now`` / ``session_id`` are injected: the filename and the header's ``created_at`` are
derived from them, so injecting (rather than calling argless ``datetime.now()`` / ``uuid4()``)
makes both **deterministic in tests**. The defaults (:func:`_utc_now`, :func:`uuid4`) are the
production clock/id source.

Why the file I/O is **sync**, not async: each call appends a single small line to a local file
and the tool layer is sequential in v1 (ADR-0002 §7,10), so there is no concurrent writer to
interleave with and nothing to overlap with other I/O — the same rationale the sibling
:mod:`decode.memory.extract` filesystem write-back uses. Network and the durable
(SQLite/Kitaru) store are later milestones.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic_ai.messages import ModelMessagesTypeAdapter

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

logger = logging.getLogger(__name__)

# The current header schema version: bump if the on-disk line format changes (replay can then
# branch on it). M1 only ever writes version 1.
_HEADER_VERSION = 1
# Typed-line discriminators: line 0 is the session header, every later line a turn's messages,
# a full-compaction checkpoint (ADR-0006 §1), or a ``/clear`` marker (compaction-to-zero).
_HEADER_TYPE = "session"
_MESSAGES_TYPE = "messages"
_COMPACTION_TYPE = "compaction"
_CLEAR_TYPE = "clear"
# Session files are JSONL.
_SUFFIX = ".jsonl"
# The timestamp format embedded in the filename: compact, UTC, and lexically sortable so the
# newest file is simply the lexicographic max (what load_latest relies on).
_FILENAME_TS_FORMAT = "%Y%m%dT%H%M%SZ"


def _utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime (the default clock, injected in tests)."""
    return datetime.now(UTC)


@dataclass(slots=True)
class SessionLog:
    """The writer side of a session's JSONL log: open once, append per turn (ADR-0002 §9).

    Constructed via :meth:`create` (which writes the header); thereafter :meth:`append_turn`
    appends one typed ``messages`` line per turn. ``path`` is the file it owns.
    """

    path: Path

    @property
    def session_id(self) -> str:
        """The session id embedded in the log filename (``<timestamp>_<session_id>.jsonl``).

        The single accessor for "which session is this?" — used by the task-083 git hand-back to name
        its ``decode/<session-id>`` Session Branch (ADR-0012 §8). The filename is
        ``{now:_FILENAME_TS_FORMAT}_{session_id}{_SUFFIX}`` and the timestamp format carries no ``_``,
        so the id is exactly the stem's segment after the first ``_`` (falling back to the whole stem if
        the format ever changes). Recovered from the filename rather than re-read from the header so it
        needs no file I/O.
        """
        return self.path.stem.split("_", 1)[-1]

    @classmethod
    def create(
        cls,
        sessions_dir: Path,
        *,
        cwd: Path,
        now: datetime | None = None,
        session_id: UUID | None = None,
    ) -> SessionLog:
        """Open a fresh session file under ``sessions_dir`` and write the typed header (§9).

        Creates ``sessions_dir`` (and parents) if absent, derives the filename from ``now`` and
        ``session_id`` (both injected so tests are deterministic; defaults are the production
        clock + a random uuid), and writes **line 0**: a typed ``session`` object carrying the
        schema ``version``, the ``session_id``, the launch ``cwd``, and the UTC ``created_at``.

        ``now`` **must be timezone-aware UTC** — a naive datetime is rejected, the package-wide
        boundary rule (ADR-0002 §10).
        """
        resolved_now = now or _utc_now()
        if resolved_now.tzinfo is None:
            raise ValueError("now must be a timezone-aware (UTC) datetime, not naive")
        resolved_now = resolved_now.astimezone(UTC)
        resolved_id = session_id or uuid4()

        sessions_dir.mkdir(parents=True, exist_ok=True)
        path = sessions_dir / f"{resolved_now:{_FILENAME_TS_FORMAT}}_{resolved_id}{_SUFFIX}"

        header = {
            "type": _HEADER_TYPE,
            "version": _HEADER_VERSION,
            "session_id": str(resolved_id),
            "cwd": str(cwd),
            "created_at": resolved_now.isoformat(),
        }
        with path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(header) + "\n")
        logger.debug("opened session log %s", path)
        return cls(path=path)

    def append_turn(self, new_messages: list[ModelMessage]) -> None:
        """Append one turn's new messages as a typed ``messages`` line (append-only) (§9).

        ``new_messages`` is the turn's ``new_messages()`` — the messages added on *this* turn,
        not the whole history — serialized via
        :data:`~pydantic_ai.messages.ModelMessagesTypeAdapter` and wrapped in a typed
        ``{"type": "messages", "messages": ...}`` envelope. An empty batch (a turn that added
        nothing) writes nothing, so the file stays clean. The file is opened in append mode and
        the header / prior turns are never touched.
        """
        if not new_messages:
            return
        payload = json.loads(ModelMessagesTypeAdapter.dump_json(new_messages))
        entry = {"type": _MESSAGES_TYPE, "messages": payload}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        logger.debug("appended %d message(s) to %s", len(new_messages), self.path)

    def append_compaction(self, summary_message: ModelMessage, tail: list[ModelMessage]) -> None:
        """Append one typed ``compaction`` checkpoint line (append-only) (ADR-0006 §1, §6).

        A **full** compaction (the LLM tier — ADR-0006 §2) persists here: ``summary_message`` is
        the synthetic summary head and ``tail`` the recent verbatim turns kept past the
        Compaction Boundary (ADR-0006 §5). Both are serialized via
        :data:`~pydantic_ai.messages.ModelMessagesTypeAdapter` (the same adapter
        :meth:`append_turn` uses) and wrapped in a typed
        ``{"type": "compaction", "summary": <dump([summary_message])>, "tail": <dump(tail)>}``
        envelope. On replay :func:`load` **discards** everything accumulated before this line and
        restarts the history from ``[summary_message, *tail]``, then continues — so earlier
        verbatim copies of the tail are superseded, never double-counted, and successive full
        compactions merge for free (the prior summary rides as the head). The file is opened in
        append mode; the header and prior lines are never touched.

        Microcompaction (ADR-0006 §3a) is in-memory only and is deliberately **not** persisted
        here — the log keeps full fidelity for recovery.
        """
        summary_payload = json.loads(ModelMessagesTypeAdapter.dump_json([summary_message]))
        tail_payload = json.loads(ModelMessagesTypeAdapter.dump_json(tail))
        entry = {
            "type": _COMPACTION_TYPE,
            "summary": summary_payload,
            "tail": tail_payload,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        logger.debug("appended compaction checkpoint (tail=%d) to %s", len(tail), self.path)

    def append_clear(self) -> None:
        """Append one typed ``clear`` marker line (append-only) — the ``/clear`` checkpoint.

        ``/clear`` is compaction-to-zero: on replay :func:`load` **discards** everything
        accumulated before this line and restarts the history from ``[]`` (the same
        discard-and-restart path a ``compaction`` checkpoint uses, with nothing kept), so a
        ``--resume`` after a ``/clear`` continues the post-clear conversation instead of
        resurrecting the wiped turns. The file stays append-only — the wiped turns remain on
        disk above the marker (full-fidelity history, like the compaction checkpoint's
        superseded prefix), they just no longer replay.
        """
        entry = {"type": _CLEAR_TYPE}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        logger.debug("appended clear marker to %s", self.path)


def load(path: Path) -> list[ModelMessage]:
    """Replay a session file into a flat ``message_history`` list (ADR-0002 §9, ADR-0006 §1).

    Reads every line **in file order**. The header and any malformed line are skipped; a
    ``messages`` entry is deserialized via
    :data:`~pydantic_ai.messages.ModelMessagesTypeAdapter` and **appended**; a ``compaction``
    checkpoint **discards everything accumulated so far** and restarts the history from its
    serialized ``[summary, *tail]``, after which later lines continue to extend it. So a
    compacted file replays to the *compacted* history (summary + kept tail + any later turns),
    and successive compactions land on the **last** checkpoint — the earlier verbatim turns it
    superseded are never double-counted. The result is what ``decode --resume`` seeds the turn
    handler with.

    **Tolerant by design**: a truncated or garbage line — most likely the tail, from a crash
    mid-write — is logged at debug and skipped, never raised; a malformed ``compaction`` line is
    likewise skipped, degrading to the un-compacted history. So a resume recovers every whole
    turn that reached disk. A missing file replays to ``[]``.
    """
    if not path.is_file():
        return []

    history: list[ModelMessage] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if _is_clear_line(line):
            history = []  # /clear marker: compaction-to-zero — discard prior, restart empty
            continue
        compacted = _parse_compaction_line(line)
        if compacted is not None:
            history = compacted  # checkpoint: discard prior, restart from [summary, *tail]
            continue
        batch = _parse_messages_line(line)
        if batch is not None:
            history.extend(batch)
    return history


def load_latest(sessions_dir: Path) -> list[ModelMessage] | None:
    """Replay the most recent session file in ``sessions_dir`` (ADR-0002 §9).

    "Most recent" is the lexicographic max of the timestamped filenames (the ``%Y%m%dT%H%M%SZ``
    prefix is sortable). Returns the replayed history, or ``None`` when there is no session to
    resume (empty or absent directory) — the signal ``decode --resume`` turns into a friendly
    "nothing to resume" message rather than a crash.
    """
    latest = _latest_session_file(sessions_dir)
    if latest is None:
        return None
    return load(latest)


def resolve_session(sessions_dir: Path, identifier: str) -> Path | None:
    """Resolve a ``--resume <id-or-filename>`` argument to a session file path (§9).

    Accepts either a full filename (``20260619T123045Z_<uuid>.jsonl``) or just the embedded
    session id; matches against the ``.jsonl`` files in ``sessions_dir``. Returns the matching
    path, or ``None`` when nothing matches (the friendly "no such session" signal for the CLI).
    """
    if not sessions_dir.is_dir():
        return None

    direct = sessions_dir / identifier
    if direct.is_file():
        return direct

    for candidate in _session_files(sessions_dir):
        if candidate.name == identifier or identifier in candidate.stem:
            return candidate
    return None


def _session_files(sessions_dir: Path) -> list[Path]:
    """All ``.jsonl`` session files in ``sessions_dir`` (empty if the dir is absent)."""
    if not sessions_dir.is_dir():
        return []
    return [p for p in sessions_dir.iterdir() if p.is_file() and p.suffix == _SUFFIX]


def _latest_session_file(sessions_dir: Path) -> Path | None:
    """The newest session file by sortable timestamped name, or ``None`` when there are none."""
    files = _session_files(sessions_dir)
    if not files:
        return None
    return max(files, key=lambda p: p.name)


def _parse_messages_line(line: str) -> list[ModelMessage] | None:
    """Deserialize one ``messages`` line, or ``None`` for the header / a corrupt line.

    Blank lines, the typed ``session`` header, and any line that does not parse as a
    well-formed ``messages`` entry (truncated JSON, garbage, wrong type, or a payload the type
    adapter rejects) yield ``None`` — replay skips them. This is what makes :func:`load`
    tolerant of a crash mid-write.
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        obj: Any = json.loads(stripped)
    except json.JSONDecodeError:
        logger.debug("skipping unparseable session-log line: %r", line[:80])
        return None
    if not isinstance(obj, dict) or obj.get("type") != _MESSAGES_TYPE:
        return None
    try:
        return ModelMessagesTypeAdapter.validate_python(obj["messages"])
    except Exception:
        logger.debug("skipping malformed messages entry in session log", exc_info=True)
        return None


def _is_clear_line(line: str) -> bool:
    """True for a well-formed ``clear`` marker line (the ``/clear`` checkpoint-to-zero).

    Mirrors the sibling parsers' tolerance: blank lines, unparseable JSON, and every other typed
    line yield ``False`` so :func:`load` falls through to the compaction / messages parse — never
    raises.
    """
    stripped = line.strip()
    if not stripped:
        return False
    try:
        obj: Any = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and obj.get("type") == _CLEAR_TYPE


def _parse_compaction_line(line: str) -> list[ModelMessage] | None:
    """Deserialize one ``compaction`` checkpoint into ``[*summary, *tail]``, or ``None`` (§1).

    A well-formed ``compaction`` line carries a serialized ``summary`` (the synthetic one-message
    summary head) and a verbatim ``tail`` (the recent turns kept past the Compaction Boundary),
    both via :data:`~pydantic_ai.messages.ModelMessagesTypeAdapter`; this reconstructs the
    **compacted** history ``[*summary, *tail]`` that :func:`load` restarts from at this line.

    Any other line yields ``None`` so :func:`load` falls through to the ``messages`` parse / skip:
    the header, a ``messages`` batch, blank lines, and unparseable JSON return ``None`` silently
    (the unparseable case is logged by :func:`_parse_messages_line`, the second parse attempt),
    while a ``compaction``-typed line the type adapter rejects — a crash mid-write — is logged at
    debug and skipped, degrading to the un-compacted history. Mirrors
    :func:`_parse_messages_line`'s tolerance; never raises.
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        obj: Any = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or obj.get("type") != _COMPACTION_TYPE:
        return None
    try:
        summary = ModelMessagesTypeAdapter.validate_python(obj["summary"])
        tail = ModelMessagesTypeAdapter.validate_python(obj["tail"])
    except Exception:
        logger.debug("skipping malformed compaction entry in session log", exc_info=True)
        return None
    return [*summary, *tail]
