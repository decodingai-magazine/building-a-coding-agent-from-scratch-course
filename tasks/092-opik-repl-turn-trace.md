---
id: 092-opik-repl-turn-trace
feature: opik-observability
status: pending
---

# Opik observability — per-turn root span in the REPL (thread_id = session id)

Tags: `observability`, `opik`, `tui`, `agent-loop`
Depends on: #091
Blocks: #094, #095

## Scope

Wire tracing into the interactive REPL so every turn is ONE trace (ADR-0014): a root span wraps ALL
the turn's legs — including a gated tool's approve/resume leg, so turn latency honestly includes the
gate wait — with `thread_id = session id` so Opik groups a session's turns into one conversation
thread. The nested model/tool spans come free from the global `instrument_pydantic_ai()` (task 091)
because the same asyncio task drives the whole turn.

- **Init + startup line** — call `observability.init_tracing()` once, early in
  `decode.tui.app.run_app` (before the agent is built). When it returns `True`, emit ONE console line
  through the existing render path near the startup banner (`tui/app.py:1103` area), styled like the
  sandbox lines: `Decode - Opik tracing on (project 'decode').`. When `False`, print nothing
  (byte-identical to today).
- **Thread id into the handler** — add `session_id: str | None = None` to
  `AgentTurnHandler.__init__` (`agent/loop.py:114`) and pass `session_log.session_id` from
  `tui/app.py` (the id already exists at `context/session_log.py:77-88`, wired at
  `tui/app.py:1050-1056`). Keep it optional so a headless/test handler with no session log is
  unaffected.
- **Root span per turn** — in `AgentTurnHandler.__call__` (`agent/loop.py:157`) wrap the entire
  `while True` body in `with observability.root_span("chat_turn", thread_id=self._session_id):`.
  One `__call__` == one harness turn == one `TurnStarted`/`TurnFinished` (verified: `Runner._run_turn`
  drives one async generator per turn and calls `agen.aclose()` deterministically in its `finally`,
  `harness/runner.py:153-190`); follow-ups chained at `WOULD_STOP` continue inside the same `__call__`,
  so they ride the same trace. **Careful async-generator detail:** the span is entered before the
  first `yield` and must close exactly once on normal return, on exception, AND on abort — the
  runner's `aclose()` throws `GeneratorExit` into the suspended `yield`, which unwinds the `with`
  (do NOT rely on GC — the runner closes deterministically). Contextvars are task-shared across the
  `yield`s, so the pydantic-ai model/tool spans emitted during each leg nest under this root.
- **No behavior change when inactive** — `root_span` is a `nullcontext` when tracing is off, so a
  no-key REPL is byte-identical.

## Acceptance Criteria

*(All hermetic, no network: drive the REAL `build_agent()` + `Runner` + `AgentTurnHandler` + gate via
`agent.override(model=FunctionModel(...))` and assert spans with `logfire.testing`'s in-memory
exporter; fake `opik_api_key` for activation only.)*

- [ ] A single turn produces exactly ONE root span named `chat_turn` carrying a `thread_id` attribute
  equal to the session id; the model-request (`chat`) span(s) and tool span(s) for that turn nest
  under it.
- [ ] A gated tool (approve then resume) stays inside the SAME root span — one trace spans the pause
  and the resume leg (turn latency includes the gate wait).
- [ ] An LLM span carries token usage (`gen_ai.usage.input_tokens` present and > 0 for the scripted
  model).
- [ ] Two turns in one session emit two root spans that share the same `thread_id` (session id).
- [ ] A compaction call triggered inside a turn nests under that turn's root span (rides free via
  global instrumentation).
- [ ] **Abort safety:** a turn aborted mid-flight (runner sets `_abort`, calls `aclose()`) closes the
  root span exactly once — no leaked/unclosed span, asserted under `filterwarnings=["error"]`.
- [ ] **Inactive path:** with no `opik_api_key`, a full turn emits ZERO spans and the REPL launch
  prints no tracing line — byte-identical to before.
- [ ] The startup console line appears exactly once when tracing is active and never when inactive.
- [ ] `make ci` green with no key/network; unit tests mirror the touched modules.

## Out of scope

- The headless `decode run` root span (093).
- Subagent-nesting assertions and the live Opik smoke (095).
- Docs prose / manual-QA rows (094).

## Log
