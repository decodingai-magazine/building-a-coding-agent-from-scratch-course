---
name: kitaru-investigation
description: Guide users from their own agent code or recorded traces through Kitaru setup, session import or recording, human review, an accepted behavior, a versioned cohort, and evaluator selection, then hand one bounded change to the replay-experiment skill. Use when a user wants to connect or inspect an existing agent, import real traces, investigate a known bad or surprising session, discover recurring failure modes, learn the evidence-led review flow, resume an investigation, create a cohort from reviewed evidence, or author an evaluator for an accepted behavior. When a first-time user has no agent or evidence and wants a fast demonstration with the public returns-agent template, use the `kitaru-guided-tour` skill instead.
---

# Kitaru investigation

Treat this skill as Kitaru's evidence-led front door for real agents and traces.
Guide one continuous journey from the evidence the user has to a reviewed
behavior and, when they want to test a change, the
`kitaru-replay-experiment` skill.

## Core contract

- Treat the human as the judge. Select, summarize, organize, and compile
  evidence; never turn an agent suggestion into a human label.
- Preserve durable Kitaru state. Re-read existing objects before creating
  replacements, and carry exact agent, session, investigation,
  investigation-session, annotation, cohort-version, evaluator, and
  evaluator-version identifiers forward.
- Separate observed behavior from desired behavior. A trace records what
  happened, not what should have happened or whether the external outcome was
  correct.
- Distinguish agent behavior, external dependency behavior, product purpose,
  and independent outcome evidence.
- Use open observations before proposing a taxonomy. Deterministic signals may
  select sessions; they do not judge them.
- Explain remote writes and paid or live execution before running them. Ask for
  one proportional confirmation at the point of action.
- Prefer native Kitaru MCP operations when available. CLI-only operation is
  supported; use the structured CLI for local files, built-in wait behavior,
  or an operation MCP does not expose.
- Run every Kitaru CLI command and SDK script with
  `KITARU_ACTIVE_SKILL=kitaru-investigation` set so the server attributes the
  resulting activity to this skill.
- Start or restart a user-controlled worker with `--concurrency 10`. Use
  `KITARU_WORKER_CONCURRENCY=10` only when the launch surface exposes worker
  settings through environment variables instead of CLI options.
- Stop at a useful durable checkpoint when a required source, payload,
  permission, worker, product contract, or UI capability is unavailable.

## Keep the experience light

- Lead with the current state and one next useful action.
- Use ordinary language for user decisions. Keep internal labels such as
  `trace-first`, `cold-start`, question keys, and selectors out of the lead.
- Ask for one meaningful judgment at a time. Do not turn setup into a long
  questionnaire.
- Prefer short prose during active review. Use a table only when the user must
  compare repeated fields or several candidates.
- Summarize structured output instead of dumping JSON. Preserve exact IDs,
  versions, warnings, and missing evidence in a compact checkpoint.
- Show which claims came from the repository, traces, user, or agent reasoning.

## Orient a first-time user

When the user is new to Kitaru, explain this five-step method once:

1. **Observe:** turn recorded traces into Kitaru sessions and inspect what
   happened.
2. **Judge:** let a human record what should have happened beside the evidence.
3. **Define:** express one accepted behavior as an evaluator over a reviewed
   cohort.
4. **Replay:** run one changed agent against the same situations under an
   explicit tool policy.
5. **Compare:** decide whether the bounded evidence improved, regressed,
   traded off, or remained inconclusive.

Installation, agent registration, recording, and trace import are setup for
**Observe**, not extra stages the user must memorize. Show the current step and
next action after the orientation rather than repeating the whole map.

When a first-time user has no agent or traces and wants to see why Kitaru is
useful through the public template, continue with the `kitaru-guided-tour`
skill. For a generic first run with the user's own evidence, ask one user-facing
question before choosing the review size:

> Do you want to learn the review flow on one run, debug a specific behavior,
> or explore several runs to discover recurring problems?

Use a structured question action when the host provides one. Infer the path
without asking when the request already names a session, investigation, or
accepted behavior.

An incomplete starter handoff takes precedence over this question. Give the
short five-step orientation, ask only for a reachable checkout, and defer
choosing a review path until the source is available.

## Load references only when needed

- Read [references/investigation-method.md](references/investigation-method.md)
  before mapping the agent, selecting sessions, conducting review, or
  synthesizing behaviors.
- Read [references/kitaru-operations.md](references/kitaru-operations.md) before
  checking setup or calling Kitaru CLI or MCP operations. Verify the installed
  schemas when they differ from the reference.
- Read [references/starter-template.md](references/starter-template.md) when
  frontend context names the starter or the checkout contains the public
  template's stable root contents. Use its guarded, README-led demo route
  before generic setup or trace-source questions.
- Read
  [references/deterministic-evaluators.md](references/deterministic-evaluators.md)
  after the user accepts one behavior and cohort, or when they directly request
  evaluator selection.
- Read [references/evaluator-authoring.md](references/evaluator-authoring.md)
  only after checking the installed catalog. Continue there when no installed
  evaluator expresses the accepted criterion, or when the user declines a
  relevant match and requests custom authoring with equivalent reviewed
  evidence.

## Establish readiness and evidence

Begin with read-only inspection.

1. Resolve a reachable project root. For a frontend starter or `template`
   handoff, inspect the opened checkout before relying on its directory name or
   origin URL. Route to the starter-template reference when the root contains
   `pyproject.toml`, `returns_agent/`, and
   `traces/langfuse-traces.jsonl`; renamed clones and forks can still be the
   public template. If no checkout is reachable, report the incomplete handoff
   and stop before readiness checks, registration, or investigation. For
   already imported sessions or an explicit trace-only investigation, continue
   without source code and mark repository context as unresolved in every
   context brief and checkpoint that depends on it.
2. Inventory the discovered Kitaru MCP tool names and capability mode, and
   independently inventory whether the project Kitaru CLI is available. For
   MCP, distinguish absent tools or host configuration, read-only mode,
   `standard` mode, `destructive` mode, and a host that has not restarted
   since configuration. Modes are cumulative: a `destructive`-mode server also
   exposes every `standard` write tool. Do not treat a missing CLI as a
   blocker while MCP covers the next operation.
3. Choose one transport that can complete the next operation and its required
   handoff. Prefer a discovered MCP tool in at least `standard` mode; a
   `destructive`-mode server may perform ordinary writes, while destructive
   actions still require an explicit user request. Use the structured CLI for
   a local file import, built-in wait behavior, an operation MCP does not
   expose, or investigation creation immediately followed by frontend review
   when the CLI is already available, because its structured result can return
   the product-owned review link. When the CLI is absent but MCP can create the
   investigation, create it once through MCP and resolve the compatibility URL
   from the verified `dashboard_url`; do not install the CLI or recreate the
   investigation solely to obtain a link. Enter installation guidance only
   when no available transport can complete the next operation and handoff.
   Before using the CLI, check the selected server with `kitaru status` and
   inspect the installed command schema before giving exact syntax. Explain and
   obtain approval before changing the project environment. If MCP setup
   requires a host restart, return a resume checkpoint first.
4. Resolve the registered agent and exact agent version when possible.
5. Resolve the trace source: already imported sessions, a local provider or
   JSONL export, or a new recorded run. Ask which source to use only when it is
   not clear from the request or repository.
6. Explain that importing converts trace records into Kitaru sessions. Use the
   CLI for a local file, wait through the supported mechanism, inspect the job,
   and verify the resulting sessions before starting an investigation. Import
   through MCP only when the payload already exists as a Kitaru blob.
7. Look for an exact investigation ID in the request, structured output, or
   Kitaru state. Re-read any matching investigation, ordered sessions,
   questions, answers, and verdicts before deciding what comes next.

Route from durable state:

```text
sessions ready, no investigation
  -> map context and choose a bounded review path

investigation pending or in progress
  -> resume its worklist and report answer and verdict coverage

investigation completed
  -> synthesize persisted evidence or create a bounded follow-up

accepted behavior, no cohort version
  -> confirm exact membership and create a cohort version

cohort version ready, no evaluator version
  -> select an installed evaluator or author one narrow custom evaluator

evaluator version ready
  -> offer one bounded replay experiment
```

### Continue from frontend onboarding

Treat frontend onboarding as the doorway, not a separate workflow owner. Use
the repository, agent, and trace context it provides only after verifying them
against the reachable checkout and durable Kitaru state. When the frontend
names the starter or supplies no exact agent identity, stable starter contents
replace generic framework or trace-provider placeholders. When it names a
different concrete agent, framework, or trace source, report the conflict and
resolve which program is in scope before routing. Do not send the user back
into a circular handoff.

If the frontend promises a starter but supplies no reachable repository and
working directory, first inspect the current working directory for the stable
starter contents. If no candidate checkout is reachable, report that the
starter handoff is incomplete and stop before registration or investigation.

When the stable starter contents are present, follow the starter-template
reference. Do not ask the user to choose a framework or trace provider, obtain
live Langfuse credentials, export a time window, regenerate traces, or run the
included agent through a paid model. Leave that demo route when its canonical
agent or checked-in trace input was customized, and continue through this
skill's generic investigation path instead.

Route a missing integration only when it blocks usable sessions. Resolve the
installed importer catalog before treating a provider or export shape as
unsupported.

- Continue with the `kitaru-importer-builder` skill when existing traces use an
  provider or export shape that the installed catalog does not support. Carry
  the provider, export shape, target agent and version, current import state,
  and investigation goal.
- Continue with the `kitaru-adapter-builder` skill when the user needs
  in-process recording but no supported adapter covers the installed framework
  and invocation mode. Carry the repository, entrypoint, language, installed
  versions, required recording fidelity, target agent and version, and goal.

Choose one route from the evidence. Resume this skill only after usable sessions
exist or the builder returns an exact blocker.

## Map the agent and ask for missing purpose

Reconcile the current repository and public entrypoint with the registered
agent version and the versions attached to eligible sessions. Stop and ask for
the correct source when they could describe materially different programs.

Inspect only reachable code needed to understand prompts, models, control flow,
routing, retries, stopping, repository-defined tools, memory, permissions, and
external dependencies. Do not install packages, start services, invoke
credentials, or mutate external state during this mapping.

Give a short context brief covering:

- what the agent does;
- its external environment and important side effects;
- the user job and successful or prohibited outcomes;
- the repository, trace, test, feedback, or external evidence supporting each
  claim.

Ask the user to describe what the agent does and what a good outcome looks like
in their own words when code and traces leave important domain purpose
unstated. Do not ask them to restate mechanics the repository or traces already
establish.

Start the brief with: **Chat-only context. Kitaru does not currently store or
display this brief on the agent page.** Repeat that boundary at the review
handoff. Do not present the brief as durable Kitaru state.

## Choose a bounded review path

### Learn the review flow on one run

Choose one representative complete session with the user. Create a one-session
investigation only when they want a durable answer or review-flow smoke test.
Make no recurrence, prevalence, cohort, or evaluator claim from that review.
Afterward, explain what broader review would add and ask whether to continue.

### Debug a specific behavior

1. Resolve the exact seed session and read its complete nodes and payloads.
2. Ask for the suspected behavior in ordinary language if it is not clear.
3. When comparable sessions exist, select a small worklist containing the seed,
   plausible related sessions, and one deliberately dissimilar counterexample.
4. Treat the suspicion as a search direction, not a label.

### Discover recurring problems or unexpectedly good behavior

1. Define the eligible session population, time and version bounds, exclusions,
   payload availability, and pagination coverage.
2. Derive explicitly named selection signals directly from the bounded session
   and node data, such as cost, latency, error nodes, loops, repeated calls,
   node count, and tool patterns. Use them to find varied or unusual sessions,
   not to judge them. This selection step does not start evaluator jobs;
   consider evaluators only after the user accepts a behavior and exact cohort.
3. Start with four to six diverse sessions for one review block, combining
   coverage-oriented and seeded-random examples. Expand toward a larger pilot
   only after the first synthesis and the user's agreement.
4. Record the population, seed, selection method revision, and selection reason
   for every session.
5. Describe the batch as discovery, not evidence of prevalence or saturation.

## Create or resume the investigation

Every selected session needs a fixed non-empty question list. Default to one
required `observation` question asking what the reviewer notices. Add at most
one direction-specific question when the user already named a focus. Keep the
optional whole-session verdict separate from question answers.

Before creation, state in short prose:

- the agent and review goal;
- the session count and why this size fits the goal;
- the question the user will answer;
- that creating the investigation writes remote state.

Then ask once and create the complete fixed worklist. Do not create an empty
investigation because sessions cannot be appended later.

After creation, preserve the complete structured result, including
`links.review` when the CLI returns it. Never retry creation because
`links.review` is absent. If the result includes a review-link warning, retain
it while resolving the compatibility route and report it only when URL
resolution remains blocked. Return only the exact investigation ID,
agent/version, session count, review mode in user language, and next action.
Keep question keys, selectors, and the complete ordered ID list available for
technical resumption, not in the conversational lead.

## Hand off to frontend review

Prefer one continuous Kitaru frontend review block. Resolve the review URL in
this order:

1. Use `links.review` from the structured `kitaru investigation create` result
   when it exists. Do not reconstruct or rewrite that product-owned URL.
2. Otherwise use only the compatibility route documented from `dashboard_url`,
   the exact agent ID, and exact investigation ID in
   [references/kitaru-operations.md](references/kitaru-operations.md).

If no returned or documented URL reaches the investigation, stop and report a
broken product handoff. Preserve the investigation and show its exact ID, the
attempted URL or missing configuration, and one concise retry or bug-report
action. Do not recreate the review UI or collect investigation answers in chat.

When a URL resolves, render only the following three short paragraphs as
ordinary Markdown. Never put the link in a code block. Substitute the real URL
and replace `OPEN_STATUS` with the applicable sentence below:

**Open this review now:** [Review in Kitaru](RESOLVED_URL)

OPEN_STATUS The text box answers this investigation's question; the verdict
judges the whole session; **Annotate session** creates a separate manual
annotation. The context brief above remains chat-only.

Return here when you are ready for me to read the saved review state.

Use `I opened the page.` for `OPEN_STATUS` when the user requested an ordinary
open-URL action and it succeeded. Otherwise use `Open the link manually.` If the
environment provides such an action and the user asks to open the page, use it
before rendering the handoff, then pause after the three paragraphs. Do not use
Computer Use, browser automation, tab control, or a browser-specific integration
to annotate on the user's behalf.

After the user returns, re-read the investigation, fixed questions, verdicts,
and investigation-answer annotations. Report answer coverage and verdict
coverage separately. If the investigation is still in progress, continue or
deliberately finish the accepted evidence boundary; do not create a cohort from
an incomplete review merely because frontend pasteback suggests it.

## Alternate breadth and depth

During broad discovery, require at least four non-empty observations across at
least three distinct scenarios before the first synthesis. This permits a
provisional hypothesis, not a saturation claim.

When a possible behavior appears, search for related sessions with high recall,
include a dissimilar counterexample, and present suspected matches as agent
suggestions with evidence and uncertainty. Require the human to accept, reject,
or mark a suggestion uncertain before treating it as reviewed evidence.

The API cannot append sessions to an investigation or persist a typed
suggestion and disposition. Create a bounded follow-up investigation when more
review is needed. Never store an agent suggestion as a human annotation.

## Offer one useful insight at a time

After each reviewed block, state one concrete pattern, the reviewed evidence,
why it matters, the main ambiguity, and the best next evidence in short prose.
Include unexpectedly good behavior when it teaches the user something useful.
Distinguish repeated patterns from anomalies and provisional hypotheses from
accepted behavior. Never turn an adaptive sample count into prevalence.

Propose one to three observable behavior candidates. Lead with the most
consequential candidate in prose. Use a compact comparison table only when the
user must compare several candidates. For each retain its binary definition,
initial conditions, required independent outcome evidence, supporting sessions
and selectors, counterexample, and main ambiguity.

Ask the user to accept, edit, or reject one exact candidate. Silence and
conversational momentum are not acceptance.

## Define a cohort and evaluator

After exact behavior acceptance:

1. Show the proposed positive session IDs and reviewed counterexamples excluded
   from membership.
2. Ask once: "Create this versioned cohort with this exact membership?"
3. Create the cohort and immutable first version through a supported operation.
4. Re-read it and retain the exact cohort and cohort-version IDs.

Do not encode prevalence claims from an adaptive discovery sample.

If repeatable measurement is useful, continue with
[references/deterministic-evaluators.md](references/deterministic-evaluators.md).
Run relevant descriptive evaluators first, then prefer an installed configured
evaluator that directly expresses the accepted criterion. Only continue to
[references/evaluator-authoring.md](references/evaluator-authoring.md) when the
installed catalog cannot express it.

If the cause is an obvious prompt ambiguity, missing capability, ordinary bug,
or dependency failure, recommend the direct fix. Add an evaluator only when
preserving the case as a regression check is useful.

Finish with a compact factual checkpoint: evaluator and version IDs, exact
parameters, checks run, reviewed fixtures and verdict coverage, measured human
agreement or its absence, held-out evidence or its absence, freshness limits,
the claim supported, and stronger claims not supported.

Then explain that **Define** is complete and **Replay** tests one bounded change
against the reviewed cohort. Ask whether the user wants to continue. If yes,
continue with the `kitaru-replay-experiment` skill and carry:

- the accepted behavior and intended use;
- exact cohort-version ID;
- evaluator-version IDs and parameters;
- factual checks, evidence, and limitations;
- candidate agent-version ID when known;
- one proposed override;
- explicit tool policy;
- any user-defined gate.

Do not make the user copy these identifiers or route back through investigation
merely to choose a candidate. If they stop, leave the complete resumable
checkpoint.

## Preserve failure honesty

- If the review URL fails, preserve the investigation and report the broken
  product handoff; do not fall back to in-chat review.
- If agent source cannot be resolved safely, stop before presenting a
  code-grounded context brief.
- If full trace payloads are unavailable, identify the missing evidence and do
  not silently summarize truncated input.
- If a worker is unavailable, do not imply analyses or evaluations completed.
- If a mutating response is dropped, read current state before retrying because
  general request idempotency is not guaranteed.
- If the user abandons an investigation, leave it incomplete. Do not invent an
  `uncertain` verdict as a skip state or use destructive deletion as
  cancellation.
- If label isolation is not server-enforced, do not claim blinded evaluator
  validation.
