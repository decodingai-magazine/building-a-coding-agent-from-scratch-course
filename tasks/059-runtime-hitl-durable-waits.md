---
id: 059-runtime-hitl-durable-waits
feature: kitaru-runtime
status: pending
---

# HITL: bridge the decision channel to durable `kitaru.wait()` in flow mode

Tags: `runtime`, `agent`
Depends on: #058
Blocks: #062

This task implements ADR-0008 §3: in **headless flow mode**, decode's decision surfaces pause the
execution on a durable Kitaru wait and resume out-of-band, instead of asking a human at a keyboard.
decode already routes its decisions through two resolver fields that both ride the single
`DecisionChannel` (`agent/deps.py:81`): `resolve_user_question` (used by `ask_user`
`tools/askuser.py:71` AND `exit_plan_mode` `tools/orchestration.py:82`) and `resolve_permission`
(write/bash gates). In flow mode, **both** become bridges to flow-scope `kitaru.wait()`. Interactive
mode keeps the console resolvers untouched.

## Scope

Verify exact adapter signatures against the installed SDK + context7 `/kitaru/adapters/pydantic-ai.md`
and `/kitaru/guides/wait-and-resume.md` first (pre-1.0).

- **Question bridge (`resolve_user_question`)** — in the runtime deps (task 058), replace the headless
  `deny_user_question_resolver` with a **flow-mode resolver** that calls the adapter's
  `wait_for_input(question=…, name=…)` (preferred over the `@hitl_tool(schema=…)` decorator — the
  schema does not round-trip on the local stack today) and coerces the result to `str`. This makes
  `ask_user` and `exit_plan_mode` pause the flow on a durable wait. Because `resolve_user_question`
  is `async` while `wait_for_input`/`kitaru.wait` are sync flow-scope calls, bridge the sync wait
  from the async resolver (e.g. `anyio.to_thread` or the adapter's
  `allow_sync_tool_body_waits=True`), confirmed against the SDK.
- **Approval bridge (`resolve_permission`)** — run the headless flow under a **gating** mode (e.g.
  `default`/`edit` instead of 058's `bypass`) so a mutating tool (`write`/`bash`) raises
  `ApprovalRequired`. The flow drives the resulting `DeferredToolRequests` (the same shape the
  interactive `Runner` handles) by resolving each request through a `kitaru.wait()` that returns an
  allow/deny verdict, then resumes `run_sync` with the results. The allow/deny answer is a
  bool-ish wait value (operator passes `--value 'true'` / `'false'`).
- **Wait naming & timeout** — give each wait a stable name (the adapter's
  `<tool>:<call_index>:<sha1(question)[:8]>` scheme) so **replay reuses a prior answer** instead of
  re-asking. Use `settings.runtime_wait_timeout_s` (from 057) for the poll/timeout.
- **Checkpoint opt-out** — when `settings.runtime_checkpoint_strategy == "calls"`, pass
  `tool_checkpoint_config_by_name={ASK_USER_TOOL_NAME: False, EXIT_PLAN_MODE_TOOL_NAME: False}` to
  `KitaruAgent` (the adapter rule: waits live at flow scope, not inside a per-tool checkpoint). Under
  the `"turn"` default this is a no-op.
- **Out-of-band resolution** — document that a paused flow is inspected with `kitaru executions list`
  / `get` and resolved with `kitaru executions input <execution_id> --value '…'`. Add a HITL row to
  the AGENTS.md **Testing E2E** table.
- **Interactive mode is untouched** — the TUI console resolvers and the `DecisionChannel`
  single-flight behavior are unchanged; only the runtime-deps resolvers differ.

## Acceptance criteria

- [ ] In flow mode, `ask_user` and `exit_plan_mode` resolve through `wait_for_input(...)` /
      `kitaru.wait(...)` (not the deny-resolver); a hermetic test (local stack, no network/server)
      drives a scripted agent that calls `ask_user`, asserts the flow pauses on a named wait, injects
      an answer programmatically (the test's local input path), and asserts the answer becomes the
      tool result.
- [ ] In a gating mode, a `write`/`bash` call pauses the flow on a durable approval wait; an injected
      allow verdict lets the tool run, a deny verdict feeds the denial back to the model (mirrors the
      interactive gate outcome). Unit/integration-tested with the runtime seam, no network/server.
- [ ] Each wait has a **stable name**; a replay of the execution reuses the prior answer and does **not**
      re-prompt (asserted, mirroring the Kitaru HITL replay behavior).
- [ ] The async-resolver → sync-`wait` bridge is verified against the installed adapter and documented
      (this is ADR-0008 §Consequences "Honest risk" on async tool surfaces vs `run_sync` — resolved
      here); no deadlock, no event-loop error.
- [ ] Under `runtime_checkpoint_strategy="calls"`, the waiting tools are exempted from per-tool
      checkpoints (`tool_checkpoint_config_by_name`); under `"turn"` no opt-out is needed; both paths
      tested or asserted.
- [ ] Interactive TUI behavior is byte-unchanged (the console resolvers + DecisionChannel single-flight
      still pass their existing tests).
- [ ] `make ci` green, 0 warnings.

## User stories

### Story: An operator approves a destructive step hours later
1. A `decode run` task reaches a `write` that needs approval; the flow pauses on a durable wait and
   the process can exit.
2. Hours later the operator runs `kitaru executions list`, finds the waiting execution, and runs
   `kitaru executions input <id> --value 'true'`.
3. The flow resumes from exactly that point, the write runs, and the task finishes — no work before
   the pause is repeated.

### Story: The agent asks a question headlessly and an operator answers from a CLI
1. A headless task calls `ask_user("which environment should I target?")`; the flow pauses on a named
   wait.
2. The operator runs `kitaru executions input <id> --value '"staging"'`.
3. `ask_user` returns `"staging"` to the model and the turn continues.

### Story: Replaying a resolved task does not re-ask
1. An operator replays a finished HITL execution (`kitaru executions replay <id> --from default`).
2. The earlier answer is served from the saved wait record; the operator is **not** prompted again.

## Out of scope
- `sleep` as a durable timer (task 060).
- Credentials proxy (task 061).
- A web/dashboard approval surface beyond the CLI/REST that Kitaru already provides.
- Escaping/sanitizing operator input (Kitaru tutorial caveat) — note it but defer hardening to step 12.

## Log
