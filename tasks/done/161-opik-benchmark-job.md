---
id: 161-opik-benchmark-job
feature: evals-v2
status: done
---

# Opik Benchmark Job: `evaluate()` over trials, pass@k on Opik's axis, CLI + Makefile

Tags: `evals`, `benchmark`, `opik`
Depends on: 159, 160
Blocks: 165

## Scope
Wire the trial runner into Opik as ADR-0022 §6 describes, replace the old benchmark path end to end, keep `make eval-benchmark` the one entry point.

## Acceptance criteria
- [x] `evals/harness/datasets.py`: `BENCHMARK_DATASET_NAME = "decode-benchmark-v2"`; item = `{task_id, difficulty, category, tags, instruction, checksum}` with `checksum` = sha256 over the task folder's files in sorted relative-path order; `sync --benchmark` upserts (Opik dedupes unchanged content).
- [x] `evals/harness/benchmark.py::run_benchmark(*, task_id, difficulty, sandbox, trials, threads, job_name, model, client=None) -> BenchmarkRun(result, job_dir)`: `evaluate(dataset, task=<trial fn>, scoring_metrics=[RewardMetric()], trial_count=trials, task_threads=threads, experiment_scoring_functions=[...], experiment_config={model, provider, git_sha, sandbox, decode_version, kitaru_agent_id, trials, job_name}, project_name=settings.eval_project_name, dataset_item_ids=<selected>, experiment_name=job_name)`. The `experiment_scoring_functions` signature is verified against the installed 2.2.36 and pinned by a unit test with a fake result; `kitaru_agent_id` is `None` when `KITARU_AGENT_ID` is unset (never a placeholder).
- [x] The trial fn maps item → `run_trial(task, sandbox=…, job_dir=<.decode/evals/runs/<job>>, trial_id=f"{task_id}__{uuid4().hex[:8]}", model=…)` and returns `result.json`'s dict; it never raises (an unexpected exception becomes `infra_error` with the reason).
- [x] `RewardMetric.score(status, reward, reason, **_)` → `ScoreResult(name="reward", value=reward, reason=reason)` for `agent_ok|agent_fail`; `ScoreResult(name="reward", value=0.0, scoring_failed=True, reason=reason)` for `infra_error`; a unit test proves the aggregate excludes the failed score.
- [x] `evals/harness/aggregates.py` keeps only pure functions — `pass_at_1`, `pass_at_k` (documented as Harbor's unbiased estimator, which equals `any()` when n == k), `pass_hat_k`, `is_flaky`, `success_per_dollar`, plus new `infra_error_rate`, `mean_cost_usd`; `DEFAULT_PASS_METRIC = "reward"`; `attach_experiment_aggregates`, `extract_trial_outcomes`'s Opik glue, and the opik-1.9.8 docstring deleted; the Rich table gains per-difficulty rollup rows and a totals row (n, pass@1, pass@k, pass^k, flaky, mean cost, infra errors).
- [x] `python -m evals benchmark [--task ID] [--difficulty D] [--sandbox docker|modal] [--trials K] [--threads N] [--job-name NAME] [--model ID]`: `--threads` defaults 1 for docker, 4 for modal; `--job-name` defaults `bench-<UTC yyyymmdd-HHMMSS>`; prints the table, the experiment name, and the job dir path; `--nb-samples` removed. `--help` imports no opik.
- [x] `make eval-benchmark` unchanged in shape (`keys` guard, `ARGS=`); `.gitignore` needs no change (`.decode/*` covers `.decode/evals/`); a unit test asserts the job dir resolves under `.decode/evals/runs/`.
- [x] `tests/unit/evals/harness/test_benchmark.py` rewritten against a fake `run_trial` and a fake Opik client; `test_aggregates.py` covers the two new functions.
- [ ] [HUMAN] `make eval-benchmark ARGS='--task 001-find-and-replace --trials 2'` with docker: two Trial Dirs, an Opik experiment `bench-…` under `decode-evals` with `reward` scores and the aggregates on the experiment row; `--sandbox modal --task 001-find-and-replace` once, same evidence. Ids logged here.

## Out of scope
- Regression track (162). Cohort creation (165). Per-task judges (deleted for good).

## Log
### [PA] 2026-09-10 — Grooming
Opik 2.2.36 `evaluate()` signature verified: `trial_count`, `experiment_scoring_functions`, `experiment_tags`, `error_tolerance` exist — no more post-hoc feedback-score attachment. `success_per_dollar` reads `cost_usd` from the trial summary; when the provider reports no cost it is `None` and the table says "n/a", never 0. Subprocess trials are independent, so `task_threads > 1` is safe; docker defaults to 1 only for warm-up CPU.

### [SWE] 2026-09-11 14:05 — Implementation

**Files modified**
- `evals/harness/datasets.py` — `decode-benchmark-v2`; v2 item (`task_id`/`difficulty`/`category`/`tags`/`instruction`/`checksum`) + `task_checksum()` (sha256 over the folder's files, sorted relative path, `__pycache__`/`*.pyc` skipped).
- `evals/harness/aggregates.py` — rewritten pure + opik-free: `pass_at_1`, `pass_at_k` (Harbor estimator, clamped k), `pass_hat_k`, `is_flaky`, `success_per_dollar`, `infra_error_rate`, `mean_cost_usd`; `TrialOutcome`/`TaskTrials`/`TaskAggregate`/`Rollup`/`BenchmarkSummary`; `summarize_tasks` + a Rich table with per-tier rollups and a `TOTAL` row. `attach_experiment_aggregates`, `extract_trial_outcomes` and the opik-1.9.8 docstring deleted.
- `evals/harness/metrics.py` — new `RewardMetric` (`track=False`), `scoring_failed=True` on `infra_error` AND on a graded status with no numeric reward.
- `evals/harness/benchmark.py` — rewritten as the Opik surface: `BenchmarkRun(result, job_dir)`, `run_benchmark(...)`, `default_job_name()`/`new_job_dir(job)`/`default_threads(sandbox)`, a never-raising trial fn, checksum-scoped `dataset_item_ids`, the `TestResult` → `TaskTrials` reader, `summarize()`, and `EXPERIMENT_SCORING_FUNCTIONS` (7 callables).
- `evals/harness/trial.py` — `child_env(..., workspace_dir=)` pins `SANDBOX_WORKSPACE_DIR` per trial (set after the settings export, so an absolute `.env` value cannot make concurrent trials share one Workspace); `run_trial` passes `<trial>/home/.decode/sandbox`.
- `evals/run.py` — `benchmark` command: `--task/--difficulty/--sandbox/--trials/--threads/--job-name/--model`; `--nb-samples` removed; prints table + experiment name + job dir.
- `evals/harness/__init__.py`, `evals/benchmark/tasks/README.md` — export `RewardMetric`; the README's dataset name/item shape (one-word doc fix; the docs pass is task 166).
- Tests: `tests/unit/evals/harness/test_{benchmark,aggregates,datasets,metrics,trial}.py`, `tests/unit/evals/test_run.py`, `tests/support/fake_decode.py` (the fake `decode run` now reports its `SANDBOX_WORKSPACE_DIR`).

**Tests**
- Unit: 2679 passing, 0 failing, 1 skipped (`fastapi` absent) — `make unit-tests`. Evals subset: 660 passing.
- Integration: 114 passing — `make integration-tests` (6m15s).
- Format / lint / pre-commit: clean.

**Acceptance criteria**
- [x] dataset v2 + checksum — `tests/unit/evals/harness/test_datasets.py::test_item_payload_has_the_slice_labels_the_instruction_and_a_checksum`, `::test_checksum_changes_when_any_task_file_changes`, `::test_checksum_ignores_generated_bytecode`
- [x] `run_benchmark` / `evaluate` wiring — `test_benchmark.py::test_run_benchmark_wires_evaluate`, `::test_run_benchmark_experiment_config_carries_the_full_provenance`, `::test_experiment_config_kitaru_agent_id_is_none_when_unset`, `::test_experiment_scoring_functions_match_the_installed_opik_signature`
- [x] the trial fn never raises — `test_benchmark.py::test_task_fn_turns_an_unexpected_raise_into_an_infra_error`, `::test_task_fn_survives_an_item_naming_an_unknown_task`
- [x] `RewardMetric` + the excluded aggregate — `test_metrics.py::test_reward_metric_marks_an_infra_error_as_scoring_failed`, `::test_opik_excludes_a_failed_reward_from_the_aggregate` (driven through the INSTALLED `opik.evaluation.score_statistics`)
- [x] `aggregates.py` pure + the table — `test_aggregates.py` (19 tests), `::test_table_shows_task_rows_tier_rollups_and_a_totals_row`
- [x] the CLI — `test_run.py::test_benchmark_subcommand_invokes_run_benchmark`, `::test_benchmark_subcommand_has_no_nb_samples_flag`, `::test_benchmark_help_imports_no_opik`
- [x] `make eval-benchmark` shape unchanged; job dir under `.decode/evals/runs/` — `test_benchmark.py::test_a_job_dir_lands_under_the_harness_home`
- [x] tests rewritten — `test_benchmark.py` (26), `test_aggregates.py` (19)
- [ ] [HUMAN] the two real runs (docker ×2 trials + one modal) — a one-task/one-trial docker smoke is reported below; the full AC stays open.

**Evidence**

```
$ make unit-tests
2679 passed, 1 skipped in 55.55s

$ make integration-tests
114 passed in 375.58s (0:06:15)

$ uv run python -m evals benchmark --help
  --task TEXT / --difficulty [easy|medium|hard] / --sandbox [docker|modal] (default docker)
  --trials INTEGER RANGE (default 1) / --threads INTEGER RANGE [default: 1 docker, 4 modal]
  --job-name TEXT [default: bench-<UTC stamp>] / --model TEXT
```

**Notes**
- **Deviation (deliberate):** the AC's `trial_id=f"{task_id}__{uuid4().hex[:8]}"` would produce a Trial Dir `<task>__<task>__<short-id>` — `run_trial` already prefixes `task.id` and ADR-0022 §7 + the glossary pin `<task>__<short-id>`. Implemented as `trial_id=uuid4().hex[:8]`.
- **Deviation (deliberate):** `evaluate(project_name=...)` is passed ONLY when the dataset carries no project, not unconditionally as the AC's call shape shows. opik 2.2.36 (`opik/evaluation/helpers.py::resolve_project_name`) resolves the run's project from the DATASET and warns once per run when both are set; the dataset is now created in `settings.eval_project_name`, so the parameter is the compatibility fallback. Same project either way — see the bug note below.
- **Beyond the AC (load-bearing):** `dataset_item_ids` selects on `(task_id, checksum)`, not `task_id` alone. Opik's `insert` dedupes by content hash but never DELETES, so an edited task leaves its stale item in the dataset forever and a `--task` run would otherwise evaluate every historical version of it. Pinned by `::test_run_benchmark_selects_the_item_matching_the_task_folder_on_disk`. Its mirror: a selected task with NO fresh item now raises `BenchmarkSelectionError` instead of falling through to `dataset_item_ids=None`, which Opik reads as "run the whole dataset" — a silent 19-task paid run (`::test_run_benchmark_stops_when_a_task_has_no_fresh_dataset_item`).
- `EXPERIMENT_SCORING_FUNCTIONS` holds seven callables: ADR-0022 §6's five plus `pass_at_1` and `flaky_rate`, which the glossary's pass@k row names. Macro-averaged `pass_at_1` is NOT redundant with Opik's own mean of `reward` (that is a micro-average over graded trials); the two diverge as soon as one task loses trials to an Infra Error.
- Cost scores with no provider figure are `ScoreResult(value=0.0, scoring_failed=True)` and the table prints `n/a` — never a `$0` that reads as free.
- Docker containers are unnamed (one per process, `docker_backend.py`), so `--threads > 1` on docker collides on nothing now that the Workspace is pinned per trial; the default stays 1 for warm-up CPU.
- At `--trials 1` the table drops the pass@k / pass^k / flaky columns: they are pass@1 by definition, and three columns headed `pass@1` say nothing (`test_aggregates.py::test_table_shows_one_pass_column_for_a_single_trial_run`).
- `agent_model()` is now `model or settings.active_model`: `Settings.active_model` has the identical openrouter / modal / gemini branching the old private resolver had, so the regression track's experiment row records the same string as before — one resolver instead of two.
- `evals/harness/metrics.py` now imports two constants from `aggregates` (`DEFAULT_PASS_METRIC`, `INFRA_ERROR_STATUS`) — one definition of the score name and of the taxonomy string.

**Smoke (real, docker, gemini) — the [HUMAN] AC stays open**

```
$ DECODE_ENV=local LLM_PROVIDER=gemini make eval-benchmark \
    ARGS='--task 001-find-and-replace --trials 1 --job-name bench-smoke-161b'
OPIK: Started logging traces to the "decode-evals" project
╭─ decode-benchmark-v2 (1 samples) ─╮
│ reward: 1.0000 (avg)  pass_at_1: 1.0000  pass_at_k: 1.0000  pass_hat_k: 1.0000
│ flaky_rate: 0.0000  success_per_dollar: 16.8988  mean_cost_usd: 0.0592  infra_error_rate: 0.0000
╰───────────────────────────────────╯
                   decode benchmark — 1 task(s) x 1 trial(s)
┃ task                 ┃ n ┃ pass@1 ┃ pass@1 ┃ pass^1 ┃ flaky ┃ ~$/trial ┃ infra ┃
│ 001-find-and-repl…   │ 1 │   1.00 │   1.00 │   1.00 │       │  $0.0592 │       │
│ easy (1 task(s))     │ 1 │   1.00 │   1.00 │   1.00 │  0%   │  $0.0592 │     0 │
│ TOTAL                │ 1 │   1.00 │   1.00 │   1.00 │  0%   │  $0.0592 │     0 │
evals benchmark: experiment bench-smoke-161b logged under decode-evals; trial dirs in .decode/evals/runs/bench-smoke-161b.
```

Experiments: `bench-20260911-041427`, `bench-smoke-161b`, `bench-smoke-161c` (dataset `decode-benchmark-v2`,
project `decode-evals`). Trial Dir `.decode/evals/runs/bench-smoke-161b/001-find-and-replace__34e60f23/`
holds `seed/ home/ pristine/ agent/ verifier/ result.json`; `result.json` = `agent_ok`, reward 1.0,
branch `decode/8ed65a83`, 8 requests, $0.0592, `agent: {git_sha, decode_version, model, provider, sandbox}`.

**Bug the smoke found (fixed in this task)**

The first smoke printed `OPIK: The 'project_name' parameter of 'evaluate()' is deprecated … the dataset's
project ('scrabble-brown') will be used` — opik 2.2.36 resolves a run's trace project from the DATASET
(`evaluation/helpers.py::resolve_project_name`), so with the dataset created in the developer's default
Opik project every benchmark trace landed there while the CLI claimed `decode-evals`. Fixed:
`sync_benchmark_dataset` now creates the dataset with `project_name=settings.eval_project_name`, and
`evaluate(project_name=…)` is passed ONLY when the dataset carries no project (`_evaluate_project_name`),
so there is no deprecation line per run. A dataset's project is fixed at creation (`Dataset.from_public`
resolves the real one via a workspace-wide name fallback), so the stray `decode-benchmark-v2` my first
smoke created in `scrabble-brown` was deleted once with the SDK; the next sync recreated it in
`decode-evals` and the third smoke logged there (line 1 above). **Note for the PA / task 162:**
`sync_regression_dataset` still creates `decode-regression-v1` with no project and has the same latent
mis-routing — deliberately left alone, it is the regression track's surface.

**Final smoke after those two fixes** (`bench-smoke-161d`, docker + gemini, reward 1.0, $0.0767,
traces in `decode-evals`):

```
       decode benchmark — 1 task(s) x 1 trial(s)
┃ task                 ┃ n ┃ pass@1 ┃ ~$/trial ┃ infra ┃
│ 001-find-and-replace │ 1 │   1.00 │  $0.0767 │       │
│ easy (1 task(s))     │ 1 │   1.00 │  $0.0767 │     0 │
│ TOTAL                │ 1 │   1.00 │  $0.0767 │     0 │
evals benchmark: experiment bench-smoke-161d logged under decode-evals; trial dirs in .decode/evals/runs/bench-smoke-161d.
```

Final counts: unit 2683 passing / 1 skipped, integration 114 passing, format + lint + pre-commit clean.

### [Tester] 2026-09-11 07:55 — QA

**Test summary**
- Format / lint: PASS (`uv run ruff format --check .` — 331 files already formatted; `uv run ruff check .` — all checks passed).
- `make ci`: red, but for a cause outside this diff — see below. `uv lock --check` + format-check + lint-check all passed; `make test` failed one test.
- Unit tests (`uv run pytest tests/unit -q`, re-run standalone): 2683 passed, 1 skipped (`fastapi` absent). Matches the SWE's report exactly.
- Integration tests (`uv run pytest tests/integration -q --deselect tests/integration/test_subagents_capstone.py::test_live_gemini_fanout_smoke`): 113 passed, 1 deselected.
- The one `make ci` failure is `tests/integration/test_subagents_capstone.py::test_live_gemini_fanout_smoke` — `pydantic_ai.exceptions.ModelHTTPError: status_code: 503 ... 'This model is currently experiencing high demand'` from the real Gemini API. Reproduced standalone (32s) with the same 503. This file is **absent from `git diff --stat`** for this branch (`git log -1 -- tests/integration/test_subagents_capstone.py` → `bcd39c1`, task 131-146, unrelated to 161) and the test is `skipif`-gated on `GEMINI_API_KEY` (skips in keyless CI). Attributed to transient upstream Gemini unavailability, not a regression from this diff — excluded from the verdict.
- Warnings: 0. `pyproject.toml` sets `filterwarnings = ["error"]`, so a green unit run is itself the zero-warning proof. The one place a warning-shaped message exists (`opik.evaluation.helpers.resolve_project_name`'s deprecation line) is emitted via `LOGGER.warning` (the `logging` module), never `warnings.warn`, so pytest's filter cannot and does not fire on it — confirmed by reading `.venv/.../opik/evaluation/helpers.py` and by two real runs below producing zero deprecation lines.

**E2E adversarial pass**
- Happy path: `DECODE_ENV=local LLM_PROVIDER=gemini make eval-benchmark ARGS='--task 002-regex-extraction --trials 2 --job-name bench-qa-161'` (real docker + gemini) → table printed (`pass@1 1.00`, `pass@2 1.00`, `pass^2 1.00`, `$0.0844`/trial), `evals benchmark: experiment bench-qa-161 logged under decode-evals; trial dirs in .decode/evals/runs/bench-qa-161.` — verified against the live Opik API below. PASS.
- Break path 1 (malformed/boundary `--trials`/`--threads`/`--difficulty`/`--sandbox`): `--trials 0` → `Error: Invalid value for '--trials': 0 is not in the range x>=1.`; `--threads -1` → same shape; `--difficulty extreme` → `Error: Invalid value for '--difficulty': 'extreme' is not one of 'easy', 'medium', 'hard'.`; `--sandbox aws` → same shape. All one-line Click errors, no traceback, no run started. PASS.
- Break path 2 (hostile `--task`, no filesystem/shell effect): `--task '../../../etc/passwd'` → `Error: no benchmark task matched (task='../../../etc/passwd', ...)`; `--task '$(echo pwned)'` → same shape, literal string, never shell-evaluated (subprocess uses argv lists throughout, no `shell=True` anywhere in `trial.py`). PASS.
- Break path 3 (path traversal in `--job-name` — FINDING, not blocking): `--job-name '../../evil'` with no `--task` filter (a real, intentional 19-task selection) resolved `new_job_dir()` to `.decode/evil/` — **outside** the intended `.decode/evals/runs/` tree — and `decode run` was launched with `--repo .../.decode/evil/017-flaky-test-hunt__.../seed`. `new_job_dir` (`evals/harness/benchmark.py`) does `Path(settings.decode_dir).joinpath(*RUNS_DIR_PARTS, job_name)` with no validation that `job_name` stays a single path segment — `..` components are not rejected. Killed the run at 3/19 trials (real docker + gemini spend, ~$0.20, cleaned up: process, its docker container `quizzical_easley`, and `.decode/evil/` all removed by hand). Not a FAIL: `--job-name` is an operator-typed local CLI flag, not remote/attacker input, no AC or ADR-0022 §7 requires sanitizing it, and the two levels of `../` here only escaped as far as `.decode/` — but with enough `../` it can escape the harness home entirely onto the host filesystem, so it is worth a follow-up: reject `os.sep`/`..` in `--job-name`, or resolve-and-assert containment under the runs dir.

**Acceptance criteria**
- [x] PASS — `datasets.py` v2 item + checksum — `benchmark_dataset_item`/`task_checksum` read (sha256, sorted relative path, `__pycache__`/`.pyc` skipped); `tests/unit/evals/harness/test_datasets.py::test_item_payload_has_the_slice_labels_the_instruction_and_a_checksum`, `::test_checksum_changes_when_any_task_file_changes`, `::test_checksum_changes_when_a_file_is_renamed`, `::test_checksum_ignores_generated_bytecode` all pass; live-verified `ds = opik.Opik().get_dataset('decode-benchmark-v2')` → `ds.project_name == 'decode-evals'`.
- [x] PASS — `run_benchmark`/`evaluate` wiring — read `evals/harness/benchmark.py::run_benchmark`; every kwarg in the AC (`dataset`, `task`, `scoring_metrics=[RewardMetric()]`, `trial_count`, `task_threads`, `experiment_scoring_functions`, `experiment_config`, `project_name`, `dataset_item_ids`, `experiment_name`) present. `experiment_scoring_functions` signature independently pinned against the installed opik 2.2.36: read `.venv/.../opik/evaluation/types.py` (`ExperimentScoreFunction = Callable[[List[TestResult]], Union[ScoreResult, List[ScoreResult]]]`), `.venv/.../opik/evaluation/evaluator.py` (`evaluate()`'s own docstring: "takes a list of TestResult objects and returns ... ScoreResult objects"), and `.venv/.../opik/evaluation/evaluation_result.py::compute_experiment_scores` (swallows a raising/wrong-shaped function with `LOGGER.warning` — a bad signature fails silently, so the test asserting all 7 score names is the real proof). `test_benchmark.py::test_experiment_scoring_functions_match_the_installed_opik_signature` drives the real 7 callables through the real `compute_experiment_scores`, not a mock. `kitaru_agent_id` None-when-unset: `::test_experiment_config_kitaru_agent_id_is_none_when_unset`.
- [x] PASS — trial fn maps item → `run_trial(..., trial_id=uuid4().hex[:8], ...)`, never raises — code in `make_benchmark_task_fn` wraps `run_trial` in `try/except Exception` → `_infra_payload`; `test_benchmark.py::test_task_fn_turns_an_unexpected_raise_into_an_infra_error`, `::test_task_fn_survives_an_item_naming_an_unknown_task` pass. **Documented deviation confirmed correct against ADR-0022 §7** (`.../docs/adr/0022-evals-v2-own-harbor-on-opik.md` §7: `<task>__<short-id>`, not `<task>__<task>__<short-id>`) — `trial.py::run_trial` already does `trial_dir = job_dir / f"{task.id}__{trial_id}"`, so passing `trial_id=uuid4().hex[:8]` (not the AC's literal `f"{task_id}__{uuid4().hex[:8]}"`) is the ADR-compliant shape, and the AC's own template would have double-prefixed.
- [x] PASS — `RewardMetric` + excluded aggregate — read `evals/harness/metrics.py::RewardMetric.score`, matches the AC exactly (name `"reward"`, `scoring_failed=True` + `value=0.0` on `infra_error`). Independently confirmed against the **installed** opik's own aggregation code: `.venv/.../opik/evaluation/score_statistics.py::calculate_aggregated_statistics` — `if not score_result.scoring_failed and _is_valid_score_value(...)` — a `scoring_failed` score is excluded from both `values` and `mean`, not zeroed; opik DOES exclude, so the AC's premise holds. `test_metrics.py::test_reward_metric_marks_an_infra_error_as_scoring_failed` and `::test_opik_excludes_a_failed_reward_from_the_aggregate` drive the real installed `calculate_aggregated_statistics` over real `opik.evaluation.test_result.TestResult`/`test_case.TestCase` objects, not a mock — both pass.
- [x] PASS — `aggregates.py` pure + table — read the whole module: no `opik` import anywhere, `pass_at_1`/`pass_at_k`/`pass_hat_k`/`is_flaky`/`success_per_dollar`/`infra_error_rate`/`mean_cost_usd` all plain math over sequences; `test_aggregates.py` (19 tests incl. `::test_infra_error_rate_counts_the_harness_failures`, `::test_mean_cost_usd_averages_the_reported_costs_only`, `::test_table_shows_task_rows_tier_rollups_and_a_totals_row`, `::test_table_shows_one_pass_column_for_a_single_trial_run`) pass.
- [x] PASS — CLI flags + `--help` opik-free — read `evals/run.py::benchmark`; opik/harness imports are inside the command body only. `test_run.py::test_benchmark_help_imports_no_opik` runs `--help` in a **fresh subprocess** and asserts `not [m for m in sys.modules if 'opik' in m]` — the strongest form of this check, not an import-time inspection of the already-loaded test process. Live-verified: `DECODE_ENV=local LLM_PROVIDER=gemini uv run python -m evals benchmark --task nonexistent-task-xyz` → `Error: no benchmark task matched (task='nonexistent-task-xyz', ...)` with no keys/network needed to reach that error path.
- [x] PASS — `make eval-benchmark` shape + job dir under `.decode/evals/runs/` — `test_benchmark.py::test_a_job_dir_lands_under_the_harness_home` pass; live job dirs observed at `.decode/evals/runs/bench-qa-161/`, `.decode/evals/runs/bench-qa-161-clean/`.
- [x] PASS — tests rewritten — `test_benchmark.py` (26 tests, offline against a fake `run_trial` + fake Opik client, confirmed by reading the file), `test_aggregates.py` (19 tests, pure).
- [ ] [HUMAN] real docker+modal runs — **not fully closed; docker leg re-verified independently by the Tester with stronger evidence than the SWE's own smoke, modal leg still unrun by anyone.** Docker evidence (real, this review, `DECODE_ENV=local LLM_PROVIDER=gemini`):
  - `make eval-benchmark ARGS='--task 002-regex-extraction --trials 2 --job-name bench-qa-161'` → experiment `bench-qa-161` (id `01a08ec4-0e00-7589-bd0c-ebc9872b4d64`), read back via `opik.Opik().get_experiments_by_name('bench-qa-161', project_name='decode-evals')`: **2 items** (`exp.get_items()`), each `feedback_scores == [{'name': 'reward', 'value': 1.0, 'reason': 'the Verifier scored 1.0.'}]`, and `exp.get_experiment_data().experiment_scores` carries all **7** aggregate keys on the experiment row: `pass_at_1=1.0, pass_at_k=1.0, pass_hat_k=1.0, flaky_rate=0.0, success_per_dollar≈11.84, mean_cost_usd≈0.0844, infra_error_rate=0.0`.
  - Live-project isolation, checked clean (no concurrent test suite running): baseline `decode-prod` last trace `2026-09-10 22:41:12`, `decode-local` last trace `2026-09-11 04:40:28` (that trace independently attributed to `tests/integration/test_observability_capstone.py`'s live smoke, untouched by this diff, run moments earlier by my own `make ci`/integration re-run — confirmed via `grep -rn "single word" tests/` matching its literal prompt). Ran a second clean benchmark (`bench-qa-161-clean`, 1 trial) with nothing else running; re-checked both projects immediately after: **identical** last-trace ids/timestamps in both — zero new traces from the benchmark job in either live project.
  - `git status`/`git log`/`git branch -vv` after all runs: only the SWE's original 16 modified files, no new commits; `git ls-remote origin 'refs/heads/decode/*'` → empty, no Session Branches pushed.
  - Modal leg: **not run by the SWE or the Tester** — no modal credentials exercised in this review. Box stays unchecked.

**Evidence**
```
$ uv run pytest tests/unit -q
2683 passed, 1 skipped in 55.25s

$ uv run pytest tests/integration -q --deselect tests/integration/test_subagents_capstone.py::test_live_gemini_fanout_smoke
113 passed, 1 deselected in 317.44s (0:05:17)

$ uv run ruff format --check . && uv run ruff check .
331 files already formatted
All checks passed!

$ DECODE_ENV=local LLM_PROVIDER=gemini make eval-benchmark ARGS='--task 002-regex-extraction --trials 2 --job-name bench-qa-161'
...
│ reward: 1.0000 (avg)  pass_at_1: 1.0000  pass_at_k: 1.0000  pass_hat_k: 1.0000
│ flaky_rate: 0.0000  success_per_dollar: 11.8430  mean_cost_usd: 0.0844  infra_error_rate: 0.0000
evals benchmark: experiment bench-qa-161 logged under decode-evals; trial dirs in .decode/evals/runs/bench-qa-161.
```

**Other issues found**
- `evals/harness/benchmark.py::new_job_dir` does not validate `--job-name` — `..`/path-separator components escape `.decode/evals/runs/` (demonstrated: `.decode/evil/`). Low severity (operator-typed local CLI flag, not remote input), no AC requires it; recommend a small follow-up to reject or contain it.
- QA debris disclosure: an aborted adversarial run (`--job-name '../../evil'`, no `--task` filter → real 19-task selection) left a partial Opik experiment literally named `../../evil` with ~3 trials of real docker+gemini spend (~$0.20) in the `decode-evals` project, plus two intentional smoke experiments `bench-qa-161` / `bench-qa-161-clean`. All local filesystem side effects (`.decode/evil/`, the orphaned docker container `quizzical_easley`, the orphaned `decode run` subprocess) were found and removed before finishing this review; the Opik-side experiments are harmless leftover data, same category as the SWE's own `bench-smoke-161*` entries already in that project.
- `make ci` as a single gate is red on this branch, but for a documented reason external to the diff (live Gemini 503, unrelated test file/commit, `skipif`-gated) — flagging so this doesn't get miscounted as a regression by a later reviewer running `make ci` verbatim.

**VERDICT: PASS**

### [SWE] 2026-09-11 08:40 — Fixes (Tester's `--job-name` finding)

**Files modified**
- `evals/harness/benchmark.py` — `JOB_NAME_RE` / `JOB_NAME_RULE` + `validate_job_name()`, called from
  inside `new_job_dir()` so EVERY caller of the job dir is guarded, not just the CLI. A job name must be
  one path segment matching `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` — it is both a directory under
  `.decode/evals/runs/` and the Opik experiment name, so the rule keeps it contained AND readable.
- `evals/run.py` — `--job-name` gains a `_validate_job_name` Click callback that re-dresses the harness's
  `ValueError` as `click.BadParameter`, so a bad name is refused with the same one-line
  `Invalid value for '--job-name': …` shape the other flags produce, BEFORE `opik.Opik()` (no keys needed)
  and before any billed trial starts. `validate_job_name` is imported inside the callback body, so
  `--help` (an eager option that exits first) still imports no opik.
- `tests/unit/evals/harness/test_benchmark.py`, `tests/unit/evals/test_run.py` — regression tests, written
  red before the fix.

**Tests**
- Unit: 2692 passing, 1 skipped (`fastapi` absent) — `make pre-commit` (format + lint + full unit run).
- `uv run pytest tests/unit/evals -q` → 673 passing.
- Integration: N/A — no infra touched by this fix (the last full run stands: 114 passing).

**Acceptance criteria**
- Unchanged from the Tester's pass; the `[HUMAN]` modal leg stays open.
- The Tester's "Other issues found" `--job-name` traversal is now closed —
  `test_benchmark.py::test_a_job_name_that_is_not_one_path_segment_is_refused` (`../../evil`, `a/b`, ``,
  `.`, `-lead`, 65 chars), `::test_a_readable_job_name_is_accepted` (`bench-20260911-120000`),
  `::test_the_default_job_name_passes_its_own_guard`, and
  `test_run.py::test_benchmark_subcommand_refuses_a_traversing_job_name`, which also asserts
  `run_benchmark` is NEVER called — the regression the Tester actually hit was a real 19-task
  docker+gemini job that had already started writing into `.decode/evil/`.

**Evidence**

```
$ uv run python -m evals benchmark --job-name '../../evil'
Error: Invalid value for '--job-name': a job name must be a single path segment matching
[A-Za-z0-9][A-Za-z0-9._-]{0,63} (no '/' and no '..'); got '../../evil'.      (exit 2, nothing started)

$ uv run python -m evals benchmark --job-name 'a/b'                          (exit 2, same line)

$ uv run python -m evals benchmark --job-name bench-ok --task nonexistent-task-xyz
Error: no benchmark task matched (task='nonexistent-task-xyz', difficulty=None); 19 task(s) available.

$ ls .decode        # no `evil` dir — MEMORY.md evals logs outputs sandbox sessions skills

$ make pre-commit
2692 passed, 1 skipped in 53.40s   (ruff format --check + ruff check clean)
```

**Notes**
- Chokepoint check before writing: `grep -rn "new_job_dir|job_name|job_dir" evals --include="*.py"` — only
  `run.py` (the flag) and `trial.py` (which receives an already-built `job_dir`) touch it, so
  `new_job_dir()` is the single place every path routes through. One guard, not one per caller.
