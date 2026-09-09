# Investigation method

Use this reference to map the agent, select sessions, conduct human review, and synthesize observable behaviors.

## Contents

- [Build the context brief](#build-the-context-brief)
- [Resolve source mismatches](#resolve-source-mismatches)
- [Select a trace-first worklist](#select-a-trace-first-worklist)
- [Select a cold-start worklist](#select-a-cold-start-worklist)
- [Generate evidence-linked summaries and highlights](#generate-evidence-linked-summaries-and-highlights)
- [Guide human review](#guide-human-review)
- [Keep suggestions provisional](#keep-suggestions-provisional)
- [Draft behavior candidates](#draft-behavior-candidates)
- [Automation boundary](#automation-boundary)

## Build the context brief

Start at the public agent entrypoint and follow only reachable code relevant to recorded behavior.

Capture:

- **Agent behavior:** entrypoint, revision, prompts, models, control loop, routing, retries, stopping, hooks, repository-defined tools, memory, and session behavior.
- **External environment:** data, services behind tools, identity, permissions, network, time, mutable state, and dependency failure modes.
- **Purpose:** intended users, important jobs, useful outcomes, policies, required handoffs, and prohibited outcomes.
- **Evidence:** tests, fixtures, issues, existing evaluations, complete traces, user feedback, and independently observed resulting state.

Prefix each material claim with one provenance marker:

- `[repository]` for a claim supported by a named path and revision;
- `[trace]` for a claim supported by a named session or node;
- `[user]` for a fact supplied or corrected by the user;
- `[unknown]` for a fact that remains unresolved.

Start with one source line, then four short bullets. Lead each bullet with what is established; add provenance and the most important unknown only when useful:

```text
Registered agent/version: ...
Workspace, revision, and public entrypoint: ...
Version mismatches or reconstruction gaps: ...
```

```text
- Agent behavior: ...
- External environment: ...
- Purpose and success: ...
- Available evidence: ...
```

Use a table only when several conflicting sources must be compared. Show the brief before asking questions. When purpose or success remains unclear, ask the user to describe what the agent does and what a good outcome looks like in their own words. Ask other questions only for missing policy, impact, channel, handoff, or external-state facts. Do not ask the user to restate prompts, tools, retries, or session mechanics that the repository or traces establish.

## Resolve source mismatches

Compare three identities:

1. the registered Kitaru agent and version;
2. the agent versions attached to eligible sessions;
3. the current workspace and revision inspected.

Continue when differences are understood and named. Stop when the current source could describe a materially different program and the correct revision cannot be established. If an investigation spans several agent versions, retain each session's version and state which revision supplied the context brief.

## Select a trace-first worklist

Start with the exact seed. Add:

- plausible matches selected from trace-visible evidence;
- at least one session that shares surface features but may not contain the suspected behavior;
- at least one deliberately dissimilar counterexample.

Keep the first worklist small enough for one continuous review block. Do not use the suspected behavior as a pre-filled answer.

## Select a cold-start worklist

Define the population before sampling. Record agent version bounds, time bounds, filters, excluded statuses, payload availability, and pagination coverage.

Inspect several complete records before deciding which deterministic features can support diversity. Useful candidate-selection signals include cost, latency, node count, repeated calls, loops, tool-call patterns, error presence, and output shape. These are selection aids, not judgments.

Start with four to six sessions for one human review block. Combine:

- 60 to 70 percent coverage-oriented representatives across known structural variation;
- 30 to 40 percent seeded random sessions.

After the first synthesis, explain what broader evidence would add and ask before expanding toward a 15 to 30 session pilot. Do not turn the initial onboarding review into a large worklist merely because more sessions exist.

For every selected session retain:

- population definition;
- sampling seed;
- feature schema and algorithm or rule revision;
- selection round;
- selection reason such as seed, coverage representative, random, deterministic signal, suspected match, or counterexample.

Present the review queue as a short purpose, count, and explanation of the selection mix. Keep exact session IDs, versions, reasons, answer coverage, and verdicts in the resumable technical checkpoint. Show a table only when the user needs to compare or confirm individual members.

Adaptive sampling discovers candidate modes. It does not estimate prevalence. A serious cold-start taxonomy may need roughly 100 diverse traces and at least 20 observed failures before new-mode convergence becomes credible, but treat those numbers as course heuristics rather than product gates.

## Generate evidence-linked summaries and highlights

Keep summaries and persisted highlights neutral during the unprimed batch. A useful conversational summary contains:

1. the final output or terminal state;
2. a short factual execution outline;
3. important tool calls, model calls, retries, errors, or grouped parallel work;
4. exact session and node references for every canonical payload summarized.

Persist a highlight only under the specific session question it supports. Its selector may use an exact node ID, an RFC 6901 JSON Pointer into the node or session response, and an optional text span. Do not hide roles, tool names, errors, or omitted payloads. Do not use a free-floating conversational summary as durable evidence.

## Guide human review

Use an outcome-first review sequence. The reviewer does not traverse every node in reverse:

1. Start with what the user or customer received and the expected outcome.
2. Inspect only the execution evidence needed to determine whether it supports that result.
3. Expand relevant tool results, model calls, retries, and grouped parallel work.
4. Identify the earliest relevant step that made the result wrong, risky, unsupported, or unexpectedly good.
5. Persist the human's open observation as an answer to the linked session's question, targeting the whole session or exact node when appropriate.
6. Set a whole-session verdict only when the human confirms it. A null verdict says nothing about whether question review occurred, so report answer coverage separately.

During broad discovery, prefer the first upstream failure over every downstream symptom. After the user accepts one high-priority behavior, re-code every occurrence needed for a reliable cohort while preserving the original open observation.

## Keep suggestions provisional

After the unprimed batch, state each agent suggestion in one short evidence-led paragraph. Retain these fields in the technical checkpoint:

```text
Suggestion: <provisional behavior>
Why it was suggested: <bounded search or clustering method>
Evidence: <session and node selectors>
Counterevidence: <reviewed counterexample or missing evidence>
Uncertainty: <main ambiguity>
Human disposition: accept | reject | uncertain
```

Do not bulk-accept evaluator-critical evidence. A human-confirmed session verdict may change what supports a cohort, but a chat-only disposition on an agent suggestion is not durable Kitaru provenance. Neither rewrites the original observation or makes the agent its author.

## Draft behavior candidates

Use this template:

```text
Behavior: <provisional title>
Binary definition: <one observable criterion>
Initial conditions: <information, permissions, dependencies, and state available>
Required outcome: <independently observable success or failure>
Evidence needed: <what establishes the outcome without trusting the agent's account>
Supporting sessions: <exact IDs and selectors>
Counterexamples: <exact IDs and selectors>
Ambiguity: <what could change the judgment>
```

When comparing several candidates, follow the individual evidence cards with:

| Candidate | Observable definition | Positive evidence | Counterexample | Ambiguity |
|---|---|---|---|---|
| ... | ... | Exact sessions and selectors | Exact reviewed sessions and selectors | ... |

Reject or revise a candidate when the agent could satisfy it without exercising the named capability, required information was absent, success is ambiguous, or the evidence measures only a dependency or infrastructure failure.

## Automation boundary

Automate:

- diverse initial selection;
- deterministic outlier and structural signals;
- clustering human open codes into provisional hypotheses;
- high-recall searches for suspected instances;
- evidence linking and coverage accounting;
- candidate evaluator mechanisms after behavior acceptance.

Do not automate:

- conversion of a suggestion into a human label;
- taxonomy acceptance or freezing;
- trusted severity or business-impact assignment;
- judgment that locally plausible behavior satisfies an unstated product goal;
- reuse of adaptively explored traces as hidden evaluator test data;
- evaluator deployment or automatic agent changes.
