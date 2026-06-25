---
id: 023-drop-dead-read-only-bookkeeping
feature: permission-system-agents-catalog
status: done
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

- [x] `grep -rn "is_read_only\|TOOL_READ_ONLY" src/` returns nothing; `is_read_only` and the
      `TOOL_READ_ONLY` map are gone (and removed from `__all__`).
- [x] `grep -rnE "[A-Z_]+_READ_ONLY" src/` returns nothing — the five per-tool `*_READ_ONLY`
      constants are deleted. (The `ToolKind.READ_ONLY` enum member and the `"read_only"` value are
      NOT matched by that pattern and remain.)
- [x] No production behaviour change: the gate still classifies every tool correctly via
      `ToolSpec.kind` / `tool_kind()`; the M1 capstone and all M2 tests still pass.
- [x] The registry test proves tool kinds via `tool_kind()` (the `is_read_only` assertions folded
      in, coverage preserved); no test references a deleted symbol.
- [x] `make ci` green, 0 warnings; the diff is deletions only (plus the folded test assertions) —
      net negative line count.

## Out of scope
- Any change to the gate decision, modes, rules, or tool behaviour — this is a pure dead-code sweep.
- The `"allow"` sentinel-string readability nit in `loop._decide` (separate concern; not a deletion).

## Log

### [ponytail-review] 2026-06-25 — Filed
Filed from `/ponytail-review` of the M2 diff: `is_read_only()` + `TOOL_READ_ONLY` + five per-tool
`*_READ_ONLY` constants have no production caller after task 017 switched the gate to `ToolKind`.
~15 in-diff lines deletable (~25 incl. the pre-existing M1 siblings). Matches PR-Reviewer Nit 1.

### [SWE] 2026-06-25 20:45 — Implementation

Pure dead-code deletion sweep — no behaviour change. Confirmed each symbol had zero production
reader (`grep -rn`) before deleting: `TOOL_READ_ONLY`, `is_read_only`, and the five per-tool
`*_READ_ONLY` constants were only ever referenced by their own definitions/docstrings and tests.

**Files modified**
- `src/decode/tools/registry.py` — removed the derived `TOOL_READ_ONLY` map + its docstring clause.
- `src/decode/tools/__init__.py` — removed `is_read_only()`, the `TOOL_READ_ONLY` import/re-export,
  the two `__all__` entries, and the `is_read_only` docstring sentence. (`tool_kind` / `TOOL_KIND`
  / `KNOWN_TOOL_NAMES` kept.)
- `src/decode/tools/{tasks,bash,web,files,askuser}.py` — removed `TODO_WRITE_READ_ONLY`,
  `BASH_READ_ONLY`, `WEB_FETCH_READ_ONLY`, `FILE_TOOLS_READ_ONLY`, `ASK_USER_READ_ONLY` and the
  docstring lines describing them.
- `tests/unit/decode/tools/test_registry.py` — dropped the `is_read_only` import/`TOOL_READ_ONLY`
  import; folded the deleted `test_is_read_only_is_derived_from_the_kind` into the existing
  `tool_kind()` assertions (coverage already present in `test_tool_kind_reflects_the_registered_kinds`);
  removed the `noop`-not-in-`TOOL_READ_ONLY` / `is_read_only("noop")` asserts.
- `tests/unit/decode/tools/test_{files,web,tasks,askuser,bash}.py` — deleted the five per-tool
  `*_READ_ONLY` tagging tests (tool-name coverage is preserved by the registry's name asserts).

**Tests**
- Unit: 562 passing, 0 failing — `make unit-tests`.
- Integration: 1 passing (M1 capstone) — `make integration-tests`.
- `make ci`: green, 0 warnings (563 passed).

**Acceptance criteria**
- [x] `grep -rn "is_read_only\|TOOL_READ_ONLY" src/` → empty (exit 1).
- [x] `grep -rnE "[A-Z_]+_READ_ONLY" src/` → empty (exit 1); `ToolKind.READ_ONLY` / `"read_only"`
      untouched.
- [x] No behaviour change — gate still classifies via `tool_kind()` (smoke-checked); M1 capstone +
      all M2 tests pass.
- [x] Registry test proves kinds via `tool_kind()`; no test references a deleted symbol.
- [x] `make ci` green, 0 warnings; diff is deletions-only, net **-95** lines (excl. task file).

**Evidence**
```
$ grep -rn "is_read_only\|TOOL_READ_ONLY" src/ ; echo "exit: $?"
exit: 1
$ grep -rnE "[A-Z_]+_READ_ONLY" src/ ; echo "exit: $?"
exit: 1

$ make ci   # tail
uv lock --check → Resolved 166 packages
ruff format --check → 95 files already formatted
ruff check → All checks passed!
============================= 563 passed in 7.36s ==============================

$ git diff --numstat (excl. tasks/) → added=11 deleted=106 net=-95
```

**Notes**
- Out of scope, left in place (PA scoped only the five `*_READ_ONLY` constants): the sibling
  `FILE_TOOLS_MUTATING` map (`tools/files.py`, only reader is a test) and the test-only
  `NOOP_READ_ONLY` (`tests/support/noop_helper.py`). Both are dead bookkeeping too — flagging as an
  adjacent finding for a future sweep, not touched here.
- The 11 "insertions" in the diff are docstring sentences reflowed after a removed clause (e.g.
  `web.py` / `files.py` gating paragraphs), not new content — the change is deletions-only.
- DID NOT COMMIT — handing off to the Tester first.

### [Tester] 2026-06-25 21:30 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 95 files formatted; `ruff check` all passed)
- Unit tests: 562 passed / 0 failed
- Integration tests: 1 passed / 0 failed (M1 capstone)
- `make ci`: 563 passed; `uv lock --check` OK
- Warnings: 0 (suite runs `filterwarnings=["error"]`, so any warning fails the run)

**E2E adversarial pass** (pure-deletion sweep — surface is the gate behaviour + `decode.tools` public API)
- Happy path: drove `tool_kind()` for read/glob/grep/web_fetch/todo_write → READ_ONLY and write/edit→FILE_EDIT, bash/ask_user→OTHER — all correct (PASS)
- Break path 1 (boundary: unknown tool): `tool_kind("does-not-exist")` → OTHER; gate under DEFAULT → `ask`, under PLAN → `deny` (safe default preserved) (PASS)
- Break path 2 (state edges: full mode×kind matrix): all 12 (DEFAULT/PLAN/EDIT/BYPASS × READ_ONLY/FILE_EDIT/OTHER) verdicts identical to pre-sweep behaviour — read-only auto-allows every mode, PLAN denies mutations, EDIT allows FILE_EDIT but asks bash, BYPASS allows all (PASS)
- Break path 3 (import safety / dangling refs): `hasattr(decode.tools,"is_read_only")` False, `hasattr(...,"TOOL_READ_ONLY")` False, module imports with no ImportError, `__all__ == ["KNOWN_TOOL_NAMES","TOOL_KIND","tool_kind"]` (PASS)
- Break path 4 (enum integrity): `ToolKind.READ_ONLY.value == "read_only"` and `ToolKind("read_only") is ToolKind.READ_ONLY` — untouched and round-trips (PASS)

**Acceptance criteria**
- [x] PASS — `grep -rn "is_read_only\|TOOL_READ_ONLY" src/` empty (exit 1); `is_read_only`/`TOOL_READ_ONLY` gone, removed from `__all__`
- [x] PASS — `grep -rnE "[A-Z_]+_READ_ONLY" src/` empty (exit 1); five per-tool constants deleted; `ToolKind.READ_ONLY`/`"read_only"` remain (`src/decode/permissions/types.py:48`)
- [x] PASS — No behaviour change: gate matrix 12/12 unchanged (driven independently above); M1 capstone passes; gate reads `request.kind` from `tool_kind()`, never the deleted bookkeeping (`src/decode/permissions/gate.py:130`)
- [x] PASS — Registry test proves kinds via `tool_kind()` (`tests/unit/decode/tools/test_registry.py:84-100` + `:60-81`); coverage of read/glob/grep/web_fetch/todo_write=READ_ONLY and write/edit/bash NOT read-only preserved; no test references a deleted symbol (`is_read_only(` grep exit 1; the `_READ_ONLY` test matches are the test-only `NOOP_READ_ONLY` + local `_READ_ONLY_TOOLS` var, both out of scope)
- [x] PASS — `make ci` green, 0 warnings; `git diff --numstat src/ tests/` = 11 insertions / 106 deletions / net -95; the 11 insertions are docstring reflow + the shortened `__all__`, no new logic

**Evidence**
```
$ grep -rn "is_read_only\|TOOL_READ_ONLY" src/ ; echo exit:$?     → exit:1
$ grep -rnE "[A-Z_]+_READ_ONLY" src/ ; echo exit:$?               → exit:1
$ make ci   # tail
============================= 563 passed in 6.61s ==============================
$ git diff --numstat src/ tests/  → insertions=11 deletions=106 net=-95
gate matrix (independent drive): 12/12 OK · tool_kind 10/10 OK · ToolKind('read_only') is READ_ONLY: True
```

**Other issues found**
- None blocking. Adjacent dead bookkeeping the SWE already flagged for a future sweep (out of this task's scope): `FILE_TOOLS_MUTATING` (`src/decode/tools/files.py:55`, only reader is a test) and `NOOP_READ_ONLY` (`tests/support/noop_helper.py:32`). Not a defect in this diff.

**VERDICT: PASS**
