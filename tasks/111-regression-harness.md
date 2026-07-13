---
id: 111
feature: evals
status: pending
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

- [ ] A fixture probe runs end-to-end host-native under a scripted model and scores through its
      metrics (offline unit test).
- [ ] Gate modes / resolvers / message-history pre-fill all reachable from a probe declaration.
- [ ] `python -m evals sync --regression` upserts probe items (mock-verified).
- [ ] Probe contract documented in `evals/regression/README.md`.
- [ ] `make ci` green.

## Out of scope

- The 20 real probes (112–114). Threshold gate (115). Test Suites (116).

## Log
