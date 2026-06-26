---
id: 036-skills-loader-ponytail-trim
feature: skills-directory-convention
status: done
---

# Skills: ponytail trim — drop the dead flat-format migration hint + inline a one-line helper

Cleanup follow-up from `/ponytail-review` of the directory-convention refactor. Two findings, both
**no behaviour change** (deletions). Depends on: 032 · Blocks: —

## Scope

1. **Delete the loose-`*.md` DEBUG migration hint** in `src/decode/skills/loader.py`
   `discover_project_skills`. It warns (at DEBUG, invisible by default) about the dropped flat
   `skills/<name>.md` format — but Milestone 3 is **unmerged**, so no released format ever existed and
   nobody has a flat skill to migrate (task 035 already fixed the docs that could have produced one).
   The `if not sub.is_dir():` branch collapses to a bare `continue`:
   ```python
   for sub in sorted(skills_dir.iterdir(), key=lambda p: p.name):
       if not sub.is_dir():
           continue
       ...
   ```
   Remove any unit test that exists **only** to assert that DEBUG hint fires (a loose `*.md` is still
   correctly ignored — keep/lean on a test that a flat file is not discovered, just drop the
   assertion on the log line if one exists).

2. **Inline `_has_bundled_resources`** in `src/decode/skills/loader.py`. It is a one-line `any(...)`
   with a single caller and a docstring longer than its body. Inline it at the call site and delete the
   helper:
   ```python
   resource_dir = sub if any(e.name != _SKILL_FILE for e in sub.iterdir()) else None
   ```
   If a unit test calls `_has_bundled_resources` directly, retarget it at the observable behaviour
   (`discover_project_skills` setting `resource_dir` when a sibling exists / `None` when only
   `SKILL.md`) — that coverage must stay.

Keep everything else untouched: the WARNING on subdir-without-SKILL.md, the WARNING on
dir-name ≠ frontmatter-name (a requested feature), the `(ValueError, OSError, yaml.YAMLError)`
skip-with-WARNING (task 030 invariant), override-by-name, and the built-in-raises asymmetry.

## Acceptance criteria

- [x] The loose-`*.md` DEBUG hint is gone; `discover_project_skills`'s non-directory branch is a bare
      `continue`. A loose `<cwd>/.decode/skills/legacy.md` is still **not** discovered (hard switch
      preserved). Unit-tested (behaviour, not the removed log line).
- [x] `_has_bundled_resources` is deleted and inlined at its single call site; `resource_dir` is still
      set when a project skill dir holds a sibling and `None` when it holds only `SKILL.md`. Unit-tested
      via `discover_project_skills` behaviour.
- [x] No behaviour change anywhere else: all other `discover_project_skills` / `load_skills` /
      `load_builtin_skills` tests pass unchanged (the WARNINGs, the skip asymmetry, override-by-name,
      name-mismatch WARNING all intact).
- [x] Net-negative production diff; `make ci` green, 0 warnings (`filterwarnings=["error"]`); the M3
      capstone + all skills tests pass.

## Out of scope
- The PR-Reviewer's dotfile-cruft nit (whether `_has_bundled_resources`/the inlined `any` should ignore
  `.DS_Store` etc.) — a separate, deferred behaviour question; this task only inlines the existing logic
  verbatim.
- Any other loader/dispatcher/catalog/TUI change.

## Log

### [ponytail-review] 2026-06-26 — Filed
From `/ponytail-review` of the directory-convention refactor: the loose-`*.md` DEBUG migration hint is
dead (M3 unmerged → no flat skills exist), and `_has_bundled_resources` is a single-caller one-line
`any()`. ~-14 lines, no behaviour change.

### [SWE] 2026-06-26 12:51 — Implementation

**Files modified**
- `src/decode/skills/loader.py` — `discover_project_skills`: non-directory branch collapsed to a bare
  `continue` (dropped the loose-`*.md` DEBUG migration hint); inlined the one-line `any(...)` at its
  call site and deleted the `_has_bundled_resources` helper; docstring updated to match.
- `tests/unit/decode/skills/test_loader.py` — `test_discover_does_not_discover_a_loose_flat_md`:
  dropped the assertion on the removed DEBUG log line; kept the behaviour assertion (a loose
  `<skills_dir>/legacy.md` is **not** discovered) and removed the now-unused `caplog` plumbing.

**Tests**
- Unit: 690 passing, 0 failing — `make unit-tests` (skills/test_loader.py: 53 passing).
- Integration: 8 passing — M3 skills capstone + M1 capstone (`make integration-tests`).

**Acceptance criteria**
- [x] Loose-`*.md` DEBUG hint gone; non-directory branch is a bare `continue`; a loose
  `<cwd>/.decode/skills/legacy.md` is still not discovered — verified by
  `tests/unit/decode/skills/test_loader.py::test_discover_does_not_discover_a_loose_flat_md` and the
  e2e run below.
- [x] `_has_bundled_resources` deleted and inlined; `resource_dir` still set when a sibling exists,
  `None` when only `SKILL.md` — verified by `test_discover_sets_resource_dir_when_the_skill_ships_a_sibling`,
  `test_discover_resource_dir_is_none_when_only_skill_md`, `test_discover_resource_dir_set_for_a_sibling_directory_too`.
- [x] No behaviour change elsewhere: all WARNINGs (no-SKILL.md, name-mismatch), the
  `(ValueError, OSError, yaml.YAMLError)` skip asymmetry, override-by-name, built-in-raises asymmetry
  all pass unchanged.
- [x] Net-negative production diff (`+4 / -21` = -17 lines); `make ci`-equivalent gates green, 0
  warnings under `filterwarnings=["error"]`.

**Evidence**
```
$ git diff --stat src/
 src/decode/skills/loader.py | 25 ++++---------------------
 1 file changed, 4 insertions(+), 21 deletions(-)

$ make integration-tests
collected 8 items
tests/integration/test_milestone1_capstone.py .                          [ 12%]
tests/integration/test_milestone3_skills_capstone.py .......             [100%]
============================== 8 passed in 1.51s ===============================

$ uv run python -c '<drive discover_project_skills against a temp cwd>'
discovered keys: ['commit', 'deploy']
deploy.resource_dir set: True -> deploy
commit.resource_dir: None
legacy discovered: False
```

**Notes**
- No test called `_has_bundled_resources` directly, so no retargeting was needed — the
  resource_dir set/unset behaviour was already covered via `discover_project_skills`. Only the dead
  DEBUG-log assertion was dropped; coverage is unchanged (behaviour still asserted).
- Pure deletion/inline, no architectural decision. Handing off to the Tester. NOT committed.

### [Tester] 2026-06-26 14:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check`: 111 files formatted; `ruff check`: All checks passed)
- Unit tests: 690 passed / 0 failed (`make unit-tests`); `skills/test_loader.py`: 53 passed
- Integration tests: 8 passed / 0 failed (`make integration-tests`; M1 capstone + M3 skills capstone)
- `uv lock --check`: PASS (`make ci` components all green)
- Warnings: 0 (`filterwarnings=["error"]` active — no warnings surfaced)
- code-review plugin enabled in `.claude/settings.json`; it is an advisory slash-command surface with
  no callable tool in the tester runtime, so an equivalent manual diff review was performed — no defects.

**E2E adversarial pass** (real `discover_project_skills` / `load_skills` / `load_builtin_skills` over
real temp cwds; 20/20 checks PASS)
- Happy path: project dir `deploy/` with sibling `references/x.md` → `resource_dir` set to the
  **cwd-joined, un-resolved** `<cwd>/.decode/skills/deploy` (starts with cwd, not a realpath); `source`
  is the resolved absolute `…/deploy/SKILL.md`. Dir `commit/` with only `SKILL.md` → `resource_dir is
  None`. (PASS)
- Break path 1 (hard-switch / dead-hint removal): loose `<skills_dir>/legacy.md` → NOT discovered
  (`{}`); loose non-`.md` `NOTES.txt` → also ignored (exercises the bare `continue`); the only DEBUG
  line emitted is the existing "discovered 0 project skills" summary — the deleted migration hint did
  NOT re-enable discovery and emits nothing. (PASS)
- Break path 2 (preserved invariants):
  - subdir without `SKILL.md` → WARNING naming `incomplete`, skipped; sibling `good` still loads. (PASS)
  - dir-name `foo/` ≠ frontmatter `name: bar` → keyed by `bar`, WARNING names both. (PASS)
  - malformed-YAML project skill (`yaml.YAMLError`, not `ValueError`) → skipped with WARNING naming the
    offending `SKILL.md`; sibling `good` still loads (task-030 catch intact). (PASS)
  - override-by-name: project `commit/SKILL.md` wins over built-in (body + resolved source); other
    built-in `review-diff` untouched (`source == "builtin"`). (PASS)
  - built-in/project asymmetry: the SAME malformed input as a **built-in** RAISES `ValueError` loudly. (PASS)
- Extra: missing skills dir → `{}`; empty skills dir → `{}`; sibling *folder* (not just file) also sets
  `resource_dir`. (PASS)

**Acceptance criteria**
- [x] PASS — DEBUG hint gone; non-directory branch is a bare `continue`; loose `legacy.md` not
      discovered — `git diff src/decode/skills/loader.py` (lines 133-134 collapse to `if not
      sub.is_dir(): continue`); `tests/unit/decode/skills/test_loader.py::test_discover_does_not_discover_a_loose_flat_md`
      passes; e2e break path 1.
- [x] PASS — `_has_bundled_resources` deleted + inlined; `resource_dir` set when sibling exists, `None`
      when only `SKILL.md` — `grep -rn "_has_bundled_resources" src/` empty; `loader.py:142` =
      `resource_dir = sub if any(e.name != _SKILL_FILE for e in sub.iterdir()) else None`; tests
      `test_discover_sets_resource_dir_when_the_skill_ships_a_sibling`,
      `test_discover_resource_dir_is_none_when_only_skill_md`,
      `test_discover_resource_dir_set_for_a_sibling_directory_too` pass; e2e happy path.
- [x] PASS — No behaviour change elsewhere: all WARNINGs (no-SKILL.md, name-mismatch), the
      `(ValueError, OSError, yaml.YAMLError)` skip asymmetry, override-by-name, built-in-raises all pass
      unchanged — 53/53 loader tests + e2e break path 2.
- [x] PASS — Net-negative production diff (`git diff --stat src/`: `+4 / -21` = -17); format/lint/unit/
      integration green, 0 warnings; M3 capstone + all skills tests pass.

**Evidence**
```
$ git diff --stat src/
 src/decode/skills/loader.py | 25 ++++---------------------
 1 file changed, 4 insertions(+), 21 deletions(-)

$ grep -rn "_has_bundled_resources" src/      # (empty — helper fully removed)

$ make unit-tests
============================= 690 passed in 7.15s ==============================

$ make integration-tests
tests/integration/test_milestone1_capstone.py .                          [ 12%]
tests/integration/test_milestone3_skills_capstone.py .......             [100%]
============================== 8 passed in 1.44s ===============================

$ uv run python e2e_adversarial.py
20 checks, 0 failed
```

**Other issues found**
- None blocking. The inlined `any(...)` treats a stray dotfile sibling (e.g. `.DS_Store`) as a
  resource and would set `resource_dir` — but this is verbatim-identical to the deleted helper's
  behaviour and is explicitly the task's Out-of-scope deferred item (PR-Reviewer's dotfile-cruft nit),
  not a regression introduced here.

**VERDICT: PASS**
