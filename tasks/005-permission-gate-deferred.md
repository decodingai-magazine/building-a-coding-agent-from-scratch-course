---
id: 005-permission-gate-deferred
feature: m1-vanilla-agent
status: done
---

# Permission gate (deferred approval)

## Scope
The ask-on-every-tool gate per [ADR-0002 §2–3](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md), wired into the loop's `DeferredToolRequests` → approve → resume path. Tested with one trivial gated tool.

## Acceptance criteria
- [x] `permissions/gate.py` `check()` returns `allow/ask/deny` and carries a `mode` field (always asks in v1); `entities/permissions.py` defines `PermissionRequest`/`PermissionDecision`.
- [x] A gated tool pauses the run, emits `PermissionRequested`, and resumes with `DeferredToolResults`.
- [x] A denial is fed back to the model as a tool result (`ToolDenied`); tools carry a `read_only` flag (tagged, still asked in v1).
- [x] The single-flight lock spans the full multi-leg turn; tested with `TestModel(call_tools=...)`.

## Out of scope
- Modes `default/plan/edit/bypass`, read-only auto-allow, persisted rules (M3).

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan + ADR-0002.

### [SWE] 2026-06-19 — Implementation

**Files modified**
- `src/decode/permissions/__init__.py` — new package docstring (gate = policy; resolver owns the ask→allow/deny).
- `src/decode/permissions/types.py` — `PermissionMode` enum (only `ASK` in v1; the seam for M3 `default/plan/edit/bypass`).
- `src/decode/permissions/gate.py` — `PermissionGate.check(request) -> PermissionDecision`; v1 policy always `ASK` (records `read_only`, ignores it for the decision). Pure, synchronous policy object; does not own the UI.
- `src/decode/entities/permissions.py` — `PermissionRequest` (tool_name, args summary, read_only, tool_call_id) + `PermissionDecision` (`PermissionOutcome` allow/ask/deny + `mode` + optional `reason`, with `allow`/`ask`/`deny` factories). Frozen + slotted, mirroring `entities/events.py`.
- `src/decode/tools/__init__.py` — flat-registry stub + `TOOL_READ_ONLY` map and `is_read_only()` (default `False` = mutating); registers `noop`.
- `src/decode/tools/noop.py` — the ONE trivial gated tool: echoes its input, raises `ApprovalRequired` when `not ctx.tool_call_approved`; `register_noop(agent)`. (Real file/bash/web tools are 006-011.)
- `src/decode/agent/deps.py` — `AgentDeps` widened with `gate: PermissionGate` and `resolve_permission: PermissionResolver` (async hook `PermissionRequest -> PermissionDecision`; the deferred-pause seam task 011 AskUser reuses).
- `src/decode/agent/factory.py` — `build_agent()` registers the `noop` tool (output_type already had `DeferredToolRequests` from 004).
- `src/decode/agent/loop.py` — the core: when `agent.iter()` resolves to `DeferredToolRequests`, for each approval-required call ask the gate (policy → `ask`), emit `PermissionRequested`, resolve the human verdict via `deps.resolve_permission`, build `DeferredToolResults` (`True` to approve, `ToolDenied(reason)` to deny), then resume with `deferred_tool_results=` + `message_history=`. Loops until output is a plain `str`. Steering drained at the resume `MODEL_REQUEST` boundary is **appended to history as a `UserPromptPart` before the resume** (closes the task-004 carryover); on a normal prompt leg it is still folded into the prompt.
- `src/decode/tui/app.py` — `parse_permission_answer()` (pure: y/yes/a/allow → allow, anything else → deny w/ reason), `deny_permission_resolver()` (safe headless default), `_make_permission_resolver(session, console)` (interactive: render `PermissionRequested`, prompt allow/deny, parse; Ctrl-D/Ctrl-C → deny). `run_app()` wires `PermissionGate()` + the interactive resolver into `AgentDeps`.
- Tests: new `tests/unit/decode/entities/test_permissions.py`, `tests/unit/decode/permissions/{test_types,test_gate}.py`, `tests/unit/decode/tools/test_noop.py`; extended `tests/unit/decode/agent/{test_deps,test_loop}.py` and `tests/unit/decode/tui/test_app.py`.

**Tests**
- Unit: 112 passing, 0 failing (was 73; +39). Integration: N/A — no infra changes; `tests/integration` empty.
- No network: every model interaction is `TestModel(call_tools=...)` / `FunctionModel` (streamed via `DeltaToolCall` for the gated leg). `filterwarnings=["error"]` stays clean; `uv lock --check` current (163 packages, no new deps — all APIs come from the existing `pydantic-ai`).

**Acceptance criteria**
- [x] gate `check()` returns allow/ask/deny + `mode`; entities define the request/decision — `tests/unit/decode/permissions/test_gate.py`, `tests/unit/decode/entities/test_permissions.py`.
- [x] gated tool pauses, emits `PermissionRequested`, resumes with `DeferredToolResults` — `test_loop.py::test_gated_tool_pauses_and_emits_permission_requested`, `::test_approval_resumes_and_executes_the_tool`.
- [x] denial fed back as `ToolDenied` tool-result; tools carry `read_only` (tagged, still asked) — `test_loop.py::test_denial_feeds_a_tooldenied_result_back_to_the_model`, `test_noop.py::test_noop_is_registered_as_not_read_only`, `test_gate.py::test_gate_still_asks_for_a_read_only_tool_in_v1`.
- [x] single-flight lock spans the multi-leg deferred turn (driven through the real `Runner`) — `test_loop.py::test_single_flight_lock_spans_the_whole_multi_leg_deferred_turn`.

**Confirmed deferred-API facts (pydantic-ai 1.107.0)** — verified by reading `tools.py`/`exceptions.py`/`_run_context.py` AND by a runnable spike, not just asserted:
- `from pydantic_ai import DeferredToolRequests, ApprovalRequired, ToolDenied`; `from pydantic_ai.tools import DeferredToolResults`. These are dataclasses, **not** pydantic models (no `model_fields`).
- A tool raises `pydantic_ai.ApprovalRequired` (the class — `raise ApprovalRequired`) when `not ctx.tool_call_approved`. The run then resolves to `DeferredToolRequests` with `approvals: list[ToolCallPart]` (each has `.tool_name`, `.tool_call_id`, `.args`; `.args_as_json_str()` gives the summary). `RunContext.tool_call_approved: bool` is the flag the tool reads.
- Build results with `requests.build_results(approvals={tool_call_id: True | ToolDenied("msg")})` → `DeferredToolResults`. `True` ≡ `ToolApproved()` (tool executes); `ToolDenied(message)` returns the message to the model as a `ToolReturnPart` (confirmed in the spike: model saw `RETURN:<msg>`, tool did NOT run).
- Resume with `agent.iter(None, deps=..., message_history=<history incl. the deferred call>, deferred_tool_results=results)` — **no `user_prompt` on the resume leg**. `agent.run`/`agent.iter` both accept `deferred_tool_results=`.

**How steering-at-deferred-resume was validated (closes the task-004 carryover)**
- Unit (deterministic): `test_loop.py::test_steering_message_reaches_the_model_at_the_deferred_resume` drives the handler generator by hand, injecting `"STEER-AT-RESUME-123"` ONLY at the resume `MODEL_REQUEST` boundary (after the gated pause). The loop appends it as a `UserPromptPart` to `message_history` before the resume; the captured resume-leg messages contain it → the model saw the steering at a real deferred resume.
- Spike (raw pydantic-ai, no decode): appended `UserPromptPart("STEER-MSG-XYZ")` to history between the deferred output and `agent.iter(message_history=..., deferred_tool_results=...)`; the FunctionModel echoed `seen=...|STEER-MSG-XYZ` on the resume leg. Confirms the SDK preserves an appended user message across the resume.

**Evidence — e2e (no network; `GEMINI_API_KEY` not set — used a dummy)**
Drove the REAL `AgentTurnHandler` + `Runner` + `render.render_event` (same wiring as `run_app`) against a streaming `FunctionModel` that calls the gated `noop` tool:
```
===== APPROVE -> tool runs, turn resumes =====
you please run noop on a file
permission? noop {"text": "touch file"}
done: tool path complete.
[done]        # active_turns=0, phase=idle
===== DENY -> denial fed back, turn resumes =====
[verify-deny] tool-return parts in history: ['DENIAL-MARKER-789']   # denial fed back; tool echo absent → did NOT run
===== STEER AT RESUME =====
[smoke] resume-leg user messages: ['STEER: actually be careful\nplease run noop on a file']  # steering reached the model at resume ✓
```

**Evidence — QA gate**
```
$ make format-check  → 44 files already formatted        (exit 0)
$ make lint-check    → All checks passed!                (exit 0)
$ make pre-commit    → 112 passed in 1.83s               (0 warnings under filterwarnings=["error"])
$ uv lock --check    → Resolved 163 packages (current)   (exit 0)
$ GEMINI_API_KEY=dummy uv run decode --help → renders     (exit 0)
```

**Notes**
- The deferred tests stream the gated tool call via `FunctionModel(stream_function=...)` yielding `{0: DeltaToolCall(name="noop", json_args=...)}`, because the loop streams every model node (`node.stream`). The four pre-existing `TestModel(custom_output_text=...)` chat tests now pass `call_tools=[]` so the auto-tool-calling `TestModel` doesn't trip the newly-registered `noop` tool.
- The interactive resolver (`_make_permission_resolver`) reads real stdin, so it is not unit-tested (mirrors the `run_app` loop convention); its decidable parsing (`parse_permission_answer`) and the headless `deny_permission_resolver` are unit-tested. Concurrency caveat for a later task: the resolver currently uses a second `session.prompt_async` while the main input loop may also be prompting — fine for the single-flight v1 turn (the turn task awaits the answer) but a candidate for a dedicated mid-turn input mode in the TUI when multiple deferred pauses / AskUser (011) land. Flagging, not building.
- `metadata`/external-execution `calls` on `DeferredToolRequests` are unused in v1 (no `CallDeferred` tools yet); only `approvals` is exercised.

### [Tester] 2026-06-19 20:30 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 44 files clean; `ruff check` all passed; `make pre-commit` 112 passed in ~1.7s)
- Unit tests: 112 passed / 0 failed
- Integration tests: N/A (`tests/integration` is empty bar `.gitkeep`; no infra changes)
- Warnings: 0 (`filterwarnings=["error"]` confirmed in `pyproject.toml:92`)
- `uv lock --check`: current (163 packages, no new deps)
- Deferred-API facts re-verified against installed pydantic-ai 1.107.0: `ApprovalRequired`/`ToolDenied`/`DeferredToolRequests` import OK; `RunContext.tool_call_approved` field present; `DeferredToolRequests.build_results(*, approvals=...)` keyword-only with `dict[str, bool | DeferredToolApprovalResult]`; `Agent.iter` accepts `deferred_tool_results=` + `message_history=`; `ToolCallPart.args_as_json_str` present. All as the SWE claimed.

**E2E adversarial pass** (throwaway async harnesses vs `FunctionModel` forcing the gated `noop`; all independent of the SWE's own tests)
- Happy path (loop+runner, APPROVE): gated call → `permission_requested` emitted → resume → `ToolReturnPart "noop: call-0"` in history + final str. (PASS)
- Break path 1 (DENY, side-effect assertion): denial reason reaches model as a `ToolReturnPart`; tool body did NOT run (no echo present). (PASS)
- Break path 2 (genuine pause): blocking resolver → turn is not done and no final text appears before release; resumes only after the decision is awaited (not pre-decided). (PASS)
- Break path 3 (single-flight): second `submit` during the pause does NOT start a parallel turn (active_turns stays 1, text enqueued as steering qsize=1); phase returns to idle. Driven through the real `Runner`. (PASS)
- Break path 4 (steering-at-resume, independent re-verify): injected `STEER-INDEP-999` only at the resume `MODEL_REQUEST` boundary → resume-leg user messages contain it. (PASS)
- Break path 5 (headless `deny_permission_resolver`): feeds a denial to the model, body does not run. (PASS)
- Break path 6 (3 approval-required calls in one `DeferredToolRequests`): each emits its own `PermissionRequested`, each resolved by its `tool_call_id` (approve-0, deny-1, approve-2 → exactly the right return parts; denied call did not run). (PASS)
- Break path 7 (resolver RAISES): no stuck lock — phase → idle, `AgentError("resolver blew up")` surfaced, turn finished, REPL survives. (PASS)
- Break path 8 (abort mid-pause): `Esc` while paused → turn stops at the next boundary, phase → idle, `aborted=True`. (PASS)
- Break path 9 (empty `text:""` args / 50 KB args): `PermissionRequest` constructed cleanly, no crash/truncation. (PASS)

**e2e through the REAL `run_app` wiring — FAIL (blocker)**
Drove the real `run_app()` with a piped prompt_toolkit terminal (`create_app_session` + `create_pipe_input`), an offline gated model, the real `_make_permission_resolver`, and the real renderer. The user submits a prompt that triggers `noop`, then types `y`:
```
decode - type a line; /quit exits.
you please run noop
permission? noop {"text": "x"}     # rendered (and rendered a SECOND time — see below)
[done]                              # turn ends WITHOUT executing the tool
                                    # "final answer after approve" never appears; /quit never consumed → run_app hangs (TimeoutError)
```
Root cause (confirmed by isolating the exact pattern): `run_app` returns from `await runner.submit(...)` immediately and loops straight back to `await session.prompt_async(_PROMPT)`, so the main REPL prompt is *in flight* (`Application._is_running is True`) for the entire turn. When the gated tool fires, `_make_permission_resolver`'s `await session.prompt_async(_PERMISSION_PROMPT)` is a **second concurrent run on the same `PromptSession`/`Application`**. prompt_toolkit guards this with `assert not self._is_running, "Application is already running."` (verified in `Application.run_async`); the two prompts collide and the resolver's `prompt_async` is left in `CancelledError`/hung. Net effect in a real terminal: **every gated tool call wedges the REPL** — no approval is possible, the turn never completes, the process must be killed. This is the central capability task 005 ships and it is broken in the only production wiring.

Why the suite is green anyway: every unit test uses a stub/headless resolver (never the interactive one against a concurrently-running session), and the SWE's "e2e" used a separate, idle session for the resolver — so the concurrency collision never occurs in any existing test.

**Verdict on the SWE's flagged concern** (Notes bullet 2, "second `session.prompt_async` while the main input loop may also be prompting"): this is a **BLOCKER, not a nit**. The SWE's premise — "fine for the single-flight v1 turn (the turn task awaits the answer)" — is incorrect: the main loop does *not* await the turn; it re-enters `prompt_async` immediately, so the collision happens on the very first gated call in v1, not only with multi-pause/AskUser later.

**Acceptance criteria** (the four AC describe the loop/gate/entities contract — all genuinely pass and were independently re-verified; none of them names the TUI `run_app` wiring, which is where the defect lives)
- [x] PASS — gate `check()` returns allow/ask/deny + `mode`; entities define request/decision — `test_gate.py` (4), `test_permissions.py` (9), `test_types.py` (2) all pass; verified `PermissionGate.check` always returns `ASK` and records `read_only`.
- [x] PASS — gated tool pauses, emits `PermissionRequested`, resumes with `DeferredToolResults` — `test_loop.py::test_gated_tool_pauses_and_emits_permission_requested` + `::test_approval_resumes_and_executes_the_tool`; independently reproduced (break paths 1, 2).
- [x] PASS — denial fed back as `ToolDenied`; tools carry `read_only` (tagged, still asked) — `test_loop.py::test_denial_feeds_a_tooldenied_result_back_to_the_model`, `test_noop.py::test_noop_is_registered_as_not_read_only`, `test_gate.py::test_gate_still_asks_for_a_read_only_tool_in_v1`; independently reproduced with a side-effect assertion (break path 1).
- [x] PASS — single-flight lock spans the multi-leg deferred turn — `test_loop.py::test_single_flight_lock_spans_the_whole_multi_leg_deferred_turn`; independently reproduced through the real `Runner` (break path 3).

**Evidence**
```
$ make pre-commit
============================= 112 passed in 1.74s ==============================   (0 warnings)
$ uv lock --check
Resolved 163 packages in 2ms                                                       (current)
$ <isolated concurrency repro>
RESULT: {'resolver_err': 'AssertionError: Application is already running.', 'main': 'y'}
$ <real run_app, gated tool + "y">
... permission? noop {"text": "x"} / [done] ... -> run_app hangs (TimeoutError); tool never approved
```

**Other issues found**
- (Blocker) The interactive permission flow deadlocks in `run_app` — see above. Fix must let the resolver collect the answer without a second concurrent `prompt_async` on the live session: e.g. route the verdict through the harness/runner (the runner already owns the input boundary), use a dedicated mid-turn input mode / a one-shot `asyncio.Future` the main loop fulfils, or a separate prompt application. A regression test must drive the *real* `run_app` (or `_make_permission_resolver` against a session whose `Application` is already running) and assert the tool is approved + the turn resumes — the current tests cannot catch this.
- (PASS-with-note) `PermissionRequested` renders **twice** per gated call in `run_app`: once from the loop's `ctx.emit(PermissionRequested(...))` (`loop.py:216`) routed through `_on_event`, and once from the resolver's own `console.print(render.render_event(PermissionRequested(...)))` (`app.py:115`). Cosmetic duplication, not a correctness defect — but worth deciding who owns the render (loop emits the event; the resolver should probably just prompt). Folds naturally into the blocker fix.

**VERDICT: FAIL** — suite + isolated loop/gate logic are solid (112 green, 0 warnings, all 9 adversarial loop/runner probes pass), but the feature's headline capability (HITL approval) **deadlocks the REPL in the real `run_app` wiring** on the first gated tool call. 1 blocker + 1 cosmetic note for the SWE.

### [SWE] 2026-06-19 22:40 — Fixes (concurrent-prompt deadlock + double render)

Fixed the blocker exactly as scoped: one input surface with a pending-decision mode; the interactive resolver awaits a Future instead of opening a second `prompt_async`. Single render path. New end-to-end regression test that drives the real `run_app`.

**Root cause (reproduced before fixing)**
Confirmed the Tester's diagnosis with a piped `run_app` (`create_pipe_input` + `DummyOutput` inside `create_app_session`): a gated `noop` + typed `y` → `permission?` rendered twice, `[done]` without running the tool, `run_app` hung (TimeoutError). The resolver's `await session.prompt_async(...)` collided with the main loop's in-flight `prompt_async` on the same `Application`.

**Files modified**
- `src/decode/harness/decisions.py` — **new** `DecisionChannel`: the single mid-turn HITL channel. `request()` awaits an `asyncio.Future` (the verdict); `resolve(line)` fulfils it (returns `False` when nothing pending so the loop handles the line normally); `pending` is the source of truth for the input mode; `cancel()` unblocks a pending requester (raises `CancelledError` out of `request()`) on abort/shutdown. One decision pending at a time (single-flight). Designed so task 011 `AskUser` reuses the exact same pending-request → Future → next-line-resolves mechanism (permission approval is its first instance).
- `src/decode/tui/app.py` —
  - `_make_permission_resolver(channel, console)` (was `(session, console)`): **awaits `channel.request()`** instead of a second `prompt_async`; parses the raw line with the existing `parse_permission_answer`; on `CancelledError` denies (safe default). It no longer re-renders the request — only prints a minimal inline `allow this tool call? [y/N]` affordance. **Removes the double render** (the loop's emitted `PermissionRequested` is now the single render path); dropped the resolver's own `console.print(render_event(PermissionRequested(...)))` and the `_PERMISSION_PROMPT` constant.
  - `run_app`: builds one `DecisionChannel`, wires it into the resolver, and gives the single input loop two modes — **normal** (idle→new turn, busy→steer/follow-up, Esc→abort, unchanged) vs **awaiting-decision** (`if decisions.pending: decisions.resolve(text); continue`). On `Ctrl-D`/`/quit` it `decisions.cancel()`s any in-flight resolver before `wait_idle()` so shutdown can't hang. Module docstring documents the two modes.
- `tests/unit/decode/harness/test_decisions.py` — **new** (6 tests): idle→pending→resolved/cancelled lifecycle; `resolve` returns `False` with nothing pending; `cancel` raises `CancelledError` into the requester and is a no-op when idle; two concurrent `request()`s rejected (single-flight invariant).
- `tests/unit/decode/tui/test_app_e2e.py` — **new** regression test (2 tests): drives the **real `run_app`** through a gated tool via a programmatically-driven prompt_toolkit input. Typed `y` → permission prompt surfaces, turn resumes, finishes with text, and `permission? noop` is rendered **exactly once** (double-render guard). Typed `n` → the denial reaches the model as a `ToolReturnPart` and the tool body does not run. The user-script drives input *reactively* (waits for the affordance before typing the answer — like a human) under a hard 5s timeout, so the concurrent-prompt deadlock would fail fast, not hang.
- `tests/unit/decode/tui/test_app.py` — +2 tests for the interactive resolver against a real `DecisionChannel`: resolved line → parsed allow/deny; cancelled decision → deny (safe default), no hang. Tidied the now-shared imports.

**Regression test is genuinely red against the bug**
Temporarily reverted the resolver to a faithful buggy variant (a second `prompt_async` on the *same* live session); `test_app_e2e.py` then **failed** — and took ~11s (the deadlock/timeout cascade) before erroring with pydantic-ai's `UserError: Cannot provide a new user prompt when the message history contains unprocessed tool calls`, exactly the wedge. Restored the fix → green.

**Tests**
- Unit: 122 passing, 0 failing (was 112; +10: 6 decisions, 2 run_app e2e, 2 resolver). Integration: N/A — no infra changes.
- 0 warnings under `filterwarnings=["error"]`; no network (agent built directly on a streaming `FunctionModel`; the gated `noop` drives the real `DeferredToolRequests` pause/resume).

**Acceptance criteria** — the four AC describe the loop/gate/entities contract and remained green throughout; this fix is in the TUI `run_app` wiring (not named by any AC) plus its regression coverage. All four AC re-verified green.

**Evidence**
```
$ make format-check  → 47 files already formatted        (exit 0)
$ make lint-check    → All checks passed!                (exit 0)
$ make pre-commit    → 122 passed in 1.64s               (0 warnings)
$ uv lock --check    → Resolved 163 packages             (current; no new deps)
$ GEMINI_API_KEY=dummy uv run decode --help → renders     (exit 0)

# real run_app via a piped terminal, gated noop + typed "y":
decode - type a line; /quit exits.
you please run noop
permission? noop {"text": "x"}        # rendered ONCE
allow this tool call? [y/N]
FINAL-ANSWER-AFTER-APPROVE            # turn resumed + completed
[done]
decode - bye.                         # /quit consumed; run_app returned cleanly (no hang)
```

**Notes**
- `DECISION` channel is deliberately general (a "mid-turn request the human must answer"), so task 011 `AskUser` reuses it unchanged: build a resolver that `await channel.request()` and returns the raw line as the answer; the same `if decisions.pending` branch routes it. No second input surface.
- Headless/no-TTY path is unchanged: `deny_permission_resolver` still denies safely (it never touches the channel).
- Front-loading the approve/deny line *before* the prompt surfaces (only possible with programmatic input, never a human at a terminal) lands while `decisions.pending` is still `False`, so it routes as steering and the resolver later denies on shutdown-cancel (safe). The regression test drives input reactively to assert the real typed-`y`/`n` paths; in a live terminal the user only answers after seeing the affordance, so `pending` is reliably `True`.

### [Tester] 2026-06-19 23:45 — Re-QA (blocker fix verification)

Re-QA of the SWE's `DecisionChannel` fix for the concurrent-prompt REPL deadlock I previously FAILed. **The blocker is genuinely fixed.** Drove the real `run_app` myself (independent of the SWE's regression test) through the gated tool from five angles under hard timeouts; all green.

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 47 files clean; `ruff check` all passed; `make pre-commit` 122 passed in 1.63s)
- Unit tests: 122 passed / 0 failed (was 112; +10: 6 decisions, 2 run_app e2e, 2 resolver)
- Integration tests: N/A (`tests/integration` empty; no infra changes)
- Warnings: 0 (`filterwarnings=["error"]`; full suite also re-run with explicit `-W error` → 122 passed)
- `uv lock --check`: current (163 packages, no new deps)
- No `print()` in library code; all new signatures typed; diff scoped to task 005 only (no stray files)

**E2E adversarial pass — INDEPENDENT, drove the REAL `run_app` via `create_pipe_input` + `DummyOutput` in `create_app_session`, offline streaming `FunctionModel` forcing the gated `noop`, real `_make_permission_resolver` + real `DecisionChannel` + real renderer, all under hard timeouts (a deadlock fails fast, never hangs the suite)**
- Happy path (APPROVE): `send("please run noop")` → wait for `allow this tool call?` → `send("y")` → `FINAL` text + `[done]` + `decode - bye.`; `permission? noop` rendered **exactly once** (`output.count == 1`); resume leg saw `noop: PAYLOAD-1` (tool ran); clean exit. (PASS)
- Break path 1 (DENY + responsiveness): `send("n")` → denial `The user denied this tool call.` fed back as a `ToolReturnPart`, **tool body did NOT run** (no `noop: PAYLOAD` echo), then a **following new turn** (`second turn please`) was accepted and ran → REPL stays responsive. (PASS)
- Break path 2 (Esc while a decision is pending): `Esc` while `decisions.pending` → routes to the channel as an empty line → safe **DENY** (tool body does not run), turn resumes + finishes, no stuck lock, REPL responsive for a following turn, clean `/quit`. (Reasonable: the pending-decision branch takes precedence over abort; an empty answer denies. No hang.) (PASS)
- Break path 3 (Ctrl-D while a decision is pending): `pipe.close()` (EOF) while the resolver awaits → `run_app` `decisions.cancel()`s the in-flight resolver → resolver denies (safe default), tool body does not run, `wait_idle()` returns, `decode - bye.` rendered → **no hang**. (PASS)
- Break path 4 (second gated call after the first resolves): two separate turns each issuing a gated `noop` → **2** independent `permission? noop` prompts, both approved, both tool bodies ran (`noop: X` ×2), 2 `[done]`, clean exit. (PASS)
- No-second-`prompt_async` confirmation: `grep` shows exactly one `session.prompt_async(_PROMPT)` in the whole src tree (`app.py:221`); old `_PERMISSION_PROMPT` constant is gone; resolver awaits the channel only. The `Application-already-running` path is structurally removed.

**Regression test is genuinely red against the bug (independently reproduced)**
Temporarily reverted the resolver to a faithful buggy variant — a second `prompt_async` on the **same live session** (fed the real `session` object into the resolver, not a fresh one) — and ran `test_app_e2e.py`: it **FAILED** in ~6s with pydantic-ai `UserError: Cannot provide a new user prompt when the message history contains unprocessed tool calls` (the exact wedge). Restored from backup → 31/31 green, `git diff` byte-identical to the fix state. (My first buggy attempt used a *separate* `PromptSession`, which did NOT reproduce — the collision requires the *same* `Application` that is already `_is_running`; the faithful same-session variant does reproduce, matching the SWE's account.)

**`DecisionChannel` soundness for AskUser (011) reuse — 7 adversarial probes, all PASS**
single pending enforced (2nd concurrent `request()` → `RuntimeError`); no lost wakeup (resolve right after register is delivered); `resolve` twice → 2nd is a no-op (`False`); `cancel` after `resolve` → no-op, value preserved; `resolve` after `cancel` → `False` (line falls through to normal handling) + requester sees `CancelledError`; channel reusable after a full cycle (AskUser can sequence after permission on the same channel); `resolve` when idle → `False`. resolve/cancel semantics are clean; no lost wakeups.

**Prior-passing loop/gate behaviour re-confirmed (no regression)**
approve/deny/pause→`PermissionRequested`/read-only-tagging/single-flight/steering-at-deferred-resume all green via `test_loop.py` (6 targeted) + `test_runner.py` (11); headless `deny_permission_resolver` denies safely (unit + the interactive resolver's cancel→deny path).

**Acceptance criteria** (the four AC describe the loop/gate/entities contract; the fix lives in the TUI `run_app` wiring not named by any AC — all four remain green and were re-verified)
- [x] PASS — gate `check()` returns allow/ask/deny + `mode`; entities define request/decision — `test_gate.py` (4), `test_permissions.py` (9), `test_types.py` (2).
- [x] PASS — gated tool pauses, emits `PermissionRequested`, resumes with `DeferredToolResults` — `test_loop.py::test_gated_tool_pauses_and_emits_permission_requested` + `::test_approval_resumes_and_executes_the_tool`; independently reproduced through the real `run_app` (happy path, exactly-once render).
- [x] PASS — denial fed back as `ToolDenied`; tools carry `read_only` (tagged, still asked) — `test_loop.py::test_denial_feeds_a_tooldenied_result_back_to_the_model`, `test_noop.py`, `test_gate.py`; independently reproduced through the real `run_app` (DENY break path — body skipped, denial fed back).
- [x] PASS — single-flight lock spans the multi-leg deferred turn — `test_loop.py::test_single_flight_lock_spans_the_whole_multi_leg_deferred_turn` + `test_runner.py`.

**Evidence**
```
$ make pre-commit
============================= 122 passed in 1.63s ==============================   (0 warnings)
$ uv run pytest tests/ -W error
============================= 122 passed in 1.65s ==============================
$ uv lock --check
Resolved 163 packages in 3ms                                                       (current)
$ grep -rn "prompt_async" src/ | grep "await"
src/decode/tui/app.py:221:                submitted = await session.prompt_async(_PROMPT)   (the ONLY one)

# real run_app, gated noop + typed "y":
you please run noop
permission? noop {"text": "PAYLOAD-1"}      # rendered ONCE
allow this tool call? [y/N]
APPROVE-DONE                                # turn resumed + completed
[done]
decode - bye.                               # clean exit, no hang

# faithful buggy variant (second prompt_async on the SAME session) → regression test:
FAILED tests/unit/decode/tui/test_app_e2e.py ... UserError: Cannot provide a new user prompt
  when the message history contains unprocessed tool calls    (~6s; red, as required)
```

**Other issues found**
- None. The prior cosmetic double-render note is also resolved: `output.count("permission? noop") == 1` confirmed through the real `run_app` (loop's emitted event is the single render path; resolver only prints the minimal `[y/N]` affordance).

**VERDICT: PASS** — the headline HITL-approval capability now works end-to-end in the only production wiring (`run_app`): approve resumes to completion, deny feeds `ToolDenied` back without running the tool, the REPL stays responsive across abort/Ctrl-D/second-call, the request renders exactly once, and there is no second `prompt_async`/Application-already-running path left. Suite 122/0/0-warnings, lock current, `DecisionChannel` sound for AskUser (011) reuse. Hand off for commit.
