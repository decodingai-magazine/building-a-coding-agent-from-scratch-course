---
name: kitaru-replay-experiment
description: Run and interpret one safe, bounded Kitaru replay comparison against an accepted cohort and exact evaluators. Use when a user wants to replay a cohort, test or compare a model, prompt, system prompt, parameter, agent version, or tool policy, supervise an experiment run, determine whether one candidate helped, or ask for one bounded change worth testing.
---

# Kitaru replay experiment

Test one candidate condition against known cases and explain whether the available evidence improved, regressed, traded off, or stayed inconclusive. Do not make the deployment decision.

## Core contract

- Start from an accepted behavior, exact cohort version, exact evaluator versions and parameters, and one candidate change. Suggest one bounded candidate only when asked.
- Replay starts a fresh agent task from each historical session's stored top-level inputs after applying the override. It does not restore an arbitrary checkpoint, conversation, process memory, adapter instance state, filesystem, or external world state.
- Resolve adapter support and its construction path before asking to run the experiment. A shared replay schema does not prove that an adapter supports a requested override or tool source.
- Require an explicit tool policy for every tool-using run. Omission resolves to live passthrough on the current server and is unsafe as an implicit default.
- Carry exact IDs, versions, evaluator parameters, run-spec evidence, tool policy, failures, and missing results forward.
- Explain remote writes, model and worker compute, cost uncertainty, and possible live effects before execution. One approval after this explanation covers experiment creation and the run start; any tool path with external effects needs separate approval.
- Use established Kitaru product terms only. Do not coin labels for summaries or steps, such as “run card,” “result card,” “agent fingerprint,” or “execution checksum.” Do not replace a Kitaru object with a friendly-sounding alias such as “accepted baseline”; explain the official term when necessary, then use it consistently. In user-facing text, describe what will happen and what the user must decide in ordinary language.
- Prefer native Kitaru MCP operations. Use the structured CLI for built-in waiting or another capability MCP does not expose. Verify installed schemas when they differ from the references.
- Run every Kitaru CLI command and SDK script with `KITARU_ACTIVE_SKILL=kitaru-replay-experiment` set so the server attributes the resulting activity to this skill.
- Start or restart a user-controlled worker with `--concurrency 10`. Use `KITARU_WORKER_CONCURRENCY=10` only when the launch surface exposes worker settings through environment variables instead of CLI options.
- Never bypass a missing adapter, evidence, comparison, or product contract with direct REST calls or ad hoc local state.

## Load references only when needed

- Read [references/experiment-contract.md](references/experiment-contract.md) before any Kitaru operation or adapter capability claim.
- Read [references/experiment-method.md](references/experiment-method.md) before choosing the comparison type, interpreting conflicting results, adding repeats, or evaluating a user-defined gate.

## Resolve the starting state

Begin read-only. Reuse exact identifiers from an investigation checkpoint or the user's request, then re-read each object.

Resolve:

1. the accepted behavior and intended use: exploration, known-case regression, or an exact user-defined gate;
2. the exact cohort-version ID and ordered case count;
3. one exact candidate agent-version ID and the current public run spec and capabilities;
4. exact evaluator-version IDs, parameters, and factual evidence about their checks and limitations;
5. one proposed replay override and its expected effect;
6. the adapter and construction path;
7. every tool the candidate may invoke and one explicit policy covering all of them.

Route to `kitaru-investigation` when no behavior or cohort has been accepted. Use its evaluator route when no suitable evaluator exists or when the current evaluator is too weak for the intended claim. Offer a claim downgrade instead of forcing validation ceremony when the user only needs an exploratory result.

If an experiment or run already exists, re-read it and resume supervision or interpretation. Experiment configuration freezes after it has runs, so create a new experiment for a materially changed candidate, evaluator set, override, or tool policy.

## Preflight capability and safety

Reject unsupported adapter conditions before approval or paid execution. Return the exact blocker and smallest supported alternative. Do not let a user approve past an unresolved adapter or construction path.

Resolve the model provider and credentials required by the candidate process. Verify credential availability to the worker without reading, displaying, or fingerprinting secret values when the environment exposes a safe readiness signal. A credential visible to the coding agent or stored in a project file does not prove that an already-running worker inherited it. If worker readiness is unavailable or cannot be verified, ask the user to configure the credential and restart or provision the worker, then stop before experiment creation or run start.

For tool-using agents:

- Choose the tool source that answers the hypothesis. Do not default to recorded history merely because a baseline is available.
- Prefer explicit passthrough for verified deterministic, isolated local fixtures when the candidate may change tool choice or arguments. State that this still executes tool code, name its effects, and distinguish it from an external live system.
- Use baseline-scoped recorded history when holding recorded tool results fixed is part of the intended comparison and the adapter supports it. Explain that lookup matches a prior call; it does not restore baseline state or permit new arguments.
- Use `fail` or `error_result` on a history or static miss unless live execution is deliberate.
- Treat `on_miss=passthrough` as live passthrough and gate it separately.
- Warn that cohort-wide or agent-wide history can borrow stale or cross-case state. Matching uses tool name and canonical JSON arguments. Baseline scope replays repeated identical calls in recorded order, wider scopes select the latest match in scope.
- Treat LLM-generated results as a synthetic condition, not restoration of the historical external world.
- Keep tool execution failures separate from agent-quality failures.

Check whether repeated calls with identical arguments can legitimately return different values. Baseline scope replays each such call with its own recorded result in order, wider scopes answer every one with the latest match. Baseline scope is the narrowest history source, not proof that a stored result remains faithful.

When passthrough and history are both valid but would change what the experiment means, explain the concrete difference and ask the user which condition they want unless a verified project-specific contract already selects one.

## Confirm the run with the user

Before asking for approval, give the user a short summary in ordinary language. Prefer a few bullets over a table. Include only:

- the change being tested and the question it should answer;
- the cohort and evaluator versions, with the number of cases;
- what each new task starts from and the important state it does not restore;
- how tools will behave, including any possible external effects;
- the expected model and worker work, with cost uncertainty; and
- what the comparison can and cannot establish.

Include exact agent, experiment, or adapter details only when the user needs them to verify the decision or when they expose a material limitation. Keep complete IDs, versions, parameters, configuration hashes, and adapter evidence in the resumable technical record rather than making the user read them all before a routine exploratory run.

Hash the complete public run-spec and capabilities models internally using canonical serialization. Do not resolve or expose secrets. Immediately before experiment creation and again before starting execution, re-read both models and compare the hashes. If either changed, invalidate approval and explain which configuration changed without naming the hash as a product object. An exact agent-version ID identifies a mutable configuration, not an immutable reproducible artifact, and these checks cannot freeze or prove the configuration a worker later resolves. For a user-defined gate or live-effect run, require immutable runtime identity or narrow the use to exploration. Without that identity, report observed violations but return `cannot evaluate` for the gate. For an exploratory run, preserve the approved hashes, re-read the models after settlement, and return `inconclusive` if observable drift occurred.

Ask for one approval after the summary is visible. If passthrough can affect an external system, ask separately for approval of the named live tools and effects. Verified isolated local mock execution can be covered by the first approval.

## Create and supervise one run

After approval:

1. Create one experiment with the exact agent parent, replay override, explicit tool policy, and evaluator versions and parameters.
2. Re-read it and verify that the server resolved the intended configuration.
3. Recheck the candidate run-spec and capabilities hashes internally.
4. Start one run against the exact cohort version and candidate agent version. Set `evaluate_baselines=true` for comparative claims.
5. Watch the asynchronous run until it completes, fails, is canceled, reaches an agreed stop condition, or the local wait times out.
6. Read the run and all paginated replay jobs needed to account for completed, failed, canceled, and missing cases.
7. Re-read the candidate run spec and capabilities after settlement. Report any observed drift and the residual risk that a transient mutation could not be detected.

Run once by default. Add predeclared matched repetitions only when stochasticity could reverse a consequential or borderline conclusion, and approve the extra work and cost first.

Cancel only when the user asks or an approved stop condition is met. Cancellation preserves evidence. Deletion is destructive and is not cancellation.

## Read exact evidence

For each case, resolve evaluation results tied to the expected evaluator-version ID. Verify evaluator parameters or deterministic configuration hashes where available. Decline a decision-grade parameterized baseline comparison when the parameters of an existing result cannot be established.

Do not use the current UI aggregate as a decision-grade source. Read exact per-session evidence and show raw counts, denominators, failed, canceled, and missing cases beside every rate. Failed, canceled, and missing results remain outside the quality denominator.

Check candidate prompts, evaluator prompts, examples, tool fixtures, history scopes, and related trace families for leakage. Warn for exploration. Require resolution or narrow the claim only when the intended use is consequential.

## Explain the result

Report:

- exact experiment and run IDs, cohort version, candidate version, evaluator versions and parameters, tool policy, and run-spec evidence;
- comparison type and the state that was and was not restored;
- improved, regressed, unchanged, failed, canceled, and missing case counts, plus pending or evaluating counts for nonterminal runs;
- important paired case changes and protection outcomes;
- evaluator limitations, provenance gaps, and known execution differences;
- one conclusion: `improved`, `regressed`, `trade-off`, or `inconclusive`;
- only when the user supplied a fit-for-purpose gate: `meets`, `does not meet`, or `cannot evaluate` that exact gate;
- one next useful action.

Do not say `ship`. The user owns deployment. Kitaru does not currently expose a public winner, release policy, statistical significance, or CI-exit verdict.

If a run stalls, fails, is canceled, or remains partial, return its exact current state, completed, failed, canceled, missing, pending, and evaluating counts, resumable identifiers, and the smallest safe next action. Keep canceled cases outside quality denominators rather than folding them into failed or missing cases. If replay exposes a genuinely new or ambiguous behavior, hand exact session evidence to a bounded follow-up `kitaru-investigation` instead of silently changing the evaluator.

## Suggest one candidate when asked

Prefer, in order:

1. clarify an ambiguous instruction or tool description;
2. add one small non-leaking example or deterministic protection;
3. change one pipeline component or model setting;
4. consider a larger structural or model change after simpler interventions fail.

Do not start an automatic prompt search, cascade, fine-tuning program, or multi-candidate tournament.
