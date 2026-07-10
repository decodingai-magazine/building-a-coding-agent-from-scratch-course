---
name: kitaru-replay-ops
description: decode's Kitaru operator surface for headless replay and what-if — three-runs (observed / baseline-rerun / fork), CLI replay with --args/--overrides, checkpoint overrides, diffing execution records, cohort scaling, wait re-ask behavior, subagent-as-one-checkpoint. Use when replaying or forking a decode run, overriding checkpoints, or comparing executions with the kitaru CLI/SDK.
---

## Headless replay & what-if (Kitaru operator surface — documented, not wrapped)

`decode replay` wraps only the **bypass model-swap** common case, 1:1 over Kitaru's native flow-object replay (ADR-0010 §5). Full **checkpoint → replay → diff → decide** loop = Kitaru's own CLI/SDK — decode deliberately does **not** re-implement diff, cohort, or checkpoint-override machinery (ADR-0010 §6). Below: that operator surface, verified against installed **kitaru 0.18** + docs.zenml.io ("Replay and Overrides", "Replay and improve"); patterns / roadmap items flagged as such.

**Three runs, not two.** Trustworthy what-if = three runs; the middle is the point:

| Run | What it is | Role |
|---|---|---|
| **Observed** | original recorded run | what actually happened |
| **Baseline Rerun** | `kitaru executions replay <id> --from <cp>`, **no** change | *control* — proves replay reproduces faithfully |
| **Fork** | same `--from`, **one** input changed (e.g. `--args '{"model":…}'`) | your change |

Diff **Fork vs Baseline Rerun**, not vs Observed — the control isolates your one variable. Baseline Rerun ≠ Observed (nondeterministic tool, external state, time)? Diff untrustworthy; pin the nondeterminism first.

**CLI replay with overrides** (surface `decode replay --model` wraps a slice of):

```bash
kitaru executions replay <exec_id> --from <cp>                                   # Baseline Rerun (control)
kitaru executions replay <exec_id> --from <cp> --args '{"model":"gemini-2.5-pro"}'   # Fork (flow-input swap)
kitaru executions replay <exec_id> --from <cp> --overrides '{"checkpoint.<name>":<value>}'  # checkpoint-output swap
```

- `--args` = **flow-input** overrides (CLI mirror of `flow.replay(..., model=…)`; `decode replay --model` surfaces this). The **Model Override** rides here.
- `--overrides checkpoint.<name>` = **Checkpoint Override**: substitute a recorded checkpoint's single output at its **direct consumers**, re-executing from there forward. Keys **must** start with `checkpoint.` (else `KitaruUsageError`); overridden checkpoint must expose a single output.
- **`--overrides checkpoint.X` = the tool-output mock stand-in.** Per-tool-call `output=` / `raise_=` mocks (fake value / forced failure) are **Kitaru roadmap, not shipped** — ZenML guide flags this. Today: override the tool's recorded checkpoint output.

**Diff = compare the two execution records.** **No `kitaru diff` CLI, no `.diff()` SDK method in kitaru 0.18** (verified — do not assume one). Manual comparison; the ZenML guide's own pattern:

```bash
kitaru executions get <fork_exec_id>        # decision, per-checkpoint outputs, cost, latency
kitaru executions get <baseline_rerun_id>   # the control to compare against
```

SDK: `KitaruClient().executions.get(fork.exec_id)` vs `.get(rerun.exec_id)` — compare cost/latency/decision. Baseline reproduced → any difference attributable to your one change. `decode replay` prints the same stderr hint (`kitaru executions get <new> vs <original>`), pointing only at this confirmed surface.

**Cohort: scale the winning change across recent runs** — **example pattern on SDK primitives, NOT a core Kitaru API.** ZenML "Replay and improve" guide ships `run_cohort` (+ `cost` / `latency` / `quality_judge` metric callables) in the **kitaru examples repo** (`examples/end_to_end/pydantic_replay_fork`); *"not in the `kitaru` package — copy or adapt"* (`import kitaru_recipes` is **not** an installed module — verified):

```python
from cohort import run_cohort                 # from the EXAMPLE dir, not `import kitaru`
from utils import cost, latency, quality_judge
# exec_ids: recent runs, e.g. KitaruClient().executions.list(flow="run_agent_task")
report = run_cohort(exec_ids, baseline_model="gemini-2.5-flash",
                    variant_model="gemini-2.5-pro", metrics=[cost, latency, quality_judge])
report.summary()      # per-metric baseline-vs-variant deltas + an is-it-better verdict
report.regressions()  # the metrics / decisions that got worse
```

Per run: reproduce baseline, replay variant, score the pair — decide on a cohort, not one lucky run.

**Waits re-ask on replay.** A replayed run **re-asks** every `wait()` — Kitaru "does not support overriding or pre-populating wait results." Hence `decode replay` is bypass-only + HITL answer-reuse deferred (see replay row above). Honesty note: on a `decode run --hitl` **pause**, Kitaru itself prints `Waiting for input…` to **stdout** (framework behavior) — the pipe-clean guarantee covers the completed **bypass** answer only.

**A subagent run = one opaque checkpoint.** A whole `agent(...)` spawn — the child's entire nested loop — is one opaque tool call → **one** checkpoint under `"calls"`: nested child model calls are **not** replay anchors; a `decode replay --model` swap does **not** reach inside a child (child rides parent's model — `AgentDef` has no model field). Read-only child's cached summary is replay-safe → `agent` never joins the sandbox-bash cache-disable set; child token spend stays folded into that one tool call, invisible until Opik (M10) — ADR-0013 §9.

**An agent can drive the whole loop.** Kitaru exposes this replay surface over an **MCP server** (`kitaru-mcp` console script) — a coding agent (Claude Code, Codex, Cursor) can pull a recent run, propose a change, replay vs control, compare, widen to a cohort — future automation hook (no decode work now).
