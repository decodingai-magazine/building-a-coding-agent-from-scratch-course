# Custom evaluator authoring

Use this reference only after inspecting the installed evaluator catalog. Continue when no installed evaluator expresses one accepted observable behavior. If the user directly requests custom authoring despite a relevant installed match, show that match first and continue only after they explicitly choose the custom route. Require equivalent reviewed evidence either way. Keep the first result small and useful. Add stronger validation only when the intended decision needs it.

## Preserve the accepted meaning

Start from the accepted behavior and exact cohort version. Separate the criterion into the parts that matter:

- **Outcome:** the independently observable terminal state that establishes success.
- **Protections:** side effects or states that must not occur.
- **Interaction:** required clarification, approval, communication, or handoff.
- **Trajectory diagnostics:** tool choice, arguments, retries, loops, latency, and cost.

Prefer outcome evidence over a preferred tool sequence. Require an exact order only when order is itself part of an approval, security, transactional, or other explicit contract. Otherwise allow materially equivalent successful paths.

Reject or revise the criterion when the agent could pass without exercising the capability, required information is absent, success is ambiguous, or the evidence measures only a dependency failure. If the underlying issue is an obvious prompt ambiguity, missing capability, ordinary bug, or dependency failure, recommend the direct fix and add an evaluator only when a regression check is useful.

## Choose the simplest mechanism

Use deterministic executable code for objective structure, content, limits, models, tools, or workflow rules. Use an LLM judge only when the accepted criterion genuinely requires interpretation. A small hybrid may use deterministic checks for hard protections and a judge for the interpretive outcome.

Do not explain this taxonomy unless it helps the user decide. State the selected mechanism, the evidence it reads, and what it can establish.

## Show one rubric checksum

Draft one narrow criterion and show one prefilled card:

| Field | Confirmed value |
|---|---|
| Accepted behavior | Exact accepted text and intended use |
| Outcome | Independently observable success |
| Protections | Must-not-happen states, or none |
| Required interaction | Exact requirement, or none |
| Pass | Exact boundary |
| Fail | Exact boundary |
| Equivalent behavior | Valid alternatives |
| Evidence | Required session, node, or external outcome evidence |
| Missing evidence | Unresolved or infrastructure behavior |
| Mechanism | Deterministic, LLM judge, or small hybrid |
| Examples | Representative reviewed session and node references |
| Cohort | Exact cohort-version ID |

Treat this card as a checksum, not a new form. If it preserves the behavior already accepted in investigation, continue without asking the user to accept the same meaning twice. Ask for a decision only when the draft adds or changes a boundary, permitted equivalence, or missing-evidence rule.

Do not require a large labeled dataset before the first result. A deterministic executable rule with explicit meaning can proceed without reviewed Pass and Fail examples. For an interpretive evaluator, use enough reviewed evidence to make the boundary concrete. Pass, Fail, and boundary examples become important before consequential automation.

## Implement the Kitaru contract

Return Kitaru's installed `EvaluationResult` model or a nonempty list of uniquely named results. A binary evaluator needs a stable name and a boolean score or string value, and may also set `passed` and a trace-grounded explanation.

```python
from kitaru.task.evaluator import EvaluationResult

return EvaluationResult(
    name="accepted_behavior",
    score=passed,
    passed=passed,
    explanation="Short trace-grounded explanation.",
)
```

If a judge emits an internal object such as `{reason, verdict}`, validate it and translate it into `EvaluationResult`. Do not return the judge object directly.

Missing evidence, invalid judge output, timeouts, credential errors, and evaluator crashes remain unresolved or infrastructure outcomes. They are not automatic agent failures.

## Run behavioral checks

For every custom evaluator:

1. Invoke it against representative `SessionView` fixtures and assert exact `EvaluationResult` values.
2. Confirm that missing evidence does not silently become a Fail.
3. Confirm that a valid alternate trajectory can pass when exact order is not contractual.
4. Run `kitaru evaluator test` separately as a load-and-signature check. It uses a placeholder object and is not behavioral validation. It executes local code in a bounded child process, not a security sandbox, so review the code and use a credential-free isolated environment.

For an LLM judge, add cheap probes where relevant: reverse pairwise order, remove irrelevant context, present a valid alternate path, pair the expected token or tool call with an incorrect outcome, and verify that missing evidence stays unresolved. These are implementation checks, not a separate product stage.

## Register and report facts

After the checks pass and the user approves the remote write:

1. Register a new immutable evaluator version. Use the CLI for local script upload; use MCP only when the implementation already exists as a server blob or exact package pin.
2. Re-read the evaluator and registered version.
3. Return exact evaluator, evaluator-version, and cohort-version IDs plus exact parameters and configuration hashes when available.
4. Report the evidence as facts:

```text
Implementation checks: passed
Kitaru load/signature check: passed
Reviewed fixtures: 3, including Pass and Fail
Human agreement: not measured
Held-out evaluation: not run
Judge model and settings: exact identity, or not applicable
Freshness limitations: none observed, or exact change
Supports: exploratory or known-case regression use
Does not support: automated release gating
```

Do not invent product states such as `behavior-tested`, `test-validated`, or `revalidation-due`. Validation evidence and freshness are separate factual dimensions, and Kitaru does not persist those labels.

## Add rigor only for consequential use

When a subjective evaluator will support consequential automation or a user-defined release gate:

- collect independent labels only when authority is genuinely shared;
- keep related trace families together across examples, development labels, and held-out labels;
- freeze the prompt, parser, observation window, and judge model before the held-out run;
- report pass recall, failure recall, raw counts, false passes, false fails, disagreements, and infrastructure errors;
- state whether label isolation was actually enforced.

Do not use Cohen's kappa as a readiness threshold. Do not require multiple annotators when one trusted domain expert defines correctness. Do not apply population corrections or confidence intervals to a deliberately curated regression cohort. If label isolation is unavailable, state that limitation instead of awarding a validation label.

Finish at the exact evaluator-version checkpoint. Do not deploy the evaluator or change the agent automatically. If the user wants to test one candidate, hand the exact cohort, evaluator version, parameters, evidence facts, and limitations to `kitaru-replay-experiment`.
