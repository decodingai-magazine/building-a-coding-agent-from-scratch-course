---
id: 023-drop-dead-read-only-bookkeeping
feature: permission-system-agents-catalog
status: pending
---

# Drop the dead read-only bookkeeping superseded by ToolSpec.kind

Cleanup follow-up to Milestone-2 (ADR-0003 §2). Task 017 made `ToolSpec.kind` (a `ToolKind`) the
single source of truth for a tool's permission classification. That stranded the old boolean
read-only bookkeeping: `is_read_only()`, the `TOOL_READ_ONLY` map, and the per-tool `*_READ_ONLY`
constants now have **zero production callers** (the loop reads `tool_kind()`). Surfaced by
`/ponytail-review` and already flagged as a PR-Reviewer Nit + a task-017 SWE follow-up.

Depends on: 017 · Blocks: — · Pure cleanup, **no behaviour change**.

## Scope

Delete the superseded read-only bookkeeping; `ToolSpec.kind` / `tool_kind()` stay the only API.

- **`src/decode/tools/registry.py`** — remove the `TOOL_READ_ONLY` map and its docstring references.
- **`src/decode/tools/__init__.py`** — remove the `is_read_only()` function, the `TOOL_READ_ONLY`
  import/re-export, and the `__all__` entry. (`tool_kind` / `TOOL_KIND` / `KNOWN_TOOL_NAMES` stay.)
- **Per-tool constants** — remove the now-unused module constants: `TODO_WRITE_READ_ONLY`
  (`tools/tasks.py`), `BASH_READ_ONLY` (`tools/bash.py`), `WEB_FETCH_READ_ONLY` (`tools/web.py`),
  `FILE_TOOLS_READ_ONLY` (`tools/files.py`), `ASK_USER_READ_ONLY` (`tools/askuser.py`), plus any
  docstring lines that describe them.
- **Tests** — fold the registry test's `is_read_only(...)` assertions into the existing
  `tool_kind(...)` / kind-based assertions (do not lose the coverage that `read`/`glob`/`grep`/
  `web_fetch`/`todo_write` are READ_ONLY and `write`/`edit`/`bash` are not); delete any test that
  asserts a removed `*_READ_ONLY` constant.

Before deleting, confirm each symbol has no remaining production reader (`grep -rn`); if any does,
migrate it to `tool_kind()` rather than keeping the constant.

## Acceptance criteria

- [ ] `grep -rn "is_read_only\|TOOL_READ_ONLY" src/` returns nothing; `is_read_only` and the
      `TOOL_READ_ONLY` map are gone (and removed from `__all__`).
- [ ] `grep -rnE "[A-Z_]+_READ_ONLY" src/` returns nothing — the five per-tool `*_READ_ONLY`
      constants are deleted. (The `ToolKind.READ_ONLY` enum member and the `"read_only"` value are
      NOT matched by that pattern and remain.)
- [ ] No production behaviour change: the gate still classifies every tool correctly via
      `ToolSpec.kind` / `tool_kind()`; the M1 capstone and all M2 tests still pass.
- [ ] The registry test proves tool kinds via `tool_kind()` (the `is_read_only` assertions folded
      in, coverage preserved); no test references a deleted symbol.
- [ ] `make ci` green, 0 warnings; the diff is deletions only (plus the folded test assertions) —
      net negative line count.

## Out of scope
- Any change to the gate decision, modes, rules, or tool behaviour — this is a pure dead-code sweep.
- The `"allow"` sentinel-string readability nit in `loop._decide` (separate concern; not a deletion).

## Log

### [ponytail-review] 2026-06-25 — Filed
Filed from `/ponytail-review` of the M2 diff: `is_read_only()` + `TOOL_READ_ONLY` + five per-tool
`*_READ_ONLY` constants have no production caller after task 017 switched the gate to `ToolKind`.
~15 in-diff lines deletable (~25 incl. the pre-existing M1 siblings). Matches PR-Reviewer Nit 1.
