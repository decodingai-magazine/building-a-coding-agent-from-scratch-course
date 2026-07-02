---
id: future-hitl-replay-answer-reuse
feature: runtime-replay
status: future
---

# HITL replay with answer-reuse (deployed-stack milestone)

Tags: `runtime`, `replay`, `hitl`, `future`
Refs: ADR-0010 §7, ADR-0008 §3, `tasks/070-runtime-replay-command.md`

> **Parking lot — not part of the active 067+ sequence.** `status: future`; do not schedule until the
> deployed Kitaru stack milestone (deploy/step 12). No `status: pending`.

## Why deferred

Replaying a HITL run (`run_agent_task_hitl`) on the **local** in-process Kitaru stack **re-asks every**
`write`/`edit`/`bash` approval and every `ask_user`/`exit_plan_mode` question — it does not reuse the
recorded answers. This is confirmed twice over:

- Kitaru docs ("Replay and Overrides"): *"Replay does not support overriding or pre-populating wait
  results. If a replayed execution reaches a `wait()` … that wait behaves like any new wait and must
  be resolved through the normal wait input flow."*
- decode's own capstone: `test_replay_re_asks_a_wait_on_the_local_stack` proves the local stack
  re-creates the `ask_user` wait under the same deterministic `_hitl_wait_name`, rather than serving
  the saved answer from cache (ADR-0008 §3 amendment 5). True answer-reuse needs a **deployed** stack.

So `decode replay` (task 070) is deliberately **bypass-only**, and this HITL slice waits for a
deployed Kitaru stack where a resolved wait is durably keyed and reusable on replay.

## Scope (to pick up when Kitaru matures)

- Confirm on the deployed stack whether a resolved wait replays from its saved record (keyed by the
  deterministic `_hitl_wait_name` for `ask_user`/`exit_plan_mode`, and by the adapter's tool-call-id
  name for native approvals).
- Extend `decode replay` to accept HITL executions (drop the bypass-only guard) once answer-reuse is
  real; otherwise surface which waits will re-ask.
- Add a deployed-stack integration test proving a HITL replay reuses recorded answers without
  re-prompting (the deployed analogue of `test_replay_re_asks_a_wait_on_the_local_stack`).

## Acceptance criteria (draft — refine at pickup)
- [ ] On a deployed Kitaru stack, replaying a completed HITL run reuses each recorded wait answer
      (no re-ask) — proven by a test.
- [ ] `decode replay` accepts a HITL exec_id on the deployed stack; the bypass-only friendly-error
      guard becomes a deployed-vs-local capability check.

## Out of scope
- Anything on the local in-process stack (it structurally cannot reuse wait answers).
- Per-tool-call output/raise mocks (separate Kitaru roadmap item).

## Log
