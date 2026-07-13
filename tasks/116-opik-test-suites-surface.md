---
id: 116
feature: evals
status: pending
---

# Regression surface (b): Opik 2.0 Test Suites

Depends on: 111 (+ probes for material). Implements ADR-0017 §6.

## Scope

The second, contrasting regression surface — natural-language assertions instead of code metrics —
over a SUBSET (~5) of the most judge-flavored probes (17, 18, 19, and two others the SWE picks):

**`evals/harness/test_suite.py`** + `python -m evals suite`:

- `opik.Opik().get_or_create_test_suite("decode-regression-suite", global_assertions=[...])` with
  natural-language quality bars ("the response never invents a file that does not exist", "the
  response follows the requested template sections", …); item-level assertions where a probe needs
  its own.
- Task adapter: reuse `regression_task_fn` outputs shaped for `opik.run_tests(test_suite=suite,
  task=...)`; keep judge-visible `input`/`output` clean (the docs warn: leaking expected answers
  into `input` lets the judge cheat).
- Run: `result = opik.run_tests(...)`; print and assert `result.pass_rate` against one suite-level
  bar; exit non-zero below it.

Docs paragraph in `evals/regression/README.md` contrasting the two surfaces (deterministic code
metrics + thresholds vs natural-language assertions) — that contrast is the teaching point.

**Tests**: suite construction + adapter shaping with the opik client mocked; pass_rate gate logic.

## Acceptance Criteria

- [ ] `python -m evals suite` builds/reuses the suite, runs it, prints pass rate, and gates on the
      bar (spot-run with real keys; logged).
- [ ] Assertions never receive expected answers via `input`.
- [ ] Offline unit tests for construction/adapter; `make ci` green.

## Out of scope

- Migrating all 20 probes to Test Suites (5-ish is the point — two surfaces, one lesson).

## Log
