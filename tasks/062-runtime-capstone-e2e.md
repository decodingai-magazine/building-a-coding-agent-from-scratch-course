---
id: 062-runtime-capstone-e2e
feature: kitaru-runtime
status: done
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

- [x] Runs under `make integration-tests` / `make ci` with **no** `GEMINI_API_KEY`, **no network**,
      **no Kitaru server**; swaps only the runtime seam + the model (`FunctionModel`).
- [x] Durability asserted: the task completes and returns the scripted output; a replay serves a
      finished **model** checkpoint from cache (the model is not re-called for it). **VERIFIED FIRST:**
      `flow.replay(exec_id, from_=<downstream checkpoint>)` of a finished `"calls"` run genuinely serves
      the upstream model checkpoints from cache on the local stack (model leg-count does not move,
      deterministic) — so this is the **real** assertion, not a stand-in
      (`test_replay_serves_a_finished_model_checkpoint_from_cache`).
- [x] HITL asserted: a `ask_user`/approval pauses on a named durable wait, an injected answer resolves
      it (becomes the tool result / lets the write run). **AC3 wording CORRECTED to match the verified
      local-stack reality (PA to adjudicate):** a `ask_user` wait is opted out of its per-call checkpoint
      to land at flow scope (ADR-0008 §3), so it is **never cached** — a replay **re-asks** it rather than
      reusing the saved answer. "A replay reuses the answer without re-asking" needs a **deployed** stack
      (deferred to step 12). What is provable on the local stack — and the key a deployed replay would
      reuse the answer by — is the **deterministic wait name**, which the replay re-creates identically
      (`test_replay_re_asks_a_wait_on_the_local_stack`).
- [x] Durable sleep asserted: `sleep` in flow mode invokes the durable timer with the capped timeout
      (no real wall-clock pause); interactive `asyncio.sleep` is unaffected.
- [x] Credentials asserted: with the proxy enabled (real Kitaru secret), the model is built
      from the secret-sourced key and the raw key is not in the serialized flow payload/logs.
- [x] The real agent loop ran (a stub would not produce the scripted tool sequence); the interactive
      loop path was not exercised (a spy asserts neither `Runner` nor `AgentTurnHandler` is constructed).
- [x] The optional real-local test runs when a local Kitaru stack is available and is **skipped** (not
      failed) otherwise.
- [x] `make ci` green, 0 warnings; `uv lock --check` passes.

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

### [SWE] 2026-06-28 14:40 — Implementation

**Files modified**
- `tests/integration/test_runtime_capstone.py` — new; the OFFLINE end-to-end capstone for the Kitaru
  runtime (7 tests): durability, model replay-from-cache, HITL named waits, the local-stack re-ask
  reality (AC3), the durable sleep timer, the credentials proxy, and the guarded real-local smoke.
- `tasks/062-runtime-capstone-e2e.md` — `status: in-progress`; ACs checked; **AC2/AC3 wording
  corrected** to match the verify-first replay finding below.

**Tests**
- Unit: 991 passing, 0 failing (`make pre-commit`).
- Integration: 19 passing (7 new + 12 existing capstones), 0 failing (`make integration-tests`).
- Full `make ci`: 1010 passing, 0 warnings; `uv lock --check` clean; format-check + lint-check clean.
- The new file run **alone under `-W error` twice** → 7 passed, 0 `PytestUnraisableExceptionWarning`
  (hermetic: ZenML engine disposed + idle loop closed + `gc.collect()` in teardown, mirroring the 059
  conftest fix).

**⚠️ REPLAY VERIFICATION — verified first, NOT faked (the AC2/AC3 adjudication for PA).**
Probed the installed kitaru 0.18 local in-process stack directly (`flow.replay(exec_id, from_=…)`).
The grooming warning (059 "local stack can't replay") splits into two distinct realities:

1. **Finished MODEL checkpoints DO replay from cache on the local stack — AC2 is REAL.** Replaying a
   finished `"calls"`-strategy run `from_` a downstream checkpoint serves the upstream model
   checkpoints from the original execution's cache: the scripted `FunctionModel`'s leg-count does **not**
   increase (deterministic across 3+ probe runs). So the AC2 "model not re-called for the cached turn"
   assertion is implemented for real (`test_replay_serves_a_finished_model_checkpoint_from_cache`), not
   a stand-in. This is a STRONGER result than 059 anticipated.
2. **A WAIT answer does NOT replay from cache on the local stack — AC3 corrected.** A HITL `ask_user`
   wait is opted out of its per-call checkpoint precisely so its wait lands at flow scope (ADR-0008 §3),
   so it is never cached — a replay **re-creates (re-asks)** the wait under the same deterministic name
   rather than reusing the saved answer (deterministic across probes, even replaying from the terminal
   `_capture_runtime_output`). So AC3's "a replay reuses the answer without re-asking" needs a
   **deployed** stack (deferred to step 12), confirming the spirit of 059's deny/resume finding. The
   capstone asserts what IS provable locally — the **deterministic wait name** the replay re-creates
   identically (the key a deployed replay would reuse the answer by) — in
   `test_replay_re_asks_a_wait_on_the_local_stack`, and the AC3 wording is corrected above. **PA to
   adjudicate the wording change.**

**Acceptance criteria**
- [x] Offline (no key/network/server), seam+model swapped only — `test_runtime_capstone.py` whole file.
- [x] Durability + real model replay-from-cache — `test_durability_runs_the_real_flow_to_completion`,
      `test_replay_serves_a_finished_model_checkpoint_from_cache`.
- [x] HITL named waits + injected answers drive the tools —
      `test_hitl_pauses_on_named_waits_and_injected_answers_drive_the_tools`; AC3 replay-reuse corrected
      and documented by `test_replay_re_asks_a_wait_on_the_local_stack`.
- [x] Durable sleep with the capped timer, interactive seam unaffected —
      `test_durable_sleep_uses_the_capped_timer`.
- [x] Credentials proxy sources the key, raw key off the payload —
      `test_credentials_proxy_sources_the_key_and_keeps_it_off_the_payload`.
- [x] Real agent loop ran, interactive `Runner`/`AgentTurnHandler` never built (spy) —
      `test_durability_runs_the_real_flow_to_completion`.
- [x] Guarded real-local test runs/skips gracefully — `test_real_local_stack_wire`.
- [x] `make ci` green, 0 warnings, `uv lock --check` passes.

**Evidence**
```
$ uv run pytest tests/integration/test_runtime_capstone.py -W error -q   # run twice
.......                                                                  [100%]
7 passed in 15.45s
.......                                                                  [100%]
7 passed in 15.61s

$ make ci
... 1010 passed in 55.22s ...

$ uv lock --check
Resolved 149 packages in 2ms
```

**Notes**
- The tests are **synchronous** (the `@flow` is sync; `KitaruAgent.run_sync` bridges the async agent),
  so they do not run under pytest-asyncio's loop — matching the 058-061 unit tests.
- The capstone is **self-contained** (the established capstone convention: M1/LSP/compaction all are),
  copying the load-bearing 059 hermeticity teardown + the inline-wait resolver verbatim with
  attribution, rather than importing across the unit/integration test boundary.
- **Guarded real-local test scope:** it proves the real `@flow` checkpoints + completes on the real
  local stack and skips gracefully when the runtime is absent. The "pauses on a real wait" half of User
  Story 3 IS proven (hermetically, by the HITL tests resolving a real flow-scope wait inline); the
  "…and resumes" (post-timeout resume) half needs a **deployed** stack and stays deferred to step 12 —
  same boundary as 059.
- No production code changed — this task adds only the capstone test + the task-file updates.
- DID NOT commit — handing off to the Tester first.

### [Tester] 2026-06-28 14:05 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` → 140 files formatted; `ruff check` → all checks passed)
- Unit tests: 991 passed / 0 failed
- Integration tests: 19 passed / 0 failed (7 new + 12 existing capstones; no cross-test contamination)
- Full `make ci`: 1010 passed / 0 failed; `uv lock --check` clean
- Warnings: 0 (suite runs under `filterwarnings=["error"]`; the two scoped third-party deprecations are unrelated to decode)

**E2E adversarial pass**
- Happy path: `uv run pytest tests/integration/test_runtime_capstone.py -W error -q` (run twice) → `7 passed` deterministically, 0 `PytestUnraisableExceptionWarning` (PASS)
- Break path 1 (hermeticity / keyless): `env -u GEMINI_API_KEY uv run pytest tests/integration/test_runtime_capstone.py -W error -q` → `7 passed` (PASS — genuinely offline/keyless; the credentials test mints its own Kitaru secret, the others patch the seam so `build_agent` is never reached)
- Break path 2 (replay-from-cache is REAL, not a no-op — independent probe with ONE shared model-leg counter): initial run legs=2 (steps `model_request`, `read_tool`, `model_request_2`, `_capture_runtime_output`); `replay(from_=_capture_runtime_output)` → **delta=0** (cache hit); `replay(from_=<first checkpoint>)` → **delta=1** (model re-fires when NOT cached → cache is selective, not a blanket no-op); fresh `.run()` → **delta=2** (new exec re-pays) (PASS — AC2 assertion is meaningful)
- Break path 3 (state leakage / seam reset): `test_durable_sleep_uses_the_capped_timer` asserts `_SLEEPER` is reset to `_interactive_sleep` on flow exit, and the clamp runs before the seam (sleep(10_000) with `sleep_max_s=3.0` → `kitaru.wait(name="sleep", timeout=3)`, int-coerced) (PASS — no durable-sleeper leakage into a later in-process sleep)
- Break path 4 (no filesystem leakage): tools write under the `tmp_path` cwd; `git status` after all runs shows no stray `.kitaru`/files in the repo (PASS)

**Acceptance criteria**
- [x] PASS — Offline (no key / network / server), seam + model swapped only — verified by the keyless run above; `FunctionModel` only, `tmp_path` ZenML store, `Path.home`/cwd redirected, seams `_build_runtime_agent` / `_build_hitl_runtime_agent` patched.
- [x] PASS — Durability + real replay-from-cache — `test_durability_runs_the_real_flow_to_completion` (real flow, file lands on disk under BYPASS, `Runner`/`AgentTurnHandler` spies not called, finished checkpointed execution, fresh re-run = new exec_id) + `test_replay_serves_a_finished_model_checkpoint_from_cache` (independently re-proven a genuine cache hit, see break path 2).
- [x] PASS — HITL named waits + injected answers drive the tools — `test_hitl_pauses_on_named_waits_and_injected_answers_drive_the_tools` (two ordered named waits: `_hitl_wait_name(question)` then `approve_write*`; approved write lands on disk). AC3 wording **corrected** by the SWE (replay re-asks the flow-scope wait; reuse needs a deployed stack, deferred to step 12) — `test_replay_re_asks_a_wait_on_the_local_stack`. Correction is consistent with the `_HITL_WAIT_TOOL_NAMES` checkpoint opt-out + ADR-0008 §3 and matches the verified local-stack behavior → ACCEPTABLE-with-correction (flagged for PA).
- [x] PASS — Durable sleep with the capped timer; interactive seam unaffected — `test_durable_sleep_uses_the_capped_timer` (see break path 3).
- [x] PASS — Credentials proxy sources the key, raw key off the payload — `test_credentials_proxy_sources_the_key_and_keeps_it_off_the_payload` (real `build_agent(flow_mode=True)` resolves the model key from a real Kitaru secret == `_KITARU_RAW_KEY` ≠ settings sentinel; persisted run params == `{"task"}`; neither raw key in `run.config.model_dump_json()`).
- [x] PASS — Real agent loop ran, interactive path not exercised — durability test (`counter["legs"] >= 3` + `runner_spy.assert_not_called()` + `handler_spy.assert_not_called()`).
- [x] PASS — Guarded real-local test runs/skips gracefully — `test_real_local_stack_wire` RAN here (kitaru + zenml importable); the `skipif` import probe never *fails* a stripped env. Note: the guard is import-based and the test runs on the same isolated `tmp_path` stack as the hermetic tests, so it is a thin smoke rather than a distinct "real local stack" path — the task framed it as optional; not blocking.
- [x] PASS — `make ci` green, 0 warnings, `uv lock --check` passes — 1010 passed.

**Evidence**
```
$ make integration-tests
... tests/integration/test_runtime_capstone.py .......                       [100%]
============================= 19 passed in 15.98s ==============================

$ make ci
uv lock --check
uv run ruff format --check → 140 files already formatted
uv run ruff check → All checks passed!
============================ 1010 passed in 55.38s =============================

$ env -u GEMINI_API_KEY uv run pytest tests/integration/test_runtime_capstone.py -W error -q
7 passed in 15.68s

# independent replay-cache probe (throwaway, removed after):
PHASE1 initial run legs=2 steps=[model_request, read_tool, model_request_2, _capture_runtime_output]
PHASE2 replay from terminal legs=2 (delta=0)   # cache hit
PHASE3 replay from FIRST cp   legs=3 (delta=1)  # model re-fires when not cached
PHASE4 fresh re-run           legs=5 (delta=2)  # new exec re-pays
```

**Other issues found**
- None blocking. PASS-with-note for PA / PR Reviewer: (a) the AC3 wording change ("replay re-asks; reuse deferred to step 12") and the "wait-resume on a deployed stack" boundary stay flagged for PA — same deferral boundary as 059/060/061; (b) `test_real_local_stack_wire`'s guard is import-based, so in any installed environment it runs identically to the hermetic tests rather than exercising a distinct bring-up — acceptable given the task made it optional and the hermetic tests are the primary proof.
- File ownership clean: diff is only `tests/integration/test_runtime_capstone.py` (new) + `tasks/062-runtime-capstone-e2e.md`; `git diff --stat -- src/` is empty (no production code touched).

**VERDICT: PASS**

### [PA] 2026-06-28 — Acceptance Review (feature `kitaru-runtime`, tasks 057-062, PR #19)

**VERDICT: ACCEPT**

Reviewed the whole feature from the user's perspective — the `decode run` / `decode run --hitl`
surfaces, every friendly guard line, the flow output paths, the README headless + credentials
sections, the AGENTS.md E2E rows, `.env.example`, ADR-0008/0009, and the six glossary rows. The
interactive TUI is genuinely untouched (`git diff main...HEAD -- src/decode/tui/` is empty;
`agent/loop.py` carries only the documented ADR-0009 `last_input_tokens` usage shim, and the
compaction trigger + Context Gauge read it unchanged with the full suite green at 1010).

Per sub-feature (concrete evidence):
- **Durability + replay (058/062):** `decode run "<task>"` tool-loops headlessly under bypass and
  prints the agent's final text, exit 0; tools still honor sandbox containment + arg validation
  inline under bypass (Tester breaks 1-4). `flow.replay()` serves finished MODEL checkpoints from
  cache on the local stack (capstone break path 2: delta=0 cache hit, delta=1 when uncached,
  delta=2 on a fresh run) — replay-from-cache is real, not a stand-in.
- **HITL (059):** a gated `write`/`bash` and `ask_user`/`exit_plan_mode` pause the execution on a
  named durable wait resolved out-of-band (`kitaru executions input`); an injected allow runs the
  tool, an answer becomes the tool result; live break path confirmed an unapproved write never ran.
- **Durable sleep (060):** `sleep` becomes a flow-scope `kitaru.wait(name="sleep", timeout=capped)`
  in `decode run --hitl`, clamp + nan/negative `ModelRetry` fire before the wait, seam resets on
  exit (no leak into an in-process REPL sleep).
- **Credentials proxy (061):** with the proxy on, the gemini/openrouter model key resolves from a
  Kitaru secret inside the flow body; the serialized payload + all logs carry only the task + secret
  name (re-attacked on the real store — leaked nowhere); a missing/incomplete secret is one friendly
  pre-flight line on both `decode run` and `--hitl`, never a traceback, never a silent settings
  fallback.

Explicit ruling on the four flagged deviations:
1. **Headless deny STOPS the run (no feed-back-to-model)** — **ACCEPT.** A clean abort with a clear
   message (`_HITL_DENIED_MESSAGE`) is the safer default for an *unattended* run than letting the
   model adapt around a denied destructive op with no human watching. Accurately documented (ADR-0008
   §3, AC2, the AGENTS.md HITL row "`'false'` (deny → the run stops, the tool never ran)").
2. **HITL forces `checkpoint_strategy="calls"` + durable sleep is `--hitl`-only** — **ACCEPT.** A
   flow-scope wait cannot live under a `"turn"` checkpoint (a true adapter constraint, Tester
   reproduced the `KitaruUsageError` live), and a durable sleep only makes sense in the wait-capable
   pausing flow — the plain bypass run is non-pausing by design, so it correctly keeps `asyncio.sleep`.
   The user does not need durable sleep in plain `decode run`. Documented in README l.151, task 060
   User Story 1, the flow docstring; `runtime_checkpoint_strategy` governing only the bypass run is noted.
3. **modal not routed through the credentials proxy** — **ACCEPT.** The two single-api-key providers
   (gemini — the default — and openrouter) ARE proxied; modal authenticates with dual proxy *tokens*
   (a header surface belonging to the later sandbox step), a genuinely different mechanism. Documented
   in `factory.py` (l.119-121, 160-164), `cli.py` (l.106-110), README l.173, ADR-0008 §5.
4. **HITL answer-reuse / wait-resume on replay deferred to a deployed stack (step 12)** — **ACCEPT.**
   Durability replay-from-cache is proven locally (deviation reconciled above); the wait-answer reuse
   is deferred precisely because the wait is opted out of its per-call checkpoint to land at flow
   scope (so it is never cached and a replay re-asks) — an inherent consequence of the correct design,
   not a hidden gap. AC3 wording corrected; the capstone asserts what is locally provable (the
   deterministic wait name a deployed replay would reuse the answer by).

ADR-0009's heavy-footprint downgrade is honestly recorded (the caps table, the ~40 transitive deps,
the meta→`pydantic-ai-slim[google,openai]` correction that sheds them, the new pydantic-ai-2.x
ceiling, reversibility). The glossary additions (Headless Runtime, Durable Flow, Checkpoint, Replay,
Wait (HITL), Credentials Proxy) read consistently with the shipped code and cross-reference the
existing Decision Channel / Sandbox / Provider Seam rows correctly.

Note (non-blocking, for a future deployed-stack step, not this MVP): the glossary's **Replay** /
**Wait (HITL)** rows state the *designed* "Replay reuses the prior answer" behavior — accurate as the
concept and on a deployed stack; the local-stack re-ask limitation lives in ADR-0008 §3 + AC3. A
one-line "(on a deployed stack)" qualifier there would be tidy but is not user-facing and not REJECT-worthy.

All acceptance criteria verified from the user POV. Hand off to the PR Reviewer.
