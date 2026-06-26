---
id: 045-compact-tui-command
feature: context-compaction
status: done
---

# Manual `/compact` TUI command (forces full compaction)

Implements [ADR-0006](../docs/adr/0006-conversation-compaction.md) §7. A reserved slash command that
forces a **full** compaction now, wired like `/quit`, idle-only. An explicit user request compacts
regardless of the window-relative thresholds or `compaction_enabled`.
Depends on: 044 · Blocks: —

## Scope

In `src/decode/tui/app.py`:

- Add `_COMPACT_COMMAND = "/compact"` and a pure `is_compact_command(line) -> bool` (mirror
  `is_quit_command`).
- Intercept `/compact` **among the reserved commands** (with `/quit` / `/agent` / `/mode`, before
  `parse_skill_command`), so a `compact` skill can't shadow it.
- Behaviour:
  - `runner.phase is Phase.IDLE`: `await handler.compact()`. `True` → handler already emitted
    `ContextCompacted`; `False` → `emit_line("Decode - nothing to compact yet.")`.
  - Busy → `emit_line("Decode - busy; try /compact again once the turn finishes.")`, continue.
- Update `footer_hint` to list `/compact` (kept pure/unit-tested) and the startup hint if it enumerates
  commands.

## Acceptance criteria

- [x] `is_compact_command("/compact")` / `"  /compact  "` are `True`; `"/compactx"`, `"compact"`,
      `"/quit"` are `False` (pure unit test).
- [x] Typing `/compact` while **idle** calls `handler.compact()`; on a FunctionModel-seeded over-budget
      history, history becomes `[summary, *tail]` and a `ContextCompacted` line renders (no network).
- [x] `/compact` with nothing to compact renders `Decode - nothing to compact yet.`, history unchanged.
- [x] `/compact` while busy renders the busy line, no history mutation / no turn started.
- [x] `/compact` matched before the skill branch (precedence test).
- [x] `footer_hint` lists `/compact`; footer unit test updated.
- [x] `make ci` green, 0 warnings, no network.

## Out of scope
- The auto cascade + `compact()`/`_microcompact()` (044); the gauge (047).
- A manual microcompaction command (auto-only).

## Log

### [SWE] 2026-06-26 14:30 — Implementation

**Files modified**
- `src/decode/tui/app.py` — added `_COMPACT_COMMAND`, pure `is_compact_command(line)`, the async
  `_handle_compact_command(handler, runner, *, emit)` (idle → `await handler.compact()`, busy →
  busy line), wired `/compact` into `run_app` among the reserved commands (after `/mode`, before
  `parse_skill_command`), re-exported `Phase` from the runner, and added `/compact` to `footer_hint`.
- `tests/unit/decode/tui/test_app.py` — `is_compact_command` truth table, `footer_hint` lists
  `/compact`, the three `_handle_compact_command` branches (idle-True / idle-False / busy×2 phases),
  and the `/compact`-vs-skill precedence test.
- `tests/unit/decode/tui/test_app_e2e.py` — drove the real `run_app` `/compact`: an over-budget
  resumed history compacts to `[summary, *tail]` (a follow-up turn proves the model now sees the
  framed summary + recent tail, not the old turn), and a fresh session renders the friendly no-op.

**Tests**
- Unit: 796 passing, 0 failing (`make pre-commit`). 11 new tests are `/compact`-specific.
- Integration: 8 passing — full `make ci` is 804 passing, 0 warnings, no network.

**Acceptance criteria**
- [x] `is_compact_command` truth table — `tests/.../test_app.py::test_is_compact_command_*`
- [x] `/compact` idle compacts over-budget history → `[summary, *tail]` + `ContextCompacted` renders
      — `test_app_e2e.py::test_run_app_compact_while_idle_compacts_the_over_budget_history`
      (+ unit `test_handle_compact_command_idle_true_compacts_and_emits_no_extra_line`)
- [x] nothing-to-compact renders the friendly line, history unchanged —
      `test_app_e2e.py::test_run_app_compact_with_nothing_to_compact_is_a_friendly_line`
      (+ unit `test_handle_compact_command_idle_false_renders_nothing_to_compact`)
- [x] busy renders the busy line, no compaction / no turn —
      `test_handle_compact_command_busy_renders_busy_and_never_compacts`
- [x] `/compact` matched before the skill branch —
      `test_compact_reserved_command_is_not_shadowed_by_a_same_named_skill`
- [x] `footer_hint` lists `/compact` — `test_footer_hint_mentions_compact`
- [x] `make ci` green, 0 warnings, no network

**Evidence**
```
$ make ci
... 804 passed in 7.86s ...

# Real run_app driven over piped input (no network, summarizer stubbed):
=== /compact on an OVER-BUDGET session ===
Decode - compacted context (~0 tokens → summary + 1 recent messages).
=== /compact on a FRESH (empty) session ===
Decode - nothing to compact yet.
=== footer hint ===
agent:build mode:default | Enter steer | Alt+Enter follow-up | Esc abort | Shift+Tab mode | /agent /mode /compact /quit
```

**Notes**
- Busy is covered at the `_handle_compact_command` unit level (deterministic, both `DISPATCHING`
  and `RUNNING` phases) rather than e2e: a piped busy turn without a *pending decision* can't be
  driven deterministically, and a pending-decision pause routes the typed line to the decision
  channel before the `/compact` check ever runs (the task allows "or an equivalent").
- `/compact` is idle-only and ignores window thresholds / `compaction_enabled` per ADR-0006 §7 —
  it calls `handler.compact()` directly, which the auto-cascade (044) also uses.
- No commit per workflow — handing off to the Tester.

### [Tester] 2026-06-26 16:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 114 files clean; `ruff check` all passed)
- Unit tests: 796 passed / 0 failed
- Integration tests: 8 passed / 0 failed
- `make ci` (incl. `uv lock --check`): 804 passed / 0 failed
- Warnings: 0 (`filterwarnings=["error"]` in effect — any warning would fail the run)

**E2E adversarial pass** (all driven through the REAL `run_app` over a piped prompt_toolkit input, no network — summarizer stubbed)
- Happy path: `/compact` on a fresh session → `Decode - nothing to compact yet.`, REPL stays alive and a follow-up turn runs (PASS)
- Break path 1 (boundary — surrounding whitespace): `  /compact  ` on a fresh session → friendly no-op renders, REPL alive (PASS)
- Break path 2 (state edge — called twice): `/compact` then `/compact` on a fresh session → two no-op lines, no crash, REPL alive (PASS)
- Break path 3 (boundary — case): `/COMPACT` → NOT treated as the command (case-sensitive, like `/quit`); no compaction, no no-op line, no crash, REPL alive (PASS)
- Break path 4 (hostile — shadowing skill, precedence through the loop): a real loadable `compact` skill present in cwd (`load_skills(cwd)` confirms it) + typing `/compact` → reserved command wins, skill body `SKILL-BODY-SHOULD-NEVER-BE-SUBMITTED` is never submitted as a turn, no-op line renders (PASS)
- Break path 5 (headline + idempotency): seeded over-budget resumed history → `/compact` compacts to `[summary, *tail]`, `ContextCompacted` line renders, a second `/compact` is sound (re-summarizes with the prior summary as head — tail + summary preserved, old turn gone, no corruption), follow-up turn sees `E2E-COMPACTED-SUMMARY-MARKER` + the recent tail, old turn absent (PASS)
- Break path 6 (boundary — trailing arg): `/compact foo` → exact-match miss (consistent with `/quit foo`), not the compact command, no crash, REPL alive (PASS)

**Acceptance criteria**
- [x] PASS — `is_compact_command` truth table — `test_app.py::test_is_compact_command_*` pass; live probe: `/compact`→T, `  /compact  `→T, `/compactx`→F, `compact`→F, `/quit`→F, ``→F
- [x] PASS — `/compact` idle compacts over-budget history → `[summary, *tail]` + `ContextCompacted` renders — `test_app_e2e.py::test_run_app_compact_while_idle_compacts_the_over_budget_history` + my break path 5
- [x] PASS — nothing-to-compact renders `Decode - nothing to compact yet.`, history unchanged — `test_app_e2e.py::test_run_app_compact_with_nothing_to_compact_is_a_friendly_line` + my break paths 1-2
- [x] PASS — busy renders the busy line, no history mutation / no turn — `test_app.py::test_handle_compact_command_busy_renders_busy_and_never_compacts[DISPATCHING|RUNNING]`; code review of `_handle_compact_command` (`app.py:331` — `if runner.phase is not Phase.IDLE: emit(_COMPACT_BUSY); return`, `handler.compact()` never reached) confirms logic is sound (unit-level acceptable per task)
- [x] PASS — `/compact` matched before the skill branch — `test_app.py::test_compact_reserved_command_is_not_shadowed_by_a_same_named_skill`; loop order verified `app.py:811` (before `parse_skill_command` at `:820`); live: `parse_skill_command("/compact")` returns `('compact','')`, so ordering is load-bearing and break path 4 proves the reserved command wins through the real loop with a real skill present
- [x] PASS — `footer_hint` lists `/compact` — `test_app.py::test_footer_hint_mentions_compact`; live: `agent:build mode:default | … | /agent /mode /compact /quit`
- [x] PASS — `make ci` green, 0 warnings, no network — 804 passed, `uv lock --check` clean

**Evidence**
```
$ make ci
uv lock --check
uv run ruff format --check  → 114 files already formatted
uv run ruff check           → All checks passed!
============================= 804 passed in 8.44s ==============================

$ uv run python adv_compact.py   # real run_app, piped input, no network
[PASS] A whitespace '  /compact  ' on fresh session -> friendly no-op, REPL alive
[PASS] B /compact twice on fresh session -> two no-op lines, REPL alive  -- no-op count=2
[PASS] C '/COMPACT' (uppercase) -> not treated as compact, no crash, REPL alive
[PASS] D real 'compact' skill present -> reserved /compact wins, skill body never submitted -- skill_loadable=True
[PASS] E over-budget /compact compacts -> [summary,*tail]; 2nd /compact sound; follow-up sees summary+tail -- compact_events=2
[PASS] F '/compact foo' -> not the compact command (exact-match like /quit), no crash
ALL 6 ADVERSARIAL SCENARIOS PASSED
```

**Other issues found** (non-blocking, all consistent with the sibling `/quit` design — no action required)
- `/compact` is case-sensitive (`/COMPACT` falls through to the skill branch) and exact-match (`/compact foo` is not the command) — identical to `is_quit_command`; not in the AC, mirrors the established convention.
- A second consecutive `/compact` re-summarizes (`compact_events=2`) rather than no-op'ing; this is task-044 `split_tail` behaviour (the prior summary rides as the head and merges) — verified non-destructive (tail + summary preserved, old turn gone). Out of 045's scope.

**VERDICT: PASS**
