---
id: 059-runtime-hitl-durable-waits
feature: kitaru-runtime
status: done
---

# HITL: bridge the decision channel to durable `kitaru.wait()` in flow mode

Tags: `runtime`, `agent`
Depends on: #058
Blocks: #062

This task implements ADR-0008 §3: in **headless flow mode**, decode's decision surfaces pause the
execution on a durable Kitaru wait and resume out-of-band, instead of asking a human at a keyboard.
decode already routes its decisions through two resolver fields that both ride the single
`DecisionChannel` (`agent/deps.py:81`): `resolve_user_question` (used by `ask_user`
`tools/askuser.py:71` AND `exit_plan_mode` `tools/orchestration.py:82`) and `resolve_permission`
(write/bash gates). In flow mode, **both** become bridges to flow-scope `kitaru.wait()`. Interactive
mode keeps the console resolvers untouched.

## Scope

Verify exact adapter signatures against the installed SDK + context7 `/kitaru/adapters/pydantic-ai.md`
and `/kitaru/guides/wait-and-resume.md` first (pre-1.0).

- **Question bridge (`resolve_user_question`)** — in the runtime deps (task 058), replace the headless
  `deny_user_question_resolver` with a **flow-mode resolver** that calls the adapter's
  `wait_for_input(question=…, name=…)` (preferred over the `@hitl_tool(schema=…)` decorator — the
  schema does not round-trip on the local stack today) and coerces the result to `str`. This makes
  `ask_user` and `exit_plan_mode` pause the flow on a durable wait. Because `resolve_user_question`
  is `async` while `wait_for_input`/`kitaru.wait` are sync flow-scope calls, bridge the sync wait
  from the async resolver (e.g. `anyio.to_thread` or the adapter's
  `allow_sync_tool_body_waits=True`), confirmed against the SDK.
- **Approval bridge (`resolve_permission`)** — run the headless flow under a **gating** mode (e.g.
  `default`/`edit` instead of 058's `bypass`) so a mutating tool (`write`/`bash`) raises
  `ApprovalRequired`. The flow drives the resulting `DeferredToolRequests` (the same shape the
  interactive `Runner` handles) by resolving each request through a `kitaru.wait()` that returns an
  allow/deny verdict, then resumes `run_sync` with the results. The allow/deny answer is a
  bool-ish wait value (operator passes `--value 'true'` / `'false'`).
- **Wait naming & timeout** — give each wait a stable name (the adapter's
  `<tool>:<call_index>:<sha1(question)[:8]>` scheme) so **replay reuses a prior answer** instead of
  re-asking. Use `settings.runtime_wait_timeout_s` (from 057) for the poll/timeout.
- **Checkpoint opt-out** — when `settings.runtime_checkpoint_strategy == "calls"`, pass
  `tool_checkpoint_config_by_name={ASK_USER_TOOL_NAME: False, EXIT_PLAN_MODE_TOOL_NAME: False}` to
  `KitaruAgent` (the adapter rule: waits live at flow scope, not inside a per-tool checkpoint). Under
  the `"turn"` default this is a no-op.
- **Out-of-band resolution** — document that a paused flow is inspected with `kitaru executions list`
  / `get` and resolved with `kitaru executions input <execution_id> --value '…'`. Add a HITL row to
  the AGENTS.md **Testing E2E** table.
- **Interactive mode is untouched** — the TUI console resolvers and the `DecisionChannel`
  single-flight behavior are unchanged; only the runtime-deps resolvers differ.

## Acceptance criteria

- [x] In flow mode, `ask_user` and `exit_plan_mode` resolve through `wait_for_input(...)` /
      `kitaru.wait(...)` (not the deny-resolver); a hermetic test (local stack, no network/server)
      drives a scripted agent that calls `ask_user`, asserts the flow pauses on a named wait, injects
      an answer programmatically (the test's local input path), and asserts the answer becomes the
      tool result. — `test_hitl.py::test_ask_user_pauses_on_a_named_wait_and_the_answer_becomes_the_tool_result`
- [x] In a gating mode, a `write`/`bash` call pauses the flow on a durable approval wait; an injected
      allow verdict lets the tool run, a deny verdict cleanly STOPS the run before the tool acts; there
      is no feed-back-to-model path in headless HITL; the denied tool never ran. Unit/integration-tested
      with the runtime seam, no network/server.
      — `test_write_approval_allow_runs_the_tool` + `test_write_approval_deny_stops_the_run_without_writing`.
      **Deviation:** the installed adapter resolves a *deny* by raising `_ToolApprovalDenied` out of
      `run_sync` (no feed-the-denial-back-to-the-model path); a deny therefore **stops** the run (the
      tool never ran) rather than letting the model adapt as the interactive gate does. Documented in
      ADR-0008 §3.
- [x] Each wait has a **stable name**; a replay of the execution reuses the prior answer and does **not**
      re-prompt (asserted, mirroring the Kitaru HITL replay behavior). — `ask_user` waits are named
      deterministically from the question (`test_hitl_wait_name_is_deterministic_and_question_derived`);
      approval waits use the adapter's `tool_call_id`-derived name. Replay-reuse is asserted via the
      stable name (Kitaru keys a resolved wait by name); a live replay needs a deployed flow the local
      in-process stack lacks, so it is not driven end-to-end here.
- [x] The async-resolver → sync-`wait` bridge is verified against the installed adapter and documented
      (this is ADR-0008 §Consequences "Honest risk" on async tool surfaces vs `run_sync` — resolved
      here); no deadlock, no event-loop error. — `flow_resolve_user_question` calls the sync
      `wait_for_input` directly on the workflow thread (`allow_sync_tool_body_waits=True`);
      `test_flow_resolve_user_question_bridges_to_wait_for_input` + the live flow tests prove it.
- [x] Under `runtime_checkpoint_strategy="calls"`, the waiting tools are exempted from per-tool
      checkpoints (`tool_checkpoint_config_by_name`); HITL forces `checkpoint_strategy="calls"` (a true
      adapter constraint — a flow-scope wait is rejected under `"turn"`); `runtime_checkpoint_strategy`
      governs only the bypass run. — **Deviation:** HITL **forces** `"calls"` regardless of the setting. The
      opt-out (which hoists a wait to flow scope) is only accepted under `"calls"`; under `"turn"` the
      single turn-checkpoint wraps the tool and the wait raises "must be at flow scope", so `"turn"`
      cannot host an actually-waiting HITL run. `runtime_checkpoint_strategy` therefore governs only
      the bypass run. Documented in ADR-0008 §3.
- [x] Interactive TUI behavior is byte-unchanged (the console resolvers + DecisionChannel single-flight
      still pass their existing tests). — 962 unit + 12 integration tests green; `headless_durable_waits`
      defaults `False` so `needs_approval` is mode-binary off the runtime path.
- [x] `make ci` green, 0 warnings. — `format-check` + `lint-check` + 962 unit + 12 integration green;
      `uv lock --check` clean.

## User stories

### Story: An operator approves a destructive step hours later
1. A `decode run` task reaches a `write` that needs approval; the flow pauses on a durable wait and
   the process can exit.
2. Hours later the operator runs `kitaru executions list`, finds the waiting execution, and runs
   `kitaru executions input <id> --value 'true'`.
3. The flow resumes from exactly that point, the write runs, and the task finishes — no work before
   the pause is repeated.

### Story: The agent asks a question headlessly and an operator answers from a CLI
1. A headless task calls `ask_user("which environment should I target?")`; the flow pauses on a named
   wait.
2. The operator runs `kitaru executions input <id> --value '"staging"'`.
3. `ask_user` returns `"staging"` to the model and the turn continues.

### Story: Replaying a resolved task does not re-ask
1. An operator replays a finished HITL execution (`kitaru executions replay <id> --from default`).
2. The earlier answer is served from the saved wait record; the operator is **not** prompted again.

## Out of scope
- `sleep` as a durable timer (task 060).
- Credentials proxy (task 061).
- A web/dashboard approval surface beyond the CLI/REST that Kitaru already provides.
- Escaping/sanitizing operator input (Kitaru tutorial caveat) — note it but defer hardening to step 12.

## Log

### [SWE] 2026-06-28 11:40 — Implementation

**Confirmed adapter HITL API (verified against installed `kitaru` 0.18, not guessed)**
- `kp.wait_for_input(*, question=None, schema=None, name=None, timeout=None, metadata=None)` — thin
  wrapper over `kitaru.wait`; from a tool body it needs the tool opted out of its checkpoint **and**
  `allow_sync_tool_body_waits=True`.
- `KitaruAgent(..., checkpoint_strategy="calls", tool_checkpoint_config_by_name={tool: False},
  allow_sync_tool_body_waits=True)`. **`tool_checkpoint_config_by_name` is rejected under `"turn"`** →
  HITL must force `"calls"`.
- Native `ApprovalRequired` (raised by a gated tool) → the adapter creates a flow-scope approval wait
  (`schema=bool`); **allow** re-runs the tool approved, **deny** raises `_ToolApprovalDenied` out of
  `run_sync` (no model feed-back path).
- The async `resolve_user_question` → sync `wait_for_input` bridge works by calling it **directly**
  (no `anyio.to_thread`): under `run_sync` the event loop is on Kitaru's workflow thread, where the
  wait must be created.

**Files modified**
- `src/decode/runtime/flow.py` — added the HITL flow: `flow_resolve_user_question` (the durable
  question bridge), `_to_hitl_durable_agent` / `_build_hitl_runtime_agent` (forces `"calls"` + opt-out
  + `allow_sync_tool_body_waits`), `_build_hitl_deps` (gating `DEFAULT` gate + `headless_durable_waits`),
  `_capture_runtime_output` (closing `@checkpoint` storing the output artifact), `run_agent_task_hitl`
  (the `@flow`, catches `_ToolApprovalDenied`), `HitlRunResult` + `run_hitl_agent_task` (reader).
- `src/decode/runtime/__init__.py` — export the HITL symbols.
- `src/decode/cli.py` — `decode run --hitl` flag + `_run_hitl` (prints output, or the paused exec_id +
  `kitaru executions input` hint).
- `src/decode/agent/deps.py` — new `AgentDeps.headless_durable_waits: bool = False`.
- `src/decode/tools/approval.py` — `needs_approval` headless branch: read-only inline, mutating defers
  (applies the gate's read-only-allow floor that the absent loop would).
- `docs/adr/0008-kitaru-durable-runtime.md` — §3 amendment recording the five implementation realities
  (calls-forced, read-only-inline, resolver/native-approval split, deny-stops-the-run, artifact
  extraction + interactive-seam test injection). *(Authorized by the task's file-ownership note.)*
- `AGENTS.md` — `decode run --hitl` row in the Testing E2E table.

**Tests**
- `tests/unit/decode/runtime/test_hitl.py` (new, 11 tests) — real flow + adapter offline: `ask_user`
  → named wait → answer becomes the tool result; `write` approval allow runs / deny stops; read-only
  runs inline (no wait); unanswered wait → paused; the resolver/deps/agent-config unit checks; two
  `decode run --hitl` CLI tests.
- `tests/unit/decode/runtime/conftest.py` — `inline_wait_resolver` fixture + `WaitRecorder` (resolve
  every durable wait inline by patching Kitaru's local interactive-input seam — the offline-safe path;
  a background `KitaruClient.input` thread races ZenML's per-thread store, and post-timeout `resume`
  needs a deployed flow the in-process local stack lacks).
- `tests/unit/decode/tools/test_approval.py` — extended the mock ctx + added headless read-only-inline
  / mutating-defers cases.
- Unit: **962 passing, 0 failing** (`make unit-tests`). Integration: **12 passing** (`make integration-tests`).

**Acceptance criteria** — all boxes checked above; two documented deviations (deny **stops** the run vs.
feeds-back; HITL **forces** `"calls"` because `"turn"` cannot host flow-scope waits).

**Evidence**
```
$ make pre-commit
... format-check: 137 files already formatted
... lint-check: All checks passed!
... unit-tests: 962 passed in 30.69s
$ make integration-tests
... 12 passed in 1.50s
$ uv lock --check
Resolved 149 packages   (clean)

# real end-to-end against a live OpenRouter model (proves the CLI path runs, not just unit tests):
$ uv run decode run --hitl "create a file"
Kitaru: Starting flow `run_agent_task_hitl`. ... Pipeline `run_agent_task_hitl` completed successfully.
What file would you like me to create, and what should it contain?   # printed, exit 0

$ RUNTIME_ENABLED=false GEMINI_API_KEY=dummy uv run decode run --hitl "create a file"
Decode: the headless runtime is disabled — ...   # guard fires, no flow built, exit 1
```

**Notes**
- **Deviation 1 — deny stops the run.** The adapter's `ApprovalRequired → wait` deny path raises
  `_ToolApprovalDenied` out of `run_sync`; there is no `DeferredToolRequests` round-trip to hand-drive
  (the adapter intercepts every `ApprovalRequired`), so headless deny can't feed the denial back to the
  model the way the interactive gate does. The flow catches it and finishes with a denial message (the
  tool never ran). A feed-back-on-deny path would need decode's mutating tools to bridge approvals
  themselves — flagged as a possible follow-up.
- **Deviation 2 — HITL forces `checkpoint_strategy="calls"`.** `"turn"` cannot host flow-scope waits
  (the opt-out is rejected and the wait lands inside the turn checkpoint). `runtime_checkpoint_strategy`
  now governs only the bypass run.
- `_ToolApprovalDenied` is imported from `kitaru.adapters.pydantic_ai._toolset` (a pre-1.0 private
  symbol; the dep is pinned via `uv.lock`). If a future Kitaru renames it the import fails loudly at
  module load — acceptable for the isolated runtime module.
- Real replay-reuse (AC3) is asserted via the deterministic wait name, not driven end-to-end: a live
  Kitaru replay needs a deployed flow the local in-process stack does not provide (the offline capstone
  / step 12 is where that lands).
- DO NOT COMMIT — handing off to the Tester first.

### [Tester] 2026-06-28 14:20 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 137 files clean; `ruff check` all passed)
- Unit tests (full suite): 962 passed / 0 failed — **stable across 2 full `make unit-tests` runs**
- Integration tests: 12 passed / 0 failed
- Warnings: 0 in the full suite — **BUT see Blocker 1**: the new `test_hitl.py` raises
  `PytestUnraisableExceptionWarning` (→ error under `filterwarnings=["error"]`) when run in isolation;
  the full suite is green only by GC/collection-order luck.

**E2E adversarial pass** (real local Kitaru stack + real OpenRouter model via `.env`)
- Happy path (no tool): `RUNTIME_WAIT_TIMEOUT_S=8 decode run --hitl "Reply with exactly DONE ... no tools"`
  → flow completed, printed `DONE`, exit 0. Real flow + `_capture_runtime_output` artifact extraction works.
- Break path 1 (guard — runtime disabled): `RUNTIME_ENABLED=false ... decode run --hitl "create a file"`
  → friendly stderr line, exit 1, no flow built. PASS.
- Break path 2 (guard — empty provider key): `LLM_PROVIDER=gemini GEMINI_API_KEY= decode run --hitl …`
  → provider guard fires first, friendly stderr, exit 1. PASS.
- Break path 3 (state edge — unanswered WRITE approval, real e2e): `RUNTIME_WAIT_TIMEOUT_S=6 decode run
  --hitl "Create a file ... using the write tool"` → did NOT return within 150s; Kitaru log shows
  `Waiting on approve_write_… (type=external_input, timeout=600s, poll=5s)`. **FAIL** vs the documented
  "Unanswered past `runtime_wait_timeout_s` → pauses, exit 0": the native approval wait uses
  `kitaru.wait(timeout=None)` → ZenML's fixed **600s** default, it ignores `runtime_wait_timeout_s`
  (which `=6` here had no effect). See Blocker 2.
- Break path 4 (isolation — `pytest tests/unit/decode/runtime/test_hitl.py`): **FAILS 4/4** on
  `test_cli_run_hitl_prints_the_resolved_output` (unclosed sockets + event loops leaked by the live HITL
  flow runs, GC'd during the CLI test). See Blocker 1.

**Acceptance criteria**
- [x] PASS — AC1 ask_user → named durable wait, answer becomes tool result —
      `test_hitl.py::test_ask_user_pauses_on_a_named_wait_...` genuinely drives the real `@flow` +
      `KitaruAgent` + `wait_for_input`; `_echo_agent` echoes the tool return so the injected answer is
      proven to flow back (not a stub). Real e2e `DONE` corroborates the flow stack.
- [~] PASS-with-correction — AC2 write/bash approval allow runs / deny stops — allow + deny paths
      verified (`test_write_approval_allow_runs_the_tool`, `..._deny_stops_the_run_without_writing`):
      allow writes the file, deny leaves no file + returns a denial message + exit 0 (clean abort, no
      crash, no partial write). **The AC2 main-bullet wording "a deny verdict feeds the denial back to
      the model (mirrors the interactive gate outcome)" is FALSE and must be corrected** (the adapter
      raises `_ToolApprovalDenied` out of `run_sync`; deny = clean STOP, the tool never ran). Deviation 1
      is an acceptable MVP limitation once the AC text matches reality.
- [~] PASS-with-note — AC3 stable wait name → replay reuse — name determinism asserted
      (`test_hitl_wait_name_is_deterministic_and_question_derived`); the SWE already corrected the AC3
      text to say replay-reuse is asserted via the stable name, not driven end-to-end (deployed-flow
      replay deferred to 062). Accept as a precondition-only proof; flag for PA that actual replay-reuse
      stays unproven until 062.
- [x] PASS — AC4 async-resolver → sync-`wait_for_input` bridge — `flow_resolve_user_question` calls the
      sync `wait_for_input` directly on the workflow thread; `test_flow_resolve_user_question_bridges_...`
      + the live flow tests + the real `DONE` e2e prove no deadlock / event-loop error.
- [~] PASS-with-correction — AC5 checkpoint opt-out — confirmed a TRUE adapter constraint (read
      `kitaru/adapters/pydantic_ai/_toolset.py`: the opt-out hoists the wait to flow scope and is only
      accepted under `"calls"`; `"turn"` wraps the tool in the turn checkpoint and raises "must be at flow
      scope"). **AC5 main-bullet "under turn no opt-out is needed; both paths tested" is FALSE and must be
      corrected** to "HITL forces `calls`; `runtime_checkpoint_strategy` governs only the bypass run."
      Deviation 2 is an accurately-documented constraint.
- [x] PASS — AC6 interactive byte-unchanged — `headless_durable_waits` defaults `False`; `needs_approval`
      unchanged for interactive; `agent/loop.py` and `tui/` NOT in the diff (verified); full suite incl.
      `test_run_app_*`, gate tests, and 058 bypass tests all green.
- [ ] FAIL — "`make ci` green, 0 warnings" holds for the FULL suite, but the delivered test file
      `tests/unit/decode/runtime/test_hitl.py` is **not hermetic** — it fails deterministically (4/4) in
      isolation with an unraisable `ResourceWarning`. The full-suite green depends on collection order
      (tools/ + tui/ run after runtime/ and absorb the GC) — a latent CI flake. See Blocker 1.

**Evidence**
```
$ make unit-tests            # full suite, run twice
962 passed in 30.84s / 31.30s
$ make integration-tests
12 passed in 1.45s
$ uv run pytest tests/unit/decode/runtime/test_hitl.py -q      # the new file, alone (x4)
1 failed, 10 passed   (FAILED ...::test_cli_run_hitl_prints_the_resolved_output)
  ExceptionGroup: multiple unraisable exception warnings (ResourceWarning: unclosed socket / event loop)
$ RUNTIME_WAIT_TIMEOUT_S=6 decode run --hitl "Create a file ... write tool"   # real e2e
... Waiting on approve_write_… (type=external_input, timeout=600s, poll=5s)   # ignores the =6 setting
(killed at 150s — did not pause/exit)
```

**Blockers (must fix)**
1. **`test_hitl.py` not hermetic — fails 4/4 in isolation.** The live HITL flow tests leak ZenML
   SQLAlchemy connection-pool sockets + asyncio event loops; GC during the CLI test trips
   `PytestUnraisableExceptionWarning` → error under `filterwarnings=["error"]`. The 058 `test_flow.py`
   tests alone are clean — 059's extra live-flow + CLI tests push it over the threshold. Fix: dispose
   ZenML's engine + `gc.collect()` in the `isolated_kitaru_store` teardown (the conftest already resets
   the singletons but never disposes the engine), or otherwise stop the live-flow tests leaking. Masking
   it with a `ResourceWarning`/`PytestUnraisableExceptionWarning` filter is the weaker last resort.
2. **`runtime_wait_timeout_s` does NOT govern write/edit/bash approval waits.** The native approval wait
   is `kitaru.wait(timeout=None)` → ZenML's fixed 600s; only `ask_user`/`exit_plan_mode` (via
   `flow_resolve_user_question`) honor the setting. AGENTS.md ("Unanswered past `runtime_wait_timeout_s`
   → pauses … exit 0") and the `cli.py` docstring claim it applies to write/edit/bash too — inaccurate
   for the **headline** destructive-write-approval story. Fix the docs to scope the setting to
   ask_user/exit_plan_mode, and ADD a unit test for the unanswered-**approval** paused path (only the
   ask_user paused path is currently tested; the approval clean-pause-at-timeout is unverified).

**AC-text corrections required (flag all three deviations for PA acceptance in /review)**
- AC2: replace "a deny verdict feeds the denial back to the model (mirrors the interactive gate outcome)"
  with "a deny verdict cleanly STOPS the run before the tool acts (the adapter raises `_ToolApprovalDenied`
  out of `run_sync`; there is no feed-back-to-model path — interactive keeps feed-back). The denied tool
  never ran; the run finishes with a denial message, exit 0."
- AC5: replace "under `\"turn\"` no opt-out is needed; both paths tested or asserted" with "HITL forces
  `checkpoint_strategy=\"calls\"` (the per-tool wait opt-out is rejected under `\"turn\"`, which cannot
  host flow-scope waits); `runtime_checkpoint_strategy` governs only the bypass run. The `\"calls\"` path
  is tested."
- AC3: already corrected by the SWE (replay-reuse asserted via the stable wait name, not driven
  end-to-end). Accept; flag that live replay-reuse is deferred to 062.

**Other issues found**
- The deny path is correct and clean (no crash, no partial write) — Deviation 1 is acceptable.
- File ownership clean: diff = runtime HITL wiring + `deps.py` flag + approval predicate + `cli.py`
  + tests + ADR §3 + AGENTS.md row + task file (11 files); nothing 057/058/063-committed is broken
  (full suite green); `agent/loop.py` / `tui/` untouched.

**VERDICT: FAIL** — 2 blockers (non-hermetic test file fails in isolation; `runtime_wait_timeout_s`
doc/behaviour mismatch + untested approval-pause path) and 2 AC-text corrections (AC2, AC5). The HITL
logic itself is sound and the happy/allow/deny/ask_user paths genuinely work end-to-end.

### [SWE] 2026-06-28 16:35 — Fixes (QA round 2)

Addressed both Tester blockers + both AC-text corrections. No production logic changed — the HITL
flow behavior is unchanged; the fixes are test hermeticity, doc accuracy, and added coverage.

**Blocker 1 — `test_hitl.py` now hermetic in isolation.**
Root cause traced to two live-flow stragglers that `filterwarnings=["error"]` turned into errors only
when GC'd during a *later* test: (a) ZenML's SQLAlchemy SQLite connection pool (unclosed sockets), and
(b) the idle asyncio event loop pydantic-ai's `run_sync` leaves set as the **main thread's current
loop** — reused while current, but orphaned (and its self-pipe socketpair leaked) the moment a later
test's `asyncio.run` resets the current loop. I confirmed empirically (throwaway diag test) that the
orphan == `policy._local._loop` and that closing it + `set_event_loop(None)` lets the next flow build a
fresh loop with no breakage.
Fix in `tests/unit/decode/runtime/conftest.py` `isolated_kitaru_store` teardown: dispose ZenML's
engine (`_dispose_kitaru_engine`), then `_close_idle_event_loop` (close the idle current loop + clear
it), then `gc.collect()` — all within the fixture scope.

**Files modified**
- `tests/unit/decode/runtime/conftest.py` — engine dispose + idle-loop close + gc in teardown.
- `tests/unit/decode/runtime/test_hitl.py` — `_fast_approval_wait` fixture (wraps `kitaru.wait` to
  inject a 1s timeout *only* on the native no-timeout approval wait) + 2 new approval-pause tests.
- `AGENTS.md` — `decode run --hitl` row: `runtime_wait_timeout_s` scopes only `ask_user`/`exit_plan_mode`
  waits; native `write`/`edit`/`bash` approval waits use the adapter's fixed 600s default (known limit).
- `src/decode/cli.py` — `run` docstring: same timeout-scope correction.
- `src/decode/runtime/flow.py` — `HitlRunResult` docstring: paused-timeout differs by wait kind.
- `docs/adr/0008-kitaru-durable-runtime.md` — §3 reality 3: added the `runtime_wait_timeout_s`
  known-limitation note (approval waits are `kitaru.wait(timeout=None)` → 600s; honoring the setting
  would require forking the adapter's `_invoke_wait`, which decode does not do).
- `tasks/059-...md` — AC2 (deny STOPS the run, no feed-back-to-model) + AC5 (HITL forces `"calls"`).

**Blocker 2 — `runtime_wait_timeout_s` scope corrected in docs + approval-pause path now tested.**
Verified the overclaim against the SDK: `kitaru/adapters/pydantic_ai/_toolset.py::_invoke_wait` calls
`kitaru.wait(...)` with **no** timeout → ZenML `runner.wait` default `timeout=600`. Did NOT fork the
adapter (infra is imported); corrected AGENTS.md + cli.py + flow.py docstrings + ADR §3 to scope the
setting to the `ask_user`/`exit_plan_mode` waits decode drives via `wait_for_input`. Added
`test_unanswered_write_approval_leaves_the_run_paused` (unit) and
`test_cli_run_hitl_reports_a_paused_write_approval` (CLI, exit 0 + exec-id + `kitaru executions input`
hint) for the previously-untested approval-pause path.

**Tests**
- Unit: 964 passing, 0 failing (was 962; +2 approval-pause tests). Integration: 12 passing.
- Hermeticity: `pytest tests/unit/decode/runtime/test_hitl.py` passes IN ISOLATION (13 passed),
  deterministic across 2 isolated runs; `test_flow.py` alone still clean.

**Acceptance criteria**
- [x] AC2 — deny path verified (`test_write_approval_deny_stops_the_run_without_writing`); AC text now
      states the deny STOPS the run (no feed-back-to-model).
- [x] AC5 — text now states HITL forces `"calls"`; `runtime_checkpoint_strategy` governs only bypass.
- [x] All other ACs unchanged and still green.

**Evidence**
```
$ make format-check && make lint-check
137 files already formatted
All checks passed!
$ make pre-commit
964 passed in 36.34s
$ make integration-tests
12 passed in 1.37s
$ uv lock --check
Resolved 149 packages   (clean)

# Hermeticity proof — the file ALONE (was 4/4 fail in isolation, now clean, twice):
$ uv run pytest tests/unit/decode/runtime/test_hitl.py -q
13 passed in 21.38s
$ uv run pytest tests/unit/decode/runtime/test_hitl.py -q
13 passed in 21.29s
```

**Notes**
- Kept `filterwarnings=["error"]` — no new warnings; no warning-suppression filter was added to mask
  the leak (closed the resources instead, per the Tester's preferred path).
- `_close_idle_event_loop` reads `policy._local._loop` directly (peek without creating a loop) and only
  acts on a non-running, non-closed current loop — non-flow tests are a no-op.
- DO NOT COMMIT — handing back to the Tester for re-review.

### [Tester] 2026-06-28 18:05 — QA (round 2, re-review after fixes)

Re-reviewed the two round-1 blockers + the two AC-text corrections, then re-ran the full gate and the
live e2e happy/break paths. **Both blockers are genuinely closed** (verified with my own evidence, not
the SWE summary).

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 137 files clean; `ruff check` all passed)
- Unit tests (full suite): **964 passed / 0 failed** (was 962; +2 approval-pause tests)
- Integration tests: **12 passed / 0 failed**
- Warnings: **0**; `uv lock --check` clean (149 packages)

**Blocker 1 — `test_hitl.py` hermeticity — FIXED (genuine resource disposal, not suppression).**
- `pytest tests/unit/decode/runtime/test_hitl.py` ALONE → **13 passed**, run twice (21.3s / 22.0s),
  deterministic. `pytest tests/unit/decode/runtime/test_flow.py` alone → **4 passed** (058 stays clean).
- The fix is in `tests/unit/decode/runtime/conftest.py` `isolated_kitaru_store` teardown: real disposal
  within the fixture scope — `_dispose_kitaru_engine()` (`engine.dispose()` on ZenML's SQLAlchemy
  pool), `_close_idle_event_loop()` (close the idle current loop + `asyncio.set_event_loop(None)`),
  then `gc.collect()`. NOT a cheat: `pyproject.toml` still `filterwarnings = ["error"]`; no
  `ResourceWarning` / `PytestUnraisableExceptionWarning` filter exists anywhere; the two `pytestmark`
  filters in `test_hitl.py` (`'crypt' is deprecated`, `There is no current event loop`) are byte-identical
  to those already in `test_flow.py` (scoped third-party DeprecationWarnings, unrelated to the leak).

**Blocker 2 — `runtime_wait_timeout_s` doc accuracy + approval-pause coverage — FIXED.**
- Docs now correctly scope the setting in all four places: AGENTS.md `decode run --hitl` row, `cli.py`
  `run` docstring, `flow.py` `HitlRunResult` docstring, and ADR-0008 §3 reality 3 — `ask_user`/
  `exit_plan_mode` waits honor `runtime_wait_timeout_s`; native `write`/`edit`/`bash` approval waits are
  `kitaru.wait(timeout=None)` → the adapter's fixed `600s`, and ignore the setting (honoring it would
  require forking `_invoke_wait`, which decode does not do — infra is imported).
- Two NEW approval-pause tests genuinely exercise the unanswered-**approval** path (not stubs), both
  on the real `@flow` + adapter: `test_unanswered_write_approval_leaves_the_run_paused`
  (`paused is True`, `output is None`, non-empty `exec_id`, the approval wait was created before the
  pause) and `test_cli_run_hitl_reports_a_paused_write_approval` (exit 0 + "paused" + "kitaru executions
  input" on stderr). The `_fast_approval_wait` fixture shortens **only** the native approval wait
  (injects `timeout=1` iff `timeout is None`); decode-driven `wait_for_input` passes an explicit timeout
  and is left untouched — the pause *mechanism* is unchanged, so the shortened wait is faithful.

**E2E adversarial pass** (real local Kitaru stack + real OpenRouter model via `.env`)
- Happy path (no tool): `RUNTIME_WAIT_TIMEOUT_S=8 decode run --hitl "Reply with exactly DONE … no tools"`
  → flow completed, stdout `DONE`, **exit 0** (real `run_sync` + `_capture_runtime_output` artifact). PASS.
- Break path (state edge — unanswered WRITE approval, real model + real flow + real adapter wait,
  native wait shortened to 1s exactly as `_fast_approval_wait` does): a gated `write` opened a native
  approval wait, no operator approved → **exit 0**, stderr printed `the task paused on a durable
  human-in-the-loop wait (execution 14de43d1-…)` + `kitaru executions input <id> --wait <name>
  --value '<answer>'` + list/resume hints; `out.txt` **absent** (the unapproved write never ran).
  Matches the documented behavior exactly. PASS.

**Acceptance criteria**
- [x] PASS — AC1 ask_user → named durable wait, answer becomes tool result —
      `test_ask_user_pauses_on_a_named_wait_…` drives the real flow + `wait_for_input`; live `DONE` corroborates.
- [x] PASS — AC2 write/bash approval allow runs / **deny STOPS the run** — `test_write_approval_allow_runs_the_tool`
      + `…deny_stops_the_run_without_writing`; AC2 text now correctly says deny cleanly STOPS the run
      (no feed-back-to-model). Live break path confirms the unapproved write never wrote.
- [x] PASS-with-note — AC3 stable wait name → replay reuse — name determinism asserted
      (`test_hitl_wait_name_is_deterministic_and_question_derived`); AC3 text scopes replay-reuse to the
      stable-name proof, live replay deferred to 062. Flag for PA.
- [x] PASS — AC4 async-resolver → sync-`wait_for_input` bridge — `flow_resolve_user_question` calls the
      sync wait directly on the workflow thread; unit + live flow tests + live `DONE` prove no deadlock.
- [x] PASS — AC5 checkpoint opt-out — AC5 text now correctly says HITL **forces** `"calls"`;
      `runtime_checkpoint_strategy` governs only the bypass run; `test_to_hitl_durable_agent_forces_calls…`.
- [x] PASS — AC6 interactive byte-unchanged — `headless_durable_waits` defaults `False`; `agent/loop.py`
      + `tui/` NOT in the diff; full suite incl. gate / TUI / 058 bypass tests green.
- [x] PASS — `make ci` green, 0 warnings — 964 unit + 12 integration green, lock clean, AND the new
      `test_hitl.py` is now hermetic in isolation (round-1 FAIL resolved).

**Evidence**
```
$ uv run pytest tests/unit/decode/runtime/test_hitl.py -q      # the file ALONE, x2
13 passed in 22.01s  /  13 passed in 21.28s
$ uv run pytest tests/unit/decode/runtime/test_flow.py -q
4 passed in 7.47s
$ make pre-commit
964 passed in 36.51s
$ make integration-tests
12 passed in 1.59s
$ uv lock --check
Resolved 149 packages   (clean)
$ RUNTIME_WAIT_TIMEOUT_S=8 decode run --hitl "Reply with exactly DONE … no tools"   # live happy path
DONE        # exit 0
$ decode run --hitl "Create a file named out.txt … using the write tool"            # live break path
Decode: the task paused on a durable human-in-the-loop wait (execution 14de43d1-…). … exit 0
  kitaru executions input 14de43d1-… --wait <name> --value '<answer>'               # out.txt absent
```

**Deviations to flag for PA acceptance in /review** (all accurately documented in ADR-0008 §3 + the ACs):
1. **deny-stops-run** — a denied approval raises `_ToolApprovalDenied` out of `run_sync`; the run stops
   with a denial message (no feed-back-to-model path the interactive gate has).
2. **HITL-forces-`calls`** — `runtime_checkpoint_strategy` governs only the bypass run (`"turn"` cannot
   host flow-scope waits).
3. **replay-via-name** — AC3 replay-reuse is proven via the deterministic wait name, not driven
   end-to-end; live replay needs a deployed flow the in-process local stack lacks (deferred to 062).
   Related known limitation (now documented): `runtime_wait_timeout_s` does not govern native approval
   waits (fixed `600s`).

**Other issues found**
- None blocking. File ownership clean: diff = 10 modified + 1 untracked (`test_hitl.py`); no files
  leaked from the live runs; `agent/loop.py` / `tui/` untouched.

**VERDICT: PASS** — both round-1 blockers genuinely closed (hermetic test file via real resource
disposal; timeout docs corrected + approval-pause path now tested), both AC-text corrections (AC2, AC5)
landed, full suite + live happy/break paths green, 0 warnings. The three deviations above stay flagged
for PA acceptance in `/review`.
