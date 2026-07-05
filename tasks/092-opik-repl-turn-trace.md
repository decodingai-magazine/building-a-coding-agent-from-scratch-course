---
id: 092-opik-repl-turn-trace
feature: opik-observability
status: done
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

- [x] A single turn produces exactly ONE root span named `chat_turn` carrying a `thread_id` attribute
  equal to the session id; the model-request (`chat`) span(s) and tool span(s) for that turn nest
  under it.
- [x] A gated tool (approve then resume) stays inside the SAME root span — one trace spans the pause
  and the resume leg (turn latency includes the gate wait).
- [x] An LLM span carries token usage (`gen_ai.usage.input_tokens` present and > 0 for the scripted
  model).
- [x] Two turns in one session emit two root spans that share the same `thread_id` (session id).
- [x] A compaction call triggered inside a turn nests under that turn's root span (rides free via
  global instrumentation).
- [x] **Abort safety:** a turn aborted mid-flight (runner sets `_abort`, calls `aclose()`) closes the
  root span exactly once — no leaked/unclosed span, asserted under `filterwarnings=["error"]`.
- [x] **Inactive path:** with no `opik_api_key`, a full turn emits ZERO spans and the REPL launch
  prints no tracing line — byte-identical to before.
- [x] The startup console line appears exactly once when tracing is active and never when inactive.
- [x] `make ci` green with no key/network; unit tests mirror the touched modules.

## Out of scope

- The headless `decode run` root span (093).
- Subagent-nesting assertions and the live Opik smoke (095).
- Docs prose / manual-QA rows (094).

## Log

### [SWE] 2026-07-05 11:45 — Implementation

**Files modified**
- `src/decode/agent/loop.py` — `AgentTurnHandler.__init__` gains optional `session_id: str | None = None`
  (stored as `self._session_id`); `__call__` now wraps the entire `while True` in
  `with observability.root_span("chat_turn", thread_id=self._session_id):` (added `from decode import
  observability`). One `__call__` == one turn == one root span; the gated approve/resume legs and
  `WOULD_STOP` follow-ups ride inside it.
- `src/decode/tui/app.py` — added `from decode import observability`; call `observability.init_tracing()`
  once, early (right after `console` is set, before `build_agent()`) and capture the bool; emit ONE
  startup line `Decode - Opik tracing on (project '<name>').` through the render path right before the
  banner when active; pass `session_id=session_log.session_id` into `AgentTurnHandler`.
- `src/decode/observability/tracing.py` — folded in the pre-approved 091 nit: `.rstrip("/")` the OTLP
  base before appending `/v1/traces` so a trailing-slash `opik_url_override` can't yield `//v1/traces`.
- `tests/unit/decode/agent/test_loop_tracing.py` (new) — unit-level wiring: `root_span` opened once per
  turn, right name + `thread_id`, closed exactly once on a normal turn, a multi-leg gated turn, two
  turns, and on abort. Patches `observability.root_span` with a recording CM (no real logfire).
- `tests/unit/decode/tui/test_app_tracing.py` (new) — `run_app` calls `init_tracing` once before
  `build_agent`; prints the line once when active / never when inactive; passes `session_log.session_id`
  to the handler. Drives the REAL `run_app` via piped prompt_toolkit input.
- `tests/integration/test_opik_repl_trace.py` (new) — the capstone: REAL `build_agent()` + `Runner` +
  `AgentTurnHandler` + gate + `logfire.testing` in-memory exporter. Asserts the real span tree for
  every AC (root+nesting, usage, gated single-trace, two-roots-one-thread, in-turn compaction nesting,
  abort-exactly-once, inactive-zero-spans).
- `tests/unit/decode/observability/test_tracing.py` — one added test for the trailing-slash rstrip.

**Tests**
- Unit: 1483 passing, 0 failing (`make pre-commit`). New: 5 (loop) + 4 (app) + 1 (tracing rstrip).
- Integration: 111 passing, 0 failing (`make integration-tests`), including 6 new
  `test_opik_repl_trace.py`.
- `uv lock --check` clean; `make format-check` + `make lint-check` clean → all four `make ci` gates green.

**Acceptance criteria**
- [x] one `chat_turn` root per turn, `thread_id` = session id, chat/tool spans nested — verified by
  `tests/integration/test_opik_repl_trace.py::test_single_turn_is_one_chat_turn_root_with_nested_model_and_tool_spans`
- [x] gated approve/resume stays in one trace —
  `...::test_gated_tool_approve_and_resume_stay_in_one_trace`
- [x] LLM span carries `gen_ai.usage.input_tokens` > 0 — asserted in the single-turn test above
- [x] two turns → two roots sharing `thread_id` — `...::test_two_turns_emit_two_roots_sharing_the_thread_id`
- [x] in-turn compaction nests under the turn root — `...::test_in_turn_compaction_nests_under_the_turn_root`
- [x] abort closes the root span exactly once — `...::test_abort_closes_the_root_span_exactly_once`
  (+ unit `test_loop_tracing.py::test_abort_closes_the_root_span_exactly_once`)
- [x] inactive path emits ZERO spans + no line — `...::test_inactive_turn_emits_zero_spans` +
  `test_app_tracing.py::test_run_app_prints_no_tracing_line_when_inactive`
- [x] startup line exactly once when active, never when inactive —
  `test_app_tracing.py::test_run_app_prints_the_tracing_line_once_when_active` / `...when_inactive`
- [x] `make ci` green with no key/network; unit tests mirror the touched modules (loop + tui/app)

**Evidence**
```
$ uv run pytest tests/unit/decode/agent/test_loop_tracing.py tests/integration/test_opik_repl_trace.py \
    tests/unit/decode/tui/test_app_tracing.py tests/unit/decode/observability/test_tracing.py -q
28 passed in 1.43s

$ make pre-commit    # format-check + lint-check + unit-tests
All checks passed!
======================= 1483 passed in 100.36s (0:01:40) =======================

$ make integration-tests
tests/integration/test_opik_repl_trace.py ......                         [ 39%]
======================= 111 passed in 359.80s (0:05:59) ========================

# Teeth check: temporarily replacing `with observability.root_span(...)` with `nullcontext()`
# turns 10 of 11 tracing tests RED (only inactive-zero-spans stays green, correctly). Restored.

# E2E (real init_tracing active, OTLP exporter mocked → no network; driven through real run_app):
tracing active after launch: True
  | Decode - Opik tracing on (project 'decode').
  | Decode - gemini:gemini-2.5-flash - type a line; /quit exits.
  | Decode E2E-REPLY-STREAMED
  | Decode - bye.
E2E OK: real init_tracing wired, line printed, turn ran, clean exit.
```

**Notes**
- **De-risking spike first:** because the load-bearing unknowns were "do pydantic-ai model/tool spans
  actually nest under a `with logfire.span(...)` across `yield`s?" and "does the sync `with` unwind
  cleanly on `aclose()`'s `GeneratorExit`?", I ran two throwaway spikes against the real stack before
  writing tests — confirming (a) all model/tool spans share the `chat_turn` trace with the root as
  `parent=None` and `gen_ai.usage.input_tokens=50` on the `chat` span, and (b) an abort produces exactly
  one exported `chat_turn`, no warnings under `warnings.simplefilter("error")`. Then wrote the tests and
  ran the teeth check above.
- **Abort-safety test mechanism:** the runner submits a turn then `abort()`s before the first leg;
  `wait_idle()` lets the runner see `_abort` at the first boundary, break, and call `agen.aclose()`,
  which throws `GeneratorExit` into `__call__` suspended at its first `yield` (inside the `with`). The
  `with` unwinds and closes the span. A captured/exported span is one that *ended*, so exactly one
  exported `chat_turn` (integration) / `recorder.exits == 1` (unit) proves close-exactly-once with no
  leak — under the suite-wide `filterwarnings=["error"]`, an ignored-`GeneratorExit` would fail the run.
- **Test activation choice:** the integration test forces `tracing._active = True` +
  `logfire.instrument_pydantic_ai()` directly (mirroring the 091 in-memory test) rather than calling
  `init_tracing()`, because `init_tracing`'s real `logfire.configure` would replace `capfire`'s in-memory
  exporter and risk a background OTLP flush. A fake `opik_api_key` is set for fidelity only. An autouse
  fixture saves/restores the global `Agent._instrument_default` + the module flag so nothing leaks across
  tests (guards the inactive-zero-spans test against ordering). The REAL `init_tracing()` path is instead
  exercised by the e2e above (exporter mocked → no network).
- No new dependencies (`logfire` landed in 091); `docs/adr` / `docs/glossary.md` / `docs/notes/`
  untouched. No architectural forks encountered.

### [Tester] 2026-07-05 12:01 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` → 181 files formatted; `ruff check` → all
  passed; `make pre-commit` → 1483 passed)
- Unit tests: 1483 passed / 0 failed
- Integration tests: 111 passed / 0 failed (full `make integration-tests`, 6:00; incl. 6 new
  `test_opik_repl_trace.py`)
- `uv lock --check`: clean (155 packages, no drift) → all four `make ci` gates green
- Warnings: 0 (suite runs under `filterwarnings=["error"]`; every pass = zero warnings)

**E2E adversarial pass**
- Happy path (inactive REPL, no OPIK key): `printf '/quit\n' | uv run decode` → banner
  `Decode - gemini:gemini-2.5-flash - type a line; /quit exits.`, NO "Opik tracing on" line, `Decode
  - bye.`, exit 0 → byte-identical to pre-092 (PASS)
- Break path 1 (lifecycle: **exception mid-turn** — the spec's third unwind case): scripted
  `FunctionModel` raising `RuntimeError("boom-in-model")` inside the leg → span closes **exactly
  once** (recorder enters==1/exits==1; real logfire exports exactly one `chat_turn` root), error
  surfaces **unchanged** as `AgentError("boom-in-model")`, no leaked span, clean under
  `filterwarnings=["error"]`. Behavior is **correct** — but this lifecycle case has **NO committed
  regression test** (only my throwaway probe, now deleted). (behavior PASS / coverage FAIL — see below)
- Break path 2 (mutation: drop `thread_id` from `root_span(...)`): 7 tracing tests go RED with
  `thread_id` assertion failures; the `None`-default test correctly stays green → thread_id wiring has
  teeth (PASS). Reverted byte-exact.
- Break path 3 (mutation: move `with` INSIDE the `while` → per-leg spans): the gated single-trace
  test + single-turn test go RED (2 `chat_turn` roots, distinct trace_ids) → single-root assertion
  has teeth (PASS). Reverted byte-exact (loop.py diff back to 59/36).
- Break path 4 (mid-turn **steering** at the resume boundary): steer folded into the same turn →
  exactly ONE `chat_turn` span, closed once; the steer text reached the model on the resume leg →
  nothing double-opened (PASS)
- Break path 5 (**isolation/leak hunt**): tracing tests + `test_subagents_capstone` +
  `test_milestone1_capstone` run together in BOTH orders → 20 passed each; `test_inactive_turn_emits
  _zero_spans` stays green even after the active-tracing tests → `_isolate_tracing_state`
  save/restore of `Agent._instrument_default` + `reset_tracing` leaves zero cross-contamination (PASS)
- Break path 6 (**resume continuity**, light check): span path uses `self._session_id` verbatim
  (`__init__` → `root_span(thread_id=...)`), no `uuid4()`/id-minting anywhere in loop.py's span path
  (PASS). Note: `SessionLog.create` mints a fresh id every launch incl. `--resume`, so a resumed
  conversation starts a NEW Opik thread — consistent with the spec ("pass `session_log.session_id`")
  and the "one session = one thread" model; flagged for the PA, not a 092 defect.
- Probe 7 (real Opik export): SKIPPED — `OPIK_API_KEY` is unset in `.env` and the process env; this is
  095's live proof anyway.

**Acceptance criteria** (all 9 verified PASS; the FAIL below is an adversarial gap, not an AC miss)
- [x] PASS — one `chat_turn` root/turn, `thread_id` = session id, chat+tool spans nested — evidence:
  `test_opik_repl_trace.py::test_single_turn_is_one_chat_turn_root_with_nested_model_and_tool_spans`
  (asserts `parent is None`, `thread_id == _SESSION_ID`, nested share trace_id); mutation-tested teeth
- [x] PASS — gated approve/resume in ONE trace — `...::test_gated_tool_approve_and_resume_stay_in_one_trace`; mutation (b) proves teeth
- [x] PASS — LLM span carries `gen_ai.usage.input_tokens` > 0 — asserted in the single-turn test
- [x] PASS — two turns → two roots sharing `thread_id` — `...::test_two_turns_emit_two_roots_sharing_the_thread_id` (2 roots, 1 thread, 2 trace_ids)
- [x] PASS — in-turn compaction nests under the root — `...::test_in_turn_compaction_nests_under_the_turn_root` (ContextCompacted fired; ≥2 model spans share the root trace)
- [x] PASS — abort closes the span exactly once — `...::test_abort_closes_the_root_span_exactly_once` (+ unit); green under `filterwarnings=["error"]`
- [x] PASS — inactive path: ZERO spans + no line — `...::test_inactive_turn_emits_zero_spans` + `test_app_tracing.py::...when_inactive` + manual `uv run decode` (no line, normal banner)
- [x] PASS — startup line exactly once active / never inactive — `test_app_tracing.py::...once_when_active` (count==1) / `...when_inactive`
- [x] PASS — `make ci` green, no key/network; unit tests mirror touched modules (loop / tui-app / tracing)

**Evidence**
```
$ make pre-commit
All checks passed!
======================= 1483 passed in 100.32s (0:01:40) =======================

$ make integration-tests
tests/integration/test_opik_repl_trace.py ......                         [ 39%]
======================= 111 passed in 360.32s (0:06:00) ========================

$ make format-check   → 181 files already formatted
$ make lint-check     → All checks passed!
$ uv lock --check     → Resolved 155 packages (clean)

# Isolation hunt (both orders): 20 passed / 20 passed
# Mutation (a) drop thread_id: 7 red.  Mutation (b) per-leg with: gated+single-turn red.  Both reverted byte-exact.
```

**Other issues found**
1. (BLOCKING — the FAIL) **No regression test for the exception-path lifecycle.** The spec's "Root
   span per turn" section requires the span to "close exactly once on normal return, on exception,
   AND on abort alike." The suite tests normal-return (every turn test) and abort (AC6 + dedicated
   unit+integration tests) but NOT the exception case — the third named unwind path, and the hardest
   to reason about (the exception unwinds through `logfire.span.__exit__` before the runner's
   `except Exception` catches it). My probe proves the behavior is correct, so the fix is a cheap,
   copy-paste regression test mirroring the abort test at both levels. Suggested tests (verified
   green against the current code):
   - unit (`tests/unit/decode/agent/test_loop_tracing.py`): a `FunctionModel` whose `stream_function`
     raises mid-leg; assert `recorder.enters == 1 and recorder.exits == 1` and that the runner emitted
     an `AgentError` carrying the original message (turn `aborted is False`).
   - integration (`tests/integration/test_opik_repl_trace.py`, under `active_tracing`): same raising
     model; assert exactly one exported `chat_turn` root (closed once, never leaked) and that the
     `AgentError` still surfaced — proving the error is neither swallowed nor altered by the span.
2. (non-blocking) `--resume` mints a fresh `session_id` → a resumed conversation opens a NEW Opik
   thread rather than continuing the prior one. Matches the 092 spec exactly (it says to pass
   `session_log.session_id`), so out of scope here — flagging for the PA to decide if cross-resume
   thread continuity is wanted (a 094/future concern).
3. (note) The `code-review` plugin is enabled in `.claude/settings.json` but is not invocable from
   the Tester subagent's tool context (no slash-command mechanism); I performed the equivalent manual
   diff review — types on all new signatures, no bare `print()` in library code (tracing line rides
   `emit_line`→`console.print`), no secrets, no unrelated files in the diff, docstrings accurate.

**VERDICT: FAIL** — 9/9 acceptance criteria pass, full suite green (1483 unit + 111 integration, 0
warnings), and every adversarial break-path *behavior* is correct. The single blocking gap is issue
#1: the exception-path lifecycle (a spec-named safety property) ships with no regression test while
its sibling (abort) has two. Add the two tests above (behavior already correct — my probes pass), then
this is a clean PASS.

### [SWE] 2026-07-05 12:18 — Fixes (Tester issue #1: exception-path regression coverage)

Added exactly the requested regression pair for the **third** spec-named unwind path — a model
failure mid-leg — mirroring the existing abort tests at both levels. Behavior was already correct
(the Tester's probe passed); this closes the missing *committed* coverage. No production code changed.

**Files modified**
- `tests/unit/decode/agent/test_loop_tracing.py` — new `_raising_model()` helper (a `FunctionModel`
  whose `stream_function` yields one token then raises `RuntimeError("boom-in-model")` mid-leg) +
  `test_exception_mid_leg_closes_the_root_span_exactly_once`: asserts the recording CM saw
  `enters == 1 and exits == 1`, the error surfaced as an `AgentError` carrying the original message,
  and the turn is `aborted is False` (an error, not an abort — distinguishes it from AC6).
- `tests/integration/test_opik_repl_trace.py` — same `_raising_model()` helper +
  `test_exception_mid_leg_closes_the_root_span_exactly_once` under the `active_tracing` fixture:
  drives the raising model through the REAL stack and asserts exactly ONE exported `chat_turn` root
  (closed once, `thread_id == _SESSION_ID`, never leaked), the `AgentError` still surfaced unchanged
  (`message == "boom-in-model"`), and `aborted is False`.

**Teeth** — temporarily suppressing the runner's `AgentError` emission (`harness/runner.py`) turned
BOTH new tests RED on the `assert errors` check; reverted byte-exact (`git diff` clean). The
integration test's `len(roots) == 1` is a nullcontext guard (would be 0 with tracing off). The
exception unwinds through `observability.root_span.__exit__` (span closes) *before* the runner's
`except Exception` maps it to the `AgentError`, exactly as the spec's "Root span per turn" requires.

**Tests**
- Unit: 1484 passing, 0 failing (`make pre-commit`; +1 vs the prior 1483 — the new exception test).
- Integration: 112 passing, 0 failing (`make integration-tests`; +1 vs 111 — `test_opik_repl_trace.py`
  now 7). Both suites clean under the suite-wide `filterwarnings=["error"]`.
- `make format-check` + `make lint-check` clean.

**Evidence**
```
$ uv run pytest tests/unit/decode/agent/test_loop_tracing.py::test_exception_mid_leg_closes_the_root_span_exactly_once \
    tests/integration/test_opik_repl_trace.py::test_exception_mid_leg_closes_the_root_span_exactly_once -q
2 passed in 0.91s

$ make pre-commit
======================= 1484 passed in 100.20s (0:01:40) =======================

$ make integration-tests
tests/integration/test_opik_repl_trace.py .......                         [ 40%]
======================= 112 passed in 361.11s (0:06:01) ========================
```

**Notes**
- No production code touched — the exception path already closed the span exactly once and surfaced
  the error unchanged; this adds the regression tests that were missing. `docs/adr` / `docs/glossary.md`
  untouched; no new dependencies; no architectural forks. The non-blocking `--resume` thread-continuity
  note (Tester issue #2) remains out of scope for 092, flagged for the PA.

### [Tester] 2026-07-05 12:34 — Re-QA (delta: exception-path regression coverage)

Scoped re-review of the SINGLE blocking issue from the prior FAIL (no committed regression test for
the exception-unwind of the per-turn root span). Did NOT redo the full adversarial battery — the prior
PASS evidence for all 9 ACs stands and was spot-checked green below. No production code changed since
the prior FAIL (confirmed: `runner.py` byte-clean; `tracing.py` diff is only the 091 rstrip nit).

**New tests reviewed (the requested pair)**
- unit `test_loop_tracing.py::test_exception_mid_leg_closes_the_root_span_exactly_once` +
  integration `test_opik_repl_trace.py::test_exception_mid_leg_closes_the_root_span_exactly_once`,
  both driving `_raising_model()` (yields one token, then raises `RuntimeError("boom-in-model")`
  mid-leg — the third spec-named unwind path).
- They assert exactly the spec's exception case: span opened once (right name + `thread_id`), closed
  **EXACTLY once** (unit `enters==1`/`exits==1`; integration `len(roots)==1` — a leaked span is never
  exported, so one exported root proves closed-once + no leak), error surfaces **UNCHANGED**
  (`message == "boom-in-model"`, not swallowed by the span `__exit__`), and it is an error NOT an
  abort (`aborted is False`, distinguishing it from AC6). Not vacuous — mutation-proven below.

**Teeth — my own mutations (distinct from the SWE's; both reverted byte-exact)**
- Mut 1 — corrupt the runner's AgentError message (`harness/runner.py:180`, `str(exc)` → a constant):
  **BOTH** new tests RED on `errors[-1].message == _BOOM`. Proves the "error surfaces unchanged"
  assertion is load-bearing in both.
- Mut 2 — force `root_span` to `nullcontext()` when active (`observability/tracing.py`): the
  **integration** new test RED on `len(roots) == 1` (0 roots exported); the **unit** test stayed GREEN
  (it mocks `root_span` — correctly a wiring-level test). Proves the integration test's
  span-present/closed-exactly-once assertion is load-bearing.
- Post-revert: `git diff src/decode/harness/runner.py` empty; `tracing.py` diff = only the 091 rstrip
  nit; both new tests GREEN again.

**Full gates**
- `make pre-commit`: PASS — 1484 unit passed (format-check + lint-check clean), 0 warnings.
- `make integration-tests`: PASS — 112 passed (`test_opik_repl_trace.py` now 7), 0 warnings under
  `filterwarnings=["error"]`.

**git hygiene** — only the 092 file set: modified `agent/loop.py`, `observability/tracing.py`,
`tui/app.py`, the tracing rstrip test, this task md; added `test_opik_repl_trace.py`,
`test_loop_tracing.py`, `test_app_tracing.py`. Nothing stray.

**Prior PASS still stands (spot-check, re-ran green)**
- `test_opik_repl_trace.py::test_gated_tool_approve_and_resume_stay_in_one_trace` — PASSED
- `test_opik_repl_trace.py::test_abort_closes_the_root_span_exactly_once` (+ unit sibling) — PASSED
- All 9 ACs remain `[x]`.

**Evidence**
```
$ make pre-commit
======================= 1484 passed in 99.85s (0:01:39) ========================
$ make integration-tests
======================= 112 passed in 355.93s (0:05:55) ========================
# Mut 1 (runner message): 2 failed (both new).  Mut 2 (root_span→nullcontext): 1 failed (integration
# new), unit passed.  Both reverted byte-exact (runner.py clean; tracing.py = rstrip nit only).
$ uv run pytest <gated + abort spot-checks + the 2 new tests> -v
5 passed in 1.47s
```

**VERDICT: PASS** — the one blocking issue from the prior FAIL is closed with exactly the requested
regression pair; both tests assert the spec's exception case (close exactly once / error unchanged /
no leaked span) and are mutation-proven non-vacuous. Full suite green (1484 unit + 112 integration,
0 warnings), git hygiene clean, no production code touched. The non-blocking `--resume` thread-continuity
note (prior issue #2) remains a PA / future concern. Hand off to PA for acceptance review.
