---
id: 009-tools-tasks
feature: m1-vanilla-agent
status: pending
---

# Tools: tasks (TodoWrite-style)

## Scope
An in-memory to-do list the model maintains within a session, rendered by the TUI.

## Acceptance criteria
- [ ] `tools/tasks.py` exposes `todo_write` which replaces/updates the per-run task store on `ctx.deps`.
- [ ] `entities/task.py` defines `Task` (id, content, `status: pending|in_progress|completed`), validated.
- [ ] The TUI renders the checklist via a `TaskListUpdated` event.
- [ ] Gated like any tool in v1 (ask-everything).

## Out of scope
- Cross-session persistence; claude-code's `TaskRegistry`/background-job state machine.

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. In-memory per-session; pi has no task tool, claude-code's is richer — minimal here.
