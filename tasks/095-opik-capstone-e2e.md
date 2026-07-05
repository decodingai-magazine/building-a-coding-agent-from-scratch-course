---
id: 095-opik-capstone-e2e
feature: opik-observability
status: done
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

- [x] **Hermetic:** the span-tree slice passes with no key/network and proves — turn root span with
  `thread_id`=session id, nested chat/tool spans, tokens on the LLM span, subagent child spans nested
  in the parent turn with child token usage, compaction nesting, and the zero-spans no-op path (each
  mutation-proof: e.g. removing the root span or the activation guard fails a test).
- [x] **Subagent closure:** an explicit assertion that a child `agent(...)` run's spans are children
  of the parent turn's root (ADR-0013 §9 fulfilled) with child token counts present.
- [x] **Live smoke:** SKIPs cleanly when `OPIK_API_KEY` is unset (7-style `-rs` skip reason) and PASSes
  (presence: spans exported, tokens on the Gemini span, cost present-or-tokens-only) when creds are in
  `.env`.
- [x] Hermetic under `filterwarnings=["error"]` run alone (no leaked async tasks, no unclosed spans;
  `logfire.testing` provider isolation clean); `make ci` green infra-less (live smoke skipped).
- [x] The module docstring documents the feature end to end, naming REAL vs FAKED boundaries.

## Out of scope

- New product code (all in 091/092/093).
- A deployed/remote-stack proof of headless run-level nesting (documented ceiling, 093).
- Any Opik-side (server) content assertion beyond export-succeeded + span-attribute presence.

## Log

### [SWE] 2026-07-05 16:10 — Implementation

**Files modified**
- `tests/integration/test_observability_capstone.py` — NEW; the Opik-observability capstone. Module
  docstring is the milestone's living doc (REAL vs FAKED boundaries). 4 always-run hermetic tests +
  1 skipif-guarded live smoke.
- `tests/unit/decode/runtime/test_flow_tracing.py` — added the 093-QA follow-up (1 test) + an
  `active_tracing` fixture + extended `_reset_tracing_state` to save/restore global instrumentation.
- No product code (all shipped in 091-094); no new deps (`uv lock --check` unchanged, 155 pkgs).

**Tests**
- Unit: 1491 passing, 0 failing (`make pre-commit` — +1 vs 093's 1490).
- Integration: 120 passing, 1 skipped (`make integration-tests` — +4 hermetic vs 116; live smoke skips).
- Both slices under `filterwarnings=["error"]`, no key, no network. Cross-file leak check (capstone +
  both opik trace files + flow_tracing, BOTH orders) → 22 passed each way — no global tracing-state leak.

**Test inventory (what each proves)**
- `test_observability_capstone.py`
  - `test_subagent_child_spans_nest_in_the_parent_turn_trace_with_child_token_usage` — **the flagship
    (the one assertion no other file makes)**: a parent turn fans out `agent(...)`; the child model AND
    tool (glob) spans nest INSIDE the parent turn's `chat_turn` trace, with the CHILD's
    `gen_ai.usage.input_tokens` > 0 on the child LLM span. Closes ADR-0013 §9. Child/parent told apart
    structurally (a child span descends through the parent's `agent` `running tool` span).
  - `test_full_turn_is_one_chat_turn_tree_with_nested_spans_and_usage` — the integrated living-doc tree:
    one `chat_turn` root (`thread_id` = the REAL `SessionLog.session_id`, wired as run_app does) →
    nested `chat`/`running tool` spans → tokens on the LLM span; render path exercised on every event.
  - `test_in_turn_compaction_nests_under_the_turn_root` — an in-turn compaction summarizer call rides
    free under the same global instrumentation and nests under the turn root.
  - `test_untraced_turn_is_a_noop_zero_spans_and_byte_identical_events` — the no-op mutation-proof:
    real `init_tracing()` returns **False** with no key AND the identical turn emits ZERO spans; then
    the same turn traced-ON emits spans yet the emitted event stream is byte-identical (tracing is
    transparent). Fails if the `if not key: return False` guard is dropped.
  - `test_live_opik_export_smoke` — skipif both keys unset; ONE real Gemini turn + real Opik OTLP
    export; asserts no OTLP-exporter ERROR logged (export succeeded) + `gen_ai.usage.*`/`request.model`/
    `system` on the real Gemini leaf span. Cost is Opik server-side (UI), out of scope here.
- `test_flow_tracing.py::test_bypass_flow_raise_with_tracing_active_closes_decode_run_span_once` — the
  093-QA follow-up: a raising model leg with tracing active closes the `decode_run` span EXACTLY once
  (one exported span, `logfire.level_num==17`, `exception` event) and the error propagates unchanged.

**Acceptance criteria**
- [x] Hermetic slice proves root/nesting/tokens/subagent/compaction/no-op, each mutation-proof —
  `test_observability_capstone.py` (4 tests); the flagship span tree verified by hand (parent vs CHILD
  classification, child tokens 53/57 > 0).
- [x] Subagent closure explicit — the flagship asserts child model+tool spans share the root trace_id,
  `parent is not None`, and child `gen_ai.usage.input_tokens` > 0 (ADR-0013 §9).
- [x] Live smoke SKIPs cleanly (`-rs`: "OPIK_API_KEY and GEMINI_API_KEY must both be set …") and its
  PASS path is structurally sound (see Notes for the real-Gemini attribute proof).
- [x] Hermetic passes run alone under `filterwarnings=["error"]` (4 passed, 1 skip) + full suite green;
  `make ci` links all green (uv lock --check, format-check, lint-check, 1491 unit, 120 integration).
- [x] Module docstring documents the feature end-to-end, naming REAL (`build_agent`, `Runner`/
  `AgentTurnHandler`, gate, `render_event`, `SessionLog`, global `instrument_pydantic_ai`,
  `init_tracing`) vs FAKED (`FunctionModel`, `logfire.testing` exporter, fake key).

**Evidence**
```
$ uv run pytest tests/integration/test_observability_capstone.py tests/unit/decode/runtime/test_flow_tracing.py -rs -q
....s.......                                                              [100%]
SKIPPED [1] …:630: OPIK_API_KEY and GEMINI_API_KEY must both be set for the live Opik export smoke
11 passed, 1 skipped

$ make pre-commit          # format-check + lint-check + unit
1491 passed in 109.84s
$ make integration-tests
120 passed, 1 skipped in 365.59s
$ uv lock --check
Resolved 155 packages in 2ms

# manual e2e — ONE REAL Gemini turn through the real stack, tracing active (in-memory tap, no OPIK):
chat_turn roots: 1 | thread_id: 00000000-0000-0000-0000-0000000000e5
real Gemini chat spans: ['chat gemini-2.5-flash']
  gen_ai.usage.input_tokens = 18007
  gen_ai.request.model = 'gemini-2.5-flash'
  gen_ai.system       = 'google-gla'
```

**Notes**
- **End-to-end proof (Step 7).** I have a real `GEMINI_API_KEY` but NO `OPIK_API_KEY`, so the live Opik
  smoke SKIPS. To de-risk the half the skip hides, I drove ONE real Gemini turn through the real stack
  with tracing active (in-memory tap, no export) — output above. It confirms my live-smoke attribute
  assertions are exactly right against a **real** provider: `gen_ai.usage.input_tokens` > 0,
  `gen_ai.request.model == 'gemini-2.5-flash'`, `gen_ai.system == 'google-gla'` (all truthy). Only the
  OPIK-export "no error logged" half is unproven on this machine — left to the Tester if they hold an
  OPIK key; it is structurally sound (real `init_tracing()` builds the OTLP exporter; `force_flush`
  pushes; caplog scans `opentelemetry.exporter.*` for ERROR). Cost hygiene: the live smoke is ONE turn.
- **`logfire.force_flush()` returns `False` even on a clean flush** (verified empirically): logfire's
  internal `CheckSuppressInstrumentationProcessorWrapper.force_flush()` returns False, so the return
  value is NOT a reliable success signal. The live smoke therefore asserts the task's stated
  alternative — **no OTLP-exporter ERROR logged** — and only records `force_flush()`'s bool. Documented
  in the test docstring.
- **093-QA follow-up folded in** (pre-approved): the raise-unwind-with-tracing-active test in
  `test_flow_tracing.py`, adapting the 093 Tester's "PROBE 1" template. Its `active_tracing` fixture +
  the `_reset_tracing_state` instrumentation save/restore are new there; the existing 6 tests still pass.
- **No architectural fork.** All names, thread_id sources, REAL/FAKED boundaries, and the escape-hatch
  cost framing were fixed by ADR-0014 + ADR-0013 §9 + the task. `docs/` untouched (PA-owned).
- Task kept in `tasks/` with `status: in-progress` (091-093 convention: archive move + `status: done`
  happen in the commit step, after Tester PASS). NOT COMMITTED — handing to the Tester first.

### [Tester] 2026-07-05 17:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make pre-commit` → format-check + lint-check + 1491 unit passed in 109.10s)
- Unit tests: 1491 passed / 0 failed
- Integration tests: 120 passed / 0 failed / 1 skipped (live smoke — OPIK_API_KEY absent)
- Warnings: 0 (`filterwarnings=["error"]`; capstone alone → 4 passed, 1 skipped, no warnings)
- `uv lock --check`: 155 packages, no drift (no new deps)

**E2E adversarial pass** (test-only task → mutation-testing the new assertions for non-vacuousness; each mutation reverted byte-exact and re-confirmed green)
- Happy path: `pytest tests/integration/test_observability_capstone.py -rs` → 4 passed, 1 skipped; skip reason renders under `-rs`: "OPIK_API_KEY and GEMINI_API_KEY must both be set for the live Opik export smoke" (PASS)
- Break path 1 (flagship teeth — root span): `root_span` forced to `nullcontext` w/ instrumentation on → flagship RED at `len(roots)==1` (`0==1`; pydantic-ai spans still emit, zero `chat_turn` roots → shared-trace invariant collapses). Load-bearing (PASS/caught)
- Break path 2 (flagship teeth — child tokens): child-tokens assertion pointed at `gen_ai.usage.input_tokens_BOGUS` → RED `[None,None,None,None]`. Assertion genuinely reads the real attr (PASS/caught)
- Break path 2b (independent span-tree proof): dumped the real tree — one trace, `chat_turn(sid=1,pid=None,thread_id=session)` → parent `agent run(3)` → parent `chat(50t)` + two `agent`-tool `running tool(7,9)` → each CHILD `agent run` → child `chat(53t,57t)` + child `glob running tool`. `_descends_through_tool` partition genuine (parent spans child=False, child spans child=True). ADR-0013 §9 closure real, not vacuous (PASS)
- Break path 3 (no-op teeth, half 1 — init False): invert guard `if not key:`→`if key:` → RED at `assert init_tracing() is False` (`True is False`) (PASS/caught)
- Break path 3b (no-op teeth, half 2 — zero spans): instrument on the no-key path while keeping `return False` → RED at `assert exported_spans == []` (6 spans leak) while `is False` stays green → both halves independently mutation-proof (PASS/caught)
- Break path 4 (093 follow-up teeth): flip expected error level 17→9 → RED (`assert 17 == 9`); span genuinely records the exception (error level + `exception` event); combined w/ `pytest.raises` + `_exception_carries` it targets close-once-plus-record-exception (PASS/caught)
- Isolation: capstone + `test_opik_repl_trace` + `test_opik_headless_trace` + `test_flow_tracing`, BOTH orders (forward + reverse) → 22 passed, 1 skipped each way; no-op test's `_active is False` sentinel holds even after active-tracing files run first (no tracing-state leak). NB: no `pytest-randomly` installed → deterministic order, so two explicit orders is the right isolation check (PASS)
- Live smoke structural review (OPIK_API_KEY genuinely absent from env + `.env` — verified quietly, never printed; GEMINI present): (a) skip renders cleanly; (b) "export succeeded" scans `opentelemetry.exporter.*` at ERROR — confirmed the OTLP-HTTP exporter logs failures via `getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter").error("Failed to export span batch code: %s…")`, so a 401/dead endpoint WOULD be caught; (c) the three attr keys it reads (`gen_ai.request.model`/`gen_ai.system`/`gen_ai.usage.input_tokens`) are real keys the instrumentation emits (hermetic dump: `'function:…'`/`'function'`/50). Real-Opik-export half unproven-on-this-machine — the skipif contract IS the AC → not a FAIL

**Acceptance criteria**
- [x] PASS — Hermetic slice proves root+thread_id / nested chat+tool / tokens / subagent child nesting+tokens / compaction nesting / zero-spans no-op, each mutation-proof — 4 hermetic tests pass alone under `filterwarnings=["error"]` (no key/network); break paths 1–3b above prove the root span, child-token, and activation-guard (both halves) assertions all have teeth
- [x] PASS — Subagent closure explicit (ADR-0013 §9) — flagship `test_subagent_child_spans_nest_in_the_parent_turn_trace_with_child_token_usage` asserts child model+tool spans share the root `trace_id`, `parent is not None`, child `input_tokens > 0`; span-tree dump confirms child `chat(53t,57t)` + child `glob` descend through the parent's `agent` `running tool`, all in the one `chat_turn` trace
- [x] PASS — Live smoke SKIPs cleanly (`-rs` reason at :630) and its PASS path is structurally sound (right logger, real attr keys, would catch a 401); real-Opik half honestly unproven on this machine (no OPIK key) — skipif contract is the AC
- [x] PASS — Hermetic clean under `filterwarnings=["error"]` alone (4 passed, 0 warnings) + isolation clean both orders (22/22); `make ci` infra-less green: `uv lock --check` (155 pkgs), format-check, lint-check, 1491 unit, 120 integration (live smoke skipped)
- [x] PASS — Module docstring names REAL (`build_agent`, `Runner`/`AgentTurnHandler`, gate, `render_event`, `SessionLog`, global `instrument_pydantic_ai`, `init_tracing`) vs FAKED (`FunctionModel`, `logfire.testing` exporter, fake key) end-to-end; honest disclosure that the hermetic slice forces `_active`+direct-instrument (not `init_tracing`) to avoid a network flush — a documented 092/093 fidelity trade

**Evidence**
```
$ uv run pytest tests/integration/test_observability_capstone.py -rs -q
....s
SKIPPED [1] .../test_observability_capstone.py:630: OPIK_API_KEY and GEMINI_API_KEY must both be set for the live Opik export smoke
4 passed, 1 skipped in 1.17s

$ make pre-commit
1491 passed in 109.10s
$ make integration-tests
120 passed, 1 skipped in 361.25s

# span-tree dump (flagship scenario, hermetic): one trace, one chat_turn root
chat_turn(1,pid=None,thread_id=…e5) → agent run(3) → chat(50t) · running tool(7)→CHILD agent run(11,110t)
  → chat(15,53t) child · running tool(19,glob) child · chat(23,57t) child   [+ symmetric child 2: 53t/57t]

# isolation, both orders
ORDER A (capstone→repl→headless→flow):  22 passed, 1 skipped
ORDER B (reversed):                     22 passed, 1 skipped
```

**Other issues found**
- None blocking. Notes for the record (PASS-with-note, not FAILs): (1) the always-run span tests fake activation via `tracing._active=True` + direct `instrument_pydantic_ai` rather than calling `init_tracing()` — honestly documented (docstring §33-36), sound rationale (a real `logfire.configure` would replace the `capfire` exporter and could flush to the network), matches 092/093; the real `init_tracing()` IS exercised in the no-op guard test (mutation-proofed) and the live smoke. (2) SWE pre-checked the AC boxes; I re-verified all five as genuinely passing, so the `[x]` marks are accurate. (3) Hygiene clean — `git status` shows only the 095 set (capstone file, `test_flow_tracing.py`, `tasks/095`); `docs/` and `tasks/future/` untouched.

**VERDICT: PASS**

### [PA] 2026-07-05 18:20 — Acceptance Review (feature: opik-observability, tasks 091-095)

**VERDICT: ACCEPT**

Feature-level user-POV review of the whole Opik monitoring feature on PR #27. Walked the user story
end to end — "set `OPIK_API_KEY`, relaunch, every turn/run is one Opik trace grouped by session; unset
→ byte-identical" — reading the shipped code, the README + AGENTS.md contract, and the grooming
artifacts, and exercising the surface with my own eyes (not just the Tester logs).

**Concrete evidence per AC-cluster**
- **Enablement + byte-identical no-op** — live `printf '/quit' | GEMINI_API_KEY=fake OPIK_API_KEY=fake
  uv run decode` prints `Decode - Opik tracing on (project 'decode').` before the banner; key unset →
  0 occurrences, no line. `OPIK_PROJECT_NAME=my-team` → `project 'my-team'` (the custom name flows
  through). Settings `config/settings.py:138-144` = the 4 ADR fields/defaults; `.env.example:57-70`
  fully commented (the `changeme` regression is dead). Capstone
  `test_untraced_turn_is_a_noop_zero_spans_and_byte_identical_events` mutation-proofs the guard.
- **One Trace per turn, grouped by session** — `agent/loop.py:190` wraps the whole `while True` in
  `root_span("chat_turn", thread_id=self._session_id)`; capstone flagship + full-tree tests prove root
  + nested chat/tool spans + two-turns-one-thread; abort + exception close-once regressions present.
- **One Trace per headless run, grouped by exec_id** — `runtime/flow.py:523,529` init AFTER
  secret-store hydration + `root_span("decode_run"/"decode_run_hitl",
  thread_id=current_execution_id())`; hydration-ordering + inactive-zero-spans mutation-proofed.
- **Tokens + cost** — `gen_ai.usage.input_tokens > 0` on LLM spans (hermetic + the Tester's real-Gemini
  probe: 18007 tokens, `request.model=gemini-2.5-flash`, `system=google-gla`); cost is Opik server-side
  for priced models, honestly qualified in docs, with the `OpikSpanProcessor` escape hatch (ADR-0014 §8).
- **Subagent closure (ADR-0013 §9)** — capstone flagship nests child model+tool spans in the parent
  `chat_turn` trace with child tokens; ADR-0013 §9 + Consequences carry the append-only closure.
- **Docs + grooming artifacts** — README Monitoring section + AGENTS.md E2E row/headless note; ADR-0014
  Accepted, 5-section Nygard + coloured Mermaid, decisions realized 1:1; 4 glossary rows
  (Trace/Span/Thread (Opik)/Observability) drift-free vs shipped identifiers.
- **Hygiene** — re-ran `test_observability_capstone.py -rs` → 4 passed, 1 skipped (live smoke, clean
  skip reason); diff scoped to the feature only (no `docs/notes/`, no stray files, `logfire` the sole
  new top-level dep).

**Product-judgment rulings (all ACCEPTABLE for M10 — documented ceilings, disclosed in the user-facing
contract, core value preserved)**
1. `--resume` → new Opik Thread — out of scope by design; disclosed in README + AGENTS.md.
2. HITL pause closes the run span, resume opens a fresh one under the same Thread — inherent to durable
   pause/resume; grouped by exec_id; disclosed in the AGENTS.md row.
3. Live-export half unproven without real creds — the skipif IS the groomed AC; the Gemini-attr half is
   proven against a real provider; cost is server-side with a documented escape hatch. Honest, not a gap.
4. Headless real-provider sibling spans under `checkpoint_strategy="calls"` — documented ceiling
   (`flow.py:48-57`, ADR-0014 Consequences, AGENTS.md row); tokens ride every span regardless, so the
   value is preserved.

**Non-blocking note (not a reject; for the PR Reviewer's eye)** — the README headline "One Trace per
`decode run`" does not restate the headless real-provider sibling caveat that the AGENTS.md row and
ADR-0014 do; accurate for the REPL + offline path and honestly qualified elsewhere, so a prose-softening
nit at most.

All acceptance criteria verified from the user's perspective. User satisfaction guaranteed. Hand off to
the PR Reviewer.
