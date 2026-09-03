# Adapter method

Use this reference to assess a framework, choose a public integration boundary,
and implement one run lifecycle without committing to language-specific SDK
calls.

## Contents

- [Discover the actual execution path](#discover-the-actual-execution-path)
- [Check existing support first](#check-existing-support-first)
- [Inventory public hooks](#inventory-public-hooks)
- [Write the adapter assessment](#write-the-adapter-assessment)
- [Choose the recording boundary](#choose-the-recording-boundary)
- [Use one lifecycle state machine](#use-one-lifecycle-state-machine)
- [Preserve failure precedence](#preserve-failure-precedence)
- [Treat streaming as a lifecycle](#treat-streaming-as-a-lifecycle)
- [Implement without changing the framework API](#implement-without-changing-the-framework-api)
- [Reduce scope instead of inventing coverage](#reduce-scope-instead-of-inventing-coverage)

## Discover the actual execution path

Inspect before proposing files or dependencies.

Record:

- repository instructions and current uncommitted changes;
- package manager, lockfile, runtime, module system, and test runner;
- framework, framework version, Kitaru package, and Kitaru version;
- public entrypoint called by the application;
- construction path for the agent, model, tools, callbacks, and context;
- sync, async, streaming, manual-iteration, batch, handoff, subagent,
  interrupt, resume, and durable-execution modes actually used;
- deployment constraints that change resource ownership or process lifetime.

Use installed metadata, source, type stubs, and lockfiles as primary evidence for
the project. Consult official framework documentation and release notes for that
exact version. If external documentation is unavailable, mark version-sensitive
claims unverified and stop when installed source or types do not prove them.

Do not install packages, start services, call providers, or authenticate during
discovery.

## Check existing support first

Search in this order:

1. a framework-native Kitaru adapter already installed or documented;
2. a project-local adapter already wrapping the real entrypoint;
3. a supported Kitaru importer for traces the framework already exports;
4. an existing OpenTelemetry export path and its actual event coverage.

Compare the existing path with the user's goal. An importer can be enough when
the user needs post-hoc recording and its source contains the required evidence.
An in-process adapter may still be necessary for runtime overrides, tool-policy
enforcement, precise application failures, or framework-native replay boundaries.

If existing ingestion is insufficient, record the concrete gap. Do not dismiss
it merely because it is not an adapter. OpenTelemetry GenAI conventions are a
useful vocabulary, but they do not prove that a trace contains an executable
framework state.

## Inventory public hooks

Build the hook matrix from the installed version. Use one row per invocation
mode instead of extrapolating from a single callback.

| Mode | Public entrypoint | Before hook | Terminal hook | Model evidence | Tool evidence | Cancellation or abandonment | Proven coverage |
|---|---|---|---|---|---|---|---|
| Async run | ... | ... | ... | ... | ... | ... | yes, partial, or no |
| Sync run | ... | ... | ... | ... | ... | ... | yes, partial, or no |
| Stream | ... | ... | ... | ... | ... | ... | yes, partial, or no |
| Resume or interrupt | ... | ... | ... | ... | ... | ... | yes, partial, or no |

Trace callback ordering with a deterministic local probe. Verify whether a
configured user callback is replaced, composed, or called before or after the
adapter. Verify whether tool callbacks receive validated arguments, provider
arguments, results, public errors, retries, and stable call identifiers.

Reject a private hook even when it appears convenient. A private integration can
silently stop recording after an ordinary framework upgrade.

## Write the adapter assessment

Write the assessment before editing code. Record categories and paths, never
real prompt text, credentials, or payload values.

```text
Adapter assessment
Project and entrypoint: <path and public callable>
Language/runtime: <exact versions>
Framework: <package and exact version>
Kitaru: <package, exact version, verified exports>
Existing support checked: <adapter/importer/OTLP and conclusion>
Requested scope: <recording-only or recording-plus-replay>

Invocation mode | Public hook | Observable events | Opaque events | Claim
...

Recording boundary: <whole run, turn, model step, tool call, or coarser span>
Replay tier: <input rerun, turn, framework boundary, checkpoint, or not claimed>
Tool identities and inputs: <public source and normalization>
Side effects: <read-only, idempotent write, non-idempotent write, unknown>
Kitaru availability policy: <fail invocation or explicit best effort>
Privacy boundary: <allowlisted field paths and excluded categories>
Planned files: <user-project paths only>
Tests: <deterministic cases and recorded artifacts inspected>
Approvals still needed: <install, provider, remote Kitaru, passthrough, OSS>
Unsupported or unverified: <explicit list>
```

Do not fill unknown fields with likely behavior. An unknown becomes a reduced
claim, a local probe, or a stop condition.

## Choose the recording boundary

Choose the narrowest public boundary that observes the real application path
without changing its behavior.

Prefer, in order:

1. a supported framework capability or middleware boundary that encloses the
   invocation and composes with existing callbacks;
2. a public agent or generation wrapper that preserves the original signature;
3. public model and tool callbacks plus a wrapper around the terminal result;
4. a whole-invocation span when finer events are not observable.

Do not represent inferred inner events as recorded events. A whole-run adapter
that cannot observe provider-native tools may still be useful, but its report
must say those tools are opaque.

## Use one lifecycle state machine

Model each invocation as isolated state. A shared adapter object may hold
immutable configuration and a client factory, but not live run state.

```text
discover
  -> create session in progress
  -> initialize root node at index 0
  -> record child nodes with stable increasing indexes
  -> replace root with completed or failed terminal state
  -> update session to the same terminal state
  -> release resources owned by this invocation
```

Keep these invariants:

- create one session and one root per invocation;
- write every parent before its child and keep each parent index lower;
- allocate a node index once and reuse it for idempotent retry;
- preserve confirmed children if a later batch fails;
- serialize concurrent callbacks through per-run ordering state;
- wait for queued node writes before declaring completion;
- replace the in-progress root at its original index;
- align root and session terminal status when both writes succeed;
- close only clients and resources the adapter created.

Do not promise atomicity. Session creation, root initialization, child ingestion,
root replacement, and session update are separate network writes.

## Preserve failure precedence

Track failures by origin:

1. application or framework failure;
2. adapter policy or serialization failure;
3. Kitaru recording failure;
4. cleanup failure.

Preserve the first failure that should determine the application outcome. When
an application failure already exists, attach later recording or cleanup
failures as secondary evidence instead of replacing it.

When recording fails after session creation:

- remember the last confirmed write and node indexes;
- attempt best-effort failed root and session updates;
- retain the original recording failure if no application failure preceded it;
- report the trace as partial even if terminal failure writes also fail;
- apply the assessment's Kitaru-availability policy consistently.

Do not silently switch from fail-fast to best-effort behavior. Both policies can
be valid, but the generated adapter must state and test the chosen one.

## Treat streaming as a lifecycle

Do not mark a streaming run complete when the framework returns an iterator or
stream object. The run remains active until the public terminal lifecycle signal
fires or the stream is fully consumed.

Test separately:

- completion after full consumption;
- failure before the first visible item;
- failure after one or more items;
- cancellation by the caller;
- consumer abandonment without explicit cancellation;
- failure during a tool call inside the stream;
- cleanup after the last visible item but before terminal bookkeeping.

If no public hook exposes abandonment or terminal status, state that limitation.
Do not claim exact chunk or timing replay merely because the original streaming
API remains intact.

## Implement without changing the framework API

Keep the user's normal call site when possible. Preserve:

- positional and keyword arguments;
- overloads, generics, and return types;
- configured callbacks and middleware;
- framework validation and defaulting;
- cancellation and exception behavior;
- model and tool retry semantics unless a replay safety rule requires a visible,
  documented change.

Put the adapter in the project's normal integration or infrastructure location.
Use dependency injection for fake Kitaru clients and clocks when the project
already supports it. Avoid a universal abstraction that is used only once.

Before recording payloads, define an allowlisted projection for framework input,
model request and response, tool arguments and results, errors, and attributes.
Serialization success is not evidence that a field is safe to record.

## Reduce scope instead of inventing coverage

Offer a concrete reduced result when the requested boundary is unavailable:

| Missing capability | Honest fallback |
|---|---|
| No model callback | Whole-run recording without model-call nodes |
| Hidden provider tools | Record the enclosing model step and mark tool details opaque |
| No stream terminal hook | Support non-streaming calls only |
| No safe async entrypoint in Python | Stop sync support rather than bridge event loops |
| No replay match cardinality | Disable history replay instead of choosing an ambiguous result |
| Incomplete imported baseline | Permit inspection or input rerun only; block effectful replay |
| Missing TypeScript adapter primitives | Use a sufficient installed public client, or stop if none exists |

If the reduced boundary no longer serves the user's goal, stop with the exact
hook or SDK contract that would be required.
