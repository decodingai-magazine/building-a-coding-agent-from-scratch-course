# Python adapters

Use this reference after the project route identifies a Python agent entrypoint.
Verify every symbol against the project's installed Kitaru and framework
versions before writing code.

## Contents

- [Establish the installed contract](#establish-the-installed-contract)
- [Use the current reference carefully](#use-the-current-reference-carefully)
- [Choose a safe Python boundary](#choose-a-safe-python-boundary)
- [Map one invocation to Kitaru](#map-one-invocation-to-kitaru)
- [Resolve input and replay context](#resolve-input-and-replay-context)
- [Record model and tool work](#record-model-and-tool-work)
- [Apply replay without overclaiming](#apply-replay-without-overclaiming)
- [Project data before serialization](#project-data-before-serialization)
- [Test the Python adapter](#test-the-python-adapter)

## Establish the installed contract

Inspect the project's lockfile and environment. Record the exact versions of
Python, Kitaru, the framework, the model-provider package, and the test runner.

Resolve imports and signatures from the installed package rather than assuming
the latest source tree. First identify which public recording boundary the
installed package exposes. Do not infer the API from the version string alone.
Current Kitaru releases do not export the historical `flow`, `checkpoint`, or
`log` decorators. A separately built artifact with the same version string may
expose different symbols, so record its exact source or revision before relying
on it.

Only when using the session-node path, verify whether the installed package
exposes:

- `KitaruAPIClient` from `kitaru.client`;
- `client.sessions.create(...)`;
- `client.sessions.ingest_nodes(...)`;
- `client.sessions.update(...)`;
- `client.sessions.list_nodes(...)` or `iter_nodes(...)` for inspection;
- `client.replays.get(...)` and `client.replays.tool_lookup(...)` when replay is
  requested;
- the v1 session, session-node, replay, replay-config, and task request models
  used by those methods;
- `close()` or async context-manager behavior for client ownership.

Inspect the actual request-model fields and enum values. Do not reproduce them
from this reference. If the installed release lacks a required method or model,
propose the exact released dependency change and ask before installing it. Stop
when no released contract supplies the needed operation.

Also inspect constructor and environment precedence. Current source uses an
explicit base URL before `KITARU_API_URL` and stored configuration, and an
explicit API key before `KITARU_API_TOKEN`, `KITARU_API_KEY`, or stored
credentials. Treat that only as a current-source observation until the installed
package confirms it. Never copy tokens into code, fixtures, logs, or reports.

## Use the current reference carefully

The checked `develop` source at commit
`3675d90e02a690f2bd9a3ff43eba576f0a813515` contains independently versioned
adapter distributions:

| Framework | Distribution | Checked source | Advertised capability |
|---|---|---|---|
| PydanticAI | `kitaru-pydantic-ai` | `plugins/packages/pydantic-ai/` | Recording and replay |
| LangGraph, LangChain, and Deep Agents | `kitaru-langgraph` | `plugins/packages/langgraph/` | Recording and replay |
| OpenAI Agents SDK | `kitaru-openai-agents` | `plugins/packages/openai-agents/` | Recording and restricted replay |

Their tests live under `plugins/tests/adapters/pydantic_ai/`,
`plugins/tests/adapters/langgraph/`, and
`plugins/tests/adapters/openai_agents/`. Adapter distributions are installed by
agent projects rather than bundled into the core Kitaru wheel. They are
published independently from Kitaru core, so inspect PyPI and the project's
lockfile for the current version. This inventory describes the checked source
tree, not what is installed in the user's project.

Check the project's installed distributions and current Kitaru documentation
before designing a custom adapter. When one of these packages supports the
installed framework version and requested invocation mode, use it instead of
building a duplicate. Otherwise record the exact missing boundary. Kitaru still
exposes no general adapter base in the core wheel, so do not import repository
source into a user project or copy a package wholesale.

Re-check the source revision before relying on implementation details. If it
cannot be resolved, report the reference as unavailable and rely only on
installed public symbols and types. When the relevant behavior is verified,
extract only these design lessons:

- compose through a public framework wrapper or capability;
- create fresh run state and a client for each invocation;
- record one in-progress session and root before agent work;
- buffer child nodes while preserving stable indexes;
- record input, output, and system-prompt JSON Pointer selectors plus visible
  reasoning only when the framework exposes them;
- replace the root and then update the session at the terminal state;
- preserve the original agent exception when recording failure follows it;
- close the per-run client on setup, success, and failure;
- keep concurrent invocations through one wrapper isolated;
- distinguish local tools from provider-native calls that cannot be mocked.

The reference's broad Pydantic serialization is not a reusable privacy policy.
Define a narrower project-specific projection.

## Choose a safe Python boundary

The session-node Python client shape is async. Require a documented async
framework entrypoint only when the adapter depends on that shape.

When a separately verified installed Kitaru artifact exposes a documented
synchronous recording boundary, a coarser whole-invocation adapter may use that
public contract if it meets the user's goal and preserves the framework's exact
return and exception behavior. Report only that observed boundary. Do not infer
it from `0.21.0`, and do not translate it into session-node, child-event, or
replay claims.

When the application calls only a synchronous framework API, do not add
`asyncio.run`, start a private event loop, block an existing loop, or move work
to an ad hoc thread merely to reach Kitaru. Those bridges change cancellation,
thread-local context, exception propagation, and shutdown behavior.

Offer one of these outcomes:

1. adapt an existing public async entrypoint and leave sync mode unsupported;
2. use a released, documented synchronous Kitaru contract when the installed
   package exposes one and its claimed boundary can be tested;
3. stop with the exact async or sync contract that is missing.

Do not infer that an exported sync client or decorator is suitable merely from
its name or from an older `0.21.0` installation. Inspect its installed signature
and behavior, then limit the adapter and report to the operations that public
contract actually supports.

## Map one invocation to Kitaru

Use this session-node mapping only when the installed public client exposes the
required methods and the user's goal needs this granularity. For a synchronous
decorator path, wrap only the whole invocation, preserve its return or
exception, and do not claim the state, nodes, or replay behavior below.

Keep one private run-state object per invocation. It normally needs:

```text
client
session_id
task_id and replay_id when used
effective input and framework-native prompt/history
replay spec and override
started_at
next node index
pending node buffer and ordering lock
latest observable parent indexes
first failure
finished and closed flags
```

Use the installed request models to perform this sequence:

1. Create a session with `origin=recorded` or `origin=replay`, the bounded
   effective input, framework identifier, adapter version, and in-progress
   state when supported.
2. Store the returned session ID immediately.
3. Ingest root index `0`, `parent_index=None`, type `span`, status in progress.
4. Allocate increasing child indexes. Write parents before children.
5. Record model and tool nodes only from events the public framework API exposes.
6. At success, await pending writes, upsert the root at index `0` as completed,
   then update the session as completed with bounded output and end time.
7. At failure, preserve the primary error, best-effort upsert a failed root and
   update a failed session, then close owned resources.

The node-ingestion API replaces an existing index whole. Use that property for
the root transition and idempotent retry, but never allocate a different index
for a retried event.

If session creation succeeds but root initialization fails, attempt to mark the
session failed. If a child batch fails, do not discard already confirmed nodes
or claim the trace is complete.

## Resolve input and replay context

Use the installed Kitaru worker and replay conventions. Do not transplant
precedence rules from TypeScript or from a stale draft.

For each source that exists in the installed release, test the precedence among:

- the caller's ordinary prompt or structured input;
- task-bound input and task identity;
- environment-provided serialized task input;
- replay identity and replay override;
- explicitly configured agent and agent-version identity.

Keep two values when necessary:

- the bounded value recorded as session input;
- the framework-native value passed to the agent.

Imported conversational input requires a framework-specific reconstruction.
Only synthesize message history when the importer schema, turn ordering, roles,
and missing-field behavior are known and tested. Preserve the final user turn as
the active prompt and earlier complete turns as history when that matches the
framework's public API. Otherwise support an input rerun only or stop.

## Record model and tool work

For model events, capture only fields the public hook proves:

- bounded request messages or prompt projection;
- requested and effective model identifiers;
- allowlisted model settings;
- bounded response projection;
- token usage and cost when the framework exposes them reliably;
- provider identity and public error;
- timestamps and stable external call ID when available.

For tool events, prefer validated framework arguments. Record the exact public
tool name, stable call ID, bounded inputs, bounded output or public failure, and
the model-call parent when it is known.

Treat these as separate visibility classes:

- local registered tools whose execution hook the adapter wraps;
- MCP calls exposed through a public tool hook;
- provider-native tools reported by the model response;
- hidden or framework-internal operations.

Do not fabricate results for an unpaired provider-native call. Record it as
opaque or failed evidence if the result is not public. Do not claim replay
control over a tool the adapter cannot intercept before execution.

## Apply replay without overclaiming

Classify replay before implementing it. An input rerun may apply prompt, system
prompt, model, and model-setting overrides without reproducing intermediate
execution.

For tool policies:

- select per-tool policy by the exact observable tool identity;
- canonicalize the framework's validated JSON arguments before computing a key;
- implement static exact or subset matching only with explicit tested semantics;
- treat missing history keys according to the declared miss policy;
- require separate approval before live passthrough of an effectful tool;
- report provider-native and hidden tools as unsupported unless a public
  pre-execution hook controls them.

The current public history lookup request carries a tool name, a cache key, and
an optional zero-based `occurrence`. The response exposes only `found` and
`result`. The server resolves the lookup by scope:

- Baseline scope with `occurrence` n: the nth match in baseline order, a miss
  past the last match.
- Any scope without `occurrence`: the newest match in scope.
- Any other scope with `occurrence` set: rejected.

For baseline scope, keep one counter per cache key, send it as `occurrence`,
and increment it only on a hit. Repeated identical calls then replay their own
recorded results in order, and extra calls follow the declared miss policy.
Never send `occurrence` for other scopes. When the installed release lacks
`occurrence` or the scope is not baseline, claim history replay only when the
adapter can preflight the complete baseline and prove every required key unique
and paired with a result. Otherwise disable history replay rather than
accepting an arbitrary match.

Run the baseline-admissibility check from
[validation and reporting](validation-and-reporting.md) before any effectful
replay. A terminal imported session can still be incomplete if its source never
observed a hidden call.

## Project data before serialization

Define explicit projectors for each recorded shape. Use allowlists for model
settings and metadata. Bound recursion depth, collection size, string length,
and total serialized size. Decide how to represent unsupported objects rather
than relying on `str(value)`, which can expose credentials or unstable memory
addresses.

Always exclude authorization headers, cookies, API keys, access and refresh
tokens, client secrets, credential objects, raw HTTP request objects, and
provider configuration that may contain secrets. Apply the same projection to
success, failure, logging, and test diagnostics.

Use sentinel secrets in tests and scan the recorded session, exceptions, logs,
and snapshots for them.

## Test the Python adapter

Use the project's test runner, a deterministic fake model, inert local tools,
and fakes for the exact installed public Kitaru boundary. Fake and inspect the
client and request models for a session-node adapter, or the installed
decorators for a coarser synchronous adapter. Do not require provider
credentials or a remote Kitaru server.

For every adapter, cover exact application return and exception behavior, the
declared availability policy, bounded projection, and credential sentinels.
For a session-node adapter, also cover at least:

- session creation and root initialization;
- normal completion and exact application return value;
- application failure followed by recording and cleanup failures;
- session creation failure and root initialization failure;
- child-ingestion failure after confirmed nodes;
- the documented fail-fast or best-effort availability policy;
- concurrent invocations through one wrapper;
- correct parentage and stable monotonic node indexes;
- task and replay input precedence;
- prompt, system-prompt, model, and model-setting overrides when claimed;
- static and history policy hits and every miss policy;
- ambiguous or incomplete history refusal;
- local, MCP, provider-native, and hidden tool visibility;
- bounded projection and credential sentinels;
- fully consumed, failed, canceled, and abandoned streams when streaming is
  claimed;
- sync-only refusal when no safe public contract exists.

For a session-node path, inspect the fake client's stored session and node tree
after each lifecycle case. For a decorator path, inspect calls through the
installed public boundary and assert that the report makes no unsupported node
or replay claim. A passing framework return-value assertion alone does not prove
the adapter recorded correct evidence.
