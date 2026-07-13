---
id: 107
feature: evals
status: pending
---

# Trials, pass@k / pass^k / flakiness, cost-normalized aggregates

Depends on: 106. Implements ADR-0017 §8.

## Scope

**`evals/harness/aggregates.py`** — pure functions over Opik test results (all unit-testable on
crafted result lists; handle empty/missing scores gracefully):

- Per task + suite-level: `pass_at_1` (mean of trial passes), `pass_at_k` (≥1 of k trials passed),
  `pass_hat_k` (ALL k trials passed — the reliability bar), `flakiness_rate`
  (tasks with 0 < passes < k, over tasks attempted).
- Cost-normalized: `success_per_dollar`, mean cost + tokens per task — token counts from the task
  fn's recorded usage (driver, task 103); dollar cost from the Opik trace `total_cost` where
  available, else a documented tokens-only fallback.

**Wiring** (`evals/harness/benchmark.py`): `--trials k` (default 1) → `evaluate(trial_count=k)`;
the aggregates ride `experiment_scoring_functions=[...]` so they land on the experiment row in the
Opik UI (sortable/filterable); also printed as a Rich summary table at the end of
`python -m evals benchmark`.

**Tests**: each aggregate against hand-built trial matrices (all-pass, all-fail, mixed, k=1
degenerate, empty); `trial_count` + `experiment_scoring_functions` forwarding with `evaluate`
mocked.

## Acceptance Criteria

- [ ] `python -m evals benchmark --trials 3` runs k trials per item and the experiment carries
      pass@1 / pass@3 / pass^3 / flakiness + cost aggregates.
- [ ] All aggregate math unit-tested including edge cases (empty results never raise).
- [ ] Suite summary table prints pass rates + cost per task.
- [ ] `make ci` green.

## Out of scope

- best@k with verifier, majority vote (non-goals).

## Log
