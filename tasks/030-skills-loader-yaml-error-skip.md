---
id: 030-skills-loader-yaml-error-skip
feature: skills
status: done
---

# Skills: a malformed-YAML project skill must skip-with-WARNING, not crash the session

Bug fix for [ADR-0004](../docs/adr/0004-milestone-3-skills.md) §3 — surfaced by the task-029
capstone and independently confirmed by the task-029 Tester. `discover_project_skills`
(`src/decode/skills/loader.py`) catches only `(ValueError, OSError)`, so a project skill `.md`
whose **frontmatter is syntactically malformed YAML** (e.g. a `description` with an unquoted `:`,
an unterminated quote, or a tab indent) raises `yaml.YAMLError` / `yaml.scanner.ScannerError` —
which subclasses `yaml.YAMLError`, **not** `ValueError` — so it escapes the `except` and propagates.

Because `load_skills(cwd)` runs on **every turn** (the catalog `@agent.instructions` hook), a single
typo'd file in `<cwd>/.decode/skills/` would crash a live session — directly contradicting the
loader docstring ("a malformed or unreadable project skill is logged at WARNING and skipped") and
ADR-0004 §3 ("a user's typo never breaks a session").

Depends on: 025 · Blocks: — · Small, scoped fix + one regression test.

## Scope

- **`src/decode/skills/loader.py`** — widen the project-skill discovery guard so a malformed-YAML
  frontmatter file is skipped with a WARNING log like every other malformed/unreadable project
  skill. Catch `yaml.YAMLError` alongside the existing `(ValueError, OSError)` (a single
  `except (ValueError, OSError, yaml.YAMLError)`, or whatever the module's import style makes
  cleanest — `yaml` is already imported). The WARNING must still name the offending file (its
  `source` path) so the typo is debuggable. **Only `discover_project_skills` changes**:
  - `load_builtin_skills` must STILL raise on a malformed built-in (our packaging bug — the
    built-in/project asymmetry from ADR-0004 §3 is intentional and must be preserved).
  - `parse_skill_file` keeps raising on bad input (it is the project-discovery caller that decides
    to skip).
- Keep the fix minimal — no behavior change beyond "YAML syntax error in a *project* skill is now
  caught and skipped instead of propagating".

## Acceptance criteria

- [x] `discover_project_skills(cwd)` **skips with a WARNING** (does not raise) when a project skill
      `.md` has syntactically malformed YAML frontmatter — verified for at least: an unquoted `:` in
      a value, an unterminated quote, and a bad indent. The sibling **valid** project skills in the
      same dir still load. Unit-tested (mirrors the existing malformed-project-skill skip tests).
- [x] The WARNING log names the offending file path (`source`), so the bad file is identifiable.
      Unit-tested (assert on the log record / caplog).
- [x] `load_skills(cwd)` no longer raises when a `<cwd>/.decode/skills/*.md` has malformed YAML — it
      returns the built-ins + any valid project skills, with the bad one skipped. Unit-tested. (This
      is the live-session crash path.)
- [x] **Asymmetry preserved:** a malformed **built-in** still raises via `load_builtin_skills`
      (regression-guard the existing behavior — `parse_skill_file` on malformed text still raises).
- [x] No other behavior change: the existing 025 loader tests (override-by-name, missing dir → {},
      directory/unreadable skip, non-string scalar → ValueError) all still pass unchanged.
- [x] `make ci` green, 0 warnings; the new regression test lives in
      `tests/unit/decode/skills/test_loader.py` next to the existing skip tests.

## Out of scope
- Any change to `parse_skill_file`, `load_builtin_skills`, the dispatcher, the catalog, or the TUI.
- Capping or validating skill body size (deferred, ADR-0004).
- Reporting skipped skills to the user in the TUI (a WARNING log is sufficient this milestone).

## Log

### [orchestrator] 2026-06-26 — Filed
Surfaced while authoring the task-029 capstone and confirmed by the 029 Tester: `discover_project_skills`
catches `(ValueError, OSError)` but `yaml.YAMLError`/`ScannerError` is neither, so a malformed-frontmatter
project skill propagates instead of being skipped. `load_skills` runs every turn via the catalog hook, so
a single typo'd `.decode/skills/*.md` crashes the session — contradicts the loader docstring + ADR-0004 §3.
One-line `except` widening + a regression test; preserve the built-in-raises / project-skips asymmetry.

### [SWE] 2026-06-26 14:05 — Implementation

**Files modified**
- `src/decode/skills/loader.py` — widen the `discover_project_skills` per-file guard to
  `except (ValueError, OSError, yaml.YAMLError)` so a malformed-YAML project skill is logged at
  WARNING and skipped (not propagated); added a comment noting the built-in path still catches only
  `ValueError`, preserving the ADR-0004 §3 asymmetry. No other code changed.
- `tests/unit/decode/skills/test_loader.py` — added the malformed-YAML skip tests next to the
  existing skip tests: a parametrized skip-with-WARNING (unquoted `:`, unterminated quote, tab
  indent) asserting the sibling valid skill still loads; a WARNING-names-the-`source`-path test;
  a `load_skills` does-not-raise test (built-ins + valid project skill, bad one skipped); and a
  regression guard that `parse_skill_file` propagates `yaml.YAMLError` (the built-in-raises path).
  Added `import yaml`.

**Tests**
- Unit: 658 passing, 0 failing (`make pre-commit`); loader file 39/39.
- Integration: 6 passing (`make integration-tests`); `make ci` = 664 passing, 0 warnings.
- TDD: the 5 skip tests were RED first (`yaml.scanner.ScannerError` propagating), GREEN after the
  one-line fix; the asymmetry regression guard was GREEN before and after (documents existing
  behavior).

**Acceptance criteria**
- [x] `discover_project_skills` skips-with-WARNING on malformed YAML (unquoted `:`, unterminated
      quote, tab indent), sibling valid skill still loads — `test_loader.py::test_discover_skips_a_malformed_yaml_project_skill_with_a_warning`
- [x] WARNING names the offending `source` path — `test_loader.py::test_discover_malformed_yaml_warning_names_the_offending_file`
- [x] `load_skills` no longer raises (live-session path) — `test_loader.py::test_load_skills_does_not_raise_on_a_malformed_yaml_project_skill`
- [x] Asymmetry preserved (malformed built-in still raises) — `test_loader.py::test_parse_skill_file_propagates_malformed_yaml_frontmatter`
- [x] No other behavior change — existing 025 loader tests all still pass unchanged
- [x] `make ci` green, 0 warnings; new tests live in `tests/unit/decode/skills/test_loader.py`

**Evidence**
```
$ uv run pytest tests/unit/decode/skills/test_loader.py -q
.......................................                                  [100%]
39 passed in 0.13s

$ make ci   # tail
tests/integration/test_milestone1_capstone.py .                          [ 99%]
tests/integration/test_milestone3_skills_capstone.py .....               [100%]
============================= 664 passed in 7.10s ==============================
```

E2E (real `.decode/skills/` with a malformed `typo.md` + valid `deploy.md`, fed to `load_skills`):
```
WARNING skipping malformed/unreadable project skill .../e2e_proj/.decode/skills/typo.md: while scanning a quoted scalar ...
LOADED OK, no crash. skills = ['commit', 'deploy', 'review-diff']
```

**Notes**
- Minimal fix: only the `except` tuple in `discover_project_skills` changed (+ an explanatory
  comment). `parse_skill_file`, `load_builtin_skills`, and `load_skills` are untouched.
- The new tests assert a WARNING via `caplog` (a logging record, not a Python `warnings` warning),
  so `filterwarnings=["error"]` stays clean — confirmed by `make ci` (0 warnings).
- Not committed — handing off to the Tester.

### [Tester] 2026-06-26 15:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 107 files ok; `ruff check` all passed)
- Unit tests: 658 passed / 0 failed (`make pre-commit`); loader file 39/39
- Integration tests: 6 passed / 0 failed (`make integration-tests`)
- Total (unit + integration): 664 passed; Warnings: 0 (matches SWE claim)

**E2E adversarial pass** (real temp `<cwd>/.decode/skills/` dir + sibling valid `deploy.md`,
each call wrapped in `warnings.simplefilter("error")` to mirror `filterwarnings=["error"]`)
- Happy path: a valid `deploy.md` alone → `discover_project_skills` returns `{deploy}`,
  `load_skills` returns `{commit, review-diff, deploy}` (PASS)
- Break 1 (boundary/malformed: unquoted `:` in value) → `typo.md` SKIP+WARN naming the file,
  sibling `deploy` + built-ins still load, no raise (PASS)
- Break 2 (malformed: unterminated quote) → SKIP+WARN names file, sibling loads (PASS)
- Break 3 (malformed: tab indent) → SKIP+WARN names file, sibling loads (PASS)
- Break 4 (hostile: `!!python/object:os.system` tag) → `safe_load` rejects the tag as
  `ConstructorError`(⊂`yaml.YAMLError`) → SKIP+WARN, no code execution (PASS)
- Break 5 (reserved indicators: leading `@` and leading backtick values) → SKIP+WARN names file (PASS)
- Break 6 (block-after-scalar tab) → SKIP+WARN names file (PASS)
- Break 7 (duplicate YAML key `name:`) → PyYAML keeps last value, parses cleanly into `second`,
  NO crash (acceptable — requirement is "never crash"; not a malformed-syntax case) (PASS)
- No Python `warnings` warning emitted on any path (the `simplefilter("error")` wrapper never
  tripped) → confirms the WARNING is a logging record, safe under `filterwarnings=["error"]` (PASS)
- Asymmetry (critical): `parse_skill_file` on malformed YAML raises `yaml.YAMLError`; runtime check
  monkeypatching `_builtin_files` with a malformed built-in → `load_builtin_skills` PROPAGATES
  `ScannerError` (built-in path catches only `ValueError`, unchanged) (PASS)

**Acceptance criteria**
- [x] PASS — `discover_project_skills` skips-with-WARNING on malformed YAML (unquoted `:`,
      unterminated quote, bad indent), sibling valid skill loads — `test_loader.py::test_discover_skips_a_malformed_yaml_project_skill_with_a_warning[unquoted_colon|unterminated_quote|tab_indent]` + adversarial breaks 1-6
- [x] PASS — WARNING names the offending `source` path — `test_loader.py::test_discover_malformed_yaml_warning_names_the_offending_file`; adversarial: each WARNING contains `str(typo.md.resolve())`
- [x] PASS — `load_skills` no longer raises (live-session path) — `test_loader.py::test_load_skills_does_not_raise_on_a_malformed_yaml_project_skill`; adversarial `load_skills` returns built-ins + valid project skill
- [x] PASS — asymmetry preserved (malformed built-in still raises) — `test_loader.py::test_parse_skill_file_propagates_malformed_yaml_frontmatter` + runtime `load_builtin_skills` propagates `ScannerError`; `git diff` shows `load_builtin_skills` untouched
- [x] PASS — no other 025 behavior change — `test_load_skills_project_skill_overrides_a_builtin_by_name`, `test_discover_missing_dir_returns_empty_dict`, `test_discover_skips_an_unreadable_project_skill_with_a_warning`, `test_parse_skill_file_rejects_a_non_string_name/description`, `test_load_skills_working_looks_like_project_commit_wins` all PASS
- [x] PASS — `make ci` green / 0 warnings; new tests live in `tests/unit/decode/skills/test_loader.py` (loader.py:109 is the only behavior change)

**Scope check** (`git diff`): only `src/decode/skills/loader.py` (the `except` tuple at line 109
widened to `(ValueError, OSError, yaml.YAMLError)` + an explanatory comment) and
`tests/unit/decode/skills/test_loader.py` (+81 lines) changed. `parse_skill_file`,
`load_builtin_skills`, `load_skills`, dispatcher, catalog, and TUI untouched. No `print()` added.

**Evidence**
```
$ make pre-commit
... 658 passed in 7.34s
$ make integration-tests
... 6 passed in 1.30s
$ make format-check  -> 107 files already formatted (exit 0)
$ make lint-check    -> All checks passed! (exit 0)
$ adversarial harness (real .decode/skills, warnings-as-errors)
ALL ADVERSARIAL CHECKS PASSED
$ load_builtin_skills with a malformed built-in
OK: load_builtin_skills PROPAGATES yaml.YAMLError -> ScannerError
```

**Other issues found**
- None blocking. Note: a duplicate-`name:` frontmatter parses (PyYAML keeps the last value) rather
  than being skipped — this is PyYAML default behavior, not malformed *syntax*, and never crashes, so
  it is out of scope for this fix. No action required.

**VERDICT: PASS**
