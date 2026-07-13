---
id: 106
feature: evals
status: pending
---

# Benchmark runner: sandboxed execution + Opik evaluate wiring

Depends on: 103, 104, 105. Implements ADR-0017 §3,4,5.

## Scope

**`evals/harness/sandbox.py`** — per-task-run sandbox lifecycle reusing decode's OWN seam:

- Fresh Workspace per task run: a per-run temp dir handed to
  `SandboxExecutor(DockerBackend(...)).start(workspace)` (the executor accepts an explicit
  workspace — `sandbox/executor.py`); `--sandbox modal` selects `ModalBackend` instead (the rung
  flag; same seam, zero new abstraction).
- Seed: copy `setup/` host-side into the workspace before `start()` (the modal backend uploads the
  tree at create, same as skills seeding); run `setup/setup.sh` via `executor.run(...)` after
  create so it works on both backends.
- Wire the executor into `bash`'s module seam for the run (`decode.tools.bash`: `warm_executor` /
  `reset_executor` / `close_executor` + `decode.sandbox.select_executor` — the
  `runtime/flow.py::_warm_headless_executor` pattern) and point `deps.cwd` at the workspace so
  file tools ride the seam too; restore + teardown in a `finally` (mirror
  `_reap_runtime_executor`'s dedicated-loop caution where needed — the task fn is sync).
- Grade: AFTER the agent finishes, inject `verify/` through the seam, `executor.run("bash
  verify.sh", ...)`, record exit code + stdout, teardown. verify.sh IS the grading logic —
  transparent by design.

**`evals/harness/benchmark.py`** — the Opik glue:

- `benchmark_task_fn(item) -> dict` (sync; `run_agent_once_sync`): full lifecycle above, returns
  `{output, tool_calls, steps, input_tokens, output_tokens, verify_exit, verify_stdout}`.
- `run_benchmark(...)`: `evaluate(dataset=get_or_create_dataset("decode-benchmark-v1"),
  task=benchmark_task_fn, scoring_metrics=[VerifyOracleMetric(), MaxStepsMetric(), ...] + per-task
  G-Eval judges from task.yaml, experiment_config={agent model, provider, git sha
  (`git rev-parse HEAD`), settings version bits}, project_name=settings.eval_project_name)`.
  Filters: `--task <id>`, `--difficulty`, `--sandbox docker|modal` (default docker) on
  `python -m evals benchmark`.
- Agent runs under a BYPASS gate + headless deny resolvers (unattended; matches the headless
  runtime posture, ADR-0008 §2).

**Tests** (offline, `tests/unit/evals/harness/`): task fn with a fake executor + scripted model —
assert seed→run→inject→verify ordering (verify assets absent during the run, present only at grade
time), returned payload shape, teardown on failure; backend selection for the modal rung
(construction only, no real modal call); `evaluate` wiring with `opik.evaluation.evaluate` mocked.

## Acceptance Criteria

- [ ] One benchmark item runs end-to-end through a real docker sandbox against the 105 fixture
      task (integration test, `pytest.mark.skipif` no docker daemon / no `GEMINI_API_KEY` — or a
      scripted model where feasible).
- [ ] Verify assets provably never exist in the Workspace before grade time (unit-asserted).
- [ ] `python -m evals benchmark --task <id>` produces an Opik experiment under
      `settings.eval_project_name` with `experiment_config` carrying model + git sha.
- [ ] `--sandbox modal` constructs the `ModalBackend` path (unit-verified).
- [ ] Executor teardown runs on success AND on agent/verify failure.
- [ ] `make ci` green.

## Out of scope

- Trials / pass@k / cost aggregates (107). Real benchmark tasks (108–110). Credential proxy
  (non-goal). Makefile targets (120).

## Log
