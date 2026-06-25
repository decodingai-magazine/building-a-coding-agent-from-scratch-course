---
id: 017-permission-modes-end-to-end
feature: permission-system-agents-catalog
status: pending
---

# Permission modes end-to-end (default / plan / edit / bypass)

Implements [ADR-0003](../docs/adr/0003-milestone-2-permission-system-and-agents-catalog.md) §1-3.
Depends on: — · Blocks: 018, 019, 020, 021, 022

## Scope

Turn the M1 ask-on-every-tool gate into a real mode-driven allow/ask/deny decision. No
`settings.json` / agent rules yet (task 018) — this lands the four modes, the tool-kind
classification, the mutable mode, and the loop rewire.

- **`permissions/types.py`** — replace `PermissionMode.ASK` with
  `PermissionMode{DEFAULT, PLAN, EDIT, BYPASS}` (values `"default"/"plan"/"edit"/"bypass"`). Add
  `ToolKind{READ_ONLY, FILE_EDIT, OTHER}` (values `"read_only"/"file_edit"/"other"`).
- **`tools/registry.py`** — `ToolSpec` carries `kind: ToolKind` (replacing the `read_only` bool);
  derive a `TOOL_KIND` map. Mapping: `read`/`glob`/`grep`/`web_fetch` → `READ_ONLY`; **`todo_write`
  → `READ_ONLY`** (in-memory checklist, no side effect); `write`/`edit` → `FILE_EDIT`; `bash` →
  `OTHER`. Keep `TOOL_READ_ONLY` / `is_read_only()` working, derived as `kind is READ_ONLY`. Add
  `tools.tool_kind(name) -> ToolKind` (default `OTHER` for unknown names).
- **`entities/permissions.py`** — `PermissionRequest` gains `kind: ToolKind` (default `OTHER`);
  `PermissionDecision` default `mode` becomes `DEFAULT`. `PermissionOutcome` (allow/ask/deny)
  unchanged.
- **`permissions/gate.py`** — `check(request)` returns ALLOW/ASK/DENY by evaluating the mode against
  the request's `kind` per ADR-0003 §1 (no rules yet): `bypass`→ALLOW; `plan`→ read_only ALLOW else
  DENY (reason: "Plan mode is read-only — present your plan and call exit_plan_mode."); `default`→
  read_only ALLOW else ASK; `edit`→ read_only/file_edit ALLOW else ASK. Add `set_mode(mode)`; default
  mode is `DEFAULT`.
- **`agent/loop.py` `_decide`** — build the request with `kind` (`tools.tool_kind(name)`), call
  `gate.check`, and **honor the verdict**: ALLOW → return `"allow"` without asking; DENY → return the
  decision's reason (mapped upstream to `ToolDenied`); ASK → emit `PermissionRequested` and route to
  `deps.resolve_permission` (today's path). Auto-allowed/auto-denied calls do **not** prompt.
- **`tui/app.py`** — `PermissionGate()` constructs with `DEFAULT` (default arg); no `ASK` references
  remain.
- **Update the M1 capstone** (`tests/integration/test_milestone1_capstone.py`): under `default`
  mode `read` / `todo_write` / `web_fetch` auto-allow (no verdict consumed); only the two `write`
  steps still prompt (one approved, one denied). Adjust the scripted verdicts + per-step assertions
  accordingly (the capstone still covers auto-allow + human-allow + human-deny). Update any unit test
  asserting "a read-only tool prompts" (task-005 gate tests). Keep `make ci` fully green.

## Acceptance criteria

- [ ] `PermissionMode` is exactly `{DEFAULT, PLAN, EDIT, BYPASS}` (no `ASK` value remains);
      `ToolKind` is `{READ_ONLY, FILE_EDIT, OTHER}`. Unit-tested.
- [ ] Every registered tool has the right `kind`: `read`/`glob`/`grep`/`web_fetch`/`todo_write` are
      `READ_ONLY`, `write`/`edit` are `FILE_EDIT`, `bash` is `OTHER`; `is_read_only()` still returns
      the right bool. Unit-tested.
- [ ] `gate.check` for a **read-only** request → ALLOW under all four modes.
- [ ] `gate.check` for a **file-edit** request → ASK (default), ALLOW (edit), DENY (plan), ALLOW
      (bypass); the plan DENY reason mentions `exit_plan_mode`.
- [ ] `gate.check` for an **other** (bash) request → ASK (default), ASK (edit), DENY (plan), ALLOW
      (bypass).
- [ ] `gate.set_mode(PLAN)` then `check(<read-only>)` → ALLOW and `check(<bash>)` → DENY (mode is
      mutable). Unit-tested.
- [ ] In the loop, an auto-ALLOW runs the tool **without** calling `deps.resolve_permission` and
      **without** emitting `PermissionRequested`; an auto-DENY feeds the reason back as a model-visible
      `ToolReturnPart` without prompting; an ASK still prompts. Driven through the real loop with
      `FunctionModel`/`TestModel`.
- [ ] **Working looks like:** in `default` mode, `read pyproject.toml` runs with **no** `permission?`
      prompt; `create a file …` still shows `permission? write …`.
- [ ] The M1 capstone is updated (read/todo_write/web_fetch auto-allow; only the two writes prompt)
      and passes; any "read prompts" unit assertion is updated; `make ci` green, 0 warnings under
      `filterwarnings=["error"]`.

## Out of scope
- `.decode/settings.json` + agent rules and the `always`-persist answer (task 018).
- The agents catalog, per-agent tool restriction, control surfaces (tasks 019-022).
- Orchestration / sleep tools (task 021).

## Log

### [PA] 2026-06-25 — Grooming
Groomed from the Milestone-2 plan (ADR-0003 §1-3). Locked: `todo_write` is `READ_ONLY` (round-2
grilling — it must work in plan mode and need not prompt anywhere). Known impact baked into AC: the
capstone now prompts only for the two writes. No `settings.json` here — this is the mode floor.
