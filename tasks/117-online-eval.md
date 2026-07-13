---
id: 117
feature: evals
status: pending
---

# Online eval: one LLM-judge rule + thread-level metric on live traces

Depends on: 103 (settings). Implements ADR-0017 §10. Builds on ADR-0014 tracing.

## Scope

Small and scripted/documented — the production-eval teaching story over the EXISTING Opik project
decode's live REPL traces land in (not `eval_project_name`):

- **Online rule (documented)**: `docs/evals.md` §online (or `evals/README.md`) — step-by-step
  setup of ONE Opik online LLM-judge rule in the UI scoring live decode traces (e.g. a response-
  quality/groundedness judge), with a screenshot-free, CLI-first description of what to configure
  and what appears on traces.
- **Thread-level metric (scripted)**: `evals/harness/online.py` + `python -m evals online` —
  `evaluate_threads(...)` over recent threads in the live project (session id / exec_id threads,
  ADR-0014) with one conversation-level judge metric; prints per-thread scores.

**Tests**: the thread-selection + wiring with the opik client mocked.

## Acceptance Criteria

- [ ] `python -m evals online` scores recent live threads (spot-run against a real workspace with
      traces; logged) and skips friendly without keys.
- [ ] The online-rule walkthrough is complete enough to set up without guesswork.
- [ ] `make ci` green.

## Out of scope

- User simulation / `opik.simulation` (non-goal). Any change to `observability/tracing.py`.

## Log
