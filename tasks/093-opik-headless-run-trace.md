---
id: 093-opik-headless-run-trace
feature: opik-observability
status: pending
---

# Opik observability — headless run root span (thread_id = Kitaru exec_id)

Tags: `observability`, `opik`, `runtime`, `headless`
Depends on: #091
Blocks: #094, #095

## Scope

Trace headless `decode run` (bypass + HITL) so each run is ONE trace (ADR-0014): `init_tracing()`
runs inside the flow — AFTER `_config_from_secret_store()` so a secret-store-hydrated `OPIK_API_KEY`
is honored — and a root span wraps `durable_agent.run_sync(task)` with
`thread_id = kitaru.current_execution_id()` (verified top-level export in the installed kitaru 0.18;
returns the active execution id inside a flow scope).

- **Init inside the flow** — in both `run_agent_task` (`runtime/flow.py:456`) and
  `run_agent_task_hitl` (`runtime/flow.py:617`), call `observability.init_tracing()` inside the
  `with _config_from_secret_store(), _sandbox_proxy(...):` block, before opening the root span.
  Idempotent, so re-running per flow is safe; when `OPIK_API_KEY` is absent it is a no-op (byte-
  identical headless run). Keep the import local to the flow module — the REPL path already never
  imports `runtime.flow`.
- **Root span per run** — wrap the `durable_agent.run_sync(task, deps=deps)` call in
  `with observability.root_span("decode_run", thread_id=kitaru.current_execution_id()):`
  (`"decode_run_hitl"` for the HITL flow). `current_execution_id()` is read inside the flow body where
  the execution scope is active.
- **Honest limitation (document, do not over-claim).** Under `checkpoint_strategy="calls"` (the
  default) each model call runs in its own `asyncio.run` loop on a worker thread
  (`factory._flow_mode_http_client`), so OTel contextvars may not propagate into those loops — the
  run-root span groups best-effort and some model spans may export as sibling traces rather than
  nested children. Global instrumentation still emits every model/tool span with tokens regardless of
  thread. Record this as a ceiling (mirrors ADR-0008/0013 honest scoping); the offline `FunctionModel`
  path (which runs in-process on one loop) may nest fully — assert what is true offline and document
  the real-provider caveat.
- **No stdout pollution** — headless surfaces the activation via `init_tracing()`'s single INFO log
  line (goes to the log file), NOT stdout, so a piped `decode run` stays exactly the agent's answer.

## Acceptance Criteria

*(Hermetic, kitaru-`skipif`-guarded like `test_runtime_capstone.py`: patch the `_build_runtime_agent`
/ `_build_hitl_runtime_agent` seam to inject a `FunctionModel`-scripted `KitaruAgent`, drive the REAL
`@flow` + adapter offline, and assert spans with `logfire.testing`.)*

- [ ] A bypass `decode run` flow with tracing active opens ONE root span named `decode_run` whose
  `thread_id` equals the run's `current_execution_id()`; the run's model/tool calls emit spans
  carrying `gen_ai.usage.*` tokens.
- [ ] The HITL flow opens a `decode_run_hitl` root span with the same `thread_id` contract.
- [ ] `init_tracing()` is invoked INSIDE the flow, after `_config_from_secret_store()` — proven by a
  test where `OPIK_API_KEY` lives only in a (stubbed) secret-store hydration and tracing still
  activates.
- [ ] **Inactive path:** with no `OPIK_API_KEY`, both flows emit ZERO spans and the run is byte-
  identical (same stdout answer, same exit code, no stdout tracing line).
- [ ] Offline nesting is asserted to the extent the single-loop `FunctionModel` path allows; the
  per-call-worker-thread caveat for a real provider is documented in the module/flow docstring.
- [ ] `make ci` green with no key/network; the kitaru-skipif keeps it green where kitaru is absent.
- [ ] Unit tests mirror the touched `runtime/flow.py` seams 1:1.

## Out of scope

- The REPL turn span (092).
- Making run-level nesting robust against Kitaru's per-call worker threads (documented ceiling).
- Docs prose / manual-QA rows (094) and the capstone / live smoke (095).

## Log
