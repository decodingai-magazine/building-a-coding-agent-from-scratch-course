# Kitaru tour operations

Use this reference before reading or mutating Kitaru state. Inspect the installed CLI schema and discovered MCP tool schemas first; they are authoritative when they differ from this reference.

## Choose a transport

Prefer native Kitaru MCP for bounded reads and writes it exposes in `standard` or `destructive` mode. CLI-only use remains supported. Use the structured CLI for local trace files, built-in waiting, local evaluator code, or investigation creation when it can return the product-owned frontend link.

For agent-facing CLI calls, use `--output json --machine --non-interactive --no-browser` when the installed command exposes those flags. Preserve structured results and exact IDs. Do not use direct REST calls to bypass a missing CLI or MCP operation.

## Prepare CLI and MCP

After the template checkout is verified, inspect the project prerequisites, the active environment, and discovered native Kitaru MCP tools.

- Follow `starter-template.md` and the verified template README's frozen environment command when the project environment is missing or incomplete. Explain that this installs the project-local CLI, worker, MCP entrypoint, importer, adapter, and other locked template dependencies, then ask before changing the environment. Use the README's `uv run kitaru ...` form afterward.
- Treat MCP as preferred, not required. If suitable MCP tools are already configured, use them for bounded operations. If they are absent, say once that the guided tour can continue through the project-local CLI and proceed.
- When the user asks to install MCP, or the CLI cannot complete the next operation, distinguish package installation from host registration. First verify the template's `kitaru-mcp` entrypoint. Then inspect the configuration scope the current host actually loads and follow the official host-specific Kitaru MCP setup. Do not write `.mcp.json` into a newly cloned project unless that is the project scope the host will reopen. Explain and ask before installing packages or changing host configuration.
- After adding or changing `.mcp.json` or equivalent host MCP configuration, state plainly that the user must restart or reload the coding-agent host process or IDE. An already-open task does not hot-load the new server. Preserve the exact template path, selected Kitaru server, completed setup, and next action in a compact checkpoint, then stop MCP-dependent work until the restarted host discovers the tools. Restarting only `kitaru-mcp`, refreshing the Kitaru webpage, or proving that a browser can reach `localhost` is not a substitute. If the user chooses not to restart now and the CLI covers the next operation, say that the tour is continuing through CLI only.
- Do not install or reconfigure MCP solely to replace a working CLI during the short tour. Offer MCP setup after the AHA when it would improve the user's ongoing Kitaru workflow.

Keep network contexts separate when diagnosing local setup. The user's browser, the IDE host, an isolated shell, and the MCP subprocess may not share the same view of `localhost`. Prefer `uv run kitaru status` from the verified template environment for CLI connectivity and discovered MCP tools for MCP connectivity. Do not declare the server unhealthy from a failed `curl` or browser-tool probe inside an isolated environment when the user's host browser reaches it.

## Preserve the selected server

Transport and server selection are separate. CLI and MCP may both address the same selected local or cloud Kitaru server.

1. Read the current server selection and connectivity before following any workspace command from the template README.
2. When the selected server is healthy, keep using it for registration, import, review, cohorts, and evaluation. Do not ask the user to choose again and do not run `login --local`.
3. If the selected server is reachable but lacks permission or a required capability, report that exact blocker. Do not silently switch servers.
4. Only when no usable server is selected, offer the README's isolated local workspace as the tutorial fallback. Explain the state change and ask before starting or selecting it.

## Operation map

| Need | Structured CLI | Native MCP |
|---|---|---|
| Check selected server and worker | `kitaru status`; diagnose with `kitaru doctor` | Use available bounded registry and activity reads |
| Resolve agent/version | `kitaru agent get`; `kitaru agent version get` | `kitaru_registry_read` |
| Read sessions and complete nodes | `kitaru session list`; `kitaru session get`; `kitaru session nodes --include-payloads` | `kitaru_activity_read` session and children |
| Run descriptive evaluators | `kitaru session evaluate ... --wait`; inspect with `kitaru evaluation list` | `kitaru_workflow_start` and bounded activity reads |
| Create ordinary annotation | `kitaru annotation create --session SESSION --value JSON [--selector JSON]` | `kitaru_review_manage`, `create_annotation` |
| List annotations | `kitaru annotation list` with a session filter | `kitaru_review_read`, `list` annotations |
| Create fixed investigation | `kitaru investigation create` with repeated session, question, and highlight options | `kitaru_review_manage`, `create_investigation` |
| Read investigation and worklist | `kitaru investigation get`; `kitaru investigation session list` | `kitaru_review_read` |
| Create cohort/version | `kitaru cohort create`; `kitaru cohort version create` | `kitaru_cohorts_manage` |
| Check and register evaluators | `kitaru evaluator list`; `scaffold`; `test`; `register` | Registry reads; writes require the appropriate evaluator tool and existing script blob |
| Evaluate baseline sessions | `kitaru session evaluate ... --wait` | `kitaru_workflow_start`, evaluation |
| Create and run experiment | `kitaru experiment create`; `kitaru experiment run start ... --wait` | `kitaru_experiments_manage`; `kitaru_workflow_start`, experiment run |

Inspect exact argument names before constructing commands. Prefer exact IDs and versions over display names.

If both `kitaru status` and `kitaru doctor` fail before returning a structured selected-server result, do not inspect credential files or guess at server state. First use a discovered working MCP connection when it identifies a reachable server. Otherwise verify the installed CLI version and explain that no usable server selection can be read. Offer the canonical template README's local-workspace command as a fallback, not as an automatic recovery, and ask before running it. Re-run `status` afterward before registration or import.

## Keep prepared notes separate from verdicts

Create tour observations as ordinary annotations using a session ID, optional selector, and string value. They remain visible on the session independently of the investigation.

The CLI parses `--value` as JSON. For a string annotation, preserve the JSON double quotes inside the shell argument:

```text
kitaru annotation create --session SESSION --value '"Agent observation: The refund exceeded the approval limit."'
```

Do not pass `--value 'Agent observation: ...'`; shell quotes group that text but do not make it a JSON string. Use a JSON encoder rather than hand-escaping text that contains quotes, backslashes, or line breaks.

Do not create an investigation-answer annotation for the reviewer. An investigation answer carries an investigation-session ID and question key, starts a pending investigation, and appears as the answer to that question. The guided tour leaves that optional field to the human and relies on the separately stored whole-session verdict.

Current annotations record an owning account but no typed human, agent, or suggestion provenance. Keep the durable `Agent observation:` prefix. Explain its meaning once in chat and move on.

Before retrying annotation creation, list existing manual annotations for the session and compare selector plus value. General write idempotency is not guaranteed.

## Create and open the review

Every investigation session needs one non-empty question list. Supply one question with one primary highlight for each selected session. Use the question's highlight selector to place the prompt beside its key evidence.

Before creating the investigation, inspect the installed name constraint. Under the current schema, use a machine name containing only letters, digits, hyphens, or underscores and starting and ending with a letter or digit, such as `guided-returns-tour`. Add a short letter-or-digit suffix if a distinct investigation is required. Put the readable tour title in the description instead of passing a spaced title as the name. This validation belongs before the write so the tour does not need a failed create attempt to discover the constraint.

Resolve the review URL in this order:

1. Use `links.review` unchanged from structured investigation creation.
2. Otherwise use the exact verified `dashboard_url`, agent ID, and investigation ID with the documented compatibility route:

```text
DASHBOARD_URL/agents/AGENT_ID/investigations/INVESTIGATION_ID/review
```

Strip one trailing slash from `dashboard_url`. Do not rewrite its path or add trace contents, questions, or judgments to the URL.

If neither route works, preserve the investigation and report its exact ID, the failed or missing URL, and one retry or bug-report action. Do not recreate the prepared frontend tour in chat.

## Resume and evaluate

After frontend review, re-read investigation questions, ordinary annotations, optional answers, and verdicts. Treat verdict coverage as the tour completion signal. Do not infer a missing verdict from a written answer.

Resume an investigation's verdicts only when the current conversation already carries its exact investigation ID or the user explicitly identifies that investigation as their review. A matching tutorial name is not proof that the current reviewer supplied its judgments. Reuse its sessions and exact-match prepared annotations, but create a fresh investigation when reviewer identity is ambiguous.

Before cohort creation, show the exact proposed membership once and obtain confirmation together with the accepted behavior. Prefer an installed configured evaluator when it expresses that behavior. Otherwise keep custom code local until it passes the installed evaluator test command, then register one immutable version.

Evaluation jobs require a live worker. Wait through the supported mechanism once, inspect the terminal job and every resulting evaluation, and keep missing or failed evaluations out of the quality numerator while retaining them in the population accounting.

## Pause three times for frontend review

Use the frontend three times during the normal tour. Pause after each visit and wait for the user to return:

1. Use the investigation review route above so the user can inspect evidence and record verdicts.
2. After cohort creation and baseline evaluation, show the cohort and evaluator pages so the user can see the frozen examples, exact rule, and stored results.
3. After the bounded experiment settles, show its selected run so the user can inspect the candidate sessions and comparison.

Use a product-owned link unchanged when a structured response supplies one. Otherwise, the current managed and self-hosted frontends support these compatibility routes from the verified `dashboard_url`:

```text
DASHBOARD_URL/agents/AGENT_ID/cohorts/COHORT_ID/sessions
DASHBOARD_URL/evaluators/EVALUATOR_ID
DASHBOARD_URL/experiments/EXPERIMENT_ID?run=RUN_NUMBER
```

Strip one trailing slash from `dashboard_url`. Use the parent cohort ID, not the cohort-version ID, and the parent evaluator ID, not the evaluator-version ID. Omit the experiment query only when no run number is available. Do not construct a separate evaluation URL; the cohort and experiment pages present the relevant results in context.

At each visit, lead with one or two direct links, explain in two or three sentences what now exists and what to inspect, and say exactly how to continue, such as “Come back here after you have looked.” Do not add frontend pauses for registration, import, individual annotations, jobs, or other routine objects.

## Run the final experiment safely

Use `kitaru-replay-experiment` for the final candidate comparison. Carry the accepted behavior, exact cohort and evaluator versions, one small candidate change, and the requirement to return to the experiment page after settlement. The replay skill owns adapter checks, the explicit tool policy, a concise explanation of the proposed run, approval for remote writes and model work, execution, and interpretation. Do not create or start the experiment before that approval.
