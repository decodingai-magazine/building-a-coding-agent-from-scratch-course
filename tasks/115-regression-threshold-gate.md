---
id: 115
feature: evals
status: pending
---

# Regression surface (a): pytest threshold gate + baseline compare

Depends on: 111–114. Implements ADR-0017 §6,9.

## Scope

**`evals/regression/test_thresholds.py`** — the pre-merge ritual module, DELIBERATELY outside
pytest's `testpaths` (never collected by `make test`/`make ci`; invoked explicitly by
`make eval-regression` → `uv run pytest evals/regression/test_thresholds.py`):

- Runs `run_regression()` once (session-scoped fixture), then asserts
  `evaluation.aggregate_evaluation_scores()` meets a per-metric threshold table (one readable
  constant dict at the top of the module — thresholds ARE the contract; start honest, e.g. tool-
  discipline ≥ 0.8, judges ≥ 0.7, tune from the first real runs and record the chosen numbers in
  the task log).
- Baseline compare: fetch the previous experiment via
  `opik.Opik().get_experiments_by_name(...)` and WARN (not fail) on regressions vs baseline while
  the absolute thresholds are the hard gate — keeps the ritual usable on day one.
- Skips gracefully (clear reason) when `GEMINI_API_KEY` / `OPIK_API_KEY` are absent.

**Tests** (`tests/unit/evals/`): threshold assertion logic + baseline-compare logic factored into
pure helpers and unit-tested with crafted score dicts (the pytest module itself stays thin).

## Acceptance Criteria

- [ ] `uv run pytest evals/regression/test_thresholds.py` runs the suite and gates on thresholds;
      plain `pytest` / `make ci` never collects it.
- [ ] Missing keys → skip with a friendly reason, exit 0.
- [ ] Baseline compare surfaces per-metric deltas vs the last experiment by name.
- [ ] Helper logic unit-tested offline; `make ci` green.

## Out of scope

- CI wiring (documented as "CI-pointable later", 120). Test Suites surface (116).

## Log
