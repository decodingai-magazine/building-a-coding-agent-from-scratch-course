---
id: 160-benchmark-trial-runner
feature: evals-v2
status: pending
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
- [ ] `run_trial(task, *, sandbox: Literal["docker","modal"], job_dir: Path, trial_id: str, model: str | None = None, decode_command: Sequence[str] = (sys.executable, "-m", "decode")) -> TrialResult` performs, with per-phase timings:
      1. **seed** — `seed_task_repo(task, <trial>/seed)` (157); records `base_sha`.
      2. **run** — `Popen([*decode_command, "run", task.instruction, "--repo", <seed>, "--local", "--max-requests", str(task.max_steps), "--summary-json", <trial>/agent/summary.json, *(["--model", model] if model)], cwd=<trial>/home, start_new_session=True)`; stdout/stderr to files; wall clock `task.agent_timeout_sec` → SIGINT to the process group (so decode's `finally` reaps the sandbox and hands back), 60 s grace, then SIGKILL; `timed_out=True` recorded.
      3. **collect** — read `summary.json`; branch = `summary["handback"]["branch"]` or `None` (skipped ⇒ base).
      4. **verify** — `git clone [--branch <branch>] <seed> <trial>/pristine`; overlay `tests/` → `pristine/tests/`; mkdir `pristine/.verifier`; `bash tests/test.sh` with cwd=pristine, `VERIFIER_DIR` absolute, timeout `task.verifier_timeout_sec`; parse `reward.txt`; copy verifier outputs to `verifier/`.
      5. **record** — `result.json` `{task_id, trial_id, status, reason, reward, timed_out, base_sha, branch, agent: {git_sha, decode_version, model, provider, sandbox}, summary: <agent/summary.json or null>, timings: {seed, run, verify}}`, written in a `finally` (an Infra Error still leaves evidence).
- [ ] Child env = the parent's `os.environ` + every set `Settings` field exported under its env-var name (secrets unwrapped) so a key that lives only in the repo `.env` reaches a subprocess launched from a cwd without one; overrides `SANDBOX_MODE=<sandbox>`, `OPIK_PROJECT_NAME=<settings.eval_project_name>` (live tracing is never polluted), `RUNTIME_ENABLED=true`; removes `KITARU_TASK_ID`, `SANDBOX_REPO`, `KITARU_REPLAY_ID`. `KITARU_AGENT_ID` passes through (recording rides the Recording Seam). One pure `child_env(...)` helper, unit-tested for the overrides/removals and that no `Settings` value leaks into `result.json`.
- [ ] `src/decode/__main__.py` added (3 lines: `init_logger()` then `cli()`), so the trial runs the harness's own interpreter/venv.
- [ ] Taxonomy pinned by tests: `status ∈ agent_ok | agent_fail | infra_error`. `agent_ok` ⇔ reward == 1. `agent_fail` = summary present and reward < 1 — including `exit_reason ∈ request_limit|error`, a timeout, a skipped Hand-back (base graded). `infra_error` = seed failure, subprocess exited with NO `summary.json`, clone failure, verifier timeout/crash, `reward.txt` missing/empty/non-numeric/out of range — each with a one-line `reason`. A timeout is never an Infra Error.
- [ ] The agent never sees `tests/` or `solution/`: a test launches the fake `decode` and asserts neither path exists under `<seed>` nor `<trial>/home`; the pristine overlay happens only after the subprocess exits.
- [ ] Verifier is host-side only: `trial.py` imports nothing from `decode.sandbox` / `decode.tools`; a test asserts the module's imports.
- [ ] `evals/harness/sandbox.py` and `tests/unit/evals/harness/test_sandbox.py` deleted; `tests/integration/test_benchmark_docker.py` deleted (its subject is gone); `driver.py` no longer imported by anything benchmark-side.
- [ ] `tests/unit/evals/harness/test_trial.py` uses a fake `decode_command` (a Python script) covering: oracle-as-agent (clones seed, runs `solve.sh`, commits, pushes `decode/<short>`, writes summary) → `agent_ok`, reward 1, complete Trial Dir; nop (summary with `handback: null`) → `agent_fail`, base graded; no summary → `infra_error`; hang → SIGINT path, `timed_out`; verifier writing garbage → `infra_error`.

## Out of scope
- Opik and parallelism policy (161). Modal e2e (manual, runbook). Container cleanup after a SIGKILL (documented in the runbook; SIGINT-first makes it rare).

## Log
### [PA] 2026-09-10 — Grooming
`decode run --repo <local path> --local` clones host-side into `<home>/.decode/sandbox`, uploads it to Modal or bind-mounts it for docker, and Hand-back pushes host-side to `origin` = the seed path (ADR-0012 §8) — pushing a new branch into a non-bare repo is fine (only the checked-out `main` is refused). "Unchanged ⇒ skipped" is exactly the nop-grades-on-base behaviour §3 wants. Grill decision 3 moved verification host-side: no seam, no docker at grade time, so the runner's unit tests need no daemon at all. The `Settings`-to-child-env export is deliberate and is NOT the ADR-0016 anti-pattern (that forbids pouring config into the sandbox Worker; this is the harness's own process).
