# Current Kitaru experiment contract

Verify the installed CLI, MCP schemas, API models, and adapter documentation before operating. This reference describes the current v2 contract and may age.

## Honest replay semantics

An experiment run creates one fresh agent task per session in an exact cohort version. The task receives the historical session's stored top-level inputs after applying the experiment replay override. The override can replace the model, system prompt, prompt, or model parameters when the selected adapter construction path supports it.

This path does not restore an arbitrary conversation checkpoint, process memory, adapter instance state, filesystem, external service state, or other hidden world state. Explain every material missing state before asking the user to run the experiment.

## Experiment and run operations

Prefer these current MCP operations:

| Need | MCP operation |
|---|---|
| Read exact registry resources and versions | `kitaru_registry_read` |
| Create or update an experiment | `kitaru_experiments_manage` |
| Start an experiment run | `kitaru_workflow_start` with `operation=experiment_run` |
| Read runs and paginated replay jobs | `kitaru_activity_read` |
| Request cancellation | `kitaru_workflow_cancel` with `operation=experiment_run` |

The structured CLI equivalents are:

```text
kitaru experiment create NAME --agent AGENT --evaluator EVALUATOR@VERSION ...
kitaru experiment get EXPERIMENT
kitaru experiment run start EXPERIMENT --cohort-version UUID --agent AGENT@VERSION --evaluate-baselines
kitaru experiment run get RUN_UUID
kitaru experiment run jobs RUN_UUID
kitaru experiment run watch RUN_UUID
kitaru experiment run cancel RUN_UUID
```

Use JSON objects for `--override`, `--tool-policy`, and `--evaluator-params`. Verify `kitaru schema` or installed help before relying on example syntax. Creating an experiment and starting a run are non-idempotent remote writes. Watching is read-only. Cancellation requests settlement but does not wait. Deleting a run immediately removes its replay jobs and tasks and is not a cancellation mechanism.

An experiment requires at least one exact evaluator selection. An omitted evaluator version resolves to latest in the API model, but this skill always pins a version. Configuration can be updated only before the experiment has runs; use a new experiment for a changed condition afterward.

## Replay configuration

Current replay override fields are `model`, `system_prompt`, `prompt`, and `model_params`.

A tool policy has a required `default` configuration and optional per-tool overrides. Current configurations are:

- `passthrough`: execute the live tool;
- `history`: read a recorded call from `baseline`, `cohort_version`, or `agent` scope;
- `static`: return the first matching exact or subset case;
- `llm`: generate a synthetic tool result with a named model.

History and static misses support `fail`, `error_result`, or `passthrough`. Omitted experiment tool policy currently resolves to passthrough. Require an explicit policy for a tool-using run.

History lookup matches tool name plus canonical JSON arguments. With baseline scope, a zero-based `occurrence` selects the nth match in recorded order, so repeated identical calls replay their own results. Without `occurrence`, every scope returns its latest match. Wider scopes reject a set `occurrence`. A match does not prove that a stateful or time-sensitive result is faithful. Broader scopes can borrow data from another case.

## Adapter capability boundary

Resolve the installed adapter, exact version, and construction path. Current examples that must be re-verified:

- OpenAI Agents supports passthrough and restricted static substitution. It rejects history, LLM, hosted, MCP, approval-bearing, and agent-as-tool substitutions.
- LangGraph support differs between a direct compiled-graph wrapper and a supported factory path. The factory path can install model and tool middleware; direct wrappers reject unsupported overrides instead of guessing.
- PydanticAI instance-held message history does not survive a new process or replay unless the application makes that state explicit.

Unsupported capability is a blocker. The user cannot approve past it.

## Mutable and asynchronous state

An agent version's public run spec and capabilities can currently be updated in place. Canonically serialize and hash the complete public models recorded for approval, then re-read them immediately before creation and execution. Do not inspect secret values. If a hash changes, invalidate approval and explain that the configuration changed. Treat the hash as an internal comparison mechanism, not a Kitaru object or user-facing concept. These reads do not freeze the configuration a worker later resolves, and a transient mutation can escape pre-run and post-run checks. Require immutable runtime identity for a user-defined gate or live-effect run; otherwise narrow the use to exploration, report this residual risk, and return `cannot evaluate` for the gate.

Experiment runs are asynchronous and report `running`, `canceling`, `completed`, `failed`, or `canceled`. Progress counts pending, evaluating, completed, failed, canceled, and total replays. Page through all backing replay jobs needed for a complete accounting.

## Evaluation provenance limits

Set `evaluate_baselines=true` for a comparative claim. Baseline scheduling currently skips an evaluator-version ID that already completed on a session without comparing parameters. Therefore, verify prior parameters or deterministic configuration hashes before using parameterized baseline scores for a decision-grade comparison.

The current UI aggregate groups by evaluation name and data type and retains recent paired values. It is exploratory, not a full exact-version-and-parameters comparison contract. Read exact per-session evidence and report raw counts and missingness.

No public CLI or MCP operation currently computes a winner, release policy, statistical significance judgment, or CI verdict.
