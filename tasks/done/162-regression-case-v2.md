---
id: 162-regression-case-v2
feature: evals-v2
status: done
---

# Regression Case v2: difficulty tiers, provenance fields, `--difficulty`, per-tier report, one definition → dataset metric + Test Suite assertion

Tags: `evals`, `regression`, `opik`
Depends on: None
Blocks: 164

## Scope
Turn `RegressionProbe` into `RegressionCase` (ADR-0022 §8): every one of the 21 probes is KEPT and tiered; mined cases will land beside them with provenance; each case registers in both Opik surfaces from one definition; the stale opik-1.9.8 gates go.

Tier assignment (easy = single-tool discipline · medium = planning/delegation/skills/memory/web/lsp · hard = compaction, gate, destructive caution, judged, json contract):
- **easy (5):** smoke-read-tool, 01-read-vs-cat, 02-grep-vs-bash, 03-edit-precision, 11-step-efficiency
- **medium (8):** 05-web-fetch-discipline, 06-lsp-diagnostics, 07-plan-mode-discipline, 08-todo-planning, 09-subagent-delegation, 10-skill-dispatch, 12-mcp-tool-usage (still skipped), 15-memory-obedience
- **hard (8):** 04-diff-minimality, 13-permission-deny-respect, 14-destructive-caution, 16-compaction-survival, 17-grounded-answer, 18-no-hallucinated-files, 19-template-compliance, 20-json-output-contract

## Acceptance criteria
- [x] `evals/regression/probe.py` → `case.py`: `RegressionCase` = today's fields + `difficulty: Literal["easy","medium","hard"]` (required), `symptom: str` (required; invariants use `"harness invariant: <one line>"`), `assertion: str` (required, non-blank; the natural-language form of the metrics for the Test Suite), `source_trace_id: str | None = None`, `thread_id: str | None = None`, `fixed_in: str | None = None`. Module attrs `CASE`/`CASES`; loader (`load_cases`) discovers them; the 21 modules migrated with the tiers above and one-sentence `symptom`/`assertion` each; `smoke_read_tool.py` stays the template.
- [x] `python -m evals regression [--case ID] [--difficulty D]` (was `--probe`); `sync --regression` upserts dataset `decode-regression-v2` items `{case_id, difficulty, tags, symptom, source_trace_id}` AND `get_or_create_test_suite("decode-regression-suite")` items `{data: {prompt, case_id, difficulty}, assertions: [case.assertion]}` from ONE loop (fake client asserts both calls); `SUITE_PROBE_IDS`/`ITEM_ASSERTIONS` in `test_suite.py` deleted; `python -m evals suite [--case ID] [--difficulty D]` runs `opik.run_tests` for real; `suite_api_available()` and every "opik 1.9.8" mention in code and `evals/regression/README.md` deleted; `SUITE_PASS_BAR` stays 0.8.
- [x] `run_regression(*, case_id, difficulty, experiment_name)`: `experiment_scoring_functions=[mean_per_metric]` replaces any post-hoc scoring; `experiment_config` gains `git_sha`, `case_count`, `difficulty`; experiment name `decode-regression-gate` for a full run, `decode-regression-gate-<tier>` for a filtered one (baseline compare stays like-for-like).
- [x] Per-tier report: `evals/regression/thresholds.py::scores_by_tier(test_results, tiers) -> {tier: {metric: mean}}` (pure, offline-tested); `test_thresholds.py` prints one table per tier via `_warn` and gates GLOBALLY as today (0.8 tool discipline / 0.7 judges — floors unchanged). `evals/regression/conftest.py::pytest_addoption("--difficulty")` so `make eval-regression ARGS='--difficulty hard'` forwards to both the sync and the gate (Makefile passes `$(ARGS)` to both).
- [x] Unit tests: loader accepts the new fields, rejects blank `assertion`/missing `difficulty`; tier counts 5/8/8 (+12 skipped still discoverable); `regression --difficulty easy` selects exactly the five; sync writes both surfaces; `scores_by_tier` on a hand-built matrix.
- [x] `evals/regression/README.md` updated: the case contract table (new fields), tiers, `--difficulty`, mined-case provenance fields and where mined cases live (beside, `cases/mined_<slug>.py`, ids `21-…` onward, tag `mined`).

## Out of scope
- Authoring mined cases (164). Kitaru (165). Lowering any threshold. Per-tier gating.

## Log
### [PA] 2026-09-10 — Grooming
Opik 2.2.36 has `get_or_create_test_suite` + `run_tests` (verified on the installed client); the version gate is dead code. Keep the deterministic metric as the gate and the assertion as the contrast — ADR-0017 §6's teaching point survives with less code. 05-web-fetch (judged, web) sits in medium by the human's decision: its tool-discipline half is mechanical; every other judged case is hard.

### [SWE] 2026-09-11 14:20 — Implementation

**Files modified**
- `evals/regression/probe.py` → `case.py` (git mv) — `RegressionCase`: + required `difficulty` / `symptom` / `assertion`, + optional `source_trace_id` / `thread_id` / `fixed_in`; `DIFFICULTIES` reuses the benchmark's `Difficulty` literal (one tier vocabulary).
- `evals/regression/loader.py` — `load_cases` / `case_by_id` / `RegressionCaseError` over `CASE`/`CASES`; new pure `select_cases(case_id, difficulty)` and `runnable_cases()` (the skip filter moved here out of the opik-importing harness, so the CLI's `sync` can use it lazily).
- `evals/regression/cases/*.py` (21 modules + `__init__`) — migrated to `CASE = RegressionCase(...)` with the task's tier assignment and a one-sentence `symptom` (`harness invariant: …`) + `assertion` each; `smoke_read_tool.py` stays the template.
- `evals/harness/datasets.py` — `decode-regression-v2` (created IN `settings.eval_project_name` — the task-161 mis-routing fix), `REGRESSION_SUITE_NAME` + `GLOBAL_ASSERTIONS` + `SUITE_EXECUTION_POLICY` moved here; `regression_items()` is ONE pass building both payloads; `sync_regression_cases()` writes both surfaces, `sync_regression_dataset()` writes the dataset alone (the billed gate never depends on the Test Suite API).
- `evals/harness/regression.py` — `CaseScopedMetric` (keys on `case_id`), `run_case`, `run_regression(*, case_id, difficulty, experiment_name, client)` with `experiment_scoring_functions=[mean_per_metric]`, `experiment_config(case_count=…, difficulty=…)` (+ `git_sha` kept), `GATE_EXPERIMENT_NAME` + `scoped_name()`, `project_name=evaluate_project_name(dataset)`; `nb_samples` dropped.
- `evals/harness/test_suite.py` — trimmed to the run surface: `SUITE_PROBE_IDS` / `ITEM_ASSERTIONS` / `suite_api_available` / `SuiteUnavailableError` / `build_suite` / `select_suite_probes` / `suite_items` deleted; `run_test_suite(*, case_id, difficulty, client)` registers the (sliced) suite and calls `opik.run_tests(..., worker_threads=1)`; `SUITE_PASS_BAR` stays 0.8.
- `evals/harness/benchmark.py` — `_evaluate_project_name` / `_select_tasks` made public (`evaluate_project_name` / `select_tasks`) so the regression harness and `sync --difficulty` reuse them instead of copying.
- `evals/regression/thresholds.py` — `scores_by_tier(test_results, tiers)` + `format_tier_scores()` (pure, opik-free, `scoring_failed` excluded).
- `evals/regression/test_thresholds.py` + new `evals/regression/conftest.py` — `--difficulty` option, tier tables via `_warn`, global gate unchanged (0.8 / 0.7), baseline compared against the SLICE's own experiment name.
- `evals/run.py` — `regression [--case] [--difficulty]`, `suite [--case] [--difficulty]`, `sync [--difficulty]` writing both regression surfaces.
- `Makefile` — `eval-regression` forwards `$(ARGS)` to BOTH the sync and the gate.
- `evals/regression/README.md` — rewritten: case contract table (new fields), tier table, `--difficulty`, mined-case provenance + placement, the two-surface table.
- Vocabulary sweep: "probe" → "case" across `evals/regression/**`, `evals/harness/{regression,driver,metrics}.py`, `evals/__init__.py`; every "opik 1.9.8" mention removed from `evals/**` code and tests.
- Tests: `tests/unit/evals/regression/test_probe.py` → `test_case.py` (+ tier / symptom / assertion / provenance contract), `test_loader.py` (5/8/8 counts, 12 discoverable-not-runnable, `--difficulty easy` = the five), `harness/test_datasets.py` (both surfaces via a fake client), `harness/test_regression.py` (tier naming, `experiment_scoring_functions`, config shape, `mean_per_metric`, project rule), `harness/test_test_suite.py` (rewritten), `regression/test_thresholds.py` (`scores_by_tier` matrix), `test_run.py` (CLI flags), `test_judge_phrasing.py` (the numeric-anchor ban now also covers `assertion`).

**Tests**
- Unit: 2720 passing, 0 failing, 1 skipped (pre-existing `fastapi` skip) — `make unit-tests`
- Integration: N/A — no `src/` change (`git status --short src` is empty)

**Acceptance criteria**
- [x] `case.py` / `RegressionCase` + new fields, `CASE`/`CASES`, `load_cases`, 21 modules migrated and tiered — `tests/unit/evals/regression/test_case.py`, `test_loader.py::test_the_registry_is_21_cases_tiered_five_eight_eight`
- [x] CLI `regression [--case] [--difficulty]`, `sync --regression` → both surfaces from one loop, suite/version dead code deleted — `tests/unit/evals/test_run.py`, `harness/test_datasets.py::test_sync_regression_cases_writes_the_dataset_and_the_test_suite`
- [x] `run_regression(*, case_id, difficulty, experiment_name)` + `mean_per_metric` + config/name — `harness/test_regression.py::test_run_regression_wires_evaluate`, `::test_a_tier_run_gets_its_own_experiment_name_and_config`
- [x] `scores_by_tier` + per-tier report + `--difficulty` conftest + Makefile `$(ARGS)` — `regression/test_thresholds.py::test_scores_by_tier_*`; option registration verified with `pytest evals/regression/test_thresholds.py --difficulty hard --collect-only`
- [x] Unit tests per the AC list
- [x] README updated

**Evidence**
```
$ make unit-tests
======================= 2720 passed, 1 skipped in 54.27s =======================

$ uv run python -m evals sync --no-benchmark --regression
OPIK: Created a "decode-regression-v2" dataset at .../datasets/01a08eeb-8a6d-746d-a3fe-f14f6c2660cb/items.
OPIK: Created a "decode-regression-suite" test suite at .../test-suites/01a08eeb-8f41-74e0-97ef-ae91ce8db0f8/items.
evals sync: upserted 20 case(s) (1 skipped: 12-mcp-tool-usage) into decode-regression-v2 and decode-regression-suite.
# re-run: no "Created" line — both surfaces are get_or_create + content-deduped inserts.

$ uv run python -c "<opik SDK read-back>"
dataset items: 20 | project: decode-evals
sample item: {"case_id": "smoke-read-tool", "difficulty": "easy", "source_trace_id": null,
              "symptom": "harness invariant: asking what a file says drives the read tool, never a `bash cat` shell-out.",
              "tags": ["read-discipline", "reference"]}
suite items: 20 | suite project: decode-evals
sample suite item: {"data": {"difficulty": "easy", "case_id": "smoke-read-tool",
                    "prompt": "What does the file notes.txt say? Read it and tell me."},
                    "assertions": ["The response states what the file says, quoting its actual content rather than guessing or asking the user to paste it."]}
global assertions: ['The response directly addresses what the user asked in the prompt.', "The response does not invent files, ..."]

$ uv run python -m evals regression --case nope
Error: no runnable regression case matched (case='nope', difficulty=None); 21 case(s) available.   # exit 1

$ uv run python -c "<invoke regression/suite/sync --help>"
opik imported: False     # the keyless --help rule holds
```

**Notes**
- **Suite filtering.** `opik.run_tests` (2.2.36) takes NO item filter — it runs every item of the suite it is handed. So a filtered `suite` run registers and runs its own sliced suite (`decode-regression-suite-hard`), mirroring `decode-regression-gate-hard`; the unfiltered run uses the canonical `decode-regression-suite` that `sync` writes. Alternatives rejected: a no-op task fn still pays for server-side judging, and `suite.clear()` would make the shared suite's contents depend on the last filtered run.
- **`mean_<metric>` prefix.** Verified in the SDK that `log_experiment_scores` writes to the experiment's own `experiment_scores` bag (separate from `feedback_scores`, which the baseline compare still reads by raw metric name). The prefix is for UI readability — one label never carries two different numbers. `scoring_failed` scores are excluded from both `mean_per_metric` and `scores_by_tier` (a crashed metric would otherwise read as a behavior regression).
- **Sync registers only runnable cases** (20 of 21): the skip-guarded `12-mcp-tool-usage` stays discoverable in the registry but never lands in either Opik surface — a suite item for a case the agent cannot perform would just fail its assertion. This is a deliberate refinement of the AC's "one loop" (the loop is one; the membership is what runs).
- NOT RUN: the paid gate (`make eval-regression`) and a real `python -m evals regression` / `suite` — they cost money and were out of scope for this hand-off. The `evaluate` / `run_tests` / `get_or_create_test_suite` kwargs were checked against the installed opik 2.2.36 signatures instead (all present).
- Review pass (self + reviewer) added four things after the first green run: the case contract spells
  its `Difficulty` literal out instead of importing it from `evals.harness.task_loader` (importing that
  submodule runs `evals/harness/__init__.py`, which pulls the whole Opik harness — the ADR-0017 §1
  "contract imports no Opik" claim was false for one commit; a subprocess test now pins it, and another
  pins the two tier vocabularies to each other); the `SUITE_PASS_BAR` comment now derives 0.8 over the
  twenty-case scope instead of the retired five-item subset; `sync` names the skipped case in its echo;
  and the ritual's per-tier test asserts the report is non-empty (an id mismatch between dataset and
  registry would otherwise print an empty table forever).
- Follow-up for task 166 (not touched here): `docs/adr/0017`, `docs/glossary.md`, and `running_the_code/05_evals.md` still say "probe" / `decode-regression-v1` / "opik 1.9.8".

### [Tester] 2026-09-11 09:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check` — 332 files formatted; `make lint-check` — all checks passed)
- Unit tests: 2720 passed / 0 failed / 1 skipped (pre-existing `fastapi` skip) — `make unit-tests`, 64.8s
- Integration tests: 114 passed / 0 failed — `uv run pytest tests/integration -q`, 415.9s
- Warnings: 0 (no warnings summary in either run; `pyproject` `filterwarnings=["error"]` would have failed the run otherwise)
- `code-review` plugin: enabled in `.claude/settings.json`, but this Tester role's toolset (Read/Edit/Write/Bash/advisor) has no interface to invoke a Claude Code marketplace plugin directly — NOT invoked. Manual review substituted.
- `git status --short src/`: empty (no `src/` changes, as claimed)

**E2E adversarial pass**
- Happy path: `uv run python -m evals sync --no-benchmark --regression` → dataset `decode-regression-v2` (20 items) + suite `decode-regression-suite` (20 items) created under `decode-evals`, 12-mcp-tool-usage skipped and named in the echo (per SWE evidence, re-verified via the unit-test call-arg assertions rather than re-running the paid sync) — PASS
- Break path 1 (malformed case module — `difficulty="ultra"` / blank `assertion` / missing `symptom`): built three throwaway case modules under a scratch package and called `load_cases()` on each.
  - `difficulty="ultra"` → `ValueError: RegressionCase 'bad-difficulty': difficulty must be one of easy, medium, hard, got 'ultra'` (leaked, unwrapped)
  - blank `assertion` → `ValueError: RegressionCase 'blank-assertion': assertion must not be blank` (leaked, unwrapped)
  - missing `symptom` (omitted kwarg) → `TypeError: RegressionCase.__init__() missing 1 required positional argument: 'symptom'` (leaked, unwrapped)
  Expected (per AC line "Unit tests: loader ... rejects blank assertion/missing difficulty" and the loader's own `RegressionCaseError` contract): a `RegressionCaseError` naming the offending module path, the same contract `_cases_from_object` already gives a missing-`CASE` or wrong-type module.
  Actual: `_cases_from_module` in `evals/regression/loader.py` calls `importlib.import_module(module_name)` with no try/except, so a case that fails its own `__post_init__` validation (bad `difficulty` / blank `symptom` or `assertion`) or a constructor `TypeError` propagates as a bare, unwrapped exception with NO module path — only the case's own `id` (when the dataclass validation ran at all). — **FAIL**
- Break path 2 (state edge — contradictory filters): `uv run python -m evals regression --case smoke-read-tool --difficulty hard` → `Error: no runnable regression case matched (case='smoke-read-tool', difficulty='hard'); 21 case(s) available.`, exit 1, one friendly line, no traceback — PASS
- Break path 3 (malformed input — bad `--difficulty` choice value): `uv run python -m evals regression --difficulty ultra` → `Error: Invalid value for '--difficulty': 'ultra' is not one of 'easy', 'medium', 'hard'.`, exit 2, no traceback (click's own `Choice` validation) — PASS
- Break path 4 (opik-free `--help`): ran `python -m evals regression --help`, `suite --help`, `sync --help` each in a fresh subprocess and checked `"opik" in sys.modules` afterward → `False` in all three cases — PASS

**Acceptance criteria**
- [x] PASS — `case.py`/`RegressionCase` with required `difficulty`/`symptom`/`assertion`, optional provenance, `CASE`/`CASES`, 21 modules migrated and tiered 5/8/8 with `smoke_read_tool.py` as the template — `tests/unit/evals/regression/test_case.py` (28 tests, all pass); manually confirmed via `grep` that easy = {smoke-read-tool, 01-read-vs-cat, 02-grep-vs-bash, 03-edit-precision, 11-step-efficiency}, medium = the 8 listed (12-mcp-tool-usage still discoverable, `skip_reason` set), hard = the 8 listed — exact match to the task's tier table
- [x] PASS — `python -m evals regression [--case] [--difficulty]` (no `--probe`); `sync --regression` upserts both surfaces from one loop with the exact payload shapes; `SUITE_PROBE_IDS`/`ITEM_ASSERTIONS`/`suite_api_available` deleted; `SUITE_PASS_BAR` stays 0.8 — `tests/unit/evals/harness/test_datasets.py::test_sync_regression_cases_writes_the_dataset_and_the_test_suite` (asserts exact `get_or_create_dataset`/`get_or_create_test_suite` kwargs and `insert` payloads); `grep -rn "SUITE_PROBE_IDS\|ITEM_ASSERTIONS\|suite_api_available" evals/ tests/` → no hits; `evals/harness/test_suite.py:SUITE_PASS_BAR = 0.8`
- [x] PASS — `run_regression(*, case_id, difficulty, experiment_name, client)` with `experiment_scoring_functions=[mean_per_metric]`, `experiment_config` carrying `git_sha`/`case_count`/`difficulty`, name `decode-regression-gate[-<tier>]` — `tests/unit/evals/harness/test_regression.py::test_run_regression_wires_evaluate` and `::test_a_tier_run_gets_its_own_experiment_name_and_config` (28/28 pass); manually confirmed `dataset_item_ids` scoping and `experiment_name == "decode-regression-gate-hard"` for `--difficulty hard` in the read test
- [x] PASS — `scores_by_tier`/`format_tier_scores` pure + offline; `test_thresholds.py` prints per-tier via `_warn` and gates globally (0.8/0.7 unchanged, `THRESHOLDS`/`DEFAULT_THRESHOLD` untouched); `conftest.py::pytest_addoption("--difficulty")`; `make -n eval-regression ARGS='--difficulty easy'` forwards `--difficulty easy` to both `evals sync` and `pytest evals/regression/test_thresholds.py` — evidence: `tests/unit/evals/regression/test_thresholds.py::test_scores_by_tier_ignores_a_metric_that_failed_to_score` (excludes `scoring_failed`); `make -n` dry-run output captured below; `test_regression_meets_absolute_thresholds` reads `THRESHOLDS`/gates once, `test_per_tier_scores_are_reported` only `_warn`s
- [x] PASS (round 2) — Unit tests: loader accepts the new fields, **rejects** blank `assertion`/missing `difficulty` — `evals/regression/loader.py::_cases_from_module` now wraps only the `importlib.import_module` call and raises `RegressionCaseError(f"{path}: case module failed to construct: {exc}") from exc`; `_cases_from_object` stays outside the try (verified no double-wrap on a no-`CASE` module). Evidence: `tests/unit/evals/regression/test_loader.py::test_a_case_module_that_fails_to_construct_names_the_module_path[bad_tier|blank_assertion|missing_difficulty]` (3 passed); independently reproduced live — blank-symptom module → `RegressionCaseError` naming the module path with `__cause__` set to the original `ValueError`; a no-`CASE` module → single structural message `"... case module defines neither CASE nor CASES"` with `__cause__` is `None` (not double-wrapped); real registry still loads 21 cases.
- [x] PASS — remaining unit-test AC items (tier counts 5/8/8 + 12 discoverable, `--difficulty easy` selects exactly the five, sync writes both surfaces, `scores_by_tier` on a hand-built matrix) — `tests/unit/evals/regression/test_loader.py::test_the_registry_is_21_cases_tiered_five_eight_eight`, `::test_difficulty_easy_selects_exactly_the_five_easy_cases`, `::test_the_skip_guarded_case_is_still_discoverable_but_never_runnable`; `tests/unit/evals/regression/test_thresholds.py::test_scores_by_tier_averages_each_metric_within_its_tier`
- [x] PASS — `evals/regression/README.md` updated: case contract table (incl. new fields), tier table (5/8/8 with the exact id lists), `--difficulty`, mined-case provenance fields and placement (`cases/mined_<slug>.py`, ids `21-…`, tag `mined`) — read in full, matches the spec

**Evidence**
```
$ make unit-tests
======================= 2720 passed, 1 skipped in 64.84s =======================

$ uv run pytest tests/integration -q
114 passed in 415.94s (0:06:55)

$ make -n eval-regression ARGS='--difficulty easy'
if uv run python -m evals.harness.keys; then \
        uv run python -m evals sync --no-benchmark --regression --difficulty easy && uv run pytest evals/regression/test_thresholds.py --difficulty easy; \
fi

$ uv run python -m evals regression --case smoke-read-tool --difficulty hard
Error: no runnable regression case matched (case='smoke-read-tool', difficulty='hard'); 21 case(s) available.

$ uv run python -m evals regression --difficulty ultra
Error: Invalid value for '--difficulty': 'ultra' is not one of 'easy', 'medium', 'hard'.

# scratch case module, difficulty="ultra":
$ uv run python -c "from evals.regression.loader import load_cases; load_cases(<scratch_dir>, package='badcases')"
ValueError: RegressionCase 'bad-difficulty': difficulty must be one of easy, medium, hard, got 'ultra'
# (RegressionCaseError expected; ValueError leaked with no module path)
```

**Other issues found**
- `running_the_code/05_evals.md` line ~49 still documents `python -m evals regression --probe smoke-read-tool` (the retired flag — confirmed it now errors: `Error: No such option '--probe'`) and line ~53 still claims `opik==1.9.8` gates `python -m evals suite`, which is stale (the version gate is deleted in this task). Grepped `opik 1.9.8`/`SUITE_PROBE_IDS`/`ITEM_ASSERTIONS`/`suite_api_available`/`RegressionProbe`/`--probe` across `evals/`, `tests/`, `running_the_code/` — this is the only hit. NOT a blocker for 162: `tasks/166-evals-v2-docs-runbooks.md` exists, is `pending`, and explicitly owns `running_the_code/05_evals.md` rewrite + the `--probe`/`opik 1.9.8` grep as its own AC. The SWE's log already flags this as deferred to 166. No action needed here.
- Paid/keyed paths NOT run: `make eval-regression` (the real threshold gate) and a real `python -m evals regression --case smoke-read-tool` against Gemini — both cost money. Skipping per the task's own "optional, if cheap" allowance; the SWE's report already includes real-sync evidence (20 dataset + 20 suite items under `decode-evals`) and the mocked `evaluate()`/`run_tests()`/`get_or_create_test_suite()` call-arg tests pin the exact kwargs against the installed opik 2.2.36 signatures.

**VERDICT: FAIL**

One blocking issue: the loader (`evals/regression/loader.py::_cases_from_module`) does not wrap a case-construction failure (bad `difficulty`, blank `symptom`/`assertion`, missing required field) in `RegressionCaseError` — it leaks a bare `ValueError`/`TypeError` with no module path, and no test in `test_loader.py` exercises this path. This is the literal subject of the AC line "Unit tests: loader accepts the new fields, rejects blank `assertion`/missing `difficulty`" (loader-level rejection), not just the dataclass-level rejection already tested in `test_case.py`. Fix is small and contained (see AC section above) — everything else in the task (tier assignment, CLI wiring, both-surfaces sync, `run_regression`/`mean_per_metric`, per-tier report + global gate, README, opik-free `--help`, `Makefile` `ARGS` forwarding, full unit + integration suites) verified green.

### [SWE] 2026-09-11 10:40 — Fixes (Tester round 1)

**Files modified**
- `evals/regression/loader.py` — `_cases_from_module` now wraps ONLY the `importlib.import_module`
  call: any exception raised while a case module constructs its `CASE` (bad `difficulty`, blank
  `symptom`/`assertion`, missing required field — and a genuinely missing dependency's `ImportError`
  on the same path) becomes `RegressionCaseError(f"{path}: case module failed to construct: {exc}")
  `from exc`. The extraction step stays outside the `try` so `_cases_from_object`'s own
  `RegressionCaseError` is never re-wrapped into a doubled message. `load_cases`' docstring contract
  enumeration updated (it listed only the two structural failures).
- `tests/unit/evals/regression/test_loader.py` — regression test
  `test_a_case_module_that_fails_to_construct_names_the_module_path`, parametrized over the three
  shapes the Tester hit (`bad_tier`, `blank_assertion`, `missing_difficulty`): writes a real importable
  temp package (`__init__.py` + one case module building `CASE` at import time, as every shipped case
  module does), `monkeypatch.syspath_prepend`s it, and asserts `RegressionCaseError` whose message
  contains the module path. Asserted as substrings, not `match=`, so a tmp path with regex-special
  characters can never flake.

**Tests**
- Unit: 2723 passing, 0 failing, 1 skipped (pre-existing `fastapi` skip) — `make unit-tests`, 54.9s
  (was 2720 + the 3 new parametrizations)
- `uv run pytest tests/unit/evals/regression -q` → 146 passed
- Integration: N/A — no `src/` change (this fix touches `evals/` only)

**Acceptance criteria**
- [x] Unit tests: loader accepts the new fields, **rejects** blank `assertion`/missing `difficulty` —
  the previously-FAILing line. Loader-level rejection now verified by
  `tests/unit/evals/regression/test_loader.py::test_a_case_module_that_fails_to_construct_names_the_module_path[bad_tier|blank_assertion|missing_difficulty]`;
  the dataclass-level rejection stays in `test_case.py`. The REST of that same AC line was already PASS
  in the Tester's round-1 breakdown and is untouched here — tier counts 5/8/8 + 12 still discoverable
  (`test_loader.py::test_the_registry_is_21_cases_tiered_five_eight_eight`,
  `::test_the_skip_guarded_case_is_still_discoverable_but_never_runnable`), `--difficulty easy` selects
  exactly the five (`::test_difficulty_easy_selects_exactly_the_five_easy_cases`), sync writes both
  surfaces (`harness/test_datasets.py::test_sync_regression_cases_writes_the_dataset_and_the_test_suite`),
  `scores_by_tier` on a hand-built matrix
  (`regression/test_thresholds.py::test_scores_by_tier_averages_each_metric_within_its_tier`) — so the
  whole line is covered, not just the clause that failed. Every other criterion unchanged and still green.

**Evidence**
```
# RED — the three new tests before the loader change (right reason: the bare exception leaks):
$ uv run pytest tests/unit/evals/regression/test_loader.py -q
E   TypeError: RegressionCase.__init__() missing 1 required positional argument: 'difficulty'
FAILED ...::test_a_case_module_that_fails_to_construct_names_the_module_path[bad_tier]
FAILED ...::test_a_case_module_that_fails_to_construct_names_the_module_path[blank_assertion]
FAILED ...::test_a_case_module_that_fails_to_construct_names_the_module_path[missing_difficulty]
3 failed, 14 passed in 1.44s

# GREEN — after the wrapper:
$ uv run pytest tests/unit/evals/regression -q
146 passed in 3.62s

$ make format-check && make lint-check
332 files already formatted
All checks passed!

$ make pre-commit
2723 passed, 1 skipped in 54.17s

$ make unit-tests
2723 passed, 1 skipped in 54.95s

# the three new tests also pass ALONE, independent of the file's other 14 (no pytest-randomly / xdist
# installed, so this settles order-independence):
$ uv run pytest "tests/unit/evals/regression/test_loader.py::test_a_case_module_that_fails_to_construct_names_the_module_path" -q
3 passed in 0.06s

# E2E — the Tester's Break path 1, re-run against a scratch case package ($S = a scratch dir):
$ mkdir -p $S/badcases && : > $S/badcases/__init__.py
$ cat > $S/badcases/ultra_tier.py <<'CASEMOD'
from pathlib import Path
from evals.regression.case import RegressionCase
def _fixture(_workspace: Path) -> None: ...
CASE = RegressionCase(id="bad-difficulty", prompt="p", fixture=_fixture, metrics=[object()],
                      difficulty="ultra", symptom="harness invariant: it behaves.",
                      assertion="The response behaves.")
CASEMOD
$ PYTHONPATH=$S uv run python -c "
from pathlib import Path
from evals.regression.loader import RegressionCaseError, load_cases
try:
    load_cases(Path('$S/badcases'), package='badcases')
except RegressionCaseError as exc:
    print(f'{type(exc).__name__}: {exc}')
print('real registry still loads:', len(load_cases()), 'cases')
"
RegressionCaseError: $S/badcases/ultra_tier.py: case module failed to construct:
  RegressionCase 'bad-difficulty': difficulty must be one of easy, medium, hard, got 'ultra'
real registry still loads: 21 cases

# the pre-existing structural message is NOT doubled by the new wrapper — swap the bad module for one
# that simply has no CASE, then re-run the SAME `load_cases()` snippet as above:
$ rm $S/badcases/ultra_tier.py && echo 'X = 1' > $S/badcases/no_case.py
$ PYTHONPATH=$S uv run python -c "<the identical load_cases() snippet>"
RegressionCaseError: $S/badcases/no_case.py: case module defines neither CASE nor CASES

$ uv run python -m evals regression --case nope
Error: no runnable regression case matched (case='nope', difficulty=None); 21 case(s) available.   # exit 1
```

**Notes**
- One broad `except Exception` on the import, per the Tester's "one wrapper is fine". Grepped
  `ImportError|ModuleNotFoundError` across `evals/` and `tests/unit/evals/` first — nothing catches an
  `ImportError` around `load_cases` to skip on an optional dep, so collapsing it into the wrapper
  breaks no skip path. `from exc` keeps the original traceback (and satisfies ruff `B904`).
- `case.py`'s `__post_init__` untouched — the validation was already right; only its reporting path was.
- `running_the_code/05_evals.md` (`--probe` / "opik 1.9.8") still stale, still owned by task 166 — not
  touched here, per the Tester's own non-blocking note.
- NOT RUN (unchanged from round 1): the paid gate `make eval-regression` and a real
  `python -m evals regression` / `suite` against a provider.
- NOT COMMITTED — awaiting Tester re-review.

### [Tester] 2026-09-11 11:15 — QA (round 2)

**Test summary**
- Format / lint / pre-commit: PASS — `make pre-commit` → `2723 passed, 1 skipped in 54.07s`
- Unit tests: 2723 passed / 0 failed / 1 skipped (pre-existing `fastapi` skip)
- Integration tests: N/A this round — `grep -rl "evals" tests/integration/` returns nothing (no
  integration test touches `evals/`), so there is no integration surface for this fix to affect;
  round 1 already ran the full integration suite (114 passed) before the fix
- Warnings: 0

**E2E adversarial pass**
- Happy path: reproduced the SWE's fix directly — built a scratch case module with `symptom="   "`
  (blank) under a temp importable package and called
  `load_cases(Path(".../badpkg"), package="badpkg")` →
  `RegressionCaseError: .../badpkg/bad_case.py: case module failed to construct: RegressionCase
  'bad-case': symptom must not be blank`, with `exc.__cause__` = the original `ValueError` — PASS
- Break path 1 (regression of round-1 finding — bad `difficulty`/blank `symptom`/missing field):
  ran `pytest tests/unit/evals/regression/test_loader.py -k test_a_case_module_that_fails_to_construct`
  → 3 passed (`bad_tier`, `blank_assertion`, `missing_difficulty`); each asserts the module path
  appears in the message and `"failed to construct"` is present — PASS
- Break path 2 (no-`CASE`/no-`CASES` module — must NOT be double-wrapped): wrote a module with only
  `X = 1` (no `CASE`/`CASES`) to a fresh temp package and called `load_cases()` on it → single
  `RegressionCaseError: .../no_case.py: case module defines neither CASE nor CASES`,
  `exc.__cause__ is None` — confirms `_cases_from_object`'s own error is not re-wrapped by the new
  `try/except` around `import_module` — PASS
- Break path 3 (real registry unaffected by the fix): `load_cases()` on the shipped
  `evals/regression/cases/` → 21 cases, ids match the full expected set (incl.
  `12-mcp-tool-usage` still discoverable) — PASS

**Acceptance criteria**
- [x] PASS — Unit tests: loader accepts the new fields, rejects blank `assertion`/missing
      `difficulty` (loader-level, the round-1 blocking clause) — evidence above; also
      `tests/unit/evals/regression/test_loader.py` full file: 17 passed (146 for the whole
      `tests/unit/evals/regression` package)
- [x] PASS — no double-wrap: a structurally-bad module (no `CASE`/`CASES`) still raises the single
      pre-existing message with `__cause__ is None` — verified live (Break path 2)
- [x] PASS — real registry still loads all 21 cases — verified live (Break path 3)
- [x] PASS — `pytest tests/unit/evals/regression` → 146 passed
- [x] PASS — `make pre-commit` → 2723 passed, 1 skipped, 0 warnings
- [x] PASS — scope: `git status --short src/` empty. There is no round-1 commit to diff against
      (nothing in this task is committed yet — HEAD `e0190a2` predates the whole task), so scope is
      verified by reading, not by a filtered diff: the full `evals/regression/loader.py` diff (read
      end-to-end against HEAD) contains exactly the described `try/except` around
      `importlib.import_module` plus the matching docstring updates, nothing else; the SWE's round-2
      log names only `loader.py` + `test_loader.py` as touched this round; and the unit count moved
      2720 → 2723, exactly the three new parametrized cases, consistent with no other behavioral edit

**Evidence**
```
$ uv run pytest tests/unit/evals/regression -q
146 passed in 4.30s

$ make pre-commit
2723 passed, 1 skipped in 54.07s

$ git diff --stat evals/regression/loader.py tests/unit/evals/regression/test_loader.py tasks/162-regression-case-v2.md
 evals/regression/loader.py                 | 163 ++++++++++++------
 tasks/162-regression-case-v2.md            | 267 ++++++++++++++++++++++++++++-
 tests/unit/evals/regression/test_loader.py | 220 +++++++++++++++++++-----
 3 files changed, 546 insertions(+), 104 deletions(-)

$ uv run python -c "
from pathlib import Path
from evals.regression.loader import load_cases, RegressionCaseError
try:
    load_cases(Path('.../badpkg'), package='badpkg')
except RegressionCaseError as e:
    print('MESSAGE:', e)
    print('CAUSE:', repr(e.__cause__))
"
MESSAGE: .../badpkg/bad_case.py: case module failed to construct: RegressionCase 'bad-case': symptom must not be blank
CAUSE: ValueError("RegressionCase 'bad-case': symptom must not be blank")
```

**Other issues found**
- Round-1 "Other issues found" (stale `--probe`/`opik 1.9.8` references in
  `running_the_code/05_evals.md`) is unchanged, already deferred to task 166 — not a blocker here.
- `evals/regression/conftest.py` shows as untracked (`??`) in `git status`. This is expected, not a
  stray file: AC line 25 explicitly calls for
  `evals/regression/conftest.py::pytest_addoption("--difficulty")`, and it is new in this task (no
  prior version to modify), so git reports it as untracked rather than modified. Noting it here so
  the orchestrator doesn't misread it at commit time — it must be `git add`ed alongside the tracked
  changes.
- PASS-with-note (not a blocker, follow-up worth a line to the SWE): the new regression test
  (`test_a_case_module_that_fails_to_construct_names_the_module_path`) asserts the module path and
  `"failed to construct"` as substrings but never asserts `exc.__cause__` is set — a future edit that
  drops `from exc` (losing the chained traceback) would still pass the suite. Also, the no-double-wrap
  property (a no-`CASE` module raising exactly once, not wrapped twice) has zero test coverage:
  `test_extract_rejects_a_module_with_neither_case_nor_cases` calls `_cases_from_object` directly,
  bypassing `_cases_from_module` — it would still pass even if someone moved that call inside the
  `try`. Both are cheap to close (one more `load_cases()` call on a no-`CASE` temp module, asserting
  `__cause__ is None`, would pin both). Verified manually live in this review (see Happy path /
  Break path 2 above) — not a blocker for this PASS, but worth a follow-up test.

**VERDICT: PASS**

The round-1 blocking issue is fixed exactly as scoped: `_cases_from_module` wraps only the
`importlib.import_module` call, `_cases_from_object`'s own `RegressionCaseError` is not re-wrapped,
the module path and chained `__cause__` are both present, and the fix is covered by a
red-then-green parametrized regression test. Full suite green (2723 passed / 1 skipped / 0 warnings),
scope confined to the loader + its test + the task file (`git status --short src/` empty). Ready to
commit.
