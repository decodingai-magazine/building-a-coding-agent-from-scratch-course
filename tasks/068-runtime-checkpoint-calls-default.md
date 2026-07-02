---
id: 068-runtime-checkpoint-calls-default
feature: runtime-replay
status: done
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

- [x] `settings.runtime_checkpoint_strategy` defaults to `"calls"`; `.env.example` documents the new
      default and the `turn` opt-out; a settings unit test asserts the default is `"calls"`.
- [x] The SWE log records the verify-first result for bypass `.wait()` under `"calls"` (works vs.
      `_MultipleTerminalStepsOutputError`) with the offline evidence, and the chosen extraction path.
- [x] A real bypass `decode run` (hermetic: real `@flow` + `KitaruAgent`, scripted `FunctionModel`,
      `"calls"` from settings) round-trips a **multi-tool** task and returns the correct final text —
      proving output extraction survives `"calls"` (via `.wait().output` or the artifact reader,
      whichever the verify step selected).
- [x] The recorded execution under `"calls"` persists **per-call** checkpoints (a test asserts the
      step set reflects per-model/tool-call granularity, not the single `"decode_runtime"` turn step).
- [x] `test_run_command_prints_the_agents_output` and the `test_flow.py` round-trip tests pass under
      `"calls"` (updated helpers), including the disabled-runtime / provider-guard / proxy /
      secret-store guard tests (no regression to the guard behaviour).
- [x] **HITL unchanged:** `run_agent_task_hitl` still forces `"calls"` and reads back via
      `_load_runtime_output`; `test_hitl.py` and the capstone HITL tests stay green.
- [x] **Interactive TUI byte-unchanged** (not in the diff); `make ci` green, **0 warnings**;
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

### [SWE] 2026-07-02 15:20 — Implementation

**Verify-first finding (the crux): bypass `.wait()` BREAKS under `"calls"`.**
Drove the REAL bypass flow `run_agent_task.run(...).wait()` offline (isolated Kitaru store, scripted
`FunctionModel`) under `checkpoint_strategy="calls"` with a multi-tool agent (read → write → text).
Result: `handle.wait()` raised `kitaru.flow._MultipleTerminalStepsOutputError` — "multiple terminal
checkpoints were found (5): decode_runtime_model_request, decode_runtime_model_request_2,
decode_runtime_model_request_3, read_tool, write_tool". The persisted step set was per-call
(`decode_runtime_model_request{,_2,_3}`, `read_tool`, `write_tool`) — **not** the single
`decode_runtime` turn step. This is exactly the HITL-under-`"calls"` failure mode (ADR-0008 §3
amendment 5). **Decision: apply the HITL fix pattern to the bypass path.**

A second characterization probe (with the fix applied) confirmed: adding the terminal
`_capture_runtime_output` checkpoint breaks `.wait()` under **both** `"turn"` and `"calls"` (the
extracted `str` is not a checkpoint ref, so `decode_runtime` stays a leaf → ≥2 terminals), while
`_load_runtime_output(exec_id)` returns the correct text under both. So the CLI + all bypass tests
now read the output artifact by name and never call `.wait()` — the same mechanism HITL already uses.
This widened the blast radius beyond the task's named files: every bypass-flow test that called
`.wait()` (secret-store, credentials-proxy, and the 3 capstone bypass tests) had to switch to
`_load_runtime_output` to stay green — done. The capstone bypass tests keep `checkpoint_strategy="turn"`
(minimal change; their `decode_runtime`-step assertions still hold under `"turn"`).

**Files modified**
- `src/decode/config/settings.py` — `runtime_checkpoint_strategy` default `"turn"→"calls"` + comment.
- `.env.example` — `RUNTIME_CHECKPOINT_STRATEGY` block: default `calls` (replay-ready), `turn` opt-out, HITL forces `calls`.
- `src/decode/runtime/flow.py` — promoted `_capture_runtime_output` to a shared terminal sink before both flows; `run_agent_task` now `return _capture_runtime_output(output)`; docstrings for the flow + `_load_runtime_output` updated (shared by both flows).
- `src/decode/cli.py` — bypass `run` path reads `_load_runtime_output(handle.exec_id)` instead of `.wait().output`.
- `tests/unit/decode/config/test_settings.py` — default asserts `"calls"`.
- `tests/unit/decode/runtime/test_flow.py` — `_durable` default `"calls"`; round-trips read via `_load_runtime_output`; new per-call-checkpoint assertion; new multi-tool round-trip with `"calls"` from settings; seam unit test asserts `checkpoint_strategy == "calls"`.
- `tests/unit/decode/runtime/test_run_command.py` — `_patch_seam` → `"calls"`.
- `tests/unit/decode/runtime/test_secret_store_config.py` — `_scripted_durable` → `"calls"`; new `_run_and_read`; `.wait()` → artifact reader.
- `tests/unit/decode/runtime/test_credentials_proxy.py` — seam → `"calls"`; `.wait()` → artifact reader.
- `tests/integration/test_runtime_capstone.py` — 3 bypass tests read via `_load_runtime_output` (kept `"turn"`); module docstring updated.

**Tests**
- Unit: 1028 passing, 0 failing (`make pre-commit`). Integration: 20 passing (`make integration-tests`).
- `make ci`: 1048 passed, 0 warnings; `uv lock --check` + format-check + lint-check clean.

**Acceptance criteria**
- [x] Default `"calls"` + `.env.example` documented + settings test — `tests/unit/decode/config/test_settings.py::test_runtime_defaults`.
- [x] Verify-first result + evidence + chosen path recorded (above).
- [x] Real bypass multi-tool round-trip under `"calls"` from settings — `test_flow.py::test_flow_round_trips_a_multi_tool_task_under_the_calls_default` (+ offline CLI e2e below).
- [x] Per-call checkpoints asserted — `test_flow.py::test_flow_records_per_call_checkpoints_not_a_single_turn_step`.
- [x] `test_run_command_prints_the_agents_output` + round-trip + guard tests pass under `"calls"`.
- [x] HITL unchanged; `test_hitl.py` + capstone HITL tests green.
- [x] TUI byte-unchanged (not in diff); `make ci` green, 0 warnings; `uv lock --check` passes.

**Evidence**
```
# verify-first probe (calls) — bypass .wait() under multi-tool agent:
STEPS=['decode_runtime_model_request', 'decode_runtime_model_request_2', 'decode_runtime_model_request_3', 'read_tool', 'write_tool']
WAIT_RAISED: kitaru.flow._MultipleTerminalStepsOutputError: ...multiple terminal checkpoints were found (5)...

# offline e2e of the REAL `decode run` command (store isolated, scripted multi-tool seam):
settings.runtime_checkpoint_strategy = calls
EXIT_CODE: 0
STDOUT: 'read the spec and wrote out.txt'   # read back via _load_runtime_output
MODEL_LEGS: 3                               # real agent loop drove all 3 legs
out.txt on disk: done                       # write ran inline under bypass

$ make ci
======================= 1048 passed in 99.63s (0:01:39) ========================

$ RUNTIME_ENABLED=false uv run decode run "do something"
Decode: the headless runtime is disabled — set RUNTIME_ENABLED=true ... (friendly, no traceback)
```

**Notes**
- No architectural fork — this is the shipped HITL pattern applied to the bypass path (ADR-0008 §3, ADR-0010 §3).
- A real subprocess `decode run` happy-path was not run: this machine's ZenML store points at a live server (`127.0.0.1:8383`) that is down, so `run(...)` fails at the store boundary before any decode logic. The offline hermetic e2e above (store redirected under tmp) is the faithful proof; the runtime-disabled guard was verified via a real subprocess.

### [Tester] 2026-07-02 16:40 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make ci`: `uv lock --check` clean, `ruff format --check` 144 files, `ruff check` all-pass)
- Unit + integration tests: 1048 passed / 0 failed (`make ci`, 97.3s)
- Warnings: 0 (grep of the full CI log for `warning`/`deprecat` = 0; `filterwarnings=["error"]` would fail on any)

**E2E adversarial pass** (own throwaway probe drove the REAL Click `run` + real `@flow` + real `_build_runtime_agent` seam + real `KitaruAgent` + real agent loop; only the model boundary = `FunctionModel` and store = tmp were swapped — the offline-by-design strategy per ADR-0008/0010)
- Happy path (`"calls"` from settings, multi-tool read→write→text, unicode+newline final text): `CliRunner invoke ["run", ...]` → exit 0, printed text byte-identical incl. `✓`/`café`/newline (artifact `save`/`load` round-trip preserved it), `out.txt` written inline under BYPASS, agent drove 3 legs. PASS
- Break path 1 (state edge — `"turn"` opt-out via real CLI, User Story 2): `RUNTIME_CHECKPOINT_STRATEGY=turn` multi-tool run → exit 0, still prints the answer, file written. The shared terminal sink does NOT break `"turn"`. PASS
- Break path 2 (masking check — does dropping `.wait()` hide a regression?): called `handle.wait()` directly under BOTH strategies after the sink → `raised:_MultipleTerminalStepsOutputError` under **both** `"turn"` and `"calls"`; `_load_runtime_output` returned the correct text under both. Confirms the SWE's claim independently — switching all bypass tests to the artifact reader is NECESSARY, not masking a regression (the CLI no longer calls `.wait()` at all). PASS
- Break path 3 (boundary — empty task string): `["run", ""]` → exit 0, prints scripted text, no traceback. PASS
- Break path 4 (AC4 independent — per-call granularity): a `"calls"` run's persisted steps = `['_capture_runtime_output', 'decode_runtime_model_request', 'decode_runtime_model_request_2', 'read_tool']` — per-call, no single `decode_runtime` turn step. PASS
- Real subprocess `decode run "hi"` (runtime enabled): EXIT 124 (hung at the down ZenML store, 0 bytes) — reproduces the SWE's disclosed env limitation; NOT a code defect. Real subprocess `RUNTIME_ENABLED=false decode run` → friendly line + exit 1, no traceback (guard intact, `.env` key present so the provider guard passed first).

**Acceptance criteria**
- [x] PASS — default `"calls"` + `.env.example` + settings test — `settings.py:207`; `.env.example:131-135`; `test_settings.py::test_runtime_defaults` asserts `== "calls"` (green); own probe asserted the live singleton too.
- [x] PASS — verify-first result recorded with evidence + chosen path — SWE log documents `_MultipleTerminalStepsOutputError` (5 terminals); independently reproduced (break path 2).
- [x] PASS — real hermetic bypass multi-tool round-trip returns correct final text — `test_flow.py::test_flow_round_trips_a_multi_tool_task_under_the_calls_default` + own real-CLI e2e (happy path, unicode round-trip via the artifact reader).
- [x] PASS — per-call checkpoints persisted — `test_flow.py::test_flow_records_per_call_checkpoints_not_a_single_turn_step` + own probe (step set above).
- [x] PASS — run-command / round-trip / guard tests green under `"calls"` — `test_run_command.py`, `test_secret_store_config.py`, `test_credentials_proxy.py`, `test_settings.py` (81 passed together); disabled-runtime guard also verified via real subprocess.
- [x] PASS — HITL unchanged — `flow.py:347` still forces `checkpoint_strategy="calls"`; `flow.py:487` still reads `_load_runtime_output`; `_capture_runtime_output` body byte-identical (relocated only); `test_hitl.py` unchanged + green; capstone `test_hitl_pauses_on_named_waits...` green.
- [x] PASS — TUI byte-unchanged; `make ci` green, 0 warnings; `uv lock --check` passes — `git diff` touches no `tui/`/REPL/harness/loop/render file (only `cli.py` bypass `run` path, `config/settings.py`, `runtime/flow.py`).

**Evidence**
```
$ make ci
uv lock --check → Resolved 149 packages
ruff format --check → 144 files already formatted
ruff check → All checks passed!
======================= 1048 passed in 97.30s (0:01:39) ========================
(grep -icE 'warning|deprecat' ci_log = 0)

# own adversarial probe (throwaway, since deleted):
6 passed in 13.03s
QA_WAIT_PROBE strategy=turn  -> raised:_MultipleTerminalStepsOutputError
QA_WAIT_PROBE strategy=calls -> raised:_MultipleTerminalStepsOutputError
QA_STEPS calls -> ['_capture_runtime_output', 'decode_runtime_model_request', 'decode_runtime_model_request_2', 'read_tool']

$ RUNTIME_ENABLED=false uv run decode run "do something"
Decode: the headless runtime is disabled — set RUNTIME_ENABLED=true ... (exit 1, no traceback)
```

**Other issues found** (non-blocking, PASS-with-note)
- `flow.py:469` — the now-shared `_load_runtime_output` error string still reads `f"HITL execution {exec_id} finished without a ... artifact"`, but the bypass flow also uses this function now (its docstring was updated to "Shared by both flows"; the message wasn't). Cosmetic staleness on an internal error-only path (reached only if `_capture_runtime_output` never ran). Worth a one-word fix ("HITL execution" → "execution") for the PR reviewer; not a defect.
- Faithfulness of the offline proof: sufficient. The real cross-process ZenML *server* round-trip is out of scope here and every prior runtime task (ADR-0010 §7 defers it to a deployed stack); the hermetic e2e exercises the real flow end-to-end minus only the network model + live server.

**VERDICT: PASS**
