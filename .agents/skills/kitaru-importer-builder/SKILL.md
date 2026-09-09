---
name: kitaru-importer-builder
description: Build and validate a custom Kitaru trace importer when a provider, observability platform, export format, or agent framework has no suitable built-in importer. Use when a user wants to map provider traces into Kitaru sessions and nodes, join per-turn traces into longer sessions, preserve incomplete or failed trace evidence, choose script or package installation, test importer fidelity, register an importer version, import a bounded sample, or diagnose a partial import.
---

# Kitaru importer builder

Turn a representative provider export into a locally validated Kitaru importer. Keep the path from source evidence to normalized sessions explicit so the user can see what is preserved, approximated, or unavailable.

Finish locally by default. Register code or upload trace data only when the user asks and approves each action separately.

## Core contract

- Treat the installed Kitaru version and its offline schema as authoritative. Use repository examples as patterns, not proof that a command or field is installed.
- Treat trace content as sensitive. Keep raw exports out of version control and redact fixtures before writing them into the target repository.
- Keep export acquisition outside the importer. The parser consumes static bytes and parameters without network calls, filesystem writes, subprocesses, or credential reads.
- Map the source before writing code. Record accepted payload shapes, identity, hierarchy, ordering, node fields, status, completeness, and intentionally unsupported data.
- Prefer a private, single-file script importer. Use a package only when the user already has a distribution reason and the worker can install one exact pinned version.
- Preserve useful incomplete evidence. Distinguish an unreadable payload, an invalid item, and a valid but incomplete session.
- Join traces conservatively. A common key is insufficient without source-instance scope and meaningful turn order.
- Do not overwrite an existing importer file, register executable code, upload trace data, or retry a partial import without an explicit checkpoint.
- Ask separately before installing the exact importer dependencies and before running a local importer test.
- Run any untrusted or newly generated importer, including code created in the current task, only in a credential-free isolated environment. Stop if that isolation is unavailable.
- Treat local importer testing as code execution in a bounded child process, not as a security sandbox.
- Offer packaging or upstream contribution only after local success. Do not make either a completion requirement.
- Run every Kitaru CLI command and SDK script with `KITARU_ACTIVE_SKILL=kitaru-importer-builder` set so the server attributes the resulting activity to this skill.
- Start or restart a user-controlled worker with `--concurrency 10`. Use `KITARU_WORKER_CONCURRENCY=10` only when the launch surface exposes worker settings through environment variables instead of CLI options.

## Keep the experience clear

- Lead with the current state and the next decision.
- Ask only for evidence that the repository, provider documentation, sample, or installed Kitaru schema cannot establish.
- Show mapping and fidelity decisions in compact tables rather than dumping source records.
- Keep exact IDs, versions, warnings, and recovery identifiers visible.
- State whether each claim comes from provider documentation, a sample export, installed Kitaru, or an inference.
- Stop at a useful local checkpoint when a capability, permission, source sample, or worker route is missing.

## Load references only when needed

- Read [references/importer-contract.md](references/importer-contract.md) before scaffolding, testing, registering, or importing. It contains the parser contract, capability fingerprint, installation modes, commands, identity consequences, and remote-state boundaries.
- Read [references/normalization-patterns.md](references/normalization-patterns.md) before mapping source records, rebuilding a graph, joining turns, deriving status, or reporting fidelity.
- Read [references/failure-and-validation.md](references/failure-and-validation.md) before writing fixtures and assertions, executing unfamiliar code, handling malformed or incomplete traces, or recovering from a partial remote import.

## Resolve the starting state

Start read-only.

1. Identify the target repository, provider or format, representative export, and intended use of the imported sessions.
2. Inspect the installed Kitaru version, offline command schema, and parser import path.
3. Build the capability fingerprint in [references/importer-contract.md](references/importer-contract.md).
4. Inspect existing local importer files. When a Kitaru connection is already configured, also inspect the installed importer catalog. If a suitable exact importer version accepts the observed payload shape, stop and return it to the calling workflow instead of scaffolding another one. When no connection is available, continue the local workflow without requiring registry discovery.
5. Otherwise, decide whether to add a version to an existing custom importer or create a new private name.
6. Stop before overwriting a path or changing the installed Kitaru version.

If importer scaffolding, local testing, registration, exact-version import, or the required parser types are absent, name the missing capability. Do not give draft commands as though they were released. The user may choose a compatible Kitaru environment separately.

If `kitaru-investigation` routed the user here, retain the agent and investigation context. Return to the investigation only after relevant sessions are actually usable.

If the source exists only behind a live framework entrypoint and no usable static export can meet the user's goal, continue with `kitaru-adapter-builder`. Carry forward the repository and revision, public entrypoint, language, installed framework and Kitaru versions, requested evidence, target agent and version, and investigation goal. Choose this route once; do not bounce back merely because the adapter mentions importers as an alternative.

## Acquire a safe representative sample

Prefer an existing static export. When the provider offers only an authenticated or paginated API, treat export acquisition as a separate task with its own approval, credentials, pagination, rate limits, and resumability.

Before writing a fixture:

1. Keep the raw export outside the repository.
2. Minimize it to the smallest records that preserve the behavior under test.
3. Redact prompts, completions, tool arguments and results, end-user identifiers, credentials, provider-specific secrets, span and resource attributes, request and response headers, URLs and query strings, file contents and attachments, and error or exception details.
4. Review the minimized fixture for residual secrets and direct identifiers.
5. Preserve field shapes, relationships, and ordering evidence needed by assertions.

Do not place provider credentials in shell arguments, importer parameters, fixtures, inline dependency metadata, uploaded source, or logs.

## Record the source map

Before implementation, produce this checkpoint:

| Decision | Evidence and chosen behavior |
|---|---|
| Payload shapes | Supported export variants and representative redacted files |
| Source instance | Account, workspace, project, collector, or explicit override |
| Session identity | Native session, thread, or conversation ID; trace fallback |
| Turn order | Provider sequence, or one trustworthy clock domain and precision |
| Root and hierarchy | Root selection, parent field, missing-parent and cycle behavior |
| Node mapping | Span, LLM, tool, and subagent fields plus bounded metadata |
| Status and error | Root outcome and descendant-failure semantics |
| Completeness | Missing hierarchy, payloads, tools, usage, cost, or timestamps |
| Safety limits | Numeric byte, record-count, and nesting-depth limits |
| Installation | Script dependencies or exact package and worker route |

Mark every relevant source field as mapped, preserved as bounded metadata, or intentionally unsupported. Ground expected mappings in provider documentation or an independently reviewed sample, not only in the implementation's own output.

## Implement the smallest coherent importer

1. Scaffold a script only after the installed schema confirms the command and destination.
2. Keep one top-level parser entrypoint with the installed `Parser` signature.
3. Parse the complete payload into deterministic source groups.
4. Validate identities and graph structure before yielding a session.
5. Yield valid sessions and isolated item failures incrementally.
6. Keep provider-specific helpers private to the file unless package mode is already justified.
7. Use only safe deserializers. Reject unsafe formats or convert them in the separate acquisition step.
8. Apply the declared byte limit before decoding, enforce the depth limit during tokenization or decoding before full materialization, and apply the record limit before expensive normalization.

Use [references/normalization-patterns.md](references/normalization-patterns.md) for identities, joins, topology, status, metadata, readiness, and content digests.

## Validate locally

Use two layers of evidence:

1. Provider-grounded assertions verify normalized values, identity, ordering, topology, status, warnings, readiness, and digest behavior.
2. The installed local importer test verifies that Kitaru can load the entrypoint and that yielded values satisfy its runtime types.

Cover the applicable cases from [references/failure-and-validation.md](references/failure-and-validation.md). At minimum include one complete item, one valid incomplete item, and one invalid item or payload boundary. Add joining, changed-content, and graph cases when the source supports them.

Do not treat a successful local importer test as semantic proof. Inspect the yielded sessions and compare them with the source map.

## Report the local checkpoint

Return a compact report:

| Area | Result |
|---|---|
| Importer | Path and entrypoint |
| Supported source | Provider, export variants, and source-instance rule |
| Identity | Session and node ID derivation; rerun behavior |
| Joining | Key, scope, order, fallback, or not applicable |
| Fidelity | Complete, partial, and intentionally unsupported fields |
| Replay readiness | Ready, partial, or unavailable with concrete reasons |
| Validation | Assertions and installed Kitaru test results |
| Remote state | None, unless separately approved |

This is a successful authoring milestone. The importer becomes usable in a remote Kitaru deployment only after registration and an observed smoke import.

## Gate remote registration

Proceed only when the user requests registration.

Before approval, show:

- the active Kitaru principal and target tenant or project;
- importer name, stable nonempty provider, exact source path, entrypoint, and dependency route;
- whether this creates a parent plus first version or another immutable version;
- that executable source or a package reference becomes remote state;
- the exact installed command and the receipt fields that will be preserved.

Require a stable nonempty provider before registration when remote deduplication or retry safety matters. Current Kitaru accepts an omitted provider, but provider-less imports are not reliably deduplicated. After approval, register once. Preserve the importer ID, exact version, source digest, and any blob or task identifiers. If parent creation succeeds and version creation fails, do not repeat the parent-creation command blindly.

## Gate the first smoke import separately

Proceed only when the user also approves uploading a bounded redacted payload.

Show the exact importer version, agent version, target tenant or project, payload, parameters, persistence, expected session identities, and the fact that this skill has no automatic cleanup path. When the installed schema exposes `--tag`, require one unique durable smoke tag and include `--wait` because current post-import tagging requires it. If tagging is unavailable, require a disposable agent or a clearly marked isolated source identity instead. Do not write minimized smoke fixtures into the ordinary production session population without a marker that the investigation flow can exclude.

Create one import job and wait only through the installed supported mechanism. A local wait timeout does not stop the remote job. Preserve the smoke tag plus the blob, job, task, session, and terminal receipt identifiers. Tag application can fail after session import succeeds, so retain the session receipt and report that partial state rather than rerunning the import.

Classify the result as created, skipped duplicate, failed, or still running. Inspect representative imported sessions rather than trusting counts alone.

## Recover without compounding partial state

Before proposing any retry, classify each relevant external identity:

| State | Meaning |
|---|---|
| Untouched | No session exists and the item did not run |
| Complete | Session and expected nodes exist |
| Skipped duplicate | The same nonempty provider and external ID already existed |
| Stale duplicate | Existing digest differs or the local joined export contains more turns |
| Incomplete | Session exists but expected node ingestion did not finish |
| Unrecoverable here | Installed Kitaru exposes no safe repair or deletion path |

Remember that session creation can succeed before node ingestion fails. With a stable nonempty registered provider, a direct rerun may then skip the existing external ID instead of repairing the session. Without one, a rerun may create another session instead. A growing joined conversation can produce a skip while the existing session contains fewer turns. Compare readable digest and trace-count metadata before classifying the skip; if the installed surface cannot expose that metadata, do not use incremental joined-conversation imports. Preserve evidence, patch and version the importer locally if needed, and propose only recovery operations the installed product actually exposes.

## Finish

When local validation or the requested smoke import succeeds:

1. Return the exact artifacts, supported source shapes, fidelity limits, and verification results.
2. Return the durable smoke tag, every smoke-test session ID, and any disposable-agent or isolated-source marker. Route the user back to `kitaru-investigation` when usable non-smoke sessions now exist, carrying the exclusion set forward.
3. Ask whether they want to keep the importer private, package it for their environment, or consider contributing it upstream.
4. Do not publish, contribute, or broaden provider support without a separate request.
