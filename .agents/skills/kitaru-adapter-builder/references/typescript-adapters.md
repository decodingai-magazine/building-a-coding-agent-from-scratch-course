# TypeScript adapters

Use this reference after the project route identifies a TypeScript agent
entrypoint. Treat the TypeScript SDK and adapter foundation as conditional until
the installed package proves that they are available.

## Contents

- [Establish the installed package surface](#establish-the-installed-package-surface)
- [Use the current source reference carefully](#use-the-current-source-reference-carefully)
- [Choose the supported implementation path](#choose-the-supported-implementation-path)
- [Preserve the framework type surface](#preserve-the-framework-type-surface)
- [Use the adapter primitives when available](#use-the-adapter-primitives-when-available)
- [Map framework events](#map-framework-events)
- [Control replay and side effects](#control-replay-and-side-effects)
- [Bound TypeScript payloads](#bound-typescript-payloads)
- [Test the TypeScript adapter](#test-the-typescript-adapter)

## Establish the installed package surface

Inspect the package manager, lockfile, Node version, module system, TypeScript
configuration, framework version, `@zenml-io/kitaru` version, and exact exports
resolved by the user's project.

Feature-detect the public client before the adapter subpath. A sufficient client
for the current lifecycle normally needs public methods equivalent to:

```text
createSession
updateSession
upsertSessionNodes
getReplay                 when replay is requested
lookupToolResult          when history replay is requested
```

Verify the installed signatures and request types. Do not write direct REST
calls when a method is absent.

Then test whether the project can resolve `@zenml-io/kitaru/adapter` and inspect
its actual exports. The checked source snapshot exports `RunRecorder`,
per-run state, normalized step helpers, replay parsing and resolution, and
tool-policy helpers. Use only the exports present in the installed or explicitly
approved local package.

If neither a sufficient public client nor the adapter subpath is available,
propose an exact published dependency only when installed metadata or an
approved registry check proves that it exists, then ask before changing the
project. Otherwise report publication as unverified and stop.

## Use the current source reference carefully

The checked TypeScript reference is merged into Kitaru's `develop` branch:

```text
branch: origin/develop
commit: 3675d90e02a690f2bd9a3ff43eba576f0a813515
core SDK: packages/core/ (@zenml-io/kitaru)
Mastra adapter: packages/mastra/ (@zenml-io/kitaru-mastra)
Vercel AI adapter: packages/vercel-ai/ (@zenml-io/kitaru-vercel-ai)
```

These packages are published independently from Kitaru's Python distributions.
Inspect npm and the project's lockfile for the current versions before relying
on them.

All three packages are ESM-only and require Node `>=22.22.0 <23`. The core
package exports its public client and types plus `./client`, `./environment`,
`./errors`, and `./adapter`. The adapter subpath exposes recording, replay,
normalized-step, and local tool-policy primitives for adapter authors. It is not
a framework-independent agent or streaming abstraction.

The Mastra package targets `@mastra/core >=1.51.0 <1.52.0` and wraps
non-streaming `Agent.generate()` through `KitaruAgent`. The Vercel package
targets `ai >=7.0.0 <8.0.0` and wraps non-streaming `generateText` through
`createKitaruGenerateText`. Both support local tools with passthrough, static,
and history replay policies. Neither supports streaming, provider-executed or
dynamic tools, LLM tool policy, or TypeScript scorers. Inspect their pinned
READMEs for additional framework-specific exclusions before offering support.

Replay can execute live passthrough tools and is not a transaction. Require
approval for those effects. Treat history matches as adapter-specific because
framework validation, defaults, or serialization may change tool inputs. Both
adapters require a replay model replacement to appear in the configured
`allowedReplayModels` list. An unchanged requested model is not checked against
that list.

The checked core client is generated from that commit's OpenAPI schema and its
recorder no longer sends the removed session `expected` field. This establishes
compatibility with that checked repository revision only. Confirm the installed
package exports, version, and compatible server schema before using it.

If the branch or checked commit cannot be resolved, treat these lessons as
unverified. Rely on the installed package's public types and symbols, and report
that the source reference was unavailable.

Do not copy package source into the user's project. Do not add an unpublished
workspace dependency without approval. When the user wants to test the branch,
offer an exact locally built tarball or workspace-link path and ask before using
it.

## Choose the supported implementation path

Use this order:

1. Use an existing published framework adapter when it supports the installed
   framework and requested modes.
2. Use the installed `@zenml-io/kitaru/adapter` primitives with the installed
   public client.
3. If the adapter subpath is absent but the installed public client exposes the
   complete session and replay methods, implement the shared lifecycle locally
   against that client.
4. If the only usable package is a user-built draft tarball or workspace link,
   continue only after explicit approval and report the dependency as
   experimental.
5. Otherwise stop. Do not handwrite HTTP requests or generated API types.

Keep the local implementation small. Do not recreate the whole adapter
foundation merely because one helper is absent.

## Preserve the framework type surface

Use a wrapper factory or class that preserves the framework's public call site.
Retain:

- generic model and tool-set parameters;
- input and option types;
- overloads when the framework exposes them;
- exact promised result type;
- configured hooks and callbacks;
- `AbortSignal` behavior;
- ESM import paths and the project's module-resolution rules.

Compile a type-only usage test that assigns the wrapped callable where the
original callable was accepted and checks important inferred result fields.
Runtime tests cannot detect an adapter that degrades a generic API to `unknown`
or `any`.

Compose callbacks in an explicit order. Record the framework event first when a
later user callback failure must not erase evidence. Preserve the user's
callback and its error behavior. Document any order that differs from the
framework default.

## Use the adapter primitives when available

At the checked `develop` revision, `RunRecorder` demonstrates this lifecycle:

1. `create(...)` creates an in-progress session and isolated `RunState`.
2. `initialize()` upserts the in-progress root at index `0`.
3. framework callbacks normalize model and tool steps and enqueue ordered node
   upserts;
4. `complete(result)` waits for queued steps, replaces the root as completed,
   and updates the session;
5. `fail(error)` stores the first failure, best-effort waits for the current step
   queue, writes failed policy outcomes, and closes the root and session as
   failed.

Verify the installed implementation and the framework callback lifecycle before
reusing it. The checked `RunState` does not itself seal `enqueueStep()` when
failure finalization begins. The adapter must therefore prove that the framework
cannot deliver another recording callback after `fail()` starts, or add a local
closed-state guard. Failure finalization must prevent new writes from entering
the queue and wait for all accepted writes to settle. Retain a queued-write
rejection as secondary evidence without letting it replace the primary
application error. Only then write the failed root and failed session.

Use the installed implementation when it satisfies these invariants rather than
duplicating it. Verify whether it closes or owns any client resource; the current
TypeScript client uses `fetch` and does not mirror Python client ownership.

When implementing against the public client without these primitives, reproduce
the shared lifecycle invariants from `adapter-method.md`, not the private class
layout. Keep one run-state instance per invocation, serialize node writes,
allocate indexes once, and preserve the first failure.

The current client retries node upsert only for a small set of transient server
statuses. Preserve the same node indexes and payload on a retry. Do not retry
model or tool execution as part of recording recovery.

## Map framework events

Prove each callback against the installed framework types and runtime.

For model steps, normalize:

- bounded request input;
- requested and effective model identifiers;
- allowlisted model settings;
- bounded output and public error;
- usage, cost, provider, and external ID when exposed;
- public tool calls and their stable call IDs.

For tools, wrap only public local tool execution paths. Preserve tool schemas,
validation, context, abort signals, return types, and public failures.

Use the current adapters as contrasting lessons:

### Mastra lessons

- wrap `generate()` only when that is the application's actual entrypoint;
- preserve method binding to the original agent;
- compose configured and caller-provided hooks instead of replacing them;
- treat the narrow peer range as version evidence, not broad compatibility;
- do not infer streaming support from non-streaming generate callbacks.

### Vercel AI lessons

- preserve the typed `generateText` signature rather than returning a generic
  promise;
- wrap local tools before execution so replay policy can prevent side effects;
- disable model retries during controlled replay when a retry could repeat tool
  planning or produce duplicate effects, and report that behavior change;
- store callback recording failures and throw the first failure after generation
  rather than allowing later successful work to hide them;
- compose stop conditions and user callbacks deliberately;
- use ordered execution tickets or an equivalent mechanism when concurrent tool
  starts could let a later side effect run after an earlier policy failure.

Provider-executed, dynamic, approval-gated, and hidden tools remain unsupported
unless their public hooks expose a pre-execution decision and terminal result.

## Control replay and side effects

Resolve replay identity and overrides through the installed SDK. Do not assume
the Python environment contract. At the checked revision, the TypeScript SDK
parses `KITARU_REPLAY_ID`, optional task input, and replay overrides through its
own helpers; verify current precedence and supported fields.

Before generation:

1. run the baseline-admissibility check;
2. apply only bounded, allowlisted prompt, system-prompt, model, and model-setting
   overrides;
3. resolve replacement models through an explicit allowlist and resolver;
4. classify every observable tool by effect;
5. establish the exact miss policy and execution order.

For history replay, the same public contract applies as in Python. For
baseline scope, keep one counter per cache key, send it as a zero-based
`occurrence`, and increment it only on a hit, so repeated identical calls
replay their own recorded results in baseline order. Never send `occurrence`
for other scopes, the server rejects it. When the installed API lacks
`occurrence` or the scope is not baseline, a `found/result` lookup does not
prove unique cardinality: require a complete preflight that proves unique
baseline keys. Otherwise mark history replay unsupported.

When several tool calls can run concurrently, ensure that a failure in an
earlier replay decision prevents later write-capable calls from starting.
Cancellation must reject pending turns and clear any ticket or ledger state.
Do not leave unresolved promises after the framework returns.

Do not support pre-supplied approval responses, provider-native execution, or
dynamic tool shapes unless the installed framework exposes enough public state
to validate and reproduce them safely.

## Bound TypeScript payloads

Do not use broad object cloning as the capture policy. Project the exact fields
needed from framework options, messages, results, and tools.

Enforce limits for:

- object depth;
- object keys and array items;
- individual string length;
- total serialized JSON size;
- model-setting names and numeric ranges;
- replay override keys and sizes.

Reject cycles, non-finite numbers, dangerous prototype keys, functions, symbols,
and unsupported objects. Decide deliberately whether oversized strings are
rejected or visibly truncated.

Always redact or omit authorization, cookies, API keys, passwords, access and
refresh tokens, client secrets, credential objects, raw requests, URLs when they
may carry credentials, provider options, runtime context, file contents, and
other project-specific sensitive paths. Apply the same rules to errors and test
diagnostics.

## Test the TypeScript adapter

Use the project's package manager and test runner. Use a fake Kitaru client,
deterministic fake model, inert local tools, and no provider credentials by
default.

Run:

- the focused unit tests;
- the project's TypeScript typecheck;
- the package or application build when it can expose ESM/export mistakes;
- an import smoke test through the project's real module system.

Cover at least:

- missing adapter subpath with a sufficient public client;
- missing or unapproved Kitaru dependency;
- approved local artifact, clearly reported as experimental;
- native signature, overload, generic, and return-type preservation;
- callback composition and callback failure;
- session creation, initialization, normal completion, and partial failure;
- first-failure preservation;
- a delayed queued-write rejection during failure finalization, proving that
  finalization waits for the queue, preserves the primary application error,
  and writes the failed root and session afterward;
- stable node indexes across transient upsert retry;
- concurrent invocations and concurrent local tools;
- earlier policy failure blocking later side effects;
- passthrough side effect followed by recording failure;
- static and history hits, misses, ambiguity, and incomplete baseline refusal;
- bounded projection and sentinel credentials;
- unsupported provider, dynamic, hidden, and approval-gated tools;
- explicit non-streaming scope unless a separate public stream lifecycle is
  implemented and tested;
- abort and cleanup of pending execution state.

Inspect the fake client's final session and node tree. Typecheck the user's real
call site, not only an isolated wrapper fixture.
