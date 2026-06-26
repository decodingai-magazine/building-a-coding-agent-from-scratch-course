---
id: 043-session-log-compaction-line
feature: context-compaction
status: pending
---

# JSONL `compaction` checkpoint line: persist + tolerant replay

Implements [ADR-0006](../docs/adr/0006-conversation-compaction.md) §6 — the careful correctness bit.
Extends `src/decode/context/session_log.py` so a **full** compaction is a typed checkpoint line on the
existing append-only JSONL log (NO SQLite), and replay reconstructs the **compacted** history.
**Microcompaction is NOT persisted** (in-memory only — ADR-0006 §3a), so this task is full-compaction only.
Depends on: 042 · Blocks: 044

## Scope

In `src/decode/context/session_log.py`:

- Add `_COMPACTION_TYPE = "compaction"` alongside `_HEADER_TYPE` / `_MESSAGES_TYPE`.
- **Writer** — `SessionLog.append_compaction(summary_message, tail) -> None`: append one
  `{"type": "compaction", "summary": <dump([summary_message])>, "tail": <dump(tail)>}` line (both via
  `ModelMessagesTypeAdapter`). Append-only.
- **Reader** — teach `load()` to honor a `compaction` line **in file order**: discard everything
  accumulated so far, set history to `[*summary, *tail]`, then continue with subsequent lines. Tolerant —
  a malformed `compaction` line is logged at debug and skipped (degrading to the un-compacted history),
  never raised.

This makes **successive full compactions merge for free** at the log level (each checkpoint
discards-and-restarts; the prior summary, as the head of the handler's history, is re-summarized — task
044). No merge logic in the log.

## Acceptance criteria

- [ ] `append_compaction` appends exactly one `type == "compaction"` line carrying serialized summary +
      tail; header and prior turn lines untouched.
- [ ] **Compact-then-resume replays compacted history:** `header → turn1 → turn2 → turn3 →
      compaction(summary, tail=[turn3])` replays to `[summary_message, *turn3]`, NOT the full transcript.
- [ ] **Post-compaction turns continue:** `… → compaction(summary, tail) → turn4` replays to
      `[summary_message, *tail, *turn4]`.
- [ ] **Successive compactions:** two `compaction` lines replay to the second checkpoint (+ later turns).
- [ ] A truncated/garbage `compaction` line is skipped (logged, not raised); replay still yields a valid
      un-compacted history.
- [ ] `load_latest` / `resolve_session` work unchanged through a compacted file.
- [ ] `tests/unit/decode/context/test_session_log.py` extended; `make ci` green, 0 warnings.

## Out of scope
- Deciding when/what to compact (tasks 042/044).
- Persisting microcompaction (deliberately in-memory only).
- Any SQLite store; changing the `messages`/`session` formats or header version.

## Log
