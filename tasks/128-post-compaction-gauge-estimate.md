---
id: 128
feature: fix-compaction
status: pending
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

- [ ] Regression test (written first, fails on current code): after a successful `compact()`
      (Model-instance summarizer stub), `handler.last_input_tokens ==
      estimate_history_tokens([summary, *tail])` and is strictly less than the
      pre-compaction value.
- [ ] Test: the next leg after a compaction overwrites the estimate with the
      provider-authoritative last-response number (rides task 126's helper).
- [ ] Test: `NOTHING_TO_COMPACT` and `SUMMARIZER_FAILED` outcomes leave
      `last_input_tokens` untouched.
- [ ] `estimate_history_tokens` unit-tested directly (empty list → 0; known content → known
      chars//4 sum) and used by BOTH the tail sizing and the post-compaction seed (single
      source of truth).
- [ ] Docstring language updated everywhere listed in Scope; no remaining claim that the
      estimate "never" feeds the trigger.
- [ ] `make format-check lint-check unit-tests` green.

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
