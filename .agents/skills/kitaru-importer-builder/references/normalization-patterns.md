# Importer normalization patterns

Use this reference to map provider records into deterministic Kitaru sessions and node trees without hiding fidelity loss.

## Contents

- [Map before coding](#map-before-coding)
- [Stable identity](#stable-identity)
- [Parse and group deterministically](#parse-and-group-deterministically)
- [Join per-turn traces conservatively](#join-per-turn-traces-conservatively)
- [Build the node tree](#build-the-node-tree)
- [Map node semantics](#map-node-semantics)
- [Derive session status and content](#derive-session-status-and-content)
- [Report completeness and replay readiness](#report-completeness-and-replay-readiness)
- [Create a stable content digest](#create-a-stable-content-digest)

## Map before coding

Use provider documentation and at least one representative redacted export. Record every field that matters:

| Source concept | Kitaru target | Questions to settle |
|---|---|---|
| Account or workspace | Source-instance namespace | Can native IDs repeat elsewhere? |
| Session or conversation | Session external ID and join key | Is it present on every trace? |
| Trace or run | Turn identity and node `trace_id` | Does one trace equal one whole session or one turn? |
| Span or event | Node external ID | Is it stable and unique within the source instance? |
| Parent relationship | `children` tree | Are missing parents and multiple roots possible? |
| Operation kind | Node type | Does the provider explicitly identify LLM, tool, or subagent work? |
| Status and error | Node and session status | Which root outcome is authoritative? |
| Input and output | Session/turn/node payloads | Are values structured, encoded JSON, or absent? |
| Model and usage | Model, tokens, cost, params | Which values are requested versus observed? |
| Extra attributes | Bounded metadata | Which keys aid provenance or recovery? |

Classify each relevant field as mapped, preserved in an allowlisted provider namespace, or intentionally unsupported. Do not preserve an arbitrary source record wholesale in metadata.

## Stable identity

Normalize the source instance explicitly. Prefer a provider account, workspace, project, service namespace, or collector identity. Use an explicit parameter when the export omits the scope. A filename fallback may be useful for a local prototype, but record that it is weak and require confirmation before remote import.

Use stable source values rather than array offsets, timestamps alone, random UUIDs, or hashes of mutable content for external IDs.

Recommended shapes use a stable URI path-segment encoding for every component:

```text
session external_id = <encoded-source-instance>/<encoded-native-session-or-trace-id>
node external_id    = <encoded-source-instance>/<encoded-native-trace-id>/<encoded-native-span-run-or-event-id>
```

Percent-encode each UTF-8 component independently, including `/`, `%`, and `:`, then join the encoded segments with `/`. Test separator-containing and non-ASCII values. Do not join raw components with a delimiter because different component tuples can otherwise produce the same external ID.

The source-instance node prefix may be omitted only when the provider guarantees that native trace and node identities are globally unique across every source instance handled by the registered importer.

A nonempty importer registration provider and the session external ID form the remote deduplication key. Include any additional account or project scope in the external ID when the same registered provider can import several isolated source instances. Provider-less imports are not reliably deduplicated; require a stable provider before depending on duplicate skips or safe retries.

Reject conflicting required identity inside one source group. Do not silently select one of two conversation IDs or merge records from different account scopes.

## Parse and group deterministically

1. Check the byte-size limit before decoding.
2. Enforce structural depth while tokenizing or decoding; do not materialize an arbitrarily nested object first.
3. Decode with a safe loader and validate the supported top-level shape and record-count limit.
4. Convert records into a small internal representation with native identity, parent identity, time, type, content, and source location.
5. Detect exact duplicates. Ignore them only when their canonical source content matches.
6. Isolate groups containing conflicting duplicate identities.
7. Group by source instance and native session or trace fallback.
8. Sort groups, turns, roots, and sibling nodes by provider-defined order followed by stable identity.

Never let input row order decide normalized output. Reversing independent source records should produce the same sessions, node order, and digest.

## Join per-turn traces conservatively

Some providers represent every user turn as a separate trace while attaching a session, thread, or conversation key. Join those traces only when both identity and meaningful order are established.

### Join identity

A valid group key contains:

```text
registered provider + source instance/account + project/tenant when present + conversation key
```

Built-in patterns may expose native fields such as a session ID or conversation ID. The dedicated `kitaru session import --join-on` option accepts an RFC 6901 JSON Pointer. A custom parser may separately accept a dotted or JSON Pointer path through its documented `--params`; do not imply that the dedicated CLI option accepts dotted paths. Treat every configured path as strict: when a record is expected to contain the selected path but does not, yield an isolated failure rather than silently changing the grouping rule.

For implicit provider keys:

- all eligible traces have the same scoped key: join when order is also valid;
- no trace has a key: keep each trace as one session;
- only some traces have the key: keep traces separate and warn;
- traces contain conflicting keys or account scopes: isolate the invalid trace or group.

### Turn order

Use this precedence:

1. A provider-defined integer or monotonic turn sequence.
2. A timestamp only when every joined trace uses the same clock domain, has sufficient precision, and supplies a non-missing value.
3. Another provider-defined order field to resolve equal timestamps.
4. Stable trace ID only as a final deterministic tie-breaker after meaningful order is already established.

Sorting by timestamp and trace ID is not enough when clocks differ, precision creates ties, or timestamps are missing. In those cases, keep traces separate and record an ambiguous-order fallback.

Preserve every turn's original trace ID and root tree. Do not fabricate a parent edge between turn roots merely to make one connected tree.

Before remotely importing joined turns, establish whether the source conversation is closed or can still receive turns. A provider plus session external ID is a snapshot identity, not an append operation. For a growing conversation, wait for a frozen export boundary or use a provider-native immutable segment identity. Do not import an early snapshot under the final conversation identity and expect a later export to extend it; deduplication can preserve the shorter session.

## Build the node tree

Validate topology per native trace before yielding a session:

- reject duplicate node identities with conflicting content;
- reject parent cycles;
- reject a trace with no root;
- preserve multiple roots with a warning when they represent usable evidence;
- promote a node with a missing parent to a root, mark graph completeness false, and warn;
- sort each parent's children deterministically;
- keep parents before children in the emitted tree.

When the source is a DAG, choose one evidence-backed primary parent and preserve other representable parents through `secondary_parent_indexes`. Use the explicitly indexed flat-node form so every referenced parent precedes the child. Record secondary relations in provider metadata only when the installed contract cannot represent them, and mark that topology loss. Do not duplicate a node to simulate several parents.

## Map node semantics

Prefer explicit provider semantics over names:

| Source evidence | Node type and fields |
|---|---|
| Model/generation operation | `llm_call`; requested and served model, `model_provider`, model params, usage, cost |
| Tool execution operation | `tool_call`; canonical tool name, structured inputs and outputs |
| Delegated agent operation | `subagent_call`; subagent identity and available inputs/outputs |
| Other timed operation | `span`; bounded attributes and metadata |

Keep tool activity as a plain span when the export does not establish a callable tool boundary. Record the limitation rather than inventing a replayable tool call.

Keep timestamps timezone-aware. Preserve missing values as `None`; do not invent zero usage, empty payloads, or success timestamps. Convert cost through decimal-safe logic rather than binary floating-point arithmetic where the installed model expects a decimal.

## Derive session status and content

For one trace, derive session status from the authoritative root. For joined turns, derive it from the last root in meaningful turn order:

- explicit failed root: failed session with that root's error;
- completed root: completed session even when a descendant failed and later recovered;
- missing or conflicting root outcome: do not invent failure; warn and make readiness partial.

Always preserve failed descendant status and error on its node.

Use the ordered turn roots to derive session inputs and outputs. A provider-neutral projection may use:

```json
{
  "schema_version": 1,
  "turns": [
    {
      "source_trace_id": "trace-1",
      "inputs": {"message": "..."},
      "outputs": {"message": "..."}
    }
  ]
}
```

Use this projection only when the installed Kitaru contract or importer-owned metadata documents its consumer. Otherwise preserve the simplest source-grounded session inputs and keep turn provenance in metadata. Session outputs normally reflect the last ordered turn. Do not merge independent turn payloads into one invented conversational message.

## Report completeness and replay readiness

Verify where fidelity fields belong in the installed importer contract. The reference design stores them in session metadata. When no first-class fields exist, use an importer-owned namespace or documented common metadata keys rather than passing unsupported model fields.

Useful common metadata:

- provider-specific session key and original trace IDs;
- `source_trace_count`;
- `source_completeness`;
- `normalization_warnings`;
- `replay_readiness`;
- `source_content_digest`.

Completeness describes the export, not whether parsing succeeded. Use source-grounded values such as `full`, `flat`, `partial`, or `unknown`, and explain the provider-specific meaning.

Replay readiness should contain:

```text
level: ready | partial | unavailable
root_inputs_available: boolean
graph_complete: boolean
tool_call_count: integer
replayable_tool_call_count: integer
tool_activity_observable: boolean
reasons: ordered list of concrete limitations
```

Suggested reduction:

- `unavailable`: any required turn root input is missing;
- `ready`: root inputs exist, the graph is complete, tool activity is observably complete, and every observed tool call has a name, input, and output. An explicit source guarantee that no tools ran also satisfies tool observability;
- `partial`: root inputs exist but graph completeness, tool observability, or tool fidelity is incomplete.

This is an evidence report, not a promise that every agent can be replayed. Keep unsupported model behavior, external effects, memory, and tool environment limitations visible.

## Create a stable content digest

Build a canonical projection containing the source instance, native session identity, derived status, ordered turns, and ordered nodes with their parent identities. Exclude volatile importer diagnostics that do not change source meaning.

Serialize as canonical JSON with sorted object keys and compact separators, then hash the UTF-8 bytes with SHA-256. Lists must already be in semantic deterministic order.

The digest must:

- remain stable when independent input records are reordered;
- remain stable for exact duplicate source records;
- change when mapped semantic content, identity, hierarchy, status, or order changes.

Before a remote import, compare the local digest and `source_trace_count` with the existing session metadata for each expected external ID when the installed session-read surface exposes them. A differing digest or larger local turn count is a stale duplicate, not a harmless skip: stop and report that the existing session will not be extended automatically. When the existing metadata cannot be read, treat incremental imports of joined conversations as unsupported and require a frozen snapshot. The digest does not change Kitaru's deduplication key or repair an existing session.
