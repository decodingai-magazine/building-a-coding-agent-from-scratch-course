---
id: 169-pr-review-rollup-evals-v2
feature: evals-v2
status: done
---

# [PR review rollup] Evals v2 — own Harbor on Opik primitives, Kitaru orthogonal

Tags: `rollup`, `pr-review`
Refs: PR #68 (branch: `feat/evals-v2`, head `f389cd5`)
Depends on: None
Blocks: hand-off of PR #68

## Scope

PR Reviewer found **3 Blocker(s)** and **9 Nit(s)** in the diff (373 files, ~+35.9k/-6.6k). The SWE
must fix every Blocker (and may fix Nits at their discretion) in a single coordinated pass, then hand
back to the Tester. Pipeline re-runs from QA → PA acceptance → push → re-review.

All three Blockers are small and local: one env scrub at the host-side grade step (a security
default), and two duplicated helpers left behind by mid-feature refactors. Nothing in the trial
taxonomy, the `finally` ordering, the process-group kill, the hand-back guard, the importer's
self-containment, the bootstrap idempotency, the task verifiers or the trace fixtures needs to change.

## Acceptance Criteria

- [x] Blocker 1: `evals/harness/verifier.py::grade_checkout` runs `bash tests/test.sh` with an
      allow-listed environment (no inherited `os.environ`); a regression test proves a verifier that
      echoes `$GEMINI_API_KEY` / `$OPIK_API_KEY` / `$SANDBOX_GIT_TOKEN` sees nothing when the parent
      process has a sentinel set. The oracle gate (`make ci`) and every edge-case suite still pass.
- [x] Blocker 2: one `git rev-parse HEAD` helper in the repo — `evals/harness/trial.py::git_sha`
      delegates to (or is replaced by) `decode.observability.git_sha`; `benchmark.py` /
      `regression.py` provenance unchanged in value.
- [x] Blocker 3: `tests/unit/evals/benchmark/conftest.py::grade_workspace` calls
      `evals.harness.verifier.grade_checkout` instead of re-implementing it; `_read_reward` deleted;
      the edge-case suites (`test_verifier_edge_cases_{easy,medium,hard}.py`) still pass.
- [x] Tester re-runs full QA suite and PASSES (including the new regression test for Blocker 1).
- [ ] PA re-runs acceptance review and ACCEPTS.
- [ ] PR Reviewer re-runs and reports `NO BLOCKERS`.

## Blockers (detail)

### 1. [Standards — security default] — `evals/harness/verifier.py:67-75`
- **What's wrong:** `grade_checkout` runs the Verifier with `env={**os.environ, "VERIFIER_DIR": …}`.
  Most Verifiers **execute the agent's own code host-side**: 007/009/014/016/017/019/020 run
  `python3 -m unittest` importing the agent-edited modules, 006 runs the agent-written `ban_ips.py`,
  015 `importlib.import_module("service")`. The benchmark process imports `opik` → `litellm`, whose
  `load_dotenv()` copies the repo `.env` into `os.environ` (documented in `tests/conftest.py`), so a
  model-authored file runs unsandboxed with `GEMINI_API_KEY`, `OPIK_API_KEY`, `OPENROUTER_API_KEY`,
  `SANDBOX_GIT_TOKEN`, Modal keys, etc. in its environment. ADR-0022 §3 says "the agent never sees
  the grader"; it does not say the grader executes the agent's code with the operator's secrets.
- **Why it's a Blocker:** a missing security default the codebase otherwise enforces — decode's whole
  sandbox story (ADR-0011/0012/0016) is that model-produced work never sees the operator's config;
  the host-side grade step is the one place it does, one `os.system` away from exfiltration.
- **Suggested fix:** build the Verifier env from an allow-list rather than inheriting:
  `{"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"}` (whichever are set) plus `VERIFIER_DIR`. This also
  serves the ADR's own reproducibility claim ("a reward is reproducible from the Trial Dir with a bare
  bash"). Apply the same env to the oracle step in `oracle_sanity.py::_run_oracle` and to
  `seed.py::_run_setup_script` for consistency (both are repo-authored, so those two are hygiene, not
  security). Add one implementation-note sentence to ADR-0022 §3 naming the trust boundary (the
  Verifier runs agent-written code on the host; it gets no operator env).
- **Regression test:** in `tests/unit/evals/harness/test_trial.py` (or a new
  `tests/unit/evals/harness/test_verifier.py`, which would also close the `tests/` ↔ `src/` mirror
  gap noted in Nit 9): patch `os.environ` with `GEMINI_API_KEY=sk-sentinel…`, run a synthetic task
  whose `tests/test.sh` does `printf '%s' "${GEMINI_API_KEY:-}" > "$VERIFIER_DIR/leak.txt"`, assert
  the file is empty. The existing `test_no_secret_reaches_the_result_json` covers `result.json` only.

### 2. [Clean code — duplicated block] — `evals/harness/trial.py:292-303` vs `src/decode/observability/metadata.py:36-59`
- **What's wrong:** two near-identical `git rev-parse HEAD` → sha-or-`"unknown"` helpers. `trial.py`'s
  is the v1 `benchmark.py::git_sha` moved; `metadata.py::git_sha(cwd)` is new in task 156 (cached,
  timeout-bounded, cwd-explicit) and is already public via `decode.observability.__all__`.
  `benchmark.py:49-51` re-exports the `trial.py` copy for `regression.py`.
- **Why it's a Blocker:** duplicated logic where a single helper is the obvious fix — the harness
  already imports `decode.config.settings`, so importing `decode.observability.git_sha` costs nothing.
- **Suggested fix:** delete `trial.git_sha`; in `trial.agent_info` / `benchmark.experiment_config` /
  `regression.experiment_config` call `decode.observability.git_sha(str(Path.cwd()))` (or keep a
  one-line `git_sha()` wrapper in `trial.py` that delegates, if the no-arg spelling is preferred).
  Drop the `trial.py` `subprocess` import if it becomes unused (it does not — `_run_agent` uses it).
- **Regression test:** none new; `test_trial.py::test_result_json_records_the_trial` and
  `test_benchmark.py::test_run_benchmark_experiment_config_carries_the_full_provenance` already pin
  the field.

### 3. [Clean code — duplicated block] — `tests/unit/evals/benchmark/conftest.py:94-127`
- **What's wrong:** `grade_workspace` re-implements `evals/harness/verifier.py::grade_checkout` step
  by step (overlay `tests/` last, empty `.verifier`, `bash tests/test.sh` with `VERIFIER_DIR`, parse
  `reward.txt`) and `_read_reward` duplicates `verifier.read_reward`. The fixture was written in task
  158 before task 160 extracted `grade_checkout` as "the ONE grade-time step both graders drive" and
  was never folded back. It also hard-codes `timed_out=False` and would let a `TimeoutExpired`
  escape the fixture instead of reporting it.
- **Why it's a Blocker:** the same grade-time logic now lives in two places; the edge-case suites
  (~1000 lines) claim to reproduce grade time "exactly as `run_verifier` defines it" while running a
  hand copy of it — a drift here would silently grade the real tasks and the tests differently.
- **Suggested fix:** after dropping `files` / running `post_setup`, `return grade_checkout(task,
  workspace)`; delete `_read_reward` and the `os` / `shutil` imports that become unused. Once Blocker
  1 lands, the env scrub is inherited for free.
- **Regression test:** none new; the three `test_verifier_edge_cases_*.py` suites are the coverage.

## Nits (non-blocking; will be appended to PR description if pipeline advances)

### 1. [Simplicity] — `src/decode/runtime/recording.py:182-197`
- **Suggestion:** `delete:` `recorded_session_id()` reads `kitaru_session_id` / `session_id`
  attributes no adapter publishes (its own docstring: "today it does not"), so it always returns
  `None`, the summary's `kitaru_session_id` is always `null`, and a test pins that it is `null`.
  Speculative accessor → drop it and write the summary key as a literal `None` until adapter
  exposes an id (ADR-0022 §10 already says the join never depends on it).

### 2. [Simplicity] — `src/decode/runtime/headless.py:221,229`
- **Suggestion:** `shrink:` `_run_task(state: _RunState | None = None)` plus the
  `state = state if state is not None else _RunState()` fallback has exactly one caller, which
  always passes a state → make it `state: _RunState` (required) and delete the fallback line.

### 3. [Standards — secrets] — `evals/harness/trial.py:82`
- **Suggestion:** add `SANDBOX_GIT_TOKEN` to `STRIPPED_ENV_VARS`. `_settings_env` exports it when the
  operator's `.env` sets it, so every benchmark sandbox receives the operator's GitHub PAT as
  `GITHUB_TOKEN` while the Seed Repo is a local path (nothing to authenticate against). Nit rather
  than Blocker because it is ADR-0016's documented opt-in and identical to any user's `decode run`.

### 4. [Clean code] — `importers/opik_importer.py:225-228`
- **Suggestion:** `_build_node` still unwraps only the old `tool_arguments` / `tool_response`
  spelling, while the new `tool_span_arguments` / `tool_span_result` readers a few lines above handle
  both spellings. A current-instrumentation span imports its tool node with
  `inputs={"gen_ai.tool.call.arguments": …}` still wrapped. Use the readers (fall back to the raw
  dict when neither key is present).

### 5. [Simplicity] — `scripts/bootstrap_kitaru.py:196,401`
- **Suggestion:** `version_argv[5:]` is a magic slice tied to the shape of `register_argv`'s prefix;
  build the shared tail once and prepend `["kitaru","agent","register",agent]` /
  `["kitaru","agent","version","register",agent]` explicitly. `Runner = Any` →
  `Callable[[list[str]], dict[str, Any]]`.

### 6. [Simplicity] — `evals/harness/benchmark.py:445-542`
- **Suggestion:** `shrink:` seven experiment-scoring functions each call `_total(test_results)`,
  which re-groups and re-summarises the same run seven times. `evaluate` accepts a function returning
  a `list[ScoreResult]` — one `experiment_scores(test_results)` computing `_total` once and returning
  the seven scores is shorter and drops `EXPERIMENT_SCORING_FUNCTIONS`. Cold path, so optional.

### 7. [PA] [Documentation discipline] — `docs/glossary.md`
- **Suggestion:** `Signature` is a domain noun in code (`evals/harness/mine.py::Signature`) and in the
  mining NOTES table, but only appears lowercase inside the Trace Mining row. Either give it a row
  (`(preset, error, last tool, model)` — what makes two bad runs the same bug) or spell the
  four-tuple out in the Trace Mining row.

### 8. [Untested — edge case] — `evals/harness/verifier.py`
- **Suggestion:** `verifier.py` is exercised only indirectly (oracle gate, `test_trial.py`, the
  edge-case suites); there is no `tests/unit/evals/harness/test_verifier.py` although `tests/`
  mirrors source 1:1 by convention. Blocker 1's regression test is a natural first occupant.

### 9. [Simplicity] — `evals/harness/trial.py:402-405`
- **Suggestion:** after `start_new_session=True` the child's pgid **is** `process.pid`;
  `os.killpg(process.pid, sig)` reads clearer than `os.killpg(os.getpgid(process.pid), sig)` and
  cannot race a reaped pid on the `getpgid` call.

## Out of scope

- The RED regression gate (`max_steps` 0.684 / `g_eval_metric` 0.571) — diagnosed and groomed as
  task 168; not a PR-review finding.
- The four `[HUMAN]` acceptance items (161 modal leg, 163 sign-off, 164 pick review, 165 replay leg).

## Log

### [PR Reviewer] 2026-09-11 12:27 — Review of PR #68

**VERDICT: BLOCKERS** — 3 Blockers, 9 Nits. Walked every dimension over the full diff; priority
areas checked: `_has_origin` guard (minimal and correct — `rev-parse --show-toplevel` compared to the
resolved Workspace before any other git call; every other git subprocess in `handback.py` is
downstream of it, `seed.py` / `trial.py` / `metadata.py` never walk up into a user checkout because
they run in trial-owned dirs or are read-only), `headless.py` `finally` ordering (reap → hand-back →
summary, exit codes unchanged, `capture_run_messages` bound before the wrapped run), `trial.py`
(process-group SIGINT → grace → SIGKILL tested against a grandchild; `result.json` and Trial Dir
logs carry no Settings value — pinned by `test_no_secret_reaches_the_result_json`; taxonomy matches
ADR-0022 §4 plus the documented timeout-wins rule), `benchmark.py` (`evaluate()` wiring, job-name
guard, `scoring_failed` on infra errors), importer self-containment (stdlib + `kitaru` only),
bootstrap idempotency (read-then-register, digest-matched scripts), verifiers 006/007/011/015/018 (no
untrusted string reaches a shell; `set -uo pipefail` without `-e`, one reward per exit path),
trace fixtures and mining JSON (no keys, emails or local paths), v1 leftovers (`driver.py` used by
the regression track only; `probe.py`, `sandbox.py`, `register_kitaru_agent.py`, per-task judges
deleted; no stale references outside `tasks/`).

### [SWE] 2026-09-11 15:40 — Implementation

**Files modified**
- `evals/harness/verifier.py` — `host_script_env()` + `HOST_SCRIPT_ENV_ALLOW_LIST`; the Verifier gets
  an allow-listed env (`PATH`/`HOME`/`TMPDIR`/`LANG`/`LC_*` + `VERIFIER_DIR`), never `os.environ`.
- `evals/harness/seed.py`, `evals/harness/oracle_sanity.py` — `setup.sh` / `solve.sh` run with the
  same allow-listed env (reproducibility, per the reviewer's hygiene note).
- `evals/harness/trial.py` — `git_sha()` delegates to `decode.observability.git_sha`; `SANDBOX_GIT_TOKEN`
  added to `STRIPPED_ENV_VARS`; `os.killpg(process.pid, …)`.
- `importers/opik_importer.py` — `_build_node` unwraps tool payloads through `tool_span_arguments` /
  `tool_span_result` (both spellings), keeping an unrecognised envelope — and a DEFERRED span's whole
  input, marker included — intact.
- `src/decode/runtime/recording.py` — `recorded_session_id()` deleted (always `None`).
- `src/decode/runtime/headless.py` — no `_RunState.kitaru_session_id`; the summary key is a literal
  `None` and `trace_metadata(model)` omits it (ADR-0022 §10 note unchanged, still accurate).
- `tests/unit/evals/benchmark/conftest.py` — `grade_workspace` calls `grade_checkout`; `_read_reward`
  and the hand-copied run step deleted (a real timeout now reports `timed_out` instead of escaping).
- `tests/unit/evals/harness/test_verifier.py` (new, closes the `tests/` ↔ `src/` mirror gap),
  `test_seed.py`, `test_oracle_sanity.py`, `test_trial.py`, `tests/unit/importers/test_opik_importer.py`
  — 18 new tests (19 nodes).
- `evals/benchmark/tasks/README.md` — the Verifier contract + audit checklist now state the env
  allow-list, so a future task author writes `test.sh` / `solve.sh` against it.
- `docs/adr/0022-…md` — one `## Implementation notes` bullet naming the §3 trust boundary.
- `docs/glossary.md` — one `Signature` row.

**Per-item outcome**

| Item | Outcome | Note |
|---|---|---|
| Blocker 1 — Verifier env allow-list | fixed | `host_script_env()`; `GIT_*` confirmed NOT needed (015/018 only read history); `PYTHONPATH`/`PYTHONHOME` dropped too. Same env applied to `seed.py::_run_setup_script` and `oracle_sanity.py::_run_oracle`. `driver.py` is in-process — out of scope, untouched. |
| Blocker 2 — duplicate `git rev-parse` | fixed | `trial.git_sha()` → `decode.observability.git_sha(str(Path.cwd()))`; no-arg wrapper kept for `benchmark.py` / `regression.py`. Import weight measured: marginal cost of `decode.observability` after `evals.harness` is 0.001 s (logfire/opik are already loaded by the package `__init__`), so a module-level import is the right direction. |
| Blocker 3 — conftest re-implements grade time | fixed | `return grade_checkout(task, workspace)`; `_read_reward`, `os`, `shutil` and the four grade-time constants gone; `timed_out` no longer hardcoded. |
| Nit 1 — `recorded_session_id` | fixed | Accessor + `_RunState` field deleted; summary key stays `kitaru_session_id: null` (pinned by `test_the_summary_reports_a_null_kitaru_session_id`, unchanged). `build_summary` / `trace_metadata` parameters kept. |
| Nit 2 — `headless._run_task(state=None)` | **skipped** | Not in the rollup's enumerated fix list; no behaviour impact, and a tighter diff is worth more at re-review. |
| Nit 3 — `SANDBOX_GIT_TOKEN` strip | fixed | Added to `STRIPPED_ENV_VARS`; a local Seed Repo needs no PAT (ADR-0016). |
| Nit 4 — importer `_build_node` spellings | fixed | Reads through the ONE reader; an unrecognised envelope rides whole, and a DEFERRED span keeps its input verbatim — the arguments and the `ApprovalRequired` marker share that dict, so unwrapping would have cost the imported node the fact that the call never ran (`test_a_deferred_call_imports_with_its_deferral_evidence_intact`, red before the guard). |
| Nit 5 — bootstrap magic slice / `Runner = Any` | **skipped** | Not enumerated; `Runner` is a duck-typed CLI callable the script keeps deliberately loose, and the `[5:]` slice is covered by `test_bootstrap_kitaru.py`. Cosmetic only. |
| Nit 6 — seven scoring fns re-fold `_total` | **skipped** | Already DRY via the existing `_total` helper; the further collapse to one list-returning `experiment_scores` cannot be verified locally (it changes the paid `evaluate()` contract) and only saves a 7× in-memory fold on a cold path. |
| Nit 7 — `Signature` glossary row | fixed | One row, the four-tuple spelled out. |
| Nit 8 — no `test_verifier.py` | fixed | New file, 10 tests: allow-list (leak + what still gets through), timeout, silent/garbage/stale reward, informational exit code, `read_reward`. |
| Nit 9 — `os.getpgid` race | fixed | `os.killpg(process.pid, sig)` + the one-line why. |

**Tests**
- `make ci` (the target AC1 names: `uv lock --check` + format-check + lint-check + full suite):
  **3078 passing, 1 skipped** (`fastapi` absent), 0 failing.
- Unit: 2964 passing — `make pre-commit` (format + lint + full unit suite).
- Integration: 114 passing — `make integration-tests` (src/ changed).
- Red-first proof for each new behaviour: the env-leak test fails with `AssertionError: assert
  'SECRET_SENTINEL' not in {...}` on the pre-fix `env={**os.environ, …}`; the seed/oracle env tests
  and the two `git_sha` delegation tests were likewise confirmed red before their fix.

**Acceptance criteria**
- [x] Blocker 1 — `tests/unit/evals/harness/test_verifier.py::test_a_parent_secret_never_reaches_the_verifier`
      (+ `test_the_verifier_still_gets_what_it_needs_to_run`, so an empty env cannot pass); oracle gate
      and all three edge-case suites still green (213 tests in `tests/unit/evals/benchmark/`).
- [x] Blocker 2 — `test_trial.py::test_git_sha_delegates_to_the_products_one_rev_parse_helper` +
      `::test_the_trial_carries_no_second_rev_parse_implementation`; provenance value unchanged
      (`test_result_json_records_the_trial`, `test_run_benchmark_experiment_config_carries_the_full_provenance`).
- [x] Blocker 3 — `tests/unit/evals/benchmark/conftest.py` now calls `grade_checkout`; the three
      `test_verifier_edge_cases_{easy,medium,hard}.py` suites pass unchanged.
- [ ] Tester re-runs full QA suite and PASSES
- [ ] PA re-runs acceptance review and ACCEPTS
- [ ] PR Reviewer re-runs and reports `NO BLOCKERS`

**Evidence**
```
$ make ci
...
SKIPPED [1] tests/unit/decode/remote/test_app.py:130: could not import 'fastapi'
================= 3078 passed, 1 skipped in 463.67s (0:07:43) ==================

$ make integration-tests
======================= 114 passed in 394.87s (0:06:34) ========================

$ SECRET_SENTINEL=… GEMINI_API_KEY=… SANDBOX_GIT_TOKEN=… uv run python <oracle gate, 3 real tasks>
host_script_env() = ['HOME', 'LANG', 'PATH', 'TMPDIR']
007-fix-failing-test: oracle=1.0 nop=0.0 timed_out=False
015-secret-scrub: oracle=1.0 nop=0.0 timed_out=False
018-git-bisect-revert: oracle=1.0 nop=0.0 timed_out=False
OK — oracle 1 / nop 0 with the operator's secrets in the parent env

$ uv run python -m evals benchmark --help
Usage: python -m evals benchmark [OPTIONS]   (harness imports clean after the delegation)
```

**Notes**
- The three real tasks in the e2e are the ones the scrub could plausibly break: 007 runs
  `python3 -m unittest` on agent-edited modules, 015 and 018 shell out to `git`. `USER` / `LOGNAME`
  were NOT needed — no git identity or ownership error appeared.
- `PYTHONPATH` is dropped from the Verifier env on purpose (it would point a Verifier at the
  harness's own site-packages); the e2e above ran with `PYTHONPATH=.` set in the parent and every
  task still graded correctly.
- NOT RUN — a real `decode run --summary-json` against a provider (needs a key); the headless leg of
  Nit 1 is covered by `tests/integration/test_opik_headless_trace.py` + `test_headless.py` (159 unit
  tests in `tests/unit/decode/runtime/`).
- Two host-side `subprocess.run` calls deliberately still inherit `os.environ`, both scoped out by
  the reviewer: `tests/unit/evals/benchmark/conftest.py`'s `post_setup` (it stages the *answer* —
  018's `git revert` needs a committer identity from the operator's `~/.gitconfig` — and is upstream
  of grading, which is what Blocker 3 folded onto `grade_checkout`), and `seed.py::_run_git` (the
  seeder's own git plumbing, repo-authored, not a task script).
- Uncommitted-but-kept: the PR-Reviewer log entry on `tasks/done/167-…md` rides in this task's commit.

### [Tester] 2026-09-11 16:20 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check`: 350 files already formatted;
  `ruff check`: all checks passed; `make pre-commit` unit suite green)
- Unit tests: 2964 passed / 0 failed / 1 skipped (`fastapi` absent, pre-existing)
- Integration tests: 114 passed / 0 failed (`make integration-tests`, 367.98s)
- Warnings: 0 (`filterwarnings=["error"]` in `pyproject.toml`; no warning surfaced in either run)

**E2E adversarial pass**
- Happy path: real oracle gate over `007-fix-failing-test`, `015-secret-scrub`,
  `018-git-bisect-revert` via `evals.harness.oracle_sanity.run_verifier`, with
  `SECRET_SENTINEL`/`GEMINI_API_KEY`/`SANDBOX_GIT_TOKEN` planted in the parent shell →
  `oracle_reward=1.0`, `nop_reward=0.0` for all three, no sentinel in stdout/stderr (PASS)
- Break path 1 (hostile/exfiltration: secret in parent env reaches `tests/test.sh`,
  `environment/setup.sh`, `solution/solve.sh`): scratch task whose three scripts each `env >`
  a file; ran via `run_verifier(..., with_solution=True)` with the same three sentinels planted
  → `setup.sh` dump and `solve.sh` dump both show `PATH present: True`, `HOME present: True`,
  `SECRET_SENTINEL present: False`, `GEMINI_API_KEY present: False`,
  `SANDBOX_GIT_TOKEN present: False`; reward 1.0 (grading unaffected). `test.sh`'s own leak is
  additionally pinned by `test_verifier.py::test_a_parent_secret_never_reaches_the_verifier`
  (green) (PASS)
- Break path 2 (boundary: allow-listed vars absent from the parent): `os.environ.pop("TMPDIR",
  None)`, `os.environ.pop("LANG", None)`, then `host_script_env(VERIFIER_DIR="/tmp/x")` →
  no `KeyError`, both keys simply absent from the returned dict, `VERIFIER_DIR` still present
  (PASS)
- Break path 3 (state edge: a verifier that runs past its timeout / writes nothing / a stale
  reward file from an earlier run): read `test_verifier.py::
  test_a_verifier_that_runs_past_its_timeout_is_a_verifier_error`,
  `::test_a_verifier_that_writes_nothing_earns_no_reward`,
  `::test_a_stale_reward_from_an_earlier_run_is_never_read_as_this_runs_verdict` — all three
  green under `make pre-commit`; independently re-ran the whole `tests/unit/evals/harness/
  test_verifier.py` file (10/10 passed) (PASS)

**Acceptance criteria**
- [x] PASS — Blocker 1 (Verifier env allow-list) — `host_script_env()` (`evals/harness/
      verifier.py:41-64`) applied to `grade_checkout` (`verifier.py:105`), `seed.py::
      _run_setup_script:104`, `oracle_sanity.py::_run_oracle:85`; `tests/unit/evals/harness/
      test_verifier.py` (10 tests, all green) proves a `SECRET_SENTINEL`/`GEMINI_API_KEY`/
      `SANDBOX_GIT_TOKEN`/`PYTHONPATH` sentinel never reaches the Verifier while `PATH`/`HOME`/
      `VERIFIER_DIR` still do; independently reproduced red-then-green against the pre-fix
      `env={**os.environ, …}` mentally matches the SWE's stated red-run; my own scratch-task
      e2e (above) confirms the same for `setup.sh`/`solve.sh`; oracle gate (`make ci`'s target,
      exercised here via `make pre-commit` + `make integration-tests`) and all three edge-case
      suites (`test_verifier_edge_cases_{easy,medium,hard}.py`, 213 tests total in
      `tests/unit/evals/benchmark/`) pass unchanged. ADR-0022 §3 implementation note present
      (`docs/adr/0022-evals-v2-own-harbor-on-opik.md`); README env contract updated
      (`evals/benchmark/tasks/README.md:131-135,179-180`).
- [x] PASS — Blocker 2 (single `git rev-parse` helper) — `evals/harness/trial.py::git_sha()`
      delegates to `decode.observability.git_sha(str(Path.cwd()))` (`trial.py:302-309`); grep
      across `evals/` finds no second `subprocess.run(["git", "rev-parse", "HEAD"], …)` outside
      `decode.observability.metadata.git_sha` itself (`seed.py`'s `_git`/`_run_git` are a
      separate, repo-authored git-plumbing helper for building the Seed Repo's own history —
      not a rev-parse-HEAD-for-provenance duplicate); `test_trial.py::
      test_git_sha_delegates_to_the_products_one_rev_parse_helper` green; provenance value
      unchanged (`test_result_json_records_the_trial`,
      `test_run_benchmark_experiment_config_carries_the_full_provenance` both green).
- [x] PASS — Blocker 3 (conftest calls `grade_checkout`) — read `tests/unit/evals/benchmark/
      conftest.py:83-88`: `grade_workspace`'s `_grade` closure now ends with
      `return grade_checkout(task, workspace)`; `_read_reward`, the hand-rolled `shutil.copytree`
      + `subprocess.run(env={**os.environ, …})` block, and the `os`/`shutil` imports are gone;
      `timed_out` is no longer hardcoded `False` — it now comes from the real
      `VerifierResult.timed_out ` `grade_checkout` returns. The three
      `test_verifier_edge_cases_{easy,medium,hard}.py` suites pass unchanged (26 tests, part of
      the 213 counted above).
- [x] PASS — Tester re-runs full QA suite and PASSES — see Test summary + E2E pass above.

**Evidence**
```
$ make format-check && make lint-check
uv run ruff format --check
350 files already formatted
uv run ruff check
All checks passed!

$ make pre-commit
...
=========================== short test summary info ============================
SKIPPED [1] tests/unit/decode/remote/test_app.py:130: could not import 'fastapi'
================== 2964 passed, 1 skipped in 62.72s (0:01:02) ==================

$ make integration-tests
...
======================= 114 passed in 367.98s (0:06:07) ========================

$ uv run python <oracle gate over 007/015/018, SECRET_SENTINEL + fake GEMINI_API_KEY + fake
  SANDBOX_GIT_TOKEN planted>
007-fix-failing-test: oracle_reward=1.0 nop_reward=0.0 oracle_timed_out=False
  oracle: sentinel_in_output=False
  nop: sentinel_in_output=False
015-secret-scrub: oracle_reward=1.0 nop_reward=0.0 oracle_timed_out=False
  oracle: sentinel_in_output=False
  nop: sentinel_in_output=False
018-git-bisect-revert: oracle_reward=1.0 nop_reward=0.0 oracle_timed_out=False
  oracle: sentinel_in_output=False
  nop: sentinel_in_output=False

$ uv run python <scratch task, setup.sh/solve.sh each dump `env`>
reward: 1.0 timed_out: False
--- setup.sh ---
PATH present: True / HOME present: True / SECRET_SENTINEL present: False /
GEMINI_API_KEY present: False / SANDBOX_GIT_TOKEN present: False
--- solve.sh (oracle) ---
PATH present: True / HOME present: True / SECRET_SENTINEL present: False /
GEMINI_API_KEY present: False / SANDBOX_GIT_TOKEN present: False

$ uv run kitaru importer test --entrypoint parser --payload importers/fixtures/opik-sample.json \
  --non-interactive importers/opik_importer.py
{"ok": true, "item": {"loaded": true, "invoked": true, "sessions": 2, "failures": 1, "items": 3}}
```

**Nit judgments (5 fixed nits spot-checked, 3 skips judged, 1 out-of-scope confirmed)**
- Nit 1 (`recorded_session_id` deleted) — confirmed: `src/decode/runtime/recording.py` no
  longer defines it; `headless.py` passes `kitaru_session_id=None` literally
  (`headless.py:344-346`), matches ADR-0022 §10.
- Nit 3 (`SANDBOX_GIT_TOKEN` stripped) — confirmed: `trial.py::STRIPPED_ENV_VARS` now includes
  it (`trial.py:81-90`); `test_trial.py:465,555` assert it is absent from `child_env(...)`.
- Nit 4 (importer both spellings, deferred span intact) — confirmed: `_build_node` now reads
  through `tool_span_arguments`/`tool_span_result` and skips unwrapping entirely when
  `tool_span_deferred(span)` is true (`opik_importer.py:225-237`);
  `test_a_deferred_call_imports_with_its_deferral_evidence_intact` (green) proves a DENIED
  span's `input` — arguments + the `ApprovalRequired` marker — rides whole; independently ran
  `kitaru importer test` against `importers/fixtures/opik-sample.json` (2 sessions, 1 failure,
  3 items, `ok: true`) to confirm the importer still loads and runs end to end after the change.
- Nit 8 (`test_verifier.py` added) — confirmed: 10 tests, closes the `tests/`↔`src/` mirror gap.
- Nit 9 (`os.killpg(process.pid, …)`) — confirmed: `_signal_group` no longer calls
  `os.getpgid` (`trial.py:409-414`); reasoning (pid IS pgid under `start_new_session=True`) is
  correct and matches the comment.
- Skipped Nit 2 (`_run_task(state=None)` fallback) — reasonable: grepped both call sites of
  `_run_task(` in `src/`; exactly one caller (`headless.py:311`), always passes `state` — the
  skip costs nothing and the SWE's stated rationale (tighter diff at re-review) is defensible.
- Skipped Nit 5 (bootstrap magic slice / `Runner = Any`) — reasonable: cosmetic, already
  covered by `test_bootstrap_kitaru.py`, no behavior risk.
- Skipped Nit 6 (fold seven scoring fns) — reasonable: SWE's stated reason (touches the paid
  `evaluate()` contract, cannot be verified locally without cost) is a legitimate reason to defer
  a cold-path refactor rather than risk it in a blocker-fix pass.
- Out of scope Nit 7 (glossary `Signature` row) and README env-contract updates — confirmed
  present and topically correct (`docs/glossary.md` Trace Mining section; `evals/benchmark/
  tasks/README.md`).

**Deliberate non-changes judged**
- `seed.py::_run_git` inherits `os.environ`: confirmed reasonable — it runs fixed git
  subcommands (`-C <dest> -c user.name=… -c user.email=… <verb> …`) on trial-owned/seed-owned
  paths, never a task-authored script; no untrusted content is interpreted by it, so the
  Blocker-1 threat model (agent-authored code running with operator secrets) does not apply.
- `tests/unit/evals/benchmark/conftest.py`'s `post_setup` inherits `os.environ`: confirmed
  reasonable — it is test-suite-only (the edge-case suites construct fixture git states with
  it), never runs in a real trial or the oracle gate, and the snippet is authored by the test
  file itself, not by an agent or a task. `grade_checkout` (the one path that actually grades
  agent-authored code, both in real trials at `trial.py:446` and in these same fixtures via
  Blocker 3's fix) is confirmed the only host-side path now scrubbed — grepped every
  `grade_checkout(` call site in `evals/` + `tests/`: `trial.py:446`, `oracle_sanity.py:69`,
  `conftest.py:88`, and `test_verifier.py`'s own direct calls; all route through the same
  allow-listed `grade_checkout`.

**Other issues found (not blocking, PASS with note)**
- `evals/harness/verifier.py::read_reward` (unchanged by this task, pre-existing behavior)
  accepts `"nan"` and `"1e400"` as valid rewards via bare `float()` — `float("nan")` and
  `float("inf")` both parse without raising, so a `reward.txt` containing `nan` or an
  overflowing literal would NOT be treated as a verifier ERROR the way empty/non-numeric text
  is. Out of scope for this rollup (not introduced or touched by the three Blockers), but worth
  a follow-up task given `reward` is documented as "one float in `[0, 1]`".

**VERDICT: PASS**

### [SWE] 2026-09-11 16:45 — Fix (Tester's pre-commit note)

**Files modified**
- `evals/harness/verifier.py` — `read_reward` returns `None` for a non-finite reward: `float()`
  parses `nan` / `inf` / `-inf` / an overflowing `1e400` without raising, and a silent `nan` would
  poison every mean it reaches. ADR-0022 §3's "absent, empty or non-numeric reward is a verifier
  error" already covers it, so only the function's docstring changed.
- `tests/unit/evals/harness/test_verifier.py` — `test_read_reward_is_none_for_a_non_finite_reward`
  (`nan`, `inf`, `-inf`, `1e400`).

**Tests**
- Unit: 2965 passing, 1 skipped (`fastapi` absent), 0 failing — `make pre-commit`.
- Red-first: the new test failed `AssertionError: nan / assert nan is None` before the guard.
- E2E: `007-fix-failing-test` through `run_verifier` still grades `1.0` with the Oracle and `0.0`
  without it; the same task with a `tests/test.sh` that writes `nan` now grades `None`
  (a verifier ERROR), `timed_out=False`.

**Notes**
- Non-finite ONLY — no `[0, 1]` range check was added; clamping is a behavior change beyond the note.

