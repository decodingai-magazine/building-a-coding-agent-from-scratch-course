---
id: 127
feature: fix-compaction
status: done
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

- [x] `compact()` returns `CompactOutcome`; the three mappings above each covered by a unit
      test (failed summarizer simulated with a Model that raises — the Model-instance seam).
      Verified by `test_loop.py::test_compact_returns_nothing_to_compact_on_trivial_history`,
      `::test_compact_returns_summarizer_failed_when_the_call_raises`,
      `::test_compact_returns_summarizer_failed_when_summary_is_blank`, `::test_no_repersist_after_full_compaction`.
- [x] `/compact` on an empty/short session prints the nothing-to-compact line (unchanged UX) —
      `test_app.py::test_handle_compact_command_idle_nothing_to_compact_renders_line`.
- [x] Regression test (written first, fails on current code): `/compact` with compactable
      history but a failing summarizer prints a DISTINCT line containing
      `.decode/logs/decode.log` — not "nothing to compact yet".
      `test_app.py::test_handle_compact_command_idle_summarizer_failed_names_the_log`.
- [x] `/compact` success path prints no extra inline line; `ContextCompacted` renders as today —
      `test_app.py::test_handle_compact_command_idle_compacted_emits_no_extra_line`.
- [x] Auto path: full trigger fired + `SUMMARIZER_FAILED` → exactly one INFO log line naming
      the outcome (caplog test); micro fired + zero elided → one INFO line.
      `test_loop.py::test_auto_full_trigger_fired_but_failed_logs_one_info_line`,
      `::test_auto_micro_trigger_fired_but_zero_elided_logs_one_info_line`.
- [x] No new user-facing term drift: user-visible copy says "compaction", matching the
      glossary; the enum name `CompactOutcome` and glossary term **Compaction Outcome** align.
- [x] `make format-check lint-check unit-tests` green.

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

### [SWE] 2026-07-23 00:55 — Implementation

**Files modified**
- `src/decode/context/compaction.py` — new `CompactOutcome` enum (COMPACTED / NOTHING_TO_COMPACT / SUMMARIZER_FAILED); the ONE definition site (ADR-0018 §3, glossary "Compaction Outcome").
- `src/decode/agent/loop.py` — `compact() -> CompactOutcome` (split==0 → NOTHING_TO_COMPACT first, `None` summary → SUMMARIZER_FAILED, success → COMPACTED); `_maybe_auto_compact` logs ONE INFO line when full fired but outcome != COMPACTED, and one INFO line when micro fired but elided == 0; `_microcompact() -> int` returns elided count.
- `src/decode/tui/app.py` — `_handle_compact_command` maps the three outcomes to distinct lines; new `_COMPACT_SUMMARIZER_FAILED` naming `.decode/logs/decode.log`; COMPACTED stays event-rendered, busy line unchanged.
- `tests/unit/decode/agent/test_loop.py` — three outcome-mapping tests + two caplog tests for the auto-path INFO lines; `_raising_summarizer` helper; updated the success assertion.
- `tests/unit/decode/tui/test_app.py` — updated the three `_handle_compact_command` tests to `CompactOutcome`; added the SUMMARIZER_FAILED regression test.
- `tests/unit/decode/tui/test_app_e2e.py` — stale docstring updated (no behavior change).
- `tests/unit/evals/regression/test_cases_grounding.py` — updated the bool assertion to `CompactOutcome.COMPACTED`.

**Tests**
- Unit: 2207 passing, 0 failing — `make unit-tests`.
- Integration: 105 passing, 16 skipped (docker daemon unavailable — expected), 0 failing — `make integration-tests`.

**Acceptance criteria** — all met (see checkboxes above); `[HUMAN]`: none.

**Evidence**
```
$ make unit-tests
======================= 2207 passed in 119.46s (0:01:59) =======================

$ make integration-tests
================= 105 passed, 16 skipped in 382.28s (0:06:22) ==================

$ make format-check && make lint-check
308 files already formatted
All checks passed!

# end-to-end smoke: /compact with compactable history + a failing (raising) summarizer
SMOKE_OUTPUT_LINES: ['Decode - compaction summarizer failed; see .decode/logs/decode.log.']
```

**Red→green regression evidence**
- Key regression `test_app.py::test_handle_compact_command_idle_summarizer_failed_names_the_log`
  and the loop mapping tests were written FIRST. On current code they could not even run
  (`ImportError: cannot import name 'CompactOutcome'`) — the bool `compact()` had no way to
  express SUMMARIZER_FAILED, so `/compact` on a failing summarizer printed "nothing to compact
  yet". After the enum + mapping landed, all pass and the line now names `.decode/logs/decode.log`.

**Notes**
- Blank summary (`split > 0` but empty output) maps to SUMMARIZER_FAILED per ADR-0018 §3 (a
  non-trivial transcript that produced nothing is a failure, not a no-op) — the existing
  "summary is None" test was renamed/repurposed accordingly.
- The capstone integration test does not assert `compact()`'s return; checked, no change needed.

### [Tester] 2026-07-23 00:56 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check` — 308 files already formatted;
  `make lint-check` — all checks passed; `make pre-commit` — 2207 passed in 117.45s)
- Unit tests: 2207 passed / 0 failed
- Integration tests: 105 passed / 0 failed (16 skipped — docker daemon unreachable, expected/pre-existing)
- Warnings: 0 (`filterwarnings = ["error"]` in `pyproject.toml:120` — any warning would have failed the suite)

**Independent regression-test-first verification**
- `git stash push --keep-index` on the three src hunks only (`loop.py`, `compaction.py`,
  `app.py`), tests left in place. Reproduced the claimed red:
  - `uv run pytest tests/unit/decode/tui/test_app.py -k "summarizer_failed or nothing_to_compact or compacted_emits_no_extra"`
    → `ImportError: cannot import name 'CompactOutcome' from 'decode.context.compaction'`
    (matches SWE's claimed red-first evidence exactly).
  - `uv run pytest tests/unit/decode/agent/test_loop.py -k "outcome or raises or auto_full_trigger or auto_micro_trigger"`
    → 3 failed: the raising-summarizer test propagated an uncaught `RuntimeError` (old
    `compact()` had no outcome mapping), the two caplog tests asserted `len(info) == 1` and got
    `0` (old code logs nothing on a fired-but-failed/zero-elided auto tier).
  - `git stash pop` restored the working tree; re-ran the same `-k` selection → 3 passed.

**E2E adversarial pass** (real `AgentTurnHandler` + real `tui.app._handle_compact_command`,
no mocks on the object under test — script at
`/private/tmp/claude-501/.../scratchpad/e2e_compact.py`, FunctionModel/TestModel seam, no network)
- Happy path: `/compact` on a 4-message history with `keep_recent_tokens` patched small +
  a working summarizer → `lines == []`, `ContextCompacted` emitted, history 4→2 messages (PASS)
- Break path 1 (state edge — busy mid-turn): `runner.phase = RUNNING` then `_handle_compact_command`
  → `lines == ["Decode - busy; try /compact again once the turn finishes."]`,
  `handler.compact` call count `== 0` (spied) — compact() is genuinely never invoked, not just
  its result ignored (PASS)
- Break path 2 (failure-mode equivalence — blank vs raising summarizer): ran `/compact` against
  `TestModel(custom_output_text="   ")` and a `FunctionModel` whose call raises `RuntimeError` —
  both produced the identical line `"Decode - compaction summarizer failed; see
  .decode/logs/decode.log."`, never "nothing to compact yet" (PASS)
- Break path 3 (both auto tiers eligible by threshold): `_last_input_tokens` set above BOTH the
  full (80) and micro (60) reserve levels with a raising summarizer → captured log output
  contains exactly ONE INFO line (`"full compaction trigger fired but did not land:
  outcome=SUMMARIZER_FAILED"`), zero micro lines — confirms the pre-existing `if full: ... elif
  micro:` exclusivity holds under the new logging (PASS)
- Break path 4 (auto success emits no breadcrumb): same setup as break path 3 but with a working
  summarizer → captured INFO log output `== ""` — `COMPACTED` on the auto path adds nothing extra
  (PASS)

**Acceptance criteria**
- [x] PASS — `compact()` returns `CompactOutcome`; three mappings each unit-tested (raising +
      blank summarizer seams) — `test_loop.py::test_compact_returns_nothing_to_compact_on_trivial_history`,
      `::test_compact_returns_summarizer_failed_when_the_call_raises`,
      `::test_compact_returns_summarizer_failed_when_summary_is_blank`,
      `::test_no_repersist_after_full_compaction` all pass; independently reproduced e2e above.
- [x] PASS — `/compact` on empty/short session prints the nothing-to-compact line unchanged —
      `test_app.py::test_handle_compact_command_idle_nothing_to_compact_renders_line` passes;
      `test_app_e2e.py::test_run_app_compact_with_nothing_to_compact_is_a_friendly_line` passes.
- [x] PASS — Regression test (written first, red on current code): `/compact` with compactable
      history + failing summarizer prints a distinct line naming `.decode/logs/decode.log`, not
      "nothing to compact yet" — `test_app.py::test_handle_compact_command_idle_summarizer_failed_names_the_log`
      passes; independently reproduced red (`ImportError`) via `git stash`, see above.
- [x] PASS — `/compact` success prints no extra inline line, `ContextCompacted` renders as today —
      `test_app.py::test_handle_compact_command_idle_compacted_emits_no_extra_line` passes;
      confirmed live in the e2e happy-path scenario (`lines == []`).
- [x] PASS — Auto path: full fired + `SUMMARIZER_FAILED` → exactly one INFO line naming the
      outcome; micro fired + zero elided → one INFO line —
      `test_loop.py::test_auto_full_trigger_fired_but_failed_logs_one_info_line`,
      `::test_auto_micro_trigger_fired_but_zero_elided_logs_one_info_line` pass; independently
      reproduced red pre-fix (0 INFO lines) via `git stash`, and confirmed live (break paths 3-4
      above) that exactly one line fires when both tiers would be eligible, and none on success.
- [x] PASS — No term drift: `_COMPACT_SUMMARIZER_FAILED` copy reads "compaction summarizer
      failed" (no enum jargon leaked to the user); `docs/glossary.md:19` defines **Compaction
      Outcome** with the same three values, landed in the planning commit `f348ea2`.
- [x] PASS — `make format-check lint-check unit-tests` green (see Test summary above).

**Evidence**
```
$ make unit-tests   (via make pre-commit)
======================= 2207 passed in 117.45s (0:01:59) =======================

$ make integration-tests
================= 105 passed, 16 skipped in 331.98s (0:05:31) ==================

$ make format-check && make lint-check
308 files already formatted
All checks passed!

$ git stash push --keep-index -- src/decode/agent/loop.py src/decode/context/compaction.py src/decode/tui/app.py
$ uv run pytest tests/unit/decode/tui/test_app.py -k "summarizer_failed or nothing_to_compact or compacted_emits_no_extra" -q
ImportError: cannot import name 'CompactOutcome' from 'decode.context.compaction'
$ uv run pytest tests/unit/decode/agent/test_loop.py -k "outcome or raises or auto_full_trigger or auto_micro_trigger" -q
3 failed, 47 deselected in 1.90s
$ git stash pop   # restored, re-ran same -k selection → 3 passed
```

**Other issues found**
- None. `grep -n "print(" src/decode/context/compaction.py src/decode/agent/loop.py
  src/decode/tui/app.py` finds only `console.print` (Rich, user-facing CLI output — allowed);
  library code uses `logger.info`/`logger.warning` exclusively. All new/changed signatures fully
  typed including `-> CompactOutcome` / `-> int` / `-> None`. `if full: ... elif micro:` in
  `_maybe_auto_compact` (pre-existing control flow) structurally guarantees the two auto-path INFO
  breadcrumbs are mutually exclusive — confirmed live, not just by code inspection.
- `code-review` plugin is enabled but its `/code-review` command is GitHub-PR-specific (`gh pr
  diff`/`gh pr comment`); this task is file-tracker mode with no PR yet, so it was not invocable.
  Performed the equivalent manual checks (CLAUDE.md compliance, bug scan, comment compliance)
  above instead.

**VERDICT: PASS**


### [PA] 2026-07-23 — Acceptance Review (feature fix-compaction, PR #50)

**VERDICT: ACCEPT**

Walked the whole feature from the user's perspective against the Tasks Plan (tasks 125-130,
ADR-0018): the original single-long-turn session shape now auto-compacts (capstone 4/4 green,
re-run); the Context Gauge reads the last response's usage and drops to the kept-history
estimate the instant compaction lands (footer reads `handler.last_input_tokens`, app.py:587);
`/compact` gives three honest distinct lines (failure copy names `.decode/logs/decode.log`,
no enum jargon leaked); all three providers summarize via `compaction_model=agent.model`
(wiring test gemini/openrouter/modal green, re-run); glossary terms (Compaction Boundary /
Compaction Outcome / Context Gauge) and ADR-0018 land verbatim-consistent with code.
Non-blocking nit noted for a future cleanup: stale "user-turn boundary, ADR-0006 §5" comment
at tests/integration/test_compaction_capstone.py:413. Hand off to the PR Reviewer.
