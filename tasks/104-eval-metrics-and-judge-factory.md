---
id: 104
feature: evals
status: pending
---

# Custom Opik metrics + G-Eval judge factory

Depends on: 103. Implements ADR-0017 §7 (and the metric surface §4,5 consume).

## Scope

**`evals/harness/metrics.py`** — code metrics subclassing
`opik.evaluation.metrics.base_metric.BaseMetric`, each returning a `ScoreResult` (score in [0,1] +
`reason`):

- `ToolCalledMetric(tool_name)` / `ToolNotCalledMetric(tool_name)` — score 1.0 when the named tool
  appears / is absent in the task-fn output's `tool_calls` list.
- `VerifyOracleMetric` — maps the runner's recorded verify result (`exit_code`, stdout) to 1.0/0.0
  (PASS = exit 0; the metric does NOT run anything — the task fn did, ADR-0017 §5).
- `MaxStepsMetric` — 1.0 when `steps <= max_steps` from the item; carries `steps` in the reason.
- `DiffLinesMetric(max_lines)` — 1.0 when the recorded diff's changed-line count ≤ threshold.
- Built-ins (`Equals`, `Contains`, `IsJson`, `RegexMatch`) are used directly where they fit — do
  not wrap them.

**`evals/harness/judges.py`** — the judge factory:

- `make_judge(task_introduction, evaluation_criteria) -> GEval` using
  `GEval(task_introduction=..., evaluation_criteria=..., model=<LiteLLM string>)`.
- `judge_model()` resolves the LiteLLM string: explicit `settings.eval_judge_model` wins; else
  derive from `settings.llm_provider` — `gemini` → `gemini/gemini-2.5-flash` (the default),
  `openrouter` → `openrouter/<settings.openrouter_model>`, `modal` →
  `openai/<settings.modal_endpoint_model>` with `base_url={settings.modal_endpoint_url}/v1`
  (LiteLLM OpenAI-compatible route). Pure function, unit-testable without network.

**Tests** (`tests/unit/evals/harness/`): every metric scored against crafted inputs (both
outcomes + malformed/missing fields → graceful 0.0 with a reason, never a raise);
`judge_model()` routing for all three providers + explicit override. `GEval` construction is
smoke-tested (no LLM call).

## Acceptance Criteria

- [ ] All five custom metrics return correct `ScoreResult`s and never raise on missing keys.
- [ ] `judge_model()` covers explicit-override + all three provider derivations, unit-tested.
- [ ] `make_judge` builds a `GEval` carrying the resolved model string.
- [ ] `make ci` green (all tests offline).

## Out of scope

- pass@k aggregation functions (107). Wiring metrics into `evaluate()` (106, 111).

## Log
