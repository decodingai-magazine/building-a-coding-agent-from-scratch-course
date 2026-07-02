---
id: 070-runtime-replay-command
feature: runtime-replay
status: pending
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

- [ ] `decode replay <exec_id> --model <id>` replays the recorded bypass run and prints the (possibly
      changed) answer on stdout; the new `exec_id` + diff hint print on stderr. Driven end-to-end by
      the offline integration test.
- [ ] The offline test proves the model swap **re-executes downstream turns with the new model** (the
      swapped scripted agent's leg-counter moves / its text is returned), not served from cache — the
      inverse of the terminal-anchor cache proof.
- [ ] `--from <cp>` anchors a partial replay (upstream cached, downstream re-executed); omitting
      `--from` mirrors Kitaru's own behaviour exactly (no decode-invented default). The SWE log
      records the verified `from_` semantics on the installed kitaru.
- [ ] Replaying a **HITL** exec_id exits non-zero with one friendly line naming the bypass-only limit
      and `kitaru executions replay` — no traceback (unit test with a HITL-recorded exec or a mocked
      detector).
- [ ] Ambiguous/invalid `--from` (`KitaruStateError`) and a diverged swap (`KitaruDivergenceError`)
      each surface one friendly stderr line, non-zero exit, no raw traceback (unit tests).
- [ ] The full `run` guard chain fires for `replay` too (disabled runtime / missing key / proxy /
      secret-store) — friendly line, no replay attempted (tripwire tests).
- [ ] `AGENTS.md` gains the two E2E rows (`decode run --model`, `decode replay`) **and** the "Headless
      replay & what-if (Kitaru operator surface)" subsection (checkpoint→replay→diff→decide, the
      "three runs" rule, `kitaru executions replay --args/--overrides`, `--overrides checkpoint.X` as
      the tool-mock stand-in with per-tool mocks flagged as roadmap, `forked.diff(control)`,
      `kitaru_recipes` framed as example-pattern-not-core-API, the wait-re-ask limitation linked to
      `tasks/future/`). A docs check confirms every claim matches the real surface and asserts nothing
      unshipped; no unconfirmed `kitaru diff` CLI is presented as fact.
- [ ] `import decode.cli` still does not import `kitaru` (lazy-import subprocess test); `make ci`
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
