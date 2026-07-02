# 0010. Kitaru-powered replay for the headless `decode run` — reuse native replay, build only the enablers

**Status:** Accepted
**Date:** 2026-07-02

## Context

ADR-0008 gave decode a **Headless Runtime**: a Kitaru `@flow` (`runtime/flow.py`) that runs the same
`build_agent()` autonomously, recording durable **Checkpoints** and getting **Replay**/**Wait** in
place of the interactive loop. That ADR framed Replay narrowly — crash-resume (finished checkpoints
served from cache) and HITL answer-replay. Kitaru's replay is far more capable: `flow.replay(exec_id,
from_=<cp>, <flow-input-overrides>, overrides={…})` re-executes a recorded run from any checkpoint
with one thing changed, so you can ask *what would have happened* if you had shipped a different model
or prompt — and then diff the fork against a faithful control. This is Kitaru's headline capability
("this is the part that other tooling can't do"), proven to work **offline on the local stack** in
`tests/integration/test_runtime_capstone.py` (`run_agent_task_hitl.replay(exec_id, from_=…)` serves
upstream model checkpoints from cache while the terminal step re-runs).

The user wants that what-if loop for `decode run`. The design principle, decided with the user, is
blunt: **fully leverage Kitaru's native replay — decode builds only the minimum enablers and reinvents
nothing.** No decode-side diff engine, no cohort runner, no re-implemented override machinery.

Facts confirmed against `docs.zenml.io` (kitaru 0.18 line) + the installed SDK while scoping:

- **Flow-object replay forwards flow args as kwargs:** `flow.replay(exec_id, from_="cp", model="…",
  overrides={"checkpoint.x": …})`. So a model override must be a **flow parameter** for Kitaru to swap
  it on replay. `client.executions.replay(...)` and `kitaru executions replay <id> --from <cp> --args
  '{…}' --overrides '{…}'` are the SDK/CLI mirrors (`--args` = flow inputs; `--overrides checkpoint.X`
  = checkpoint-output substitution).
- **Overrides only bite downstream of `from_`.** Replay serves everything upstream of the anchor from
  cache and re-executes the anchor + descendants for real (capstone: replaying from the terminal
  checkpoint leaves the model leg-counter unmoved). So a **Model Override** takes effect only on turns
  re-executed downstream of `--from`; to swap the model for a whole run, anchor at/before the first
  model call.
- **Per-turn checkpoints are too coarse to anchor a replay before a model call.** Replay-readiness
  requires `checkpoint_strategy="calls"`. HITL already forces `"calls"`; the bypass run defaults to
  `"turn"` (`settings.runtime_checkpoint_strategy`).
- **`"calls"` can break the bypass flow's `.wait()` return extraction** the same way it did for HITL
  (`_MultipleTerminalStepsOutputError` — ADR-0008 §3 amendment 5). The HITL flow's fix is a terminal
  `@checkpoint _capture_runtime_output` + a named-artifact reader.
- **Waits cannot be pre-populated on replay** ("must be resolved through the normal wait input flow").
  A replayed HITL run re-asks every approval/question on the local stack (capstone
  `test_replay_re_asks_a_wait_on_the_local_stack`). True answer-reuse needs a deployed stack.
- **Per-tool-call `output=`/`raise_=` mocks are Kitaru roadmap, not shipped.** `--overrides
  checkpoint.X` is the current stand-in. **Cohort/recipes** (`kitaru_recipes.cohort/Recipe/experiment`)
  are an *example pattern on the SDK primitives*, not a core API.

Project constraints honored: infrastructure is imported/called directly (no wrapper); the interactive
REPL and its JSONL log are untouched (headless-only); `runtime/` stays the isolated module. This ADR
**extends** ADR-0008 (it does not supersede it) and is groomed into tasks **067-070** (feature
`runtime-replay`).

## Decision

1. **Reuse Kitaru's native replay; decode builds only enablers.** `decode replay` is a thin wrapper
   over `run_agent_task.replay(...)` whose anchor semantics mirror Kitaru 1:1. Diff, checkpoint-overrides,
   and cohort/recipe experiments stay on the Kitaru operator surface (CLI + SDK + `kitaru_recipes`);
   decode does not reimplement them (it documents them — §6). This keeps the feature small and rides
   Kitaru's roadmap for free.

2. **Model as a replayable flow parameter (task 067).** Thread `model: str | None = None` through
   `_build_model → build_agent → _build_runtime_agent/_build_hitl_runtime_agent → run_agent_task/
   run_agent_task_hitl`. `None` reads `settings.<provider>_model` (byte-unchanged); a value overrides
   **only** the active provider's model id. The provider stays selected by `LLM_PROVIDER` — **no
   cross-provider swap** (permanent non-goal). Being a flow input is exactly what lets Kitaru swap it
   via `flow.replay(..., model=…)`.

3. **Record every `decode run` under `"calls"` (task 068).** Flip
   `settings.runtime_checkpoint_strategy` default `"turn" → "calls"` so every bypass run is
   replay-ready (HITL already forces `"calls"`). Because `"calls"` can break the bypass `.wait()`
   extraction, the task **verifies the bypass path first** and, if it breaks, applies the shipped HITL
   fix (terminal `@checkpoint _capture_runtime_output` + `_load_runtime_output` reader, and the CLI
   reads the artifact instead of `.wait().output`). `"turn"` stays selectable for a coarse run.

4. **`decode run --model X` + surface the exec_id (task 069).** Expose the §2 parameter as a flag, and
   stop discarding the `FlowHandle` (today `cli.py:364`): print the answer on stdout, then `exec_id:
   <id>` + a paste-ready `decode replay <id> --model …` hint on stderr, so the checkpoint→replay loop
   is discoverable and stdout stays pipe-clean.

5. **`decode replay <exec_id> [--from <cp>] [--model Y]` — thin, bypass-only, 1:1 Kitaru (task 070).**
   Wraps `run_agent_task.replay(exec_id, from_=…, model=…)`. `--from` maps straight to Kitaru's
   `from_` and mirrors Kitaru's behaviour exactly — decode invents no default anchor; if kitaru
   requires `from_`, `decode replay` surfaces kitaru's own requirement as a friendly line. `--model`
   default = replay as-is. Prints the new exec_id + a diff hint pointing at the confirmed Kitaru
   surface. **Bypass-only:** a HITL exec_id fails friendly (HITL replay re-asks every wait — §7) and
   points at `kitaru executions replay`. Ships with an OFFLINE integration test proving a model swap
   re-executes downstream turns with the new model.

6. **The Kitaru operator surface stays on Kitaru; decode documents it (task 070).** AGENTS.md gets the
   checkpoint→replay→diff→decide playbook: the "three runs, not two" rule (Observed → **Baseline
   Rerun** control → **Fork**), `kitaru executions replay --args/--overrides`, `--overrides
   checkpoint.X` as the tool-output mock stand-in (per-tool mocks are roadmap), `forked.diff(control)`,
   and `kitaru_recipes` cohort experiments framed as an *example pattern, not a core API*.

7. **HITL replay with answer-reuse is deferred.** On the local in-process stack a replayed HITL run
   re-asks every approval/`ask_user` (Kitaru cannot pre-populate wait results; capstone-proven). Real
   answer-reuse needs a deployed stack, so it is parked in `tasks/future/hitl-replay-answer-reuse.md`
   and picked up at the deploy milestone. This is why §5 is bypass-only.

**Non-goals (NG, permanent):** decode-side `diff`/`cohort` commands; cross-provider model swap on
replay; per-tool-call `output=`/`raise_=` mocks (use `--overrides checkpoint.X`); any change to the
live REPL path (headless-only).

## Diagram

```mermaid
flowchart TB
    subgraph obs["OBSERVED — the recorded run (task 069)"]
        run["decode run --model A 'task'"]
        flowA["@flow run_agent_task(task, model=A)"]
        cps[("Checkpoints (calls) — task 068<br/>cp1 · cp2 · … · cpN<br/>durable in the Kitaru store")]
        outA["answer + exec_id: kr-A (stderr hint)"]
        run --> flowA --> cps --> outA
    end

    subgraph enabler["ENABLER (task 067)"]
        param["model: str | None threaded<br/>_build_model → build_agent → seams → flows<br/>overrides only the active provider's model id"]
    end
    param -. makes model a swappable flow input .-> flowA

    subgraph replay["REPLAY — what-if (task 070)"]
        rcmd["decode replay kr-A --from cpK --model B"]
        cache[("cp1 … cp(K-1)<br/>served from CACHE (model A)")]
        reexec["cpK … cpN RE-EXECUTE for real<br/>with Model Override B"]
        fork["FORK: new exec kr-B<br/>original_exec_id → kr-A"]
        rcmd --> cache
        rcmd --> reexec --> fork
    end
    cps -. flow.replay(kr-A, from_=cpK, model=B) .-> rcmd

    subgraph ops["KITARU OPERATOR SURFACE — documented, not wrapped (task 070)"]
        baseline["Baseline Rerun (control)<br/>kitaru executions replay kr-A --from cpK"]
        diff["forked.diff(baseline) → decision · cost · latency Δ"]
        over["--overrides 'checkpoint.X': … (tool-output stand-in)"]
        cohort["kitaru_recipes: cohort(flow, last=N) + Recipe + experiment<br/>(example pattern, not core API)"]
        fork --> diff
        baseline --> diff
        diff --> decide{{"ship the change? keep / reject"}}
        cohort --> decide
    end
    reexec -. tool-output mock .-> over

    subgraph deferred["DEFERRED (tasks/future/)"]
        hitl["HITL replay with answer-reuse<br/>needs a DEPLOYED stack — waits re-ask locally"]
    end
    fork -. bypass-only; HITL exec_id → friendly error .-> hitl

    classDef observed fill:#1565c0,stroke:#0d47a1,color:#ffffff;
    classDef store fill:#37474f,stroke:#102027,color:#ffffff;
    classDef enable fill:#00838f,stroke:#005662,color:#ffffff;
    classDef rep fill:#2e7d32,stroke:#1b5e20,color:#ffffff;
    classDef opsc fill:#e65100,stroke:#bf360c,color:#ffffff;
    classDef defer fill:#6a1b9a,stroke:#38006b,color:#ffffff;
    class run,flowA,outA observed;
    class cps,cache store;
    class param enable;
    class rcmd,reexec,fork rep;
    class baseline,diff,over,cohort,decide opsc;
    class hitl defer;
```

## Consequences

- **Small feature, big capability.** Four tiny enablers (a flow param, a settings default flip, a
  flag, a thin wrapper) unlock Kitaru's full replay/what-if/cohort loop. Everything hard (diff,
  overrides, cohort) is Kitaru's and stays Kitaru's.
- **Every `decode run` costs more checkpoints.** `"calls"` records per model/tool call, not per turn —
  more store writes per run, the price of replay-readiness. `RUNTIME_CHECKPOINT_STRATEGY=turn` remains
  the coarse opt-out.
- **A model swap can diverge the recorded call sequence.** A different model may tool-call differently
  downstream of `--from`; Kitaru may raise `KitaruDivergenceError`. `decode replay` surfaces it as a
  friendly line (that *is* the honest what-if outcome — the change diverged the run), never a
  traceback. `KitaruStateError` (ambiguous `--from`) is handled the same way.
- **The `.wait()`-under-`"calls"` repair is verify-first, not assumed.** Task 068 measures the real
  bypass behaviour before choosing between keeping `.wait().output` and adopting the HITL output
  artifact — the fix pattern already exists, so the risk is bounded.
- **Bypass-only is a deliberate, documented limit.** HITL replay re-asks every wait on the local
  stack (Kitaru cannot pre-populate wait results); answer-reuse is deferred to the deploy milestone
  (`tasks/future/`). `decode replay` refuses a HITL exec_id with guidance rather than silently
  re-prompting.
- **CI stays offline.** The new replay proof rides the capstone's `isolated_kitaru_store` + scripted
  `FunctionModel` seam — no server, no network, no key — mirroring every prior runtime test.
- **The REPL stays kitaru-free.** `decode replay` imports `runtime/` lazily, exactly like `decode
  run`; `import decode.cli` still never imports kitaru.
- **Extends, does not supersede, ADR-0008.** ADR-0008's Replay/Checkpoint/Wait decisions stand; this
  ADR only broadens Replay to the what-if use case and adds the four enablers. The glossary's `Replay`
  row is extended and gains `Model Override`, `Fork`, `Baseline Rerun`, `Checkpoint Override`.
