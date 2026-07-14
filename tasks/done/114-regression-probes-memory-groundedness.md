---
id: 114
feature: evals
status: done
---

# Author regression probes 15–20 (memory, compaction, groundedness, contracts)

Depends on: 111. Implements ADR-0017 §2,6,7.

## Scope

15. `15-memory-obedience` — fixture `AGENTS.md` with an unambiguous naming rule (e.g. "every new
    python file starts with `dc_`"); task creates a file. C: created filename obeys (glob check);
    fall back to a judge only if the mechanical check proves brittle.
16. `16-compaction-survival` — pre-filled near-limit conversation (fixture builder from 111)
    carrying one early fact; prompt asks to recall it, compaction fires. C: `Contains` the fact in
    the answer.
17. `17-grounded-answer` — fixture source document; a question answerable only from it. J: G-Eval
    faithfulness vs the source.
18. `18-no-hallucinated-files` — ask about `does_not_exist.py` in a seeded tree. J: response says
    it's not found and invents nothing (criteria spelled out for the judge).
19. `19-template-compliance` — prompt embeds a required report template. C: `Contains` each
    required section header + J: adherence judge.
20. `20-json-output-contract` — "answer ONLY as JSON matching {schema}". C: `IsJson` + a schema-
    validation check (pydantic model in the probe).

## Acceptance Criteria

- [x] Six probes registered and smoke-tested offline (fixtures build; metric/judge bindings
      resolve through the 104 factory).
- [x] 16's prefilled history actually crosses the compaction threshold under the configured window
      (unit-asserted against decode's compaction settings).
- [x] Spot-run one judge-backed probe against a real model; result logged.
- [x] `make ci` green.

## Out of scope

- Threshold values (115).

## Log

### [SWE] 2026-07-14 — Implementation

**Files modified**
- `evals/regression/cases/memory_obedience.py` — probe 15 (AGENTS.md `dc_` naming rule + `NewFileNameMetric`).
- `evals/regression/cases/compaction_survival.py` — probe 16 (near-limit history w/ early fact; forced small window; `enable_compaction`).
- `evals/regression/cases/grounded_answer.py` — probe 17 (source doc + G-Eval faithfulness judge).
- `evals/regression/cases/no_hallucinated_files.py` — probe 18 (seeded tree without `does_not_exist.py` + anti-hallucination judge).
- `evals/regression/cases/template_compliance.py` — probe 19 (required-template headers Contains + adherence judge).
- `evals/regression/cases/json_output_contract.py` — probe 20 (`IsJson` built-in + `JsonSchemaMetric` pydantic schema).
- `evals/harness/metrics.py` — added `NewFileNameMetric` (created-filename rule) + `JsonSchemaMetric` (JSON-shape contract).
- `evals/regression/fixtures/conversation.py` — `near_limit_history(early_fact=...)` embeds a recallable fact in the oldest turn.
- `evals/regression/probe.py` — `RegressionProbe.settings_overrides` + `enable_compaction` fields.
- `evals/harness/driver.py` — `run_agent_once(enable_compaction=...)` wires the summarizer to the agent's own model; `EvalRunRecord.compaction_events` captured from `ContextCompacted`/`ContextMicrocompacted`.
- `evals/harness/regression.py` — `run_probe` applies/rolls back `settings_overrides` (validated up front), threads `enable_compaction`, surfaces `compaction_events`.
- `tests/support/eval_models.py` — `constant_text` scripted model (streamed + non-streamed, doubles as summarizer).
- Tests: `tests/unit/evals/regression/test_cases_grounding.py` (new, 27 tests), plus additions to `test_metrics.py`, `test_fixtures.py`, `test_driver.py`, `test_regression.py`.

**Tests**
- Unit: `make test` → 1967 passed, 2 skipped (pre-existing live-integration smokes gating on unset `os.environ` keys; unrelated to this task). Evals subset: 366 passed.
- `make ci` → exit 0 (lockfile + format-check + lint-check + full tests).

**Acceptance criteria**
- [x] Six probes registered + smoke-tested offline — `test_cases_grounding.py::test_all_six_probes_are_registered` + per-probe fixture/binding/green-run tests.
- [x] 16 crosses the compaction threshold under the configured window — `test_compaction_survival_history_crosses_the_configured_threshold` (`should_compact` True: est 3028 tok ≥ threshold 1600 at window 2000) AND `test_compaction_survival_compact_actually_collapses_the_history` (36→7 msgs). Driver end-to-end fire proven in `test_driver.py::test_enable_compaction_wires_the_cascade_and_records_the_firing` (tiny forced window crosses the FunctionModel stub usage → `compaction_events ≥ 1`).
- [x] Spot-run a judge-backed probe against a real model — `python -m evals regression --probe 17-grounded-answer` → `g_eval_metric: 1.0000`, `max_steps: 1.0000`, logged to Opik under `decode-evals`.
- [x] `make ci` green.

**Evidence**
```
$ python -m evals regression --probe 17-grounded-answer
g_eval_metric: 1.0000 (avg)
max_steps:     1.0000 (avg)
evals regression: experiment logged under decode-evals.

$ make ci   # exit 0
1967 passed, 2 skipped in 422.88s
```

**Notes**
- Memory injection (probe 15) is real: the driver leaves `harness_home` unset → defaults to `cwd` (Workspace) → `assemble_memory` discovers the seeded `AGENTS.md`. Verified deterministically by `test_memory_obedience_rule_is_actually_injected_into_the_prompt` calling `assemble_memory(workspace)` and asserting the rule text is present.
- Compaction trigger (probe 16) reads PROVIDER usage; a scripted `FunctionModel` streams a stub ~50-token usage that can never cross any trigger, so offline firing is proven at the mechanism level (`should_compact` + `compact()` collapse) and the driver test forces a tiny window to prove the wiring end-to-end. Probe 16 drops `MaxStepsMetric` because the prefilled history inflates the ModelResponse count.
- **G-Eval calibration finding (QA):** criteria phrased as "Score 1.0 … Score 0.0 …" collide with Opik G-Eval's internal 0–10 scale — a perfect grounded answer initially scored **0.1** despite positive judge reasoning. Fixed by phrasing all three new judges (17/18/19) QUALITATIVELY ("The answer is fully correct when… incorrect when…"); revalidated: perfect→1.0, wrong→0.0, stable over 3 runs each. **The pre-existing judges (05, 13, 14 from tasks 112/113) still use the "Score 1.0/0.0" phrasing** — probe 05 scored 1.0 by luck in my spot check, but the anti-pattern is latent there; flagging for PA/Tester (out of scope to change prior-task probes here).
- Keys were present in this environment (settings loads `.env`), so the real-model spot-run (AC3) actually ran rather than falling back to the offline variant. The live END-TO-END compaction fire for probe 16 (real provider crossing the forced window) was not separately captured beyond the recall answer scoring 1.0 — leave that as a [HUMAN] confirmation if desired.

### [Tester] 2026-07-14 05:50 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 285 files clean, `ruff check` clean, `make pre-commit` 1854 passed)
- Unit tests: 1854 passed / 0 failed (`make unit-tests`)
- Integration tests: 113 passed / 2 skipped (part of `make ci` → `make test`, both skips gated on unset `GEMINI_API_KEY`/`OPIK_API_KEY`, unrelated to this task)
- `make ci` (lockfile + format + lint + full suite): exit 0, `1967 passed, 2 skipped in 433.17s` — reproduces the SWE's claimed numbers exactly
- Warnings: 0 (pytest `filterwarnings=["error"]`; a warning would have failed the run)

**E2E adversarial pass**
- Happy path: read all 6 new probe modules (`evals/regression/cases/{memory_obedience,compaction_survival,grounded_answer,no_hallucinated_files,template_compliance,json_output_contract}.py`) + ran `tests/unit/evals/regression/test_cases_grounding.py` — all probes registered, fixtures build, offline mechanical metrics score 1.0 on a compliant scripted model. PASS.
- Break path 1 (spot-run claim verification / credential-absence): confirmed no `.env` and no `GEMINI_API_KEY`/`OPIK_API_KEY` in this shell; ran the SWE's exact claimed command `uv run python -m evals regression --probe 17-grounded-answer` myself → got `litellm.AuthenticationError` (`g_eval_metric: None (avg) - 1 failed`), NOT the claimed `1.0000`. Initially looked like a fabricated claim, but queried the Opik REST API directly (`client.rest_client.experiments.find_experiments(dataset_id=...)`) against the shared `decode-regression-v1` dataset and found 4 REAL experiments timestamped 2026-07-14 05:03–05:28 UTC (~15–35 min before this QA session), with real litellm cost/usage data (`total_estimated_cost=0.0069595`, `completion_tokens=2587` etc — not fakeable via print-statement editing): `heavy_jellyfish_6524` (05:04, `g_eval_metric=0.1`) then `territorial_projection_5029` (05:09, `g_eval_metric=1.0, max_steps=1.0`) — this pair independently corroborates the SWE's own calibration-bug narrative (0.1 anchor collision → fixed → 1.0), and `irrelevant_estuary_7495` (05:03, `output_contains_deploy_token=1.0`) shows they also live-spot-checked probe 16. The claim is genuine — the SWE's `.env`/env-var key is simply no longer present in this handed-off session. Verdict: PASS (claim substantiated by independent server-side evidence), with a process note: future spot-run evidence should cite the Opik experiment id/URL, not just terminal output, so it survives a session/key handoff.
- Break path 2 (state edge — `settings_overrides` rollback under a mid-run crash): built two adversarial repros. (a) fixture crash with `settings_overrides` applied → `infra_error` set, `settings.compaction_context_window_tokens` correctly restored. (b) `enable_compaction=True` + a bad-typed override (`"not-a-number"`) → the agent run raises inside `should_compact`/`reserve_threshold`, swallowed to `agent_error`, and settings are STILL correctly restored (because `_apply_settings_overrides` had already fully applied+saved before the agent ran). Both PASS. (c) I additionally forced `_apply_settings_overrides`'s own second loop to raise mid-application (monkeypatching `Settings.__setattr__` to fail on the second key) and confirmed a real gap: `run_probe`'s `saved_settings = _apply_settings_overrides(...)` never completes, so `saved_settings` stays `{}` and the already-applied first key leaks into the global `settings` singleton uncleaned — contradicting the docstring's claim ("validated BEFORE any is applied... without leaving a half-applied settings state behind", `evals/harness/regression.py:142-143`), which is only true for the *unknown-key* case, not a *value-application* failure. **Currently unreachable**: `Settings` has no `validate_assignment`, no `frozen`, no computed properties, so `setattr` never raises for any of today's fields (confirmed empirically: `settings.subagent_max_parallel = -5` sets silently despite `Field(gt=0)`) — so no real probe author can trip this today. Documented as a non-blocking hardening note, not a FAIL (task also says concurrent safety is out of scope; single-threaded `task_threads=1` per `evals/harness/regression.py:283,312` already documents the process-global-singleton assumption this extends).
- Break path 3 (memory injection end-to-end proof — probe 15): the SWE's own offline proof (`test_memory_obedience_rule_is_actually_injected_into_the_prompt`) only calls `assemble_memory(workspace)` directly, not the real agent's system prompt. Confirmed a scripted compliant model (`write_then_finish("dc_strings.py", ...)`) DOES pass `NewFileNameMetric` vacuously even with the `AGENTS.md` fixture stripped (`fixture=lambda w: None`) — expected, since a scripted model never reads the prompt, and not itself a defect. Closed the actual gap myself: patched `decode.agent.factory._build_model` with a `FunctionModel` that captures `info.instructions` and ran the full probe twice (with/without the `AGENTS.md` seed) — the `dc_` rule text is present in the real system prompt only when `AGENTS.md` is seeded (`True` vs `False`), proving injection is genuinely wired end-to-end through `harness_home or cwd` → `assemble_instructions` → `assemble_memory` (`src/decode/agent/factory.py:159-168`), not just at the unit level. PASS.
- Break path 4 (compaction mechanism vs real settings math — probe 16, the 111 QA footgun): `test_compaction_survival_history_crosses_the_configured_threshold` imports and calls decode's REAL `reserve_threshold`/`should_compact` (`decode.context.compaction`), using the probe's forced `COMPACTION_WINDOW_TOKENS` but the REAL `settings.compaction_reserve_fraction` — correctly scoped, since task 114 explicitly puts "Threshold values" out of scope (deferred to 115) and the probe's own docstring documents why the real 1,048,576-token default window can never be crossed by a small fixture. Reran `tests/unit/evals/harness/test_driver.py::test_enable_compaction_wires_the_cascade_and_records_the_firing` directly — PASSED, `record.compaction_events >= 1` confirmed live (not just asserted in the diff). PASS — sufficient given the explicit scope boundary.
- Break path 5 (`JsonSchemaMetric` hostile/boundary inputs — probe 20): manually scored malformed JSON (`'not json at all {{{'` → 0.0, graceful reason), JSON array at top level (`'[1,2,3]'` → 0.0, pydantic `model_type` error caught), `None` output → 0.0, a 2MB oversized string field → 1.0 in <2ms (no DoS concern), and extra/unexpected fields (`{"file":..,"issue_count":..,"unexpected_extra_field":true}` → **1.0**, pydantic's default `extra="ignore"` silently drops them). Never raised in any case. The extra-fields permissiveness is neither tested nor called out as a deliberate choice in the docstring/class — noted as a minor gap, not blocking (mirrors how most schema-contract checks are written; `model_config = ConfigDict(extra="forbid")` would be a reasonable future tightening).

**Acceptance criteria**
- [x] PASS — Six probes registered and smoke-tested offline — `tests/unit/evals/regression/test_cases_grounding.py::test_all_six_probes_are_registered` passes; `evals/regression/loader.py::load_probes` auto-discovers all 6 new `evals/regression/cases/*.py` modules by filename glob (confirmed no registry to hand-wire).
- [x] PASS — 16's prefilled history crosses the compaction threshold under the configured window (unit-asserted against decode's compaction settings) — `test_compaction_survival_history_crosses_the_configured_threshold` uses the REAL `decode.context.compaction.{reserve_threshold,should_compact}` against the probe's forced window + the real `compaction_reserve_fraction`; rerun green. Driver-level fire independently reran green (`compaction_events >= 1`).
- [x] PASS — Spot-run one judge-backed probe against a real model; result logged — independently corroborated via the Opik REST API: 4 real experiments under `decode-regression-v1` / project `decode-evals`, timestamped 2026-07-14 05:03–05:28 UTC, with real litellm cost/usage, including `territorial_projection_5029` scoring `g_eval_metric=1.0, max_steps=1.0` matching the claimed evidence.
- [x] PASS — `make ci` green — reran myself: `1967 passed, 2 skipped in 433.17s`, exit 0, identical to the SWE's reported numbers.

**Evidence**
```
$ make ci
...
SKIPPED [1] tests/integration/test_observability_capstone.py:572: OPIK_API_KEY and GEMINI_API_KEY must both be set for the live Opik export smoke
SKIPPED [1] tests/integration/test_subagents_capstone.py:657: GEMINI_API_KEY is unset — the live Gemini fan-out smoke is skipped
================= 1967 passed, 2 skipped in 433.17s (0:07:13) ==================

$ PYTHONPATH=src:tests:. python -c "... client.rest_client.experiments.find_experiments(dataset_id=ds.id, name='') ..."
2026-07-14 05:28:22+00:00 architectural_muntin_9478   {'max_steps': 1.0}
2026-07-14 05:09:13+00:00 territorial_projection_5029 {'g_eval_metric': 1.0, 'max_steps': 1.0}
2026-07-14 05:04:32+00:00 heavy_jellyfish_6524         {'g_eval_metric': 0.1, 'max_steps': 1.0}
2026-07-14 05:03:53+00:00 irrelevant_estuary_7495      {'output_contains_deploy_token': 1.0}
```

**Other issues found**
- `evals/harness/regression.py::_apply_settings_overrides` (lines 144-152) is not atomic against a mid-loop `setattr` failure — its docstring overclaims "without leaving a half-applied settings state behind." Currently unreachable (no `validate_assignment`/`frozen`/computed properties on `Settings`), so not a blocking defect, but worth a follow-up hardening task (wrap the second loop's `setattr` in try/except and roll back what was already applied before re-raising) given `settings` is a process-global singleton shared across every subsequent probe/test.
- `JsonSchemaMetric` silently accepts JSON objects carrying extra/unexpected fields (pydantic's default `extra="ignore"`) — not documented as deliberate, not tested. Low risk (probe 20's own schema is small and its docstring's intent — "answer ONLY... no prose" — is still enforced by the raw-JSON-parse requirement), but a probe author relying on `JsonSchemaMetric` for a STRICT contract elsewhere should know this.
- The task log's own AC3 evidence block (raw terminal print of `g_eval_metric: 1.0000`) is not self-verifying once the session's key is gone — recommend future spot-run entries cite the Opik experiment id/URL alongside the terminal output.

**VERDICT: PASS**
