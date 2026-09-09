# Bounded comparison method

Use the smallest comparison that can answer the user's actual question. A curated cohort supports a claim about those known cases, not production prevalence.

## Choose the comparison

### Historical observed vs candidate

Use this default for exploration and ordinary known-case regression. Pair each recorded case with a fresh candidate task started from the same stored top-level inputs. This asks whether the candidate now handles that known case. It is not restoration of the historical execution and not a controlled fresh baseline.

### Fresh control vs candidate

Use when stronger attribution or a user-defined gate needs comparable current conditions. Kitaru has no first-class fresh-control abstraction, so this may require a separate bounded run. Record the control agent version, internal hashes of the public run spec and capabilities, overrides, tool policy, evaluator versions and parameters, experiment and run IDs, timing, and every known difference from the candidate. Downgrade attribution when material conditions cannot be matched. Do not present those hashes as a named product concept.

### Historical observed vs fresh control

Use only when reproduction or environment drift is itself the question.

A fresh control is not required for a first exploratory result. If a consequential claim needs one and it is unavailable, narrow the claim instead of adding statistical ceremony.

## Use one outcome and few protections

Select one evaluator as the intended outcome. Add at most one or two must-not-happen protections when side effects, cost, or policy compliance matter. Do not make the user configure a metric hierarchy.

For each paired case, classify the candidate outcome against the comparison as improved, regressed, or unchanged. Keep execution failures and missing evaluations separate. Show concrete cases when outcome and protection metrics conflict, then label the overall result `trade-off`.

Use `inconclusive` when missingness, evaluator weakness, state drift, or mixed evidence prevents the intended claim. Do not convert missing results into negative scores.

## Match rigor to the claim

| Intended use | Minimum useful evidence |
|---|---|
| Exploration | Relevant exact evaluators, a bounded cohort, case evidence, and explicit limits |
| Known-case regression | Exact cohort and evaluator versions, paired cases, parameters, and honest missingness |
| User-defined gate | Trusted rubric, required protections, leakage checks, and a comparable control when attribution matters |
| Production prevalence | Out of scope without representative sampling, validated measurement, error rates, and uncertainty |

Evaluate a gate only when the user supplied its exact condition and the evaluator and runtime-identity evidence are fit for it. Return `meets`, `does not meet`, or `cannot evaluate` the stated gate. When runtime identity is not immutable, report observed violations but use `cannot evaluate` rather than attributing a gate result to the approved candidate configuration. Never turn that into a deployment instruction.

## Check leakage

Inspect the candidate prompt, evaluator prompt, few-shot examples, static tool results, recorded history scope, and related trace families. A candidate that includes the expected answer or a judge that saw the target labels does not establish general improvement.

For exploration, warn and narrow the interpretation. For consequential use, resolve the leakage or decline the claim. If the supported Kitaru surface does not expose prompt content, use repository or user-supplied configuration when available and name the uncertainty.

## Repeat only when it can change the decision

Run once by default. Use predeclared matched repetitions only when model stochasticity could plausibly reverse a borderline or consequential conclusion. Hold cohort, evaluators, parameters, tool policy, and other conditions fixed. Approve the extra work and cost before starting.

Report each repetition and whether the direction was stable. Do not add a significance test to a small curated cohort merely because several runs exist.

## Interpret without overclaiming

- `improved`: gains on the intended outcome with no material protection regression on the available cases.
- `regressed`: losses on the intended outcome or a new or worsened material protection failure relative to the comparison. A protection that fails in both members of a pair is directionally unchanged and must be reported separately; it may still mean an exact user-defined gate is not met.
- `trade-off`: outcome and protection changes conflict, or meaningful case-level gains and losses require a product choice.
- `inconclusive`: the available evidence cannot support a direction.

Always include raw improved, regressed, unchanged, failed, canceled, and missing counts, exact provenance, comparison type, state limitations, and one useful next action. Do not estimate population prevalence from an adaptive or curated cohort.
