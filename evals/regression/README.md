# Regression probes — behavior, not just outcome

A **benchmark task** asks *"did the agent get the job done?"* (ADR-0017 §2, `evals/benchmark/`). A
**regression probe** asks *"did it work the way we designed?"* — the right tool, the gate respected,
a minimal number of steps, compaction survived (the ADR-0002..0013 behaviors). Probes run
**host-native** (`sandbox_mode = none`, on a fresh temp dir) — fast enough to be a per-feature-branch
ritual, no docker required (ADR-0017 §3,6).

## The probe contract

One probe is a `RegressionProbe` (`evals/regression/probe.py`) — pure data, no control flow:

| Field | Meaning |
|---|---|
| `id` | Stable probe key; also the Opik dataset item id. Non-blank, unique across the suite. |
| `prompt` | What the agent is asked. |
| `fixture` | `Callable[[Path], None]` — seeds the fresh temp Workspace (files, `AGENTS.md`, `.decode/settings.json`). |
| `metrics` | The Opik metric instances that grade the run (from `evals/harness/metrics.py`, Opik built-ins, or G-Eval judges). At least one. |
| `gate_mode` | `PermissionMode` — defaults to `BYPASS`. |
| `permission_rules` | Optional `RuleSet` threaded into the gate. |
| `resolve_permission` | Optional async approval resolver (default = headless auto-deny). |
| `resolve_user_question` | Optional async `ask_user` resolver (default = headless auto-deny). |
| `message_history` | Optional `Callable[[], list[ModelMessage]]` — a pre-filled conversation (the compaction probe's near-limit history). |
| `context` | Optional `Callable[[Path], ContextManager]` — a live resource entered **around** the run (e.g. the `http.server` web-fetch fixture). |
| `max_requests` | Optional model-request cap so a runaway run stops gracefully. |
| `tags` | Slice labels carried onto the Opik dataset item. |

Everything the eval driver (`evals/harness/driver.py`) can vary per run is reachable straight from the
declaration — gate mode, resolvers, and pre-filled history included.

## Registering a probe

Drop a module under `evals/regression/cases/` exposing a module-level `PROBE` (or `PROBES` for a
list). `evals/regression/loader.py::load_probes()` auto-discovers every `cases/*.py` — no central list
to edit. See `cases/smoke_read_tool.py` for the reference template. The full behavior suite lands in
tasks 112–114.

```python
# evals/regression/cases/my_probe.py
from evals.harness.metrics import ToolCalledMetric
from evals.regression.probe import RegressionProbe
from evals.regression.fixtures import seed_type_error

PROBE = RegressionProbe(
    id="fix-type-error",
    prompt="There is a type error in buggy.py. Find and fix it.",
    fixture=seed_type_error,
    metrics=[ToolCalledMetric("read")],
    max_requests=8,
)
```

## Shared fixtures

`evals/regression/fixtures/` ships reusable seeds a probe's `fixture` composes:

- `seed_type_error(workspace)` — a tiny Python module with one deliberate type error.
- `seed_skills_dir(workspace)` — a `.decode/skills/<name>/SKILL.md` layout.
- `serve_page(body)` — a context manager running a stdlib `http.server` on localhost, yielding the base
  URL (use it as a probe's `context`).
- `near_limit_history(target_tokens=...)` — a pre-filled pydantic-ai conversation sized near a token
  budget (the compaction probe's `message_history`).

## Running

```bash
python -m evals sync --regression         # upsert probe items into decode-regression-v1
python -m evals regression                # run the whole suite
python -m evals regression --probe smoke-read-tool   # run one probe
```

Each run is one Opik experiment under `EVAL_PROJECT_NAME` (`decode-evals`). Every probe declares its
own metrics; `run_regression` wraps each in a `ProbeScopedMetric` so a single `evaluate()` call scores
every probe only on its own item — one experiment, clean per-probe scores. The threshold gate over
those scores lands in task 115. Regression runs cost real money and need keys — they are **never** part
of `make ci` (ADR-0017 §9).
