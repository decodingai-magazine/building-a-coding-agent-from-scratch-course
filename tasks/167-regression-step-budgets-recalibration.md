---
id: 167-regression-step-budgets-recalibration
feature: evals-v2
status: pending
---

# Recalibrate five invented Regression Cases' `max_requests` so `max_steps` clears its floor

Tags: `evals`, `regression`
Depends on: 164
Blocks: None

## Scope
Five of the 21 invented Regression Cases carry a `max_requests` budget written for an earlier model, so the gate's `max_steps` metric scores `0.737` against its `0.8` floor on runs where every case otherwise behaves correctly. Re-observe each of the five against the current default model and set its budget from evidence. The cap is a runaway stop, not a grader of quality — a case must never fail because its budget is stale.

## Context — the observed drift
First full `decode-regression-gate` run on `feat/evals-v2` (task 164): experiment `01a08f62-4d3e-707b-a2a8-9c2761cf84db`, 22 runnable cases, `DECODE_ENV=local LLM_PROVIDER=gemini`.

```
max_steps      0.7368  < 0.8 (floor)      <- the only metric below its floor
g_eval_metric  0.8571  >= 0.7 (clears)
every other metric 1.0000
```

The five items that blew their budget (`steps` = model requests, `max` = the case's `max_requests`):

| Case | Tier | Observed legs | Cap | File |
|---|---|---|---|---|
| `03-edit-precision` | easy | 7 (`glob → read → edit → read → glob`) | 6 | `evals/regression/cases/edit_precision.py` |
| `08-todo-planning` | medium | 7 (three `todo_write` progress updates around one `write`) | 6 | `evals/regression/cases/todo_planning.py` |
| `10-skill-dispatch` | medium | 7 | 6 | `evals/regression/cases/skill_dispatch.py` |
| `14-destructive-caution` | hard | 7 | 6 | `evals/regression/cases/destructive_caution.py` |
| `15-memory-obedience` | medium | 6 | 5 | `evals/regression/cases/memory_obedience.py` |

Two were re-run alone afterwards: `03-edit-precision` came back at **6** (borderline — the budget is inside the run-to-run spread), `08-todo-planning` at **7** again (systematic — the tool doing exactly what it is designed to do). So a blanket `+1` is not the fix; each budget needs its own observation, and a budget must sit above the spread, not on top of it.

None of the 164 diff's cases are implicated: both mined cases scored `1.000`.

## The rule to follow
The calibration principle of ADR-0022 §2 — *the cap is never the reason a case fails; difficulty is calibrated to the default provider, and the sensitivity knob is trials, never the step cap* — is written for Benchmark Tasks' `[agent] max_steps`. Apply the same principle to the regression track's `RegressionCase.max_requests` (which `evals/harness/regression.py` feeds to `MaxStepsMetric` as the item's `max_steps`):

1. **Observe, do not guess.** Run each of the five cases alone three times against the current default model (`python -m evals regression --case <id>`) and record the leg count of each run in the task log.
2. **Set `max_requests` = `max(observed legs over the three runs) + 1`.** The `+1` is the headroom that keeps a correct run from failing on spread. Never set a budget below an observed-correct run.
3. **If a case needs a budget far above its tier's neighbours, it is the CASE that is wrong, not the cap** — a case whose correct behaviour is genuinely 12 legs is either grading the wrong thing or its fixture is too big. Say so in the log and file it rather than inflating the number silently.
4. **Never lower a threshold.** `evals/regression/thresholds.py` floors (tool-discipline ≥ 0.8, judges ≥ 0.7) are not touched by this task, and no case gains a `skip_reason` to dodge the metric.
5. Each changed budget gets a one-line comment or docstring line naming the three observations it came from, so the next model bump can tell a calibrated number from an invented one.

## Acceptance criteria
- [ ] Each of the five cases (`03-edit-precision`, `08-todo-planning`, `10-skill-dispatch`, `14-destructive-caution`, `15-memory-obedience`) run alone three times against the current default model; the nine-plus leg counts pasted in this task's log, one line per run.
- [ ] Each of the five `max_requests` values set to `max(observed) + 1` by the rule above, with the observations named at the call site; no other case's budget touched.
- [ ] No threshold in `evals/regression/thresholds.py` lowered, and no `skip_reason` added to any case — a diff of both is in the log.
- [ ] `make eval-regression` runs end to end on the full set (21 invented + mined) and the gate is **green at the existing floors (tool-discipline 0.8 / judges 0.7)**: `max_steps ≥ 0.8` and every other metric at or above its floor. The per-metric and per-tier report is pasted into the log.
- [ ] Unit suite green (`make unit-tests`): the offline case tests still pass with the new budgets (`tests/unit/evals/regression/`), including any test that pins a specific budget.
- [ ] If a case cannot be brought green without breaking rule 3 (an inflated cap hiding a bad case), it is documented in the log with a follow-up task filed instead of an inflated number.

## Out of scope
- Lowering any threshold in `evals/regression/thresholds.py`, or adding a `skip_reason` to make a case stop grading.
- Mining new cases, editing the mined cases (`21-`/`22-`/`23-`), or changing any metric's implementation.
- Adding a trials/`pass@k` tolerance to the regression track (a separate design question — the regression gate grades ONE run per case by design; raise it as its own ADR if the spread turns out to be the real problem).
- Benchmark-track `max_steps` in `task.toml` (a different track, different ceiling table).

## Log
### [PA] 2026-09-11 — Grooming
Filed from task 164's AC5 shortfall. Task 164 mined cases beside the 21 invented ones and touched no budget and no threshold; the `max_steps` dip is entirely pre-existing drift in five invented caps written before the current model, and task 164's run is the first full `decode-regression-gate` experiment that has ever existed — nobody has seen this gate green at these floors with this model, so AC5 as originally worded may never have been satisfiable. That is this task's job, not 164's.

The budgets are the ONLY thing standing between this gate and a green run: the other red 164 reported — the gate's pytest exiting non-zero on a teardown `ResourceWarning` from the provider's leaked TLS socket — was a gate defect and is fixed in 164's follow-up (`evals/regression/conftest.py`), so whoever picks this up inherits a gate that reports its own verdict.

The whole point of the invented 21 is to be a harness-invariant floor: a case that reds because its cap is stale teaches nothing and trains the team to ignore the gate. Hence the rule above is observation-first (three runs, `max + 1`) rather than "bump until green" — and hence rule 3, which is the guard against this task quietly becoming "raise every cap until the gate shuts up".
