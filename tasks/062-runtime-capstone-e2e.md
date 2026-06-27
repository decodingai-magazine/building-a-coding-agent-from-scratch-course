---
id: 062-runtime-capstone-e2e
feature: kitaru-runtime
status: pending
---

# Kitaru runtime capstone: the headless flow end-to-end, offline

Tags: `runtime`, `test`
Depends on: #058, #059, #060, #061
Blocks: —

This task is ADR-0008's end-to-end proof, in the style of
`tests/integration/test_milestone1_capstone.py` (swap only the model boundary) and the LSP capstone
(patch the seam, no subprocess): drive the **real** `build_agent()` + the `@flow` + `KitaruAgent`
through a scripted conversation on the **local** Kitaru stack, swapping only the runtime seam (task
058's `_build_runtime_agent`) and the model — **no Kitaru server, no network**. CI stays offline.

## Scope

- Add `tests/integration/test_runtime_capstone.py` that builds the real headless flow with a scripted
  `FunctionModel`-backed agent injected through the task-058 runtime seam, runs on a `tmp_path`
  `.kitaru/` local stack, and redirects the session log / `.decode/` under `tmp_path`. No
  `GEMINI_API_KEY`, no network, no server.
- **Scripted conversation asserts the four sub-features:**
  1. **Durability (058):** a multi-step task runs to completion via `run_agent_task.run(task).wait()`
     and returns the scripted final text; checkpoints are recorded and a **replay** serves a finished
     checkpoint from cache (assert the model is not re-invoked for the cached turn).
  2. **HITL (059):** the scripted agent calls `ask_user` (and/or hits a gated `write`) → the flow
     pauses on a **named durable wait**; the test injects the answer/verdict through the local input
     path and asserts it becomes the tool result / lets the write run; a replay does **not** re-ask.
  3. **Durable sleep (060):** the agent calls `sleep` in flow mode → the durable sleeper / `kitaru.wait`
     is invoked with the **capped** timeout (patched seam asserts it, no real wall-clock wait).
  4. **Credentials (061):** with the proxy enabled and `kitaru.get_secret` (or the env seam) patched,
     the model is constructed from the secret-sourced key and the raw key is absent from the flow
     payload.
- Assert the **real** flow ran the real agent loop (not a stub) and returned the expected output;
  assert no interactive `Runner`/`agent/loop.py` path was used (headless bypasses it).
- **Optional guarded real-local test:** a separate `@pytest.mark.skipif`-guarded test (skip when a
  local Kitaru stack is unavailable) that runs the flow against a **real local** `kitaru init` stack
  (still offline, no remote server) to prove the real wire — mirroring the LSP capstone's guarded
  real-`ty` test. The hermetic test (above) is the always-run proof; the guarded test never *fails*
  a `kitaru`-less/incompatible environment, only skips.

## Acceptance criteria

- [ ] Runs under `make integration-tests` / `make ci` with **no** `GEMINI_API_KEY`, **no network**,
      **no Kitaru server**; swaps only the runtime seam + the model (`FunctionModel`).
- [ ] Durability asserted: the task completes and returns the scripted output; a replay serves a
      finished checkpoint from cache (the model is not re-called for it).
- [ ] HITL asserted: a `ask_user`/approval pauses on a named durable wait, an injected answer resolves
      it (becomes the tool result / lets the write run), and a replay reuses the answer without
      re-asking.
- [ ] Durable sleep asserted: `sleep` in flow mode invokes the durable timer with the capped timeout
      (no real wall-clock pause); interactive `asyncio.sleep` is unaffected.
- [ ] Credentials asserted: with the proxy enabled (patched `get_secret`/env seam), the model is built
      from the secret-sourced key and the raw key is not in the serialized flow payload/logs.
- [ ] The real agent loop ran (a stub would not produce the scripted tool sequence); the interactive
      loop path was not exercised.
- [ ] The optional real-local test runs when a local Kitaru stack is available and is **skipped** (not
      failed) otherwise.
- [ ] `make ci` green, 0 warnings; `uv lock --check` passes.

## User stories

### Story: The capstone proves the whole runtime offline
1. A maintainer runs `make integration-tests`.
2. The runtime capstone drives a scripted headless task through the real `build_agent` + `@flow` +
   `KitaruAgent` with a fake model on a local stack — proving durability+replay, HITL waits, the
   durable sleep timer, and the credentials seam.
3. All assertions pass with no API key, no network, and no Kitaru server.

### Story: A contributor without a local Kitaru stack still passes
1. A contributor whose environment can't bring up a local stack runs `make ci`.
2. The hermetic capstone passes (patched seam); the guarded real-local test is skipped.
3. The suite is green — the feature degrades gracefully in test too.

### Story: CI with the runtime installed proves the real wire
1. CI (kitaru installed, local stack available) runs the guarded real-local test.
2. A real `@flow` execution checkpoints, pauses on a real wait, and resumes — proving the wiring
   against the actual SDK.
3. The test passes.

## Out of scope
- Re-testing the units already covered by 058-061 (this is the integrated proof).
- A live Gemini run or a remote/deployed Kitaru stack (deferred to step 12).
- Cross-process conversation resume (KitaruAgent history is in-memory — single-task scope only).

## Log
