---
id: 095-opik-capstone-e2e
feature: opik-observability
status: pending
---

# Opik observability — capstone (hermetic span-tree + subagent nesting + live smoke)

Tags: `observability`, `opik`, `test`
Depends on: #092, #093
Blocks: —

## Scope

The living proof for ADR-0014, doubling as documentation — mirror
`tests/integration/test_subagents_capstone.py` / `test_milestone1_capstone.py`. New file
`tests/integration/test_observability_capstone.py`. Drive the REAL stack and swap only the model
boundary (`FunctionModel`) and the span sink (`logfire.testing` in-memory exporter — the FIRST OTel
test utility in this repo).

- **Always-run hermetic slice (no key / no network).** Build the real agent via `build_agent()`,
  fake `OPIK_API_KEY` for activation only, call `init_tracing()` against `logfire.testing`'s in-memory
  exporter, and `agent.override(model=FunctionModel(...))` to script parent + children. Through the
  real `Runner` + `AgentTurnHandler` + gate + `render_event` + `SessionLog`, assert the span TREE:
  - **Turn root span** named `chat_turn` with `thread_id` = session id, wrapping all legs.
  - **Nested spans** — the agent-run / `chat` / tool spans for the turn are children of the root.
  - **Tokens** — an LLM span carries `gen_ai.usage.input_tokens` (> 0).
  - **Subagent nesting (closes ADR-0013 §9)** — a parent turn that fans out `agent(...)` calls has the
    child `agent.run()` model/tool spans nested INSIDE the parent turn's trace, with the child's token
    usage visible on the child LLM span (same task/contextvars → automatic parenting; cite ADR-0013
    §9).
  - **No-op-when-unconfigured** — with no `OPIK_API_KEY`, the identical turn emits ZERO spans and is
    byte-identical (mutation-proof the activation guard).
  - **Compaction rides free** — an in-turn compaction call nests under the turn root.
- **skipif-guarded live Opik smoke** (SKIP when `OPIK_API_KEY` is unset — never fail): with real creds
  from `.env`, run ONE real turn/run and assert the exporter shipped without error (presence, not
  Opik-side content). Best-effort **cost/tokens presence** check on the Gemini LLM span (tokens
  expected; cost present for priced Gemini, tokens-only acceptable for open models) — presence-only,
  non-fatal, documented.
- **Test isolation** — use `logfire.testing` for per-test provider isolation and `reset_tracing()`
  around the activating tests so the process-global config never leaks into other tests.
- **Module docstring** names REAL vs FAKED boundaries (real: `build_agent`, `Runner`/
  `AgentTurnHandler`, gate, `render_event`, `SessionLog`, `init_tracing`, global
  `instrument_pydantic_ai`; faked: `FunctionModel`, `logfire.testing` exporter, fake key).

## Acceptance Criteria

- [ ] **Hermetic:** the span-tree slice passes with no key/network and proves — turn root span with
  `thread_id`=session id, nested chat/tool spans, tokens on the LLM span, subagent child spans nested
  in the parent turn with child token usage, compaction nesting, and the zero-spans no-op path (each
  mutation-proof: e.g. removing the root span or the activation guard fails a test).
- [ ] **Subagent closure:** an explicit assertion that a child `agent(...)` run's spans are children
  of the parent turn's root (ADR-0013 §9 fulfilled) with child token counts present.
- [ ] **Live smoke:** SKIPs cleanly when `OPIK_API_KEY` is unset (7-style `-rs` skip reason) and PASSes
  (presence: spans exported, tokens on the Gemini span, cost present-or-tokens-only) when creds are in
  `.env`.
- [ ] Hermetic under `filterwarnings=["error"]` run alone (no leaked async tasks, no unclosed spans;
  `logfire.testing` provider isolation clean); `make ci` green infra-less (live smoke skipped).
- [ ] The module docstring documents the feature end to end, naming REAL vs FAKED boundaries.

## Out of scope

- New product code (all in 091/092/093).
- A deployed/remote-stack proof of headless run-level nesting (documented ceiling, 093).
- Any Opik-side (server) content assertion beyond export-succeeded + span-attribute presence.

## Log
