---
id: 093-opik-headless-run-trace
feature: opik-observability
status: done
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

- [x] A bypass `decode run` flow with tracing active opens ONE root span named `decode_run` whose
  `thread_id` equals the run's `current_execution_id()`; the run's model/tool calls emit spans
  carrying `gen_ai.usage.*` tokens.
- [x] The HITL flow opens a `decode_run_hitl` root span with the same `thread_id` contract.
- [x] `init_tracing()` is invoked INSIDE the flow, after `_config_from_secret_store()` — proven by a
  test where `OPIK_API_KEY` lives only in a (stubbed) secret-store hydration and tracing still
  activates.
- [x] **Inactive path:** with no `OPIK_API_KEY`, both flows emit ZERO spans and the run is byte-
  identical (same stdout answer, same exit code, no stdout tracing line).
- [x] Offline nesting is asserted to the extent the single-loop `FunctionModel` path allows; the
  per-call-worker-thread caveat for a real provider is documented in the module/flow docstring.
- [x] `make ci` green with no key/network; the kitaru-skipif keeps it green where kitaru is absent.
- [x] Unit tests mirror the touched `runtime/flow.py` seams 1:1.

## Out of scope

- The REPL turn span (092).
- Making run-level nesting robust against Kitaru's per-call worker threads (documented ceiling).
- Docs prose / manual-QA rows (094) and the capstone / live smoke (095).

## Log

### [SWE] 2026-07-05 14:20 — Implementation

**Files modified**
- `src/decode/runtime/flow.py` — wired Opik tracing into BOTH headless flows: `import current_execution_id` +
  `from decode import observability`; each `@flow` body now calls `observability.init_tracing()` inside the
  `with _config_from_secret_store(), _sandbox_proxy(...):` block (after secret-store hydration) and wraps
  `run_sync` in `observability.root_span("decode_run" / "decode_run_hitl", thread_id=current_execution_id())`.
  Module docstring gained an Opik-tracing paragraph documenting the honest per-call-worker-thread ceiling.
- `tests/unit/decode/runtime/test_flow_tracing.py` — NEW; mirrors the two touched flow seams 1:1 (6 tests).
- `tests/integration/test_opik_headless_trace.py` — NEW; span-shape capstone through the real flow (4 tests).

**Tests**
- Unit: 1490 passing, 0 failing (`make pre-commit` — the new file adds 6). Integration: 116 passing (the new
  file adds 4). New files alone: 10 passing. All under `filterwarnings=["error"]`, no key, no network.

**Acceptance criteria**
- [x] Bypass `decode_run` root, `thread_id == current_execution_id()`, model/tool spans carry `gen_ai.usage.*`
  — `test_opik_headless_trace.py::test_bypass_run_is_one_decode_run_root_with_nested_spans_and_usage` +
  `test_flow_tracing.py::test_bypass_flow_inits_tracing_then_opens_decode_run_root_keyed_on_exec_id`.
- [x] HITL `decode_run_hitl` root, same `thread_id` contract — `test_hitl_run_is_one_decode_run_hitl_root_...`
  + `test_hitl_flow_inits_tracing_then_opens_decode_run_hitl_root_keyed_on_exec_id`.
- [x] `init_tracing()` inside the flow, after `_config_from_secret_store()` — `test_flow_tracing.py::
  test_init_tracing_runs_after_secret_store_hydration` (OPIK_API_KEY only in a real Kitaru secret; the flow
  hydrates it and the exporter is built with the secret key; OTLP boundary mocked → no network).
- [x] Inactive path: no key → ZERO spans + byte-identical — `test_inactive_bypass_run_...` /
  `test_inactive_hitl_run_...` (same output, no spans) + offline e2e stdout capture.
- [x] Offline nesting asserted; real-provider caveat documented — nesting asserted in the two span-shape
  tests; caveat in the `flow.py` module docstring (ADR-0014 §4-5 honest ceiling).
- [x] `make ci` green with no key/network — `uv lock --check` (no dep drift), format-check, lint-check,
  1490 unit + 116 integration all green; kitaru-skipif guard on the integration file.
- [x] Unit tests mirror the touched `runtime/flow.py` seams 1:1 — `test_flow_tracing.py`.

**Evidence**
```
$ uv run pytest tests/unit/decode/runtime/test_flow_tracing.py tests/integration/test_opik_headless_trace.py
............... 10 passed in 16.76s

$ make pre-commit          # format-check + lint-check + unit
1490 passed in 108.47s
$ make integration-tests
116 passed in 360.66s      # incl. test_opik_headless_trace.py .... and unchanged runtime/repl capstones
$ uv lock --check
Resolved 155 packages in 3ms
```

Offline empirical finding (the discovery the task asked for):
```
# real bypass flow, FunctionModel, checkpoint_strategy="calls", root_span opened in the flow body:
decode_run (thread_id=<exec_id>)          parent=None      <- ONE trace, ONE root
└── agent run                              parent=decode_run
    ├── chat function:model_fn:            parent=agent run   gen_ai.usage.input_tokens=51
    ├── running tool (read)                parent=agent run
    └── chat function:model_fn:            parent=agent run   gen_ai.usage.input_tokens=54
# distinct trace ids: 1 — offline nesting HOLDS FULLY (single loop, no HTTP worker threads).
```
End-to-end (real flow, tracing active, stdout captured): user-visible stdout == `'hello from the headless
agent\n'` (exactly the answer); the `Opik tracing active — project=decode target=cloud` line went to the
`decode.observability.tracing` LOG, never stdout.

**Notes**
- **Empirical nesting: NESTED (not sibling), offline.** With a `FunctionModel` there is no HTTP call, so the
  model legs run in-process on the flow's loop and OTel context propagates — one `decode_run` trace with the
  model/tool spans nested. Asserted offline. The real-provider caveat (`checkpoint_strategy="calls"` runs each
  model call in its own `asyncio.run` loop on a worker thread via `factory._flow_mode_http_client`, so context-
  vars may not cross and some model spans may export as siblings) is DOCUMENTED in `flow.py`, not asserted —
  tokens ride every span regardless of thread. Mirrors the ADR-0008/0013 honest-scoping precedent.
- **No stdout pollution — verified by real invocation.** The change adds NO `print`/`click.echo`; only
  `init_tracing()`'s one INFO log. When tracing is off (default) `init_tracing` returns early with no log line
  → byte-identical. When on, that single INFO line goes to decode's log. ZenML's own console handler
  (`_ZenMLStdoutStream`) echoes INFO+ records during ANY flow (pre-existing — it already echoes zenml's
  "Checkpoint …" lines); my one activation line rides that existing behavior identically and is gated behind
  opt-in (`OPIK_API_KEY` set). decode's CLI stdout write stays exactly `click.echo(answer)`.
- Invariants re-checked by real import: the REPL path (`decode.cli`) still never imports kitaru; `decode.
  observability` imports neither `decode.runtime` nor kitaru (no reverse coupling — runtime→observability only).
- `docs/` untouched (user-owned). No new deps (`uv lock` unchanged; logfire arrived in 091). No architectural
  fork encountered — the wiring points, names, and `thread_id` source were all fixed by ADR-0014 §4-5 + the task.
- Task file kept in `tasks/` with `status: in-progress`; per the existing convention here (091/092 sit in
  `tasks/` as `status: done`, not `tasks/done/`), leaving the archive move + `status: done` for the commit step
  after Tester PASS. NOT COMMITTED — handing to the Tester first.

### [Tester] 2026-07-05 15:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 183 files clean; `ruff check` all pass; `uv lock --check` 155 pkgs, no drift)
- Unit tests: 1490 passed / 0 failed
- Integration tests: 116 passed / 0 failed (incl. the 4 new `test_opik_headless_trace.py`)
- New files alone: 10 passed (6 unit + 4 integration)
- Warnings: 0 (`filterwarnings=["error"]` — any warning would fail the run)

**E2E adversarial pass** (six probes; all green)
- Happy path (bypass, tracing active): real `@flow` → ONE `decode_run` root, `thread_id==exec_id`, nested `chat`/`running tool` spans, leaf model span `gen_ai.usage.input_tokens>0` (PASS)
- Happy path (HITL, tracing active): real HITL `@flow` → ONE `decode_run_hitl` root, same `thread_id` contract, no bypass root leaks (PASS)
- Break path 1 (exception unwind — 092's lesson): injected a `FunctionModel` model leg that RAISES `Boom`; drove the real bypass flow with tracing active. Result: `.run()` re-raises `Boom` UNCHANGED (chain `['Boom','ExceptionGroup']`); exactly ONE `decode_run` span; it recorded the error (`logfire.level_num=17`, `events=['exception']`); `parent is None`. Span closes exactly once (a `with`-statement guarantee) and the error propagates unmangled (PASS)
- Break path 2 (state edge — HITL pause): scripted an unanswered `ask_user` wait with `runtime_wait_timeout_s=2`; the flow paused (`paused=True, output=None`) and emitted exactly ONE `decode_run_hitl` root on the pause path — no crash, no double-close, every span carries valid trace/span ids (PASS)
- Break path 3 (key hygiene teeth): forced the OPIK key onto a span attribute and proved `exported_spans_as_dict()` repr surfaces it → an attribute/payload scan HAS teeth. The shipped key-safety tests scan the two real leak surfaces (caplog + Kitaru `run.config` payload); `root_span` only ever sets `thread_id`, so the key structurally cannot reach a span attribute — no coverage gap (PASS)
- Break path 4 (mutation kills — teeth of the new tests, each reverted byte-exact; flow.py md5 unchanged `445765eb…`):
  - (a) delete bypass `init_tracing()` → 3 unit red (`…inits_tracing_then_opens_decode_run_root…`, `…runs_after_secret_store_hydration`, `…logs_do_not_carry_the_opik_secret_value`); HITL tests stay green (per-flow coverage proven)
  - (b) `thread_id=current_execution_id()` → constant → 4 red (2 unit seam + 2 integration span-shape thread_id asserts)
  - (c) move `init_tracing()` ABOVE `_config_from_secret_store()` → 2 unit red (the hydration-ordering proof + its log corollary) — the ordering AC has real teeth
- Break path 5 (cross-suite leak hunt): `test_opik_headless_trace + test_opik_repl_trace + test_runtime_capstone` in ONE process → 19 passed; REVERSED order → 19 passed. No leak of the global `_active` flag / pydantic-ai instrumentation across files
- Break path 6 (REPL purity): no module-level `import kitaru` in `decode/cli.py` (only one lazy `from kitaru.errors …` inside a function) or `decode/tui/`; importing `decode.cli` loads ZERO kitaru modules; `decode.observability` imports neither kitaru nor `decode.runtime`; flow.py's new `current_execution_id` + `from decode import observability` are module-local to the (lazily-imported) runtime package

**Acceptance criteria**
- [x] PASS — bypass ONE `decode_run` root, `thread_id==current_execution_id()`, model/tool spans carry `gen_ai.usage.*` — `test_opik_headless_trace.py::test_bypass_run_is_one_decode_run_root_with_nested_spans_and_usage`; mutation (b) proves the thread_id assert has teeth
- [x] PASS — HITL `decode_run_hitl` root, same `thread_id` contract — `test_opik_headless_trace.py::test_hitl_run_is_one_decode_run_hitl_root_with_the_same_thread_id_contract`
- [x] PASS — `init_tracing()` INSIDE the flow, after `_config_from_secret_store()` (key only in a real Kitaru secret) — `test_flow_tracing.py::test_init_tracing_runs_after_secret_store_hydration`; mutation (c) reds it when init is moved above the context
- [x] PASS — inactive path: no key → ZERO spans, byte-identical, no tracing line — `test_inactive_bypass_run_…` / `test_inactive_hitl_run_…` (spans == []) + `test_inactive_bypass_flow_never_opens_a_real_span` (`root_span` is a `nullcontext`, `logfire.span` never called). Byte-identical is structural: `init_tracing` returns before its `logger.info` when the key is empty, `root_span` is a `nullcontext`
- [x] PASS — offline nesting asserted; real-provider caveat documented — nesting asserted (model/tool spans share the root `trace_id`, `parent is not None`); caveat in `src/decode/runtime/flow.py:48-56` (the "Honest ceiling" paragraph)
- [x] PASS — `make ci` green with no key/network (ran each link: `uv lock --check`, format-check, lint-check, 1490 unit, 116 integration — all green); kitaru-`skipif` guard at `test_opik_headless_trace.py:73-77`
- [x] PASS — unit tests mirror the touched `runtime/flow.py` seams 1:1 — `tests/unit/decode/runtime/test_flow_tracing.py` (both bypass + HITL seams)

**Evidence**
```
$ uv run pytest tests/unit -q
1490 passed in 107.60s

$ uv run pytest tests/integration -q
116 passed in 372.56s

$ uv run pytest tests/unit/decode/runtime/test_flow_tracing.py tests/integration/test_opik_headless_trace.py -q
10 passed in 16.36s

# PROBE 1 (raise): PROBE1 raised type: Boom ; span names: ['chat function:_boom:', 'agent run', 'decode_run'] ; root level=17 events=['exception']
# PROBE 3 (pause): PROBE3 paused: True output: None ; span names: [... 'decode_run_hitl'] (exactly one)
# PROBE 5: forward 19 passed ; reversed 19 passed
```

**Other issues found** (all non-blocking)
- No committed regression test asserts the tracing-active exception unwind (run_sync raises → `decode_run` span closes once + error propagates). Behavior is correct (probe 1) and "close once" is a `with`-statement guarantee; the raise-unwind path is already covered for the `finally` reap in `test_executor_teardown.py::test_bypass_flow_reaps_the_executor_even_when_the_flow_errors`. NOT a blocker (093's ACs don't name unwind paths; that was 092's spec). Recommend a follow-up: add one raise-with-tracing-active test to `test_flow_tracing.py` (adapt probe 1) to harden against a future refactor that swaps the `with` for manual enter/exit.
- HITL pause emits a `decode_run_hitl` span that closes at the pause (not at eventual completion); a later resume opens a fresh span under the same `thread_id`. Inherent to durable pause/resume and grouped by thread in Opik — correct, noted for awareness.
- Active-path stdout cleanliness under a REAL provider (the "no stdout pollution" scope note) is explicitly out of scope for 093 and belongs to the 095 capstone/live smoke; the default inactive path (AC4) is pipe-clean by construction.

**VERDICT: PASS**

### [PA] 2026-07-05 18:20 — Acceptance Review

**VERDICT: ACCEPT** (feature-level; full AC-cluster evidence in the `tasks/095` acceptance log)

Verified from the user's POV as part of the opik-observability feature review on PR #27. Confirmed each
headless `decode run` opens one `decode_run` / `decode_run_hitl` root span keyed on the Kitaru exec_id
(`runtime/flow.py:523,529`), that `init_tracing()` runs INSIDE the flow AFTER `_config_from_secret_store()`
(so a secret-store `OPIK_API_KEY` is honored), and that activation surfaces only in the log so a piped
run stays pipe-clean. The two Tester notes ruled ACCEPTABLE for M10: the HITL pause/resume span split
and the real-provider sibling-span ceiling are both documented (`flow.py:48-57`, ADR-0014, AGENTS.md)
and tokens ride every span regardless. Hand off to the PR Reviewer.
