---
id: 162-regression-case-v2
feature: evals-v2
status: pending
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
- [ ] `evals/regression/probe.py` → `case.py`: `RegressionCase` = today's fields + `difficulty: Literal["easy","medium","hard"]` (required), `symptom: str` (required; invariants use `"harness invariant: <one line>"`), `assertion: str` (required, non-blank; the natural-language form of the metrics for the Test Suite), `source_trace_id: str | None = None`, `thread_id: str | None = None`, `fixed_in: str | None = None`. Module attrs `CASE`/`CASES`; loader (`load_cases`) discovers them; the 21 modules migrated with the tiers above and one-sentence `symptom`/`assertion` each; `smoke_read_tool.py` stays the template.
- [ ] `python -m evals regression [--case ID] [--difficulty D]` (was `--probe`); `sync --regression` upserts dataset `decode-regression-v2` items `{case_id, difficulty, tags, symptom, source_trace_id}` AND `get_or_create_test_suite("decode-regression-suite")` items `{data: {prompt, case_id, difficulty}, assertions: [case.assertion]}` from ONE loop (fake client asserts both calls); `SUITE_PROBE_IDS`/`ITEM_ASSERTIONS` in `test_suite.py` deleted; `python -m evals suite [--case ID] [--difficulty D]` runs `opik.run_tests` for real; `suite_api_available()` and every "opik 1.9.8" mention in code and `evals/regression/README.md` deleted; `SUITE_PASS_BAR` stays 0.8.
- [ ] `run_regression(*, case_id, difficulty, experiment_name)`: `experiment_scoring_functions=[mean_per_metric]` replaces any post-hoc scoring; `experiment_config` gains `git_sha`, `case_count`, `difficulty`; experiment name `decode-regression-gate` for a full run, `decode-regression-gate-<tier>` for a filtered one (baseline compare stays like-for-like).
- [ ] Per-tier report: `evals/regression/thresholds.py::scores_by_tier(test_results, tiers) -> {tier: {metric: mean}}` (pure, offline-tested); `test_thresholds.py` prints one table per tier via `_warn` and gates GLOBALLY as today (0.8 tool discipline / 0.7 judges — floors unchanged). `evals/regression/conftest.py::pytest_addoption("--difficulty")` so `make eval-regression ARGS='--difficulty hard'` forwards to both the sync and the gate (Makefile passes `$(ARGS)` to both).
- [ ] Unit tests: loader accepts the new fields, rejects blank `assertion`/missing `difficulty`; tier counts 5/8/8 (+12 skipped still discoverable); `regression --difficulty easy` selects exactly the five; sync writes both surfaces; `scores_by_tier` on a hand-built matrix.
- [ ] `evals/regression/README.md` updated: the case contract table (new fields), tiers, `--difficulty`, mined-case provenance fields and where mined cases live (beside, `cases/mined_<slug>.py`, ids `21-…` onward, tag `mined`).

## Out of scope
- Authoring mined cases (164). Kitaru (165). Lowering any threshold. Per-tier gating.

## Log
### [PA] 2026-09-10 — Grooming
Opik 2.2.36 has `get_or_create_test_suite` + `run_tests` (verified on the installed client); the version gate is dead code. Keep the deterministic metric as the gate and the assertion as the contrast — ADR-0017 §6's teaching point survives with less code. 05-web-fetch (judged, web) sits in medium by the human's decision: its tool-discipline half is mechanical; every other judged case is hard.
