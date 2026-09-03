# Kitaru operations

Use this reference before reading or mutating Kitaru investigation state. Treat the installed Kitaru schema and discovered MCP tools as authoritative when they differ from this reference.

## Contents

- [Transport policy](#transport-policy)
- [Operation map](#operation-map)
- [Resource contracts](#resource-contracts)
- [Setup and transport readiness](#setup-and-transport-readiness)
- [Frontend bridge and failure](#frontend-bridge-and-failure)
- [Known product boundaries](#known-product-boundaries)
- [Mutation and retry rules](#mutation-and-retry-rules)

## Transport policy

Prefer native MCP when an exact bounded operation exists. CLI-only operation is supported. Use the CLI when the operation requires a local file or evaluator script, benefits from built-in wait or watch behavior, or the relevant MCP tool is unavailable.

For agent-facing CLI calls, use these flags when the installed command schema exposes them:

```text
--output json --machine --non-interactive --no-browser
```

The deliberate frontend handoff may present or open one resolved URL. Do not open a browser for ordinary CLI calls.

Before relying on exact syntax:

1. inspect the installed `kitaru schema` output or command help;
2. inspect discovered MCP tools and their input schemas when MCP is available;
3. prefer exact IDs and versions over names;
4. preserve JSON results and carry returned IDs into the next call.

Do not use direct REST calls to fill a missing CLI or MCP contract.

## Setup and transport readiness

Treat setup as part of reaching the first usable session, not as an unexplained prerequisite:

1. Treat MCP as preferred, not required. Inventory discovered native MCP tools first. If no native tools are discovered, distinguish among a missing `kitaru[mcp]` installation, absent host configuration, a server left in read-only mode, and host configuration added after the coding-agent process started.
2. MCP write operations require `standard` or `destructive` mode. Modes are cumulative: a `destructive`-mode server also exposes every `standard` write tool, so use its ordinary write tools normally. Reserve destructive actions for explicitly requested deletes or cancellations.
3. Check whether `kitaru` is available in the active project environment only when the next operation and its handoff cannot be completed through the discovered MCP tools. For an immediate frontend handoff, prefer structured CLI creation when the CLI is already available because its result can return the product-owned review link. If the CLI is absent but `standard`-mode MCP can create the investigation, create it once through MCP and use the verified `dashboard_url` compatibility route; do not install the CLI or recreate the investigation solely to obtain a link. If neither transport can complete the operation and handoff, inspect the project's current setup instructions and the official Kitaru installation guide. Do not infer a package manager, virtual environment, or install command from repository layout alone. Explain and obtain approval before changing the project environment.
4. Run `kitaru status` under the same condition to resolve the selected server, credentials, compatibility, dashboard URL, and worker readiness. Use `kitaru doctor` when a check fails or its cause is unclear.
5. A host normally discovers new MCP configuration only after restart. Before restart, preserve the current repository, agent/version, trace source, completed setup, and next action in a compact checkpoint. Tell the user how to resume with the host's documented mechanism rather than assuming one command works everywhere.
6. When CLI covers the next operation, state that fallback in one sentence and continue. Do not make transport selection another user decision unless the missing MCP capability changes the result or blocks the next operation.

## Operation map

| Need | Structured CLI | Native MCP |
|---|---|---|
| Check connection and worker readiness | `kitaru status`; use `kitaru doctor` for independent diagnostics | No single equivalent; use bounded reads only after the server is configured |
| Resolve agent or version | `kitaru agent get AGENT`; `kitaru agent version get AGENT@VERSION` | `kitaru_registry_read`, `get` or `get_version`, kind `agent` |
| Resolve installed importers | `kitaru importer list`; inspect an exact match with `kitaru importer get IMPORTER` | `kitaru_registry_read`, `list` or `get`, kind `importer` |
| Import a local trace payload | `kitaru session import FILE --importer IMPORTER@VERSION --agent AGENT@VERSION`, optionally `--join-on JSON_POINTER`, `--tag TAG`, and `--wait`; current tag application requires waiting | `kitaru_session_import` uses an existing payload blob and exact importer and agent versions; it cannot read a local file |
| Record a new agent run | Use the repository's verified Kitaru-integrated agent entrypoint | No generic MCP operation; running the agent may execute tools or mutate external state |
| List eligible sessions | `kitaru session list --agent AGENT` with bounded filters and pagination; use `--tag TAG` to find a marked smoke population, then subtract its exact IDs | `kitaru_activity_read`, `list`, kind `session` |
| Read a complete session | `kitaru session get SESSION`; `kitaru session nodes SESSION --include-payloads` with pagination | `kitaru_activity_read`, `get` session then `list_children` session nodes with payloads |
| Create investigation and fixed worklist | `kitaru investigation create NAME --agent AGENT --session UUID --session-question 'UUID:KEY=QUESTION'`, repeating session and question options; optionally add `--session-highlights 'UUID:KEY=JSON_ARRAY'`; structured output includes `links.review` when the server exposes a supported dashboard | `kitaru_review_manage`, `create_investigation` with ordered session inputs, each carrying its questions and optional highlights; use only when an immediate frontend handoff is not required or the CLI is unavailable, because MCP does not yet return the review link |
| Read or resume investigation | `kitaru investigation get ID`; `kitaru investigation session list ID` | `kitaru_review_read`, `get` investigation then `list_sessions` |
| Set linked-session verdict | `kitaru investigation session verdict INVESTIGATION SESSION acceptable|problematic|uncertain`; the CLI cannot clear a verdict | `kitaru_review_manage`, `set_session_verdict`; use null only to clear an existing verdict deliberately |
| Complete investigation | `kitaru investigation update INVESTIGATION --status completed` | `kitaru_review_manage`, `update_investigation` with `status=completed` |
| Write whole-session or node annotation | `kitaru annotation create --session SESSION --value JSON [--selector JSON]` | `kitaru_review_manage`, `create_annotation` |
| Answer investigation question | `kitaru annotation create --investigation-session ID --question-key KEY --value JSON [--selector JSON]` | `kitaru_review_manage`, `answer_question` |
| Read or revise annotation | `kitaru annotation list|get|update` | `kitaru_review_read`, `list` or `get`; `kitaru_review_manage`, `update_annotation` |
| Create cohort snapshot | `kitaru cohort create NAME --agent AGENT --session SESSION...`; later `kitaru cohort version create` | `kitaru_cohorts_manage`, `create` or `create_version` |
| Scaffold and load-check evaluator file | `kitaru evaluator scaffold NAME`; `kitaru evaluator test PATH --entrypoint NAME` | Local coding-agent work, no MCP equivalent; the CLI test does not invoke the evaluator against sessions |
| Register immutable evaluator version | `kitaru evaluator register NAME --script PATH --entrypoint NAME`; later `kitaru evaluator version register EVALUATOR` | `kitaru_evaluators_manage`; MCP requires an existing script blob or exact package pin |
| Start evaluation batch | `kitaru session evaluate ... --evaluator EVALUATOR@VERSION`, optionally `--wait` | `kitaru_workflow_start`, `evaluation` |
| Inspect evaluation work | `kitaru job get|watch`; `kitaru evaluation list|get` | `kitaru_activity_read`, job or evaluation and bounded children |

## Resource contracts

### MCP bounds

- MCP list operations return one page of at most 100 items. Continue through opaque cursors rather than requesting an unbounded population.
- MCP investigation creation accepts at most 100 sessions. Every session input needs a non-empty list of uniquely keyed questions. Split larger review rounds into explicit follow-up investigations.
- MCP evaluation starts accept at most 100 session IDs, 100 evaluator selections, and 100 total session/evaluator pairs. Split a larger matrix into separately identified batches.

### Pre-investigation setup

- Use `kitaru status` first when server selection, credentials, compatibility, or live-worker readiness is uncertain. Use `kitaru doctor` when one of those checks fails or the cause is unclear.
- Prefer an exact agent version when recording or importing a session. If a session has no `agent_version_id`, report that provenance gap and reconcile it from other evidence only when the program revision is still unambiguous.
- Inspect the repository before running an agent to record a session. Explain the inputs, credentials, tool effects, and external mutations that may occur, and obtain any approval required by those effects.
- Use the CLI for a local import payload because MCP never reads local files. Use MCP import only when the payload already exists as a Kitaru blob.
- Import creates a job and does not make sessions available synchronously unless the CLI waits successfully. Inspect the job and resulting sessions before starting an investigation.
- In a Python SDK context, `client.sessions.get_with_nodes(...)` returns one session with every node in one unpaginated response. This does not replace the bounded CLI or MCP paths for an agent host.
- Session filters can select sessions produced by one exact experiment run. They do not by themselves express every non-experiment session, so define broader replay exclusions explicitly instead of assuming an inverse filter exists.
- After setup, resolve the exact agent version and session population again. Do not rely on an earlier name-only lookup.

### Investigation

- Create with one agent ID, name, optional description, and fixed ordered unique sessions. Every linked session carries its own non-empty fixed question list and optional per-question highlights.
- Treat the investigation ID as durable identity. Do not assume names are unique.
- Status moves forward through `pending`, `in_progress`, and `completed`.
- The first investigation answer starts the investigation. Session verdicts do not start or complete it.
- Completion is an explicit investigation update. The server does not require every question to have an answer or every session to have a verdict, so inspect both coverage dimensions before completing it.
- Update can change name, description, or status.
- The response exposes metadata, but public create and update do not write that metadata.
- There is no cancel transition.
- Deletion is destructive and removes linked investigation sessions and investigation answers.

### Investigation session

- Verdict is null, `acceptable`, `problematic`, or `uncertain`. A null verdict means only that no verdict is recorded; question-answer annotations determine review coverage separately.
- Sessions, order, per-session questions, and per-question highlights are fixed at investigation creation.
- A highlight contains a prose description and selector into canonical session or node evidence. Conversational summaries are not a separately persisted investigation-view object.
- Current operations cannot append, reorder, or replace the fixed worklist. Create a follow-up investigation for another round.

### Annotation

- A manual annotation uses `session_id`, optional selector, and JSON value.
- An investigation answer uses `investigation_session_id`, stable `question_key`, optional selector, and JSON value. The session ID is derived from the linked session.
- The CLI parses `--value` as JSON. For a string annotation, preserve the JSON double quotes inside the shell argument: `--value '"Agent observation: The refund exceeded the approval limit."'`. Do not pass `--value 'Agent observation: ...'`; shell quotes group that text but do not make it a JSON string. Use a JSON encoder rather than hand-escaping text that contains quotes, backslashes, or line breaks.
- Repeating an investigation answer for the same investigation-session and question key creates another annotation row. It does not replace or upsert the earlier answer.
- A selector contains an optional node ID, an optional RFC 6901 JSON Pointer into that node or the session response, and an optional character span. A span requires a path, and a supplied node must belong to the session.
- A null selector targets the whole session.
- Annotation update replaces only the value of one exact annotation ID. It cannot change the selector, question key, or investigation link. Deletion is a separate exact-ID destructive operation.
- Manual annotations are independent of an investigation. Investigation answers are deleted with their investigation.

## Frontend bridge and failure

Resolve the frontend URL in this order:

1. Read `links.review` from the structured `kitaru investigation create` result. Use it unchanged when it exists.
2. If creation already happened through MCP, an older CLI, or another client that did not return a link, reuse the `dashboard_url` already verified during readiness. If it is not available, read it once from `kitaru status` or equivalent product configuration. Then append the compatibility route below using the exact agent and investigation IDs.

```text
DASHBOARD_URL/agents/AGENT_ID/investigations/INVESTIGATION_ID/review
```

Current managed and self-hosted servers report the Kitaru UI base directly as `dashboard_url`; do not translate `/workspaces/` into `/kitaru-workspaces/` or otherwise rewrite its path. Strip one trailing slash before appending the route. The route carries agent and investigation identifiers only. Do not add sessions, questions, annotations, judgments, or trace contents as path or query data. Treat this construction as a compatibility path, not a substitute for a returned `links.review` contract.

Present the resolved URL as the first-line clickable action, say whether it opened automatically, explain investigation answer versus whole-session verdict versus independent manual annotation in one sentence, and pause. If the environment provides an ordinary open-URL action and the user asks to open the page, use it. Do not use Computer Use, browser automation, tab control, or a browser-specific integration to navigate or annotate on the user's behalf.

When no returned or documented URL reaches the investigation, preserve the investigation and stop. Report:

- the exact investigation and agent IDs;
- the attempted URL, unrecognized dashboard shape, or missing `dashboard_url`;
- that frontend review is the intended interaction and the skill will not recreate it in chat;
- one concise retry or product-bug action.

Low-level CLI and MCP annotation operations remain available when a user explicitly requests agent-facing annotation work. They are not an automatic fallback for a broken frontend handoff.

## Known product boundaries

- Current structured CLI investigation creation returns a direct review URL in `links.review` when server information exposes a supported dashboard. Investigation creation succeeds even if the follow-up server-information request fails; in that case the CLI returns the investigation, no review link, and a warning.
- Native MCP investigation creation still returns the investigation resource without a direct review URL. Prefer CLI creation for an immediate frontend handoff; otherwise use the documented compatibility route from the exact `dashboard_url`, agent ID, and investigation ID.
- A missing or invalid documented frontend route blocks the intended human review experience. There is no supported automatic in-chat substitute in this skill.
- No public write path exists for the structured agent-context brief or accepted-behavior record.
- No operation appends sessions to an active investigation.
- No persisted skipped-session state exists.
- No first-class suggestion, suggestion disposition, or accepted-behavior resource exists.
- No conditional-write revision, edit history, or undo contract exists for review writes.
- Evaluator versions do not carry a typed link to the accepted behavior and annotations that produced them.
- Server-enforced discovery, development, and test label isolation is unresolved.

Do not improvise around these boundaries. Preserve what can be preserved in Kitaru, keep provisional context visible in chat, and stop honestly when the missing contract matters to the user's next step.

## Mutation and retry rules

- Explain the exact remote object or compute job before creating it.
- Show and confirm exact cohort membership once, inline, before creation. Treat an evaluator card that preserves an already accepted behavior as a checksum; require a new decision only when it changes a boundary, permitted equivalence, or missing-evidence rule.
- After a dropped response, read current state before retrying. General request idempotency is not guaranteed, and retrying answer creation can append a duplicate annotation.
- Create a second answer for the same investigation-session and question key only when another annotation is intentional. Correct a known answer through exact-ID update.
- Never use destructive deletion as investigation cancellation.
- Never expose secret values, prompts, trace contents, local paths, or session identifiers through analytics.
