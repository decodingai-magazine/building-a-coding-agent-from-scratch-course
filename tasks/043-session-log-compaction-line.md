---
id: 043-session-log-compaction-line
feature: context-compaction
status: done
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

- [x] `append_compaction` appends exactly one `type == "compaction"` line carrying serialized summary +
      tail; header and prior turn lines untouched.
- [x] **Compact-then-resume replays compacted history:** `header → turn1 → turn2 → turn3 →
      compaction(summary, tail=[turn3])` replays to `[summary_message, *turn3]`, NOT the full transcript.
- [x] **Post-compaction turns continue:** `… → compaction(summary, tail) → turn4` replays to
      `[summary_message, *tail, *turn4]`.
- [x] **Successive compactions:** two `compaction` lines replay to the second checkpoint (+ later turns).
- [x] A truncated/garbage `compaction` line is skipped (logged, not raised); replay still yields a valid
      un-compacted history.
- [x] `load_latest` / `resolve_session` work unchanged through a compacted file.
- [x] `tests/unit/decode/context/test_session_log.py` extended; `make ci` green, 0 warnings.

## Out of scope
- Deciding when/what to compact (tasks 042/044).
- Persisting microcompaction (deliberately in-memory only).
- Any SQLite store; changing the `messages`/`session` formats or header version.

## Log

### [SWE] 2026-06-26 22:20 — Implementation

**Files modified**
- `src/decode/context/session_log.py` — added `_COMPACTION_TYPE` discriminator, the
  `SessionLog.append_compaction(summary_message, tail)` writer (one typed `compaction` line,
  both halves via `ModelMessagesTypeAdapter`), `load()` now honors a `compaction` checkpoint
  in file order (discard-and-restart from `[*summary, *tail]`, then continue), and a tolerant
  `_parse_compaction_line` helper mirroring `_parse_messages_line`.
- `tests/unit/decode/context/test_session_log.py` — added an 8-test compaction section + a
  `_summary_message` helper; updated the module docstring (four → five behaviours).

**Tests**
- Unit: 28 passing in `test_session_log.py`, 0 failing (770 unit total). TDD: wrote the 8 new
  tests first, confirmed red (`AttributeError: no append_compaction`), then green.
- Integration: N/A — no infra changes (pure JSONL log logic, no network/SQLite). Full
  `make ci` ran the 2 integration capstones anyway: green.

**Acceptance criteria**
- [x] `append_compaction` writes exactly one `type=="compaction"` line, header + prior turns
      untouched — `test_append_compaction_appends_exactly_one_typed_compaction_line`.
- [x] Compact-then-resume replays compacted history (not full transcript) —
      `test_compact_then_resume_replays_the_compacted_history`.
- [x] Post-compaction turns continue — `test_turns_after_a_compaction_continue_the_compacted_history`.
- [x] Successive compactions land on the second checkpoint —
      `test_successive_compactions_replay_to_the_second_checkpoint`.
- [x] Truncated/garbage `compaction` line skipped (logged, not raised), degrades to
      un-compacted history — `test_load_tolerates_a_truncated_compaction_line` +
      `test_load_tolerates_a_malformed_compaction_payload`.
- [x] `load_latest` / `resolve_session` work through a compacted file —
      `test_load_latest_replays_through_a_compacted_file` +
      `test_resolve_session_finds_and_replays_a_compacted_file`.
- [x] Tests extended; `make ci` green (778 passed), 0 warnings, no network.

**Evidence**
```
$ uv run pytest tests/unit/decode/context/test_session_log.py -q
............................                                             [100%]
28 passed in 0.74s

$ make ci
============================= 778 passed in 7.86s ==============================
```

End-to-end smoke (real `SessionLog`, no network) — `header → turn1 → turn2 → turn3 →
compaction(summary, tail=[turn3]) → turn4` on disk replays to
`[summary, *turn3, *turn4]` (5 msgs; `q1`/`q2` discarded at the checkpoint);
`load_latest` + `resolve_session` resolve and replay the compacted file unchanged:
```
--- raw on-disk lines (types) ---
  session / messages / messages / messages / compaction / messages
--- replayed history: 5 messages ---
  ModelRequest   '# Conversation summary...'
  ModelRequest   'q3' / ModelResponse 'a3'
  ModelRequest   'q4' / ModelResponse 'a4'
load_latest + resolve_session work through the compacted file: OK
```

**Notes**
- NO SQLite, full-compaction only — microcompaction is deliberately not persisted (ADR-0006 §3a),
  out of scope here.
- Tolerance is layered: `_parse_compaction_line` returns `None` silently for non-compaction /
  unparseable lines (the unparseable case is logged once by the second-pass `_parse_messages_line`)
  and logs at debug only when a `compaction`-typed payload is adapter-rejected — one debug log per
  bad line, never raised.
- Left `docs/architecture.png` (pre-existing unrelated working-tree change) untouched/unstaged.
  Not committed — handing off to the Tester.

### [Tester] 2026-06-26 23:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 114 files clean; `ruff check` clean)
- Unit tests: 770 passed / 0 failed (28 in `test_session_log.py`)
- Integration tests: 8 passed / 0 failed (M1 + M3 capstones)
- `make ci` (lock-check + format + lint + full suite): 778 passed / 0 failed
- Warnings: 0 (`filterwarnings=["error"]` would have failed the run otherwise)

**E2E adversarial pass** — hand-built raw JSONL + real `SessionLog` (did not trust SWE tests); 26/26 checks green
- Happy path (compact-then-resume): `header→t1→t2→t3→compaction(s, tail=[t3])` → `load` returns `[s, *t3]` (len 3) (PASS)
- Break 1 (regression-proof length): asserted compacted len 3 != full pre-compaction transcript len 6; q1/q2 verifiably discarded at the checkpoint (PASS)
- Break 2 (state edge — successive compactions): 2 and 3 checkpoints both land on the LAST one; summary_one superseded/gone, tail appears exactly once (no double-count) (PASS)
- Break 3 (tolerance — truncated compaction line MID-file then good turns): broken checkpoint ignored, replay degrades to un-compacted `[*t1, *t2]` (PASS)
- Break 4 (tolerance — garbage non-JSON line): skipped, `[*t1]` survives (PASS)
- Break 5 (tolerance — adapter-rejected summary / adapter-rejected tail / missing `tail` key → KeyError): all skipped, `[*t1]` survives; confirmed one DEBUG log per bad line, `load` returns cleanly, never raised (PASS)
- Break 6 (tolerance — good checkpoint + later turn + half-written tail at EOF): checkpoint honored, t3 kept, corrupt tail dropped → `[s, *tail, *t3]` (PASS)
- Break 7 (boundary — empty tail; compaction as first line after header): replay `[s]` and `[s, *tail]` respectively (PASS)
- Append-only proof: post-`append_compaction` file is a strict byte-prefix extension of the pre-call bytes (header + prior lines byte-unchanged), grows by exactly one `\n`-terminated line (PASS)
- Microcompaction NOT persisted: only `append_turn` + `append_compaction` writers exist; no `append_microcompaction`; `micro` appears solely in a "not persisted" doc note (PASS)

**Acceptance criteria**
- [x] PASS — `append_compaction` writes exactly one `type=="compaction"` line, header/prior turns untouched — `test_append_compaction_appends_exactly_one_typed_compaction_line` + adversarial byte-prefix append-only check; `session_log.py:134-161`
- [x] PASS — compact-then-resume replays compacted history, not full transcript — `test_compact_then_resume_replays_the_compacted_history` + adversarial len-3-vs-6 assertion
- [x] PASS — post-compaction turns continue — `test_turns_after_a_compaction_continue_the_compacted_history` + adversarial [2]
- [x] PASS — successive compactions land on the last checkpoint — `test_successive_compactions_replay_to_the_second_checkpoint` + adversarial 2- and 3-checkpoint cases (no double-count)
- [x] PASS — truncated/garbage compaction line skipped (logged at debug, not raised), degrades to un-compacted — `test_load_tolerates_a_truncated_compaction_line` + `test_load_tolerates_a_malformed_compaction_payload` + adversarial breaks 3-6 (incl. mid-file + KeyError + half-written tail); DEBUG log captured, no exception propagated
- [x] PASS — `load_latest` / `resolve_session` work through a compacted file — `test_load_latest_replays_through_a_compacted_file` + `test_resolve_session_finds_and_replays_a_compacted_file` + adversarial [8]
- [x] PASS — tests extended; `make ci` green, 0 warnings — `make ci` → 778 passed, 0 warnings

**Evidence**
```
$ make ci
============================= 778 passed in 8.06s ==============================
$ uv run pytest tests/unit/decode/context/test_session_log.py -q
28 passed in 0.71s
$ uv run python adv.py   # hand-built JSONL adversarial replay harness
==== adversarial summary: 26 passed, 0 failed ====
```

**Other issues found**
- None blocking. `docs/architecture.png` is a pre-existing unrelated binary working-tree change (last touched at scaffold), correctly left unstaged — not part of this task's diff. Orchestrator/SWE to decide whether it rides along; it must NOT be in the task 043 commit.
- Note (not a defect): the DEBUG log for a rejected compaction uses `exc_info=True`, so a traceback renders at DEBUG level only — appropriate and silent at the default level; behaviour is degrade-not-raise.

**VERDICT: PASS**
