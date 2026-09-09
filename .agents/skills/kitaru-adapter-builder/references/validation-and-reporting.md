# Validation and reporting

Use this reference before implementation to define the evidence contract, and
again before handoff to verify every capability claim.

## Contents

- [Set the verification level](#set-the-verification-level)
- [Test the lifecycle and failure matrix](#test-the-lifecycle-and-failure-matrix)
- [Assess replay baseline admissibility](#assess-replay-baseline-admissibility)
- [Classify replay and tool effects](#classify-replay-and-tool-effects)
- [Test streaming and concurrency](#test-streaming-and-concurrency)
- [Test the privacy boundary](#test-the-privacy-boundary)
- [Inspect remote data policy](#inspect-remote-data-policy)
- [Forward-test the skill (maintainers only)](#forward-test-the-skill-maintainers-only)
- [Write the capability report](#write-the-capability-report)

## Set the verification level

Label evidence precisely:

| Level | What it proves |
|---|---|
| Source-inspected | A named installed type, method, or public hook exists; runtime behavior is not yet proven. |
| Fake-client verified | The adapter emits the expected requests and state transitions without remote Kitaru. |
| Framework-runtime verified | The installed framework calls the adapter hooks and preserves its public behavior with deterministic fakes. |
| Live-session verified | A separately approved redacted smoke test produced and re-read one remote Kitaru session. |
| Provider verified | A separately approved provider call exercised the claimed path. Do not require this by default. |

Do not upgrade one claim because another claim reached a higher level. A live
session with no tool call does not verify tool replay.

Use deterministic fakes for the default test suite. A fake Kitaru client should
validate request models, record confirmed writes, support injected failures per
operation, and expose the final session and node tree for assertions.

## Test the lifecycle and failure matrix

Exercise each write boundary separately.

| Failure point | Required observable result |
|---|---|
| Before session creation | No remote identifiers claimed; original setup failure returned. |
| Session creation | No root claimed; application does not start unless the declared policy says otherwise. |
| Root initialization | Session ID retained; failed-session update attempted; trace reported partial. |
| Model or tool callback | First failure stored; later side effects stopped when required; failed node attempted. |
| Child-node ingestion | Confirmed nodes retained; last confirmed batch reported; terminal failure attempted. |
| Root terminal replacement | Session completion not reported as fully recorded; session failure or diagnostic attempted. |
| Session terminal update | Root result retained but trace reported uncertain or partial; no false complete claim. |
| Client or callback cleanup | Primary failure preserved; cleanup failure reported separately. |

Test both application success and application failure at relevant rows. In
particular, test an application failure followed by recording and cleanup
failures. The application failure must remain primary.

State and test the Kitaru-availability policy:

- **Fail-fast:** a Kitaru recording failure fails the application invocation.
  Current draft reference adapters follow this policy.
- **Explicit best effort:** the application result may survive, but the adapter
  must expose a machine-detectable degraded recording outcome and must never
  label the trace complete.

Allow best effort only when the unchanged framework API has a reliable
machine-detectable diagnostic or status channel, or the project exposes a
separate secondary status API. A log line alone is insufficient. Otherwise
choose fail-fast.

For every test, assert the framework result or error and the recorded artifact:

- session origin and status;
- root index, parent, type, status, inputs, outputs, error, and times;
- child ordering, stable indexes, parentage, and terminal status;
- model and tool identifiers and bounded payloads;
- last confirmed write when a later write fails;
- number and ownership of client cleanup calls.

## Assess replay baseline admissibility

Run this gate before applying overrides or allowing a tool to execute.

| Dimension | Required evidence | If unknown or false |
|---|---|---|
| Provenance | Known recorder or importer, version, source schema, and capture boundary | Treat the baseline as incomplete. |
| Terminal state | Session and root have a trustworthy terminal state | Block effectful replay. |
| Observable coverage | The source was capable of observing every event required by this replay tier | Reduce the replay tier or stop. |
| Call-result pairing | Every required tool or model call has a usable terminal result | Block policies that depend on the missing result. |
| Ordering | Required occurrence or sequence identity is present and internally consistent | Block ambiguous replay. |
| Adapter compatibility | Same framework, adapter, and compatible adapter version by default | Require an explicit compatibility proof. |
| Bounded schemas | Recorded inputs satisfy the current adapter's validated schema and limits | Reject rather than coerce silently. |

A completed session is not automatically admissible. An importer can honestly
mark a source trace complete while its source never exposed a provider-native
tool result required by the new adapter.

Ordinary replay miss policy applies only after this gate passes. An incomplete
or untrusted imported baseline is not merely a cache miss. Block effectful replay
and report which dimension failed. Fixing the importer belongs to the importer
workflow, not this skill.

## Classify replay and tool effects

Name one replay tier:

| Tier | Claim |
|---|---|
| Input rerun | Start a fresh invocation from recorded root input, possibly with overrides. |
| Turn replay | Reconstruct a complete conversation prefix and rerun the next turn. |
| Framework-boundary replay | Resume from a public framework state or checkpoint. |
| Finer checkpoint replay | Resume from a verified internal execution boundary exposed by a supported contract. |
| Not claimed | Record only. |

Do not call an input rerun deterministic, frozen, or exact. State what can vary:
model output, provider state, time, retrieval, tools, external data, retries, and
hidden framework behavior.

Classify every tool the adapter can observe:

| Effect class | Default replay behavior |
|---|---|
| Pure or deterministic local | Static or proven unambiguous history result may be allowed. |
| Read-only external | Prefer recorded result; live passthrough requires explicit approval and lowers evidence. |
| Idempotent write | Block by default; require idempotency key and explicit approval. |
| Non-idempotent write | Block unless a purpose-built suppression or sandbox strategy is proven. |
| Unknown, hidden, or provider-native | Block or mark replay unsupported. |

For history matching, verify logical tool identity, adapter and framework
version, input schema, canonical validated arguments, occurrence or sequence
identity, and result completeness. With baseline scope, the public lookup
accepts a zero-based `occurrence` that provides occurrence identity when the
adapter counts per cache key and advances only on a hit. Without `occurrence`,
a `found/result` lookup cannot prove cardinality. Preflight a complete baseline
and reject duplicate keys, or disable history replay.

Report hits, misses, ambiguity, blocked calls, live passthrough, simulated
results, and divergence separately. Hit rate is coverage, not fidelity.

## Test streaming and concurrency

For each claimed streaming entrypoint, test:

| Case | Required assertion |
|---|---|
| Full consumption | Completion occurs after the public terminal signal, not when the iterator is returned. |
| Failure before first item | Failed root and session are attempted; primary error preserved. |
| Failure mid-stream | Visible items do not cause a completed trace; failure point is recorded. |
| Caller cancellation | Cancellation propagates and terminal cleanup follows the documented policy. |
| Abandonment | Adapter either observes and records it or reports abandonment as unobservable. |
| Tool work inside stream | Parentage, ordering, policy, and failures remain correct. |
| After last visible item | Terminal bookkeeping failure is still reported as partial. |

Keep the report's three streaming columns separate: application streaming,
lifecycle observation, and chunk/timing replay.

For concurrency, run at least two overlapping invocations through one shared
adapter object. Use distinct inputs, failures, tools, and delays. Assert no
cross-run session IDs, buffers, indexes, replay specs, clients, callbacks, or
context leak.

Also test concurrent tools inside one run when the framework supports them.
Verify deterministic node ordering or explicitly documented ordering semantics.
During replay, prove that an earlier policy failure prevents later effectful
tools from starting.

Test interrupt, resume, and durable execution as separate invocations when the
framework exposes them. Require a strategy that prevents a resumed write-capable
tool from repeating unnoticed.

## Test the privacy boundary

Create a field-path inventory without copying real values:

- prompts and messages;
- model responses and reasoning-like fields;
- local and MCP tool inputs and outputs;
- provider options and headers;
- files, URLs, and attachments;
- framework runtime context;
- exception messages, causes, and stack data;
- adapter metadata and diagnostics.

For each category, mark `record`, `project`, `redact`, `omit`, or `unsupported`.
Use an allowlist where possible.

Always exclude literal credentials, including authorization headers, cookies,
API keys, passwords, access and refresh tokens, client secrets, credential
objects, signed URLs when applicable, and equivalent project-specific fields.

Test with unique sentinel strings placed in:

- ordinary input;
- nested tool input and output;
- provider configuration;
- an exception message and cause;
- callback context;
- an oversized or cyclic object.

Scan every emitted request, stored fake artifact, log, snapshot, diagnostic,
assessment, and capability report. Redaction tests must cover success and error
paths. A key-name filter alone is not a semantic privacy guarantee; include
project-specific projections where secrets can appear under ordinary names.

## Inspect remote data policy

Before an optional live Kitaru smoke test, show:

- exact Kitaru endpoint and tenant or account;
- categories and field paths to be sent;
- expected readers and access boundary;
- transport security as established by the endpoint;
- applicable retention, deletion, and residency information when known;
- whether the test creates billable or durable state;
- exact cleanup or retention plan.

Stop when the destination conflicts with the project's data policy or required
facts cannot be established. Do not send production prompts merely to validate
the adapter. Use synthetic redacted data and inert tools.

## Forward-test the skill (maintainers only)

This section validates the published skill itself. Runtime adapter builds must
skip it.

After the skill text is stable, use fresh agent contexts and disposable projects.
Do not supply the expected implementation or hidden conclusions.

Run these routing cases:

| Case | Expected behavior |
|---|---|
| Existing supported adapter | Verify compatibility and stop without duplicate code. |
| Suitable importer or OTLP path | Explain coverage and stop unless the user needs a proven in-process capability. |
| Unsupported runtime | Produce an exact unsupported-language report. |
| Missing Kitaru package | Propose the exact dependency and stop for approval. |
| Private-hook-only framework | Offer a coarser boundary or stop. |
| Sync-only Python framework | Refuse an improvised async bridge. |
| Missing TypeScript adapter subpath | Use a sufficient public client or stop at the dependency blocker. |
| Incomplete imported baseline | Permit inspection or reduced rerun, but block effectful replay. |
| Remote test without approval | Stop before creating a session. |
| OSS request without approval | Offer the step but make no upstream changes. |

Then select one real unsupported Python framework and one real unsupported
TypeScript framework that were not used as references while writing the skill.
Refresh the native-support inventory first. Use their real installed packages and
public hooks, deterministic fake models, a fake Kitaru client, and inert tools.

For each holdout record:

```text
Framework and exact version:
Prompt given to the fresh agent:
Expected route:
Observed route:
Files proposed or changed:
Approvals requested:
Validation run:
Capability claims:
False, missing, or unsupported claim:
Result: pass, revise, or blocked
```

Preserve successful holdouts as a small regression corpus only when the repository
has an established place for evaluation fixtures. Do not commit temporary
projects, downloaded packages, credentials, or provider output.

## Write the capability report

Finish with this compact report. Omit no unsupported mode the discovery found.

```text
Kitaru adapter result

Project entrypoint:
Language/runtime:
Framework and exact version:
Kitaru package and exact version:
Files changed:

Capability | Status | Boundary or policy | Verification level | Evidence
Recording | ...
Replay tier | ...
Local tools | ...
MCP tools | ...
Provider-native or hidden tools | ...
Application streaming | ...
Stream lifecycle observation | ...
Chunk/timing replay | ...
Concurrency | ...
Interrupt/resume | ...
Partial traces | ...
Kitaru availability | fail-fast or explicit best effort
Privacy projection | ...

Baseline admissibility:
Tool miss and side-effect policy:
Data destination and expected readers:
Fake-client checks:
Live-session checks:
Provider-backed checks:
Unsupported or unverified:
Migration path if native support appears:
Optional upstream contribution: not requested, offered, approved, or completed
```

When a write failed, add:

```text
Partial trace
Session ID, if created:
Primary failure:
Secondary recording or cleanup failures:
Last confirmed write:
Confirmed node indexes:
Root terminal write: confirmed, failed, or not attempted
Session terminal write: confirmed, failed, or not attempted
Application outcome:
Recovery or inspection action:
```

Use exact versions and paths. Distinguish verified behavior, reasonable inference,
and unresolved uncertainty. Do not call a fake-client result live-verified.
