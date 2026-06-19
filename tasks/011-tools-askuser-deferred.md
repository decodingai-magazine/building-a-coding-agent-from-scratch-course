---
id: 011-tools-askuser-deferred
feature: m1-vanilla-agent
status: done
---

# Tools: AskUser (deferred)

## Scope
The one blocking tool — the model asks the human a question, routed through the same deferred-pause path as approvals ([ADR-0002 §2](../docs/adr/0002-milestone-1-vanilla-agent-architecture.md)).

## Acceptance criteria
- [x] `tools/askuser.py` `ask_user(question)` produces a deferred request that pauses the turn and surfaces the question in the TUI.
- [x] The user's typed answer becomes the tool result; only one AskUser in flight at a time.
- [x] Errors cleanly when no TUI is attached (headless-safe).
- [x] Tested with a fake resolver (no real terminal).

## Out of scope
- Structured multiple-choice questionnaires.

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. Uses the deferred mechanism (not an inline Future) for M7 HITL transport.

### [SWE] 2026-06-20 10:45 — Implementation

**Files modified**
- `src/decode/tools/askuser.py` — NEW: the blocking `ask_user(ctx, question)` tool. Emits `AskUserRequested`, then awaits the human's free-text answer via `ctx.deps.resolve_user_question` (the SAME single `DecisionChannel` the permission resolver uses — no second `prompt_async`). Headless-safe: `NoInteractiveUserError` (raised by the `deny_user_question_resolver` default) and a cancelled request both map to a clean `pydantic_ai.ModelRetry` (never a hang). NOT gated — it is the human-interaction tool itself, so it never raises `ApprovalRequired`.
- `src/decode/agent/deps.py` — added the `resolve_user_question: UserQuestionResolver` hook to `AgentDeps` (alongside `resolve_permission`); new `UserQuestionResolver = Callable[[str], Awaitable[str]]` type.
- `src/decode/tools/registry.py` — registered `ask_user`; added a `gated: bool = True` flag to `ToolSpec` and set `gated=False` for `ask_user` (documents the skip-the-gate policy in one place).
- `src/decode/tui/app.py` — added `_make_user_question_resolver(channel, console)` (renders a "type your answer" affordance, awaits the SAME `decisions` channel, returns the raw line; lets `CancelledError` propagate so `ask_user` maps it to `ModelRetry`); wired it into `run_app`'s `AgentDeps`. Both resolvers share one channel → a permission ask and an ask_user question can never collide.
- `tests/unit/decode/tools/test_askuser.py` — NEW: emits the question, returns the resolved answer, passes the question to the resolver, runs without approval (not gated), headless → ModelRetry, cancellation → ModelRetry, registration flags.
- `tests/unit/decode/agent/test_loop.py` — added the real-loop `ask_user` tests: a `FunctionModel` forcing the call, a fake resolver supplies the answer and it reaches the model as a `ToolReturnPart` (no `PermissionRequested` for it); plus a headless run where the `ModelRetry` lets the turn still finish (no hang).
- `tests/unit/decode/tui/test_app_e2e.py` — added the headline `run_app` regression: ask_user mid-turn through real `run_app` + `create_pipe_input` — the question surfaces, a typed line answers it, the answer reaches the model, no REPL deadlock.
- `tests/unit/decode/tui/test_app.py` — `_make_user_question_resolver` returns the typed line verbatim; propagates cancellation.
- `tests/unit/decode/tools/test_registry.py` + `tests/unit/decode/agent/test_deps.py` — assert `ask_user` is registered (ungated, not read-only) and the new deps field; threaded `resolve_user_question` through the other tool tests' deps builders (`deny_user_question_resolver`).

**Tests**
- Unit: 273 passing, 0 failing (`make pre-commit`). Up from 268 (+8 askuser, +2 loop, +2 app, +1 registry, plus deps churn).
- Integration: N/A — no infra changes.

**Acceptance criteria**
- [x] `ask_user(question)` pauses the turn and surfaces the question in the TUI — `tests/unit/decode/agent/test_loop.py::test_ask_user_answer_reaches_the_model_as_the_tool_result`, `tests/unit/decode/tui/test_app_e2e.py::test_run_app_ask_user_surfaces_the_question_and_a_typed_line_answers_it`.
- [x] Typed answer becomes the tool result; only one in flight at a time — same two tests + `DecisionChannel` single-flight (`tests/unit/decode/harness/test_decisions.py::test_two_concurrent_requests_are_rejected`; both resolvers share one channel).
- [x] Errors cleanly when no TUI is attached (headless-safe) — `tests/unit/decode/tools/test_askuser.py::test_ask_user_model_retries_when_no_interactive_user_is_attached` + `test_loop.py::test_ask_user_model_retries_headless_and_the_turn_still_finishes`.
- [x] Tested with a fake resolver (no real terminal) — all of the above; the `run_app` test uses `create_pipe_input`.

**Evidence**
```
$ make pre-commit
... ruff format --check: 65 files already formatted
... ruff check: All checks passed!
tests/unit/decode/tools/test_askuser.py ........                         [ 37%]
tests/unit/decode/tui/test_app.py .........................              [ 93%]
tests/unit/decode/tui/test_app_e2e.py ...                                [ 94%]
============================= 273 passed in 5.06s ==============================
$ uv lock --check
Resolved 166 packages in 3ms
$ uv run python -c "<smoke: build production agent + drive interactive/headless resolvers>"
ask_user registered on production agent: True
interactive ask_user answer: 'src/main.py'
headless resolver refuses cleanly: No interactive user is attached, so the  ...
```

**Notes**
- Design choice (per task scope): `ask_user` awaits the answer **inline inside the tool body** via `ctx.deps.resolve_user_question`, reusing the existing `DecisionChannel` (task 005) rather than the `DeferredToolRequests` approval path — it is not gated, so it never reaches the gate. The turn task blocks at that `await` while the main `prompt_async()` loop stays responsive and routes the next line into `channel.resolve(...)`; the channel's single-flight invariant guarantees permission and ask_user can't be pending simultaneously. ADR-0002 §2 names AskUser under "HITL via deferred-tool-requests"; I read that as "the same mid-turn human-in-the-loop pause," which the shared `DecisionChannel` is — not a literal `DeferredToolRequests` round-trip, which would require gating it. The renderer (`AskUserRequested`, task 003) and event union already handled it; no renderer change needed.
- Adding the required `resolve_user_question` field to `AgentDeps` (mirroring the required `resolve_permission`) touched every `AgentDeps(...)` construction site in the tool tests — those were updated to pass `deny_user_question_resolver`. No behaviour change to those tests.
- Per the task instructions I stayed on `feat/m1-vanilla-agent` and did not commit.

### [Tester] 2026-06-20 12:30 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 65 files; `ruff check` all passed)
- Unit tests: 273 passed / 0 failed
- Integration tests: 0 collected (N/A — no infra changes; `make integration-tests` exits 5 "no tests")
- `uv lock --check`: PASS (resolved 166 packages, no drift)
- Warnings: 0 (`filterwarnings=["error"]` — a warning would have failed the run)

**Design interpretation (confirmed acceptable for M1)**
The AC text "produces a deferred request" is satisfied in *intent*: `ask_user` is an ungated inline-await on the shared task-005 `DecisionChannel` — a real mid-turn human pause that fragments the turn into legs — not a literal `DeferredToolRequests` round-trip. Per the task scope ("reuse DecisionChannel, don't gate it") and ADR-0002 §2's "same mid-turn HITL pause," this is the correct M1 shape; literal deferred-tool durability is an M7/M9 concern. Judged PASS against scope, not the literal word.

**E2E adversarial pass** (15 probes vs the REAL `build_agent()` loop with `FunctionModel` forcing `ask_user`; 5 probes vs the REAL `run_app` + `create_pipe_input`; every block under a hard timeout so a hang fails fast)
- Happy path: `FunctionModel(call_tools=["ask_user"])` → question emitted as `AskUserRequested`, fake answer reaches the model as a `ToolReturnPart` (PASS)
- Break path 1 (boundary: verbatim free-text): answers with leading/trailing/inner spaces, unicode (`déployer … 北京 — 🚀`), and the empty string each reach the model as the tool result *exactly* (`r == answer`) (PASS)
- Break path 2 (not-gated assertion): zero `PermissionRequested` events precede `ask_user`; no double-prompt (PASS)
- Break path 3 (failure mode: headless): `deny_user_question_resolver` → clean `ModelRetry`, model recovers, `turn_finished` emitted, NO hang under 5s timeout (PASS)
- Break path 4 (state edge: cancellation mid-pending): `channel.cancel()` while `ask_user` awaits → clean `ModelRetry`; `channel.pending` flips True→False, no stuck lock (PASS)
- Break path 5 (single-flight collision): a 2nd concurrent `channel.request()` raises `RuntimeError` while one is pending; first requester still resolves — permission + ask_user can never be pending at once (PASS)
- Break path 6 (sequence within one turn): `ask_user` then a gated `noop` in the same turn — both work; answer `use config-A` reaches the model AND `noop: payload` executes after approval AND final text streams (PASS)
- Break path 7 (malformed input: `question` as int `12345`): pydantic-ai coerces, no crash, no `AgentError`, turn finishes (PASS)
- Break path 8 (real `run_app`, empty-line answer): scripted user types `""` at the `type your answer:` affordance → empty string reaches the model verbatim, turn 1 completes (PASS)
- Break path 9 (real `run_app`, following turn): a second user prompt after the ask_user turn runs to completion — REPL not deadlocked, no second `prompt_async()` / "Application already running" (PASS)
- Break path 10 (real `run_app`, mis-parse guard): the typed answer is NOT mis-read as a y/N permission (`allow this tool call?` never appears) nor as steering — `decisions.pending` is checked before quit/abort/submit (PASS)
- Break path 11 (real `run_app`, Ctrl-D mid-pending): closing stdin while `ask_user` is pending → `decisions.cancel()` unblocks the resolver, turn winds down, `run_app` prints "bye" and exits cleanly, no hang (PASS)

**Acceptance criteria**
- [x] PASS — `ask_user` produces a (mid-turn HITL) deferred request that pauses the turn and surfaces the question in the TUI — `test_loop.py::test_ask_user_answer_reaches_the_model_as_the_tool_result`, `test_app_e2e.py::test_run_app_ask_user_surfaces_the_question_and_a_typed_line_answers_it`; renderer wires `AskUserRequested` at `src/decode/tui/render.py:83`; emit at `src/decode/tools/askuser.py:92`. Adversarial: happy path + break paths 8-11.
- [x] PASS — the user's typed answer becomes the tool result; only one AskUser in flight at a time — answer→tool-result proven in the loop and real-`run_app` tests + adversarial break paths 1 & 8 (verbatim incl. empty/unicode); single-flight via the shared `DecisionChannel` (`harness/test_decisions.py`) + adversarial break path 5.
- [x] PASS — errors cleanly when no TUI is attached (headless-safe) — `test_askuser.py::test_ask_user_model_retries_when_no_interactive_user_is_attached`, `test_loop.py::test_ask_user_model_retries_headless_and_the_turn_still_finishes`; adversarial break path 3 (hard timeout, no hang).
- [x] PASS — tested with a fake resolver (no real terminal) — all unit tests use fake/headless resolvers; the `run_app` regression uses `create_pipe_input` + `DummyOutput` (no real TTY).

**Evidence**
```
$ make pre-commit
... ruff format --check: 65 files already formatted
... ruff check: All checks passed!
============================= 273 passed in 5.01s ==============================
$ uv lock --check
Resolved 166 packages in 3ms
$ uv run python /tmp/probe_askuser.py    # 15 probes vs real agent loop
... 15 probes, 0 failed
$ uv run python /tmp/probe_runapp.py     # real run_app: empty answer + following turn
... 4 probes, 0 failed
$ uv run python /tmp/probe_ctrld.py      # Ctrl-D mid-pending
[PASS] Ctrl-D mid-pending: run_app exits cleanly (no hang) :: saw_bye=True
```

**Other issues found**
- None blocking. PASS-with-note (no action required): while an `ask_user` is pending, a typed `/quit` is consumed as the *answer* `"/quit"` rather than quitting (the `decisions.pending` check precedes `is_quit_command`). This is consistent with the permission-prompt behaviour and is the correct single-input-surface semantics; `Ctrl-D` is the always-available escape and unblocks cleanly (break path 11). Worth a one-line user-facing note in a later docs pass, not a code change.
- The `console.print(...)` calls in `tui/app.py` are `rich.console.Console.print` (the sanctioned user-facing output channel per AGENTS.md), not stray `print()`; library code uses `logger`. No regression.
- Diff is scoped: the 5 touched tool tests (`test_bash/files/noop/tasks/web`) are additions-only — an import + the now-required `resolve_user_question` deps field. No behaviour change, no `git add -A` leakage.

**VERDICT: PASS**
