---
id: 160-benchmark-trial-runner
feature: evals-v2
status: done
---

# Trial runner: seed → `decode run` → hand-back → host-side pristine verify → Trial Dir

Tags: `evals`, `benchmark`, `sandbox`
Depends on: 156, 157
Blocks: 161

## Scope
The Harbor-shaped trial (ADR-0022 §1, §3, §4, §7) as ONE module, `evals/harness/trial.py`, with no Opik import and no sandbox-seam import — the sandbox is inside the `decode run` subprocess; the Verifier runs host-side on a pristine clone. Unit-tested against a fake `decode` command.

Trial Dir: `<harness_home>/.decode/evals/runs/<job>/<task-id>__<8-hex>/` (harness_home = the launch cwd, already git-ignored via `.decode/*`):
```
seed/         the Seed Repo (`--repo` target; Hand-back pushes decode/<short> here)
home/         throwaway Harness Home for the subprocess (its .decode/sandbox is the Workspace clone; sessions + logs = evidence)
pristine/     git clone of decode/<short> (or base) + tests/ overlay + .verifier/
agent/        stdout.txt · stderr.txt · summary.json
verifier/     test-stdout.txt · test-stderr.txt · reward.txt
result.json
```

## Acceptance criteria
- [x] `run_trial(task, *, sandbox: Literal["docker","modal"], job_dir: Path, trial_id: str, model: str | None = None, decode_command: Sequence[str] = (sys.executable, "-m", "decode")) -> TrialResult` performs, with per-phase timings:
      1. **seed** — `seed_task_repo(task, <trial>/seed)` (157); records `base_sha`.
      2. **run** — `Popen([*decode_command, "run", task.instruction, "--repo", <seed>, "--local", "--max-requests", str(task.max_steps), "--summary-json", <trial>/agent/summary.json, *(["--model", model] if model)], cwd=<trial>/home, start_new_session=True)`; stdout/stderr to files; wall clock `task.agent_timeout_sec` → SIGINT to the process group (so decode's `finally` reaps the sandbox and hands back), 60 s grace, then SIGKILL; `timed_out=True` recorded.
      3. **collect** — read `summary.json`; branch = `summary["handback"]["branch"]` or `None` (skipped ⇒ base).
      4. **verify** — `git clone [--branch <branch>] <seed> <trial>/pristine`; overlay `tests/` → `pristine/tests/`; mkdir `pristine/.verifier`; `bash tests/test.sh` with cwd=pristine, `VERIFIER_DIR` absolute, timeout `task.verifier_timeout_sec`; parse `reward.txt`; copy verifier outputs to `verifier/`.
      5. **record** — `result.json` `{task_id, trial_id, status, reason, reward, timed_out, base_sha, branch, agent: {git_sha, decode_version, model, provider, sandbox}, summary: <agent/summary.json or null>, timings: {seed, run, verify}}`, written in a `finally` (an Infra Error still leaves evidence).
- [x] Child env = the parent's `os.environ` + every set `Settings` field exported under its env-var name (secrets unwrapped) so a key that lives only in the repo `.env` reaches a subprocess launched from a cwd without one; overrides `SANDBOX_MODE=<sandbox>`, `OPIK_PROJECT_NAME=<settings.eval_project_name>` (live tracing is never polluted), `RUNTIME_ENABLED=true`; removes `KITARU_TASK_ID`, `SANDBOX_REPO`, `KITARU_REPLAY_ID`. `KITARU_AGENT_ID` passes through (recording rides the Recording Seam). One pure `child_env(...)` helper, unit-tested for the overrides/removals and that no `Settings` value leaks into `result.json`.
- [x] `src/decode/__main__.py` added (3 lines: `init_logger()` then `cli()`), so the trial runs the harness's own interpreter/venv.
- [x] Taxonomy pinned by tests: `status ∈ agent_ok | agent_fail | infra_error`. `agent_ok` ⇔ reward == 1. `agent_fail` = summary present and reward < 1 — including `exit_reason ∈ request_limit|error`, a timeout, a skipped Hand-back (base graded). `infra_error` = seed failure, subprocess exited with NO `summary.json` **and did not time out** (a timeout is always `agent_fail` — ADR-0022 §4), clone failure, verifier timeout/crash, `reward.txt` missing/empty/non-numeric/out of range — each with a one-line `reason`. A timeout is never an Infra Error.
- [x] The agent never sees `tests/` or `solution/`: a test launches the fake `decode` and asserts neither path exists under `<seed>` nor `<trial>/home`; the pristine overlay happens only after the subprocess exits.
- [x] Verifier is host-side only: `trial.py` imports nothing from `decode.sandbox` / `decode.tools`; a test asserts the module's imports.
- [x] `evals/harness/sandbox.py` and `tests/unit/evals/harness/test_sandbox.py` deleted; `tests/integration/test_benchmark_docker.py` deleted (its subject is gone); `driver.py` no longer imported by anything benchmark-side.
- [x] `tests/unit/evals/harness/test_trial.py` uses a fake `decode_command` (a Python script) covering: oracle-as-agent (clones seed, runs `solve.sh`, commits, pushes `decode/<short>`, writes summary) → `agent_ok`, reward 1, complete Trial Dir; nop (summary with `handback: null`) → `agent_fail`, base graded; no summary → `infra_error`; hang → SIGINT path, `timed_out`; verifier writing garbage → `infra_error`.

## Out of scope
- Opik and parallelism policy (161). Modal e2e (manual, runbook). Container cleanup after a SIGKILL (documented in the runbook; SIGINT-first makes it rare).

## Log
### [PA] 2026-09-10 — Grooming
`decode run --repo <local path> --local` clones host-side into `<home>/.decode/sandbox`, uploads it to Modal or bind-mounts it for docker, and Hand-back pushes host-side to `origin` = the seed path (ADR-0012 §8) — pushing a new branch into a non-bare repo is fine (only the checked-out `main` is refused). "Unchanged ⇒ skipped" is exactly the nop-grades-on-base behaviour §3 wants. Grill decision 3 moved verification host-side: no seam, no docker at grade time, so the runner's unit tests need no daemon at all. The `Settings`-to-child-env export is deliberate and is NOT the ADR-0016 anti-pattern (that forbids pouring config into the sandbox Worker; this is the harness's own process).

### [SWE] 2026-09-11 06:35 — Implementation

**Files modified**
- `evals/harness/trial.py` — NEW: `run_trial` (seed → `decode run` subprocess → collect → host-side pristine verify → Trial Dir, `result.json` in a `finally`), `TrialResult`/`TrialStatus`, the pure `child_env()`, `agent_info()` / `git_sha()` / `decode_version()` provenance.
- `evals/harness/verifier.py` — NEW: `grade_checkout()` + `VerifierResult` — the ONE grade-time step (overlay `tests/` last, empty `$VERIFIER_DIR`, timeout, parse `reward.txt`) extracted from `oracle_sanity.run_verifier` so the gate and the trial share it verbatim.
- `evals/harness/oracle_sanity.py` — now seeds, optionally applies the Oracle, then delegates to `grade_checkout`; re-exports `VerifierResult` / the two name constants so every existing import site is unchanged.
- `evals/harness/benchmark.py` — rewired to `run_trial` (kills both the `sandbox.py` and `driver.py` benchmark-side imports); `new_job_dir()` (`<harness home>/.decode/evals/runs/<utc-stamp>/`), `trial_payload()`; `git_sha` now imported from `trial.py` and re-exported (`regression.py` reads it from here). Opik surface (dataset v2, RewardMetric, `experiment_scoring_functions`, parallelism) deliberately untouched — 161.
- `evals/harness/__init__.py`, `evals/run.py` — exports + the `benchmark` command docstring describe a Trial, not the dead in-sandbox lifecycle.
- `src/decode/__main__.py` — NEW (3 lines): `init_logger()` then `cli()`, so a trial runs the harness's own venv via `[sys.executable, "-m", "decode"]`.
- `src/decode/sandbox/handback.py` — **out of scope, bug found by this task's e2e** (see Notes): `_has_origin` now requires the Workspace to be the git toplevel.
- `tests/support/fake_decode.py` — NEW: the fake `decode run` (7 modes: oracle / nop / capped / lying-branch / no-summary / hang / hang-handback), each printing argv + cwd + pgid + the Seed Repo listing as evidence.
- `tests/unit/evals/harness/test_trial.py` — NEW: 24 tests (taxonomy, isolation, child env, evidence).
- `tests/unit/evals/harness/test_benchmark.py` — the four sandbox-lifecycle task-fn tests replaced by three that mock `run_trial` (the real thing is pinned in `test_trial.py`).
- `tests/unit/evals/harness/conftest.py` — the now-dead `install_fake` fixture removed (`support/fake_sandbox.py` stays: `test_bash_sandbox_selection.py` still uses it).
- `tests/unit/decode/sandbox/test_handback.py` — regression test for the hand-back escape.
- Deleted: `evals/harness/sandbox.py`, `tests/unit/evals/harness/test_sandbox.py`, `tests/integration/test_benchmark_docker.py`.

**Tests**
- Unit: 2648 passing, 0 failing (`make unit-tests`); `tests/unit/evals/harness/test_trial.py` = 24.
- Integration: 114 passing (`make integration-tests`) — re-run after the `handback.py` fix.
- Lint/format/pre-commit: clean.

**Acceptance criteria**
- [x] `run_trial(...)` five phases with per-phase timings — `test_trial.py::test_an_oracle_agent_earns_reward_one_and_a_complete_trial_dir`, `::test_result_json_records_the_trial`, `::test_the_run_command_carries_the_trial_flags`, `::test_the_subprocess_runs_in_its_own_process_group`.
- [x] `child_env` overrides/removals + no secret in `result.json` — `::test_child_env_applies_the_trial_overrides_and_removals`, `::test_child_env_exports_the_parents_resolved_settings`, `::test_child_env_round_trips_through_settings`, `::test_child_env_skips_an_unset_or_placeholder_secret`, `::test_no_secret_reaches_the_result_json`.
- [x] `src/decode/__main__.py` — `uv run python -m decode --help` (evidence below) and every trial in the suite runs through a `-m`-shaped command.
- [x] Taxonomy pinned — `::test_a_nop_agent_is_graded_on_the_base_commit`, `::test_a_request_limit_exit_is_an_agent_failure`, `::test_a_hung_agent_is_interrupted_and_still_graded`, `::test_a_timeout_without_a_summary_is_still_an_agent_failure`, `::test_a_run_without_a_summary_is_an_infra_error`, `::test_a_garbage_reward_is_an_infra_error`, `::test_a_reward_outside_the_unit_range_is_an_infra_error`, `::test_a_verifier_timeout_is_an_infra_error`, `::test_a_failing_seed_is_an_infra_error`, `::test_a_missing_branch_clone_is_an_infra_error`, `::test_run_trial_never_raises`.
- [x] The agent never sees `tests/` / `solution/` — `::test_the_agent_never_sees_the_hidden_tests_or_the_oracle` (the fake lists the Seed Repo DURING its run, so it is a run-time assertion, not just a post-hoc one).
- [x] Verifier host-side only — `::test_trial_imports_nothing_from_the_sandbox_or_tool_packages` (walks the module's AST).
- [x] Three files deleted; `driver.py` no longer imported benchmark-side (`regression.py` + `evals/harness/__init__.py` keep it, per ADR-0022 §1).
- [x] `test_trial.py` drives a fake `decode_command` for every listed scenario.

**Evidence**
```
$ make unit-tests
======================= 2648 passed, 1 skipped in 51.41s =======================
$ make integration-tests
======================= 114 passed in 368.91s (0:06:08) ========================
$ uv run python -m decode --help
Usage: python -m decode [OPTIONS] [COMMAND] [ARGS]...
  Decode — a terminal coding agent you run in your terminal.
$ uv run python -m evals benchmark --help      # keyless
Usage: python -m evals benchmark [OPTIONS]
```
Real docker trial, task `001-find-and-replace`, `DECODE_ENV=local LLM_PROVIDER=gemini`, `sandbox="docker"` — re-run AFTER the `handback.py` fix (the earlier `manual01` run predates it, and that guard gates this very success path), `result.json`:
```
{"task_id": "001-find-and-replace", "trial_id": "manual02", "status": "agent_ok", "reason": null,
 "reward": 1.0, "timed_out": false, "base_sha": "dae0b999…",  "branch": "decode/07478dd5",
 "agent": {"git_sha": "3fd0698…", "decode_version": "0.1.0", "model": "gemini-3.5-flash",
           "provider": "gemini", "sandbox": "docker"},
 "summary": {"exit_reason": "completed", "requests": 4, "input_tokens": 45861, "output_tokens": 839,
             "cost_usd": 0.0434943, "handback": {"branch": "decode/07478dd5", "pushed": true}, …},
 "timings": {"seed": 0.043, "run": 20.1, "verify": 0.06}}
```
Trial Dir complete (`seed/ home/ pristine/ agent/{stdout,stderr,summary.json} verifier/{test-stdout,test-stderr,reward.txt} result.json`); `verifier/test-stdout.txt` = "config.ini matches the expected file", `reward.txt` = 1; `git -C seed branch` = `decode/07478dd5` + `main` (so the tightened `_has_origin` still says YES for a Workspace that IS a repo, and the Hand-back still pushed); `ls -a seed` = `.git config.ini` (no `tests/`, no `solution/`); this repo's `git log`/`git branch decode/*` untouched; no docker containers left behind. Both trials kept under `.decode/evals/runs/manual-160/` (git-ignored) for the Tester.

**Notes**
- **Spec ambiguity resolved deliberately: a timeout with NO summary is `agent_fail`, not `infra_error`.** AC4 says both "no `summary.json` ⇒ infra_error" and "a timeout is never an Infra Error"; ADR-0022 §4's timeout design (SIGINT → decode's `finally` hands back → "the partial branch is graded like any other") assumes a summary exists. Counting a wedged agent as infra would drop the worst agent behaviour out of the denominator, which is exactly the bias DeepSWE's rule prevents. Pinned by `::test_a_timeout_without_a_summary_is_still_an_agent_failure`; break-path checked by mutating `if summary is None and not timed_out:` → `if summary is None:`, which turns that test red with `status == "infra_error"`.
- **Two bugs the unit tests could not see — both caught by the step-7 e2e** (`tmp_path` is absolute and lives outside any git repo, which is precisely what hid them):
  1. *Relative `job_dir`* — the default job dir is `.decode/evals/runs/<job>` and the child runs from `home/`, so `--repo` resolved against the wrong cwd and every trial died with "repository does not exist" (an Infra Error on 100% of trials). Fixed by resolving the Trial Dir up front; regression test `::test_a_relative_job_dir_is_resolved_before_the_subprocess_starts` (red with the exact production message before the fix).
  2. *Hand-back escape (`src/decode/sandbox/handback.py`, OUT OF SCOPE for this task)* — `git -C <workspace>` walks UP to the nearest enclosing repository, so when the Workspace clone fails the skip guard `_has_origin` answered about the **enclosing checkout**. The hand-back then `git add -A`-ed, committed and PUSHED the user's own working tree as a Session Branch. **This actually happened during my e2e:** commit `6b9baf3` ("decode session a8bb9227-…") was created on `feat/evals-v2` with my whole WIP and pushed as `origin/decode/a8bb9227`. I reset the commit (`git reset --soft HEAD~1` — working tree restored byte-for-byte), deleted the local and the remote branch; `git ls-remote --heads origin 'refs/heads/decode/*'` is empty and the branch history is back at `3fd0698`. The reflog still shows `6b9baf3`. Fixed at the root (`_has_origin` now requires `rev-parse --show-toplevel` == the Workspace, the single choke point every later git call sits behind — verified no other module shells out to git against `workspace_dir()`); regression test `tests/unit/decode/sandbox/test_handback.py::test_a_non_git_workspace_inside_a_repo_never_ships_the_enclosing_checkout` (red before the fix: it committed AND pushed the enclosing repo). Re-verified live: `decode run --repo /does/not/exist --local` with `SANDBOX_MODE=docker` from a dir inside this repo now leaves `git log`/`git status` untouched and creates no `decode/*` branch. Fixed here rather than escalated because ADR-0012 §8 already specifies the "non-git ⇒ skip" behaviour — the guard simply implemented it wrong — and the role rules require a regression test + root-cause fix for a bug this task's change exposes.
- **`benchmark.py` is wired to `run_trial`, not stubbed** — its old dependencies (`sandbox.py`, `driver.py`) are gone, so a stub would have been dead weight. `run_benchmark`'s Opik surface (dataset, metrics, `experiment_config`, `task_threads=1`) is untouched for 161; the task-fn payload now carries `reward` / `status` / `reason` / `timed_out` / `trial_dir` and keeps `steps` + `max_steps` so `MaxStepsMetric` still reads something. `verify: {exit_code, stdout}` is dropped (nothing consumed it; the reward metric lands in 161).
- **Deliberate trade-offs.** (a) A timed-out run with no summary is graded on the BASE commit — the trial does not go looking for a `decode/*` branch the summary never named; if that proves too pessimistic, the upgrade is one `git -C seed branch --list 'decode/*'` probe. (b) `child_env` exports `model_fields_set` only — fields nobody set re-derive identically in the child. (c) Container cleanup after a SIGKILL stays out of scope (per the task); SIGINT-first makes it rare.
- **Known interaction for 161 (no code change here):** `STRIPPED_ENV_VARS` removes the three the AC names; `SANDBOX_WORKSPACE_DIR` is NOT one of them, so an operator who set it to an absolute path in `.env` would have every trial's Workspace land outside its Trial Dir — and concurrent trials would share one directory. Whoever turns on `task_threads > 1` (161) should decide that one.
- **CI:** `gh run list` shows no workflow run fired for the deleted `decode/a8bb9227` ref (CI triggers on `main` / PRs), so there is no orphaned run for the On-Call Engineer to chase.
- **Not run:** modal e2e (manual, runbook — out of scope).

### [Tester] 2026-09-11 07:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 331 files; `ruff check` all clean; `make pre-commit` 2648 passed, 1 skipped — pre-existing `fastapi` skip, unrelated)
- Unit tests: 2648 passed, 1 skipped, 0 failed
- Integration tests: first run 113 passed / 1 failed (`test_subagents_capstone.py::test_live_gemini_fanout_smoke` — live-Gemini nondeterminism on synthesis-footer wording; file untouched by this diff; re-run in isolation passed). `make ci` re-ran the full suite end to end and it passed clean: **2762 passed, 1 skipped**, 0 failures, 0 warnings (`filterwarnings=["error"]` is enforced by pytest config, so a warning would already be a failure).
- `make ci` (`uv lock --check` + format-check + lint-check + `make test`): PASS.
- `code-review` plugin: evaluated, not invoked — its `allowed-tools` are exclusively `gh pr`/`gh issue` commands (`.claude/plugins/marketplaces/claude-plugins-official/plugins/code-review/commands/code-review.md`); there is no PR yet at Tester stage (PR Reviewer runs this in `/squid-review` after commit + push). Not applicable here, not skipped.

**Handback regression fix (out of scope, security-critical) — verified with extra care**
- Read `src/decode/sandbox/handback.py::_has_origin` (lines 197-213): now runs `git rev-parse --show-toplevel` first and refuses (`return False`) unless the toplevel equals the Workspace itself, BEFORE `remote get-url` is even checked. This guard sits upstream of every destructive step in `ship_workspace` (`_exclude_decode_namespace`, `_is_unchanged`, `_secure_session_branch` [`add -A` + commit], `_push`) — confirmed by re-reading `ship_workspace` line by line: `_has_origin` is checked first and returns immediately on failure, so nothing downstream ever touches a non-repo Workspace.
- Reproduced the original incident directly: `git stash push -- src/decode/sandbox/handback.py` (reverting only the fix) then ran `tests/unit/decode/sandbox/test_handback.py::test_a_non_git_workspace_inside_a_repo_never_ships_the_enclosing_checkout` — **RED**, with `result.branch == 'decode/escape-7'` and `pushed=True`, i.e. the test failure output literally shows the bug committing+pushing the enclosing repo. Restored the fix (`git stash pop`) — same test **GREEN**, plus the other 17 `test_handback.py` tests (including the real-clone push path) all still pass, so a genuine Workspace clone still hands back correctly.
- Searched every `"git"` subprocess call site under `src/decode/` (`grep -rln '"git"' src/decode`): `observability/metadata.py` (read-only `git rev-parse HEAD` for provenance, not a destructive path), `sandbox/workspace.py` (`git clone` into a fresh target, not `add -A`/commit/push against an existing dir), `remote/headless.py` (`git clone` / `git config --global`), and `sandbox/handback.py` itself. `handback.py` is the ONLY module that runs `add -A` + commit + push against `workspace_dir()` — confirming the SWE's claim that `_has_origin` is the single choke point every destructive git call sits behind, and that the fix is complete, not partial.
- Verdict: minimal, correct fix per ADR-0012 §8 ("non-git ⇒ skip") — it makes the skip guard actually detect "not a git repo *at this path*" instead of "not a git repo *anywhere up the tree*", with no behavior change for the legitimate clone case.

**Taxonomy decision (timeout + no summary ⇒ `agent_fail`) — verified**
- Read `trial.py` lines 194-198: `if summary is None and not timed_out: raise _InfraError(...)` — a timeout with no summary skips the `_InfraError` branch entirely, falls through to `_verify` (grades the base commit, since `branch=None`), then `_classify` scores it `agent_fail` (reward 0.0 on the synthetic fixture) rather than `infra_error`.
- Pinned by `test_a_timeout_without_a_summary_is_still_an_agent_failure` (hang mode, no summary written): asserts `timed_out=True`, `status="agent_fail"`, `reward=0.0`, `summary is None`, `"timed out" in reason`. Confirmed green in both the unit run and `make ci`.
- The task's Notes section records the resolution explicitly (spec ambiguity between AC4's "no summary.json ⇒ infra_error" bullet and "a timeout is never an Infra Error" bullet) with the rationale (ADR-0022 §4 / DeepSWE bias argument) and a documented break-path check (mutating the guard turns the pinning test red). This matches "the orchestrator ACCEPTS this" framing in my task brief. One nit for the log (non-blocking): AC4's own bullet text still reads ambiguously ("...exited with NO `summary.json`" vs "a timeout is never an Infra Error") — a future reader would benefit from tightening it to "...NO `summary.json` **and did not time out**", but the behavior, its test, and the Notes explanation are all correct and consistent.

**E2E adversarial pass**
- Happy path: ran `run_trial` against the real `oracle` fake-decode mode via `test_an_oracle_agent_earns_reward_one_and_a_complete_trial_dir` → `(status, reward, timed_out) == ("agent_ok", 1.0, False)`, complete Trial Dir (PASS). Also ran a REAL trial: `uv run --project <repo> python <script calling run_trial>` from a scratch cwd **outside** the repo, task `002-regex-extraction`, `sandbox="docker"`, `DECODE_ENV=local LLM_PROVIDER=gemini`, no inline model override → `STATUS agent_ok REWARD 1.0 REASON None`, Trial Dir complete, `result.json` well-formed, `seed/` branch `decode/ca7e9561` only inside the disposable seed clone. Afterward confirmed: this repo's `git log` unchanged (still `3fd0698`), no `decode/*` local branches, `git ls-remote --heads origin 'decode/*'` empty, no stray `runs/` dir left in the repo, no leftover decode-related docker containers (PASS).
- Break path 1 (SIGKILL escalation / process-group orphan — state edges + failure mode): wrote a fake `decode run` that installs `SIG_IGN` for SIGINT and spawns a grandchild `sleep 600` in the SAME process group (no `start_new_session`), monkeypatched `trial_module.SIGINT_GRACE_S=2.0` / `SIGKILL_REAP_S=3.0` to avoid a real 60s wait, then ran `run_trial` with `agent.timeout_sec=1.0`. Observed: `[eval] ... ignored the interrupt for 2s; killing it` logged, `elapsed≈3.1s`, `status=agent_fail`, `timed_out=True`; one second after `run_trial` returned, `os.kill(child_pid, 0)` raised `ProcessLookupError` — the grandchild was reaped, no orphan. Expected: SIGKILL reaches the whole group, no leaked process. PASS. (Note for the SWE: this exact scenario — a `decode` that ignores SIGINT — has **no automated regression test**; the existing `hang` fixture relies on Python's default `SIGINT`→`KeyboardInterrupt` behavior, so it never actually exercises the grace-period-expiry→SIGKILL branch. Recommend a `hang-ignore-sigint` fake-decode mode + a test with monkeypatched grace constants for 161 or a follow-up, so this path isn't only manually verified.)
- Break path 2 (`lying-branch` — malformed/hostile summary): read `_handback_branch` (accepts any string from the summary verbatim) → `_clone_pristine` runs `git clone --branch decode/never-pushed <seed> <pristine>`, which fails because the branch doesn't exist in the Seed Repo → raises `_InfraError("the pristine clone of decode/never-pushed failed...")`. Pinned by `test_a_missing_branch_clone_is_an_infra_error`: `status == "infra_error"`, `"clone" in reason`. Judgment: correct per AC4, which explicitly lists "clone failure" as an `infra_error` category — a summary naming a nonexistent branch is indistinguishable from a broken clone at grade time, and there's no way to attribute it to the agent vs. a Hand-back bug, so excluding it from the denominator (rather than agent_fail) is the right call. PASS.
- Break path 3 (malformed reward — boundary/hostile input): manually ran a fresh trial (oracle fake-decode, custom `tests/test.sh` writing `printf "1.5\n" > "$VERIFIER_DIR/reward.txt"`) → `status=infra_error`, `reward=None`, `reason="the Verifier wrote a reward outside [0, 1]: 1.5"`. Matches AC4 exactly ("reward.txt ... out of range" ⇒ `infra_error`). PASS. (The existing `test_a_reward_outside_the_unit_range_is_an_infra_error` covers the same code path with `7`; my run empirically confirms the fractional-overflow case too.)
- Break path 4 (child_env secret hygiene — hostile/security): read `_render()` — the `SecretStr` branch is checked before the generic `str(value)` fallback, so a `SecretStr` can never render as its `repr()`; confirmed `test_child_env_skips_an_unset_or_placeholder_secret` (empty/placeholder secret exported as nothing, never `KEY=`) and `test_no_secret_reaches_the_result_json` (a live sentinel key/token, `result.json` text does not contain it) both pass. Read `child_env`'s full removal set against `STRIPPED_ENV_VARS` and confirmed `test_child_env_applies_the_trial_overrides_and_removals`'s body (not just its setup) asserts all three removals (`KITARU_TASK_ID`, `KITARU_REPLAY_ID`, `SANDBOX_REPO`) AND `KITARU_AGENT_ID` passthrough in one test. PASS.

**Acceptance criteria**
- [x] PASS — `run_trial(...)` five phases, per-phase timings, Trial Dir layout — `test_an_oracle_agent_earns_reward_one_and_a_complete_trial_dir`, `test_result_json_records_the_trial`, `test_the_run_command_carries_the_trial_flags`, `test_the_subprocess_runs_in_its_own_process_group`, all green; manual real-docker trial confirms the same shape end to end.
- [x] PASS — `child_env` overrides/removals, no secret leak — `test_child_env_applies_the_trial_overrides_and_removals`, `test_child_env_exports_the_parents_resolved_settings`, `test_child_env_round_trips_through_settings`, `test_child_env_skips_an_unset_or_placeholder_secret`, `test_no_secret_reaches_the_result_json`, all read and green; `_render()` code path confirms `SecretStr` never reprs.
- [x] PASS — `src/decode/__main__.py` — `uv run python -m decode --help` and `uv run python -m evals benchmark --help` both keyless (ran both myself, env with no `GEMINI_API_KEY`/`OPENROUTER_API_KEY`/`SANDBOX_GIT_TOKEN`).
- [x] PASS — taxonomy pinned, incl. the timeout+no-summary=agent_fail resolution — all 11 taxonomy tests read and re-run green; empirically re-verified the reward-out-of-range and lying-branch cases myself.
- [x] PASS — agent never sees `tests/`/`solution/` — `test_the_agent_never_sees_the_hidden_tests_or_the_oracle` green; confirmed by my own real trial's `ls -a seed` = `.git config.ini` / `contacts.txt` etc, no `tests`/`solution`.
- [x] PASS — Verifier host-side only — `test_trial_imports_nothing_from_the_sandbox_or_tool_packages` (AST-walks `trial.py`'s own import statements) green; confirmed `_run_agent` drives the sandbox via a `decode run` subprocess (`subprocess.Popen`), never via `decode.tools`/`decode.sandbox` directly. (Note: `import evals.harness.trial` DOES transitively pull in `decode.tools.*` via `evals/harness/__init__.py`'s pre-existing `driver` re-export — this is Python package-init mechanics, not an import BY `trial.py`, and does not contradict the AC's "no sandbox seam at grade time" intent, since `trial.py`'s own execution path never calls into it.)
- [x] PASS — `sandbox.py` / `test_sandbox.py` / `test_benchmark_docker.py` deleted, `driver.py` no longer imported benchmark-side — confirmed all three files absent, no dangling references (`grep -rn "evals.harness.sandbox"` finds only a comment in `conftest.py`), `driver.py` still imported by `regression.py` + `evals/harness/__init__.py` only.
- [x] PASS — `test_trial.py` fake-`decode_command` scenarios (oracle/nop/no-summary/hang/verifier-garbage) all present and green, plus 3 more (capped, lying-branch, hang-handback) beyond the AC's minimum list.

**Evidence**
```
$ make ci
... (uv lock --check, format-check, lint-check, then full test suite) ...
================= 2762 passed, 1 skipped in 446.39s (0:07:26) ==================

$ git stash push -- src/decode/sandbox/handback.py && uv run pytest tests/unit/decode/sandbox/test_handback.py::test_a_non_git_workspace_inside_a_repo_never_ships_the_enclosing_checkout -q
FAILED ... AssertionError: assert 'decode/escape-7' is None
(fix restored via git stash pop; same test green after)

$ uv run pytest tests/unit/decode/sandbox/test_handback.py -q
18 passed in 3.56s

manual SIGKILL/orphan test (fake decode ignores SIGINT, spawns a same-pgid grandchild):
elapsed 3.13   status agent_fail   timed_out True
child pid 51872   child reaped: PASS (no orphan)

manual live docker trial, 002-regex-extraction, from a scratch cwd outside the repo:
STATUS agent_ok REWARD 1.0 REASON None
post-trial: git log unchanged (3fd0698), no decode/* local branches,
`git ls-remote --heads origin 'decode/*'` empty, no stray runs/ dir, no stray docker containers

manual reward=1.5 test:
status infra_error reward None reason the Verifier wrote a reward outside [0, 1]: 1.5
```

**Other issues found**
- No automated test exercises the SIGKILL-escalation branch of `_wait_with_timeout` (a `decode` that ignores SIGINT) — only manually verified in this review. Recommend a `fake_decode.py` mode for it in a follow-up (161 or a fast-follow), since it's the one branch of the timeout state machine with zero regression coverage.
- `git_sha()` in `trial.py` runs `git rev-parse HEAD` in the *parent process's* cwd (not the repo root), so a benchmark launched from outside a git checkout silently records `"unknown"` provenance (observed exactly this in my own manual scratch-cwd trial). Documented as "never crash a trial on it" and not hit by the default `new_job_dir()`/CLI invocation path (which runs from the repo), so not a blocker — flagging for awareness ahead of 161's Opik provenance work.
- AC4's own bullet text is still literally self-contradictory ("NO summary.json ⇒ infra_error" vs "a timeout is never an Infra Error"); the Notes section resolves it correctly and the behavior/tests are right, but a future reader of the AC alone could re-litigate this. Non-blocking wording nit.

**VERDICT: PASS**

### [SWE] 2026-09-11 07:40 — Fixes (Tester's two non-blocking notes)

**Files modified**
- `tests/support/fake_decode.py` — new `hang-ignore-sigint` mode: ignores SIGINT and spawns a grandchild in the SAME process group that ignores it too, then prints a second json line naming that pid.
- `tests/unit/evals/harness/test_trial.py` — `::test_an_agent_that_ignores_the_interrupt_is_killed_with_its_whole_group` (grace monkeypatched to `trial_module.SIGINT_GRACE_S = 1.0`): asserts `timed_out`, `agent_fail`, `result.json` written, and that the grandchild is gone (`_reaped()` polls `os.kill(pid, 0)`). 2.1 s wall clock. `_agent_stdout(..., line=)` now takes a line index.
- This task file — AC4's `infra_error` bullet now reads "NO `summary.json` **and did not time out**", so it no longer contradicts "a timeout is never an Infra Error".

**Tests**
- Unit: 2649 passing, 0 failing (`make pre-commit`). The new test is the only behavioural addition; no production code changed.
- Break-path check: mutating `_signal_group(process, signal.SIGKILL)` → `pass` turns it RED (the grandchild outlives the run) — the escalation branch the Tester could only verify by hand is now pinned.

**Notes**
- The Tester's other two observations (`git_sha()` reads the parent's cwd; `SANDBOX_WORKSPACE_DIR` not stripped for parallel trials) are untouched here — both are 161 concerns, already recorded above.
