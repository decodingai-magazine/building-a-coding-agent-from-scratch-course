---
id: 127
feature: fix-compaction
status: pending
---

# `compact()` returns a three-valued CompactOutcome; `/compact` and the auto path surface it

`/compact` today reads as a no-op: `AgentTurnHandler.compact()` (`src/decode/agent/loop.py:273-305`)
returns a bare bool, so the TUI (`src/decode/tui/app.py:323-339`) prints the same
"nothing to compact yet" line whether there was truly nothing OR the summarizer call failed;
the auto path (`_maybe_auto_compact`) swallows every non-success silently.

This task implements ADR-0018 §3. Depends on: none (rebases cleanly on 125/126; updates the
existing bool-asserting tests).

## Scope

- New enum **`CompactOutcome`** — `COMPACTED` / `NOTHING_TO_COMPACT` / `SUMMARIZER_FAILED` —
  defined in `src/decode/context/compaction.py` (shared vocabulary importable by both
  `agent/loop.py` and `tui/app.py` with today's import direction; SWE may place it in
  `loop.py` instead if that reads cleaner — keep it ONE place).
- `AgentTurnHandler.compact() -> CompactOutcome`:
  - `split == 0` → `NOTHING_TO_COMPACT` (still checked FIRST — a no-op never spends a
    summarizer call).
  - summarizer returned `None` (call failed or blank; `split > 0` implies a non-trivial
    transcript) → `SUMMARIZER_FAILED`.
  - success → `COMPACTED` (checkpoint + cursor + `ContextCompacted` event unchanged).
- `_handle_compact_command` (`tui/app.py:323-339`) prints one distinct friendly line per
  outcome: `COMPACTED` → nothing extra (the `ContextCompacted` event stays the feedback);
  `NOTHING_TO_COMPACT` → the existing `_COMPACT_NOTHING` line; `SUMMARIZER_FAILED` → a new
  line that NAMES `.decode/logs/decode.log` (e.g. "Decode - compaction summarizer failed;
  see .decode/logs/decode.log."). Busy line unchanged.
- `_maybe_auto_compact` (`loop.py:244-271`): when the FULL trigger fired but the outcome is
  not `COMPACTED`, log exactly ONE INFO line naming the outcome. Same discipline for the
  micro tier: micro trigger fired but `elided == 0` → one INFO line. (Library code: logger
  only, never print — house rule.)
- Update every existing test asserting `compact()`'s bool (`tests/unit/decode/agent/test_loop.py`,
  `tests/unit/decode/tui/test_app.py`, integration capstone if it asserts the return).

## Acceptance criteria

- [ ] `compact()` returns `CompactOutcome`; the three mappings above each covered by a unit
      test (failed summarizer simulated with a Model that raises — the Model-instance seam).
- [ ] `/compact` on an empty/short session prints the nothing-to-compact line (unchanged UX).
- [ ] Regression test (written first, fails on current code): `/compact` with compactable
      history but a failing summarizer prints a DISTINCT line containing
      `.decode/logs/decode.log` — not "nothing to compact yet".
- [ ] `/compact` success path prints no extra inline line; `ContextCompacted` renders as today.
- [ ] Auto path: full trigger fired + `SUMMARIZER_FAILED` → exactly one INFO log line naming
      the outcome (caplog test); micro fired + zero elided → one INFO line.
- [ ] No new user-facing term drift: user-visible copy says "compaction", matching the
      glossary; the enum name `CompactOutcome` and glossary term **Compaction Outcome** align.
- [ ] `make format-check lint-check unit-tests` green.

## User stories

### Story: User with a Modal-only setup learns WHY /compact "did nothing"
1. User (no `GEMINI_API_KEY`, summarizer failing — pre-task-129 world) types `/compact` on a
   long session.
2. Before: "Decode - nothing to compact yet." (a lie). After: "Decode - compaction summarizer
   failed; see .decode/logs/decode.log."
3. User opens that log, sees the warning with the traceback, and can act.

### Story: Operator diagnoses a silent auto-compaction failure from the log
1. A long session crosses the full threshold at would-stop; the summarizer call fails.
2. `.decode/logs/decode.log` gains ONE INFO line: trigger fired, outcome SUMMARIZER_FAILED.
3. No user-facing interruption — the turn completes normally (degrade-don't-break stands).

## Out of scope

- Retrying a failed summarizer call; changing `summarize_for_compaction`'s never-raise
  contract (ADR-0006 §4 stands).
- The gauge estimate on success (task 128) and the summarizer's model (task 129).

## Log
