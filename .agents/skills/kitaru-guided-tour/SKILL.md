---
name: kitaru-guided-tour
description: Give first-time users a short, value-first Kitaru tour with the public returns-agent template. Use when someone has no agent or traces of their own, arrives from Kitaru onboarding, asks for a demo, tutorial, quickstart, or guided example, needs the public template cloned or prepared, wants the coding agent to prepare trace annotations before they judge sessions, or wants to experience Kitaru's value before learning the full investigation method. Prepare a three-session frontend review, let the human provide verdicts, turn one accepted finding into a deterministic evaluator, and finish with one approved bounded replay experiment. Route real agents, open-ended discovery, and production evidence to kitaru-investigation instead.
---

# Kitaru guided tour

Deliver an AHA before teaching the complete method. Use the public Kitaru
returns-agent template to move from recorded traces, through a short prepared
frontend review and reusable evaluator, to one bounded experiment result.

## Experience contract

- Assume the user has not read the template agent and does not yet know how
  Kitaru works. Explain each example from the evidence visible in the review,
  translate internal names into plain language, and never require missing code
  or product context to understand a question.
- Act as a friendly guide, not an invisible automation runner. At each meaningful
  transition, explain what the tour is doing, why that step matters, what Kitaru
  concept it demonstrates, and what the user will be able to see or do next.
  Keep routine commands in the background.
- If standing user instructions strongly prefer terse or explanation-free
  responses, surface the tension before beginning the tour. Ask once: “This
  guided tour works best if I briefly explain each new concept and why each step
  matters. May I use a little more explanation than usual during the tour, while
  keeping routine commands and status updates compact?” If the user agrees,
  treat that answer as a tour-scoped clarification of their preferred style,
  not permission to ignore unrelated or higher-priority instructions. If they
  decline, remain concise but still explain the minimum needed to understand
  each checkpoint.
- Use names that already exist in Kitaru when naming product objects. Do not
  invent labels for steps, summaries, or collections, and do not present an
  internal implementation detail as a Kitaru concept. Prefer a plain sentence
  such as “Here is what the experiment will do” over “replay run card.”
- Lead with what the user is about to discover, not installation or evaluation
  terminology.
- Teach only the concept needed for the current action. Explain deeper Kitaru
  objects after the user has experienced why they matter.
- Prepare useful observations and exact evidence anchors for the reviewer. Tell
  them once that these are agent-prepared notes and their verdict is the human
  judgment.
- Use the Kitaru frontend three times: for the investigation review, the cohort
  and evaluator results, and the completed experiment run.
  Give direct links, explain what the user is looking at, then pause until they
  return. Do not interrupt the tour with frontend visits for routine objects.
- Keep the first tour to three sessions: one consequential problem, one subtle
  evidence-reading lesson, and one acceptable counterexample.
- Reach a useful evaluator result without regenerating traces or making a paid
  model call. Continue into one bounded replay, but briefly explain the proposed
  run and ask before creating the experiment or starting paid or live execution.
- Before a model-backed replay, verify without exposing secrets that the worker
  runtime has the required provider credential. If availability cannot be
  verified, ask the user to configure it and restart the worker, then stop
  before creating the experiment or starting its run.
- Start or restart a user-controlled worker with `--concurrency 10`. Use
  `KITARU_WORKER_CONCURRENCY=10` only when the launch surface exposes worker
  settings through environment variables instead of CLI options.
- Preserve the user's healthy selected Kitaru server, whether local or cloud.
  Do not switch servers merely because the public template documents a local
  quickstart.
- Resume durable agents, imports, sessions, exact-match annotations, cohorts,
  evaluator versions, experiments, and runs before creating replacements.
  Resume an investigation and its verdicts only when this conversation holds
  its exact ID or the user explicitly identifies it as their review.
- Prefer one combined approval for the clearly previewed tutorial writes. Do
  not turn the tour into a sequence of permission prompts.

## Load the tour references

- Read [references/tutorial-narration.md](references/tutorial-narration.md)
  before the first user-facing explanation and use its teaching rhythm
  throughout setup, review, the evaluator result, and the experiment result.
- Read [references/starter-template.md](references/starter-template.md) before
  setup or when deciding whether the checkout is the canonical public template.
- Read [references/tour-method.md](references/tour-method.md) before selecting
  sessions, writing observations, creating the review, or producing the AHA.
- Read [references/kitaru-operations.md](references/kitaru-operations.md) before
  any Kitaru CLI or MCP operation. Treat installed schemas as authoritative.

## Establish the tutorial route

Begin read-only.

1. Look for a candidate template checkout. If none exists, inspect the current
   canonical template README through the trusted source. Treat a prompt that
   names the public template, its canonical URL, or the returns-agent tour as
   intent to use this route; do not stop merely because the checkout is absent.
   Check the local prerequisites, choose a new `kitaru-template` destination in
   the current workspace, and preview the clone plus frozen-environment setup.
   Ask once before those writes. After approval, clone it, verify the checkout
   against the trusted canonical source, and only then install and verify the
   frozen environment. Never overwrite an existing destination. Complete this
   source setup before diagnosing Kitaru server connectivity.
   Use this route only when the resulting project root matches the public
   template contract in `starter-template.md`.
2. Establish the template environment, CLI, worker, and MCP package readiness
   through `starter-template.md` and `kitaru-operations.md`. Install the whole
   frozen template environment instead of adding packages piecemeal. Explain
   and ask before changing the environment or host MCP configuration. Treat MCP
   as preferred but optional when the CLI can complete the tour. If host MCP
   configuration is added or changed, tell the user to restart or reload the
   coding-agent host process or IDE, preserve a resume checkpoint, and do not
   claim that the current task can discover the new tools.
3. Inspect Kitaru connectivity and keep using the healthy selected server. Only
   offer the template's isolated local server when no usable server is selected,
   and ask before starting or selecting it. Then inspect the exact registered
   `returns-resolver` agent version, relevant import jobs, and the complete
   imported population carrying the `returns-baseline` tag.
4. Resume matching durable state. Perform only missing setup steps from the
   verified template README, with one clear explanation and approval before
   environment changes, service starts, registration, or import.
5. Stop and route to `kitaru-investigation` when the template was materially
   customized, the user supplies their own agent or evidence, or the requested
   conclusion needs a defensible open-ended investigation.

Do not ask a first-time user to choose a framework, trace provider, review
method, or sampling strategy on the canonical route.

## Orient in three short beats

Before selecting the tour, explain:

1. The sample agent resolves synthetic return and delivery requests with lookup,
   policy, shipment, refund, replacement, and escalation tools.
2. Kitaru preserved ten complete historical runs, including the model and tool
   evidence needed to understand what actually happened.
3. The coding agent will prepare three observations in Kitaru; the user will
   inspect the evidence and decide whether each complete session is acceptable,
   problematic, or uncertain.

Then continue. Do not explain cohorts or evaluator versions yet.

## Prepare one guided review

Follow `tour-method.md` to inspect every candidate trace and assemble the
three-session tour. Before writing, show a compact preview of what happened in
each session, why it matters, and the evidence that will be highlighted. Write
the preview for someone seeing both the agent and Kitaru for the first time.
Avoid an exhaustive investigation plan.

Ask once to:

- add the three sessions' agent observations as ordinary Kitaru annotations;
- create one fixed investigation with the prepared questions and highlights;
- open the resulting frontend review.

After approval, create or resume the annotations and investigation through the
verified operations. Keep agent observations short and start each durable note
with `Agent observation:` so it remains understandable outside this chat.
Never write the user's verdict for them.

## Hand off to the frontend

Lead with the direct review action. Use this conversational shape:

> **Open the guided review:** [Review in Kitaru](RESOLVED_URL)
>
> I prepared the highlighted observations. Your part is to inspect the trace
> evidence and choose **Acceptable**, **Problematic**, or **Uncertain** for each
> session. You can leave the written answer blank unless you want to add or
> correct something.
>
> Select **Done** after the last session, then return here and I will show you
> what your judgments make possible.

Pause. Do not duplicate the review in chat while the frontend route works.

## Create and run the evaluator

When the user returns:

1. Re-read the investigation, its ordered sessions, manual annotations,
   question-answer coverage, and verdict coverage.
2. Lead with what the user decided. Explain how each verdict now sits beside
   the exact recorded evidence that motivated the review.
3. If the user has judged every proposed cohort member, including at least one
   problematic target and one acceptable counterexample, propose one observable
   behavior that best explains the difference. Ask one short confirmation
   before turning it into a reusable check.
4. If the user accepts that behavior, create an exact cohort version from the
   confirmed target and counterexample sessions. Check the installed evaluator
   catalog before authoring code.
5. Select or create one narrow deterministic evaluator, test it, register an
   immutable version, and run it across the complete prepared baseline
   population.
6. Report the useful result first: how many sessions matched, which reviewed
   examples explain the result, and one important limitation. Resolve the
   cohort and evaluator links through `kitaru-operations.md`.

Then show the cohort and evaluator pages defined in `kitaru-operations.md`.
Explain that the cohort freezes the examples the user
judged and the evaluator page records the exact reusable rule. Pause and wait
for the user to return before preparing the experiment.

If the verdicts do not support a problem, or the user rejects the proposed
behavior, do not force an evaluator into the story. Show the useful negative
result: the prepared concern did not survive human review. Offer one more
bounded prepared review or stop successfully there.

If the review is partial, stop before behavior confirmation, cohort creation,
or evaluator work. Report what the persisted verdicts already show and keep the
same review link available for completion.

Use ordinary language before object names. For example:

> You marked two sessions problematic because the agent bypassed approval.
> Kitaru has preserved those judgments beside the policy and refund events. I
> turned that behavior into a reusable check, and it found the same pattern in
> 2 of the 10 recorded sessions.

Only then explain that the frozen examples are a cohort and the reusable check
is an evaluator version.

## Finish with one experiment

After the user returns from the cohort and evaluator pages:

1. Explain that the recorded sessions showed what happened before; an
   experiment starts fresh tasks from the same stored top-level inputs to test
   one changed condition.
2. Propose one small candidate change grounded in the accepted behavior. Do not
   start an automatic prompt search or introduce an unrelated model change.
3. Continue with `kitaru-replay-experiment`, carrying the exact accepted
   behavior, cohort version, evaluator version and parameters, candidate agent
   version, proposed override, and an explicit tool policy. For the verified
   public template, use passthrough for all six tools: each task calls
   deterministic local functions against a fresh in-memory mock store and
   cannot affect an external system. Do not silently replace those calls with
   recorded-history matching.
4. Let that skill verify adapter support and briefly explain the proposed run. Explain
   provider and worker readiness, model work, cost uncertainty, missing restored
   state, and possible tool effects. Obtain its required approval before
   experiment creation or run start.
5. After the run settles, lead with the experiment page from
   `kitaru-operations.md`. Explain which recorded cases were replayed, what
   changed, how the candidate compared, and what the result cannot establish.
   Pause until the user returns, then answer their questions and leave the exact
   experiment and run IDs in the final summary.

The normal successful tour ends after the user has inspected this experiment
result. If they decline the run or execution is blocked, preserve the proposed
conditions and cohort and evaluator links without claiming that the experiment completed.

Afterward, offer **Use my own agent** through `kitaru-investigation`, carrying
the tour's method explanation but none of its synthetic conclusions.

## Preserve a pleasant failure path

- If the template setup is incomplete, state the current checkpoint and the one
  missing action. Do not dump a generic setup checklist.
- If the imported sessions or full node payloads are unavailable, stop before
  inventing observations.
- If an annotation or investigation write may have succeeded despite a dropped
  response, re-read state before retrying.
- If the review URL cannot be resolved, preserve the created investigation and
  report the broken handoff with its exact ID and one retry action.
- If fewer than three useful sessions exist, use the smaller honest tour and say
  what teaching moment is missing.
- If the user stops after only part of the review, summarize the persisted
  verdicts and what they already show. Offer the same link for the remaining
  stops, but do not withhold the partial value or treat prepared observations as
  missing human judgments.
- If the user disagrees with an agent observation, treat that correction as part
  of the lesson. Do not defend the prepared note or silently convert it into a
  human conclusion.
- If adapter support, model credentials, a worker, or a safe tool policy blocks
  the experiment, preserve the proposed conditions and explain the missing condition. Do
  not fall back to live passthrough or call the tour complete.
