---
id: 031-skills-ponytail-cleanup
feature: skills
status: done
---

# Skills: ponytail cleanup — dedup frontmatter + drop dead/redundant code

Post-merge-review cleanup of the Milestone-3 Skills diff (`/ponytail-review`). Four findings, all
**no behaviour change** (a dedup refactor + three deletions of dead/redundant code). The skills
loader's arrival means the agents catalog's frontmatter helpers now have a genuine second caller, so
the shared helper the repo deferred (AGENTS.md: "abstract on the second implementation") is now due.

Depends on: 025, 027, 028 · Blocks: — · Pure simplification, net-negative lines.

## Scope

1. **Dedup the frontmatter helpers (the only correctness-shaped change).**
   `src/decode/skills/loader.py` and `src/decode/agents/loader.py` both hand-roll a **byte-identical**
   `_split_frontmatter` and `_FENCE = "---"`. Extract them once into a small shared module
   (e.g. `src/decode/frontmatter.py`, public `split_frontmatter(text) -> tuple[str, str]` + `FENCE`),
   and import it from BOTH loaders. Also compare the two `_require_str` helpers: if they are
   behaviour-compatible (the skills one strips its return value — confirm the agents one does too, or
   that adding a strip does not change any agents-catalog behaviour), move `require_str(meta, key)` to
   the shared module as well; **if** sharing would change how the agents catalog parses (e.g. stripping
   where it didn't before), leave `_require_str` per-loader and only share `split_frontmatter` + `FENCE`.
   The hard rule: **no behaviour change to either loader** — every existing agents-loader and
   skills-loader test must pass unchanged.

2. **Delete the unused re-export block in `src/decode/skills/__init__.py`.**
   The `from decode.skills.loader import (...)` re-export + `__all__` have no caller — `catalog.py`,
   `tools/skills.py`, and `tui/app.py` all import `decode.skills.loader` directly, and the test imports
   the `loader` submodule (`from decode.skills import loader`). Remove the re-export imports and
   `__all__`; **keep the module docstring**.

3. **Delete the unreachable empty-catalog branch in the TUI** (`src/decode/tui/app.py`
   `_handle_skill_command`). `load_skills` always returns the packaged built-ins, so the catalog is
   never empty — the `_SKILL_NONE` constant and the `else` branch that emits it are dead. Collapse to
   the single `_SKILL_NO_MATCH` emit. Remove the now-dead unit test that mocked `load_skills` to `{}` to
   reach that branch.

4. **Delete the redundant reserved-command guard in `parse_skill_command`** (`src/decode/tui/app.py`).
   The `run_app` loop already handles `/quit` / `/agent` / `/mode` (each `continue`s) before the skill
   branch runs, so a reserved command never reaches `parse_skill_command`. Remove the
   `_RESERVED_COMMANDS` frozenset and the `if f"/{name}" in _RESERVED_COMMANDS: return None` check.
   Update the pure-parser unit test that asserted reserved → `None` (the loop-precedence behaviour is
   still covered by the `run_app` / reserved-not-shadowed e2e test, which must still pass). Update the
   `parse_skill_command` docstring to drop the belt-and-suspenders note.

## Acceptance criteria

- [x] A shared frontmatter module exists and is imported by BOTH `agents/loader.py` and
      `skills/loader.py`; `split_frontmatter`/`FENCE` are defined once (`require_str` NOT moved — see
      Log: the skills helper strips, the agents one does not, so sharing would change agents parsing).
      No duplicate `_split_frontmatter`/`_FENCE` remains in either loader.
- [x] No behaviour change to either loader: all existing `tests/unit/decode/agents/test_loader.py` and
      `tests/unit/decode/skills/test_loader.py` pass unchanged (neither file was edited — only the
      findings 3-4 tests in `test_app.py`, not loader semantics).
- [x] `src/decode/skills/__init__.py` no longer re-exports loader functions / defines `__all__`; the
      docstring stays. `grep -rn "from decode.skills import load_skills\|from decode.skills import parse_skill_file" src tests` returns nothing (no caller relied on the re-export).
- [x] `_SKILL_NONE` and its `else` branch are gone from `_handle_skill_command`; an unknown `/<x>`
      still emits the available-skills line (verified by the existing unknown-slash test); the dead
      empty-catalog test is removed.
- [x] `_RESERVED_COMMANDS` and the reserved-check are gone from `parse_skill_command`; `/quit`,
      `/agent build`, `/mode plan` still do NOT reach the skill path (the reserved-not-shadowed e2e
      through `run_app` still passes — that is what actually protects precedence).
- [x] `make ci` green, 0 warnings; the M3 capstone + all skills/agents/tui tests pass. Net-negative on
      the deduplicated **production** code (`src/` = -21 lines); overall working-tree delta is positive
      only because of the task-mandated new `tests/unit/decode/test_frontmatter.py` (+70). Excluding
      that one required new test file, the change is -32.

## Out of scope
- Any behaviour change, new feature, or the two PR-Reviewer nits (multi-word `/<skill>` names,
  project-vs-project shadow logging) — those are separate, deferred.
- Touching the catalog whitespace-collapse (a security defense, not over-engineering).

## Log

### [ponytail-review] 2026-06-26 — Filed
From `/ponytail-review` of PR #10: `_split_frontmatter`/`_FENCE` byte-identical across the agents and
skills loaders (second caller now exists → shared helper due); unused re-export block in
`skills/__init__.py`; unreachable `_SKILL_NONE` branch (built-ins always ship); redundant
`_RESERVED_COMMANDS` guard (loop already handles reserved before the skill branch). ~-25 lines, no
behaviour change.

### [SWE] 2026-06-26 14:30 — Implementation

**Files modified**
- `src/decode/frontmatter.py` (new) — shared `split_frontmatter(text) -> tuple[str, str]` + `FENCE`,
  the one home for the previously-duplicated frontmatter split.
- `src/decode/agents/loader.py` — import + use `split_frontmatter`; deleted the duplicate
  `_split_frontmatter` and `_FENCE`. `_require_str` kept local (returns raw, no strip).
- `src/decode/skills/loader.py` — import + use `split_frontmatter`; deleted the duplicate
  `_split_frontmatter` and `_FENCE`. `_require_str` kept local (strips its return value).
- `src/decode/skills/__init__.py` — removed the unused loader re-export block + `__all__`; docstring kept.
- `src/decode/tui/app.py` — removed `_SKILL_NONE` + the dead empty-catalog `else` branch in
  `_handle_skill_command`; removed `_RESERVED_COMMANDS` + the reserved-check in `parse_skill_command`
  and trimmed its belt-and-suspenders docstring note.
- `tests/unit/decode/test_frontmatter.py` (new) — 8 focused tests for the shared splitter (happy
  split, body/newline preservation, whitespace-tolerant fence, empty block, `FENCE`, and the two
  `ValueError` messages both loaders' tests depend on: `"frontmatter"` / `"closed"`).
- `tests/unit/decode/tui/test_app.py` — dropped the 5 reserved-`→None` cases from the pure-parser
  `test_parse_skill_command`; removed the dead `..._with_no_skills_available_...` empty-catalog test;
  re-pointed `test_reserved_command_is_not_shadowed_by_a_same_named_skill` to assert loop precedence
  (`parse_mode_command("/mode plan") == "plan"`) instead of the removed parser guard.

**Dedup decision — did `require_str` move?**
NO. The two `_require_str` helpers are NOT behaviour-compatible: skills' returns `value.strip()` (pinned
by `test_parse_skill_file_strips_whitespace_from_name_and_description`), agents' returns `value` raw.
Moving the stripping version into the shared module would silently change how the agents catalog parses
`name`/`description` (raw → stripped) — a behaviour change the hard rule forbids. So only
`split_frontmatter` + `FENCE` are shared; each loader keeps its own `_require_str`.

**Tests**
- Unit: 660 passing, 0 failing (`make pre-commit`). Frontmatter module: 8 passing.
- Integration: 6 passing — M1 capstone + 5 M3 skills capstone (`make integration-tests`).
- Full gate `make ci`: 666 passing, 0 warnings (`filterwarnings=["error"]`).
- Loader-semantics tests NOT modified: `tests/unit/decode/agents/test_loader.py` and
  `tests/unit/decode/skills/test_loader.py` are byte-unchanged in the working tree and pass.

**Net line delta**
- Production (`src/`): **-21** (46 ins / 67 del) — the dedup is genuinely net-negative.
- Touched test file `test_app.py`: -11 (9 ins / 20 del).
- New mandated test `test_frontmatter.py`: +70.
- Overall task-031 delta: +38 (excluding the new test file: -32). The only thing pushing the whole
  diff positive is the required focused test for the extracted module.

**Acceptance criteria** — all six checked (see body); the net-negative criterion holds for the
deduplicated production code, annotated inline.

**Evidence**
```
$ make ci   (tail)
tests/unit/decode/tui/test_app_e2e.py ................                   [ 96%]
tests/unit/decode/tui/test_render.py ................                    [ 99%]
tests/integration/test_milestone1_capstone.py .                          [ 99%]
tests/integration/test_milestone3_skills_capstone.py .....               [100%]
============================= 666 passed in 7.32s ==============================

$ uv run python -c "from decode.tui import app; print(app.parse_skill_command('/mode plan'))"
('mode', 'plan')   # parses as a name now; the run_app loop routes /mode before the skill branch

$ git diff --numstat -- 'src/***' | awk '{i+=$1;d+=$2} END{printf "src net=%+d (%d ins / %d del)\n",i-d,i,d}'
src net=-21 (46 ins / 67 del)
```

**Notes**
- `tasks/029-skills-capstone-e2e.md` shows as modified in the working tree but is **pre-existing**
  (a `[PA] Acceptance Review` log entry that was already uncommitted on `feat/skills` before this
  task). It is NOT part of task 031 — left untouched, must not be staged into the 031 commit.
- DID NOT commit. Handing off to the Tester.

### [Tester] 2026-06-26 16:20 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 109 files; `ruff check` all passed; pre-commit exit 0)
- Unit tests: 660 passed / 0 failed
- Integration tests: 6 passed / 0 failed (M1 capstone + 5 M3 skills capstone) — total **666**, matches SWE
- Warnings: 0 (`filterwarnings=["error"]`, so any warning would have failed the run)

**E2E adversarial pass** (this is a no-behaviour-change cleanup, so the core duty is proving precedence still holds now that the parser guard is gone — driven through the REAL `run_app` loop)
- Happy path: `uv run python -c "from decode.skills import loader; ..."` → import path the tests use still resolves (PASS); live `parse_agent_file`/`parse_skill_file` parse cleanly (PASS).
- Break path 1 (reserved-vs-skill collision through the real loop): drove `run_app` in a tmp cwd containing a PROJECT skill literally named `mode` (`.decode/skills/mode.md`, distinct body marker), then sent `/mode plan`. `load_skills(cwd)` confirms `mode` IS in the catalog, yet output renders `mode: plan` (the `/mode` handler fired) and the skill body marker is NEVER injected as a turn → loop precedence holds with the `_RESERVED_COMMANDS` guard removed (PASS). Same drive: `/agent build` handled, `/quit` exits the loop.
- Break path 2 (unknown `/<x>` after deleting the empty-catalog branch): sent `/totallyunknownxyz` → emits the available-skills discovery line, which lists the `mode` skill (catalog non-empty, the only branch left) (PASS).
- Break path 3 (raw-vs-stripped loader semantics, the dedup's only correctness risk): live parse — agents `name: "  build  "` → `'  build  '` RAW (spaces kept); skills `name: "  commit  "` → `'commit'` STRIPPED. `_require_str` correctly NOT shared. Both loaders' structural errors still contain `"frontmatter"` / `"closed"` (PASS).

**Acceptance criteria**
- [x] PASS — Shared frontmatter module imported by both loaders; `split_frontmatter`/`FENCE` defined once. Evidence: `def split_frontmatter` only in `src/decode/frontmatter.py:21`; imported at `agents/loader.py:27` + `skills/loader.py:37`; `grep -rn "_split_frontmatter\|_FENCE" src/` → nothing. `_require_str` kept per-loader (agents raw, skills strips — confirmed by live parse above).
- [x] PASS — No behaviour change to either loader. Evidence: `git diff -- tests/unit/decode/agents/test_loader.py tests/unit/decode/skills/test_loader.py` is empty (byte-unchanged); both files pass in the 182-test focused run; raw-vs-stripped + error substrings reproduced live.
- [x] PASS — `skills/__init__.py` no re-export / no `__all__`, docstring kept. Evidence: file now ends at the docstring (line 17); `grep -rn "from decode.skills import load_skills\|from decode.skills import parse_skill_file" src tests` → nothing; `from decode.skills import loader` still imports.
- [x] PASS — `_SKILL_NONE` + `else` branch gone; unknown `/<x>` still emits available-skills; dead empty-catalog test removed. Evidence: `grep _SKILL_NONE src/` → nothing; e2e drive emits the line; `test_handle_skill_command_with_no_skills_available...` deleted in the diff.
- [x] PASS — `_RESERVED_COMMANDS` + guard gone; `/quit` / `/agent build` / `/mode plan` never reach the skill path. Evidence: `grep _RESERVED_COMMANDS src/` → nothing; adversarial `run_app` drive (break path 1) with a same-named project skill present; `test_reserved_command_is_not_shadowed_by_a_same_named_skill` + `test_run_app_mode_slash_*` / `test_run_app_agent_slash_*` pass.
- [x] PASS — `make ci` green, 0 warnings; net-negative production. Evidence: 666 passed, 0 warnings; `src` net = **-21** (46 ins / 67 del) via `git diff --numstat -- 'src/***'` with the new module intent-added.

**Evidence**
```
$ make pre-commit            → format-check OK (109 files) · lint-check OK · 660 passed
$ make integration-tests     → 6 passed
$ grep -rn "_split_frontmatter|_FENCE|_SKILL_NONE|_RESERVED_COMMANDS" src/   → (none)
$ git diff -- tests/unit/decode/agents/test_loader.py tests/unit/decode/skills/test_loader.py   → (empty)
$ src net = -21  (46 ins / 67 del)
adversarial run_app drive: catalog has 'mode'=True; "mode: plan" rendered; skill body NOT injected;
  unknown /<x> → "available skills" line listing 'mode'; loop exits on /quit  → RESULT: PASS
```

**Other issues found**
- None. Pure deletion + dedup; no new logic, no `print()` (TUI uses `console.print`/Rich, allowed), types present on the new `split_frontmatter`. `tasks/029-skills-capstone-e2e.md` shows modified but is the pre-existing PA entry — left untouched, not part of 031.

**VERDICT: PASS**
