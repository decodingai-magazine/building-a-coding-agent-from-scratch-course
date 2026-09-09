# Installed evaluator selection

Use the installed Kitaru evaluator catalog as the authority. Re-read it before selection because available evaluators and versions can change.

## Start with descriptive evidence

First list the installed evaluators and exact versions through the current registry operation. Do not try a name from this reference until the server returns it. Then run only the descriptive evaluators relevant to the accepted behavior on a small selected set. Current default installations may expose version-1 bundles such as:

| Evaluator | Useful evidence |
|---|---|
| `kitaru/session-diagnostics@1` | Session and payload completeness signals |
| `kitaru/trajectory-signals@1` | Loops, retries, and trajectory shape |
| `kitaru/tool-health@1` | Tool calls, errors, and tool-use signals |
| `kitaru/timing-profile@1` | Timing and latency evidence |
| `kitaru/llm-call-signals@1` | Model-call and token-related signals |

These results describe recorded behavior. A retry, slow span, tool error, or missing payload is not automatically an agent-quality failure.

## Prefer an installed configured rule

Check whether an evaluator returned by the installed catalog directly expresses the accepted criterion. Current default installations may expose configured rules such as:

| Evaluator | Use when the accepted criterion concerns |
|---|---|
| `kitaru/output-contract@1` | Required or forbidden output shape or content |
| `kitaru/resource-budget@1` | Cost, token, call, or latency limits |
| `kitaru/tool-policy@1` | Required, allowed, or forbidden tool use |
| `kitaru/model-policy@1` | Required or forbidden model use |
| `kitaru/workflow-conformance@1` | Contractual workflow steps or ordering |

Pin the exact evaluator version and parameters. Preserve the deterministic input and configuration hashes when the evaluator exposes them. Reuse relevant cost, timing, resource, model, and tool-policy results as replay protections instead of recreating them.

Do not force a configured evaluator to stand in for an interpretive outcome. Exact tool order is appropriate when the order is contractual, not merely because one successful trace used it.

## Return a compact checkpoint

For an installed match, run it and return:

- accepted behavior and exact cohort-version ID;
- exact evaluator and evaluator-version IDs;
- exact parameters and configuration hash when available;
- descriptive evidence used to choose it;
- missing evidence and infrastructure failures;
- intended use and claim limit.

Do not require custom authoring or reviewed Pass and Fail examples when an explicit deterministic rule already expresses the accepted meaning. Continue to custom authoring only when no installed evaluator fits.
