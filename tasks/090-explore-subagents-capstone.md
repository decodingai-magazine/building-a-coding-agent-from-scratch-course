---
id: 090-explore-subagents-capstone
feature: explore-subagents
status: pending
---

# Capstone — explore-subagents end to end (hermetic fan-out + skipif live smoke)

Tags: `agents`, `subagents`, `test`
Depends on: #088, #089
Blocks: —

## Scope

The living proof for the feature (ADR-0013), doubling as documentation — mirror
`tests/integration/test_milestone1_capstone.py`: drive the **real** stack and swap only the model
boundary (the `FunctionModel` precedent is at `test_milestone1_capstone.py:117-170`). New file
`tests/integration/test_subagents_capstone.py`.

- **Always-run hermetic slice (no key / no network).** Build the real agent via `build_agent()`
  (which wires `set_main_agent`), fake `GEMINI_API_KEY` for construction only, and `agent.override(model=…)`
  with a scripted `FunctionModel` that drives BOTH the parent and the children (same `Agent` object —
  ADR-0013 §6). A parent turn on a primary persona (build) emits **N `agent(...)` tool calls in one
  response** (fan-out); the child legs call `read`/`glob`/`grep` on a `tmp_path` working tree and return
  a compact report. Prove, through the real `Runner` + `AgentTurnHandler` + gate + `render_event`:
  - **Parallel fan-out** — the N children run concurrently (native `asyncio.create_task`), observed via
    an instrumented barrier/overlap counter, and bounded by `subagent_max_parallel` (set low to force
    the cap; overlap never exceeds it).
  - **Permission-free** — the `agent` tool auto-allows (no `PermissionRequested`), and children's
    `read`/`glob` calls never reach any resolver (ADR-0013 §5).
  - **Result folding** — each child's final text returns as the `agent` tool's `ToolResult` (the parent
    sees the reports), truncated to `subagent_result_max_bytes`.
  - **Silent-until-done TUI** — the `agent` call renders via the normal `ToolCallStarted` → `ToolResult`
    pipeline through the real `render_event`; children's internal events are NOT on the parent sink (the
    child's no-op emit produced nothing) — ADR-0013 §8.
  - **No usage threading** — after the fan-out turn, `handler.last_input_tokens` / the parent
    `run.usage()` excludes the children's request/token counts (ADR-0013 §7,10).
  - **Recursion default-deny** — the child's visible toolset excludes `agent` (`prepare=`).
  - **Ephemeral transcripts + resume** — `handler.message_history` / the JSONL session log carry only
    the spawn call + summary, not child transcripts; `session_log.load(...)` replays and `--resume`
    seeds a fresh handler cleanly.
  - **Headless no-special-casing (contract pin)** — assert that `runtime/flow.py`'s replay-safety
    config (`flow.py:396-415`) cache-disables only `BASH_TOOL_NAME` (and only when
    `sandbox_mode != "none"`) — `agent` is never in that set, so a read-only child's summary is
    replay-safe (ADR-0013 §9). Guard with the same kitaru-availability `skipif` the runtime capstone
    uses (`test_runtime_capstone.py:112-121`) if the check must import `flow`.
- **skipif-guarded live-Gemini smoke** (SKIP when `GEMINI_API_KEY` is unset — never fail): one real
  fan-out where a primary agent is asked to explore 2-3 areas of the repo in parallel and returns
  compressed reports. Assert **presence** (children ran, reports came back, no prompt), not exact
  content.
- **Module docstring** documents the feature end to end, naming REAL vs FAKED boundaries (real:
  `build_agent` registry + seam, `Runner`/`AgentTurnHandler`, gate, `render_event`, session log,
  `truncate`; faked: the `FunctionModel`, the working tree under `tmp_path`).

## Acceptance Criteria

- [ ] The hermetic slice passes with no key/network and proves: parallel fan-out bounded by
  `subagent_max_parallel`, permission-free spawn + children, result folding (truncated), silent-until-done
  rendering (real `render_event`, no child events on the parent sink), no usage threading, recursion
  default-deny, and ephemeral-transcript `--resume`.
- [ ] The headless no-special-casing contract is pinned: only `BASH_TOOL_NAME` (sandbox modes) is
  cache-disabled in `flow.py`; `agent` is not.
- [ ] The live-Gemini smoke SKIPs cleanly when `GEMINI_API_KEY` is absent and PASSes (presence) when
  present.
- [ ] Hermetic under `filterwarnings=["error"]` run alone (deterministic disposal; no leaked async
  tasks); `make ci` green infra-less (live smoke skipped).
- [ ] The module docstring documents the feature end to end, naming REAL vs FAKED boundaries.

## Out of scope

- New product code (all in #087/#088).
- A full offline **Kitaru-flow** headless subagent run — the no-special-casing contract is pinned by
  the `flow.py` assertion + the documented ceiling (#089); booting the flow for one assertion is out of
  proportion.
- A real *remote* / deployed-stack replay of a subagent.

## Log
