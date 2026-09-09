# Importer failure handling and validation

Use this reference to preserve valid incomplete traces, isolate invalid items, test semantic fidelity, and recover from partial remote operations.

## Contents

- [Classify failure at the correct boundary](#classify-failure-at-the-correct-boundary)
- [Treat incomplete traces as evidence](#treat-incomplete-traces-as-evidence)
- [Apply input and execution safety](#apply-input-and-execution-safety)
- [Build a minimum fixture matrix](#build-a-minimum-fixture-matrix)
- [Assert semantics, not only types](#assert-semantics-not-only-types)
- [Run local Kitaru validation](#run-local-kitaru-validation)
- [Inspect and recover remote state](#inspect-and-recover-remote-state)

## Classify failure at the correct boundary

Use three outcomes:

| Boundary | Examples | Importer behavior |
|---|---|---|
| Whole payload is unsafe or uninterpretable | Size/depth/count limit exceeded, invalid encoding, unsupported top-level shape, unsafe serialization, no usable records | Raise a clear parser exception; the task cannot safely continue |
| One item or source group is invalid | Conflicting identity, parent cycle, no root, conflicting duplicate span, strict join path missing | Yield `ImportFailure` with a source location or line, external ID when known, and actionable error; continue with independent groups |
| Session is valid but incomplete | Missing parent, several roots, flat export, missing optional input/output, missing usage/cost, incomplete tool fields, unrepresentable source relation | Yield `ImportedSession`; preserve evidence and report completeness, warnings, and replay readiness |

Do not convert every imperfect trace into a failure. Do not hide an invalid identity or unsafe graph behind a warning merely to maximize import counts.

A generator can fail after yielding earlier sessions. Kitaru may preserve those sessions, so a late exception is not an all-or-nothing transaction.

## Treat incomplete traces as evidence

Common incomplete cases and honest behavior:

| Source condition | Preserve | Report |
|---|---|---|
| Missing parent | Child node as a root | Missing parent ID; graph incomplete |
| Multiple roots | Every root tree | Root count and selection used for session summary |
| Flat export | Ordered independent nodes | Source completeness `flat`; no invented hierarchy |
| Missing LLM input or output | Other model fields and node | Missing payload; replay limitation |
| Tool name without input/output | Tool node and available fields | Tool not replayable |
| Implicit tool-like span | Span and source attributes | Tool boundary not established |
| Missing tokens or cost | `None` | Source did not expose the value |
| Secondary DAG link | Explicit indexed node plus `secondary_parent_indexes` when the installed contract supports it | Exact preserved parents, or a named topology loss when it cannot be represented |

Warnings must identify the affected trace or node where possible. Keep them deterministic and bounded. Do not copy raw prompt or tool content into warning strings.

## Apply input and execution safety

Treat source payloads and importer code as untrusted until reviewed.

### Payload limits

Record numeric limits in the source map and test each boundary. Select values from representative provider exports and the worker's memory budget. The current built-in reference importers use whole-payload limits around 50 to 64 MiB; that is precedent, not a universal default.

Apply limits in this order:

1. byte size before decode;
2. nesting depth during tokenization or decoding, before a complete object tree is materialized;
3. safe deserialization with no object construction or external entity resolution;
4. top-level record count, preferably during streaming decode when the format permits it;
5. per-group node count when a single group can exhaust memory.

Use a decoder with an enforced depth limit or a streaming, string-aware structural pre-check. Do not fully deserialize an arbitrarily nested value and then walk it to measure depth. Convert decoder recursion or resource-limit errors into one bounded task-level payload failure without copying source content into the diagnostic.

Never use `pickle`, unsafe YAML loading, entity-resolving XML parsers, or an archive extractor without path and expansion limits.

### Sensitive data

Assume prompts, completions, tool arguments, user identifiers, and attributes can contain personal data or secrets.

- Keep raw exports outside version control.
- Minimize before redacting; fewer fields mean fewer leak paths.
- Replace sensitive values while preserving the type and shape needed by tests.
- Review fixtures with repository secret scanning where available and a targeted identifier search.
- Never log raw payloads or include source content in exceptions.
- Do not upload a sample merely because it is locally redacted; show the exact file and ask separately.

### Code and dependencies

Local importer testing executes the file. A timeout and child process constrain duration and output, not file, credential, subprocess, or network access. It is not a security sandbox.

Before testing any untrusted importer or importer newly generated in the current task:

1. confirm its provenance;
2. inspect imports and top-level execution;
3. pin and review every dependency, or record that there are none;
4. show the exact dependencies and isolated target environment, then obtain explicit approval before installing them;
5. use a credential-free isolated environment without ambient project or cloud credentials;
6. show the exact test command, importer, fixture, and parameters, then obtain separate explicit approval before executing it;
7. stop if suitable isolation is unavailable; do not run the importer in the normal project shell.

The parser itself must not perform network calls, filesystem writes, subprocess execution, or credential reads. Put authenticated or paginated export acquisition in a separate tool and provide the importer with static bytes.

## Build a minimum fixture matrix

Use small redacted fixtures. Split them when one combined payload makes an expected outcome hard to inspect.

| Fixture | Expected evidence |
|---|---|
| Complete hierarchy | One deterministic session and correct root/child tree |
| Valid incomplete group | Session retained with exact warnings and readiness reasons |
| Malformed item among valid items | One `ImportFailure`; valid sessions remain |
| Payload boundary | Clear task-level rejection before expensive normalization |
| Nesting at and beyond the limit | Boundary value accepted; excess rejected during decode with a bounded diagnostic |
| Reordered records | Identical sessions, node order, and content digest |
| Same identity, changed content | Same external ID, different content digest, explicit conflict warning before remote retry |

Add these when applicable:

- two traces with a scoped conversation key and source-defined turn order;
- missing, mixed, conflicting, or account-mismatched join keys;
- equal, missing, or incomparable timestamps without another order field;
- duplicate identical and duplicate conflicting records;
- missing parent, multiple roots, cycle, and no-root graph;
- completed root with failed recovered child;
- failed final root;
- tool nodes with and without replay fields;
- alternate supported provider export variants.

Expected outputs must come from provider documentation or an independently reviewed mapping. Avoid generating a fixture and its expected result from the same implementation logic.

## Assert semantics, not only types

For every parsed session, assert the applicable fields:

- exact external ID and source-instance scope;
- status, error, start/end time, session input, output, and framework;
- original trace IDs and deterministic turn order;
- exact root order, primary child topology, and secondary parents;
- node types, identities, statuses, errors, inputs, and outputs;
- text and system-prompt selectors plus visible reasoning when the source exposes them;
- requested and served model, `model_provider`, usage, cost, tool, and subagent fields;
- bounded provider metadata and intentionally unsupported fields;
- completeness and ordered normalization warnings;
- replay-readiness level, counts, booleans, and reasons;
- stable digest for record reordering and changed digest for semantic changes.

For `ImportFailure`, assert the source line or location, external identity when available, and useful error class without leaking payload content.

Check cardinality explicitly. A parser that silently drops a group can otherwise pass type checks.

## Run local Kitaru validation

Run the importer-specific unit assertions first. Then use the installed command form from the offline schema, for example:

```text
kitaru importer test path/to/importer.py \
  --entrypoint parse \
  --payload path/to/redacted-fixture.json \
  --params '{"source_instance":"test-workspace"}' \
  --timeout 10
```

Verify:

- the entrypoint loads from the intended environment;
- local dependencies are installed independently of worker dependencies;
- yielded item counts match the semantic assertions;
- captured stdout/stderr contains no source content or credentials;
- timeout behavior is understood.

Do not claim remote usability from this test. Registration, worker dependency installation, and one observed smoke import are separate evidence.

## Inspect and recover remote state

After registration or import trouble, preserve every identifier before doing anything else:

- importer parent and version;
- source-code blob and digest;
- payload blob;
- job and task;
- created session IDs, provider, and external IDs;
- created, skipped, and failed counts;
- sampled failures and the total failed count;
- post-import tag results.

Re-read current state and classify each relevant external identity:

| Classification | Evidence | Safe next step |
|---|---|---|
| Untouched | No matching session; item never ran | Include only this item in a newly approved import when supported |
| Complete | Session and expected node set exist | Keep it; do not rerun |
| Skipped duplicate | Matching provider/external ID already exists | Inspect the existing session and its digest/provenance |
| Stale duplicate | Existing digest differs or the local joined export has more turns | Stop; require a frozen snapshot or an installed targeted repair path |
| Incomplete | Session exists but expected nodes or payloads are missing | Stop; use an installed targeted repair path only if one exists |
| Unrecoverable here | Incomplete state and no safe repair/delete operation exists | Preserve IDs and escalate the product limitation |

Do not infer untouched items merely from the failure sample: the reference receipt stores no more than 20 failures. Use counts, task state, expected identities, and remote reads.

A waiter timeout does not cancel the job. Read job state before retrying or creating another job.

A parser may yield valid sessions before crashing. Session creation may also succeed before node ingestion fails. With a stable nonempty registered provider, reruns deduplicate on provider and external ID, so a direct retry can skip the incomplete session rather than repair it. Without a provider, do not assume a retry will skip duplicates.

Patch the importer locally, create a new immutable version after approval, and propose the smallest recovery payload only when the installed product exposes a safe route. Never delete, reimport, or change external IDs merely to bypass a collision without explicit user direction and a documented provenance consequence.
