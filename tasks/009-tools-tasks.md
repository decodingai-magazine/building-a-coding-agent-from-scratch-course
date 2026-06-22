---
id: 009-tools-tasks
feature: m1-vanilla-agent
status: done
---

# Tools: tasks (TodoWrite-style)

## Scope
An in-memory to-do list the model maintains within a session, rendered by the TUI.

## Acceptance criteria
- [x] `tools/tasks.py` exposes `todo_write` which replaces/updates the per-run task store on `ctx.deps`.
- [x] `entities/task.py` defines `Task` (id, content, `status: pending|in_progress|completed`), validated.
- [x] The TUI renders the checklist via a `TaskListUpdated` event.
- [x] Gated like any tool in v1 (ask-everything).

## Out of scope
- Cross-session persistence; claude-code's `TaskRegistry`/background-job state machine.

## Log
### [PA] 2026-06-19 18:40 — Grooming
Groomed from the approved M1 plan. In-memory per-session; pi has no task tool, claude-code's is richer — minimal here.

### [SWE] 2026-06-19 23:40 — Implementation

**Files modified**
- `src/decode/entities/task.py` — new `Task` model (frozen+slotted; `id`/`content`/`status`), validates status against `Literal["pending","in_progress","completed"]` and rejects blank content in `__post_init__`.
- `src/decode/agent/deps.py` — added `task_store: list[Task] = field(default_factory=list)` (per-run TodoWrite store; no mutable-default aliasing).
- `src/decode/tools/tasks.py` — new gated `todo_write(ctx, tasks)` tool: TodoWrite replace semantics, rewrites `ctx.deps.task_store` in place, emits `TaskListUpdated` with status-marked checklist lines (`[x]`/`[~]`/`[ ]`), `ApprovalRequired` until approved.
- `src/decode/tools/registry.py` — registered `todo_write` in `TOOL_SPECS` (`read_only=False`).
- `src/decode/tui/render.py` — `_render_task_list_updated` now shows the pre-marked checklist lines verbatim (dropped redundant `- ` bullet; status markers are the bullet).
- `src/decode/entities/__init__.py` — docstring note that `Task` lands in task 009.
- `tests/unit/decode/entities/test_task.py` — `Task` validation (bad status + blank content rejected), defaults, frozen/hashable.
- `tests/unit/decode/tools/test_tasks.py` — gating (ApprovalRequired, store untouched when unapproved), in-place replace, `TaskListUpdated` emission, clear-to-empty, read-only registration, and one real-agent run (`TestModel(call_tools=["todo_write"])` forced + approved).
- `tests/unit/decode/agent/test_deps.py` — `task_store` defaults to a fresh empty list (no aliasing) + carries a supplied store.
- `tests/unit/decode/tools/test_registry.py` — `todo_write` in the expected tool set + read-only map.
- `tests/unit/decode/tui/test_render.py` — mixed-status checklist render + empty-list placeholder.

**Tests**
- Unit: 235 passing, 0 failing — `make pre-commit` output below.
- Integration: N/A — no infra changes.

**Acceptance criteria**
- [x] `todo_write` replaces/updates the per-run task store — `tests/unit/decode/tools/test_tasks.py::test_todo_write_replaces_the_store_in_place_when_approved`.
- [x] `Task` defined + validated — `tests/unit/decode/entities/test_task.py::test_task_rejects_an_unknown_status`.
- [x] TUI renders the checklist via `TaskListUpdated` — `tests/unit/decode/tui/test_render.py::test_render_task_list_updated_shows_a_mixed_status_checklist` + emission in `test_todo_write_emits_a_task_list_updated_event`.
- [x] Gated (ask-everything) — `tests/unit/decode/tools/test_tasks.py::test_todo_write_requires_approval_when_not_approved` + e2e `test_todo_write_runs_through_a_real_agent_when_approved`.

**Evidence**
```
$ make pre-commit
...
tests/unit/decode/tools/test_tasks.py .......                            [ 79%]
tests/unit/decode/tui/test_render.py ..............                      [100%]
============================= 235 passed in 4.81s ==============================
$ uv lock --check
Resolved 163 packages in 2ms        # no new deps

# end-to-end: forced todo_write through the real loop, approved, then a mixed-status update rendered
store after the forced+approved todo_write call:
   Task(id='a', content='a', status='pending')
╭─ tasks ──────────────────────────────────────────────────╮
│ [x] design the schema                                    │
│ [~] implement todo_write                                 │
│ [ ] write tests                                          │
╰──────────────────────────────────────────────────────────╯
```

**Notes**
- `TaskListUpdated` already carried `tasks: tuple[str, ...]`; the tool now emits status-marked lines (`[x]`/`[~]`/`[ ]`) so the renderer stays a pure string formatter and the status vocabulary lives next to the `Task` model (`tools/tasks.py::_STATUS_MARKERS`).
- The per-run store is replaced **in place** (`task_store[:] = tasks`) so the loop/TUI keep the same list object current; no rebinding.
- No new dependencies (lockfile unchanged); no env vars; no `print()` in library code.
- DID NOT commit — awaiting Tester approval.

### [Tester] 2026-06-19 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make pre-commit` exit 0)
- Unit tests: 235 passed / 0 failed
- Integration tests: N/A (no infra changes)
- Warnings: 0 (`filterwarnings=error` — any warning would fail the run)
- `uv lock --check`: PASS (163 packages resolved; no new deps)
- `code-review` plugin enabled in `.claude/settings.json` (slash-command, advisory): its checklist folded in manually — no `print()` in library code, all signatures annotated, no hardcoded secrets, diff scope clean. No defects.

**E2E adversarial pass**
- Happy path: forced `todo_write` through a real agent (`TestModel(call_tools=["todo_write"])`) + approving resolver → permission asked, store populated `[('a','pending')]`, exactly one `TaskListUpdated` emitted (PASS)
- Break path 1 (validation / wrong types): `Task` rejects unknown status (`'done'`), blank/whitespace/`\t\n`/NBSP content; at the **tool boundary** `TypeAdapter[list[Task]]` rejects int content (`string_type`), bad status (`literal_error`), blank content (`value_error`) — all surface as retriable `ValidationError`, never a loop crash (PASS)
- Break path 2 (state edge — REPLACE + in-place + aliasing): call with 3 tasks then with 1 → store has exactly 1 (not 4); `id(store)` unchanged across calls (in-place `[:]`, not rebind); two separate `AgentDeps` do NOT share the default store (no mutable-default aliasing) (PASS)
- Break path 3 (gating): unapproved call raises `ApprovalRequired` and leaves the store untouched; denied path through the real agent leaves the store empty and emits no `TaskListUpdated` — gate guards *before* any mutation (PASS)
- Break path 4 (boundary inputs): empty list → `(no tasks)` placeholder; 100-task list renders without crash; duplicate ids preserved (TodoWrite has no de-dup, correct); 5000-char content wraps and renders without crash (PASS)
- Break path 5 (hostile input — markup injection): content `[bold]`, `[red]…[/red]`, `[link=http://evil]`, `[/]` rendered **verbatim** — renderer uses `Text("…")` (literal), not `Text.from_markup`, so no Rich markup is interpreted/injected in the panel (PASS)

**Acceptance criteria**
- [x] PASS — `tools/tasks.py` `todo_write` replaces/updates the per-run store on `ctx.deps` — `test_todo_write_replaces_the_store_in_place_when_approved` + manual probe (replace 3→1, same object in place)
- [x] PASS — `entities/task.py` `Task` (id/content/`status`), validated — `test_task_rejects_an_unknown_status`, `test_task_rejects_empty_content`, `test_task_is_frozen_and_hashable` + schema-boundary probe; `src/decode/entities/task.py:41` `__post_init__` guards
- [x] PASS — TUI renders the checklist via `TaskListUpdated` — `test_render_task_list_updated_shows_a_mixed_status_checklist`, `test_render_empty_task_list_shows_a_placeholder` + emission `test_todo_write_emits_a_task_list_updated_event`; `src/decode/tui/render.py:88`
- [x] PASS — Gated (ask-everything) — `test_todo_write_requires_approval_when_not_approved`, `test_todo_write_does_not_touch_the_store_when_unapproved`, e2e `test_todo_write_runs_through_a_real_agent_when_approved` + manual denied-path drive; registered `read_only=False` in `tools/registry.py:60`

**Evidence**
```
$ make pre-commit
... All checks passed! (format + lint)
============================= 235 passed in 4.78s ==============================
$ uv lock --check
Resolved 163 packages in 2ms
$ # markup-injection probe
│ [ ] [bold]injected[/bold]                                                    │
│ [ ] [red]danger[/red] and [link=http://evil]x[/link]                         │
VERIFIED: literal markup text preserved verbatim -> no markup injection
```

**Other issues found**
- None blocking. Note (non-blocking, by design per hand-off): `TaskListUpdated.tasks` carries formatted `tuple[str, ...]` checklist lines rather than structured `Task`s; the status-marker vocabulary lives in `tools/tasks.py::_STATUS_MARKERS` so the renderer stays a pure string formatter. Acceptable for M1; if a future surface needs structured access it can be revisited.
- Minor (non-blocking): a non-string `content` constructed directly in Python (`Task(content=123)`) raises `AttributeError` not `ValueError`, but this is unreachable from the model — the Pydantic tool schema rejects it as a `ValidationError` first. No action needed.

**VERDICT: PASS**
