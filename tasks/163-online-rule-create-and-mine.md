---
id: 163-online-rule-create-and-mine
feature: evals-v2
status: pending
---

# Online track v2: `evals online-rule create` (idempotent LLM-as-judge rule) + `evals mine` (trace triage)

Tags: `evals`, `online`, `regression`, `opik`
Depends on: 156
Blocks: 164

## Scope
Two live-project commands (ADR-0022 §8, §13). `online-rule create` makes the `response_quality` rule the README today asks a human to click together — `decode-prod` has 0 rules and 68 traces, so `feedback_scores.response_quality` is empty until this exists. `mine` is the discovery half of regression mining: search the LIVE project, cluster by signature, print trace ids for a human to pick. Both keyless-skip; both `--help` opik-free.

## Acceptance criteria
- [ ] `evals/harness/online_rule.py` + `python -m evals online-rule create [--project NAME] [--model ID] [--sampling 1.0] [--dry-run]`: resolves the project id (`client.rest_client.projects.retrieve_project(name=…)` / `find_projects`), calls `client.rest_client.automation_rule_evaluators.find_evaluators(project_id=…, name="response_quality")`; if present prints `already exists: <id>` and exits 0; else `create_automation_rule_evaluator(request=AutomationRuleEvaluatorWrite_LlmAsJudge(project_id=…, name="response_quality", sampling_rate=<sampling>, enabled=True, action="evaluator", trigger_scope="production", code=LlmAsJudgeCodeWrite(model=LlmAsJudgeModelParametersWrite(name=<model>, temperature=0.0), messages=[LlmAsJudgeMessageWrite(role="USER", content=<prompt>)], variables={"input": "input", "output": "output"}, schema=[LlmAsJudgeOutputSchemaWrite(name="response_quality", type="INTEGER", description="0–10, qualitative")])))` — the exact write types under `opik/rest_api/types/`, verified at implementation against 2.2.36 (and context7 for the model-id form). `<prompt>` is the qualitative prompt from `evals/README.md` step 6 with `{{input}}`/`{{output}}` placeholders, stored as ONE module constant the README quotes (no drift). Default project = `settings.opik_project_name` (live), never `EVAL_PROJECT_NAME`.
- [ ] Default `--model` derives from `evals/harness/judges.py::judge_model()`: for the gemini route the Opik model id (`gemini-2.5-flash`, provider prefix stripped); for openrouter/modal routes the command refuses with one line asking for an explicit `--model` (Opik's server-side judge needs a provider the workspace has configured under AI Providers — say so). Unit-tested with a fake rest client (create called once; second call finds it; `--dry-run` prints the payload and calls nothing).
- [ ] `evals/harness/mine.py` + `python -m evals mine [--preset errors|low-quality|long|denied|all] [--since ISO] [--limit N] [--json]`: `Opik().search_traces(project_name=<live>, filter_string=…, max_results=limit)`; presets are OQL strings in ONE table: `errors` → `error_info` present; `low-quality` → `feedback_scores.response_quality < 5`; `long` → top decile of `usage.total_tokens` within the fetched window (computed client-side); `denied` → ≥ 2 tool spans whose output carries the gate's denial outcome (reuse the importer's tool-span detection: `gen_ai.operation.name == "execute_tool"` + `logfire.msg` — ONE helper, two callers).
- [ ] Signature = `(preset, error type or first line, last tool name, metadata.model)`; hits group by signature; one Rich table per signature (count, first/last seen, ≤ 5 trace ids with `thread_id`, `metadata.git_sha`, `metadata.model`); `--json` emits `[{signature, count, traces: [{id, thread_id, start_time, git_sha, model, error}]}]`. Metadata keys absent on pre-156 traces render as `-`.
- [ ] Keyless: `eval_keys_missing()` guard → one skip line, exit 0; `--help` imports no opik (test in `tests/unit/evals/test_run.py`).
- [ ] Unit tests on fixture traces under `tests/unit/evals/fixtures/traces/` (two anonymised real `decode_run` exports + hand-made error/denied/long cases); the Opik client is a fake; signature/preset logic pure with its own tests.
- [ ] `evals/README.md`: step 1 rewritten as "run `python -m evals online-rule create`" (the UI walkthrough becomes a two-line fallback), the prompt quoted from the constant; new "Mining regressions" section: presets, what a signature is, the hand-off to 162's case format.
- [ ] [HUMAN] `python -m evals online-rule create` against `decode-prod`, then one `decode run`: the new trace shows a `response_quality` score within a minute; `python -m evals mine --preset all --since <7 days>` prints at least the `errors` group. Ids logged here.

## Out of scope
- Writing case files (164). Importing into Kitaru (165). Python-metric online rules. Deleting or editing existing rules.

## Log
### [PA] 2026-09-10 — Grooming
`AutomationRuleEvaluatorWrite_LlmAsJudge` (base: `project_id`, `name`, `sampling_rate`, `enabled`, `trigger_scope ∈ production|experiment|both`, `action="evaluator"`; `code`: `model{name,temperature}`, `messages[{role,content}]`, `variables`, `schema[{name,type∈BOOLEAN|INTEGER|DOUBLE,description}]`) read from the installed 2.2.36 source; `find_evaluators(project_id, name)` gives idempotency. Exported `decode-prod` traces carry `thread_id` top-level and in metadata, `usage`, `error_info`, and `decode_run` roots with 15–17 spans; after 156, `metadata.git_sha/model/sandbox_mode` join them.
