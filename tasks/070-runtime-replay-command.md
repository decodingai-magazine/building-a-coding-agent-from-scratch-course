---
id: 070-runtime-replay-command
feature: runtime-replay
status: done
---

# `decode replay <exec_id> [--from] [--model]` (bypass-only, 1:1 Kitaru) + offline test + AGENTS.md replay docs

Tags: `runtime`, `cli`, `replay`, `docs`, `test`
Depends on: #067, #068, #069
Blocks: —

This task implements **ADR-0010 §5-6**. A thin CLI wrapper over the Kitaru flow-object replay
(`run_agent_task.replay(exec_id, from_=…, model=…)` — confirmed against docs.zenml.io "Replay and
Overrides" and the capstone's `run_agent_task_hitl.replay(...)`) whose **anchor semantics mirror
Kitaru 1:1** (no decode-invented default). Replay re-runs a recorded bypass run with one thing
changed (the model), preserving the original task and linking the new execution for comparison. This
task also owns the OFFLINE integration test proving a model swap re-executes downstream turns, and
**all** of the AGENTS.md replay documentation (the two E2E manual-QA rows + the operator playbook).

**Semantics confirmed (kitaru 0.18):** `flow.replay(exec_id, from_="cp", model="…")` forwards `model`
to the flow's `model` param (#067); everything upstream of `from_` serves from cache, everything at
`from_` and downstream re-executes for real. So a model swap only bites downstream of `--from`. Decode
mirrors Kitaru's `from_` semantics exactly — it does NOT compute or invent a default anchor.

## Scope — the `decode replay` command

- **New `decode replay` command in `cli.py`** (a sibling of `run`):
  - `@cli.command("replay")`, `@click.argument("exec_id")`,
    `@click.option("--from", "from_", default=None, metavar="CHECKPOINT")`,
    `@click.option("--model", "model", default=None, metavar="ID")`.
  - Guard chain reused from `run` (replay re-executes downstream model calls, so it needs a valid
    provider config): `runtime_enabled`, provider-config / proxy / secret-store pre-flights. Lazy
    `from decode.runtime import …` (keep kitaru off the REPL path).
  - **Bypass-only.** Detect whether `exec_id` belongs to the bypass flow (`run_agent_task` /
    `RUNTIME_AGENT_NAME`) vs the HITL flow (`run_agent_task_hitl` / `HITL_RUNTIME_AGENT_NAME`).
    Verify the detection mechanism against the SDK (e.g. the recorded pipeline name via
    `KitaruClient` / `Client().get_pipeline_run(exec_id)`); if it is a HITL run, exit non-zero with
    one friendly line pointing at the limitation + `kitaru executions replay <id>` (ADR-0010 §5; HITL
    replay re-asks every wait — deferred, `tasks/future/`).
  - **`--from` = Kitaru 1:1.** Pass `--from` straight to `run_agent_task.replay(from_=…)`. Do NOT
    invent or compute a default anchor. Verify on the installed kitaru 0.18 what
    `flow.replay(exec_id, model=…)` does when `from_` is omitted (replays from the first checkpoint,
    or errors); mirror that behaviour exactly — if kitaru requires `from_`, surface kitaru's own
    requirement as one friendly stderr line (no traceback). Record the verified semantics in the SWE log.
  - **`--model` default:** `None` → replay as-is (same model the original run recorded). It maps to
    the flow-arg kitaru swaps (`replay(..., model=…)`). Raw `--args`/`--overrides` are NOT exposed on
    `decode replay` — they stay on the `kitaru executions replay` CLI (documented below).
  - Launch: `handle = run_agent_task.replay(exec_id, from_=from_, model=model)`; read the output via
    the #068 contract (`_load_runtime_output(handle.exec_id)` or `.wait().output`). Print the answer
    on stdout; on stderr print the **new** `exec_id`, the `original exec_id` (from
    `handle.original_exec_id` if exposed, else the input), and a diff hint pointing at the confirmed
    Kitaru surface (verify whether a `kitaru diff` CLI exists in 0.18; if not, point at
    `kitaru executions get <new>` / the SDK `forked.diff(control)` and the replay-and-improve guide —
    documented below). Do **not** hardcode an unconfirmed `kitaru diff <a> <b>` command.
  - Surface Kitaru replay failures friendly: catch `KitaruStateError` (ambiguous/invalid `--from`),
    `KitaruDivergenceError` (the swap diverged the recorded call sequence), and a missing/HITL
    exec_id → one friendly stderr line each, non-zero exit, never a raw traceback.

## Scope — offline integration test

- Add to `tests/integration/test_runtime_capstone.py` (reuse its autouse `isolated_kitaru_store`,
  `_scripted_agent`, `_steps`, and seam-patch pattern) — or a sibling file replicating that
  isolation. `test_model_swap_replay_re_executes_downstream_turns`:
  - Patch `_build_runtime_agent(model)` to return **different scripted agents keyed on `model`**
    (baseline vs. swapped), each `checkpoint_strategy="calls"`.
  - `handle = run_agent_task.run(task="…", model="model-baseline")`; assert baseline output; capture
    `exec_id`; pick the replay anchor **explicitly** from `_steps(exec_id)` (a checkpoint at-or-before
    the first model call).
  - `replay = run_agent_task.replay(exec_id, from_=<anchor>, model="model-swapped")`.
  - Assert the **swapped** agent drove the re-executed turns: its leg-counter moved / the replay
    output equals the swapped agent's scripted text and differs from baseline — the real proof that
    the model swap re-executed downstream turns (the inverse of the capstone's
    `test_replay_serves_a_finished_model_checkpoint_from_cache`, which anchors at the terminal so the
    model is NOT re-called).
  - Assert `replay.exec_id != exec_id` (a new linked execution).

## Scope — AGENTS.md replay documentation (all of it)

- **Two E2E manual-QA rows** in the "Testing E2E" table, in the style of the existing `decode run` /
  `decode run --hitl` rows:
  - `decode run --model` — override the model for one headless run; note stdout answer + stderr
    `exec_id`/replay hint.
  - `decode replay <exec_id> --model` — re-run a recorded bypass run with a swapped model; note the
    new exec_id + diff hint, the bypass-only scope, and the documented HITL limitation. State the
    offline-provable scope (bypass model-swap) vs. the deferred HITL answer-reuse.
- **A "Headless replay & what-if (Kitaru operator surface)" subsection** documenting the surface
  decode deliberately does NOT wrap (all Kitaru CLI / SDK / `kitaru_recipes`, verified against
  docs.zenml.io):
  - **The checkpoint → replay → diff → decide loop**, and the "three runs, not two" rule:
    **Observed** (the original recorded run) → **Baseline Rerun** (`kitaru executions replay <id>
    --from <cp>` with no change — the control) → **Fork** (same `--from`, one input changed). You diff
    the **Fork against the Baseline Rerun**, not the Observed run, because the control isolates your
    one variable. If the Baseline Rerun does not reproduce the Observed run (nondeterminism), the diff
    is untrustworthy.
  - **CLI replay with overrides:**
    `kitaru executions replay <exec_id> --from <cp> --args '{"model":"gemini-2.5-pro"}' --overrides '{"checkpoint.<name>":<value>}'`
    — `--args` = flow-input overrides (the CLI mirror of `flow.replay(..., model=…)`; decode surfaces
    `--model` for the common case); `--overrides checkpoint.<name>` = substitute a recorded
    checkpoint's output at its consumers. Override keys must start with `checkpoint.` and the
    overridden checkpoint must expose a single output.
  - **`--overrides checkpoint.X` is the tool-output mock stand-in.** Per-tool-call `output=` /
    `raise_=` mocks are **Kitaru roadmap, not shipped** — use `checkpoint.X` to substitute a tool's
    recorded output today.
  - **Diff:** the SDK `forked.diff(baseline_rerun)` (decision change + cost/latency deltas). Point at
    the confirmed surface; only mention a `kitaru diff` CLI if the command scope above verified it
    exists on the installed version.
  - **Cohort / recipes** — explicitly framed as Kitaru's **example pattern on the SDK primitives, not
    a core API**: `from kitaru_recipes import cohort, Recipe, cost, latency, quality_judge`;
    `batch = cohort(flow, last=N)`; `recipe = Recipe(change={"model": …}, from_="…", metrics=[cost,
    latency, quality_judge])`; `result = batch.experiment(recipe); result.summary()`. Note it may
    require the `kitaru_recipes` example package and is a way to scale one change across recent runs.
  - **Waits on replay:** a replayed run **re-asks** every wait (Kitaru "does not support overriding or
    pre-populating wait results") — which is exactly why `decode replay` is bypass-only and HITL
    answer-reuse is deferred (link `tasks/future/hitl-replay-answer-reuse.md` and ADR-0010 §7).
  - A one-line pointer that a coding agent / MCP client can drive this whole loop (Kitaru MCP server)
    — the future automation hook, no decode work now.

## Acceptance criteria

- [x] `decode replay <exec_id> --model <id>` replays the recorded bypass run and prints the (possibly
      changed) answer on stdout; the new `exec_id` + diff hint print on stderr. Driven end-to-end by
      the offline integration test.
- [x] The offline test proves the model swap **re-executes downstream turns with the new model** (the
      swapped scripted agent's leg-counter moves / its text is returned), not served from cache — the
      inverse of the terminal-anchor cache proof.
- [x] `--from <cp>` anchors a partial replay (upstream cached, downstream re-executed); omitting
      `--from` mirrors Kitaru's own behaviour exactly (no decode-invented default). The SWE log
      records the verified `from_` semantics on the installed kitaru.
- [x] Replaying a **HITL** exec_id exits non-zero with one friendly line naming the bypass-only limit
      and `kitaru executions replay` — no traceback (unit test with a HITL-recorded exec or a mocked
      detector).
- [x] Ambiguous/invalid `--from` (`KitaruStateError`) and a diverged swap (`KitaruDivergenceError`)
      each surface one friendly stderr line, non-zero exit, no raw traceback (unit tests).
- [x] The full `run` guard chain fires for `replay` too (disabled runtime / missing key / proxy /
      secret-store) — friendly line, no replay attempted (tripwire tests).
- [x] `AGENTS.md` gains the two E2E rows (`decode run --model`, `decode replay`) **and** the "Headless
      replay & what-if (Kitaru operator surface)" subsection (checkpoint→replay→diff→decide, the
      "three runs" rule, `kitaru executions replay --args/--overrides`, `--overrides checkpoint.X` as
      the tool-mock stand-in with per-tool mocks flagged as roadmap, `forked.diff(control)`,
      `kitaru_recipes` framed as example-pattern-not-core-API, the wait-re-ask limitation linked to
      `tasks/future/`). A docs check confirms every claim matches the real surface and asserts nothing
      unshipped; no unconfirmed `kitaru diff` CLI is presented as fact. *(Deviation: verify-first found
      `forked.diff()` / `kitaru_recipes` are NOT shipped in kitaru 0.18 — the docs instead document the
      REAL shipped surfaces the ZenML guide uses, `KitaruClient().executions.get()` comparison +
      `run_cohort` from the examples repo. See SWE log.)*
- [x] `import decode.cli` still does not import `kitaru` (lazy-import subprocess test); `make ci`
      green, **0 warnings**; `uv lock --check` passes.

## User stories

### Story: A developer asks "what if I had used the bigger model?"
1. Developer ran `decode run "triage this failing test"` earlier and kept `exec_id: kr-abc123`.
2. They run `decode replay kr-abc123 --from <checkpoint> --model gemini-2.5-pro`.
3. The recorded run re-executes with the bigger model from the anchor; the (possibly different) answer
   prints, and stderr shows the new `exec_id: kr-def456` plus a diff hint.
4. They compare the two runs on Kitaru's surface to decide whether the bigger model was worth it.

### Story: A developer anchors the change to a mid-run checkpoint
1. Developer runs `decode replay kr-abc123 --from <checkpoint> --model gemini-2.5-pro`.
2. Turns before the checkpoint replay from cache (original model); turns from the checkpoint forward
   re-execute with the bigger model — isolating the change to the back half of the run.

### Story: Replaying a HITL run is refused with guidance
1. Developer runs `decode replay <hitl-exec-id> --model X`.
2. They get one friendly line: decode replay is bypass-only (HITL replay re-asks every wait); use
   `kitaru executions replay <id>` if you really want to. Exit non-zero, no traceback.

### Story: An operator scales a winning change across recent runs
1. After `decode replay <id> --model gemini-2.5-pro` looks promising, the operator opens AGENTS.md.
2. They follow the documented `kitaru_recipes` cohort snippet to run the same model change across the
   last N recorded runs and read the aggregate cost/latency/quality summary.
3. They decide whether to ship the model change based on the cohort, not a single run.

## Out of scope
- Any decode-side `diff` / `cohort` command (Kitaru CLI + `kitaru_recipes` only — documented, not wrapped).
- Per-tool-call `output=`/`raise_=` mocks (Kitaru roadmap; `--overrides checkpoint.X` is the stand-in).
- Raw `--args` / `--overrides` flags on `decode replay` (kitaru CLI only).
- HITL replay with answer-reuse (`tasks/future/hitl-replay-answer-reuse.md`).

## Log

### [SWE] 2026-07-02 17:40 — Implementation

**VERIFY-FIRST (probed the installed kitaru 0.18 + the real capstone flow on an isolated store, before coding):**

1. **`from_` omitted → Kitaru REQUIRES it (no default).** `_FlowDefinition.replay(exec_id, *, from_: str,
   …)` — `from_` is a **required keyword-only** arg. Empirically: `run_agent_task.replay(exec_id)` →
   `TypeError: … missing 1 required keyword-only argument: 'from_'`; `replay(exec_id, from_=None)` →
   `AttributeError: 'NoneType' object has no attribute 'strip'` (ugly). So decode does **not** pass a
   None-`from_` through and invents **no** default anchor — it mirrors Kitaru's requirement by exiting
   with one friendly line when `--from` is omitted (`_REPLAY_NO_FROM_MESSAGE`). Empty `from_` (`""`) →
   `KitaruUsageError`, but decode guards None before that.
2. **Bypass vs HITL detection = the recorded flow name.** `KitaruClient().executions.get(exec_id).flow_name`
   returns **`"run_agent_task"`** (bypass) vs **`"run_agent_task_hitl"`** (HITL) — verified against real
   runs of both flows. `is_hitl_execution` uses this; the constants derive from the `@flow` funcs'
   `__name__` so they can't drift. (`run.pipeline.name` gives the same; `run.config.pipeline` does NOT
   exist.) A missing exec_id → `executions.get` raises `KitaruBackendError`.
3. **Exception classes (all confirmed on the installed SDK):** ambiguous/invalid `--from` →
   **`KitaruStateError`** ("Unknown checkpoint selector 'x'. Available checkpoints: …" — which decode
   surfaces verbatim so the operator sees valid anchors); a diverged swap → **`KitaruDivergenceError`**
   (raised via `execution_error_from_failure` with `FailureOrigin.DIVERGENCE`); a missing/unloadable
   exec_id → **`KitaruBackendError`** ("Failed to load execution '…'"). The cli catches
   `KitaruStateError` / `KitaruDivergenceError` specifically, then a `KitaruError` catch-all (Backend +
   anything else) so **no** raw traceback can escape.
4. **NO `kitaru diff` CLI, NO `.diff()`/`forked.diff()` SDK, NO `kitaru_recipes` package in 0.18.** The
   real CLI groups are `analytics auth build clean deploy executions flow … secrets stack …` — no
   `diff` anywhere. `grep` for `def diff` / `forked` / `cohort` / `Recipe` / `experiment` across the
   shipped kitaru python = **empty**; `import kitaru_recipes` → `ModuleNotFoundError`. **CONFIRMED**
   surfaces (from the CLI source + docs.zenml.io "Replay and Overrides" / "Replay and improve"):
   `kitaru executions replay <id> --from <cp> --args '{…}' --overrides '{"checkpoint.*":…}'`,
   `kitaru executions get/list/input/statistics`, `kitaru secrets set`, and the `kitaru-mcp` server.
   The ZenML guide's own **diff** is a manual comparison of two `KitaruClient().executions.get(...)`
   records (not a method); its **cohort** is `run_cohort` from the examples repo
   (`examples/end_to_end/pydantic_replay_fork`), explicitly *"not in the `kitaru` package."*
5. **Model-swap replay mechanics (isolated store):** `replay.exec_id != exec_id` (a new Fork ✓); the
   swapped agent's model **re-executes** the anchored turn (its leg-counter moves). Under `"calls"` the
   per-call checkpoints are **DAG-independent siblings** and the terminal `_capture_runtime_output` sink
   has **no upstream edge**, so anchoring at one model call re-executes only that call and the cached
   terminal artifact still serves the *baseline text*. So the faithful proof the swap re-executed is the
   **leg-counter** (exactly the task's "leg-counter moves" option), not the returned text — the clean
   inverse of the existing terminal-anchor cache test.

**Deviation from the task/ADR doc wording (honesty-driven, within the "assert nothing unshipped"
mandate):** the spec/ADR-0010 §6 name `forked.diff(control)` and `kitaru_recipes import cohort, Recipe,
experiment`. Verify-first (#4) shows those exact APIs are **not shipped** in the installed kitaru 0.18.
The hard constraint "every claim must match the real surface; no unconfirmed `kitaru diff` as fact" wins,
so AGENTS.md documents the **real** shipped surfaces (the `executions.get` comparison + the `run_cohort`
example pattern), each framed as example-pattern / roadmap per the ADR's intent. I did **not** touch
ADR-0010 (PA territory) — flagging for PA to reconcile the ADR's illustrative API names if desired.

**Files modified (src)**
- `src/decode/runtime/flow.py` — added the replay primitives: `RUNTIME_PIPELINE_NAME` /
  `HITL_RUNTIME_PIPELINE_NAME` (derived from the `@flow` `__name__`s), `is_hitl_execution(exec_id)`
  (reads `KitaruClient().executions.get(...).flow_name`, lazy kitaru import), the `ReplayResult`
  dataclass (new/`original_exec_id`/`output`), and `replay_agent_task(exec_id, *, from_, model)` (thin
  1:1 wrapper over `run_agent_task.replay(...)` reading output via the #068 `_load_runtime_output`).
- `src/decode/runtime/__init__.py` — re-export `ReplayResult`, `is_hitl_execution`, `replay_agent_task`.
- `src/decode/cli.py` — new `decode replay` command (arg `exec_id`, `--from`→`from_`, `--model`), sibling
  of `run`: reuses the guard chain, mirrors Kitaru's `--from` requirement (no invented default), refuses
  HITL exec_ids (bypass-only → `kitaru executions replay`), catches `KitaruStateError` /
  `KitaruDivergenceError` / `KitaruError` as friendly one-liners, prints the answer on stdout + the Fork
  id/source/`kitaru executions get` diff hint on stderr. **Refactor:** extracted `run`'s guard chain into
  a shared `_runtime_config_preflight()` (byte-identical behaviour) so `run` and `replay` can't drift.
  Added `_REPLAY_NO_FROM_MESSAGE` + the four replay message helpers + `_echo_replay_fork`. All kitaru
  imports stay lazy inside the command body — `import decode.cli` is still kitaru-free.
- `AGENTS.md` — two E2E manual-QA rows (`decode run --model`, `decode replay --model`) + the "Headless
  replay & what-if (Kitaru operator surface)" subsection (three-runs table, `executions replay
  --args/--overrides`, `--overrides checkpoint.X` as the tool-mock stand-in with per-tool mocks flagged
  roadmap, the real `executions.get` diff, the `run_cohort` example-pattern cohort, the wait-re-ask limit
  linked to `tasks/future/`, and the `kitaru-mcp` automation hook). Every claim scoped to the shipped
  surface; explicitly states there is no `kitaru diff` CLI / `.diff()` SDK / `kitaru_recipes` in 0.18.

**Files modified (tests)**
- `tests/unit/decode/runtime/test_replay_command.py` (new) — 12 cli-contract tests (kitaru boundary
  mocked at the `decode.runtime` re-export): help; `--from` required; HITL refusal (no replay attempted);
  happy-path stdout answer + stderr fork/source/`executions get` hint (asserts stdout pipe-clean + **no
  `kitaru diff`**); `--model` omitted → `model=None`; `KitaruStateError` / `KitaruDivergenceError` /
  `KitaruBackendError` each a friendly line, no traceback; and 4 guard tripwires (disabled runtime /
  missing key / proxy secret / secret-store) proving no replay is attempted when a guard trips.
- `tests/integration/test_runtime_capstone.py` — `test_model_swap_replay_re_executes_downstream_turns`:
  real `@flow` + `KitaruAgent` on the isolated store, two scripted agents keyed on `model`; baseline run,
  explicit anchor at the first `*_model_request`, replay with `model="model-swapped"` → the swapped
  counter moves, the baseline counter stays frozen, `replay.exec_id != exec_id`. The honest inverse of
  `test_replay_serves_a_finished_model_checkpoint_from_cache`.

**Tests**
- `make ci` → **1072 passed, 0 warnings** (`filterwarnings=["error"]`), `uv lock --check` clean,
  format-check + lint-check clean. (+25 vs task 067's 1047: 12 new replay-cli + 1 new capstone, plus the
  069 additions already merged.)
- Runtime capstone: 8 passed (7 prior + the new model-swap). Replay-cli unit file: 12 passed.

**Acceptance criteria** — all met (see the checked list above); none are `[HUMAN]`.
- [x] `decode replay --model` prints the answer on stdout + new exec_id/diff hint on stderr —
  `test_replay_prints_answer_on_stdout_and_fork_hint_on_stderr` + the offline capstone e2e.
- [x] Offline test proves the swap re-executes downstream with the new model —
  `test_model_swap_replay_re_executes_downstream_turns` (swapped leg-counter moves, baseline frozen).
- [x] `--from` maps 1:1; omitting it mirrors Kitaru's requirement (no invented default) —
  `test_replay_without_from_surfaces_kitarus_requirement` + real subprocess (evidence below).
- [x] HITL exec_id refused, friendly, no traceback — `test_replay_refuses_a_hitl_execution`.
- [x] `KitaruStateError` / `KitaruDivergenceError` each a friendly line —
  `test_replay_invalid_from_is_a_friendly_line`, `test_replay_diverged_swap_is_a_friendly_line`.
- [x] Full run guard chain fires for replay (4 tripwires, no replay attempted) + real subprocess.
- [x] AGENTS.md docs added (matched to the real surface; deviation noted above).
- [x] `import decode.cli` kitaru-free; `make ci` green, 0 warnings; `uv lock --check` passes.

**Evidence**
```
$ make ci
======================= 1072 passed in 125.49s (0:02:05) =======================
(grep -icE 'warning|deprecat' ci_log = 0)   $ uv lock --check → Resolved 149 packages

# Real `decode replay` subprocess — the store-free guard/no-from paths a user hits:
$ GEMINI_API_KEY=fake RUNTIME_ENABLED=true decode replay kr-abc123 --model gemini-2.5-pro
Decode: `decode replay` needs --from <checkpoint> — Kitaru replay requires an explicit anchor … (exit 1)
$ GEMINI_API_KEY=fake RUNTIME_ENABLED=false decode replay kr-abc123 --from cp
Decode: the headless runtime is disabled — set RUNTIME_ENABLED=true … (exit 1)
$ GEMINI_API_KEY="" RUNTIME_ENABLED=true decode replay kr-abc123 --from cp
Decode: set GEMINI_API_KEY in your environment or .env to start … (exit 1)
$ python -c "import decode.cli, sys; assert 'kitaru' not in sys.modules"  → OK (kitaru not imported)
```

**Notes**
- **Real subprocess `decode replay` against a live store can't run here** — this machine's dev ZenML
  store points at a down server (`127.0.0.1:8383`); `decode replay <id> --from cp` hangs at the
  detection `executions.get` (exit 124), exactly the 068 env limitation. The **hermetic offline
  integration test** (isolated tmp_path store, real `@flow` + adapter, only the model boundary scripted)
  is the faithful proof of the real model-swap replay; the store-free guard/no-from/HITL/error paths are
  proven by real subprocess + the 12 cli unit tests.
- The `--from` None-guard is decode's only deviation from raw pass-through, and it exists precisely to
  turn Kitaru's `AttributeError`/`TypeError` (verify-first #1) into a friendly line — still "1:1 Kitaru"
  in that decode adds no default anchor, only surfaces Kitaru's own requirement.
- `docs/adr/` + `docs/glossary.md` were **read-only** (used the existing `Replay` / `Model Override` /
  `Fork` / `Baseline Rerun` / `Checkpoint Override` terms verbatim); no edits. The doc-wording deviation
  (#4/§Deviation) is flagged for PA — I did not modify ADR-0010.
- DO NOT COMMIT yet — awaiting Tester review.

### [Tester] 2026-07-02 18:35 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` → 145 files already formatted; `ruff check` → All checks passed!)
- Unit tests: PASS (part of the full gate; replay-cli file 12/12; full CLI unit suite 116/116)
- Integration tests: PASS (runtime capstone 8/8, incl. the new model-swap)
- `make ci`: **1072 passed in 120.87s, 0 warnings** (`filterwarnings=["error"]`; no warnings-summary block); `uv lock --check` → Resolved 149 packages
- Warnings: 0

**Independent verification of the unshipped-API claim (ADR-affecting — the headline check)**
- `import kitaru_recipes` → **ModuleNotFoundError** (absent) — CONFIRMED.
- kitaru 0.18 python source grep (`find -name '*.py'`): **no** `forked` / `run_cohort` / `cohort` / `class Recipe` / `def experiment` / `def diff` — CONFIRMED empty.
- Flow object introspection: `run_agent_task` is `_FlowDefinition`, `has replay: True`, **`has diff: False`, `has fork: False`** — no `.diff()`/`forked` SDK method.
- CLI: top-level groups (`analytics auth build clean deploy executions flow info init invoke log-store login logout model secrets stack status`) and `kitaru executions` subcommands (`cancel get input list logs replay resume retry statistics`) contain **no `diff`** — CONFIRMED no `kitaru diff` CLI.
- **Verdict on the claim: the SWE is RIGHT.** `forked.diff()`, a `kitaru diff` CLI, and `kitaru_recipes` do NOT exist in kitaru 0.18. → AGENTS.md is correct to document the real surface; **ADR-0010 needs PA reconciliation** (see Other issues).
- Confirmed-present surfaces the docs rely on: `kitaru executions replay` with **`--from` (required)**, `--args`, `--overrides` (checkpoint.* keys); `kitaru executions get`; `decode run --model`; `kitaru-mcp` console script; link target `tasks/future/hitl-replay-answer-reuse.md` exists.

**E2E adversarial pass**
- Happy path (hermetic offline model-swap replay): `pytest …::test_model_swap_replay_re_executes_downstream_turns` → PASS. **Proved non-vacuous** by a Tester mutation (temporarily anchored the same scenario at the terminal `_capture_runtime_output`): swapped leg-counter stays **0** (served from cache), so the real test's `>= 1` at the first-model-request anchor genuinely discriminates re-execution from cache. Mutation removed; file restored (+83, verified).
- Break path 1 (boundary: missing `--from`, real subprocess): `decode replay kr-abc123 --model gemini-2.5-pro` → friendly `needs --from … (it has no default)`, exit 1, no traceback, no kitaru touched. PASS.
- Break path 2 (guard: runtime disabled, real subprocess): `RUNTIME_ENABLED=false decode replay kr-abc123 --from cp` → friendly line, exit 1. PASS.
- Break path 3 (guard: empty key, real subprocess): `GEMINI_API_KEY= decode replay kr-abc123 --from cp` → friendly key guard, exit 1. PASS.
- Break path 4 (CLI surface: raw overrides must not leak): `--args …` → `Error: No such option: --args` (exit 2); `--overrides …` → `No such option: --overrides` (exit 2). Confirms out-of-scope flags are not exposed. PASS.
- Break path 5 (malformed: missing `EXEC_ID` arg): `decode replay --from cp` → Click `Missing argument 'EXEC_ID'`, exit 2, no traceback. PASS.
- Break path 6 (hierarchy proof for the store-hang-blocked paths): every `Kitaru*Error` (incl. `KitaruUsageError` for empty `--from ""`, `KitaruBackendError` for missing id) subclasses `KitaruError` → the CLI catch-all renders each as a friendly line; none can escape as a traceback. PASS.
- Env limit (same as 068): a real live-store `decode replay <id> --from cp` subprocess hangs on this machine's down dev ZenML server (`127.0.0.1:8383`); the HITL-refusal + KitaruState/Divergence/Backend paths are covered by the 12 offline cli-contract tests, and the model-swap by the hermetic capstone. Judged sufficient — not failing for the environment.

**Acceptance criteria**
- [x] PASS — `decode replay --model` prints answer on stdout, fork id + source + diff hint on stderr — `test_replay_prints_answer_on_stdout_and_fork_hint_on_stderr` (stdout pipe-clean, no `kitaru diff`) + the offline capstone.
- [x] PASS — offline test proves the swap re-executes downstream (leg-counter), not cache — `test_model_swap_replay_re_executes_downstream_turns`; non-vacuousness independently proven by the terminal-anchor mutation.
- [x] PASS — `--from` 1:1; omitting it mirrors Kitaru's requirement (no invented default) — `replay` signature confirmed `from_` keyword-only/required; `test_replay_without_from_surfaces_kitarus_requirement` + real subprocess.
- [x] PASS — HITL exec_id refused, friendly, no traceback — `test_replay_refuses_a_hitl_execution`; `Execution.flow_name` field confirmed present.
- [x] PASS — KitaruStateError / KitaruDivergenceError friendly lines — `test_replay_invalid_from_is_a_friendly_line`, `test_replay_diverged_swap_is_a_friendly_line`, `test_replay_missing_exec_id_is_a_friendly_line`; all Kitaru*Error ⊆ KitaruError.
- [x] PASS — full run guard chain fires for replay — 4 tripwire tests (`calls == {detect:0, replay:0}`) + real subprocess; refactor preserved (116 CLI tests, 12 run-guard among them).
- [x] PASS — AGENTS.md two E2E rows + "Headless replay & what-if" subsection assert nothing unshipped — every command/API independently mapped to a real surface; cohort framed as example-pattern, per-tool mocks flagged roadmap, `kitaru diff`/`.diff()`/`kitaru_recipes` explicitly stated absent.
- [x] PASS — `import decode.cli` kitaru-free (subprocess: no kitaru in `sys.modules`, both commands registered); `make ci` green 0 warnings; `uv lock --check` passes.

**Evidence**
```
$ make ci
uv run ruff format --check     → 145 files already formatted
uv run ruff check              → All checks passed!
======================= 1072 passed in 120.87s (0:02:00) =======================
uv lock --check                → Resolved 149 packages

$ uv run pytest tests/unit/decode/runtime/test_replay_command.py -q            → 12 passed
$ uv run pytest …::test_model_swap_replay_re_executes_downstream_turns -q      → 1 passed
$ python -c "import decode.cli, sys; assert not any(m=='kitaru' or m.startswith('kitaru.') for m in sys.modules)"  → OK
$ import kitaru_recipes  → ModuleNotFoundError   |  run_agent_task.replay from_: KEYWORD_ONLY, NO DEFAULT
$ kitaru executions --help → cancel get input list logs replay resume retry statistics   (no diff)
```

**Other issues found** (none blocking)
- **ADR-0010 reconciliation (PA action, out of this task's scope).** ADR-0010 §6 still names `kitaru_recipes.cohort/Recipe/experiment` and `forked.diff(control)` as illustrative Kitaru surface (lines 45, 57, 92-93, 133, 135, 165-166); my probe confirms those exact APIs are not in kitaru 0.18. The **glossary is already clean** (Replay/Fork/Baseline Rerun/Checkpoint Override/Model Override name only shipped surfaces). AGENTS.md is correct. AC7 is about AGENTS.md only, so this is not a task-070 blocker — the SWE correctly left ADR-0010 (PA-owned) untouched and flagged it. Hand to PA to align the ADR's illustrative names.
- Minor (cosmetic): the shared `_RUNTIME_DISABLED_MESSAGE` says "to use `decode run`" even when tripped via `decode replay`. Friendly and actionable (RUNTIME_ENABLED enables both); a per-command noun would be nicer polish. Not required by any AC.
- Note: the AGENTS.md cohort `run_cohort(...)` snippet is from an external ZenML examples repo I can't access to verify byte-exactly; it is explicitly framed as an adaptable example ("copy or adapt", "not in the kitaru package"), so it satisfies AC7's "nothing unshipped" bar. PA/PR-reviewer may spot-check against the real examples repo.
- Informational (documented, not a defect): under `checkpoint_strategy="calls"` the terminal `_capture_runtime_output` sink is DAG-independent, so a `decode replay` anchored at an early model call re-executes the swapped model (proven) but stdout still shows the **baseline** answer text served from cache on the local stack. AC2 explicitly accepts the leg-counter proof and AC1 says "possibly changed", so this is within spec; the SWE documents it transparently. PA may want to be aware of the user-visible nuance.

**VERDICT: PASS**
