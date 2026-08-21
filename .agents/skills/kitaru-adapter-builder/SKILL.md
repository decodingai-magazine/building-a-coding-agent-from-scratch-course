---
name: kitaru-adapter-builder
description: Build a project-local Kitaru adapter for an unsupported Python or TypeScript agent framework. Use when a user wants to record or replay framework-native agent runs in Kitaru, needs a custom adapter, has no supported Kitaru integration for their framework, or needs to assess whether public framework hooks and the installed Kitaru SDK can support an adapter.
---

# Kitaru adapter builder

Build the smallest honest adapter inside the user's project. Preserve the
framework's public entrypoint and report exactly what the adapter can observe,
record, replay, and recover from.

Do not assume every framework can support the same fidelity. A useful result is
either a locally tested adapter or a precise blocker tied to the installed
versions and public hooks.

## Core contract

- Start with read-only inspection of the user's project and installed packages.
- Prefer supported Kitaru adapters, importers, or OTLP export when they meet the
  requested recording and replay needs. Do not create a duplicate integration.
- Treat the user's installed SDK and framework as authority. Use Kitaru reference
  adapters as patterns, not as source to copy or as proof of a published API.
- Use only public framework hooks. Offer a coarser boundary or stop when complete
  coverage would require private internals.
- Implement in the user's project first. Do not edit Kitaru core or prepare an
  upstream contribution unless the user separately approves that work.
- Keep per-run session IDs, clients, node indexes, buffers, replay state, and
  framework context isolated. Never store invocation state on a shared wrapper.
- Treat a partially recorded run as partial or failed evidence. Never call it a
  complete trace because the application returned successfully.
- Treat replay as execution, not a transaction. Block unknown or write-capable
  side effects unless the selected policy and user approval make them safe.
- Never install dependencies, call credentialed providers, create a remote Kitaru
  session, execute write-capable tools, or publish changes without the relevant
  approval.
- Run every Kitaru CLI command and SDK script with
  `KITARU_ACTIVE_SKILL=kitaru-adapter-builder` set so the server attributes the
  resulting activity to this skill.
- Start or restart a user-controlled worker with `--concurrency 10`. Use
  `KITARU_WORKER_CONCURRENCY=10` only when the launch surface exposes worker
  settings through environment variables instead of CLI options.

## Route from the project

Begin in the project that contains the real agent entrypoint.

1. Read the repository instructions and inspect the working tree. Preserve all
   existing and concurrent changes.
2. Identify the package manager, lockfile, runtime, language, framework and
   version, Kitaru package and version, public agent entrypoint, and invocation
   modes the application actually uses.
3. Route by the entrypoint's runtime:
   - For Python, read [the Python adapter reference](references/python-adapters.md).
   - For TypeScript, read
     [the TypeScript adapter reference](references/typescript-adapters.md).
   - For another runtime, stop with an unsupported-language report.
4. Read [the adapter method](references/adapter-method.md), search the project,
   installed packages, and current Kitaru documentation for a compatible
   adapter, importer, or OTLP path, and create the adapter assessment before
   editing code.
5. Read [validation and reporting](references/validation-and-reporting.md) before
   finalizing the design or writing tests.

In a mixed repository, route from the process that executes the agent. Do not
load both language references merely because both languages exist somewhere in
the repository.

If the framework already exports traces in a format no built-in importer
supports and that post-hoc evidence meets the user's goal, continue with
`kitaru-importer-builder` instead of wrapping the live entrypoint. Carry forward
the repository and revision, public entrypoint, framework and version, export
source and shape, Kitaru package and version, target agent and version, and
requested evidence. Choose this route once; do not bounce back merely because
the importer mentions an adapter as an alternative.

## Establish the requested fidelity

Separate these questions before choosing an implementation:

| Question | Possible answer |
|---|---|
| Recording boundary | Whole invocation, turn, model step, tool call, subagent call, or coarser span |
| Replay tier | Input rerun, turn replay, framework-native boundary replay, finer checkpoint replay, or not claimed |
| Tool visibility | Local tools, MCP tools, provider-native tools, hidden framework tools, or unknown |
| Invocation modes | Async, sync, streaming, manual iteration, batch, handoff, resume, interrupt, or durable execution |
| Failure policy | Kitaru failure fails the invocation, or recording is best effort with an explicit degraded result |

Do not use “replay” without naming the tier. Running the same root input again is
an input rerun, not deterministic replay.

For streaming, keep three claims separate:

1. the application still yields tokens or events;
2. the adapter observes the stream through completion, failure, cancellation,
   and abandonment;
3. replay reproduces the original chunks or timing.

Supporting one does not prove the others.

## Stop before writing code when necessary

Stop and give the exact reason when any of these apply:

- a compatible existing adapter, importer, or OTLP route satisfies the request;
- the project is not Python or TypeScript;
- no usable public Kitaru SDK contract is installed or approved for installation;
- the framework exposes no public hook at the required boundary;
- the only Python entrypoint is synchronous and the available Kitaru contract is
  async, with no documented safe async framework hook;
- the requested behavior depends on hidden provider or framework activity;
- replay safety requires match cardinality, occurrence identity, or another
  Kitaru contract the installed SDK does not expose;
- an incomplete imported baseline cannot prove the calls needed for effectful
  replay;
- the required dependency is unpublished and the user has not approved a local
  tarball or workspace-link path.

Do not replace a missing SDK contract with handwritten REST calls, an improvised
event-loop bridge, private framework imports, or copied draft plugin code.

## Use explicit approval gates

Read-only discovery does not need a separate approval. Ask before each action
that changes the user's environment or external state:

| Action | Required approval |
|---|---|
| Install or change a dependency | Exact package, version, and package-manager command |
| Use a local unpublished TypeScript artifact | Exact tarball or workspace-link path |
| Call a model or other credentialed provider | Provider, expected calls, and cost or data consequence |
| Create a remote Kitaru session | Endpoint, tenant, captured categories, and test purpose |
| Permit live tool passthrough | Exact tools, effect class, and miss condition |
| Prepare an OSS contribution | Target repository, files, and contribution scope |

Approval for one row does not authorize another.

## Implement in bounded checkpoints

Use this order:

1. Confirm the public framework hook with a deterministic local probe.
2. Freeze the adapter assessment and capability claims.
3. Implement per-run state and the session/root lifecycle.
4. Add model, tool, subagent, and stream observation only where public hooks
   prove it.
5. Add replay only after the baseline-admissibility and side-effect gates pass.
6. Define bounded, allowlisted payload projections before broad serialization.
7. Test success, primary application failure, Kitaru write failure, concurrency,
   cancellation, and every claimed replay policy with fakes.
8. Inspect the recorded session and node tree rather than trusting only the
   application result.
9. Produce the capability report.

Keep the framework's public signature, return value, configured callbacks,
exception behavior, and type surface intact. If preserving them is not possible,
report the API change before implementing it.

## Handle failures as evidence

Preserve the first application or adapter failure. Attempt terminal failure
recording without masking that failure, and report later recording or cleanup
errors separately.

If session creation succeeds and a later write fails:

- retain already confirmed nodes;
- record the last confirmed write;
- try to mark the root and session failed;
- do not claim the trace is complete;
- apply the adapter's documented Kitaru-availability policy to the application
  invocation.

An incomplete imported trace is a replay-input problem here. Assess it and stop
unsafe replay. Do not repair or redesign the importer inside this skill.

## Resume safely

On a resumed run, re-read the project, working tree, installed versions, adapter
assessment, and tests. Recheck that the chosen hooks and SDK symbols still exist.
Do not overwrite user changes or repeat dependency installation and remote smoke
tests merely because an earlier transcript is unavailable.

## Finish with an honest handoff

Use the capability-report format in
[validation and reporting](references/validation-and-reporting.md). State what
was verified with fakes, what was verified against a real Kitaru session, and
what remains unsupported or unverified.

After every claimed mode passes locally, offer a separate optional contribution
step. Do not imply that a project-local adapter is upstream-ready merely because
the happy path works.
