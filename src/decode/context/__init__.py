"""Context engineering: conversation persistence + compaction (ADR-0002 §9, ADR-0006).

:mod:`decode.context.session_log` is the replayable JSONL log ``decode --resume`` seeds from;
:mod:`decode.context.compaction` owns the compaction primitives.
"""

from __future__ import annotations

from decode.context.session_log import SessionLog, load, load_latest, resolve_session

__all__ = ["SessionLog", "load", "load_latest", "resolve_session"]
