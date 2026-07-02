---
id: 068-runtime-checkpoint-calls-default
feature: runtime-replay
status: pending
---

# Record every `decode run` under `"calls"` (replay-ready) + repair bypass output extraction

Tags: `runtime`, `config`, `replay`
Depends on: #067
Blocks: #069, #070

This task implements **ADR-0010 §3**. Model-swap replay only takes effect on turns re-executed
**downstream** of the `--from` anchor (verified in `test_runtime_capstone.py`: replaying from a
terminal checkpoint serves model calls from cache; the leg-counter does not move). Per-turn
checkpoints are too coarse to anchor a replay before a specific model call, so every `decode run`
must be recorded under `checkpoint_strategy="calls"` to be replay-ready. HITL already forces
`"calls"` (`flow.py:317`); this flips the **bypass** default too by changing the settings default.

**Baked-in risk (verify-first):** `"calls"` may break the bypass flow's `.wait()` return-value
extraction the same way it did for HITL — several terminal model-request checkpoints →
`_MultipleTerminalStepsOutputError` (ADR-0008 §3 amendment 5). The HITL flow solved it with a
terminal `@checkpoint _capture_runtime_output` that `save()`s the output under
`RUNTIME_OUTPUT_ARTIFACT` and a reader (`_load_runtime_output`) loads it back by name
(`flow.py:356-365`, `430-441`). This task **verifies the bypass path first**; if it breaks, apply the
same pattern to the bypass flow + CLI.

## Scope

- **`config/settings.py`** — flip the default: `runtime_checkpoint_strategy: Literal["turn","calls"] = "calls"`
  (`:207`); update the field comment to say `"calls"` is now the default because it makes every
  `decode run` replay-ready (was `"turn"`, the MVP coarse default). `"turn"` remains selectable.
- **`.env.example`** — update the `RUNTIME_CHECKPOINT_STRATEGY` block (`:131-133`): default is now
  `calls` (per model/tool call — replay-ready); `turn` (one checkpoint per turn) is the coarse
  opt-out. Note that HITL always forces `calls` regardless.
- **Verify-first, then repair if needed (bypass `.wait()` under `"calls"`):**
  - Drive the real bypass flow (`run_agent_task.run(...).wait()`) under `"calls"` with a scripted
    multi-tool agent, offline, and record in the SWE log whether `.wait()` still extracts `.output`
    cleanly or raises `_MultipleTerminalStepsOutputError`.
  - **If it breaks:** end `run_agent_task` with `return _capture_runtime_output(output)` (reuse the
    existing terminal `@checkpoint` + `RUNTIME_OUTPUT_ARTIFACT`), and change the bypass CLI path
    (`cli.py:361-365`) to load the output via the artifact reader
    (`_load_runtime_output(handle.exec_id)`) instead of `.wait().output`. Keep the
    `getattr(..., "output", ...)` fallback only if `.wait()` is still used.
  - **If it does not break:** leave the extraction as-is; the only change is the strategy default +
    the step-name assertions below. Record the decision + evidence in the log either way.
- **Update tests to exercise the new default** (they currently hardcode `"turn"`, bypassing the
  settings read):
  - `tests/unit/decode/runtime/test_flow.py`: `_durable(..., strategy=…)` default `"turn"→"calls"`
    (`:31-34`); adapt the output-extraction in the round-trip tests to whatever the verify step
    chose; replace the turn-specific assertion `"decode_runtime" in set(run.steps)` (`:93`) with the
    `"calls"` reality (per-call step names and/or `_capture_runtime_output`).
  - `tests/unit/decode/runtime/test_run_command.py`: `_patch_seam` (`:43`) `checkpoint_strategy` →
    `"calls"`; the printed-output assertions must still pass under the chosen extraction path.
  - Add a unit assertion that the **real** `_build_runtime_agent()` (seed key, offline) now builds a
    `KitaruAgent` with `checkpoint_strategy == "calls"` (reads the new settings default).

## Acceptance criteria

- [ ] `settings.runtime_checkpoint_strategy` defaults to `"calls"`; `.env.example` documents the new
      default and the `turn` opt-out; a settings unit test asserts the default is `"calls"`.
- [ ] The SWE log records the verify-first result for bypass `.wait()` under `"calls"` (works vs.
      `_MultipleTerminalStepsOutputError`) with the offline evidence, and the chosen extraction path.
- [ ] A real bypass `decode run` (hermetic: real `@flow` + `KitaruAgent`, scripted `FunctionModel`,
      `"calls"` from settings) round-trips a **multi-tool** task and returns the correct final text —
      proving output extraction survives `"calls"` (via `.wait().output` or the artifact reader,
      whichever the verify step selected).
- [ ] The recorded execution under `"calls"` persists **per-call** checkpoints (a test asserts the
      step set reflects per-model/tool-call granularity, not the single `"decode_runtime"` turn step).
- [ ] `test_run_command_prints_the_agents_output` and the `test_flow.py` round-trip tests pass under
      `"calls"` (updated helpers), including the disabled-runtime / provider-guard / proxy /
      secret-store guard tests (no regression to the guard behaviour).
- [ ] **HITL unchanged:** `run_agent_task_hitl` still forces `"calls"` and reads back via
      `_load_runtime_output`; `test_hitl.py` and the capstone HITL tests stay green.
- [ ] **Interactive TUI byte-unchanged** (not in the diff); `make ci` green, **0 warnings**;
      `uv lock --check` passes.

## User stories

### Story: A `decode run` is replay-ready by default
1. A developer runs `decode run "summarize the repo"` with no extra flags.
2. The run completes and prints the answer, and the durable execution is recorded with per-call
   checkpoints — so it can later be replayed and anchored before any specific model call.

### Story: Coarse checkpoints are still available
1. A developer sets `RUNTIME_CHECKPOINT_STRATEGY=turn` and runs `decode run "…"`.
2. The run records one checkpoint per turn (the pre-067 behaviour), and still prints the answer.

## Out of scope
- The `--model` flag / `exec_id` surfacing (#069) and the `decode replay` command (#070).
- Changing HITL's forced `"calls"` or its output-artifact mechanism (already shipped).
- Any change to the capstone's HITL tests beyond keeping them green (the definitive `"calls"` bypass
  replay proof lands in #070).

## Log
