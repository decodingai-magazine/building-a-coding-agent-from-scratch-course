---
id: 060-runtime-durable-sleep-timer
feature: kitaru-runtime
status: pending
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

- [ ] Interactive mode is byte-unchanged: `sleep(seconds)` still `await asyncio.sleep(min(seconds,
      sleep_max_s))`, same clamp, same negative/`nan` `ModelRetry`, same `"Slept {capped} s."` —
      existing `tools/sleep` tests pass unchanged.
- [ ] Flow mode: with the durable sleeper installed, `sleep(seconds)` calls `kitaru.wait(name="sleep",
      timeout=min(seconds, sleep_max_s))` instead of `asyncio.sleep`; a unit test patches the seam and
      asserts the wait is invoked with the **capped** timeout (not the raw request).
- [ ] The clamp and the negative/`nan` rejection fire **before** the durable wait too (a `nan` request
      in flow mode raises `ModelRetry`, never reaches `kitaru.wait`); unit-tested.
- [ ] The seam resets after the flow so a subsequent in-process interactive `sleep` uses
      `asyncio.sleep` again (no global leakage); asserted.
- [ ] The async→sync `kitaru.wait` bridge is verified against the installed adapter; no event-loop
      error, no deadlock.
- [ ] `make ci` green, 0 warnings.

## User stories

### Story: A headless backoff survives a restart
1. A `decode run` task calls `sleep(30)` to back off before re-checking a job.
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
