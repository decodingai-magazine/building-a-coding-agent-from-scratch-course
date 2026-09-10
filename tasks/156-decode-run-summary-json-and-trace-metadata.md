---
id: 156-decode-run-summary-json-and-trace-metadata
feature: evals-v2
status: pending
---

# `decode run --summary-json` + eval-joinable trace metadata

Tags: `runtime`, `observability`
Depends on: 155
Blocks: 160, 163

## Scope
Give the Benchmark Job ground truth without parsing traces (ADR-0022 §1) and give Trace Mining the fields it filters on (ADR-0022 §10). Two small `src/decode` changes, nothing in `evals/`.

## Acceptance criteria
- [ ] `decode run … --summary-json <path>` writes ONE JSON object (parent dirs created) in `run_headless_task`'s `finally`, AFTER the executor reap and the Hand-back so it can carry the branch:
      `{"session_id", "kitaru_session_id", "exit_reason", "error", "requests", "input_tokens", "output_tokens", "cost_usd", "handback", "output"}` —
      `exit_reason ∈ completed | request_limit | error`; `error` = `"<ExceptionType>: <message>"` on `error`, else `null`; `handback` = `{"branch": "decode/<short-id>", "pushed": bool}` or `null` when skipped; `output` = the final text (`""` on the non-completed paths).
- [ ] Usage comes from the run's messages, not Opik: `pydantic_ai.capture_run_messages()` around `agent.run`, `requests` = number of `ModelResponse`s, tokens summed from `ModelResponse.usage` (the same source `evals/harness/driver.py::_build_record` uses) — so the `request_limit` and `error` paths report what was actually spent. `cost_usd` follows `observability/cost.py`'s honesty rule: the catalog price (`ModelResponse.cost()` / `RunUsage.cost`) when pydantic-ai can price it, else the configured `llm_cost_*` rates, else `null` (the Modal endpoint). One pure helper, unit-tested on the three branches.
- [ ] `kitaru_session_id`: the adapter creates the Session lazily inside `run` and exposes no public accessor (0.1.x keeps it in internal state) — best-effort: if 0.2.1 exposes one, fill it; else `null`. The join stays the decode session id (= Kitaru `session_name`); a unit test pins the `null` branch. Never read a private attribute.
- [ ] Exit codes unchanged (0 / 1 as today); the summary is written on every path the `finally` already covers (completed, request cap, agent error, Worker-Task recording failure); a write failure logs a warning and never changes the exit code.
- [ ] Without the flag: byte-identical behaviour (unit test pins no file written, stdout unchanged).
- [ ] `observability/tracing.py::root_span(name, *, thread_id, input, metadata: Mapping[str, str] | None = None)` sets each key as a plain span attribute (Opik's OTLP ingestion lands unmapped attributes in `trace.metadata` — the exported traces already show `thread_id` there). REPL and headless call sites pass `{"git_sha", "model", "sandbox_mode", "decode_env"}` (`git_sha` = `git rev-parse HEAD` of the launch cwd or `"unknown"`, resolved once); the headless site adds `"kitaru_session_id"` when known. Unit test asserts the attributes; `tests/integration/test_opik_headless_trace.py` asserts they land in the trace's metadata.
- [ ] `running_the_code/01_install_and_usage.md` (where `decode run` is introduced) gains one line for `--summary-json`; `04_deploy.md`'s remote knob table does NOT (the Modal Headless App does not expose it).

## Out of scope
- Any change to what `decode run` prints to stdout/stderr. Reading the summary in `evals/` (160).
- Exposing `--summary-json` through `decode remote`.

## Log
### [PA] 2026-09-10 — Grooming
`run_headless_task` already owns the `finally` that reaps + hands back; `_ship_headless_workspace` returns nothing today — make it return the `ShipResult` (or `None`) so the summary can record `handback`. `capture_run_messages` is public pydantic-ai API and works when `run` raises (`UsageLimitExceeded`), which `agent.run`'s result cannot. Written after Hand-back on purpose: a trial that is killed mid-hand-back has no summary and is an Infra Error, which is the honest verdict.
