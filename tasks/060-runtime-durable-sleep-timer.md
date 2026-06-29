---
id: 060-runtime-durable-sleep-timer
feature: kitaru-runtime
status: done
---

# `sleep` → a durable, resumable timer in flow mode

Tags: `runtime`, `tools`
Depends on: #058
Blocks: #062

This task implements ADR-0008 §4. The ungated `sleep` tool (`tools/sleep.py:37`, today a bare
`await asyncio.sleep(...)`) becomes mode-dependent: **interactive** mode keeps `asyncio.sleep`;
**flow** mode pauses on a durable `kitaru.wait(name="sleep", timeout=…)` so the execution can suspend
and the process exit, then resume. Same single-tool surface, two implementations — and every existing
guardrail (the `sleep_max_s` clamp, the negative/`nan` `ModelRetry` rejection, the confirmation
string) is preserved in **both** modes.

## Scope

- Introduce a **mode-aware sleep seam** for `tools/sleep.py` (prefer a module-level callable seam,
  e.g. `_SLEEPER`, mirroring bash `_EXECUTOR` / web `_TRANSPORT` / lsp `_spawn_process` — the
  established patchable pattern; a `deps`-flag alternative is acceptable if cleaner). The default
  seam is the current `asyncio.sleep` (interactive). The headless flow (task 058 deps) installs a
  **durable sleeper** that calls `kitaru.wait(name="sleep", timeout=capped)` at flow scope, reset on
  flow exit so it never leaks into an in-process REPL.
- Keep the existing logic **exactly**: reject `not (seconds >= 0)` (negatives + `nan`) with the same
  `ModelRetry("seconds must be a non-negative number")`; clamp `capped = min(seconds,
  settings.sleep_max_s)`; return `f"Slept {capped} s."`. Only the *await* differs by mode.
- The durable sleeper bridges the **async** `sleep` tool to the **sync** `kitaru.wait` (same
  sync/async bridge as 059); verify against the installed SDK that `kitaru.wait(name=…, timeout=…)`
  is callable from within `KitaruAgent.run_sync` tool execution (`allow_sync_tool_body_waits=True`
  if required).
- Document the behavior split in the README runtime section (one line: "in `decode run`, `sleep`
  becomes a durable timer — the run can pause and resume; in the TUI it is a plain in-process sleep").

## Acceptance criteria

- [x] Interactive mode is byte-unchanged: `sleep(seconds)` still `await asyncio.sleep(min(seconds,
      sleep_max_s))`, same clamp, same negative/`nan` `ModelRetry`, same `"Slept {capped} s."` —
      existing `tools/sleep` tests pass unchanged. (default `_SLEEPER` is `_interactive_sleep`; the 7
      original `tests/unit/decode/tools/test_sleep.py` tests pass untouched.)
- [x] Flow mode: with the durable sleeper installed, `sleep(seconds)` calls `kitaru.wait(name="sleep",
      timeout=min(seconds, sleep_max_s))` instead of `asyncio.sleep`; a unit test patches the seam and
      asserts the wait is invoked with the **capped** timeout (not the raw request). —
      `test_durable_sleeper_waits_on_kitaru_with_the_capped_timeout` (cap 5.0, req 10_000 → `timeout=5`).
- [x] The clamp and the negative/`nan` rejection fire **before** the durable wait too (a `nan` request
      in flow mode raises `ModelRetry`, never reaches `kitaru.wait`); unit-tested. —
      `test_durable_sleeper_rejects_negative_before_waiting`, `test_durable_sleeper_rejects_nan_before_waiting`.
- [x] The seam resets after the flow so a subsequent in-process interactive `sleep` uses
      `asyncio.sleep` again (no global leakage); asserted. —
      `test_reset_sleeper_restores_in_process_asyncio_sleep`, `test_durable_sleeper_context_*`, and the
      real-flow `test_sleep_becomes_a_durable_flow_scope_wait` asserts `_SLEEPER is _interactive_sleep`
      after the run.
- [x] The async→sync `kitaru.wait` bridge is verified against the installed adapter; no event-loop
      error, no deadlock. — `test_sleep_becomes_a_durable_flow_scope_wait` drives the **real** Kitaru
      `@flow` + `KitaruAgent.run_sync` offline: the async `sleep` body calls sync `kitaru.wait` on the
      workflow thread, the wait lands at flow scope (named `sleep`), resolves, and the flow returns.
- [x] `make ci` green, 0 warnings. — 985 passed (973 unit + 12 integration), `uv lock --check` +
      format-check + lint-check clean, `filterwarnings=["error"]` in effect.

## User stories

### Story: A headless backoff survives a restart
1. A `decode run --hitl` task calls `sleep(30)` to back off before re-checking a job. (The durable
   sleeper is wired into the wait-capable HITL flow only; the plain bypass `decode run` keeps the
   in-process `asyncio.sleep` because a flow-scope `kitaru.wait` under its `"turn"` checkpoint raises
   `KitaruUsageError("waits must be at flow scope")` — a true adapter constraint, ADR-0008 §3-4.)
2. The flow pauses on a durable `sleep` timer; the worker can be reclaimed for 30s.
3. The execution resumes after the timer and the agent continues — the sleep cost no pinned process.

### Story: The interactive sleep is unchanged
1. In the TUI, the model calls `sleep(5)`.
2. The turn pauses in-process for 5s (capped by `sleep_max_s`) and prints `Slept 5.0 s.` exactly as
   before — no Kitaru involvement.

### Story: A bogus sleep is still rejected in both modes
1. The model calls `sleep(-1)` or `sleep(nan)` headlessly.
2. It gets the same `ModelRetry("seconds must be a non-negative number")` and nothing waits — the cap
   is never defeated.

## Out of scope
- Recurring/cron scheduling — Kitaru has no native cron; `sleep` is a one-shot durable timer only.
  Recurring runs are external and deferred to step 12 (ADR-0008 §6).
- Changing the `sleep_max_s` cap or the `sleep` tool's ungated/registry status.

## Log

### [SWE] 2026-06-28 — Implementation

**Files modified**
- `src/decode/tools/sleep.py` — added the mode-aware `_SLEEPER` seam (mirrors bash `_EXECUTOR` / web
  `_TRANSPORT` / lsp `_spawn_process`): default `_interactive_sleep` (a thin `await asyncio.sleep`
  wrapper so the existing `asyncio.sleep` patch still works), `_durable_sleep` (flow-scope
  `kitaru.wait(name="sleep", timeout=int(capped))`), and `install_durable_sleeper()` / `reset_sleeper()`.
  The tool body now awaits through `_SLEEPER(capped)`; the clamp + negative/`nan` `ModelRetry` + the
  `"Slept {capped} s."` return are byte-unchanged and run **before** the seam.
- `src/decode/runtime/flow.py` — added `SLEEP_TOOL_NAME` to `_HITL_WAIT_TOOL_NAMES` (so a durable
  sleep wait is opted out of its per-call checkpoint and lands at flow scope), a `_durable_sleeper()`
  context manager (install → reset in `finally`), and wrapped the HITL flow's `run_sync` in it.
- `tests/unit/decode/tools/test_sleep.py` — 6 new durable-seam unit tests (existing 7 untouched).
- `tests/unit/decode/runtime/test_hitl.py` — real-flow durable-sleep bridge test
  (`test_sleep_becomes_a_durable_flow_scope_wait`) + 2 `_durable_sleeper` context-manager tests;
  updated the `_HITL_WAIT_TOOL_NAMES` exact-set assertion to include `sleep`.
- `README.md` — one line in the headless-runtime section (durable timer in the durable run vs
  in-process sleep in the TUI).

**Tests**
- Unit: 973 passing, 0 failing (`tools/test_sleep.py` 13, `runtime/test_hitl.py` 16).
- Integration: 12 passing.
- `make ci`: 985 passed, lock/format/lint clean, 0 warnings.

**Acceptance criteria** — all 6 checked above, each mapped to a test.

**Evidence**
```
$ make ci
... ruff format --check: 137 files already formatted
... ruff check: All checks passed!
============================= 985 passed in 38.96s =============================

$ uv run pytest tests/unit/decode/runtime/test_hitl.py::test_sleep_becomes_a_durable_flow_scope_wait -v
tests/unit/decode/runtime/test_hitl.py::test_sleep_becomes_a_durable_flow_scope_wait PASSED

$ uv run python -c "import sys, decode.tools.sleep as s; import decode.cli; print(s._SLEEPER.__name__, 'kitaru' in sys.modules)"
_interactive_sleep False   # default seam is interactive; the REPL path never imports kitaru
```

**Notes — de-risk finding (where the durable sleeper is installed) for the Tester/PA**
- Verifying `kitaru.wait` against the installed SDK (0.18, the AC-5 de-risk) showed the durable sleep
  works **only** in the wait-capable HITL flow config — `checkpoint_strategy="calls"` + `sleep` opted
  out + `allow_sync_tool_body_waits=True`. In the **bypass** `run_agent_task` (default
  `checkpoint_strategy="turn"`, no `allow_sync_tool_body_waits`) a flow-scope `kitaru.wait` from the
  sleep body raises `KitaruUsageError("waits must be at flow scope")` — the same constraint task 059
  hit for approvals (a `"turn"` checkpoint cannot host any flow-scope wait). So I wired install/reset
  into `run_agent_task_hitl` only; the bypass `decode run` keeps the in-process `asyncio.sleep`
  (non-breaking — it would otherwise crash). This matches the task's bridge note ("reuse the
  `allow_sync_tool_body_waits=True` 059 needed") and ADR-0008 §2-3 (bypass = no-wait/turn; the HITL
  flow is the wait-capable one).
- Consequence for **User Story 1**: a durable, process-exitable `sleep` is delivered via
  `decode run --hitl` (and the future deployed flow), not the plain bypass `decode run`. The README
  line and the HITL-flow docstring say so. If the PA wants the bypass run to durably sleep too, that
  needs the bypass flow to adopt the wait-capable config (force `"calls"` + opt-out +
  `allow_sync_tool_body_waits`), which changes its checkpoint granularity — out of this task's scope.
- `timeout` is coerced `int(capped)` (kitaru.wait's `timeout` is typed `int`, matching 059's
  `int(runtime_wait_timeout_s)`); the confirmation still reports the float `capped`. Sub-second durable
  sleeps therefore floor to whole seconds — coarse but harmless for a pause-the-process timer.

### [Tester] 2026-06-28 14:50 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 137 files, `ruff check` All checks passed, `make pre-commit` 973 passed)
- Unit tests: 973 passed / 0 failed
- Integration tests: 12 passed / 0 failed
- `uv lock --check`: clean
- Warnings: 0 (suites run under `filterwarnings=["error"]`)

**Hermeticity re-check (059 round-1 blocker regression risk)**
- `pytest tests/unit/decode/runtime/test_hitl.py` ALONE, `-W error`, twice → 16 passed / 16 passed, deterministic, zero `PytestUnraisableExceptionWarning`. The new live-flow `test_sleep_becomes_a_durable_flow_scope_wait` does **not** re-introduce the socket/loop leak.
- `pytest tests/unit/decode/tools/test_sleep.py` ALONE, `-W error`, twice → 13 passed / 13 passed.

**E2E adversarial pass** (real `@flow` + `KitaruAgent` + real `sleep` tool, offline; only the model + wait-resolution boundaries swapped)
- Happy path interactive: importing `decode.cli` keeps `_SLEEPER is _interactive_sleep` and `kitaru` NOT in `sys.modules` (lazy import holds); existing 7 `test_sleep` tests pass untouched → PASS.
- Happy path durable: `sleep(10_000)` cap=3.0 in the HITL flow → one flow-scope wait named `"sleep"`, capped `timeout=3`, async→sync `kitaru.wait` bridge resolves with no event-loop error/deadlock, output `"tool said: Slept 3.0 s."`, seam reset after → PASS.
- Break path 1 (boundary: `nan`/negative in flow mode): `kitaru.wait` patched; `sleep(nan)` / `sleep(-1)` raise `ModelRetry` and `wait.assert_not_called()` — guardrails fire BEFORE the durable wait → PASS.
- Break path 2 (state edge: bypass `turn` hosting a flow-scope sleep wait): built the real bypass `KitaruAgent` config (`checkpoint_strategy="turn"`, no opt-out, no `allow_sync_tool_body_waits`), installed the durable sleeper, ran in a `@flow` → raised `kitaru.errors.KitaruUsageError: ... Kitaru waits must be created at flow scope, not from checkpoint scope`. Confirms the SWE deviation is a TRUE adapter constraint (same class as 059's HITL-forces-`calls`), not a shortcut → adjudicated ACCEPTABLE.
- Break path 3 (state edge: seam leakage on error): `_durable_sleeper()` context resets `_SLEEPER` to `_interactive_sleep` even when the body raises (`finally`) — unit-tested + confirmed; a later in-process `sleep` uses `asyncio.sleep` → PASS.
- Break path 4 (boundary: `sleep(0)` / sub-second durable): `int(min(0.5, cap))==0` → `kitaru.wait(timeout=0)`; in the real HITL flow `sleep(0.5)` and `sleep(0)` return `paused=True, output=None` with a zero polling window (inline resolver never consulted). Cross-checked: a whole-second `sleep(3)` with NO resolver ALSO pauses (`paused=True, output=None`), so the pause is **inherent** to durable-sleep on the local stack (ADR-0008 §4 — pause + external resume; deployed auto-resume is step 12), not a regression unique to the floor → PASS-with-note (see below).

**Acceptance criteria**
- [x] PASS — Interactive byte-unchanged — the 7 original `test_sleep.py` tests pass untouched; default `_SLEEPER` is `_interactive_sleep`; importing `decode.cli` does not import `kitaru` (`'kitaru' in sys.modules` → False).
- [x] PASS — Flow mode calls `kitaru.wait(name="sleep", timeout=capped)` — `test_durable_sleeper_waits_on_kitaru_with_the_capped_timeout` asserts `wait.assert_called_once_with(name="sleep", timeout=5)` for cap 5.0 / req 10_000 (capped, not raw); real-flow test creates one wait named `"sleep"`.
- [x] PASS — Clamp + `nan`/negative fire before the wait — `test_durable_sleeper_rejects_negative_before_waiting` / `..._rejects_nan_before_waiting`: `ModelRetry` raised, `wait.assert_not_called()`.
- [x] PASS — Seam resets, no leakage — `test_reset_sleeper_restores_in_process_asyncio_sleep`, `test_durable_sleeper_context_*` (including reset-on-error), and the real-flow test asserts `_SLEEPER is _interactive_sleep` after the run.
- [x] PASS — async→sync bridge verified against the installed adapter — `test_sleep_becomes_a_durable_flow_scope_wait` drives the real Kitaru `@flow` + `KitaruAgent.run_sync` offline; no event-loop error, no deadlock; re-ran the bridge end-to-end at `sleep(3.0)` → completes cleanly.
- [x] PASS — `make ci` green, 0 warnings — independently reproduced: format-check + lint-check + `uv lock --check` clean, 973 unit + 12 integration passed, `filterwarnings=["error"]` in effect.

**Adjudication of the SWE-flagged deviation (durable sleep wired into HITL only)**
- VERDICT: ACCEPTABLE MVP boundary. Reproduced the bypass `turn` rejection live (`KitaruUsageError("waits must be at flow scope")`). Hosting the wait in the bypass run would require adopting `"calls"` + sleep opt-out + `allow_sync_tool_body_waits=True` — i.e. the full HITL checkpoint config, which changes the bypass run's checkpoint granularity (out of this task's scope). The bypass run could NOT host the wait without adopting HITL, so this is not a shortcut.
- REQUIRED TEXT CORRECTION (applied to the task file; flag for PA): User Story 1 step 1 corrected from "A `decode run` task calls `sleep(30)`" → "A `decode run --hitl` task calls `sleep(30)` to back off before re-checking a job." README + HITL-flow docstring already say `decode run --hitl`. No AC line implied plain `decode run`.

**Other issues found (non-blocking; for PA / follow-up)**
- The SWE's note calls sub-second durable flooring "coarse but harmless". Imprecise: `int(capped)==0` for any `seconds < 1` (incl. the explicitly-allowed `sleep(0)`) yields a zero-length polling window, so on the local stack the run pauses immediately with `output=None` (inline resolver never consulted). It is "harmless" only in the sense that ALL durable sleeps pause pending external resolution on the local stack. Suggested follow-up: floor to `timeout=max(1, int(capped))` (a non-zero window) or document the sub-second behavior precisely. Not blocking — outside the explicit ACs and consistent with the inherent durable-sleep-pauses design.
- The `_SLEEPER` seam is a plain module-level global (not re-entrant/thread-local), like the bash/web seams. Safe for the current sequential single-flow MVP; would need revisiting if concurrent/nested durable runs ever share a process. Note only.

**VERDICT: PASS** (with the User Story 1 text correction applied and two notes flagged for PA in /review, alongside 059's three deviations).
