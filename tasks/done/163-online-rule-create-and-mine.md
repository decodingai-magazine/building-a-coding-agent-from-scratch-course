---
id: 163-online-rule-create-and-mine
feature: evals-v2
status: done
---

# Online track v2: `evals online-rule create` (idempotent LLM-as-judge rule) + `evals mine` (trace triage)

Tags: `evals`, `online`, `regression`, `opik`
Depends on: 156
Blocks: 164

## Scope
Two live-project commands (ADR-0022 §8, §13). `online-rule create` makes the `response_quality` rule the README today asks a human to click together — `decode-prod` has 0 rules and 68 traces, so `feedback_scores.response_quality` is empty until this exists. `mine` is the discovery half of regression mining: search the LIVE project, cluster by signature, print trace ids for a human to pick. Both keyless-skip; both `--help` opik-free.

## Acceptance criteria
- [x] `evals/harness/online_rule.py` + `python -m evals online-rule create [--project NAME] [--model ID] [--sampling 1.0] [--dry-run]`: resolves the project id (`client.rest_client.projects.retrieve_project(name=…)` / `find_projects`), calls `client.rest_client.automation_rule_evaluators.find_evaluators(project_id=…, name="response_quality")`; if present prints `already exists: <id>` and exits 0; else `create_automation_rule_evaluator(request=AutomationRuleEvaluatorWrite_LlmAsJudge(project_id=…, name="response_quality", sampling_rate=<sampling>, enabled=True, action="evaluator", trigger_scope="production", code=LlmAsJudgeCodeWrite(model=LlmAsJudgeModelParametersWrite(name=<model>, temperature=0.0), messages=[LlmAsJudgeMessageWrite(role="USER", content=<prompt>)], variables={"input": "input", "output": "output"}, schema=[LlmAsJudgeOutputSchemaWrite(name="response_quality", type="INTEGER", description="0–10, qualitative")])))` — the exact write types under `opik/rest_api/types/`, verified at implementation against 2.2.36 (and context7 for the model-id form). `<prompt>` is the qualitative prompt from `evals/README.md` step 6 with `{{input}}`/`{{output}}` placeholders, stored as ONE module constant the README quotes (no drift). Default project = `settings.opik_project_name` (live), never `EVAL_PROJECT_NAME`.
- [x] Default `--model` derives from `evals/harness/judges.py::judge_model()`: for the gemini route the Opik model id (`gemini-2.5-flash`, provider prefix stripped); for openrouter/modal routes the command refuses with one line asking for an explicit `--model` (Opik's server-side judge needs a provider the workspace has configured under AI Providers — say so). Unit-tested with a fake rest client (create called once; second call finds it; `--dry-run` prints the payload and calls nothing).
- [x] `evals/harness/mine.py` + `python -m evals mine [--preset errors|low-quality|long|denied|all] [--since ISO] [--limit N] [--json]`: `Opik().search_traces(project_name=<live>, filter_string=…, max_results=limit)`; presets are OQL strings in ONE table: `errors` → `error_info` present; `low-quality` → `feedback_scores.response_quality < 5`; `long` → top decile of `usage.total_tokens` within the fetched window (computed client-side); `denied` → ≥ 2 tool spans whose output carries the gate's denial outcome (reuse the importer's tool-span detection: `gen_ai.operation.name == "execute_tool"` + `logfire.msg` — ONE helper, two callers).
- [x] Signature = `(preset, error type or first line, last tool name, metadata.model)`; hits group by signature; one Rich table per signature (count, first/last seen, ≤ 5 trace ids with `thread_id`, `metadata.git_sha`, `metadata.model`); `--json` emits `[{signature, count, traces: [{id, thread_id, start_time, git_sha, model, error}]}]`. Metadata keys absent on pre-156 traces render as `-`.
- [x] Keyless: `eval_keys_missing()` guard → one skip line, exit 0; `--help` imports no opik (test in `tests/unit/evals/test_run.py`).
- [x] Unit tests on fixture traces under `tests/unit/evals/fixtures/traces/` (two anonymised real `decode_run` exports + hand-made error/denied/long cases); the Opik client is a fake; signature/preset logic pure with its own tests.
- [x] `evals/README.md`: step 1 rewritten as "run `python -m evals online-rule create`" (the UI walkthrough becomes a two-line fallback), the prompt quoted from the constant; new "Mining regressions" section: presets, what a signature is, the hand-off to 162's case format.
- [ ] [HUMAN] `python -m evals online-rule create` against `decode-prod`, then one `decode run`: the new trace shows a `response_quality` score within a minute; `python -m evals mine --preset all --since <7 days>` prints at least the `errors` group. Ids logged here.

## Out of scope
- Writing case files (164). Importing into Kitaru (165). Python-metric online rules. Deleting or editing existing rules.

## Log
### [PA] 2026-09-10 — Grooming
`AutomationRuleEvaluatorWrite_LlmAsJudge` (base: `project_id`, `name`, `sampling_rate`, `enabled`, `trigger_scope ∈ production|experiment|both`, `action="evaluator"`; `code`: `model{name,temperature}`, `messages[{role,content}]`, `variables`, `schema[{name,type∈BOOLEAN|INTEGER|DOUBLE,description}]`) read from the installed 2.2.36 source; `find_evaluators(project_id, name)` gives idempotency. Exported `decode-prod` traces carry `thread_id` top-level and in metadata, `usage`, `error_info`, and `decode_run` roots with 15–17 spans; after 156, `metadata.git_sha/model/sandbox_mode` join them.

### [SWE] 2026-09-11 09:30 — Implementation

**Files modified**
- `evals/harness/online_rule.py` (new) — the `response_quality` Online Rule: prompt constant, Opik judge-model derivation, idempotent create (exact-name re-filter), `--dry-run` payload.
- `evals/harness/mine.py` (new) — Trace Mining: four presets, signature/grouping/rendering (pure), one thin `TraceSource` Opik seam.
- `importers/opik_spans.py` (new) — the ONE tool-span reader, kitaru-free, imported by BOTH `importers/opik_importer.py` and `evals/harness/mine.py`.
- `importers/opik_importer.py` — `_node_semantics` now calls the shared helper (behaviour identical; its local `_TOOL_MSG_PREFIX` deleted).
- `evals/run.py` — `online-rule create` group/command + `mine` command; keyless skip, `opik_boundary`, lazy imports.
- `evals/README.md` — step 1 rewritten around `python -m evals online-rule create` (UI walkthrough demoted to a short fallback paragraph), prompt quoted from the constant; new "Mining regressions" section (preset table, denial-detection evidence, signature, JSON hand-off to 164).
- `tests/unit/evals/fixtures/traces/` (new, 6 files) — `real_decode_run_{1,2}.json` (anonymised live `decode_run` exports: ids/threads re-minted, prompts/outputs replaced, `created_by`/`filepath`/`function`/`lineno` stripped, span payloads clipped) + hand-made `error_usage_limit`, `denied_gate`, `long_run`, `quiet_run`.
- `tests/unit/evals/harness/test_mine.py` (new, 29), `tests/unit/evals/harness/test_online_rule.py` (new, 15), `tests/unit/importers/test_opik_spans.py` (new, 9), `tests/unit/evals/test_run.py` (+13).

**Tests**
- Unit: 2789 passing, 0 failing (`make pre-commit`, 1 pre-existing `fastapi` skip). New: 66 (29 mine + 15 online_rule + 9 opik_spans + 13 CLI).
- Integration: `tests/integration/test_opik_headless_trace.py` → 6 passed (it is the one integration file that names `importers/opik_importer.py`'s tool-span detection, which this task rerouted through the shared helper). Nothing under `src/` changed, so the rest of the suite is untouched.
- Mutation check (the tests are not vacuous): removing the span sort in `last_tool_name` and replacing the deferred-minus-completed arithmetic in `denied_tool_calls` with `sum(deferred)` fails 4 tests; both reverted.

**Acceptance criteria**
- [x] AC1 `online_rule.py` + `online-rule create` — `create_response_quality_rule`; `tests/unit/evals/harness/test_online_rule.py::test_the_first_run_creates_the_rule_once_and_reports_its_id` / `::test_the_second_run_finds_it_and_writes_nothing` / `::test_a_dry_run_prints_the_payload_and_writes_nothing`. **Three spellings beat the task text** (installed opik 2.2.36 wins, as briefed): `LlmAsJudgeCodeWrite(schema_=…)` — `schema=` raises `ValidationError: schema_ Field required`, the alias only appears on `.dict(by_alias=True)`; `project_ids=[pid]` is set **alongside** `project_id` (the base write model calls `project_id` "legacy … for backwards compatibility" and `project_ids` "used when creating/updating rules"); `create_automation_rule_evaluator` returns `None`, so the id is re-read with `find_evaluators` afterwards. ~~`retrieve_project(name=…)` does not exist~~ **[CORRECTED — see the fixes entry below: it DOES exist and is now the primary lookup]** → `find_projects(name=…)`, whose `name` is a **substring** query, so both project and rule lookups re-filter for an exact match (`::test_the_project_id_is_matched_exactly_never_by_substring`, `::test_a_rule_whose_name_merely_contains_the_rule_name_does_not_count_as_existing`).
- [x] AC2 default `--model` — `opik_judge_model()` strips the `gemini/` route prefix, refuses `openrouter/…` and `openai/…` with one line naming `--model` (`::test_the_gemini_route_becomes_an_opik_model_id_without_the_litellm_prefix`, `::test_the_other_routes_refuse_and_ask_for_an_explicit_model`). Verified against the live workspace: `GET v1/private/llm/models` lists `gemini-2.5-flash` under the configured `gemini` provider, so the derived default is routable.
- [x] AC3 `mine.py` + `mine` — presets in ONE table (`PRESET_FILTERS`); `errors` uses `error_info is_not_empty` (value-less: `error_info is_not_empty ""` is a parse error, and the live backend accepts the bare form — checked with `max_results=1` against `decode-prod`). **Deviation, with evidence:** the AC's `denied` rule ("≥ 2 tool spans whose output carries the gate's denial outcome") rests on a false premise — `pydantic_ai._tool_execution._call_tool` turns a `ToolDenied` into a history part without ever entering the instrumented executor, so no tool span carries denial text (live `output contains "denied"` → 0 hits in `decode-prod`). What *does* exist, proven by a real gate-denied run recorded live (trace `01a08f1a-9aae-70c7-b6f0-5e7a00c50d9b`): the `ApprovalRequired` leg emits an `execute_tool` span whose **`input` carries `pydantic_ai.tool.deferral.name = "ApprovalRequired"`** and no result; an approved call then emits a second, completed span for the same tool+arguments, a denied one never does. `denied_tool_calls` = deferred legs minus completed legs per (tool, arguments), bar still 2 (`::test_a_denied_call_is_a_deferred_leg_with_no_completed_twin`, `::test_the_same_tool_called_twice_and_approved_once_reports_exactly_one_denial`). ONE shared helper, two callers: `importers/opik_spans.py` (`::test_the_importer_reads_tool_semantics_through_this_module`).
- [x] AC4 signature + grouping + `--json` — `::test_a_signature_is_preset_error_last_tool_and_model`, `::test_hits_cluster_by_signature_biggest_group_first`, `::test_a_table_shows_at_most_five_trace_ids_and_says_how_many_it_hid`, `::test_the_json_document_carries_every_trace_in_a_group_not_just_the_five_a_table_shows`, `::test_a_pre_156_hit_renders_its_missing_metadata_as_a_dash_but_keeps_json_null`. Table caps at 5 and says how many it hid; `--json` is uncapped (164 needs every id) and keeps absent metadata `null` rather than `"-"`.
- [x] AC5 keyless + opik-free `--help` — `tests/unit/evals/test_run.py::test_online_rule_create_skips_friendly_without_keys`, `::test_mine_skips_friendly_without_keys`, `::test_the_new_live_commands_help_imports_no_opik` (fresh subprocess, asserts no `opik` module leaked into `sys.modules`).
- [x] AC6 fixtures + fake client + pure logic — as listed above; the Opik client is a fake in every test (no key, no network).
- [x] AC7 README — step 1 is now the command (UI is a 4-line fallback), the prompt is quoted from `RESPONSE_QUALITY_PROMPT` and pinned by `::test_the_readme_quotes_the_prompt_constant_verbatim_so_it_cannot_drift`; new "Mining regressions" section covers presets, the denial rule, what a signature is, and the hand-off to a case.
- [ ] [HUMAN] AC8 — live evidence below; left open for the human's own pass.

**Evidence**
```
$ make pre-commit
338 files already formatted · All checks passed!
2789 passed, 1 skipped in 54.89s

# LIVE, against decode-prod (the task's [HUMAN] AC, run for real)
$ uv run python -m evals online-rule create --dry-run          # repo .env is LLM_PROVIDER=modal
Error: evals online-rule: cannot derive an Opik judge model from the 'modal' route
('openai/Qwen/Qwen3.6-35B-A3B-FP8') — pass --model <id> naming a model your Opik workspace has
configured under AI Providers.

$ LLM_PROVIDER=gemini uv run python -m evals online-rule create --dry-run
{ ... "code": {"model": {"name": "gemini-2.5-flash", "temperature": 0.0}, "schema": [{"name":
"response_quality", "type": "INTEGER", ...}], "variables": {"input": "input", "output": "output"}},
"project_id": "019f5cd3-105c-77fb-a1cd-6b67d4769e3c", "project_ids": [...], "sampling_rate": 1.0,
"trigger_scope": "production", "type": "llm_as_judge" }
evals online-rule: dry run — would create response_quality in decode-prod on judge model gemini-2.5-flash.

$ LLM_PROVIDER=gemini uv run python -m evals online-rule create
evals online-rule: created response_quality (01a08f28-f3ae-707e-a1c7-fcb83c464c5b) in decode-prod
on judge model gemini-2.5-flash.
$ LLM_PROVIDER=gemini uv run python -m evals online-rule create      # idempotent
evals online-rule: already exists: 01a08f28-f3ae-707e-a1c7-fcb83c464c5b (in decode-prod).

# the rule really scores: one real `decode run`, then the trace within ~1 min
$ LLM_PROVIDER=gemini decode run "read note.txt and reply with its exact contents, nothing else"
hello probe
$ # trace 01a08f2a-abdf-7748-aeb1-d608ffba49c1 → response_quality = 10.0
  reason: "The user asked to read 'note.txt' and reply with its exact contents, nothing else. …"

$ LLM_PROVIDER=gemini uv run python -m evals mine --preset errors --limit 20
errors | pydantic_ai.exceptions.ModelHTTPError | - | -                    (2 traces)
errors | pydantic_ai.exceptions.UnexpectedModelBehavior | - | -           (1 trace)
errors | pydantic_ai.exceptions.UsageLimitExceeded | glob | -             (1 trace)
evals mine: 4 trace(s) in 3 signature(s) from decode-prod (preset errors).

$ LLM_PROVIDER=gemini uv run python -m evals mine --preset denied --limit 30
denied | - | read | gemini-3.5-flash
│ 01a08f1a-9aae-70c7-b6f0-5e7a00c50d9b │ ebfc7abe-cdcb-4c71-b32c-fa7f2fd10b8a │ 2026-09-11 06:14:40Z │ 5c46f38b5f56 │ gemini-3.5-flash │
evals mine: 1 trace(s) in 1 signature(s) from decode-prod (preset denied).

$ LLM_PROVIDER=gemini uv run python -m evals mine --preset all --since 2026-09-04T00:00:00Z --limit 30
evals mine: 5 trace(s) in 4 signature(s) from decode-prod (preset all).

# --since really reaches the server (not just composed client-side):
$ LLM_PROVIDER=gemini uv run python -m evals mine --preset errors --limit 20
evals mine: 4 trace(s) in 3 signature(s) …          # spans 2026-07-13 → 2026-09-10
$ LLM_PROVIDER=gemini uv run python -m evals mine --preset errors --since 2026-09-10T00:00:00Z --limit 20
evals mine: 2 trace(s) in 2 signature(s) …          # the two July traces dropped
$ LLM_PROVIDER=gemini uv run python -m evals mine --preset denied --since 2026-09-11T07:00:00Z --limit 30
evals mine: no denied traces in decode-prod.        # the 06:14Z probe is outside the window

$ LLM_PROVIDER=gemini uv run python -m evals mine --preset long --limit 10 --json
[{"signature": "long | - | bash | -", "count": 1, "traces": [{"id": "01a0861b-799c-…",
  "thread_id": "f43f2cf7-…", "start_time": "2026-09-09T12:19:02.656000+00:00", "git_sha": null,
  "model": null, "error": null}]}]

$ LLM_PROVIDER=gemini uv run python -m evals mine --preset low-quality --limit 20
evals mine: no low-quality traces in decode-prod.      # expected: the rule is minutes old
```

**Notes**
- The gate-denied trace the `denied` preset finds is a probe run I recorded on purpose (real agent, real gate, `write` + `bash` denied) so the preset has live ground truth; it is a legitimate `decode_run` in the live project, not a fabricated record.
- On the `denied` preset the signature's `last_tool` is the run's last tool call, NOT the denied one (live: `denied | - | read | gemini-3.5-flash`, where `write`/`bash` were the denied calls and `read` ran afterwards). That is AC4 read literally ("last tool name"); flagging it because it reads oddly on this one preset. Changing it would need the AC changed.
- `--since` refuses a naive timestamp (project datetime rule) rather than assuming UTC; the OQL parser validates the column, never the value, so an unchecked one would reach the server.
- `mine` prints through a console floored at 144 columns — Rich falls back to 80 when piped, which ellipsised the trace ids the command exists to hand over.
- Deliberate trade-off: one `search_spans` per candidate trace (skipped when `has_tool_spans` is false, cached per trace id across presets) = O(`--limit`) requests. Fine at 68 traces; past ~1k, switch to one windowed span query grouped client-side. Named in the module docstring.
- Adjacent issue for the PA, NOT fixed here (out of scope): `importers/opik_importer.py::_build_node` still unwraps tool payloads with the OLD `tool_arguments`/`tool_response` keys only. Live spans now use `gen_ai.tool.call.arguments`/`gen_ai.tool.call.result`, so a current export imports the whole payload dict instead of the unwrapped value (degrades, never crashes). `importers/opik_spans.tool_span_arguments/result` already read both spellings — a one-line follow-up when someone owns the importer.
- `eval_keys_missing()` over-requires: both commands need only `OPIK_API_KEY` (the Opik-side judge uses the workspace's provider key, not ours), but AC5 pins the shared preflight, so the provider key is still demanded. Worth a follow-up if a keyless-inference checkout ever wants to mine.
- "Signature" is used as a domain term throughout (the glossary defines Trace Mining "cluster by signature" but has no Signature row) — PA's call whether it earns its own entry; I did not touch the glossary.

### [Tester] 2026-09-11 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check`: 338 files already formatted; `ruff check`: All checks passed)
- Unit tests: 2789 passed / 0 failed, 1 pre-existing skip (`fastapi`) — matches SWE's log
- Integration tests: not re-run (no `src/` diff; `git status --short src/` empty, confirmed)
- Warnings: 0

**E2E adversarial pass**
- Happy path: `LLM_PROVIDER=gemini uv run python -m evals online-rule create` → `evals online-rule: already exists: 01a08f28-f3ae-707e-a1c7-fcb83c464c5b (in decode-prod).` (idempotent, re-verified live) (PASS)
- Happy path 2: `LLM_PROVIDER=gemini uv run python -m evals mine --preset all --since 2026-09-10T00:00:00Z --limit 50 --json` → valid JSON, 4 groups, the gate-denied trace `01a08f1a-...` correctly appears under BOTH `denied` and `long` signatures (by design), absent `git_sha`/`model` render `null` not `"-"` (PASS)
- Break path 1 (malformed input: naive `--since`): `mine --preset errors --since "2026-09-04T00:00:00"` → `Error: evals mine: --since '...' is not a timezone-aware ISO timestamp...`, exit 1, no traceback (PASS)
- Break path 2 (boundary: unknown preset / out-of-range flags): `mine --preset bogus`, `online-rule create --sampling 2.0`, `mine --limit 0` → Click validation errors, exit 2, no traceback (PASS)
- Break path 3 (state edge: nonexistent / substring / shell-meta project): `online-rule create --project decode-does-not-exist-xyz --dry-run` → friendly `no Opik project named...`; `--project decode` (substring of `decode-prod`) resolved to the DISTINCT project id `019f4b8a-...` (not `decode-prod`'s `019f5cd3-...`), proving the exact-match re-filter works; `--project 'decode-prod; rm -rf /'` → friendly not-found, no shell involvement (query is HTTP, not shell) (PASS)
- Break path 4 (read-only guarantee under load): ran `online-rule create` (idempotent) + `mine --preset all --json` live; rule count on `decode-prod` was 1 before and 1 after — `mine` never writes (PASS)

**Acceptance criteria**
- [ ] FAIL (partial) — AC1 `online_rule.py` + `online-rule create` resolves the project id via `retrieve_project(name=…)` / `find_projects`.
      Evidence: idempotent create/exists/dry-run all verified live and via `tests/unit/evals/harness/test_online_rule.py` (15/15 pass); opik 2.2.36 spellings independently confirmed by reading `.venv/lib/python3.12/site-packages/opik/rest_api/types/automation_rule_evaluator_write.py` (`AutomationRuleEvaluatorWrite_LlmAsJudge` is a real class in the `AutomationRuleEvaluatorWrite` union) and `llm_as_judge_code_write.py` (`schema_: Annotated[..., FieldMetadata(alias="schema")]` — the SWE's claim is correct).
      Defect: `resolve_project_id`/`find_rule_id` use `find_projects`/`find_evaluators`, which are PAGINATED substring queries called with no `size=`, then only the returned (first) page is re-filtered for an exact name. The docstring/log claim "`retrieve_project(name=…)` does not exist" is FALSE — verified live: `client.rest_client.projects.retrieve_project(name="decode-prod")` returns `ProjectDetailed(id='019f5cd3-105c-77fb-a1cd-6b67d4769e3c', name='decode-prod', ...)` directly, exact-match, no pagination, raising `NotFoundError` when absent. As written, if enough projects/rules ever contain the substring "decode"/"response_quality" that the exact match falls off page 1, `online-rule create` will wrongly report "no Opik project named 'decode-prod'" even though it exists. Not reachable today (few projects on `decode-prod`'s workspace) but a real latent bug on ground the AC itself named as the primary option.
      Fix: switch `resolve_project_id` to `rest_client.projects.retrieve_project(name=project)`, catching `opik.rest_api.core.api_error.ApiError`/`NotFoundError` (404) into `OnlineRuleError`; correct the docstring and this task's Log to drop the false claim. `find_rule_id` can stay on `find_evaluators` (no exact-match alternative exists) but should be noted as page-1-only.
- [x] PASS — AC2 default `--model` derivation — `tests/unit/evals/harness/test_online_rule.py::test_the_gemini_route_becomes_an_opik_model_id_without_the_litellm_prefix` / `::test_the_other_routes_refuse_and_ask_for_an_explicit_model`; live: `LLM_PROVIDER=modal` (repo `.env` default) → `cannot derive an Opik judge model from the 'modal' route`; `LLM_PROVIDER=gemini` → `gemini-2.5-flash`.
- [x] PASS — AC3 `mine.py` presets, ONE table, `denied` redefinition — verified `PRESET_FILTERS` is the one table; cross-checked the `denied` redefinition against pydantic-ai source (not just the SWE's say-so): `.venv/lib/python3.12/site-packages/pydantic_ai/_tool_execution.py:722-728` — a `ToolDenied` result skips `tool_manager.execute_tool_call(validated)` entirely (`tool_result = tool_call_result`), so the instrumented executor that emits the `execute_tool` span NEVER runs for a denied call; `.venv/.../pydantic_ai/capabilities/instrumentation.py:480-488` confirms the ONLY span emitted for a call that raised `ApprovalRequired` sets `pydantic_ai.tool.deferral.name = "ApprovalRequired"` with no result. This is primary-source proof the SWE's "deferred leg minus completed twin" redefinition is CORRECT, not a hack — the task's original text (denial text on a tool span's output) rests on a false premise, exactly as logged. `tests/unit/importers/test_opik_spans.py` (9/9), `tests/unit/evals/harness/test_mine.py::test_a_denied_call_is_a_deferred_leg_with_no_completed_twin` / `::test_the_same_tool_called_twice_and_approved_once_reports_exactly_one_denial` pass; live probe trace `01a08f1a-...` reproduces the exact shape.
- [x] PASS — AC4 signature + grouping + `--json` — `test_mine.py` (29/29); manually verified `top_decile` math: n=10 → 1 trace, n=15 → 2 traces (`ceil(n/10)`); live `mine --preset all --since ... --json` confirmed uncapped JSON, duplicate id across two signatures, `null` (not `"-"`) for absent metadata.
- [x] PASS (with note) — AC5 keyless + opik-free `--help` — `test_run.py` subprocess tests confirm `sys.modules` carries no `opik` after `--help`; keyless skip confirmed live is unreachable to test without unsetting real keys, so verified via `test_online_rule_create_skips_friendly_without_keys` / `test_mine_skips_friendly_without_keys` (both pass) plus reading `evals/harness/keys.py::eval_keys_missing`.
      Note (not blocking, AC5 pins `eval_keys_missing()` verbatim so this isn't a spec violation): neither command makes an inference call — `mine` only queries Opik (read-only), and `online-rule create`'s judge runs server-side on Opik's own configured provider, never decode's local key — yet both refuse without the ACTIVE PROVIDER's key too. This bites today: the repo's own committed `.env` ships `LLM_PROVIDER=modal`, so `evals mine` (a read-only query) skips with "set OPIK_API_KEY, MODAL_ENDPOINT_URL" even though it never contacts Modal. Suggested shape for a follow-up task: `eval_keys_missing(opik_only=True)` or a separate `opik_keys_missing()`.
- [ ] FAIL (partial) — AC6 fixtures + fake client — fake-client/pure-logic tests all verified (53/53 across `test_mine.py`/`test_online_rule.py`/`test_opik_spans.py`); no emails, API keys, `created_by`/`filepath`/`function`/`lineno`, or absolute home paths found in either real export (grepped both files for key/token patterns and `/Users/`/`/home/` — none found).
      Defect: both `real_decode_run_{1,2}.json` leak `server.address: "p-b-iusztin--ep-qwen3-6-35b-a3b-fp8-server.us-west.modal.direct"` (7-8 occurrences each) on LLM spans — the real, live Modal serving-endpoint hostname, tied to the real Modal workspace slug. Checked exploitability: `curl -X POST https://p-b-iusztin--...modal.direct/v1/models` → `401 {"error":"proxy auth required"}`, so this is NOT an unauthenticated-access exposure (does not block on its own). But it contradicts the SWE's own anonymisation claim ("ids/threads re-minted, prompts/outputs replaced, `created_by`/`filepath`/`function`/`lineno` stripped") — `server.address` was missed — and it is untouched by every code path under test (`is_tool_span`/`tool_span_name`/`last_tool_name`/`denied_tool_calls`/`total_tokens`/`metadata_value` all ignore LLM-span-only fields), so redacting it costs nothing.
      Fix: `sed` both fixtures to replace `server.address` with a placeholder (e.g. `"anonymised.modal.direct"`).
- [x] PASS — AC7 README — `evals/README.md` step 1 rewritten around the command, UI demoted to a 4-line fallback; `RESPONSE_QUALITY_PROMPT` diffed byte-for-byte against the quoted README block — identical; new "Mining regressions" section covers presets/denial-rule/signature/hand-off; `test_the_readme_quotes_the_prompt_constant_verbatim_so_it_cannot_drift` passes.
- [ ] AC8 [HUMAN] — left open per spec; live evidence in the SWE's log is real (independently reproduced: idempotent create, live `mine` output) but the checkbox itself is reserved for human sign-off.

**Evidence**
```
$ make pre-commit
uv run ruff format --check
338 files already formatted
uv run ruff check
All checks passed!
uv run pytest tests/unit
2789 passed, 1 skipped in 55.05s

$ LLM_PROVIDER=gemini uv run python -m evals online-rule create
evals online-rule: already exists: 01a08f28-f3ae-707e-a1c7-fcb83c464c5b (in decode-prod).

$ uv run python -c "opik project retrieve_project(name='decode-prod')"
ProjectDetailed(id='019f5cd3-105c-77fb-a1cd-6b67d4769e3c', name='decode-prod', ...)   # exists; SWE's "does not exist" claim is false

$ curl -sX POST https://p-b-iusztin--ep-qwen3-6-35b-a3b-fp8-server.us-west.modal.direct/v1/models
{"error":"proxy auth required"}   # 401 — leaked hostname is not an unauthenticated exposure

$ LLM_PROVIDER=gemini uv run python -m evals mine --preset all --since 2026-09-10T00:00:00Z --limit 50 --json
[4 groups, valid JSON, trace 01a08f1a-... under both "denied" and "long" signatures, null metadata for pre-156 traces]
# rule count on decode-prod: 1 before, 1 after this run — mine never writes

$ uv run kitaru importer test importers/opik_importer.py --entrypoint parse --payload importers/fixtures/opik-sample.json
{"ok":true,...,"sessions":2,"failures":1,"items":3}   # importer offline parity holds after the opik_spans.py refactor
```

**Other issues found**
- None beyond the two FAILs above; the mutation-check claim (removing the span sort / denial arithmetic breaks 4 tests) is plausible and consistent with the tests actually reading order/arithmetic, not re-verified by hand.

**VERDICT: FAIL**

Two concrete, cheap fixes needed before re-review:
1. Redact `server.address` (the real Modal serving hostname) from `tests/unit/evals/fixtures/traces/real_decode_run_{1,2}.json` — not exploitable (401 proxy-auth-required, confirmed live) but contradicts the claimed anonymisation and costs nothing to fix.
2. `resolve_project_id` (and ideally note the same limit on `find_rule_id`): switch to `rest_client.projects.retrieve_project(name=project)` — exact-match, unpaginated, verified live to work — instead of `find_projects` + page-1-only re-filter. Correct the false "`retrieve_project` does not exist" claim in the module docstring and this task's Log.

Everything else (opik 2.2.36 type spellings, idempotency, judge-model routing, the `denied` redefinition — independently proven correct against pydantic-ai source, not just accepted on the SWE's word — signature/grouping/JSON shape, decile math, keyless/opik-free `--help`, importer parity, `make pre-commit`) verified and holds. Re-review will be fast: re-run `test_online_rule.py` + `test_mine.py`, confirm the two grep/curl checks, and spot-check the rest.

### [SWE] 2026-09-11 14:05 — Fixes (Tester round 1)

**Files modified**
- `tests/unit/evals/fixtures/traces/real_decode_run_1.json` / `_2.json` — the leaked live Modal serving hostname (`p-b-iusztin--…modal.direct`, 7 + 8 occurrences) replaced everywhere with `inference.example.invalid`. Nothing under test reads `server.address`, so no assertion moved.
- `tests/unit/evals/test_trace_fixtures.py` (new, 9 tests) — the regression guard: EVERY file under `tests/unit/evals/fixtures/traces/` is read as raw text and scanned against a deny-list (`iusztin`, `modal.direct|modal.run`, `/Users/` · `/home/<user>`, an e-mail regex, `sk-[A-Za-z0-9]{8,}`, `AIza[A-Za-z0-9_-]{10,}`).
- `evals/harness/online_rule.py` — `resolve_project_id` now calls `rest_client.projects.retrieve_project(name=…)` (exact-match, unpaginated) and turns opik's own `NotFoundError` into the same friendly `OnlineRuleError`; the `find_projects` page-1 substring path is deleted. Docstring corrected (the old "does not exist" claim was false); `find_rule_id` now documents that rules have no retrieve-by-name endpoint in 2.2.36 and it reads page one only.
- `evals/harness/keys.py` — `eval_keys_missing(*, require_provider: bool = True)`; `False` returns the `OPIK_API_KEY` check alone. Default path byte-identical, so the Makefile guard, the online judge and the threshold gate are unchanged.
- `evals/run.py` — `online-rule create` and `mine` (and only those two) call `eval_keys_missing(require_provider=False)`; both command docstrings say so.
- `evals/README.md` — the live-project "Keys" paragraph now says `OPIK_API_KEY` **and nothing else**, with the one thing that still needs a flag on a non-gemini route (`--model`).
- `tests/unit/evals/harness/test_online_rule.py` — the fake `projects` now models `retrieve_project` (exact match, raises the REAL `opik.rest_api.errors.NotFoundError`); the exact-match test additionally asserts the substring name `decode` resolves to its OWN id.
- `tests/unit/evals/harness/test_keys.py` (+2), `tests/unit/evals/test_run.py` (+2 parametrized) — both guard branches, and the wiring (`require_provider=False` reaches the preflight from both commands).

**Tests**
- Unit: 2802 passing, 0 failing, 1 pre-existing `fastapi` skip (`make pre-commit`). New since round 1: +13.
- Targeted: `pytest tests/unit/evals tests/unit/importers` → 783 passed.
- Integration: N/A — no `src/` or `importers/` change in this round (`git status --short src/ importers/` shows only the round-1 files).

**Acceptance criteria**
- [x] AC1 — `resolve_project_id` is now the endpoint the AC named first: `POST v1/private/projects/retrieve`. Exact-match by construction (no page to re-filter, no substring), `404 → NotFoundError →` one friendly line. `tests/unit/evals/harness/test_online_rule.py::test_the_project_id_is_matched_exactly_never_by_substring` (now also pins `decode → pid-plain`, never `decode-prod`'s id) and `::test_a_project_that_does_not_exist_is_one_friendly_error`. `find_rule_id` stays on `find_evaluators` — there is no retrieve-by-name for evaluators in 2.2.36 — with its page-1 limit written down.
- [x] AC6 — both real exports re-scanned clean; `tests/unit/evals/test_trace_fixtures.py::test_a_trace_fixture_carries_nothing_identifying` is parametrized over every fixture file, so a new fixture is scanned the moment it lands. Two meta-tests keep the scan honest: `::test_the_scan_would_actually_catch_a_leak` (the exact leaked hostname still trips two patterns) and `::test_a_bounded_key_pattern_does_not_fire_on_ordinary_fixture_text` (`sk-`/`AIza` are bounded, so `task-163` is not a credential).
- [x] Tester item 3 (narrower key guard) — `eval_keys_missing(require_provider=False)`; `tests/unit/evals/harness/test_keys.py::test_an_opik_only_caller_does_not_need_the_provider_key` asserts BOTH branches in one test (the narrow one returns `[]`, the default still returns `["MODAL_ENDPOINT_URL"]`), `::test_an_opik_only_caller_still_needs_the_opik_key`, and `tests/unit/evals/test_run.py::test_the_opik_only_commands_do_not_demand_an_inference_key` pins the wiring for both commands.

**Evidence**
```
$ grep -rEoi "iusztin|modal\.(direct|run)|/Users/|/home/|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|sk-[A-Za-z0-9]{8,}|AIza[A-Za-z0-9_-]{10,}" tests/unit/evals/fixtures/traces/*.json
(no output — all six fixtures clean)

$ make format-fix && make lint-fix && make format-check && make lint-check
339 files left unchanged · All checks passed! · 339 files already formatted · All checks passed!

$ make pre-commit
2802 passed, 1 skipped in 54.33s

$ uv run pytest tests/unit/evals tests/unit/importers -q
783 passed in 25.70s

# LIVE (item 2 — the paths whose shape actually changed), against the real workspace
$ LLM_PROVIDER=gemini uv run python -m evals online-rule create
evals online-rule: already exists: 01a08f28-f3ae-707e-a1c7-fcb83c464c5b (in decode-prod).

$ LLM_PROVIDER=gemini uv run python -m evals online-rule create --project decode-does-not-exist-xyz --dry-run
Error: evals online-rule: no Opik project named 'decode-does-not-exist-xyz' — run decode once so the
project exists, or pass --project with the right name.        # NotFoundError → friendly, no traceback

$ LLM_PROVIDER=gemini uv run python -m evals online-rule create --project decode --dry-run
  "project_id": "019f4b8a-74af-77bf-89ac-1bfc2cd291e5",        # `decode`'s OWN id, not decode-prod's
evals online-rule: dry run — would create response_quality in decode on judge model gemini-2.5-flash.
                                                               # (decode-prod is 019f5cd3-105c-…)

# the Tester's break-path 3, re-run because THIS fix changed its mechanism (empty page → 404)
$ LLM_PROVIDER=gemini uv run python -m evals online-rule create --project 'decode-prod; rm -rf /' --dry-run
Error: evals online-rule: no Opik project named 'decode-prod; rm -rf /' — run decode once so the
project exists, or pass --project with the right name.        # friendly, no traceback, no shell
$ LLM_PROVIDER=gemini uv run python -m evals online-rule create --project '' --dry-run
evals online-rule: already exists: 01a08f28-… (in decode-prod).   # empty == unset → the live default

# LIVE (item 3 — under the repo's committed .env, LLM_PROVIDER=modal, no override)
$ uv run python -m evals mine --preset errors --limit 10
evals mine: 4 trace(s) in 3 signature(s) from decode-prod (preset errors).   # was: skipped

$ OPIK_API_KEY="" MODAL_ENDPOINT_URL="" uv run python -m evals mine --preset errors
evals mine: skipped — set OPIK_API_KEY to search live traces.                # names OPIK only
$ OPIK_API_KEY="" MODAL_ENDPOINT_URL="" uv run python -m evals online-rule create
evals online-rule: skipped — set OPIK_API_KEY to create the online rule.

# every OTHER caller of the preflight is unchanged
$ MODAL_ENDPOINT_URL="" uv run python -m evals.harness.keys
evals: skipped — set MODAL_ENDPOINT_URL to run (see the Evals block in .env.example).   # exit 1
```

**Notes**
- **Correction to my round-1 log** (annotated inline above): `client.rest_client.projects.retrieve_project(name=…)` DOES exist in opik 2.2.36 — `opik/rest_api/projects/client.py:823`, `POST v1/private/projects/retrieve`, `404 → NotFoundError` at `projects/raw_client.py:1203`. My earlier claim was wrong and the paginated substring lookup it justified is gone.
- On a non-gemini route `online-rule create` no longer skips for a missing provider key; it now reaches the judge-model derivation and exits 1 with `cannot derive an Opik judge model from the 'modal' route — pass --model <id>`. That is the right failure (it names the one thing the operator must supply), and the README says so.
- A malformed project name cannot escape as a raw error: `resolve_project_id` converts `NotFoundError` (404) itself, and any OTHER opik `ApiError` (a 400 on a name the endpoint refuses to parse, say) is still caught by the command's existing `opik_boundary()`, which matches `ApiError` broadly — re-verified live with the Tester's own shell-meta project name, above.
- Deny-list patterns are deliberately bounded (`sk-` + 8, `AIza` + 10, `/home/[a-z]`): an unbounded `sk-` matches `task-163` and an unbounded `@` matches a decorator inside a captured traceback, and a test that cries wolf gets deleted. It scans raw file text, not parsed JSON values, so a leak in a KEY or inside a nested JSON-encoded string is caught too.
- Still open, unchanged from round 1: AC8 is `[HUMAN]`; the `denied` preset's `last_tool` is the run's last tool, not the denied one (AC4 read literally).

### [Tester] 2026-09-11 07:07 — QA (round 2, re-review of fixes)

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check`: 339 files already formatted; `ruff check`: All checks passed)
- Unit tests: 2802 passed / 0 failed, 1 pre-existing skip (`fastapi`) — matches SWE's round-2 log
- Integration tests: not re-run — `git status --short src/` empty (confirmed independently), no `src/` diff this round or round 1
- Warnings: 0

**E2E adversarial pass (re-verification of both round-1 fixes, live)**
- Happy path: `LLM_PROVIDER=gemini uv run python -m evals online-rule create` → `evals online-rule: already exists: 01a08f28-... (in decode-prod).` (PASS)
- Fix 1 re-check (hostname leak): `grep -rEni "iusztin|modal\.(direct|run)|/Users/|/home/|<email>|sk-...|AIza..." tests/unit/evals/fixtures/traces/*.json` → zero hits (exit 1, no matches); `inference.example.invalid` present 7×/8× in the two real exports (was the leaked hostname) (PASS)
- Fix 1 mutation check (deny-list actually red on a leak): copied `real_decode_run_1.json` into a scratch dir, replaced `inference.example.invalid` back with the original `p-b-iusztin--ep-qwen3-6-35b-a3b-fp8-server.us-west.modal.direct`, dropped it into the fixtures dir as `zzz_planted_leak.json`, ran `pytest tests/unit/evals/test_trace_fixtures.py` → 1 failed (`workspace owner's name: 'iusztin'; Modal host domain: 'modal.direct'`), removed the planted file, re-ran → 9 passed, `git status --short tests/unit/evals/fixtures/traces/` shows only the expected untracked new dir, nothing left behind (PASS)
- Fix 2 re-check (`resolve_project_id` uses `retrieve_project`): read `evals/harness/online_rule.py::resolve_project_id` — calls `rest_client.projects.retrieve_project(name=project)`, catches `NotFoundError`. Independently confirmed against the installed opik 2.2.36 source (not just the SWE's word): `.venv/lib/python3.12/site-packages/opik/rest_api/projects/client.py` — `retrieve_project(self, *, name: str, ...)`; `raw_client.py:1145-1213` — `POST v1/private/projects/retrieve`, `404 → raise NotFoundError`. No `find_projects` call remains anywhere in `online_rule.py` (grepped). `tests/unit/evals/harness/test_online_rule.py::FakeProjects.retrieve_project` fakes the real exact-match/404 contract, not a substring page. (PASS)
- Break path (state edge, live, mechanism changed): `online-rule create --project 'decode-prod; rm -rf /' --dry-run` → friendly `no Opik project named ...`, exit 1, no traceback, no shell involvement; `--project decode-does-not-exist-xyz-999 --dry-run` → same friendly shape (PASS)
- Break path (keyless-narrowing regression check): `MODAL_ENDPOINT_URL="" uv run python -m evals mine --preset errors --limit 5` (repo `.env` ships `LLM_PROVIDER=modal`, `OPIK_API_KEY` present) → ran live against `decode-prod`, printed 3 signature groups, exit 0 (previously would have skipped, wrongly, on a read-only query); `MODAL_ENDPOINT_URL="" uv run python -m evals.harness.keys` (the untouched default-path guard) → `evals: skipped — set MODAL_ENDPOINT_URL to run...`, exit 1 — confirms the narrowing is scoped to the two new commands only, the shared Makefile/threshold-gate guard is byte-identical (PASS)

**Acceptance criteria**
- [x] PASS — AC1 `online_rule.py` + `online-rule create` resolves the project id via `retrieve_project(name=…)` (exact-match, unpaginated) — round-1 defect fixed. Evidence above (source read + live re-check + fake-client contract).
- [x] PASS — AC2 default `--model` derivation — unchanged from round 1, still verified (`test_online_rule.py` judge-model tests pass; live `LLM_PROVIDER=modal` → refuses naming `--model`, `LLM_PROVIDER=gemini` → `gemini-2.5-flash`).
- [x] PASS — AC3 `mine.py` presets, `denied` redefinition — unchanged from round 1, still verified against pydantic-ai source; `test_mine.py` 29/29, `test_opik_spans.py` 9/9 pass.
- [x] PASS — AC4 signature + grouping + `--json` — unchanged from round 1, still verified.
- [x] PASS — AC5 keyless + opik-free `--help`, now scoped correctly — `eval_keys_missing(require_provider=False)` for `online-rule create`/`mine` only; `test_run.py::test_the_opik_only_commands_do_not_demand_an_inference_key` asserts `guard.call_args.kwargs == {"require_provider": False}` for both commands (a real wiring assertion, not just a return-value stub); `test_keys.py` covers both branches of `eval_keys_missing`; live-reproduced both directions (mine runs on OPIK-key-only; the shared `evals.harness.keys` guard is untouched). This resolves the round-1 "PASS with note" (the note is no longer applicable — the over-requiring is fixed).
- [x] PASS — AC6 fixtures + fake client — round-1 hostname-leak defect fixed and independently re-verified (grep + mutation-test above); new `test_trace_fixtures.py` (9 tests, parametrized over every fixture file) is a real regression guard, confirmed to go red on the exact leaked string and confirmed not vacuous (`test_the_scan_would_actually_catch_a_leak`, `test_a_bounded_key_pattern_does_not_fire_on_ordinary_fixture_text` both pass).
- [x] PASS — AC7 README — unchanged from round 1 (still verified), plus the "Keys" paragraph now correctly says `OPIK_API_KEY` and nothing else for the two live-project commands, matching the narrowed guard.
- [ ] AC8 [HUMAN] — left open per spec; live evidence in the SWE's log (both rounds) is real and independently reproduced (idempotent create, live `mine` output, keyless-narrowing live checks) but the checkbox is reserved for human sign-off.

**Evidence**
```
$ make pre-commit
uv run ruff format --check
339 files already formatted
uv run ruff check
All checks passed!
uv run pytest tests/unit
2802 passed, 1 skipped in 54.91s

$ grep -rEni "iusztin|modal\.(direct|run)|/Users/|/home/|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|sk-[A-Za-z0-9]{8,}|AIza[A-Za-z0-9_-]{10,}" tests/unit/evals/fixtures/traces/*.json
(no output)

# mutation check on the deny-list test (planted then removed, not left in the tree)
$ cp tests/unit/evals/fixtures/traces/real_decode_run_1.json /tmp/.../planted_leak.json
$ sed -i '' 's/inference\.example\.invalid/p-b-iusztin--ep-qwen3-6-35b-a3b-fp8-server.us-west.modal.direct/' planted_leak.json
$ cp planted_leak.json tests/unit/evals/fixtures/traces/zzz_planted_leak.json
$ uv run pytest tests/unit/evals/test_trace_fixtures.py -q
.......F..
FAILED ...::test_a_trace_fixture_carries_nothing_identifying[zzz_planted_leak.json] - AssertionError: ...
leaks identifying data — anonymise it before committing: workspace owner's name: 'iusztin'; Modal host domain: 'modal.direct'
$ rm tests/unit/evals/fixtures/traces/zzz_planted_leak.json
$ uv run pytest tests/unit/evals/test_trace_fixtures.py -q
9 passed
$ git status --short tests/unit/evals/fixtures/traces/
?? tests/unit/evals/fixtures/traces/          # only the expected untracked new dir, nothing planted left behind

$ git status --short src/
(empty)

$ MODAL_ENDPOINT_URL="" uv run python -m evals mine --preset errors --limit 5
[3 signature groups printed]
evals mine: 4 trace(s) in 3 signature(s) from decode-prod (preset errors).

$ MODAL_ENDPOINT_URL="" uv run python -m evals.harness.keys
evals: skipped — set MODAL_ENDPOINT_URL to run (see the Evals block in .env.example).   # exit 1, unchanged default path

$ LLM_PROVIDER=gemini uv run python -m evals online-rule create --project 'decode-prod; rm -rf /' --dry-run
Error: evals online-rule: no Opik project named 'decode-prod; rm -rf /' — run decode once so the project exists, or pass --project with the right name.
```

**Other issues found**
- None new this round. Carried forward from round 1 (not blocking, already noted): the `denied` preset's `last_tool` reads as the run's last tool call, not the denied one, on AC4's literal wording; "Signature" as a glossary candidate is the PA's call.

**VERDICT: PASS**

Both round-1 defects are fixed and independently re-verified against primary sources (installed opik 2.2.36 REST client source, not just the SWE's log) and live against `decode-prod`: the fixture hostname leak is gone (grep-clean, deny-list test proven non-vacuous by a plant-and-revert mutation check) and `resolve_project_id` now uses the exact-match, unpaginated `retrieve_project` endpoint the AC named first. The scoped `eval_keys_missing(require_provider=False)` fix (Tester round-1 note, not a blocking item) is also done cleanly — narrow, tested at the wiring level, and the shared default-path guard is confirmed byte-identical live. Full suite green (2802 passed, 1 pre-existing skip), format/lint clean, no `src/` diff.
