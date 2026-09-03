# Kitaru importer contract

Use this reference to inspect the installed importer surface, implement the parser callable, choose an installation mode, and cross remote-state boundaries safely.

## Contents

- [Authority and capability fingerprint](#authority-and-capability-fingerprint)
- [Parser contract](#parser-contract)
- [Identity and deduplication](#identity-and-deduplication)
- [Local scaffold and test](#local-scaffold-and-test)
- [Installation modes](#installation-modes)
- [Registration and import](#registration-and-import)
- [Remote partial states](#remote-partial-states)

## Authority and capability fingerprint

Treat the installed Kitaru version and offline schema as authoritative. This reference design was checked against Kitaru commit `3675d90e02a690f2bd9a3ff43eba576f0a813515` on `develop`; re-check the current installed schema before relying on it.

Inspect read-only before emitting exact commands:

```text
kitaru version
kitaru schema importer scaffold
kitaru schema importer test
kitaru schema importer register
kitaru schema importer version register
kitaru schema session import
```

Confirm the installed parser import path in the active Python environment. Record this fingerprint:

| Capability | Evidence to record |
|---|---|
| Parser types | `Parser`, `ImportedSession`, `ImportedNode`, and `ImportFailure` import successfully |
| Scaffold | Exact destination and overwrite behavior, or that manual file creation is required |
| Local test | Entrypoint, payload, params, timeout, and code-execution warning, or the project-native isolated test path |
| Registration | Script/package choice, entrypoint, provider, and version behavior when remote registration is requested |
| Version registration | Whether an existing importer can receive an immutable new version when that remote action is requested |
| Session import | Exact importer and agent versions, payload, params, optional RFC 6901 `join_on`, durable tags, and wait behavior when a remote import is requested |

Stop only when a capability required by the requested outcome is absent. Parser types are required to author against the installed contract. Missing scaffold or local-test helpers may use explicit file creation and project-native isolated tests when those paths still exercise the installed parser contract. Missing registration or session-import capabilities limit remote actions; they do not prevent completing a locally validated importer. Do not upgrade Kitaru or substitute draft syntax without the user's separate direction.

## Parser contract

The reference parser shape is:

```python
from collections.abc import Iterator
from typing import Any

from kitaru.task.importer import ImportFailure, ImportedSession, Parser


def parse(
    payload: bytes, params: dict[str, Any]
) -> Iterator[ImportedSession | ImportFailure]:
    ...


parser: Parser = parse
```

The worker passes the complete payload as `bytes` and one JSON-compatible parameters object. It advances the iterator one item at a time. A parser exception while creating or advancing the iterator is a task-level parser failure; a yielded `ImportFailure` is one isolated failed item.

### Imported session fields

| Field | Meaning |
|---|---|
| `external_id` | Stable source identity used with a nonempty registered provider for deduplication |
| `status` | Installed `SessionStatus` value |
| `name` | Useful source-derived label or `None` |
| `inputs`, `outputs` | JSON-compatible session-level content |
| `error` | Root/session failure, not every failed descendant |
| `started_at`, `ended_at` | Aware timestamps when available |
| `metadata` | Bounded source and fidelity metadata |
| `framework` | Source framework when it can be established |
| `nodes` | Root `ImportedNode` trees or an explicitly indexed flat node list in deterministic order |

### Imported node fields

Map the installed model rather than copying this list blindly. The reference includes:

- optional `index`, `parent_index`, and `secondary_parent_indexes` for an explicitly indexed flat list;
- `external_id`, `trace_id`, `node_type`, `name`, and `status`;
- `error`, `started_at`, and `ended_at`;
- `input_text_selector`, `output_text_selector`, `system_prompt_selector`, and visible `reasoning`;
- `inputs`, `outputs`, `attributes`, and bounded `metadata`;
- `requested_model`, served `model`, `model_provider`, and `model_params`;
- `tokens`, `cost`, `tool_name`, and `subagent_id`;
- `children`, which Kitaru flattens depth-first with each primary parent before its children when explicit indexes are not used.

Reference node types are `llm_call`, `tool_call`, `subagent_call`, and `span`. Do not infer a semantic type merely from a suggestive name when the provider exposes a stronger field.

Use exactly one of the two current topology forms:

- nested root nodes with `children`, which Kitaru flattens depth-first; or
- a flat list in which every node has an explicit `index`, with `parent_index` and `secondary_parent_indexes` set where applicable.

Explicitly indexed nodes cannot have `children`, and every primary or secondary parent index must precede the child index. Preserve source DAG edges with `secondary_parent_indexes` when the installed contract exposes them. Record a topology limitation in metadata only when the source relation cannot be represented by the installed model.

## Identity and deduplication

With a nonempty registered `provider`, Kitaru deduplicates imported sessions on that provider plus session `external_id`. Agent, agent version, owner, and content are not part of that key. Require a stable nonempty provider before claiming retry or duplicate-skip guarantees. Current registration permits an omitted provider, but provider-less imports are not reliably deduplicated because the stored uniqueness key contains a null value.

Construct a collision-free namespaced external ID from stable encoded components. One readable form is to percent-encode every UTF-8 component as a URI path segment and join the encoded segments with `/`:

```text
<encoded source instance>/<encoded native session or trace id>
```

Use one specified encoding everywhere and test values containing `/`, `%`, `:`, and non-ASCII text. Do not concatenate raw components with a delimiter that may also appear inside a component.

The nonempty registered provider supplies the outer namespace. Include account, workspace, project, collector, or tenant information in `source instance` whenever the source can reuse native IDs across those scopes. Require an explicit `source_instance` parameter when the export cannot establish one safely.

Construct node IDs from separately encoded source-instance, native-trace, and span, run, or event components. Scope them consistently when the source can reuse IDs across accounts.

Consequences:

- rerunning the same provider and external ID is expected to count as skipped;
- changed content under the same identity is not automatically repaired or replaced;
- importing the same source trace under another logical agent may still collide;
- a source instance fallback such as a filename is weaker and must be reported.

Use a deterministic content digest to detect changed content locally before a remote rerun. See [normalization-patterns.md](normalization-patterns.md).

## Local scaffold and test

Use only commands confirmed by the installed schema. The reference forms are:

```text
kitaru importer scaffold NAME --path path/to/provider_importer.py

kitaru importer test path/to/provider_importer.py \
  --entrypoint parse \
  --payload path/to/redacted-export.json \
  --params '{"source_instance":"workspace"}' \
  --timeout 10
```

Scaffolding writes one file and conflicts when the destination exists unless an explicit force option is used. Inspect the existing file and ask before overwriting it.

Local testing validates the script source, imports the entrypoint in a child process, invokes it when a payload is supplied, and checks yielded types and counts. It captures bounded output and enforces a timeout. It does not prove semantic mapping, join correctness, topology, replay readiness, or safety. It is not a sandbox.

Before installing local dependencies, show the user the exact pinned dependencies, their provenance, and the isolated target environment. Ask for explicit approval to install that exact set. Record `none` when the importer uses only the standard library. The remote worker's dependency installation does not make dependencies available locally.

Before running the local importer test, separately show the exact command, importer path, fixture, parameters, and isolation controls. Ask for explicit approval to execute it. Do not treat dependency-installation approval as approval to run importer code.

Run any untrusted importer or importer newly generated in the current task only in a credential-free isolated environment that prevents access to ambient project and cloud credentials. Stop when suitable isolation is unavailable; do not fall back to the normal project shell. The child process used by Kitaru constrains duration and output, but it is not a security sandbox.

## Installation modes

### Script mode: default

Use one script with one top-level attribute entrypoint. Prefer the standard library. When a dependency is necessary, declare minimal exact versions in PEP 723 inline metadata if the installed worker supports it:

```python
# /// script
# dependencies = ["provider-sdk==1.2.3"]
# ///
```

Review the dependency's provenance. Install the exact version only after the local dependency approval described above. Test untrusted or newly generated code in the required credential-free isolated environment. Inline dependencies are executable supply-chain input to the worker, not harmless metadata.

Choose script mode when the importer is private, small, or used by one project. A local script can be a complete deliverable without being copied into Kitaru's OSS repository.

### Package mode: conditional

Use package mode only when the importer already benefits from a maintained distribution route. The reference registration contract requires:

- one exact PEP 508 `==` version;
- no URL, environment marker, wildcard, range, or editable source;
- an entrypoint in `MODULE:ATTRIBUTE` form;
- a trusted package index reachable with the worker's configured credentials.

Do not promise that a local or private package can install remotely until the actual worker route is verified. Prefer script mode when index authentication is uncertain.

## Registration and import

Registration and trace import are separate remote mutations.

Reference first-version commands:

```text
kitaru importer register NAME \
  --script path/to/provider_importer.py \
  --entrypoint parse \
  --provider PROVIDER

kitaru importer register NAME \
  --package 'distribution==1.2.3' \
  --entrypoint package.module:parse \
  --provider PROVIDER
```

Reference new-version command:

```text
kitaru importer version register IMPORTER \
  --script path/to/provider_importer.py \
  --entrypoint parse
```

Registration creates executable remote state. The first registration may create the importer parent, upload a source blob, and then create the first immutable version. Preserve every returned identifier. If the parent exists after a later step fails, resolve it and use version registration rather than blindly recreating it.

Reference bounded import:

```text
kitaru session import path/to/redacted-export.json \
  --importer IMPORTER@VERSION \
  --agent AGENT@VERSION \
  --params '{"source_instance":"workspace"}' \
  --tag 'kitaru-importer-smoke:provider-unique-id' \
  --wait
```

For an eligible per-turn export, add the dedicated join option only when the installed schema exposes it and the mapping establishes a strict conversation key:

```text
  --join-on '/conversation/id'
```

The CLI `--join-on` value is an RFC 6901 JSON Pointer. Dotted paths, when a particular parser supports them, belong in that parser's documented `--params` instead of this dedicated option.

Use exact importer and agent versions. Import uploads the payload, creates a job, and may create persistent sessions. A local timeout ends waiting, not the remote job. Tags may be applied after import and can fail separately; preserve the session and job receipt before attempting follow-on mutations.

Before each action, confirm the active principal, tenant or project, least-privilege permissions, exact source or payload, and expected persistence. Never reuse an earlier approval for the second action.

## Remote partial states

Registration and import are multi-step flows. Possible durable states include:

- importer parent exists without the intended version;
- source blob exists without a completed version;
- payload blob exists without a job;
- job exists and continues after the local waiter times out;
- earlier sessions exist after a later parser item crashes;
- a session exists after node ingestion fails;
- import succeeded but post-import tagging failed.

The reference result counts created, skipped, and failed items and retains at most 20 failure samples. The sample list is not a complete failure ledger when the failed count is larger.

Always re-read current state before retrying. Session creation precedes node ingestion, so an incomplete session can occupy the deduplication key and a whole-payload retry can skip it rather than repair it.
