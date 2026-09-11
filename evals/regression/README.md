# Regression Cases — behavior, not just outcome

A **Benchmark Task** asks *"did the agent get the job done?"* (ADR-0022 §2, `evals/benchmark/`). A
**Regression Case** asks *"did it work the way we designed?"* — the right tool, the gate respected, a
minimal number of steps, compaction survived (the ADR-0002..0013 behaviors). Cases run
**host-native** (`sandbox_mode = none`, on a fresh temp dir) — fast enough to be a per-feature-branch
ritual, no docker required (ADR-0017 §3,6).

Twenty-one cases ship, tiered 5 easy / 8 medium / 8 hard (ADR-0022 §8). Nothing was dropped in the v2
migration; what changed is that every case now carries its tier, its symptom and its assertion.

## The case contract

One case is a `RegressionCase` (`evals/regression/case.py`) — pure data, no control flow:

| Field | Meaning |
|---|---|
| `id` | Stable case key; also the Opik dataset item key (`case_id`). Non-blank, unique across the suite. |
| `prompt` | What the agent is asked. |
| `fixture` | `Callable[[Path], None]` — seeds the fresh temp Workspace (files, `AGENTS.md`, `.decode/settings.json`). |
| `metrics` | The Opik metric instances that grade the run (from `evals/harness/metrics.py`, Opik built-ins, or G-Eval judges). At least one. |
| **`difficulty`** | `easy` \| `medium` \| `hard` — the Difficulty Tier `--difficulty` slices on (required). |
| **`symptom`** | One line: the regression this case catches. An invented case phrases it `"harness invariant: <one line>"`; a mined one names the bad behavior its trace showed (required). |
| **`assertion`** | The same bar in English — it becomes the Test Suite item's assertion, judged on the agent's ANSWER. States the quality without naming the expected value, so a judge cannot cheat off it (required, non-blank). |
| `gate_mode` | `PermissionMode` — defaults to `BYPASS`. |
| `permission_rules` | Optional `RuleSet` threaded into the gate. |
| `resolve_permission` | Optional async approval resolver (default = headless auto-deny). |
| `resolve_user_question` | Optional async `ask_user` resolver (default = headless auto-deny). |
| `message_history` | Optional `Callable[[], list[ModelMessage]]` — a pre-filled conversation (the compaction case's near-limit history). |
| `context` | Optional `Callable[[Path], ContextManager]` — a live resource entered **around** the run (e.g. the `http.server` web-fetch fixture). |
| `settings_overrides` | Settings forced for the duration of one run (the compaction case shrinks the context window). |
| `enable_compaction` | Wire the auto-compaction cascade for this run. |
| `max_requests` | Optional model-request cap so a runaway run stops gracefully. |
| `tags` | Slice labels carried onto the Opik dataset item. |
| `skip_reason` | Declared but not yet runnable (case 12, MCP): stays in the registry, never runs, never registers. |
| **`source_trace_id` / `thread_id` / `fixed_in`** | Mined-case provenance — the Opik trace + thread the case came from and the commit that fixed it. `None` on an invented case. |

Everything the eval driver (`evals/harness/driver.py`) can vary per run is reachable straight from the
declaration — gate mode, resolvers, and pre-filled history included.

### Difficulty tiers

| Tier | What lives there | Cases |
|---|---|---|
| `easy` (5) | Single-tool discipline | smoke-read-tool, 01-read-vs-cat, 02-grep-vs-bash, 03-edit-precision, 11-step-efficiency |
| `medium` (8) | Planning, delegation, skills, memory, web, lsp | 05-web-fetch-discipline, 06-lsp-diagnostics, 07-plan-mode-discipline, 08-todo-planning, 09-subagent-delegation, 10-skill-dispatch, 12-mcp-tool-usage *(skipped)*, 15-memory-obedience |
| `hard` (8) | Compaction, the gate, destructive caution, judged answers, the json contract | 04-diff-minimality, 13-permission-deny-respect, 14-destructive-caution, 16-compaction-survival, 17-grounded-answer, 18-no-hallucinated-files, 19-template-compliance, 20-json-output-contract |

## Registering a case

Drop a module under `evals/regression/cases/` exposing a module-level `CASE` (or `CASES` for a list).
`evals/regression/loader.py::load_cases()` auto-discovers every `cases/*.py` — no central list to
edit. See `cases/smoke_read_tool.py` for the reference template.

```python
# evals/regression/cases/my_case.py
from evals.harness.metrics import ToolCalledMetric
from evals.regression.case import RegressionCase
from evals.regression.fixtures import seed_type_error

CASE = RegressionCase(
    id="fix-type-error",
    prompt="There is a type error in buggy.py. Find and fix it.",
    fixture=seed_type_error,
    difficulty="medium",
    symptom="harness invariant: a reported type error is located with the tools, then fixed.",
    assertion="The response reports the type error it fixed and where it was.",
    metrics=[ToolCalledMetric("read")],
    max_requests=8,
)
```

### Mined cases

A mined case is the same drop with provenance attached (ADR-0022 §8). It lives **beside** the
invented ones — `evals/regression/cases/mined_<slug>.py`, ids `21-…` onward, tag `mined` — and fills
`source_trace_id` (the LIVE Opik trace it came from), `thread_id` (the decode session) and, once a
fix lands, `fixed_in` (the commit sha). Its `symptom` names the behavior the trace actually showed
instead of the `harness invariant:` phrasing an invented case uses. Nothing is ever deleted to make
room for one.

## Shared fixtures

`evals/regression/fixtures/` ships reusable seeds a case's `fixture` composes:

- `seed_type_error(workspace)` — a tiny Python module with one deliberate type error.
- `seed_skills_dir(workspace)` — a `.decode/skills/<name>/SKILL.md` layout.
- `serve_page(body)` — a context manager running a stdlib `http.server` on localhost, yielding the base
  URL (use it as a case's `context`).
- `near_limit_history(target_tokens=...)` — a pre-filled pydantic-ai conversation sized near a token
  budget (the compaction case's `message_history`).

## Running

```bash
python -m evals sync --regression                     # upsert BOTH surfaces (dataset + Test Suite)
python -m evals regression                            # run the whole suite
python -m evals regression --case smoke-read-tool     # run one case
python -m evals regression --difficulty easy          # run one tier
make eval-regression                                  # sync + the pre-merge threshold gate
make eval-regression ARGS='--difficulty hard'         # …sliced to one tier (sync AND gate)
```

Each run is one Opik experiment under `EVAL_PROJECT_NAME` (`decode-evals`), named
`decode-regression-gate` — or `decode-regression-gate-<tier>` for a filtered one, so a tier's baseline
compares against that tier and never against the whole suite. Every case declares its own metrics;
`run_regression` wraps each in a `CaseScopedMetric` so a single `evaluate()` call scores every case
only on its own item, and `experiment_scoring_functions=[mean_per_metric]` writes one `mean_<metric>`
per metric onto the Experiment row. The threshold gate over those scores is
`evals/regression/test_thresholds.py`: it prints one table **per tier** and gates **globally** at the
unchanged floors (tool discipline ≥ 0.8, judges ≥ 0.7). Regression runs cost real money and need keys
— they are **never** part of `make ci` (ADR-0017 §9).

## Two regression surfaces — code metrics vs natural-language assertions

There are **two** regression surfaces on purpose, and the contrast between them is the teaching point
(ADR-0017 §6; ADR-0022 §8). One case declaration registers in both: `python -m evals sync --regression`
walks the cases ONCE and writes the dataset item the metrics grade AND the Test Suite item the
assertion is judged against.

| | Surface (a) — `python -m evals regression` | Surface (b) — `python -m evals suite` |
|---|---|---|
| Grader | Deterministic `BaseMetric` code (`evals/harness/metrics.py`) + G-Eval judges | An LLM judge checks the case's **natural-language assertion** |
| "Correct" is | A number over a threshold (`aggregate_evaluation_scores() >= thresholds`) | *"the response never invents a file that does not exist"* — an English quality bar |
| Opik surface | `decode-regression-v2` dataset → `evaluate()` → pytest threshold gate | `decode-regression-suite` Test Suite → `run_tests()` → `result.pass_rate` gate |
| Scope | Every runnable case (`--case` / `--difficulty` slice it) | The same cases and the same filters — a full run is twenty agent runs plus judging, so `--difficulty` is the cost knob here too |
| Gate | Per-metric thresholds, global | Suite `pass_rate` below `SUITE_PASS_BAR` (0.8) → non-zero exit |

Surface (b) reuses the **same** run-and-grade path: its task adapter (`evals/harness/test_suite.py`)
calls the identical `regression_task_fn`, then reshapes the result to the Test Suites `{"input",
"output"}` contract — keeping `input` to just the prompt the agent received so a leaked expected answer
can never let the judge cheat. Because the judge only reads the answer, an assertion states the bar in
terms the ANSWER shows; the mechanical half (which tool was called) is surface (a)'s job. Deterministic
metrics catch exact regressions cheaply; NL assertions catch "the answer got worse in a way no single
number captures". Neither replaces the other.

> `opik.run_tests` runs every item of the suite it is handed and takes no item filter, so
> `python -m evals suite --difficulty hard` registers and runs its own `decode-regression-suite-hard`
> rather than billing all twenty judged items.
