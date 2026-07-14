---
id: 107
feature: evals
status: done
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

- [x] `python -m evals benchmark --trials 3` runs k trials per item and the experiment carries
      pass@1 / pass@3 / pass^3 / flakiness + cost aggregates. — `--trials` → `evaluate(trial_count=k)`
      (`test_benchmark.py::test_run_benchmark_forwards_the_trial_count`); aggregates attached to the
      experiment as trace feedback scores (`test_run_benchmark_attaches_aggregates_to_the_experiment`;
      the 1.9.8 stand-in for the removed `experiment_scoring_functions`, see Notes). [HUMAN] the live
      key-requiring run.
- [x] All aggregate math unit-tested including edge cases (empty results never raise). —
      `tests/unit/evals/harness/test_aggregates.py` (all-pass / all-fail / mixed / k=1 / empty).
- [x] Suite summary table prints pass rates + cost per task. —
      `test_aggregates.py::test_render_summary_table_shows_pass_rates_and_task_rows` +
      `test_run.py::test_benchmark_subcommand_forwards_trials_and_prints_the_summary`.
- [x] `make ci` green. — full unit suite 1598 passed; the 2 docker integration failures under the
      full run are resource flakes (exit 137, container OOM-killed) that pass in isolation, unrelated
      to this `evals/harness` change.

### [Tester] 2026-07-14 01:15 — QA (round 1)

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check`, `make pre-commit` all
  clean)
- Unit tests: 1598 passed / 0 failed
- Integration tests (full re-run, not just isolation): 113 passed / 2 skipped (key-gated live
  smokes) / 0 failed, 320.38s — the 2 docker tests the SWE reported as OOM flakes did NOT fail on
  this re-run, consistent with a resource-contention flake rather than a real regression
- Warnings: 0

**E2E adversarial pass**
- Happy path: `evals.harness.aggregates.summarize_outcomes` + `render_summary_table` over a 3-task
  mixed matrix → correct pass@1/pass@k/pass^k/flaky columns, correct SUITE footer (PASS)
- Break path 1 (math truth-check by hand): `pass_at_1([T,T,F])==0.667`, `pass_at_k==1.0`,
  `pass_hat_k==0.0`, `is_flaky==True`; `[F,F,F]` → all `0.0`/not-flaky; `[T]` → all `1.0`/not-flaky;
  `[]` → all `0.0`/not-flaky — every value hand-verified against the manual computation, not just
  test names (PASS)
- Break path 2 (mixed trial counts per item, A=3 trials/B=1 trial): `summarize_outcomes` computed
  sane per-task + suite means with no crash; `render_summary_table` rendered both rows (PASS, with
  a cosmetic note below)
- Break path 3 (malformed `TestResult` shapes): missing `verify_oracle` score, `score_results=None`,
  `scoring_failed=True` flag, `task_output=None`, non-list `test_results`, and hostile
  non-numeric `input_tokens`/`cost_usd` values (`"not-a-number"`, `"DROP TABLE users;"`) — all
  degrade to `passed=False`/`tokens=0`/`cost_usd=None` gracefully, never raise (PASS)
- Break path 4 (`success_per_dollar` with zero-but-present cost, `cost_usd=0.0`): correctly returns
  `None` (guarded by `total_cost > 0`), not a `ZeroDivisionError`; `mean_cost_usd` still reports
  `0.0` (PASS)
- Break path 5 (`--trials 0` / `--trials -5` via the real CLI, `run_benchmark` mocked): **FAIL** —
  see Acceptance Criteria below
- Break path 6 (empty summary, 0 items): `render_summary_table(summarize_outcomes({}, trials=3))`
  renders a valid table with a zeroed `SUITE` row, no crash (PASS)
- Break path 7 (large/Unicode scale): 500 tasks × 10 trials renders without error; Unicode/emoji
  task id (`タスク-日本語-🚀`) renders correctly (PASS)
- Break path 8 (state edge — attach called twice / no trace_id): `attach_experiment_aggregates`
  with `trace_id=None` is a correct no-op (`log_traces_feedback_scores` NOT called); calling it
  repeatedly is idempotent, no error (PASS)

**Acceptance criteria**
- [x] PASS — `python -m evals benchmark --trials 3` runs k trials per item and the experiment
      carries pass@1/pass@3/pass^3/flakiness + cost aggregates. — Evidence:
      `test_run_benchmark_forwards_the_trial_count`, `test_run_benchmark_attaches_aggregates_to_the_experiment`
      (both pass); manually re-ran `summarize_outcomes`/`render_summary_table` over a realistic
      3-task×3-trial matrix, output matches SWE's evidence table shape. [HUMAN] the live keyed run
      — awaiting human verification.
- [x] PASS — All aggregate math unit-tested including edge cases (empty results never raise). —
      `tests/unit/evals/harness/test_aggregates.py` (17 tests, all pass); hand-verified the actual
      numbers (not just test names) for `[T,T,F]`/`[F,F,F]`/`[T]`/`[]` — see Break path 1 above.
- [x] PASS — Suite summary table prints pass rates + cost per task. —
      `test_render_summary_table_shows_pass_rates_and_task_rows` +
      `test_benchmark_subcommand_forwards_trials_and_prints_the_summary`; manually rendered on
      empty/large/Unicode inputs (Break paths 6, 7) with no crash.
- [x] PASS — `make ci` green. — Unit: 1598 passed. Integration: re-ran the FULL suite (not just the
      2 docker tests in isolation) → 113 passed / 2 skipped / 0 failed in this run — the 2 tests the
      SWE reported failing (exit 137, OOM) did not fail here, and running just the 17 docker tests
      in isolation also passed 17/17. `git diff --name-only` confirms this task touches only
      `evals/harness/aggregates.py`, `evals/harness/benchmark.py`, `evals/run.py`, and their tests —
      no `sandbox`/`docker` code — corroborating the SWE's "pre-existing resource flake, unrelated to
      this change" claim.
- [ ] FAIL — `--trials 0` / negative → friendly CLI error (adversarial pass item, not literally an
      AC bullet but required by the QA brief and a real user-facing gap).
      Expected: `python -m evals benchmark --trials 0` (or a negative value) rejects with a
      `click.BadParameter`/`ClickException`-style friendly error before touching Opik.
      Actual: `evals/run.py`'s `--trials` option has no `click.IntRange`/validation
      (`evals/run.py:41-47`), and `run_benchmark`/`_attach_aggregates` (`evals/harness/benchmark.py`)
      never validate `trials` either. Reproduced with `run_benchmark` mocked so no real Opik call is
      needed: `CliRunner().invoke(cli, ["benchmark", "--task", "001-greeting", "--trials", "0"])` →
      `exit_code == 0`, `rb.call_args.kwargs["trials"] == 0`, prints a table titled
      `"decode benchmark — 0 task(s) x 0 trial(s)"` and `"evals benchmark: experiment logged under
      decode-evals."` — a silent, misleading SUCCESS message. `--trials -5` is worse: it reaches
      real Opik's `evaluate(trial_count=-5)`, whose engine does `for trial_id in range(trial_count)`
      (`opik/evaluation/engine/engine.py:181`) — `range(-5)` is a no-op, so `evaluate()` runs ZERO
      real trials and returns cleanly. The user gets a "logged" experiment and a table with
      nonsensical `pass@-5`/`pass^-5` column headers, no error at all, and (if a real dataset/keys
      were present) an empty Opik experiment silently created — this is the "silent
      corruption/misleading success" failure mode the QA brief explicitly asked to check.
      Fix: add `click.option("--trials", type=click.IntRange(min=1), ...)` on the `benchmark`
      command in `evals/run.py`, or an explicit `if trials < 1: raise click.BadParameter(...)`
      guard before calling `run_benchmark`. Add a regression test asserting `--trials 0` and
      `--trials -1` both produce a non-zero exit code with a friendly message, never reach
      `run_benchmark`.

**Evidence**
```
$ make pre-commit
======================= 1598 passed in 92.13s (0:01:32) ========================

$ uv run pytest tests/integration -q
......................................s.................................. [ 62%]
.......................................s...                              [100%]
113 passed, 2 skipped in 320.38s (0:05:20)

$ uv run pytest tests/integration -k docker -q
17 passed, 98 deselected in 101.96s (0:01:41)

$ python -c "... CliRunner().invoke(cli, ['benchmark', '--task', '001-greeting', '--trials', '0']) ..."
exit_code: 0
             decode benchmark — 0 task(s) x 0 trial(s)
┏━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ task  ┃ pass@1 ┃ pass@0 ┃ pass^0 ┃ flaky ┃ ~tokens ┃          ~$ ┃
...
evals benchmark: experiment logged under decode-evals.
rb called with trials= 0
```

**Other issues found**
- Cosmetic, not blocking: when trial counts differ per dataset item (e.g. a hand-built matrix with
  item A at 3 trials and item B at 1), `render_summary_table` labels every row's columns with the
  GLOBAL `summary.trials` (`pass@3`/`pass^3`), even for a row whose own `TaskAggregate.trials` is
  smaller — the header is technically for a different k than that row's own data. In practice this
  can't happen through `run_benchmark`/`evaluate(trial_count=k)` since Opik applies one `k` to every
  item in a run, so this is only reachable by hand-building a heterogeneous matrix (as done in Break
  path 2) — flagging for awareness, not a fix requirement.
- `docs/adr/0017-decode-eval-suite.md` §8 still reads "ride `experiment_scoring_functions`" and was
  not amended to reflect the 1.9.8 feedback-score adaptation — only the task log and the
  `aggregates.py` module docstring document the deviation. The task brief explicitly asked me to
  judge the adaptation itself on its merits (which is sound and well-documented at the code/task
  level), and ADR-discipline enforcement is PR Reviewer's lane per my role — flagging so PR Reviewer
  can decide whether ADR-0017 §8 needs a one-line amendment note.
- `--nb-samples` has the same missing-validation shape as `--trials` (no `IntRange`) — not in scope
  for this task, flagging for a possible follow-up.

**VERDICT: FAIL**

## Out of scope

- best@k with verifier, majority vote (non-goals).

## Log

### [SWE] 2026-07-14 12:20 — Implementation

**Files modified**
- `evals/harness/aggregates.py` (new) — pure trial-aggregate math (`pass_at_1` / `pass_at_k` /
  `pass_hat_k` / `is_flaky` over `Sequence[bool]`), `summarize_outcomes` over a hand-built matrix,
  the Opik seam (`extract_trial_outcomes` / `summarize`), the Rich `render_summary_table`, and
  `attach_experiment_aggregates` (per-item aggregates → trace feedback scores).
- `evals/harness/benchmark.py` — `run_benchmark(..., trials=1)` → `evaluate(trial_count=trials)`;
  `_attach_aggregates` best-effort post-run.
- `evals/run.py` — `benchmark --trials` option; prints the Rich summary table after the run.
- `tests/unit/evals/harness/test_aggregates.py` (new) — the aggregate math + adapter + render +
  attach.
- `tests/unit/evals/harness/test_benchmark.py` — `trial_count` forwarding (k and default 1), the
  aggregate attach, and attach-failure resilience.
- `tests/unit/evals/test_run.py` — `--trials` forwarding + summary print.

**Tests**
- Unit: 1598 passing, 0 failing (`make pre-commit`); the 31 eval-harness/CLI tests touched here all
  green.
- Integration: 111 passing, 2 skipped; 2 docker sandbox tests failed under the full run from
  resource contention (exit 137, container OOM-killed) and **pass in isolation** — unrelated to this
  `evals/harness` change (no docker/sandbox code touched).

**Acceptance criteria**
- [x] `--trials k` → `evaluate(trial_count=k)`; experiment carries the aggregates (trace feedback
      scores) — `test_run_benchmark_forwards_the_trial_count`,
      `test_run_benchmark_attaches_aggregates_to_the_experiment`. [HUMAN] the live keyed run.
- [x] Aggregate math unit-tested incl. edge cases (empty never raises) —
      `tests/unit/evals/harness/test_aggregates.py`.
- [x] Suite summary table prints pass rates + cost — `test_render_summary_table_shows_pass_rates...`
      + the CLI print test; e2e-rendered below.
- [x] `make ci` green — unit suite green; docker integration failures are pre-existing resource
      flakes (pass in isolation).

**Evidence**
```
$ python -m evals benchmark --help      # --trials INTEGER  Runs per item (Opik trial_count) ... [default: 1]

$ # e2e: summarize + render_summary_table over a realistic 3-task x 3-trial result
                 decode benchmark — 3 task(s) x 3 trial(s)
┃ task              ┃ pass@1 ┃ pass@3 ┃ pass^3 ┃ flaky ┃ ~tokens ┃      ~$ ┃
│ 001-greeting      │   1.00 │   1.00 │   1.00 │       │    1350 │ $0.0040 │
│ 010-flaky-build   │   0.67 │   1.00 │   0.00 │  yes  │    2250 │ $0.0070 │
│ 020-hard-refactor │   0.00 │   0.00 │   0.00 │       │    4500 │ $0.0120 │
│ SUITE             │   0.56 │   0.67 │   0.33 │  33%  │    2700 │  72.5/$ │
attach: 36 feedback scores over project='decode-evals'; sample={'id':'i-rel-0','name':'pass_at_1','value':1.0}

$ uv run pytest tests/unit/evals/harness/test_aggregates.py tests/unit/evals/harness/test_benchmark.py -q
30 passed
```

**Notes**
- ADR/task-text adaptation (carry-forward from task 106): installed `opik==1.9.8` `evaluate()` HAS
  `trial_count` but NO `experiment_scoring_functions` param (nearest is `scoring_functions`, a
  per-item `ScorerFunctionProtocol` — wrong axis for a cross-trial aggregate). So the aggregates are
  pure functions computed post-hoc over the `EvaluationResult.test_results`, printed as a Rich table,
  and attached to the experiment by logging them as **trace feedback scores**
  (`client.log_traces_feedback_scores`) — Opik's per-experiment averaging then surfaces the
  suite-level numbers on the experiment row. This is the honest 1.9.8 route wherever the task text
  said `experiment_scoring_functions`.
- Cost: a trial's dollar cost is read from the task-fn payload (`cost_usd` / `total_cost`, e.g. a
  copied Opik trace `total_cost`). The task-103 driver records tokens but no dollar figure yet, so
  today `success_per_dollar` / `mean_cost_usd` are `None` and the table shows the documented
  tokens-only fallback (ADR-0017 §8); the field flows through automatically once a dollar cost is
  recorded.
- "Passed" = the `verify_oracle` score is `1.0`; a missing score grades as not-passed (graceful).

### [SWE] 2026-07-14 15:40 — Fixes (QA round 1)

**Feedback addressed**
- BLOCKING — `--trials 0` / `--trials -5` silently "succeeded" (misleading "experiment logged" +
  `0 task(s) x 0 trial(s)` / `pass@-5` nonsense; `-5` reached `evaluate(trial_count=-5)` whose
  `range()` loop no-ops). Fixed at two layers:
  - `evals/run.py` — `--trials` and `--nb-samples` now use `click.IntRange(min=1)`, so bad input is
    a friendly usage error (exit 2) that never reaches `run_benchmark`.
  - `evals/harness/benchmark.py` — mirror guard: `run_benchmark` raises `ValueError("trials must be
    >= 1, ...")` BEFORE `evaluate` for direct programmatic callers.
- Fix-if-trivial — `--nb-samples` given the same `IntRange(min=1)` shape (done). Heterogeneous-matrix
  row label in `render_summary_table` left as-is (skip per Tester note; runs are uniform-`k`).

**Files modified**
- `evals/run.py` — `IntRange(min=1)` on `--trials` + `--nb-samples`.
- `evals/harness/benchmark.py` — `trials < 1` guard before `evaluate`.
- `tests/unit/evals/harness/test_benchmark.py` — `test_run_benchmark_rejects_a_non_positive_trial_count`
  (0 / -5, asserts `evaluate` never called).
- `tests/unit/evals/test_run.py` — `test_benchmark_subcommand_rejects_a_non_positive_trials`
  (0 / -1) + `..._nb_samples` (0); both assert exit non-zero and `run_benchmark` never called.

**Tests**
- Unit: 1603 passing, 0 failing (`make pre-commit`) — +5 over round 1.
- format-check / lint-check clean.

**Evidence**
```
$ python -m evals benchmark --trials -5   →  exit 2
  Error: Invalid value for '--trials': -5 is not in the range x>=1.
$ python -m evals benchmark --nb-samples 0 →  exit 2
$ uv run pytest tests/unit/evals -q  →  all eval tests pass
```

### [Tester] 2026-07-14 16:05 — QA (round 2)

**Test summary**
- Format / lint: PASS (`make format-check`, `make lint-check` clean)
- Unit tests: 1603 passed / 0 failed (matches SWE's claim: 1598 + 5 new)
- Warnings: 0
- Integration: not re-run in full this round (no `sandbox`/`docker` files touched by the fix;
  `git status --porcelain` after re-verification shows the same 5 modified + 2 new files as round 1
  — round 1 already re-ran the full integration suite green, 113 passed/2 skipped)

**Re-ran the exact round-1 repros**
- `--trials 0` via the real CLI (`CliRunner`, `run_benchmark` mocked so no Opik call needed):
  `exit_code == 2`, output `"Error: Invalid value for '--trials': 0 is not in the range x>=1."`,
  `rb.called == False` — no longer reaches `run_benchmark` (PASS, was FAIL)
- `--trials -5` via the real CLI: `exit_code == 2`,
  `"Error: Invalid value for '--trials': -5 is not in the range x>=1."`, `rb.called == False`
  (PASS, was FAIL — this was the worse of the two round-1 repros since it used to reach real Opik's
  `evaluate(trial_count=-5)` and silently no-op)
- Defense-in-depth check: called `run_benchmark(task_id=..., sandbox="docker", trials=0)` and
  `trials=-5` directly (bypassing the CLI, `opik.evaluation.evaluate` + `opik.Opik` mocked) — both
  raise `ValueError("trials must be >= 1, got <n>.")` BEFORE `evaluate` is called
  (`evaluate.called == False` in both cases) — confirms the guard is not CLI-only

**Regression-test protectiveness (revert-check)**
- Reverted `evals/run.py`'s `click.IntRange(min=1)` back to plain `int` and removed the `trials < 1`
  guard in `evals/harness/benchmark.py` (kept everything else, incl. the new tests, intact), then
  ran the 5 new regression tests: all 5 FAILED as expected —
  `test_run_benchmark_rejects_a_non_positive_trial_count[0]`,
  `[-5]`, `test_benchmark_subcommand_rejects_a_non_positive_trials[0]`, `[-1]`,
  `test_benchmark_subcommand_rejects_a_non_positive_nb_samples` — confirming they're genuinely
  protective, not vacuous. Restored the fix; `git diff --stat` for both files matched the
  pre-revert-check state exactly (65 lines added across the two files, same as before); re-ran
  `make unit-tests` → 1603 passed again.

**Acceptance criteria**
- [x] PASS — `python -m evals benchmark --trials 3` runs k trials per item and the experiment
      carries pass@1/pass@3/pass^3/flakiness + cost aggregates. — unchanged from round 1, still
      passing (`test_run_benchmark_forwards_the_trial_count`,
      `test_run_benchmark_attaches_aggregates_to_the_experiment`). [HUMAN] the live keyed run —
      awaiting human verification.
- [x] PASS — All aggregate math unit-tested including edge cases (empty results never raise). —
      `aggregates.py` untouched by this fix; re-verified `pass_at_1([T,T,F])==0.667` etc. still hold.
- [x] PASS — Suite summary table prints pass rates + cost per task. — unchanged from round 1.
- [x] PASS — `make ci` green. — Unit: 1603 passed, 0 failed, 0 warnings. Format/lint clean.
      Integration previously re-verified full-green in round 1 (113 passed/2 skipped); this round's
      diff touches only `evals/run.py` + `evals/harness/benchmark.py` (CLI/guard-only), no
      sandbox/docker surface, so the round-1 integration result still holds.
- [x] PASS (was FAIL) — `--trials 0` / negative → friendly CLI error. — `click.IntRange(min=1)` on
      both `--trials` and `--nb-samples` in `evals/run.py` gives a click usage error (exit 2) before
      `run_benchmark` is ever called; `run_benchmark` itself also raises `ValueError` before
      `evaluate` for direct callers. Regression tests added and confirmed genuinely protective via
      revert-check (see above).

**Other issues found (carried from round 1, still open, non-blocking)**
- `docs/adr/0017-decode-eval-suite.md` §8 still reads "ride `experiment_scoring_functions`", not
  amended for the 1.9.8 feedback-score adaptation — PR Reviewer's lane, not re-litigating here.
- Cosmetic `render_summary_table` global-`k` column-label note from round 1 still applies (SWE
  explicitly deferred it as a non-issue given Opik's uniform per-run `k`; agreed, not blocking).

**VERDICT: PASS**
