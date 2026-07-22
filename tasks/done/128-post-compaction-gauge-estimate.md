---
id: 128
feature: fix-compaction
status: done
---

# Post-compaction Context Gauge drops immediately via a chars≈/4 estimate of the kept history

After a successful full compaction the footer gauge keeps showing the PRE-compaction token
count until the next model leg reports — the user just paid for a compaction and the gauge
still reads ~80% full. Human-approved decision: on `COMPACTED`, seed `_last_input_tokens`
with the chars≈/4 estimate of the new `[summary, *tail]` history; the next leg's
provider-authoritative number overwrites it. This deliberately softens ADR-0006's "the
estimate never drives the trigger" to "never INFLATES it" — post-compaction the estimate can
only understate, briefly (ADR-0018 §4).

Depends on: 126 (per-leg authoritative gauge semantics), 127 (`CompactOutcome.COMPACTED`).

## Scope

- Expose a public helper in `src/decode/context/compaction.py` (suggested:
  `estimate_history_tokens(messages: list[ModelMessage]) -> int`) summing the existing
  private per-message `_estimate_tokens` — no second estimator, one divisor (`_CHARS_PER_TOKEN`).
- In `AgentTurnHandler.compact()`'s success path (`loop.py:300-305` region): after
  `self.message_history = [summary_message, *tail]`, set
  `self._last_input_tokens = estimate_history_tokens(self.message_history)`.
- Soften the "never the trigger" docstring language to "never inflates the trigger" where it
  now over-claims: `compaction.py` module header (lines 8-9), `_CHARS_PER_TOKEN` comment
  (line 43-44), `_estimate_tokens` docstring, `split_tail` docstring tail note, and the
  gauge/trigger comments in `loop.py`. Reference ADR-0018 §4.

## Acceptance criteria

- [x] Regression test (written first, fails on current code): after a successful `compact()`
      (Model-instance summarizer stub), `handler.last_input_tokens ==
      estimate_history_tokens([summary, *tail])` and is strictly less than the
      pre-compaction value.
- [x] Test: the next leg after a compaction overwrites the estimate with the
      provider-authoritative last-response number (rides task 126's helper).
- [x] Test: `NOTHING_TO_COMPACT` and `SUMMARIZER_FAILED` outcomes leave
      `last_input_tokens` untouched.
- [x] `estimate_history_tokens` unit-tested directly (empty list → 0; known content → known
      chars//4 sum) and used by BOTH the tail sizing and the post-compaction seed (single
      source of truth).
- [x] Docstring language updated everywhere listed in Scope; no remaining claim that the
      estimate "never" feeds the trigger.
- [x] `make format-check lint-check unit-tests` green.

## User stories

### Story: User watches the gauge drop the moment /compact succeeds
1. User's footer gauge shows red (~85% full); user types `/compact` while idle.
2. The `ContextCompacted` line renders AND the footer gauge immediately drops to a small
   green fill — without waiting for the next model request.
3. User sends the next prompt; the gauge updates again to the provider-reported number,
   close to the estimate.

### Story: The estimate can never re-trigger a compaction loop
1. Immediately after compaction the seeded estimate understates or matches real occupancy.
2. The auto cascade at the next would-stop reads the provider-authoritative number (which
   overwrote the estimate) — a compact→trigger→compact loop cannot start from the estimate.

## Out of scope

- Any change to trigger thresholds, reserves, or the window (task 123 / ADR-0006 §3 stand).

## Log

### [SWE] 2026-07-23 00:20 — Implementation

**Files modified**
- `src/decode/context/compaction.py` — added public `estimate_history_tokens(messages)` summing the
  existing per-message `_estimate_tokens` (one divisor, no second estimator); softened the module
  header, `_CHARS_PER_TOKEN` comment, `_estimate_tokens` and `split_tail` docstrings from "never the
  trigger" to "never INFLATES the trigger" (ADR-0018 §4).
- `src/decode/agent/loop.py` — in `compact()`'s success path, after
  `self.message_history = [summary_message, *tail]`, seed
  `self._last_input_tokens = estimate_history_tokens(self.message_history)`; imported the helper;
  softened the `_last_input_tokens` field comment + the `last_input_tokens` property docstring to note
  the post-compaction estimate seed (ADR-0018 §4).
- `tests/unit/decode/context/test_compaction.py` — direct `estimate_history_tokens` tests (empty → 0;
  known content → per-message chars//4 sum; shares `_estimate_tokens` with the tail sizing).
- `tests/unit/decode/agent/test_loop.py` — seed regression test (written first, red on current code),
  next-leg-overwrite test (rides task 126's `_StubRun` seam), and NOTHING_TO_COMPACT /
  SUMMARIZER_FAILED leave-the-gauge-untouched tests.
- `tests/integration/test_compaction_capstone.py` — updated the FULL-turn gauge assertion faithfully:
  the gauge now drops to `estimate_history_tokens([summary, *tail])` the instant compaction lands
  (not the pre-compaction provider 150); the wrap-up turn still asserts the next leg overwrites it.

**Tests**
- Unit: 2214 passing, 0 failing (`make pre-commit` ran the full unit suite). Compaction+loop focus:
  100 passing.
- Integration: 105 passing, 16 skipped (docker daemon unreachable offline — infra-gated). Compaction
  capstone passes.

**Acceptance criteria**
- [x] Regression test first, red on current code (gauge read 0 not 31), green after the seed —
      `tests/unit/decode/agent/test_loop.py::test_compaction_seeds_the_gauge_with_the_kept_history_estimate`
- [x] Next-leg overwrite — `::test_next_leg_overwrites_the_post_compaction_estimate`
- [x] NOTHING_TO_COMPACT / SUMMARIZER_FAILED untouched — `::test_nothing_to_compact_leaves_the_gauge_untouched`,
      `::test_summarizer_failed_leaves_the_gauge_untouched`
- [x] `estimate_history_tokens` direct tests + single source of truth —
      `tests/unit/decode/context/test_compaction.py::test_estimate_history_tokens_empty_is_zero`,
      `::test_estimate_history_tokens_sums_chars_over_four`, `::test_estimate_history_tokens_shares_the_tail_estimator`
- [x] Docstrings softened everywhere listed in Scope (module header, `_CHARS_PER_TOKEN`,
      `_estimate_tokens`, `split_tail`, loop.py gauge/trigger comments) — no remaining "never drives the trigger" claim
- [x] `make format-check lint-check unit-tests` green

**Evidence**
```
$ uv run python scratchpad/e2e_gauge.py   # drive compact() through the real handler
gauge BEFORE /compact: 999999
outcome: COMPACTED
gauge AFTER  /compact: 28 (estimate of 2 kept messages)
dropped: True

$ make pre-commit
... 2214 passed in 117.91s ...

$ make integration-tests
... 105 passed, 16 skipped in 342.97s ...
```

**Notes**
- The seed lands after `self.message_history = [summary_message, *tail]` and before the
  `ContextCompacted` emit; `before_tokens` is captured earlier (line 327) so the event still carries
  the pre-compaction provider number.
- Capstone note: with its contrived tiny window (150) and huge char prompt, the post-compaction
  estimate is not necessarily below 150, so the capstone asserts the faithful equality
  (`== estimate_history_tokens(compacted_history)`) plus `!= _USAGE_FULL`; the "strictly less than
  pre-compaction" property is proven in the dedicated unit regression with controlled numbers.

### [Tester] 2026-07-23 01:25 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make pre-commit` = format-check + lint-check + unit-tests, exit 0)
- Unit tests: 2214 passed / 0 failed
- Integration tests: 105 passed / 16 skipped (docker daemon unreachable, infra-gated — matches SWE claim)
- Warnings: 0 (`filterwarnings=["error"]` in pyproject; any warning would show as a failure — none did)

**E2E adversarial pass**
- Happy path: standalone driver script through the real `build_agent()` handler — `handler.compact()`
  with a Model-instance summarizer stub → gauge `999999` → `43` (== `estimate_history_tokens`), outcome
  `COMPACTED`, `ContextCompacted.before_tokens == 999999` (PASS)
- Break path 1 (event carries pre-compaction number, not the estimate): temp pytest
  `test_break_path_context_compacted_event_carries_pre_compaction_before_tokens` — seeded
  `_last_input_tokens=999999`, called `compact()`, asserted `ContextCompacted.before_tokens == 999999`
  and `handler.last_input_tokens != 999999` → both held (PASS). Also independently confirmed inside
  the real capstone: `tests/integration/test_compaction_capstone.py:391` asserts
  `full_events[0].before_tokens == _USAGE_FULL` (150) even though the gauge itself has already been
  reseeded to the estimate by the time the event is inspected.
- Break path 2 (auto cascade does not immediately re-fire off the seed): temp pytest
  `test_break_path_auto_cascade_does_not_immediately_refire_after_seed` — tiny window (100, full ≥80),
  seeded via `compact()` (estimate landed at 31, under the 80 trigger), then called
  `_maybe_auto_compact()` again immediately with no intervening leg → no `ContextCompacted` refired
  (PASS). Also confirmed by code inspection: `_maybe_auto_compact` is called once per would-stop by
  `Runner`/`AgentTurnHandler` call sites, not recursively after `compact()` returns — no loop wiring
  exists for the estimate to feed back into.
- Break path 3 (`/clear` still zeroes the gauge post-seed): temp pytest
  `test_break_path_clear_zeroes_gauge_after_compaction_seed` — seeded the gauge via `compact()`
  (non-zero), called `handler.clear()`, asserted `last_input_tokens == 0` and `message_history == []`
  (PASS) — `clear()` unconditionally sets `_last_input_tokens = 0` (`loop.py:368`), untouched by this
  change.
- Break path 4 (empty/near-empty tail edge): temp pytest `test_break_path_empty_summary_tail_edge` —
  `compaction_keep_recent_tokens` forced huge so `split_tail` returns `len(messages)` (whole history
  droppable) — outcome landed `NOTHING_TO_COMPACT` for this fixture (guard `split == 0` check fires
  first), no crash; the `estimate_history_tokens([]) == 0` case is directly unit-tested in
  `test_compaction.py::test_estimate_history_tokens_empty_is_zero` (PASS).
- Break path 5 (state edge — double `/compact` in a row): temp pytest
  `test_break_path_double_compact_in_a_row` — called `compact()` twice back-to-back on the same
  handler; second call returned `COMPACTED` again (small `keep_recent_tokens=10` still left a
  droppable prefix) with a consistent re-seeded gauge, no crash, no corrupted state (PASS).
- Independent regression-test-first verification: `git stash push -- src/decode/agent/loop.py
  src/decode/context/compaction.py` (full revert of both src files) → collection-level
  `AttributeError: module 'decode.context.compaction' has no attribute 'estimate_history_tokens'`
  (expected, since both new-test files reference the new symbol). Re-did with ONLY `loop.py` stashed
  (keeping `compaction.py`'s new `estimate_history_tokens`, isolating exactly the missing seed line):
  both new regression tests failed with `AssertionError: assert 999999 == 31` and
  `assert 0 == 31` — i.e. the gauge held the stale pre-compaction number (or the handler default `0`)
  instead of the estimate, matching the SWE's claimed red reason. `git stash pop` restored the working
  tree exactly (`git status --short` identical before/after). Green confirmed again post-restore: the
  7 new/changed tests (`test_loop.py` 4 + `test_compaction.py` 3) all pass with the fix in place.

**Acceptance criteria**
- [x] PASS — Regression test (written first, fails on current code) —
      `tests/unit/decode/agent/test_loop.py::test_compaction_seeds_the_gauge_with_the_kept_history_estimate`
      passes with the fix; independently reproduced red (`999999 == 31` failure) with only `loop.py`
      stashed back to its pre-fix state.
- [x] PASS — Next leg overwrites the estimate with the provider-authoritative number —
      `::test_next_leg_overwrites_the_post_compaction_estimate` passes; rides task 126's `_StubRun`
      seam, asserts `last_input_tokens == 4242` and `!= seeded`.
- [x] PASS — `NOTHING_TO_COMPACT` / `SUMMARIZER_FAILED` leave `last_input_tokens` untouched —
      `::test_nothing_to_compact_leaves_the_gauge_untouched`,
      `::test_summarizer_failed_leaves_the_gauge_untouched` both pass.
- [x] PASS — `estimate_history_tokens` unit-tested directly (empty → 0; known content → known
      chars//4 sum) and single source of truth for both tail sizing and the seed —
      `tests/unit/decode/context/test_compaction.py::test_estimate_history_tokens_empty_is_zero`,
      `::test_estimate_history_tokens_sums_chars_over_four`,
      `::test_estimate_history_tokens_shares_the_tail_estimator` all pass; code inspection confirms
      `split_tail` and `estimate_history_tokens` both call the same `_estimate_tokens` (one divisor,
      `_CHARS_PER_TOKEN`), no second estimator.
- [x] PASS — Docstring language softened everywhere listed in Scope, no remaining unqualified "never
      the trigger" claim — `grep -rn "never the trigger\|never drives the trigger" src/ docs/
      tasks/128*.md` shows every remaining occurrence is a deliberate quoted contrast ("softens
      ADR-0006's stronger 'the estimate never drives the trigger'"), never an unqualified current
      claim; confirmed by reading `compaction.py:1-13,43-44` (module header, `_CHARS_PER_TOKEN`),
      `compaction.py:267-292` (`_estimate_tokens`, `split_tail`), `loop.py:113-135` (field comment,
      `last_input_tokens` property docstring) — all touched, all say "never INFLATES".
- [x] PASS — `make format-check lint-check unit-tests` green — `make pre-commit` exit 0, 2214 passed.

**Evidence**
```
$ make pre-commit
... 2214 passed in 117.70s (0:01:57) ...

$ make integration-tests
... 105 passed, 16 skipped in 363.61s (0:06:03) ...

$ git stash push -m tester-loop-only-stash -- src/decode/agent/loop.py
$ uv run pytest tests/unit/decode/agent/test_loop.py -k "seed or overwrite" -q
...
E       AssertionError: assert 999999 == 31
E       AssertionError: assert 0 == 31
2 failed, 52 deselected in 1.15s
$ git stash pop   # working tree restored exactly

$ uv run python <standalone happy-path driver, real build_agent()>
gauge BEFORE /compact: 999999
outcome: CompactOutcome.COMPACTED
gauge AFTER /compact: 43 estimate: 43
ContextCompacted.before_tokens: 999999
ALL HAPPY-PATH ASSERTIONS PASSED
```

**Other issues found**
- No stray scratch files in the working tree — `git status --short` shows only the 6 task-relevant
  files (`src/decode/agent/loop.py`, `src/decode/context/compaction.py`,
  `tasks/128-post-compaction-gauge-estimate.md`, `tests/integration/test_compaction_capstone.py`,
  `tests/unit/decode/agent/test_loop.py`, `tests/unit/decode/context/test_compaction.py`) — the SWE's
  `scratchpad/e2e_gauge.py` driver was never committed to the repo tree.
- Minor doc quality nit (not blocking): `estimate_history_tokens`'s docstring in
  `src/decode/context/compaction.py` has a garbled first sentence — "the sum of the per-message
  `_estimate_tokens` `split_tail` sizes the tail with (ONE divisor, no second estimator)" reads as two
  merged, unpunctuated clauses. Functionally harmless (ruff doesn't lint docstring grammar), but worth
  a follow-up cleanup pass for readability.
- `code-review` plugin is enabled in `.claude/settings.json` but this Tester's toolset has no
  mechanism to invoke a Claude Code slash-command subagent directly; a manual line-by-line diff review
  substituted (see the diff walkthrough above) — no correctness/security issues found in that review.

**VERDICT: PASS**
