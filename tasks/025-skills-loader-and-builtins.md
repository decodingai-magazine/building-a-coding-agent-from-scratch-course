---
id: 025-skills-loader-and-builtins
feature: skills
status: done
---

# Skills: loader, project discovery, project-override merge + the two built-in skills

Implements [ADR-0004](../docs/adr/0004-milestone-3-skills.md) (skill sources + precedence + built-ins).
Depends on: 024 · Blocks: 026, 028

## Scope

Load + validate skills from two sources and merge them (project-local overrides built-in by name).
Mirror `src/decode/agents/loader.py` (packaged-data via `importlib.resources`) and
`src/decode/memory/files.py` (cwd-relative discovery, skip-unreadable). Pure load/merge — no
dispatcher (026), catalog injection (027), or TUI invocation (028) yet.

- **`config/settings.py`** — add `skills_dir: Path = Path(".decode/skills")` (mirror `sessions_dir`
  / `permissions_file`; the single config reader for the project-skills location). Mirror it in
  **`.env.example`** with a commented `# SKILLS_DIR=.decode/skills` line + one-line explanation.
- **`skills/__init__.py`** — package docstring + re-exports (`load_skills`, `load_builtin_skills`,
  `discover_project_skills`, `parse_skill_file`), mirroring `agents/__init__.py`.
- **`skills/builtin/__init__.py`** — package docstring only (makes `builtin` an importable package so
  `importlib.resources` loads it and hatchling ships the `.md`), mirroring
  `agents/builtin/__init__.py`. **Verify** the `.md` files land in the wheel by default (hatchling
  includes every file under the package dir — confirmed for `agents/builtin`; no `pyproject` change
  expected — confirm with `uv build` + `unzip -l`).
- **`skills/builtin/commit.md`** (ACTIVE — stages + commits) and **`skills/builtin/review-diff.md`**
  (advisory / read-only) — frontmatter (`name` + `description`) + a Markdown body of instructions
  (NOT executable code). Drafts below.
- **`skills/loader.py`**:
  - `parse_skill_file(text, source) -> SkillDef` — split the leading `---`-fenced YAML frontmatter
    from the body (reuse the agent loader's `_split_frontmatter` shape), require `name` +
    `description`, and let `SkillDef` validate the rest. The skill's name is the frontmatter `name:`
    (ADR-0004 §3); the filename is cosmetic. `source` is passed in by the caller (the one deliberate
    deviation from the `parse_agent_file(text)` shape — `SkillDef` carries provenance).
  - `load_builtin_skills() -> dict[str, SkillDef]` — read + validate every `builtin/*.md` via
    `importlib.resources.files("decode.skills.builtin")`, keyed by frontmatter name, `source="builtin"`.
    A built-in **parse failure raises loudly** (our packaging bug), like `load_builtin_agents`.
  - `discover_project_skills(cwd) -> dict[str, SkillDef]` — scan `cwd / settings.skills_dir` for
    `*.md`, parse each with `source=<absolute file path>`, keyed by frontmatter name. A **malformed or
    unreadable** project skill is logged at WARNING and skipped (never crashes the agent — mirror
    memory's skip-unreadable). A missing skills dir → empty dict.
  - `load_skills(cwd) -> dict[str, SkillDef]` — built-ins first, then `dict.update` the project skills
    so a **project skill whose frontmatter name equals a built-in's intentionally overrides** it
    (most-specific wins; the silent override is acceptable — `source` keeps it traceable, ADR-0004 §3).

### Built-in skill drafts

`skills/builtin/commit.md` (ACTIVE — stages and commits autonomously):

```
---
name: commit
description: Stage the appropriate changes and commit them with a Conventional Commits message.
---
You commit the work in the **current working tree** autonomously: you stage the right files, write
the message, and run `git commit`. You commit exactly what you stage.

1. Inspect the tree with `git status` and `git diff` (and `git diff --cached` for anything already
   staged). If there is nothing to commit, say so and stop.
2. Stage the changes that belong in this commit with `git add`. Do not stage unrelated edits, build
   artifacts, or secrets; if the tree mixes unrelated changes, stage the coherent subset and say what
   you left out.
3. Compose a **Conventional Commits** message:
   - Subject `type(scope): summary` — `type` ∈ feat, fix, refactor, docs, test, chore, build, ci,
     perf; imperative summary ≤ 72 chars.
   - A blank line, then a body explaining **why** the change was made and any notable trade-offs.
4. Run `git commit` with that message, committing exactly what you staged. Describe only changes you
   actually saw in the diff; never invent them. Report the resulting commit subject.
```

`skills/builtin/review-diff.md` (advisory — read-only, unchanged):

```
---
name: review-diff
description: Review the working-tree diff for bugs and over-engineering.
---
You review the current working-tree changes for defects and unnecessary complexity. You do not edit
the code and you do not commit — you report.

1. Read the change with `git diff` (and `git diff --cached` for staged work). Ground every comment in
   a specific `file:line` you actually read.
2. Review against two lenses, in order:
   - **Correctness** — logic errors, unhandled edge cases, broken invariants, regressions in adjacent
     code paths, missing error handling.
   - **Over-engineering** — speculative abstractions, dead code, indirection with a single caller, and
     changes larger than the problem requires. Prefer the smallest change that works.
3. Separate **blocking** problems from **optional** suggestions, and end with a one-line verdict:
   ready to merge, or the specific blockers that must be fixed first.
```

## Acceptance criteria

- [x] `settings.skills_dir` defaults to `Path(".decode/skills")` and is mirrored (commented) in
      `.env.example`; the loader reads the path only via the `settings` singleton (no literal path).
- [x] `load_builtin_skills()` returns exactly `{"commit", "review-diff"}`, each with the right
      `name`/`description`, a non-empty `body`, and `source == "builtin"`. Unit-tested.
- [x] The **commit** skill body is ACTIVE: it instructs staging (`git add`) and running `git commit`
      on the current working tree (asserted by substring on the loaded body — e.g. it mentions
      `git add` and `git commit`). The **review-diff** body stays advisory/read-only (mentions
      `git diff`, not `git commit`). Unit-tested. (Do **not** assert skills "never mutate".)
- [x] `parse_skill_file(text, source)` splits frontmatter/body, requires `name` + `description`,
      derives the skill name from the `name:` frontmatter, and raises a clear `ValueError` on missing
      frontmatter / missing key / unclosed fence. Unit-tested.
- [x] `discover_project_skills(cwd)` finds `<cwd>/.decode/skills/*.md`, keys them by **frontmatter
      name** (a file `foo.md` whose frontmatter is `name: bar` keys as `bar`), with `source` set to
      the absolute file path; a missing dir returns `{}`. Unit-tested.
- [x] A **malformed or unreadable** project skill is skipped with a WARNING log and the other project
      skills still load (no crash). Unit-tested. A **built-in** parse failure instead raises (tested
      via `parse_skill_file` on bad text — the shipped built-ins are valid).
- [x] `load_skills(cwd)` merges with **project-override-by-name**: a project skill whose frontmatter
      `name` equals `commit` replaces the built-in `commit` (its `body` and `source` become the
      project file's), while unoverridden built-ins and project-only skills both appear. Unit-tested.
- [x] The `builtin/*.md` load via the **installed package** (packaged data, `importlib.resources`),
      not a repo path — a test loads them through the package; `uv build` + `unzip -l` shows both
      `.md` in the wheel.
- [x] **Working looks like:** write `<tmp>/.decode/skills/commit.md` with a different body →
      `load_skills(tmp)["commit"].body` is the project body and `.source` is that file's path.
- [x] `make ci` green, 0 warnings; `tests/unit/decode/skills/` mirrors `src/decode/skills/`.

## Out of scope
- The `skill` dispatcher tool + registry wiring + adding `skill` to the agents — task 026.
- Catalog assembly / prompt injection — task 027.
- The `/<skill-name>` TUI invocation — task 028.
- A `~/.decode/skills` user-home source (deferred, ADR-0004).
- A body-size cap (deferred, ADR-0004).

## Log
### [PA] 2026-06-25 — Grooming
Mirrors `agents/loader.py` (packaged-data) + `memory/files.py` (cwd-relative, skip-unreadable). The
override rule (project wins by **frontmatter name**) is the same "most-specific wins" memory uses;
the silent same-name override is intentional (ADR-0004 §3), with `source` keeping provenance in logs.
`skills_dir` lands here — not the later catalog task — because the loader is its first reader
(single-config-reader rule). `parse_skill_file` takes a `source` arg (the one deviation from
`parse_agent_file`) so provenance is set at parse time. **Round-2 delta:** the built-in `commit`
skill is now ACTIVE — it stages and runs `git commit` (the git ops ride the gated `bash` tool, so the
gate still governs them — see ADR-0004 §7); `review-diff` stays advisory. ACs assert the active body
content rather than any "never mutates" claim. Built-in failure raises; project failure is skipped —
same asymmetry as the agents loader vs the user settings.json.

### [SWE] 2026-06-26 14:30 — Implementation

**Files modified**
- `src/decode/config/settings.py` — add `skills_dir: Path = Path(".decode/skills")` (single config reader for the project-skills location).
- `.env.example` — mirror it as a commented `# SKILLS_DIR=.decode/skills` line + one-line note.
- `src/decode/skills/__init__.py` — package docstring + re-exports (`load_skills`, `load_builtin_skills`, `discover_project_skills`, `parse_skill_file`).
- `src/decode/skills/builtin/__init__.py` — package docstring only (makes `builtin` an importable package so hatchling ships the `.md`).
- `src/decode/skills/builtin/commit.md` — ACTIVE commit skill (stages with `git add`, runs `git commit`); exact task-025 body draft.
- `src/decode/skills/builtin/review-diff.md` — advisory/read-only review skill; exact task-025 body draft.
- `src/decode/skills/loader.py` — `parse_skill_file` / `load_builtin_skills` (raises on bad built-in) / `discover_project_skills` (skip-with-WARNING, missing dir → `{}`) / `load_skills` (built-ins first, project override by frontmatter name). Mirrors `agents/loader.py` + `memory/files.py`.
- `tests/unit/decode/skills/{__init__.py,test_loader.py}` — 33 tests mirroring `src/decode/skills/` 1:1.

**Tests**
- Unit: 608 passing, 0 failing (33 new in `tests/unit/decode/skills/test_loader.py`).
- Integration: 1 passing (M1 capstone; no infra changes — skills loader is pure load/merge).
- `make ci`: green, 609 passed, 0 warnings (`filterwarnings=["error"]`).

**Acceptance criteria** — all met:
- [x] `settings.skills_dir` default + `.env.example` mirror; loader reads only via the singleton — `test_skills_dir_default_is_decode_skills`, `test_discover_reads_the_dir_via_the_settings_singleton`.
- [x] `load_builtin_skills()` → `{"commit", "review-diff"}` with right name/description, non-empty body, `source=="builtin"` — `test_load_builtin_skills_returns_the_two_skills`, `test_each_builtin_has_description_body_and_builtin_source`, `test_builtin_descriptions_match_the_frontmatter`.
- [x] commit body ACTIVE (`git add`+`git commit`), review-diff advisory (`git diff`, no `git commit`) — `test_commit_skill_body_is_active_it_stages_and_commits`, `test_review_diff_skill_body_is_advisory_read_only`.
- [x] `parse_skill_file` splits/validates, name from frontmatter, clear `ValueError` on missing frontmatter/key/unclosed fence — the `test_parse_skill_file_*` group.
- [x] `discover_project_skills` keys by frontmatter name, absolute-path source, missing dir → `{}` — `test_discover_finds_project_skills_keyed_by_frontmatter_name`, `test_discover_missing_dir_returns_empty_dict`.
- [x] malformed/unreadable project skill skipped with WARNING (others load); built-in failure raises — `test_discover_skips_a_malformed_project_skill_with_a_warning`, `test_discover_skips_an_unreadable_project_skill_with_a_warning`.
- [x] `load_skills` project-override-by-name merge — `test_load_skills_project_skill_overrides_a_builtin_by_name`, `test_load_skills_includes_project_only_skills_alongside_builtins`.
- [x] builtins load via the installed package; wheel ships both `.md` — `test_builtin_files_are_packaged_data_not_a_repo_path` + `uv build`/`unzip -l` (evidence below).
- [x] working-looks-like project commit override — `test_load_skills_working_looks_like_project_commit_wins` + manual e2e (evidence below).
- [x] `make ci` green, 0 warnings; `tests/unit/decode/skills/` mirrors `src/decode/skills/`.

**Evidence**
```
$ make ci   # tail
tests/integration/test_milestone1_capstone.py .                          [100%]
============================= 609 passed in 6.67s ==============================

$ unzip -l dist/*.whl | grep skills/builtin
      604  decode/skills/builtin/__init__.py
     1142  decode/skills/builtin/commit.md
      938  decode/skills/builtin/review-diff.md

$ # manual e2e against a real temp cwd with a project override:
built-ins: ['commit', 'review-diff'] | sources: {'commit': 'builtin', 'review-diff': 'builtin'}
project: ['commit', 'deploy']
merged keys: ['commit', 'deploy', 'review-diff']
commit.body (project override): "Use our gitmoji convention."
commit.source: <tmp>/.decode/skills/commit.md
review-diff.source (unoverridden built-in): builtin
deploy.source (project-only): <tmp>/.decode/skills/deploy.md
```

**Notes**
- Two forward-notes from the task-024 Tester are handled in the loader: (1) `_require_str` `.strip()`s `name`/`description` (and `body` is `.strip()`ed) so the dispatcher key/catalog text are exact — `test_parse_skill_file_strips_whitespace_from_name_and_description`; (2) a non-string YAML value (list/number/bool) for `name`/`description` surfaces as a clear `ValueError` from `parse_skill_file`, not an `AttributeError` from `SkillDef` — `test_parse_skill_file_rejects_a_non_string_name[...]`, `test_parse_skill_file_rejects_a_non_string_description`.
- "Unreadable" is exercised by a `*.md` directory (read raises `OSError`); the loader catches `(ValueError, OSError)` and skips with a WARNING. No `chmod`-based test (non-portable under root).
- `source` is set to `str(path.resolve())` (absolute, symlink-resolved) so it matches on macOS where `tmp_path` lives under `/var → /private/var`.
- No new deps; `uv.lock` unchanged (`uv lock --check` passes inside `make ci`). Dispatcher (026), catalog injection (027), and `/<skill-name>` TUI (028) remain out of scope.

### [Tester] 2026-06-26 16:10 — QA

**Test summary**
- Format / lint / pre-commit: PASS (`ruff format --check` 102 files clean; `ruff check` all passed; `uv lock --check` clean)
- Unit tests: 608 passed / 0 failed (33 new in `tests/unit/decode/skills/test_loader.py`)
- Integration tests: 1 passed / 0 failed (M1 capstone)
- `make ci`: 609 passed; Warnings: 0 (suite runs `filterwarnings=["error"]`, so a single warning would have failed it)

**E2E adversarial pass** (drove the loader directly + against a freshly built+installed wheel)
- Happy path: `load_builtin_skills()` via `importlib.resources` → `{commit, review-diff}`, both `source="builtin"`, keys match `SkillDef.name`, non-empty bodies (PASS)
- Installed-package proof: built wheel to scratch, `unzip -l` shows `decode/skills/builtin/{commit,review-diff}.md`; installed the wheel into a throwaway venv and loaded built-ins — `importlib.resources.files(...)` resolved to **site-packages** (not the repo `src/`), both `.md` present, active/advisory bodies intact (PASS). Repo tree byte-for-byte unchanged afterward; no stray `dist/`/`build/` artifacts.
- Active vs advisory: commit body contains `git add` + `git commit`; review-diff contains `git diff` and **no** `git commit` (PASS)
- Break path (boundary — non-string YAML scalar): `name: [a,b]` / `name: 123` / `name: true` / `description: [a,b]` / `description: 42` → all raise a clear `ValueError` ("'name'/'description' is required and must be a non-empty string"), **never** `AttributeError` (PASS)
- Break path (malformed structure): missing frontmatter / missing name / missing description / unclosed fence / empty `---\n---` block / top-level YAML list / whitespace-only name → all `ValueError` with a clear message (PASS)
- Break path (state — malformed PROJECT override): a malformed `commit.md` in the project dir is skipped with a WARNING and the **built-in `commit` survives** (source still `builtin`, body still has `git commit`) — the override does not delete the built-in (PASS)
- Break path (skip-with-warning vs raise asymmetry): project dir with `bad.md` (no frontmatter) + `broken.md/` (a directory → `OSError`) → both skipped with WARNINGs naming the files, sibling `good.md` still loads; a malformed **built-in** text instead raises (PASS)
- Edge cases probed: empty `.md` (skipped+warned), frontmatter-only/no body (skipped+warned), subdirectory under skills_dir not recursed, a directory named `weird.md` matching the glob (skipped+warned), non-`.md` files ignored silently (no warning), duplicate frontmatter names across two files (collapse to one key, last-sorted filename wins deterministically), Unicode name/body + emoji preserved, CRLF line endings parsed with no stray `\r`, leading BOM rejected cleanly (project would skip), 1 MB body parsed, path-traversal-looking name kept verbatim as a plain dict key (never used as a path in 025), 16-thread concurrent `load_skills` with no errors and consistent results — all PASS
- `discover_project_skills` missing dir → `{}` (PASS); reads location only via `settings.skills_dir` (no literal `.decode/skills` in `loader.py`; monkeypatch test confirms it follows the singleton) (PASS)

**Acceptance criteria**
- [x] PASS — `settings.skills_dir` default + `.env.example` mirror + single config reader — `settings.py:56` `Path(".decode/skills")`; `.env.example` has `# SKILLS_DIR=.decode/skills`; `loader.py:100` `cwd / settings.skills_dir` (grep: no literal path); `test_skills_dir_default_*`, `test_discover_reads_the_dir_via_the_settings_singleton`
- [x] PASS — `load_builtin_skills()` → exactly `{commit, review-diff}`, right name/description, non-empty body, `source=="builtin"` — verified live + from the installed wheel
- [x] PASS — commit body ACTIVE (`git add` + `git commit`), review-diff advisory (`git diff`, no `git commit`) — `commit.md:8,11,17`, `review-diff.md` has no `git commit`
- [x] PASS — `parse_skill_file` splits/validates, name from frontmatter, clear `ValueError` on missing frontmatter/key/unclosed fence (and non-string scalar → ValueError not AttributeError)
- [x] PASS — `discover_project_skills` keyed by frontmatter name, absolute-path source, missing dir → `{}` — `loader.py:104-105` `str(path.resolve())`
- [x] PASS — malformed/unreadable project skill skipped with WARNING (others load); built-in failure raises — verified asymmetry + that a malformed override doesn't drop the built-in
- [x] PASS — `load_skills` project-override-by-name merge (body+source become the project file's; unoverridden built-ins + project-only skills both present)
- [x] PASS — built-ins load via the **installed package** (site-packages, not repo path); `uv build` + `unzip -l` shows both `.md` in the wheel
- [x] PASS — "working looks like": project `commit.md` with a different body wins by name (`body` + `source` are the project file's)
- [x] PASS — `make ci` green, 0 warnings; `tests/unit/decode/skills/test_loader.py` mirrors `src/decode/skills/loader.py` (33 tests)

**Evidence**
```
$ make ci   # tail
tests/integration/test_milestone1_capstone.py .                          [100%]
============================= 609 passed in 6.93s ==============================

$ # wheel built to scratch, then installed into a throwaway venv:
resolved package dir: .../wheelcheck/venv/lib/python3.12/site-packages/decode/skills/builtin
built-ins: ['commit', 'review-diff']
INSTALLED-PACKAGE LOAD: PASS (site-packages, both .md, active/advisory bodies intact)

$ unzip -l <wheel> | grep skills/builtin
      604  decode/skills/builtin/__init__.py
     1142  decode/skills/builtin/commit.md
      938  decode/skills/builtin/review-diff.md

$ git status --porcelain   # unchanged before and after the build
 M .env.example
 M src/decode/config/settings.py
 M tasks/025-skills-loader-and-builtins.md
?? src/decode/skills/
?? tests/unit/decode/skills/
```

**Other issues found** (non-blocking — none affect this task's ACs)
- The `code-review` plugin is enabled in `.claude/settings.json` but is a slash-command not invocable from inside this agent's tool sandbox; performed the manual review checklist instead — no defects found (logger-only, full type annotations incl. `-> None`, no secrets, no `print()` in library code, diff scoped to the four intended files + tests, no `git add -A` spillover).
- Forward-note for 026/028: a skill `name` is kept verbatim as a dict key (path-traversal-looking strings, Unicode, etc. all pass through). Harmless in 025 (the name is never used to build a path or shell command), but the dispatcher tool (026) and the `/<skill-name>` TUI command (028) should ensure the name is never interpolated into a filesystem path or command.
- Minor follow-up (optional): duplicate frontmatter names across two **project** files collapse silently (last-sorted filename wins, deterministic). A WARNING on a project-skill name collision would aid debuggability. Out of scope for 025.

**VERDICT: PASS**
