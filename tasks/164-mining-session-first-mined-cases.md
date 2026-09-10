---
id: 164-mining-session-first-mined-cases
feature: evals-v2
status: pending
---

# [HUMAN] Mining session: first mined Regression Cases with provenance, beside the 21

Tags: `evals`, `regression`, `[HUMAN]`
Depends on: 162, 163
Blocks: 166

## Scope
The evidence half of ADR-0022 §8: one human session over `decode-prod`'s traces producing the first mined Regression Cases (three to six), landing BESIDE the 21 kept cases. Nothing is deleted.

## Acceptance criteria
- [ ] [HUMAN] `python -m evals mine --preset all --since <30 days> --json` run against `decode-prod`; output committed as `evals/regression/mining/2026-09-<dd>.json` (trace ids + signatures only, no payloads) with `NOTES.md` (what was picked, why, what was skipped).
- [ ] Three to six `evals/regression/cases/mined_<slug>.py` files, ids `21-<slug>` onward, tag `mined`, each a `RegressionCase` with `source_trace_id`, `thread_id`, `symptom` (one sentence), `fixed_in` (commit sha or `"unfixed"`), `difficulty` by the 162 rule, a minimal fixture reproducing the workspace, and ONE deterministic metric (a judge only where code cannot decide, reason in the docstring).
- [ ] Case zero is the known failure: `ModelHTTPError 400` with empty output (`evaluators/decode_bad_request_400.py` guards it on Kitaru today). If it reproduces from a fixture it becomes a case with `OutputContainsMetric`/an `agent_error`-absent metric; if not, the module documents why and ships with `skip_reason` — the attempt is the deliverable.
- [ ] Each mined case's source trace tagged `regression-case` in Opik (`client.update_trace(tags=…)`) so the online view links back; the case's log/docstring names the tag.
- [ ] `make eval-regression` green on the full set (21 + mined) at the existing thresholds; thresholds not lowered; the per-tier report pasted into this log.
- [ ] `evals/regression/README.md` "Mined cases" section lists the new ids with one line each.

## Out of scope
- Kitaru import/cohort of these traces (165 does it from the same case files). Fixing the failures captured (each unfixed case is a future task). Inventing cases to reach six.

## Log
### [PA] 2026-09-10 — Grooming
Light on purpose (ADR-0022 §9): if the live project has fewer than three distinct failure signatures, stop at what is real — do not pad with invented probes; that is the failure mode mined cases exist to end. The 21 invented probes stay as the harness-invariant floor.
