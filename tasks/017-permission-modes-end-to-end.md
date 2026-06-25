---
id: 017-permission-modes-end-to-end
feature: permission-system-agents-catalog
status: done
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

- [x] `PermissionMode` is exactly `{DEFAULT, PLAN, EDIT, BYPASS}` (no `ASK` value remains);
      `ToolKind` is `{READ_ONLY, FILE_EDIT, OTHER}`. Unit-tested.
- [x] Every registered tool has the right `kind`: `read`/`glob`/`grep`/`web_fetch`/`todo_write` are
      `READ_ONLY`, `write`/`edit` are `FILE_EDIT`, `bash` is `OTHER`; `is_read_only()` still returns
      the right bool. Unit-tested.
- [x] `gate.check` for a **read-only** request → ALLOW under all four modes.
- [x] `gate.check` for a **file-edit** request → ASK (default), ALLOW (edit), DENY (plan), ALLOW
      (bypass); the plan DENY reason mentions `exit_plan_mode`.
- [x] `gate.check` for an **other** (bash) request → ASK (default), ASK (edit), DENY (plan), ALLOW
      (bypass).
- [x] `gate.set_mode(PLAN)` then `check(<read-only>)` → ALLOW and `check(<bash>)` → DENY (mode is
      mutable). Unit-tested.
- [x] In the loop, an auto-ALLOW runs the tool **without** calling `deps.resolve_permission` and
      **without** emitting `PermissionRequested`; an auto-DENY feeds the reason back as a model-visible
      `ToolReturnPart` without prompting; an ASK still prompts. Driven through the real loop with
      `FunctionModel`/`TestModel`.
- [x] **Working looks like:** in `default` mode, `read pyproject.toml` runs with **no** `permission?`
      prompt; `create a file …` still shows `permission? write …`.
- [x] The M1 capstone is updated (read/todo_write/web_fetch auto-allow; only the two writes prompt)
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

### [SWE] 2026-06-25 14:30 — Implementation

**Files modified**
- `src/decode/permissions/types.py` — replaced `PermissionMode.ASK` with `{DEFAULT, PLAN, EDIT, BYPASS}`; added `ToolKind{READ_ONLY, FILE_EDIT, OTHER}`.
- `src/decode/entities/permissions.py` — `PermissionRequest.kind: ToolKind` (default `OTHER`, `read_only` now a derived property); `PermissionDecision` default mode → `DEFAULT`; `allow()` takes optional `mode=`.
- `src/decode/permissions/gate.py` — `check()` returns ALLOW/ASK/DENY by mode x kind (bypass→ALLOW; read-only→ALLOW; plan→DENY w/ exit_plan_mode reason; edit→file-edit ALLOW else ASK; default→ASK); added `set_mode()`; default `DEFAULT`.
- `src/decode/tools/registry.py` — `ToolSpec.kind: ToolKind` (replacing `read_only` bool); derived `TOOL_KIND` map; `TOOL_READ_ONLY` derived from it; kind mapping per ADR-0003 §2.
- `src/decode/tools/__init__.py` — added `tool_kind(name) -> ToolKind` (default `OTHER`); `is_read_only()` now derived (`kind is READ_ONLY`).
- `src/decode/tools/tasks.py` — corrected `TODO_WRITE_READ_ONLY` to `True` and docstring (now `READ_ONLY` per ADR-0003 §2).
- `src/decode/agent/loop.py` `_decide` — builds request with `tool_kind(name)`, calls `gate.check`, HONORS the verdict (ALLOW runs w/o prompt; DENY returns reason w/o prompt; ASK prompts via `_ask_human`).
- `tui/app.py` — unchanged; `PermissionGate()` now defaults to `DEFAULT` via the new default arg (verified, no `ASK` references remain).
- `tests/unit/decode/permissions/test_types.py`, `test_gate.py`, `tests/unit/decode/entities/test_permissions.py`, `tests/unit/decode/tools/test_registry.py`, `test_tasks.py`, `test_web.py`, `test_files.py`, `tests/unit/decode/agent/test_loop.py` — rewritten/extended for the four modes, the kind classification, and the loop auto-allow/auto-deny/ask paths.
- `tests/integration/test_milestone1_capstone.py` — updated: read/todo_write/web_fetch auto-allow (no verdict consumed); only the two writes prompt (one approve, one deny); two scripted verdicts; `asked_tools == ["write", "write"]`.

**Tests**
- Unit: 385 passing, 0 failing — `make unit-tests`.
- Integration: 1 passing (`test_milestone1_capstone`) — `make integration-tests`.
- Full gate: `make ci` green — 386 passing, 0 warnings under `filterwarnings=["error"]`.

**Acceptance criteria**
- [x] `PermissionMode == {DEFAULT, PLAN, EDIT, BYPASS}`, `ToolKind == {READ_ONLY, FILE_EDIT, OTHER}` — `tests/unit/decode/permissions/test_types.py`.
- [x] Tool kinds correct, `is_read_only()` derived — `tests/unit/decode/tools/test_registry.py::test_tool_kinds_match_each_spec`, `::test_is_read_only_is_derived_from_the_kind`.
- [x] Read-only → ALLOW under all four modes — `test_gate.py::test_read_only_request_allows_under_every_mode`.
- [x] File-edit → ASK/ALLOW/DENY(exit_plan_mode)/ALLOW — `test_gate.py` file-edit tests.
- [x] Other(bash) → ASK/ASK/DENY/ALLOW — `test_gate.py` other tests.
- [x] `set_mode(PLAN)` mutability — `test_gate.py::test_set_mode_makes_the_mode_mutable`.
- [x] Loop honors verdict (auto-allow / auto-deny / ask) — `test_loop.py::test_auto_allow_runs_a_read_only_tool_without_prompting`, `::test_auto_deny_feeds_the_reason_back_without_prompting`, `::test_ask_still_prompts_for_a_mutating_tool_under_default`.
- [x] Working looks like (read auto-allows, write asks in default) — verified via the gate end-to-end exercise (Evidence) + the capstone.
- [x] Capstone updated and passing; "read prompts" assertions updated; `make ci` green, 0 warnings.

**Evidence**
```
$ make ci
uv lock --check
Resolved 166 packages in 4ms
make format-check  → 80 files already formatted
make lint-check    → All checks passed!
make test          → 386 passed in 5.59s
```
```
$ uv run python -c "<gate exercise>"   # the loop builds requests exactly this way
=== DEFAULT mode (startup default) ===
         read (read_only) -> allow      # no permission? prompt
        write (file_edit) -> ask        # still prompts
         bash (    other) -> ask
=== EDIT mode ===
        write -> allow                  # file edits auto-allow
         bash -> ask
=== PLAN mode ===
        write -> deny  reason='Plan mode is read-only — present your plan and call exit_plan_mode.'
=== BYPASS mode ===
         write -> allow                 # everything allows
```

**Notes**
- Per-tool `*_READ_ONLY` constants (`WEB_FETCH_READ_ONLY`, `BASH_READ_ONLY`, `ASK_USER_READ_ONLY`, `FILE_TOOLS_READ_ONLY`/`FILE_TOOLS_MUTATING`, `NOOP_READ_ONLY`) are now **dead** — the registry's single source of truth is `ToolSpec.kind`. I left them in place (still asserted by their tool tests, and all values are now factually correct after fixing `TODO_WRITE_READ_ONLY`) to keep this change minimal. Suggest a follow-up cleanup task to delete them and their tests, replacing with the `kind`-based assertions in `test_registry.py`.
- `docs/glossary.md` already carries the canonical terms (Permission Mode, Tool Kind, the four modes); used verbatim in code/tests. No glossary/ADR edits made (PA territory).

### [Tester] 2026-06-25 16:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`make format-check` → 80 files formatted; `make lint-check` → all checks passed)
- Unit tests: 385 passed / 0 failed (`make unit-tests`)
- Integration tests: 1 passed / 0 failed (`make integration-tests`)
- Full gate `make ci`: 386 passed; lock-check + format + lint green
- Warnings: 0 (suite runs under `filterwarnings=["error"]` — any warning would have errored the run)

**E2E adversarial pass** (drove the REAL `build_agent()` + `AgentTurnHandler` loop + `PermissionGate` via `FunctionModel`/`TestModel` + scripted/guard resolvers; 17/17 checks PASS)
- Happy path (gate): `default` mode → `read` request → ALLOW (no prompt), `write` request → ASK (prompts). PASS
- Break path 1 (auto-allow, read-only/default): real `read` tool ran (content in history), `resolve_permission` never called, `PermissionRequested` never emitted. PASS
- Break path 2 (auto-deny, mutating/plan): real `write` under PLAN → reason fed back as model-visible `ToolReturnPart` mentioning `exit_plan_mode`; file never written; resolver never called; no `PermissionRequested`. PASS
- Break path 3 (edit mode): `write` auto-allowed (file created, no prompt); `bash` still ASKed (resolver called with `bash`, `PermissionRequested` emitted). PASS
- Break path 4 (mode mutability): `set_mode(PLAN)` → read-only ALLOW + bash DENY; `set_mode(DEFAULT)` again → bash ASK (mutable both directions). PASS
- Break path 5 (boundary/hostile inputs to gate): BYPASS allows mutation; unknown tool name + 10k-char args → ASK (no crash); unicode/null tool name read-only → ALLOW (no crash); `PermissionDecision` is frozen (post-verdict mutation raises). PASS
- Capstone tamper check: removed one of the two scripted write verdicts → run failed (`Cannot provide a new user prompt when the message history contains unprocessed tool calls`), proving exactly two prompts occur and read/todo_write/web_fetch consume **no** verdict. Restored capstone verbatim (diff-identical to backup) and re-ran green.
- CLI startup guard (task-004, untouched): no-key → friendly stderr line + exit 1, no traceback (verified via `CliRunner` with `gemini_api_key=""`). Not regressed.

**Acceptance criteria**
- [x] PASS — `PermissionMode == {DEFAULT, PLAN, EDIT, BYPASS}` (no `ASK`); `ToolKind == {READ_ONLY, FILE_EDIT, OTHER}` — `tests/unit/decode/permissions/test_types.py` (5 passed); `src/decode/permissions/types.py:22-50`
- [x] PASS — tool kinds correct, `is_read_only()` derived — `test_registry.py::test_tool_kinds_match_each_spec`, `::test_is_read_only_is_derived_from_the_kind`; `registry.py:69-111`
- [x] PASS — read-only → ALLOW under all four modes — `test_gate.py::test_read_only_request_allows_under_every_mode` (parametrized 4×); also adversarial BP4/BP5
- [x] PASS — file-edit → ASK/ALLOW/DENY(exit_plan_mode)/ALLOW — `test_gate.py::test_file_edit_*` (4 tests); reason asserted to contain `exit_plan_mode`
- [x] PASS — other(bash) → ASK/ASK/DENY/ALLOW — `test_gate.py::test_other_*` (4 tests); adversarial BP3/BP4 confirm edit-asks-bash, plan-denies-bash end-to-end
- [x] PASS — `set_mode(PLAN)` mutability — `test_gate.py::test_set_mode_makes_the_mode_mutable`; adversarial BP4 (mutable both ways)
- [x] PASS — loop honors verdict (auto-allow no-resolver/no-event; auto-deny reason-back; ask prompts) — `test_loop.py::test_auto_allow_runs_a_read_only_tool_without_prompting`, `::test_auto_deny_feeds_the_reason_back_without_prompting`, `::test_ask_still_prompts_for_a_mutating_tool_under_default`; independently reproduced through the real loop in adversarial BP1/BP2/BP3 (guard resolvers asserted never called)
- [x] PASS — working looks like (default read auto-allows, write asks) — direct gate exercise (read→allow, write→ask) + loop BP1; `loop.py:262-308`
- [x] PASS — capstone updated (read/todo_write/web_fetch auto-allow; only two writes prompt) and passes; "read prompts" assertions updated in `test_web.py`/`test_files.py`; `make ci` green, 0 warnings — `test_milestone1_capstone.py:243-308` (`asked_tools == ["write", "write"]`); tamper check proves verdict-count is load-bearing

**Evidence**
```
$ make ci
uv lock --check → Resolved … (ok)
make format-check → 80 files already formatted
make lint-check  → All checks passed!
make test        → 386 passed in 5.71s   (0 warnings under filterwarnings=["error"])

$ uv run python adversarial.py   # real loop + gate, FunctionModel/TestModel
17/17 checks passed — ALL ADVERSARIAL CHECKS PASSED
```

**Other issues found** (non-blocking)
- Dead per-tool `*_READ_ONLY` constants (`WEB_FETCH_READ_ONLY`, `BASH_READ_ONLY`, `ASK_USER_READ_ONLY`, `FILE_TOOLS_READ_ONLY`, `TODO_WRITE_READ_ONLY`) remain in `tools/*.py`. They are now all factually correct and still asserted by their tool tests, but the registry's `ToolSpec.kind` is the single source of truth the gate reads — the constants are consulted by no production decision path. SWE already flagged this and proposed a follow-up cleanup task. PASS with note; not a blocker.
- `todo_write` still self-gates via `ApprovalRequired` (raises until approved) yet is classified `READ_ONLY` so the gate auto-allows it — correct per ADR-0003 §2 (it takes the deferred path then auto-allows). Verified end-to-end in `test_tasks.py::test_todo_write_auto_allows_and_runs_through_a_real_agent`. Working as designed.

**VERDICT: PASS**
