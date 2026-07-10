"""Append-only JSONL session log with replay (ADR-0002 §9).

One JSONL file per REPL session: line 0 is a typed ``session`` header; every later line is a
typed ``messages`` batch, a ``compaction`` checkpoint, or a ``clear`` marker. The file is
append-only and never rewritten; :func:`load` / :func:`load_latest` replay it to seed
``decode --resume``. Replay is tolerant — a truncated or garbage line is skipped, never raised.
``now`` / ``session_id`` are injected so filename and header are deterministic in tests; file
I/O stays sync (small local appends, sequential tool layer — ADR-0002 §7,10).
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

# Header schema version — bump if the on-disk line format changes.
_HEADER_VERSION = 1
# Typed-line discriminators: header / turn messages / compaction checkpoint / clear marker.
_HEADER_TYPE = "session"
_MESSAGES_TYPE = "messages"
_COMPACTION_TYPE = "compaction"
_CLEAR_TYPE = "clear"
_SUFFIX = ".jsonl"
# Compact UTC filename timestamp, lexically sortable — load_latest takes the lexicographic max.
_FILENAME_TS_FORMAT = "%Y%m%dT%H%M%SZ"


def _utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime (the default clock, injected in tests)."""
    return datetime.now(UTC)


@dataclass(slots=True)
class SessionLog:
    """Writer side of a session's JSONL log: open once via :meth:`create`, append per turn."""

    path: Path

    @property
    def session_id(self) -> str:
        """The session id embedded in the log filename (``<timestamp>_<session_id>.jsonl``).

        Names the git hand-back's ``decode/<session-id>`` Session Branch (ADR-0012 §8). The
        timestamp format carries no ``_``, so the id is the stem's segment after the first ``_``.
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

        ``now`` / ``session_id`` are injected for deterministic tests (defaults: production
        clock + a random uuid). ``now`` must be timezone-aware UTC — naive is rejected.
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
        """Append one turn's ``new_messages()`` as a typed ``messages`` line (append-only) (§9).

        An empty batch writes nothing; the header and prior lines are never touched.
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

        On replay :func:`load` discards everything before this line and restarts the history
        from ``[summary_message, *tail]``, so successive full compactions merge for free.
        Microcompaction is in-memory only and is never persisted here (ADR-0006 §3a).
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
        """Append one typed ``clear`` marker line (append-only) — compaction-to-zero.

        On replay :func:`load` discards everything before this line and restarts from ``[]``;
        the wiped turns remain on disk above the marker but no longer replay.
        """
        entry = {"type": _CLEAR_TYPE}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        logger.debug("appended clear marker to %s", self.path)


def load(path: Path) -> list[ModelMessage]:
    """Replay a session file into a flat ``message_history`` list (ADR-0002 §9, ADR-0006 §1).

    Reads every line in file order: a ``messages`` entry extends the history; a ``compaction``
    checkpoint restarts it from ``[summary, *tail]``; a ``clear`` marker restarts it from
    ``[]``. Tolerant by design: any malformed line (e.g. a crash-truncated tail) is skipped,
    never raised — a malformed checkpoint degrades to the un-compacted history. A missing file
    replays to ``[]``.
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
    """Replay the most recent session file (lexicographic max of the timestamped names) (§9).

    Returns ``None`` when there is no session to resume (empty or absent directory).
    """
    latest = _latest_session_file(sessions_dir)
    if latest is None:
        return None
    return load(latest)


def resolve_session(sessions_dir: Path, identifier: str) -> Path | None:
    """Resolve a ``--resume <id-or-filename>`` argument to a session file path (§9).

    Accepts a full filename or just the embedded session id; ``None`` when nothing matches.
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
    if not sessions_dir.is_dir():
        return []
    return [p for p in sessions_dir.iterdir() if p.is_file() and p.suffix == _SUFFIX]


def _latest_session_file(sessions_dir: Path) -> Path | None:
    files = _session_files(sessions_dir)
    if not files:
        return None
    return max(files, key=lambda p: p.name)


def _parse_messages_line(line: str) -> list[ModelMessage] | None:
    """Deserialize one ``messages`` line; ``None`` for any other or malformed line (skipped)."""
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
    """True for a well-formed ``clear`` marker line; any other or malformed line is ``False``."""
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

    Any other or malformed line yields ``None`` (never raises) — a rejected checkpoint is
    skipped, degrading to the un-compacted history.
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
