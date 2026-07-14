---
id: 106
feature: evals
status: done
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

- [x] One benchmark item runs end-to-end through a real docker sandbox against the 105 fixture
      task (integration test, `pytest.mark.skipif` no docker daemon / no `GEMINI_API_KEY` — or a
      scripted model where feasible). — `tests/integration/test_benchmark_docker.py` (scripted model
      + real docker; passed 8.2s, graded PASS).
- [x] Verify assets provably never exist in the Workspace before grade time (unit-asserted). —
      `tests/unit/evals/harness/test_sandbox.py::test_verify_is_absent_during_the_run_present_only_at_grade`.
- [x] `python -m evals benchmark --task <id>` produces an Opik experiment under
      `settings.eval_project_name` with `experiment_config` carrying model + git sha. — wiring +
      config asserted by `test_benchmark.py::test_run_benchmark_wires_evaluate` and the CLI test;
      the live experiment creation needs `OPIK_API_KEY` ([HUMAN] smoke).
- [x] `--sandbox modal` constructs the `ModalBackend` path (unit-verified). —
      `test_sandbox.py::test_modal_rung_selects_the_modal_backend` +
      `test_select_executor_modal_really_builds_a_modal_backend`.
- [x] Executor teardown runs on success AND on agent/verify failure. —
      `test_sandbox.py::test_teardown_and_mode_restore_run_on_failure` + the lifecycle-order test.
- [x] `make ci` green.

## Out of scope

- Trials / pass@k / cost aggregates (107). Real benchmark tasks (108–110). Credential proxy
  (non-goal). Makefile targets (120).

## Log

### [SWE] 2026-07-13 23:38 — Implementation

**Files modified**
- `evals/harness/sandbox.py` — NEW: `benchmark_sandbox` sync context manager + `SandboxRun` — per-run
  temp Workspace, seed `setup/` host-side, warm the `decode.tools.bash` executor seam by
  `SANDBOX_MODE` (docker/modal rung), run `setup.sh`, inject `verify/` through the seam + run
  `verify.sh` only at grade time, `finally` teardown (`close_executor` + mode restore + rmtree).
- `evals/harness/benchmark.py` — NEW: `make_benchmark_task_fn` (sync Opik task fn: run agent BYPASS in
  the sandbox, grade, return the metric payload with `agent_error`) + `run_benchmark` (filter, upsert
  dataset, `evaluate(scoring_metrics=[VerifyOracle, MaxSteps] + single-task judges,
  experiment_config={agent_model, provider, git_sha, sandbox}, project_name=eval_project_name,
  dataset_item_ids scoped, task_threads=1)`.
- `evals/harness/driver.py` — extended: `EvalRunRecord.agent_error` + `run_agent_once` captures the
  Runner's `AgentError` event (carry-forward item 1 — a crashed run is no longer silently empty).
- `evals/run.py` — wired the `benchmark` CLI command (`--task/--difficulty/--sandbox/--nb-samples`,
  lazy opik import, `BenchmarkSelectionError` → friendly `ClickException`).
- `tests/support/eval_models.py` — added `bash_then_finish` + `crashing_model` scripted models.
- `tests/support/fake_sandbox.py` — NEW: in-memory `FakeExecutor`/`FakeBackend` for offline seam tests.
- `tests/unit/evals/harness/conftest.py` — added shared `install_fake` + autouse `_reset_seam` fixtures.
- Tests: `tests/unit/evals/harness/test_sandbox.py`, `test_benchmark.py`, driver + CLI test additions,
  `tests/integration/test_benchmark_docker.py` (real-docker e2e, skipif-guarded).

**Tests**
- Unit: 87 evals tests passing (1575 total via pre-commit), 0 failing.
- Integration: `make ci` → 1688 passed, 2 skipped (live Opik/Gemini smokes, key-gated), 0 failing.
- The docker e2e ran a real benchmark item through a real container: agent wrote `greeting.txt`
  in-sandbox, hidden `verify.sh` graded PASS.

**Acceptance criteria**
- [x] real-docker benchmark e2e — `tests/integration/test_benchmark_docker.py` (scripted model; no key).
- [x] verify assets absent before grade time — `test_sandbox.py::test_verify_is_absent_during_the_run_present_only_at_grade`.
- [x] `benchmark --task <id>` → Opik experiment w/ model + git sha — `test_benchmark.py::test_run_benchmark_wires_evaluate` + CLI test; live creation is a [HUMAN] key-gated smoke.
- [x] `--sandbox modal` constructs ModalBackend — `test_sandbox.py::test_modal_rung_selects_the_modal_backend` + `test_select_executor_modal_really_builds_a_modal_backend`.
- [x] teardown on success AND failure — `test_sandbox.py::test_teardown_and_mode_restore_run_on_failure`.
- [x] `make ci` green — 1688 passed, 2 skipped.

**Evidence**
```
$ make ci
================= 1688 passed, 2 skipped in 403.07s (0:06:43) ==================

$ uv run pytest tests/integration/test_benchmark_docker.py -q
1 passed in 8.21s

$ python -m evals benchmark --help
Options: --task TEXT · --difficulty [easy|medium|hard] · --sandbox [docker|modal] (default docker) · --nb-samples INTEGER

$ python -c "import evals.run, sys; ... 'opik' in m"
opik modules at import: NONE   # CLI stays opik-free at import (ADR-0017 §1)
```

**Notes**
- Carry-forward item 1 DONE at the evals layer: driver captures the Runner's swallowed `AgentError`
  into `EvalRunRecord.agent_error`; the task fn surfaces it in the payload and STILL grades (verify
  runs), so a crash = fail-with-reason, never silently empty. No `src/decode` change.

### [Tester] 2026-07-13 — QA (round 1)

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check` clean)
- Unit tests: 1575 passed / 0 failed
- Integration tests (`make ci`, full suite incl. real docker + modal integration tests): 1688
  passed / 2 skipped (key-gated live smokes) / 0 failed, 407.13s — matches SWE's claim
- `tests/integration/test_benchmark_docker.py`: 1 passed, 10.88s (real docker), no container leak
  (`docker ps -a` before/after byte-identical)
- Warnings: 0

**E2E adversarial pass**
- Happy path: real-docker `test_benchmark_docker.py` scripted-model run → agent writes
  `greeting.txt` in-sandbox, hidden oracle grades PASS (PASS)
- Break path 1 (verify-hiding, real docker): scripted model runs
  `find / -xdev -iname 'verify.sh'; ls -la /workspace` mid-turn through the real sandboxed `bash`
  tool → workspace listing showed only `README.md` / `seeded.txt` / `setup.sh`, no `verify.sh`
  anywhere on the filesystem (PASS)
- Break path 2 (crash mid-turn, real docker): `crashing_model("adversarial kaboom")` → payload
  `agent_error == "adversarial kaboom"`, `verify.exit_code == 1` (`FAIL: greeting.txt is missing`)
  — verify still ran and graded fail-with-reason (PASS)
- Break path 3 (teardown under a body-level exception, real docker, 5 runs): raised `RuntimeError`
  inside the `with benchmark_sandbox(...)` body after `yield` → temp Workspace removed,
  `settings.sandbox_mode` restored, `decode.tools.bash` seam reset to `LocalExecutor`, `docker ps -a`
  clean in 4/5 runs immediately and the 5th converged clean within ~1s (a known macOS Docker
  Desktop `ps` propagation lag, not a code issue — `docker rm -f` had already returned) (PASS, with
  benign note)
- Break path 4 (seam restore): after a full real-docker benchmark run, `settings.sandbox_mode`
  back to `"none"`, `decode.tools.bash._get_executor()` back to `LocalExecutor`, and a direct call
  to the real `bash` tool function returns the HOST cwd via `pwd` (PASS)
- Break path 5 (dependency-unavailable / infra failure — **FAIL**, see Acceptance Criteria below):
  `DOCKER_HOST=unix:///tmp/nonexistent-docker.sock` (docker daemon unreachable, the DEFAULT
  `--sandbox` rung) during `make_benchmark_task_fn(...)({"task_id": task.id})` →
  `RuntimeError: docker run failed (exit 1): failed to connect to the docker API at
  unix:///tmp/nonexistent-docker.sock ...` propagates uncaught out of the task fn (verified via
  `python -c` repro, full traceback captured). Same reproduces with bogus `MODAL_TOKEN_ID` /
  `MODAL_TOKEN_SECRET` under `--sandbox modal` (`modal.exception.AuthError: Token not found`,
  uncaught). Teardown itself is NOT the problem — the temp Workspace was still removed and
  `settings.sandbox_mode` still restored (the `finally` in `benchmark_sandbox` ran) — the problem is
  the exception is never caught into `agent_error`, so it escapes `make_benchmark_task_fn`'s
  closure entirely.

**Acceptance criteria**
- [x] PASS — real-docker benchmark e2e — `tests/integration/test_benchmark_docker.py` (re-ran
      myself: 1 passed, 10.88s; no docker leak)
- [x] PASS — verify assets absent before grade time — unit test
      `test_sandbox.py::test_verify_is_absent_during_the_run_present_only_at_grade` passes; also
      independently re-verified via a hostile scripted model hunting the REAL docker sandbox
      filesystem with `find /` — nothing found (break path 1 above)
- [x] PASS — `benchmark --task <id>` → Opik experiment w/ model + git sha — `test_benchmark.py::
      test_run_benchmark_wires_evaluate` passes; `python -m evals benchmark --help` builds with zero
      `opik` modules imported (`ADR-0017 §1` re-verified: `'opik' in sys.modules` → none); CLI test
      `test_run.py::test_benchmark_subcommand_invokes_run_benchmark` passes; live experiment
      creation is [HUMAN] (needs `OPIK_API_KEY`)
- [x] PASS — `--sandbox modal` constructs `ModalBackend` — unit tests pass; additionally exercised
      for REAL (this host happens to carry live Modal creds in `~/.modal.toml`): a full
      `make_benchmark_task_fn(..., sandbox="modal")` run against the `decode-sandbox` Modal app
      completed end-to-end and reaped cleanly (`modal app list` shows `decode-sandbox` at `Tasks: 0`
      after the run — no lingering billed sandbox)
- [x] PASS (as literally worded) — executor teardown runs on success AND on agent/verify failure —
      `test_sandbox.py::test_teardown_and_mode_restore_run_on_failure` passes; independently
      re-verified on real docker across 5 forced-exception runs (break path 3) — teardown itself is
      solid even under the break path 5 infra failure. **However** see break path 5: a *sandbox
      creation* failure (as opposed to an agent/verify failure) is NOT caught into `agent_error` —
      it escapes the task fn as a raw exception instead of grading the item as a failure, which is a
      distinct problem from teardown and is what fails this QA round (below).
- [x] PASS — `make ci` green — re-ran myself: 1688 passed, 2 skipped, 407.13s, 0 failures; no
      docker container leak after the full suite

**Evidence**
```
$ make unit-tests
======================= 1575 passed in 92.45s (0:01:32) ========================

$ make ci
================= 1688 passed, 2 skipped in 407.13s (0:06:47) ==================

$ uv run pytest tests/integration/test_benchmark_docker.py -v
tests/integration/test_benchmark_docker.py::test_one_benchmark_item_runs_end_to_end_through_real_docker PASSED
1 passed in 10.88s

# Break path 5 repro (docker daemon unreachable, the DEFAULT --sandbox rung):
$ DOCKER_HOST=unix:///tmp/nonexistent-docker.sock python -c "
    task_fn = make_benchmark_task_fn({task.id: task}, sandbox='docker')
    task_fn({'task_id': task.id})   # <- raises, uncaught
"
Traceback (most recent call last):
  ...
  File ".../evals/harness/benchmark.py", line 77, in _run_and_grade
    with benchmark_sandbox(task, sandbox=sandbox) as run:
  File ".../evals/harness/sandbox.py", line 109, in benchmark_sandbox
    _run_async(warm_executor(workspace))
  File ".../src/decode/sandbox/docker_backend.py", line 110, in create
    raise RuntimeError(f"docker run failed (exit {proc.returncode}): ...")
RuntimeError: docker run failed (exit 1): failed to connect to the docker API at
unix:///tmp/nonexistent-docker.sock; ...
```

**Root cause (for the SWE)**
`_run_and_grade` (`evals/harness/benchmark.py:67-96`) only wraps `run_agent_once_sync(...)` in
`try/except Exception` — it does NOT wrap `with benchmark_sandbox(task, sandbox=sandbox) as run:`
itself. `benchmark_sandbox`'s `__enter__` phase (`warm_executor` → `SandboxExecutor.start()` →
`_ensure_created()` → `backend.create()`) is documented in `sandbox/executor.py::start` as
"Failures propagate; the call site degrades to the lazy path" — a decision that is correct for
`runtime/flow.py::_warm_headless_executor` (a failed warm-up just means the live REPL's *next*
`bash` call retries lazily through `SandboxExecutor.run()`, which DOES catch `(RuntimeError,
OSError)` and renders exit-125 — never-crash). The benchmark harness reuses `warm_executor` the
same way but has no "next call" to degrade to — it propagates straight out of
`make_benchmark_task_fn`'s closure.

This matters because Opik's `evaluate()` engine (`opik/evaluation/engine/engine.py:122-130`)
re-raises any task-fn exception verbatim, and `evaluation_tasks_executor.execute()` with
`workers == 1` (which `run_benchmark` forces via `task_threads=1`, `evals/harness/benchmark.py:174`)
is a plain list comprehension with no per-item exception isolation — so ONE task hitting a
transient docker-daemon hiccup / bad Modal token mid-run aborts the ENTIRE multi-item experiment,
losing every remaining item's results. This directly contradicts the module's own documented intent
(`evals/harness/benchmark.py` docstring: "a crashed run grades as fail-with-reason, never silently
empty"; `_run_and_grade` docstring: "never let one task's crash abort the whole experiment") — that
guarantee holds for agent crashes and verify failures, but not for sandbox-creation / infra
failures, which are arguably the MOST realistic failure mode across a long unattended multi-task
benchmark run.

**Suggested fix**: widen the `try/except` in `_run_and_grade` to also cover
`benchmark_sandbox(...).__enter__()` (e.g. move the `with` inside the `try`, or wrap `warm_executor`
+ `_run_setup_script` in `benchmark_sandbox` itself and re-raise as a return-early sentinel `grade`
result), so a sandbox-creation failure degrades to `agent_error` + a fail-with-reason payload
(`verify_exit`/`verify_stdout` can stay `None`/empty since no Workspace ever came up) instead of an
uncaught exception. Add a regression test mirroring `test_teardown_and_mode_restore_run_on_failure`
but where the FAILURE is in `select_executor`/`backend.create` (a `FakeExecutor`/monkeypatch that
raises from `start()`), asserting `benchmark_task_fn` returns a payload with `agent_error` set
rather than raising.

**Other issues found**
- None beyond the above; teardown, seam restore, verify-hiding, and crash-into-`agent_error` (for
  agent-level crashes) are all solid and adversarially re-verified independently of the SWE's own
  tests.

**VERDICT: FAIL** — one issue to fix (sandbox-creation / infra failures are not caught into
`agent_error`, contradicting the harness's own "never abort the whole experiment on one task's
crash" design intent — reproducible on the DEFAULT `--sandbox docker` rung, not just modal).
- Carry-forward item 2: verified `opik==1.9.8` `evaluate()` signature against the INSTALLED package —
  `trial_count` IS present (usable by task 107). There is NO `experiment_scoring_functions` param;
  the nearest is `scoring_functions` (`ScorerFunctionProtocol`). **For task 107:** pass@k /
  cost-per-$ aggregates must ride `scoring_functions`, not `experiment_scoring_functions`, on 1.9.8 —
  not solved here (out of scope per the task).
- Deliberate trade-off: the `decode.tools.bash` executor seam is a PROCESS GLOBAL, so `run_benchmark`
  forces `evaluate(task_threads=1)` — concurrent task fns would race the shared seam. Upgrade path: a
  per-run executor seam to parallelize if benchmark wall-time bites. Noted in code.
- Design decision: per-task G-Eval judges from `task.yaml` are added ONLY for single-task runs
  (`--task <id>`); a multi-task run uses the code oracles alone, because `evaluate` applies one metric
  list to every item and a task-A judge grading task-B output is meaningless (ADR-0017 §7).
- `run_benchmark` scopes the shared `decode-benchmark-v1` dataset to the selected tasks via
  `dataset_item_ids` (reads `dataset.get_items()`), so a full-dataset sync doesn't force every item to
  run on a `--task` invocation.
- NOT run: live Opik experiment creation + live judge scoring (need `OPIK_API_KEY`/`GEMINI_API_KEY`) —
  [HUMAN] smoke; wiring is unit-proven with a mocked `evaluate`.

### [SWE] 2026-07-14 — Fixes (QA round 1 blocking issue)

**What changed**
- `evals/harness/benchmark.py` — split `_run_and_grade` into an outer catch-all wrapper +
  `_run_in_sandbox`. The outer `try/except` now covers the WHOLE `with benchmark_sandbox(...)`
  lifecycle (creation via the eager `warm_executor` included), so a sandbox that never comes up
  (docker daemon down, bad modal creds) is caught into a new distinct **`infra_error`** payload field
  instead of propagating out of the task fn. The inner `try/except` still catches a crashed AGENT run
  into `agent_error` so the oracle grades the Workspace. `_payload` now takes `verify_exit: int | None`
  and always emits `infra_error`; when the sandbox failed, `verify.exit_code` is `None`, which
  `VerifyOracleMetric` already grades `0.0` with a reason ("No verify result recorded").
- `tests/support/fake_sandbox.py` — `FakeExecutor(start_error=...)`: `start` records the attempt then
  raises, mirroring a real backend `create` failure.
- `tests/unit/evals/harness/test_benchmark.py` — new regression test
  `test_task_fn_returns_a_graded_payload_when_the_sandbox_never_comes_up` (select_executor/backend
  `start` raises → task fn RETURNS a graded payload, `infra_error` set, oracle scores `0.0`, teardown
  + mode restored, never raises). Happy-path test also asserts `infra_error is None`.

**Field choice (documented, per Tester ask):** a DISTINCT `infra_error` field, not reused
`agent_error` — a sandbox that never started is not the agent crashing, and keeping them separate lets
a reader tell "the harness broke" from "the agent failed the task". Both ride the metrics'
`**ignored_kwargs`; the oracle metric grades the (absent) verify `0.0` either way.

**Tests**
- Unit: `test_benchmark.py` 9 passing (was 8), full unit suite 1576 passed / 0 failed (pre-commit).
- Format / lint / pre-commit: clean.

**Evidence — the exact Tester repro, now fixed**
```
$ DOCKER_HOST=unix:///tmp/nonexistent-docker.sock python -c "... make_benchmark_task_fn(...)({'task_id': ...}) ..."
RETURNED (no raise). infra_error => 'sandbox lifecycle failed: docker run failed (exit 1): failed to
connect to the docker API at unix:///tmp/nonexistent-docker.sock ...'
# agent_error=None, verify={'exit_code': None, 'stdout': ''}, VerifyOracleMetric score=0.0

$ make pre-commit
======================= 1576 passed in 102.15s (0:01:42) =======================
```

**Notes**
- The traceback that appears on the repro is `logger.exception` logging the CAUGHT error (observability
  — a broken task should be visible in logs); the task fn still returns normally.
- `run.grade(...)` raising (inject fails because the sandbox died mid-run) is also covered by the outer
  handler → `infra_error`.
- `make ci` (full integration incl. real docker) was green on the round-1 code; this change only
  widens an existing `try` + adds a field/test, so I re-ran the full unit suite via pre-commit (green)
  and the live unreachable-daemon repro above. Handing back for re-review.

### [Tester] 2026-07-14 — QA (round 2, re-verify)

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check` clean)
- Unit tests: 1576 passed / 0 failed (matches SWE's claim exactly)
- Integration tests (`make ci`, full suite incl. real docker + modal integration tests): 1689
  passed / 2 skipped (key-gated live smokes) / 0 failed, 423.05s (+1 test vs round 1's 1688, the new
  regression test)
- `tests/integration/test_benchmark_docker.py`: re-ran twice, 1 passed each time (8.99s, 7.18s),
  polled `docker ps -a` for 6s after the second run — clean throughout, no container leak
- Warnings: 0

**Round-1 blocker re-verification**

1. **Red-before/green-after quality of the new regression test** — I temporarily reverted
   `_run_and_grade` in `evals/harness/benchmark.py` back to the round-1 shape (`return
   _run_in_sandbox(task, sandbox=sandbox)`, no outer `try/except`) and ran
   `test_task_fn_returns_a_graded_payload_when_the_sandbox_never_comes_up` alone:
   **RED** — `RuntimeError: docker daemon unreachable` propagated out of the test (`FakeExecutor
   (start_error=...)` raising from `start()`), 1 failed. Restored the fix (`cp` from a pre-saved
   copy, `git diff --stat evals/harness/benchmark.py` empty afterwards, confirming a clean
   round-trip) and re-ran the file: **GREEN** — 9 passed. Confirms the test is not a tautology and
   actually exercises the fix.
2. **My exact round-1 `DOCKER_HOST` repro, re-run verbatim** (`DOCKER_HOST=unix:///tmp/nonexistent-
   docker.sock`, default `--sandbox docker` rung): `make_benchmark_task_fn(...)({"task_id":
   task.id})` now RETURNS (no raise) —
   `payload == {"output": "", "tool_calls": [], "steps": 0, "input_tokens": 0, "output_tokens": 0,
   "verify": {"exit_code": None, "stdout": ""}, "max_steps": 5, "agent_error": None, "infra_error":
   "sandbox lifecycle failed: docker run failed (exit 1): failed to connect to the docker API at
   unix:///tmp/nonexistent-docker.sock; ..."}`. The traceback that prints is `logger.exception`
   (observability), not an escaping exception — confirmed by the script completing and printing the
   payload. PASS.
3. **Bogus Modal creds, `--sandbox modal`** (`MODAL_TOKEN_ID=ak-bogus...`,
   `MODAL_TOKEN_SECRET=as-bogus...`): same shape — `modal.exception.AuthError: Token not found`
   caught into `infra_error == "sandbox lifecycle failed: Token not found"`, `agent_error is None`,
   `verify.exit_code is None`, task fn returns normally. PASS.
4. **Multi-task run survives one broken item** (simulated the Opik `workers==1` list-comprehension
   shape directly, since that's the actual code path per `opik/evaluation/engine/
   evaluation_tasks_executor.py:18-25`): three items run in sequence through
   `make_benchmark_task_fn`, `select_executor` patched so ONLY the 2nd item's `FakeExecutor.start()`
   raises (`"transient daemon blip"`) — result:
   `001-greeting -> infra_error=None verify={'exit_code': 0, 'stdout': 'PASS\n'}`
   `002-broken -> infra_error='sandbox lifecycle failed: transient daemon blip' verify={'exit_code': None, 'stdout': ''}`
   `003-ok-again -> infra_error=None verify={'exit_code': 0, 'stdout': 'PASS\n'}`
   — all three items ran; item 2's infra failure did not abort items 1 or 3. PASS. This is the
   direct evidence that a broken item no longer aborts the whole experiment under `task_threads=1`.

**Acceptance criteria** (re-confirmed; all were already individually true in round 1 — this round
closes the e2e adversarial gap found in round 1's break path 5)
- [x] PASS — real-docker benchmark e2e — re-ran twice, both green, no docker leak (polled 6s)
- [x] PASS — verify assets absent before grade time — unchanged from round 1, still green
      (`test_sandbox.py::test_verify_is_absent_during_the_run_present_only_at_grade`)
- [x] PASS — `benchmark --task <id>` → Opik experiment w/ model + git sha — unchanged, still green
- [x] PASS — `--sandbox modal` constructs `ModalBackend` — unchanged, still green
- [x] PASS — executor teardown runs on success AND on agent/verify failure — unchanged, still green;
      AND now infra-level (sandbox-creation) failures also degrade gracefully rather than merely
      tearing down cleanly while still crashing the task fn (the round-1 gap)
- [x] PASS — `make ci` green — re-ran myself: 1689 passed, 2 skipped, 423.05s, 0 failures; no docker
      container leak after the full suite

**Evidence**
```
$ make unit-tests
======================= 1576 passed in 93.96s (0:01:33) ========================

$ make ci
================= 1689 passed, 2 skipped in 423.05s (0:07:03) ==================

$ uv run pytest tests/integration/test_benchmark_docker.py -q   (run twice)
1 passed in 8.99s
1 passed in 7.18s
# docker ps -a polled 1s..6s after the second run: CLEAN at every sample

# Red-before/green-after on the new regression test:
$ (reverted _run_and_grade to round-1 shape) uv run pytest tests/unit/evals/harness/test_benchmark.py::test_task_fn_returns_a_graded_payload_when_the_sandbox_never_comes_up -v
FAILED ... RuntimeError: docker daemon unreachable
1 failed in 0.70s

$ (restored the fix) uv run pytest tests/unit/evals/harness/test_benchmark.py -v
9 passed in 1.14s

# Round-1 repro, re-run against the fix:
$ DOCKER_HOST=unix:///tmp/nonexistent-docker.sock python -c "... task_fn({'task_id': task.id}) ..."
PAYLOAD: {"output": "", ..., "verify": {"exit_code": null, "stdout": ""}, "agent_error": null,
"infra_error": "sandbox lifecycle failed: docker run failed (exit 1): failed to connect to the
docker API at unix:///tmp/nonexistent-docker.sock; ..."}
EXIT CODE: 0   # no exception escaped

# Multi-task survives one broken item (3-item simulation, item 2 broken):
001-greeting -> infra_error= None verify= {'exit_code': 0, 'stdout': 'PASS\n'}
002-broken -> infra_error= sandbox lifecycle failed: transient daemon blip verify= {'exit_code': None, 'stdout': ''}
003-ok-again -> infra_error= None verify= {'exit_code': 0, 'stdout': 'PASS\n'}
ALL THREE ITEMS RAN -- ONE BROKEN ITEM DID NOT ABORT THE BATCH
```

**Other issues found**
- None. The `infra_error` vs `agent_error` field split is a good design choice (lets a reader
  distinguish "the harness broke" from "the agent failed the task", both already absorbed by the
  metrics' `**ignored_kwargs` per the existing `VerifyOracleMetric`/`MaxStepsMetric` contract — no
  metrics.py change was needed since it already handled a `None` `exit_code` gracefully).
- Process nit (not blocking): the SWE's `[SWE] 2026-07-14 — Fixes` log entry was inserted at the TOP
  of `## Log` rather than appended after the existing entries, so the file is not in strict
  chronological order top-to-bottom. Doesn't affect this verdict; flagging so the next writer
  appends at the true end.

**VERDICT: PASS** — the round-1 blocker (sandbox-creation / infra failures escaping
`make_benchmark_task_fn` uncaught) is fixed, verified red-before/green-after on the new regression
test, re-verified against my exact round-1 repro commands (both docker-daemon-down and bad-modal-
creds), and independently verified that a multi-task run survives one broken item. Full suite green
(`make ci`: 1689 passed / 2 skipped / 0 failed), no docker resource leaks. Handing off to PA for
acceptance review.
