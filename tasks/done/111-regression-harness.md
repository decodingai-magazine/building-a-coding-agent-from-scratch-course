---
id: 111
feature: evals
status: done
---

# Regression harness: probe format, fixtures, none-mode execution, dataset sync

Depends on: 103, 104. Implements ADR-0017 §2,6.

## Scope

**Probe contract** — `evals/regression/cases/<NN>-<slug>.py` (or one module with a probe registry;
SWE picks, keep it flat and readable): each `RegressionProbe` declares `id`, `prompt`,
`fixture` (a builder fn receiving a temp dir → seeds files/AGENTS.md/settings.json), `gate_mode` +
optional resolvers (defaults BYPASS + deny), `metrics` (instances from 104 / Opik built-ins /
judges), optional `message_history` builder (compaction probe), `max_requests`.

**Execution** — `evals/harness/regression.py`: `regression_task_fn(item)` builds the fixture in a
fresh `tempfile` dir, calls `run_agent_once_sync(prompt, cwd=tmp, gate_mode=..., ...)` HOST-NATIVE
(`sandbox_mode` stays `none` — fast, no docker), returns
`{output, tool_calls, steps, file_state…}` for the metrics. `run_regression(...)` wires
`evaluate(dataset="decode-regression-v1", task=regression_task_fn, scoring_metrics=<per-item>,
experiment_config={model, git sha}, project_name=settings.eval_project_name)`; exposed as
`python -m evals regression [--probe <id>]`.

**Fixtures** — `evals/regression/fixtures/`: shared builders (tiny python file with a seeded type
error; a stdlib `http.server` fixture serving a known page for web-fetch probes; a skills dir; a
near-limit prefilled conversation builder reusing decode's message entities).

**Dataset sync** — extend `evals/harness/datasets.py` with
`get_or_create_dataset("decode-regression-v1")` + probe-item sync (id, tags), on
`python -m evals sync`.

**Tests**: probe registry validation; task fn with scripted model — fixture built, gate honored,
temp dir cleaned; per-metric binding; sync payloads (mocked client).

## Acceptance Criteria

- [x] A fixture probe runs end-to-end host-native under a scripted model and scores through its
      metrics (offline unit test).
- [x] Gate modes / resolvers / message-history pre-fill all reachable from a probe declaration.
- [x] `python -m evals sync --regression` upserts probe items (mock-verified).
- [x] Probe contract documented in `evals/regression/README.md`.
- [x] `make ci` green.

## Out of scope

- The 20 real probes (112–114). Threshold gate (115). Test Suites (116).

## Log

### [SWE] 2026-07-14 — Implementation

**Files modified**
- `evals/regression/probe.py` — `RegressionProbe` contract (id/prompt/fixture/metrics + gate mode /
  rules / resolvers / message-history / context / max_requests / tags); validates non-blank id·prompt
  and ≥1 metric.
- `evals/regression/loader.py` — auto-discovers `cases/*.py` probe modules (each exposes `PROBE`/`PROBES`),
  validates unique non-blank ids; `probe_by_id` friendly lookup.
- `evals/regression/cases/smoke_read_tool.py` — reference/template probe (read-tool discipline).
- `evals/regression/fixtures/{files,web,conversation}.py` — `seed_type_error`, `seed_skills_dir`,
  `serve_page` (stdlib http.server ctx mgr), `near_limit_history` (pydantic-ai messages, ADR-0006 sizing).
- `evals/harness/regression.py` — `regression_task_fn`/`run_probe` (host-native, forces `sandbox_mode=none`
  + resets bash seam, fresh tempfile dir, file-tree snapshot, never raises → agent_error/infra_error),
  `ProbeScopedMetric` (per-item metric binding via `[]`-skip), `run_regression` (one `evaluate` over
  `decode-regression-v1`, experiment_config model+provider+sha, project=eval_project_name, task_threads=1).
- `evals/harness/datasets.py` — `REGRESSION_DATASET_NAME` + `regression_dataset_item` + `sync_regression_dataset`.
- `evals/run.py` — `regression [--probe]` wired to `run_regression`; `sync --regression/--no-regression`
  (both tracks default on); kept module opik-free (lazy imports).
- `evals/regression/README.md` — probe-contract docs.
- `tests/unit/evals/regression/test_{probe,loader,fixtures}.py`,
  `tests/unit/evals/harness/test_regression.py`, extended `test_datasets.py` + `test_run.py`.

**Tests**
- Unit: 1728 passing, 0 failing (`make unit-tests`) — 34 new across the regression suite.
- Integration: 113 passing, 2 skipped (key-gated live smokes) — `make integration-tests`.
- `make ci` green: `uv lock --check` + format-check + lint-check + full test.

**Acceptance criteria**
- [x] Fixture probe runs end-to-end host-native under a scripted model, scores through its metrics —
      `tests/unit/evals/harness/test_regression.py::test_task_fn_runs_a_probe_and_returns_the_metric_payload`.
- [x] Gate modes / resolvers / message-history reachable from a probe declaration —
      `::test_every_probe_knob_is_forwarded_to_the_driver`, `::test_custom_resolver_and_rules_reach_the_run`,
      `::test_gate_is_honored_a_denied_mutation_never_hits_disk`.
- [x] `python -m evals sync --regression` upserts probe items (mock-verified) —
      `tests/unit/evals/test_run.py::test_sync_regression_upserts_probe_items` +
      `test_datasets.py::test_sync_regression_upserts_one_item_per_probe`.
- [x] Probe contract documented — `evals/regression/README.md`.
- [x] `make ci` green.

**Evidence**
```
$ make unit-tests
======================= 1728 passed in 97.97s (0:01:37) ========================
$ make integration-tests
================== 113 passed, 2 skipped in 313.43s (0:05:13) ==================
$ python -m evals regression --help   # opik/keys not needed for --help (lazy imports)
Options:
  --probe TEXT  Run only this regression probe id.
# host-native probe run under a scripted model, scored through its metrics:
smoke-read-tool -> tool_called_read 1.0 · tool_not_called_bash 1.0 · max_steps 1.0
```

**Notes**
- Per-item metric binding design decision (Opik `evaluate` applies ONE metric list to ALL items, but
  each probe has its OWN metrics): `ProbeScopedMetric` wraps each probe's metric and returns `[]`
  (verified in opik 1.9.8 `engine._scores_by_metrics` — a list result contributes zero scores) for any
  item whose `probe_id` differs, so a single `evaluate` returns ONE `EvaluationResult` (what the task-115
  threshold gate aggregates) with clean per-probe scores, no cross-probe noise. Not an ADR-level fork —
  ADR-0017 §6 already settles "plain datasets + code metrics via evaluate()".
- `run_probe` forces `sandbox_mode=none` + `reset_executor()` (restored in `finally`) so a probe run is
  byte-identical host-native regardless of ambient `SANDBOX_MODE`, mirroring `benchmark_sandbox`.
- Shipped one reference probe (`smoke_read_tool`) as the 112-114 template; the 20 real probes stay out
  of scope.
- No new deps (stdlib `http.server`/`tempfile`/`importlib`; opik + pydantic-ai already present) — `.env`
  surface unchanged.

### [Tester] 2026-07-14 09:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check`, `make lint-check`, `make pre-commit` all green)
- `make ci` (lockfile + format + lint + full suite): PASS — `1841 passed, 2 skipped in 412.21s`
- Unit tests: 1728 passed / 0 failed (`make unit-tests`)
- Integration tests: 113 passed / 2 skipped (key-gated live smokes), 0 failed
- Warnings: 0 (`filterwarnings=["error"]` — a warning would show as a failure; none did)

**E2E adversarial pass**
- Happy path: `python -m evals regression --probe smoke-read-tool` (CLI, mocked `opik.Opik`/`evaluate`,
  scripted `read_then_finish` model) → real `regression_task_fn` ran host-native, all 3 metrics scored
  1.0 (`tool_called_read`, `tool_not_called_bash`, `max_steps`), dataset `insert` called with
  `{"probe_id": "smoke-read-tool", "tags": ["read-discipline", "reference"]}` — matches SWE's evidence
  exactly (PASS)
- Break path 1 (probe contract, real case modules on disk): built a throwaway package with two
  `cases/*.py` modules declaring the SAME probe id → `load_probes()` raised
  `RegressionProbeError: duplicate regression probe id: 'dup'`; a module exposing neither `PROBE` nor
  `PROBES` raised `RegressionProbeError: ...case module defines neither PROBE nor PROBES`; a case module
  constructing a blank-id `RegressionProbe` raised `ValueError: RegressionProbe.id must not be blank` at
  import time (construction-time validation, not silently swallowed) (PASS)
- Break path 2 (never-raise / multi-item isolation): a `probe.context` context manager that raises on
  **enter** and one that raises on **exit** both got caught into `agent_error` (never propagated out of
  `run_probe`); simulated a 3-item batch (`broken` fixture-raise, `crash` scripted-model-raise, `ok`
  happy) through `make_regression_task_fn` in sequence — `broken` → `infra_error` set, `crash` →
  `agent_error` set, `ok` → both `None`, no cross-item contamination, no raise ever escaped (PASS)
- Break path 3 (seam hygiene under failure): set ambient `settings.sandbox_mode = "docker"`, ran a
  crashing scripted model through `run_probe` — `settings.sandbox_mode` restored to `"docker"` after the
  crash, host `os.getcwd()` unchanged, temp `decode-regression-*` dir removed (glob before/after showed
  no leak), run completed in ~46ms (no hang) (PASS)
- Break path 4 (`ProbeScopedMetric` isolation vs real opik 1.9.8): called the actual installed
  `opik.evaluation.engine.engine._scores_by_metrics` (not a mock) with two `ProbeScopedMetric`-wrapped
  metrics for two different probes — probe A's item scored only by A's metric (`len(results) == 1`),
  probe B's item scored only by B's metric; confirmed source at
  `.venv/.../opik/evaluation/engine/engine.py:386-389` — `if isinstance(result, list): score_results +=
  result` — an empty list contributes zero `ScoreResult`s, exactly as documented (PASS)
- Break path 5 (fixtures): `serve_page` raising `RuntimeError` mid-`with` — socket confirmed re-bindable
  immediately after (no port leak), subsequent fetch fails with `URLError` (server truly down); confirmed
  `near_limit_history(target_tokens=settings-derived micro-compaction trigger ≈629,145)` produces a
  history estimating to 629,308 tokens (0.03% over target) against the REAL
  `compaction_context_window_tokens` / `microcompaction_reserve_fraction` settings math — the builder is
  genuinely capable of landing near the real trigger when parameterized correctly (PASS, see note below
  on the function's own default)
- Break path 6 (CLI unknown probe): `python -m evals regression --probe totally-unknown-probe-id` →
  exit code 1, single-line `Error: no regression probe matched (probe='totally-unknown-probe-id'); 1
  probe(s) available.` — no traceback, no opik client ever constructed (the empty-selection check runs
  before `opik.Opik()`) (PASS)
- Break path 7 (keyless `--help`): `python -m evals --help`, `python -m evals regression --help`,
  `python -m evals sync --help` all ran with `OPIK_API_KEY`/`GEMINI_API_KEY`/`OPENROUTER_API_KEY`
  unset; `'opik' in sys.modules` is `False` after `import evals.run` (PASS)

**Acceptance criteria**
- [x] PASS — A fixture probe runs end-to-end host-native under a scripted model and scores through its
      metrics — `tests/unit/evals/harness/test_regression.py::test_task_fn_runs_a_probe_and_returns_the_metric_payload`
      + independently reproduced via the real CLI path (happy path above)
- [x] PASS — Gate modes / resolvers / message-history pre-fill all reachable from a probe declaration —
      `test_every_probe_knob_is_forwarded_to_the_driver`, `test_custom_resolver_and_rules_reach_the_run`,
      `test_gate_is_honored_a_denied_mutation_never_hits_disk` all pass; re-verified `DEFAULT` gate +
      custom `RuleSet` + custom async resolvers thread straight into `run_agent_once_sync` kwargs
- [x] PASS — `python -m evals sync --regression` upserts probe items (mock-verified) —
      `test_sync_regression_upserts_probe_items`, `test_sync_regression_upserts_one_item_per_probe`
      pass; independently reproduced through the CLI (`sync_regression_dataset` invoked with
      `{"probe_id": "smoke-read-tool", "tags": [...]}`
- [x] PASS — Probe contract documented in `evals/regression/README.md` — file present, documents every
      `RegressionProbe` field, registration flow, shared fixtures, and the running commands
- [x] PASS — `make ci` green — `uv lock --check` (2ms, in sync) + format-check + lint-check +
      `1841 passed, 2 skipped in 412.21s`, independently re-run, not just SWE's claim

**Evidence**
```
$ make unit-tests
======================= 1728 passed in 98.63s (0:01:38) ========================
$ make ci
================= 1841 passed, 2 skipped in 412.21s (0:06:52) ==================
$ python -m evals regression --probe totally-unknown-probe-id
Error: no regression probe matched (probe='totally-unknown-probe-id'); 1 probe(s) available.
(exit code 1)
```

**Other issues found (not blocking)**
- `evals/regression/loader.py::probe_by_id` is unused by production code — `run_regression` reimplements
  its own filter (`_select_probes` + `RegressionSelectionError`) rather than reusing `probe_by_id`. Two
  parallel "unknown probe id" error paths exist with slightly different messages. Not a bug (both are
  tested and both fail loudly), just a minor duplication worth collapsing when task 112-114 needs a
  single-probe lookup helper.
- `near_limit_history()`'s own default (`target_tokens=4000`) is nowhere close to the REAL compaction
  triggers on the default 1,048,576-token window (micro ≈629K, full ≈839K) — a probe author calling it
  with no arguments would NOT get a "near-limit" history despite the name. The function is correct and
  flexible (verified above it lands within 0.03% of a real trigger when given the right target), but the
  default is a footgun for whoever writes the actual compaction probe in tasks 112-114. Worth a one-line
  README callout ("pass `target_tokens` computed from `settings.compaction_context_window_tokens` — the
  4000 default is a test convenience only") — non-blocking for this task since no compaction probe ships
  yet.
- Could not invoke the `code-review` plugin directly (no Task/slash-command tool available in this
  session); compensated with a manual read of every changed/new file, an AST scan for missing return
  annotations (none found), and a `print()`/opik-import-boundary grep (both clean).

**VERDICT: PASS**
