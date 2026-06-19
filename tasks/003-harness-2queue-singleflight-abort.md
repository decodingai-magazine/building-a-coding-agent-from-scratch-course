---
id: 003-harness-2queue-singleflight-abort
feature: m1-vanilla-agent
status: done
---

# Harness: two-queue, single-flight, cooperative abort

## Scope
The interaction loop per [ADR-0002 §4–5](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md): steering + follow-up queues, a phase machine with a single-flight lock spanning a whole turn, and a cooperative abort flag. Wired to a stub multi-step turn handler so semantics are testable before the real agent.

## Acceptance criteria
- [x] `harness/queue.py` holds steering + follow-up `asyncio.Queue`s; `harness/runner.py` owns the phase machine (`idle|dispatching|running`) + single-flight lock (phase set before first `await`).
- [x] `entities/events.py` defines the event union the loop emits / TUI renders.
- [x] Steering drains between the stub's model-steps; follow-up drains only at the would-stop boundary; `Esc` stops at the next boundary keeping completed history; a second concurrent submit does not start a parallel turn.
- [x] Async tests cover all four behaviours.

## Out of scope
- Real model calls (task 004); mid-stream/mid-tool interruption (never — boundary only).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan + ADR-0003.

### [SWE] 2026-06-19 22:10 — Implementation

**Files modified**
- `src/decode/entities/events.py` — new canonical event union (10 frozen+slotted dataclasses, discriminated on a `kind` literal): `TurnStarted`, `TurnFinished`, `AssistantTextDelta`, `ThinkingDelta`, `ToolCallStarted`, `ToolResult`, `PermissionRequested`, `AskUserRequested`, `TaskListUpdated`, `AgentError`
- `src/decode/harness/__init__.py` — new harness package docstring
- `src/decode/harness/queue.py` — `InteractionQueues` (steering + follow-up `asyncio.Queue`s) with `drain_steering`/`drain_follow_up`/`clear`
- `src/decode/harness/runner.py` — `Runner` (phase machine `idle|dispatching|running`, single-flight lock spanning a whole turn, phase set synchronously before the first await, steering/follow-up drain points, cooperative-abort flag) + the `Boundary`/`Phase` enums, `TurnContext`, the `TurnHandler` async-generator seam, and `stub_turn_handler` (fake N-step turn)
- `src/decode/tui/render.py` — swapped the local `EchoEvent`/`MessageEvent`/`ToolCallEvent` contract for the canonical `entities.events` union; re-pointed `render_event` to exhaustively match all 10 kinds (task-002 follow-up the Tester flagged)
- `src/decode/tui/app.py` — wired the `InputIntent` routing into the `Runner`: plain Enter -> `submit(STEER)` (new turn when idle, steering when busy), Alt+Enter -> `submit(FOLLOW_UP)`, Esc -> `runner.abort()`; harness events stream to Rich via an `_on_event` sink; `wait_idle()` on exit
- `src/decode/cli.py` — refreshed the launch comment (harness wired in 003, agent loop in 004)
- `tests/unit/decode/entities/{__init__.py,test_events.py}` — event-union contract tests (unique discriminants, frozen, defaults)
- `tests/unit/decode/harness/{__init__.py,test_queue.py,test_runner.py}` — async queue + runner tests
- `tests/unit/decode/tui/test_render.py` — rewritten onto the canonical events (12 tests)

**Tests**
- Unit: 59 passing, 0 failing (`make unit-tests`); 36 new/changed (15 events + 5 queue + 11 runner + 12 render rewritten — net +36 over the prior 23)
- Integration: N/A — no infra changes (nothing under `tests/integration/`)

**Acceptance criteria**
- [x] `queue.py` two `asyncio.Queue`s + `runner.py` phase machine + single-flight (phase before first await) — `tests/unit/decode/harness/test_runner.py::test_phase_is_set_before_submit_yields_and_before_the_turn_runs`, `::test_phase_starts_idle`, `test_queue.py::*`
- [x] `entities/events.py` event union — `tests/unit/decode/entities/test_events.py::*`
- [x] steering drains between model-steps / follow-up only at would-stop / Esc stops at next boundary keeping history / no parallel turn — `test_runner.py::test_steering_drains_before_each_model_request_leg`, `::test_followup_drains_only_at_would_stop_boundary`, `::test_followup_is_not_drained_at_a_model_request_boundary`, `::test_abort_stops_at_next_boundary_keeping_completed_history`, `::test_abort_clears_the_queues_and_returns_to_idle`, `::test_second_concurrent_submit_does_not_start_a_parallel_turn`
- [x] async tests cover all four behaviours — see above (all `asyncio_mode=auto`)

**Evidence**
```
$ make pre-commit
uv run ruff format --check
24 files already formatted
uv run ruff check
All checks passed!
uv run pytest tests/unit
collected 59 items
tests/unit/decode/config/test_settings.py ....                           [  6%]
tests/unit/decode/entities/test_events.py ...............                [ 32%]
tests/unit/decode/harness/test_queue.py .....                            [ 40%]
tests/unit/decode/harness/test_runner.py ...........                     [ 59%]
tests/unit/decode/test_cli.py ...                                        [ 64%]
tests/unit/decode/test_logging.py ...                                    [ 69%]
tests/unit/decode/tui/test_app.py ......                                 [ 79%]
tests/unit/decode/tui/test_render.py ............                        [100%]
============================== 59 passed in 0.17s ==============================

$ # e2e via PTY (uv run decode): type a line -> stub turn streams, /quit exits
EXIT STATUS: 0
banner present: True
stub leg 1 streamed: True       # "step 1 of the stub turn"
stub leg 2 streamed: True       # "step 2 of the stub turn"
thinking streamed: True         # "thinking about leg N..."
tool panel (noop) rendered: True   # ToolResult -> Panel on completion
done marker: True               # "[done]" (TurnFinished, not aborted)
goodbye present: True

$ # harness semantics driven against the SHIPPED stub_turn_handler:
1) steering injected at next model-request boundary: True
2) follow-up continued the turn (extra leg at would-stop): True
3) abort: idle + TurnFinished(aborted=True) + completed history kept: True
```

**Notes**
- **Turn-handler seam.** The agent loop is decoupled behind an async-generator `TurnHandler` that `yield`s a `Boundary` (`MODEL_REQUEST` / `WOULD_STOP`) and is `asend`-ed the drained messages. This keeps ALL drain/abort/single-flight logic in the runner (where the task wants it real + testable) while task 004 plugs the real Pydantic AI loop into the same seam. No new dependency, no framework import (pydantic-ai is intentionally not installed yet).
- **Phase-before-await.** `submit`'s idle path has no `await` before it flips the phase to `DISPATCHING` and schedules the turn task, so `await submit(...)` returns already-busy *before* the turn task runs a single leg — proven by `test_phase_is_set_before_submit_yields_and_before_the_turn_runs`.
- **Cooperative abort** is checked at every boundary; with `Esc` set before the first boundary the turn stops there (no leg work emitted), keeps the `TurnStarted` history, clears both queues, emits `TurnFinished(aborted=True)`, resets the flag, returns to idle.
- **Task-002 follow-up closed:** `render.py` now renders the canonical `entities.events` union (the local `EchoEvent`/`MessageEvent`/`ToolCallEvent` contract is gone) and the 7 old render tests were rewritten into 12 against the canonical events.
- `PermissionRequested` / `AskUserRequested` / `TaskListUpdated` are in the union + renderer now (part of the canonical contract) but only *emitted* by their owning tasks (005 / 011 / 009); included so the union is complete and the renderer is exhaustive.
- `make integration-tests` NOT RUN — no infra changes; nothing under `tests/integration/`.

### [Tester] 2026-06-19 21:30 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 24 files clean; `ruff check` all passed)
- Unit tests: 59 passed / 0 failed
- Integration tests: N/A — `tests/integration/` holds only `.gitkeep` (`make integration-tests` exits 5 "no tests collected"); no infra in this task. Correct.
- Warnings: 0 (`filterwarnings=["error"]` is active — any warning would have failed the run)

**E2E adversarial pass** (drove `uv run decode` through a real PTY)
- Happy path: type `hello there` → Enter → `/quit` → both legs stream (`thinking about leg 1/2`, `step 1/2 of the stub turn`, two `noop` ToolResult panels, `[done]`), `decode - bye.`, exit status 0. PASS
- Break path 1 (Esc mid-turn / abort): `long task` + Esc + `/quit` → no traceback, no hang, exit 0. Stub turn is sub-ms so Esc landed while idle (harmless no-op, also proven by the idle-abort probe). PASS
- Break path 2 (Alt+Enter / follow-up via PTY): `first` then `keep going` + Alt+Enter (`\x1b\r`) → first turn streams 2 legs + `[done]`, then `you keep going` streams 2 more legs + `[done]`. Follow-up correctly continued the conversation. exit 0, no crash. PASS

**Concurrency adversarial probe** (throwaway async harness beyond the SWE's tests; all temp files removed)
- (a) single-flight under load: 50 batches × 20 simultaneous `submit()` via `asyncio.gather` — exactly one `turn_started`/`turn_finished` per batch, never a parallel turn; the 19 racers per batch were queued as steering with none lost/duplicated. PASS
- (b) steering at NEXT model-request boundary, FIFO: against the shipped `stub_turn_handler`, `A/B/C` injected at the first boundary in order as `(steered: A/B/C)`; deterministic boundary-synced probe confirms a message arriving after leg-1's drain lands at leg-2, never mid-leg. PASS
- (c) follow-up ONLY at would-stop: against the shipped stub, a follow-up queued while busy never appeared as steering, was consumed once at would-stop, and added exactly one extra leg (`step 3`); a no-follow-up turn stops at would-stop with no extra leg. PASS
- (d) abort timing (realistic): Esc set while a leg is awaiting model/tool work → the in-flight leg finishes, the turn stops at the NEXT boundary (later legs never start), keeps completed history, `TurnFinished(aborted=True)`, clears BOTH queues, returns to `idle`, no stuck lock; flag resets so the next turn runs clean. PASS. NOTE: a synchronous-injection probe (abort set from inside the `drain_steering` call) showed the leg whose boundary is being drained still runs — but `drain_*` never `await`s, so that window is unreachable in real async execution; not a defect.
- (e) handler raises → `AgentError(message=...)` surfaced, `TurnFinished` emitted, phase back to `idle`, runner reusable for the next turn (no stuck lock). Verified for a raise before the first yield AND a raise after the first yield. PASS
- Extra: stray `abort()` while idle does not poison the next turn; `turn_id` strictly increasing (0,1,2) across turns; concurrent `wait_idle()` ×3 safe; `wait_idle()` on a never-run runner returns immediately. PASS
- Hostile steering inputs (empty, whitespace, 100 000 chars, `naïve café 🤖`, `'; DROP TABLE x;--`, `$(rm -rf /)`, null bytes): all flow through with no `AgentError`, no crash, back to idle. PASS
- Renderer hostile fields (tool name / task text / assistant text bearing Rich markup like `[red]`, `{braces}`): no crash; body text is markup-safe (wrapped in `Text(...)`); only cosmetic effect is a Rich tag in a *panel title* being interpreted — see Other issues. PASS (no crash/injection)

**Acceptance criteria**
- [x] PASS — `harness/queue.py` holds steering + follow-up `asyncio.Queue`s; `harness/runner.py` owns the phase machine (`idle|dispatching|running`) + single-flight lock, phase set before first `await` — Evidence: `queue.py:30-31` two `asyncio.Queue`s; `runner.py:139-143` sets `_phase=DISPATCHING` synchronously before `ensure_future`; `test_runner.py::test_phase_is_set_before_submit_yields_and_before_the_turn_runs`, `::test_phase_starts_idle`, `test_queue.py` (5 tests) all pass.
- [x] PASS — `entities/events.py` defines the event union — Evidence: 10 frozen+slotted `kind`-discriminated dataclasses joined into `Event` (`events.py:140-151`); `test_events.py` (unique discriminants, frozen, hashable, defaults) 15 tests pass; renderer exhaustively matches all 10 (`render.py:22-48`).
- [x] PASS — steering drains between model-steps; follow-up only at would-stop; Esc stops at next boundary keeping history; second concurrent submit does not start a parallel turn — Evidence: `runner.py:171-174` drains steering at MODEL_REQUEST / follow-up at WOULD_STOP; abort checked at every boundary (`:168`); single-flight via `active_turns` guard (`:131-136`). Verified by the unit tests AND the independent concurrency probe + PTY follow-up above.
- [x] PASS — async tests cover all four behaviours — Evidence: `test_steering_drains_before_each_model_request_leg`, `test_followup_drains_only_at_would_stop_boundary`, `test_followup_is_not_drained_at_a_model_request_boundary`, `test_abort_stops_at_next_boundary_keeping_completed_history`, `test_abort_clears_the_queues_and_returns_to_idle`, `test_abort_flag_resets_for_the_next_turn`, `test_second_concurrent_submit_does_not_start_a_parallel_turn`, `test_handler_error_surfaces_as_agent_error_and_returns_to_idle` — all pass under `asyncio_mode=auto`.

**Evidence**
```
$ make pre-commit
uv run ruff format --check
24 files already formatted
uv run ruff check
All checks passed!
uv run pytest tests/unit
collected 59 items
...
============================== 59 passed in 0.27s ==============================
```

**Code-review plugin**
- The `code-review` plugin (enabled in `.claude/settings.json`) is hard-wired to operate on an open GitHub **pull request** (`gh pr diff` / `gh pr view` / `gh pr comment`). This task is **uncommitted local work on `feat/m1-vanilla-agent`** with no PR, so the plugin has nothing to fetch or comment on and cannot be invoked here. I performed the equivalent manual review it automates (CLAUDE.md/AGENTS.md adherence, bug scan, comment-guidance compliance) directly — findings folded into this report. No blocking defects.

**Other issues found** (non-blocking; orchestrator/PA's call)
- Renderer markup in panel *titles*: `_render_tool_result` (`render.py:71`) and `_render_task_list_updated` (`render.py:91`) build titles via f-string `[bold]...[/bold]` that also embed `event.name`. A name containing a valid Rich tag is interpreted (cosmetic strip), not escaped. Cannot crash; tool/task names are model/stub-emitted (not user input) in M1. Body text is already safe (`Text(...)`). Worth a follow-up if tool names ever become user-controlled.
- `stub_turn_handler` and the runner's turn-handler seam are clean; the `AsyncGenerator[Boundary, list[str]]` contract is well-documented and matches the task's intent. No smell.

**VERDICT: PASS** — every acceptance criterion verified with evidence; full suite green with 0 warnings; e2e PTY pass green on happy path + abort + follow-up; concurrency adversarial probe green on single-flight, steering FIFO/boundary, follow-up-only-at-would-stop, cooperative abort (history kept / queues cleared / idle / flag reset / no stuck lock), handler-raise recovery, and hostile inputs. No races, stuck locks, lost messages, double-drains, or non-idle phases found.
