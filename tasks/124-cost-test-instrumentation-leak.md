---
id: 124
feature: observability
status: in-progress
---

# Fix global pydantic-ai instrumentation leak in the cost-annotation test

`tests/unit/decode/observability/test_cost.py::test_a_real_agent_run_prices_the_model_span_and_only_the_model_span`
(added in `6a67519`, "fix(observability): report LLM cost to Opik for all three providers" — itself a
scope leak onto `feat/dynamic-context-window`, flagged by the task-123 Tester as belonging on its own
branch) calls `logfire.instrument_pydantic_ai()` with no `obj=`, which sets the process-global
`pydantic_ai.Agent._instrument_default`. Every other test in the repo that does this
(`test_flow_tracing.py`, `test_observability_capstone.py`, `test_opik_headless_trace.py`,
`test_opik_repl_trace.py`) saves and restores that class attribute around the call; this one did not.

## Symptom

PR #46 CI (`gh run view 29765732996 --log-failed`) failed a full `uv run pytest` (unit + integration,
one process) with 4 failures never seen on `main`:

```
FAILED tests/integration/test_observability_capstone.py::test_untraced_turn_is_a_noop_zero_spans_and_byte_identical_events - AssertionError: an untraced turn must emit no spans
FAILED tests/integration/test_opik_headless_trace.py::test_inactive_bypass_run_emits_zero_spans_and_returns_the_same_output - AssertionError: an inactive run must emit no spans
FAILED tests/integration/test_opik_headless_trace.py::test_inactive_hitl_run_emits_zero_spans_and_returns_the_same_output - AssertionError: an inactive HITL run must emit no spans
FAILED tests/integration/test_opik_repl_trace.py::test_inactive_turn_emits_zero_spans - AssertionError: an inactive turn must emit no spans
```

`tests/unit/decode/observability/test_cost.py` collects and runs before all four of those (unit before
integration; alphabetical module order), leaving `Agent._instrument_default` set for the rest of the
process — every `Agent(...)` built afterwards, including ones under tests that explicitly assert "no
tracing", got instrumented and started emitting spans through whatever tracer provider was active at
that point.

`gh run list --branch main --limit 5` was all-red too, but for two DIFFERENT, older reasons (opik 2.x's
`TestCase.scoring_inputs` removal and a hardcoded `1.9.8` version string) that `b5aaed1` — already on
this branch — had already fixed. `tests/integration/test_milestone3_skills_capstone.py`'s 4 commit-body
failures are also pre-existing on `main` (independently reproduced by the task-123 Tester on a bare
`abf31e5` worktree) and are unrelated to this task; left untouched.

## Root cause

Missing save/restore of `pydantic_ai.Agent._instrument_default` around
`logfire.instrument_pydantic_ai()` in one test, breaking test isolation for every test that runs later
in the same `pytest` process.

## Fix

Added a `_restore_instrumentation` fixture to `test_cost.py`, mirroring the existing pattern in
`test_flow_tracing.py` / `test_observability_capstone.py` / `test_opik_headless_trace.py` /
`test_opik_repl_trace.py`: save `Agent._instrument_default` before the test, restore it via
`Agent.instrument_all(prior)` in a `finally`. Applied to
`test_a_real_agent_run_prices_the_model_span_and_only_the_model_span`, the one test in the file that
calls `logfire.instrument_pydantic_ai()`.

## Acceptance criteria

- [x] `uv run pytest` (unit + integration, one process, matching CI's `make ci`) no longer fails the
      4 span-isolation tests.
- [x] No application behavior changed — test-only fix.
- [x] `make format-check lint-check unit-tests` clean.

## Out of scope

- `tests/integration/test_milestone3_skills_capstone.py`'s 4 pre-existing commit-skill-body failures —
  present on `main` before this branch existed; not introduced by any commit here.
- Moving `b5aaed1` / `6a67519` off `feat/dynamic-context-window` onto their own branches, as the
  task-123 Tester recommended — a branch-hygiene decision for the orchestrator, not a CI-green fix.

## Log

### [On-Call] 2026-07-20 21:30 — CI failure diagnosed and fixed

**Failed step:** `CI` workflow → `ci` job → `Run make ci` (`uv run pytest`).

**Error** (from `gh run view 29765732996 --log-failed`)
```
FAILED tests/integration/test_observability_capstone.py::test_untraced_turn_is_a_noop_zero_spans_and_byte_identical_events - AssertionError: an untraced turn must emit no spans
FAILED tests/integration/test_opik_headless_trace.py::test_inactive_bypass_run_emits_zero_spans_and_returns_the_same_output - AssertionError: an inactive run must emit no spans
FAILED tests/integration/test_opik_headless_trace.py::test_inactive_hitl_run_emits_zero_spans_and_returns_the_same_output - AssertionError: an inactive HITL run must emit no spans
FAILED tests/integration/test_opik_repl_trace.py::test_inactive_turn_emits_zero_spans - AssertionError: an inactive turn must emit no spans
```

**Root cause:** `test_cost.py`'s real-agent test calls `logfire.instrument_pydantic_ai()`, which sets
the process-global `Agent._instrument_default = True`, and never restores it — every test later in the
same `pytest` process builds instrumented agents and emits spans, tripping the four "must emit zero
spans" assertions.

**Fix:** added a save/restore fixture around the one offending test in
`tests/unit/decode/observability/test_cost.py`, matching the existing pattern used in three other test
modules that make the same global call.

**Verification:**
- `uv run pytest -q` (full suite, matches `make ci`'s `make test`): `4 failed, 2302 passed` before the
  fix (the 4 span-isolation tests above); after the fix, only the 4 pre-existing
  `test_milestone3_skills_capstone.py` failures remain (`4 failed, 2302 passed` → confirmed identical
  to `main`'s pre-existing failure set, unrelated to this branch).
- `uv run pytest tests/unit -q`: `2185 passed` (matches the pre-fix local baseline; no unit regression).
- `make format-check lint-check`: clean.

Confirmed `main`'s own CI (`gh run list --branch main`) is independently red for unrelated,
already-fixed-on-this-branch reasons (opik 2.x `TestCase.scoring_inputs`, hardcoded `1.9.8` version
string) plus the same pre-existing skills-capstone failures — so this branch's CI cannot reach fully
green without also touching that pre-existing, out-of-scope bug. Filing that separately rather than
fixing it here.
</content>
