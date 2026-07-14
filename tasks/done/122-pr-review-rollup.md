---
status: done
feature: evals
---

# [PR review rollup] decode eval suite — benchmark, regression probes, demo skills, Opik harness

Tags: `rollup`, `pr-review`
Refs: PR #35 (branch: `feat/evals`)

## Scope

PR Reviewer found 1 Blocker and 5 Nits in the diff (232 files, ~21k insertions; tasks 103-121,
ADR-0017). The SWE must fix the Blocker (and may fix Nits at their discretion) in a single
coordinated pass, then hand back to the Tester. Pipeline re-runs from QA → PA acceptance → push →
re-review.

PA-accepted limitations were NOT re-flagged: [HUMAN] keyed spot-runs, [BLOCKED opik>=2.0] Test
Suites live run + litellm<1.78 pin, probe 12 MCP skip-guard, pre-push hook BlockingIOError flake.

## Acceptance Criteria

- [x] Blocker 1: `evals/regression/test_thresholds.py`'s key preflight is provider-aware and
      consistent with the suite's other two key guards. Concretely: with `LLM_PROVIDER=openrouter`
      and `OPENROUTER_API_KEY` + `OPIK_API_KEY` set (no `GEMINI_API_KEY` anywhere),
      `make eval-regression` RUNS the threshold gate instead of skipping it; with the provider key
      genuinely missing it still skips friendly, naming the RIGHT variable for the active provider.
      A unit test in `tests/unit/evals/regression/` pins the provider mapping (openrouter →
      OPENROUTER_API_KEY, modal → MODAL_ENDPOINT_URL, gemini → GEMINI_API_KEY).
- [x] Tester re-runs full QA suite and PASSES (including the new regression test above).
- [ ] PA re-runs acceptance review and ACCEPTS.
- [ ] PR Reviewer re-runs and reports `NO BLOCKERS`.

## Blockers (detail)

### 1. [Standards / Clean code] — evals/regression/test_thresholds.py:50-62 (`REQUIRED_KEYS` / `_missing_keys`)
- **What's wrong:** the pre-merge threshold gate — the feature's headline ritual — gates its own
  execution on a hardcoded `REQUIRED_KEYS = ("GEMINI_API_KEY", "OPIK_API_KEY")` read from
  `os.environ`. This is a third, divergent copy of the key-preflight logic that
  `evals/harness/keys.py::eval_keys_missing()` and `evals/harness/online.py::online_keys_missing()`
  both implement provider-aware (gemini → GEMINI_API_KEY, openrouter → OPENROUTER_API_KEY, modal →
  MODAL_ENDPOINT_URL) and settings-backed (a key in `.env` counts — `keys.py`'s own docstring names
  the raw-env check as the exact bug it exists to avoid, and task 120's log records that as a
  deliberate design decision). Consequence: an openrouter- or modal-configured operator runs
  `make eval-regression`, the provider-aware `keys.py` guard passes, `evals sync` runs — and then
  the gate pytest SKIPS demanding a `GEMINI_API_KEY` the operator neither has nor needs. `make`
  exits 0 having gated nothing: a vacuous green on the ritual whose whole promise is "the graders
  are honest." Same vacuous-skip for a gemini user whose keys live only in `.env`.
- **Why it's a Blocker:** a real defect in shipped behavior, and doc drift in the same PR —
  `docs/evals.md` promises "OPIK_API_KEY **plus the active provider's key**" and "without the keys
  every target skips friendly" for this exact track. Not covered by any PA-accepted limitation.
- **Suggested fix:** derive the skip predicate from one shared helper — reuse
  `evals.harness.keys.eval_keys_missing()` (settings-backed, provider-aware) in the session
  fixture's skip check, or if the raw-env read is intentional (litellm/opik SDKs consume process
  env directly), mirror the provider mapping over `os.environ` and say so in the module docstring.
  Either way, collapse the three copies to one mapping (see Nit 3) so they can't drift again.
- **Regression test:** unit test asserting the ritual's missing-key predicate returns the
  provider-correct variable names for each of the three providers (and empty when the active
  provider's key + OPIK_API_KEY are present).

## Nits (non-blocking; will be appended to PR description if pipeline advances)

### 1. [Clean code] — Makefile:37 (`eval-regression`)
- **Suggestion:** `python -m evals sync --regression` also syncs the benchmark dataset —
  `--benchmark` defaults to on, so `--regression` alone doesn't scope the sync. Harmless
  (idempotent, no LLM cost) but not what the target's help text says, and task 121's own QA used
  the correct `sync --no-benchmark --regression`. Add `--no-benchmark`.

### 2. [Standards] — evals/run.py:180 (`suite` command)
- **Suggestion:** `run_test_suite()` is the only opik-reaching command body not wrapped in
  `opik_boundary()`. Version-gated today (opik 1.9.8 stops before any network call), but the moment
  the opik pin lifts, a present-but-invalid `OPIK_API_KEY` here dumps the raw `ApiError` traceback
  task 121 just eliminated from the other four subcommands. One-line `with opik_boundary():` wrap.

### 3. [Clean code] — evals/harness/keys.py:23-45 / evals/harness/online.py:61-83
- **Suggestion:** `eval_keys_missing()` and `online_keys_missing()` are the same provider→key
  mapping copy-pasted (and the Blocker is its third, divergent copy). Extract one shared helper
  (e.g. in `evals/harness/keys.py`) both call; fixes naturally alongside the Blocker.

### 4. [Standards] — evals/harness/sandbox.py:101
- **Suggestion:** `benchmark_sandbox` imports the private `decode.tools.bash._get_executor`;
  `runtime/flow.py` (the cited pattern) only uses the public `warm_executor`. Expose a public
  accessor on the seam (or have `warm_executor` return the executor) so course material doesn't
  reach into decode's private surface.

### 5. [Clean code] — evals/harness/regression.py:377
- **Suggestion:** `experiment_config()` imports the private `_agent_model` / `_git_sha` from
  `evals.harness.benchmark`. Cross-module private reach; promote them to public names in one shared
  harness spot (they are also the natural home for the `experiment_config` overlap between the two
  runners).

---

Refs: PR #35

## Log

### [SWE] 2026-07-14 — Implementation

**Files modified**
- `evals/regression/test_thresholds.py` — Blocker fix: drop hardcoded `REQUIRED_KEYS`/`_missing_keys`
  (raw `os.environ` read of `GEMINI_API_KEY`); the session fixture now skips on the shared, provider-
  aware `eval_keys_missing()`. Docstring updated.
- `evals/harness/keys.py` — documented `eval_keys_missing()` as the ONE shared preflight for all three
  tracks (module + fn docstrings).
- `evals/harness/online.py` — Nit 3: `online_keys_missing()` now delegates to `eval_keys_missing()`
  (killed the duplicate provider→key mapping; kept the judge-key docstring reasoning).
- `Makefile` — Nit 1: `eval-regression` now runs `evals sync --no-benchmark --regression`.
- `evals/run.py` — Nit 2: wrapped `run_test_suite()` in `opik_boundary()` (future-proof for opik>=2).
- `src/decode/tools/bash.py` — Nit 4: added public `active_executor()` seam read accessor (mirrors
  `active_backend`).
- `evals/harness/sandbox.py` — Nit 4: use public `active_executor()` instead of importing private
  `decode.tools.bash._get_executor`.
- `evals/harness/benchmark.py` — Nit 5: promoted `_agent_model`/`_git_sha` → public `agent_model`/
  `git_sha` (one shared resolver for both experiment tracks).
- `evals/harness/regression.py` — Nit 5: import the public `agent_model`/`git_sha`.
- `docs/evals.md` — synced the `eval-regression ==` comment with the `--no-benchmark` change.
- `tests/unit/evals/regression/test_threshold_gate_keys.py` — NEW: pins the provider→key mapping
  (gemini/openrouter/modal, .env-only via settings) and asserts the gate wires the shared predicate
  (no divergent copy).
- `tests/unit/decode/tools/test_bash_sandbox_selection.py` — added two tests for the public
  `active_executor()` accessor (none + docker-warmed memo).

**Tests**
- Unit: 1966 passing, 0 failing — `make unit-tests` / `make pre-commit` (full suite). New: 7 in
  `test_threshold_gate_keys.py`, 2 in `test_bash_sandbox_selection.py`.
- Integration: N/A — no infra changes; the eval harness/sandbox seam is unit-covered offline.

**Acceptance criteria**
- [x] Blocker 1 — provider-aware, shared-helper skip predicate + provider-mapping unit test. Verified by
  `tests/unit/evals/regression/test_threshold_gate_keys.py` and the e2e proof below.
- [ ] Tester re-runs full QA and PASSES — Tester's step.
- [ ] PA re-runs acceptance and ACCEPTS — later stage.
- [ ] PR Reviewer re-runs, reports NO BLOCKERS — later stage.

**Evidence**
```
$ env -u GEMINI_API_KEY LLM_PROVIDER=openrouter OPIK_API_KEY=opik-xxx OPENROUTER_API_KEY=or-xxx \
    python -c "from evals.harness.keys import eval_keys_missing; print(eval_keys_missing())"
[]                              # gate RUNS for openrouter — no spurious GEMINI_API_KEY demand

$ env -u GEMINI_API_KEY -u OPENROUTER_API_KEY LLM_PROVIDER=openrouter OPIK_API_KEY=opik-xxx \
    python -c "from evals.harness.keys import eval_keys_missing; print(eval_keys_missing())"
['OPENROUTER_API_KEY']          # names the RIGHT variable for the active provider

$ env -u GEMINI_API_KEY -u OPENROUTER_API_KEY -u OPIK_API_KEY LLM_PROVIDER=openrouter \
    pytest evals/regression/test_thresholds.py -q
SKIPPED regression gate needs OPIK_API_KEY, OPENROUTER_API_KEY — skipping (exit 0).
2 skipped in 0.68s   (exit 0)

$ make pre-commit
1966 passed in 111.45s
```

**Notes**
- Chose the settings-backed `eval_keys_missing()` (the Blocker's preferred fix) over mirroring the
  provider map onto `os.environ`: the whole suite is settings-backed (a key in `.env` counts, per
  task 120), so consistency + `.env`-only support both point here. Three copies collapsed to one.
- `active_executor()` is a one-line public accessor with one concrete out-of-tree caller (the eval
  harness); mirrors the existing public `active_backend()` on the same seam.
- No architectural forks; no ADR/glossary edits.

### [SWE] 2026-07-14 — Tester follow-up (non-blocking)

**Files modified**
- `tests/unit/evals/test_run.py` — added `test_suite_subcommand_reports_an_invalid_opik_key`,
  mirroring the other four subcommands' mocked-`ApiError` tests, covering Nit 2's `opik_boundary()`
  wrap on `suite`.

**Tests**
- Unit: 1967 passing, 0 failing — `make unit-tests`. New test confirms `suite` translates a raw
  `ApiError` to one friendly line (would have leaked the traceback before the Nit 2 fix).

**Notes**
- Tester PASSED round 1 (revert-checked). This is the cheap coverage addition they flagged.

### [Tester] 2026-07-14 14:40 — QA (task 122 rollup)

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check`, `make pre-commit` — 1966
  unit tests passed, 0 failed, 0 warnings)
- Unit + integration (`make ci`): 2079 passed, 2 skipped (both legitimate `[HUMAN]`-tagged live-key
  skips: `test_observability_capstone.py` needs `OPIK_API_KEY`+`GEMINI_API_KEY`,
  `test_subagents_capstone.py` needs `GEMINI_API_KEY`) in 439.99s. Exit 0.
- Warnings: 0

**E2E adversarial pass**

1. **THE BLOCKER SCENARIO, live (no mocks):**
   - `env -u GEMINI_API_KEY LLM_PROVIDER=openrouter OPIK_API_KEY=opik-xxx OPENROUTER_API_KEY=or-xxx
     uv run python -c "from evals.harness.keys import eval_keys_missing; ..."` → `missing: []` — gate
     does NOT skip for an openrouter operator with no `GEMINI_API_KEY` anywhere (PASS, proves the fix).
   - `env -u GEMINI_API_KEY -u OPENROUTER_API_KEY -u LLM_PROVIDER OPIK_API_KEY=opik-xxx ...` (gemini
     default, key genuinely missing) → `missing: ['GEMINI_API_KEY']` — friendly, names the right var
     (PASS).
   - `env ... LLM_PROVIDER=modal -u MODAL_ENDPOINT_URL OPIK_API_KEY=opik-xxx ...` → `missing:
     ['MODAL_ENDPOINT_URL']` (PASS — modal mapping correct too, beyond the two providers named in the
     brief).
   - `.env`-only resolution: wrote a scratch `.env` with `GEMINI_API_KEY`/`OPIK_API_KEY` and no process
     env, instantiated `Settings()` from that cwd with all relevant vars `-u`'d out of the process env
     → `gemini key from .env: dotenv-gem-key`, `opik key from .env: dotenv-opik-key` — settings-backed
     resolution confirmed (a key in `.env` counts, matching keys.py's own docstring claim) (PASS).
   - `env -u ... -u MODAL_ENDPOINT_URL -u LLM_PROVIDER make eval-regression` (all keys absent) →
     `evals: skipped — set OPIK_API_KEY, GEMINI_API_KEY to run (see the Evals block in
     .env.example).` exit 0 (PASS — friendly, no `sync` side effect attempted since the `if` guard
     short-circuits).
2. **Identity-check test genuinely protective (revert-check):** `git stash push` on
   `evals/regression/test_thresholds.py` only, restoring the pre-fix version with the hardcoded
   `REQUIRED_KEYS`/`_missing_keys`. Ran `uv run pytest
   tests/unit/evals/regression/test_threshold_gate_keys.py -q` → `1 failed, 6 passed` —
   `test_gate_module_wires_the_shared_predicate_not_a_divergent_copy` went RED with
   `AttributeError: module 'evals.regression.test_thresholds' has no attribute
   'eval_keys_missing'` (the other 6 provider-mapping tests stayed green since they test
   `evals.harness.keys.eval_keys_missing` directly, not the gate's wiring — expected). `git stash pop`
   restored the fix; re-ran the same file → `7 passed`. VERDICT: PASS, the regression test is real
   and would have caught the original Blocker.
3. **`active_executor()` public accessor:** `src/decode/tools/bash.py:83-92` is a one-line alias
   (`return _get_executor()`), same docstring pattern as `active_backend()`. `evals/harness/sandbox.py`
   diff confirms `benchmark_sandbox()` now calls `active_executor()` instead of a local `from
   decode.tools.bash import _get_executor`. `grep -rn "_get_executor\|\._agent_model\|\._git_sha" evals/
   src/` → zero hits inside `evals/harness/*.py` (all remaining `_get_executor` hits are
   `src/decode/tools/bash.py`'s own internal use and decode's own unit tests, which is legitimate
   in-module private access, not a private-cross-module reach). Ran
   `tests/unit/decode/tools/test_bash.py`, `test_bash_sandbox_selection.py`,
   `tests/unit/decode/sandbox/test_select.py`, `tests/unit/evals/harness/test_sandbox.py` together →
   61 passed (PASS).
4. **Makefile / suite subcommand:**
   - `Makefile:41` confirmed `uv run python -m evals sync --no-benchmark --regression && ...`.
   - keyless `make eval-regression` → exit 0, friendly line, verified above (PASS).
   - `suite` ApiError path (mocked, since no dedicated unit test exists for this specific nit): patched
     `evals.harness.test_suite.run_test_suite` to raise `ApiError(status_code=401, ...)`, invoked
     `evals.run.cli` `suite` via `click.testing.CliRunner` → `exit_code: 1`, output `'Error: evals:
     Opik rejected the API key (401) — check OPIK_API_KEY (see the Evals block in
     .env.example).\n'`, no traceback, no `ApiError` leaked (PASS — confirms Nit 2's
     `opik_boundary()` wrap works exactly like the other four subcommands, even though
     `tests/unit/evals/test_run.py` doesn't yet have a dedicated `test_suite_subcommand_reports_an_
     invalid_opik_key` test alongside the benchmark/regression/sync/online ones — see Other issues).
5. **`src/decode/tools/bash.py` product-code touch:** diff is exactly the 10-line `active_executor()`
   function (docstring + `return _get_executor()`), no other lines changed. Ran the full
   `test_bash.py` (17) + `test_bash_sandbox_selection.py` (19, incl. the 2 new tests for
   `active_executor`) + `tests/unit/decode/sandbox/test_select.py` — all green, no behavior
   regression (PASS).

**Acceptance criteria**
- [x] PASS — Blocker 1: provider-aware, shared-helper (`eval_keys_missing`) skip predicate; openrouter
  scenario runs (not skips) with no `GEMINI_API_KEY`; gemini/modal scenarios skip friendly naming the
  right var; `.env`-only counts. Evidence: `tests/unit/evals/regression/test_threshold_gate_keys.py`
  (7 tests, all pass) + live e2e commands above + revert-check RED/GREEN cycle.
- [x] PASS — Tester re-runs full QA suite and PASSES. Evidence: this entry; `make ci` → 2079 passed,
  2 skipped (legitimate `[HUMAN]` live-key skips), exit 0; `make pre-commit` → 1966 passed.
- [ ] PA re-runs acceptance review and ACCEPTS — later stage, not Tester's to verify.
- [ ] PR Reviewer re-runs and reports `NO BLOCKERS` — later stage, not Tester's to verify.

**Evidence**
```
$ make pre-commit
======================= 1966 passed in 114.89s (0:01:54) =======================

$ make ci
================= 2079 passed, 2 skipped in 439.99s (0:07:19) ==================

$ env -u GEMINI_API_KEY LLM_PROVIDER=openrouter OPIK_API_KEY=opik-xxx OPENROUTER_API_KEY=or-xxx \
    uv run python -c "from evals.harness.keys import eval_keys_missing; ..."
missing: []
PASS: gate would NOT skip; identity holds

$ git stash push -m "temp-revert-blocker-for-red-check" -- evals/regression/test_thresholds.py
$ uv run pytest tests/unit/evals/regression/test_threshold_gate_keys.py -q
......F                                                                  [100%]
AttributeError: module 'evals.regression.test_thresholds' has no attribute 'eval_keys_missing'
1 failed, 6 passed in 1.48s
$ git stash pop
$ uv run pytest tests/unit/evals/regression/test_threshold_gate_keys.py -q
.......                                                                  [100%]
7 passed in 0.55s

$ grep -rn "_get_executor\|\._agent_model\|\._git_sha" evals/harness/*.py
(no output — zero private cross-module imports left in evals/)
```

**Other issues found**
- `evals/run.py`'s `suite` subcommand's `opik_boundary()` wrap (Nit 2) has no dedicated unit test
  mirroring `test_benchmark_subcommand_reports_an_invalid_opik_key` /
  `test_regression_subcommand_reports_an_invalid_opik_key` / `test_sync_subcommand_reports_an_invalid_
  opik_key` / `test_online_subcommand_reports_an_invalid_opik_key` in `tests/unit/evals/test_run.py`.
  Behavior verified correct by manual `CliRunner` invocation above, and this Nit is non-blocking /
  optional per the task scope, so not a FAIL — but worth a one-line follow-up test for full parity
  with the other four subcommands' coverage.
- No other issues. No secrets, no `print()` in touched library code, no stray unrelated file changes
  (the `tasks/done/*.md` diffs are pre-existing prior-review-cycle log appends from the earlier PA/PR
  Reviewer cycle, not part of this SWE pass — confirmed by content and by the orchestrator's own note
  at hand-off).

**VERDICT: PASS**

Hand off to PA for acceptance review.
