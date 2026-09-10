---
id: 161-opik-benchmark-job
feature: evals-v2
status: pending
---

# Opik Benchmark Job: `evaluate()` over trials, pass@k on Opik's axis, CLI + Makefile

Tags: `evals`, `benchmark`, `opik`
Depends on: 159, 160
Blocks: 165

## Scope
Wire the trial runner into Opik as ADR-0022 §6 describes, replace the old benchmark path end to end, keep `make eval-benchmark` the one entry point.

## Acceptance criteria
- [ ] `evals/harness/datasets.py`: `BENCHMARK_DATASET_NAME = "decode-benchmark-v2"`; item = `{task_id, difficulty, category, tags, instruction, checksum}` with `checksum` = sha256 over the task folder's files in sorted relative-path order; `sync --benchmark` upserts (Opik dedupes unchanged content).
- [ ] `evals/harness/benchmark.py::run_benchmark(*, task_id, difficulty, sandbox, trials, threads, job_name, model, client=None) -> BenchmarkRun(result, job_dir)`: `evaluate(dataset, task=<trial fn>, scoring_metrics=[RewardMetric()], trial_count=trials, task_threads=threads, experiment_scoring_functions=[...], experiment_config={model, provider, git_sha, sandbox, decode_version, kitaru_agent_id, trials, job_name}, project_name=settings.eval_project_name, dataset_item_ids=<selected>, experiment_name=job_name)`. The `experiment_scoring_functions` signature is verified against the installed 2.2.36 and pinned by a unit test with a fake result; `kitaru_agent_id` is `None` when `KITARU_AGENT_ID` is unset (never a placeholder).
- [ ] The trial fn maps item → `run_trial(task, sandbox=…, job_dir=<.decode/evals/runs/<job>>, trial_id=f"{task_id}__{uuid4().hex[:8]}", model=…)` and returns `result.json`'s dict; it never raises (an unexpected exception becomes `infra_error` with the reason).
- [ ] `RewardMetric.score(status, reward, reason, **_)` → `ScoreResult(name="reward", value=reward, reason=reason)` for `agent_ok|agent_fail`; `ScoreResult(name="reward", value=0.0, scoring_failed=True, reason=reason)` for `infra_error`; a unit test proves the aggregate excludes the failed score.
- [ ] `evals/harness/aggregates.py` keeps only pure functions — `pass_at_1`, `pass_at_k` (documented as Harbor's unbiased estimator, which equals `any()` when n == k), `pass_hat_k`, `is_flaky`, `success_per_dollar`, plus new `infra_error_rate`, `mean_cost_usd`; `DEFAULT_PASS_METRIC = "reward"`; `attach_experiment_aggregates`, `extract_trial_outcomes`'s Opik glue, and the opik-1.9.8 docstring deleted; the Rich table gains per-difficulty rollup rows and a totals row (n, pass@1, pass@k, pass^k, flaky, mean cost, infra errors).
- [ ] `python -m evals benchmark [--task ID] [--difficulty D] [--sandbox docker|modal] [--trials K] [--threads N] [--job-name NAME] [--model ID]`: `--threads` defaults 1 for docker, 4 for modal; `--job-name` defaults `bench-<UTC yyyymmdd-HHMMSS>`; prints the table, the experiment name, and the job dir path; `--nb-samples` removed. `--help` imports no opik.
- [ ] `make eval-benchmark` unchanged in shape (`keys` guard, `ARGS=`); `.gitignore` needs no change (`.decode/*` covers `.decode/evals/`); a unit test asserts the job dir resolves under `.decode/evals/runs/`.
- [ ] `tests/unit/evals/harness/test_benchmark.py` rewritten against a fake `run_trial` and a fake Opik client; `test_aggregates.py` covers the two new functions.
- [ ] [HUMAN] `make eval-benchmark ARGS='--task 001-find-and-replace --trials 2'` with docker: two Trial Dirs, an Opik experiment `bench-…` under `decode-evals` with `reward` scores and the aggregates on the experiment row; `--sandbox modal --task 001-find-and-replace` once, same evidence. Ids logged here.

## Out of scope
- Regression track (162). Cohort creation (165). Per-task judges (deleted for good).

## Log
### [PA] 2026-09-10 — Grooming
Opik 2.2.36 `evaluate()` signature verified: `trial_count`, `experiment_scoring_functions`, `experiment_tags`, `error_tolerance` exist — no more post-hoc feedback-score attachment. `success_per_dollar` reads `cost_usd` from the trial summary; when the provider reports no cost it is `None` and the table says "n/a", never 0. Subprocess trials are independent, so `task_threads > 1` is safe; docker defaults to 1 only for warm-up CPU.
